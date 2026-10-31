"""Editable coverage is independent of launch times and frozen report inputs."""
from contextlib import closing
from datetime import date
import uuid

import pytest

import periodic_reports as pr
import report_schedule as rs
import settings
from test_periodic_reports import db, SETTINGS, news, runner


def changed(previous=None, at="2026-10-30T16:00:00+08:00", **windows):
    previous = previous or SETTINGS
    windows = rs.normalize_windows(windows, previous.get("report_windows"))
    return {**previous, "report_windows": windows,
            "report_window_history": rs.changed_window_history(previous, windows, pr.local(at))}


@pytest.mark.parametrize("windows,error", [
    (None, "invalid_report_windows"), ({"custom": {}}, "invalid_report_windows"),
    ({"noon": None}, "invalid_report_windows"), ({"noon": {"start": "8:30"}}, "invalid_report_windows"),
    ({"noon": {"end": "24:00"}}, "invalid_report_windows"), ({"weekly": {"start_day": -1}}, "invalid_report_windows"),
    ({"morning": {"start_day": True}}, "invalid_report_windows"), ({"evening": {"start_day": -2}}, "invalid_report_windows"),
    ({"noon": {"start": "14:30"}}, "invalid_report_window_order"),
    ({"evening": {"start_day": 0}}, "invalid_report_window_order"),
])
def test_invalid_windows(windows, error):
    with pytest.raises(ValueError, match=error): rs.normalize_windows(windows)


def test_each_report_window_is_independent_and_dates_resolve_across_calendar_edges():
    windows = rs.normalize_windows({"morning": {"start": "22:00", "end": "08:00"},
        "noon": {"start": "09:00", "end": "14:00"}, "evening": {"start_day": 0, "start": "00:00", "end": "21:00"},
        "weekly": {"start": "03:00", "end": "02:00"}, "monthly": {"start": "01:00", "end": "04:00"}})
    rs.validate_windows(rs.DEFAULT_TIMES, windows)
    schedule = rs.Schedule({"report_windows": windows})
    rows = {kind:(pr.stamp(start),pr.stamp(end)) for kind,_,start,end in pr.slots(date(2026,6,1),pr.calendar_data(),schedule)}
    assert rows["morning"] == ("2026-05-31T22:00:00+08:00", "2026-06-01T08:00:00+08:00")
    assert rows["noon"] == ("2026-06-01T09:00:00+08:00", "2026-06-01T14:00:00+08:00")
    assert rows["evening"] == ("2026-06-01T00:00:00+08:00", "2026-06-01T21:00:00+08:00")
    assert rows["weekly"] == ("2026-05-25T03:00:00+08:00", "2026-06-01T02:00:00+08:00")
    assert rows["monthly"] == ("2026-05-01T01:00:00+08:00", "2026-06-01T04:00:00+08:00")
    assert tuple(map(pr.stamp,pr.selected_period("monthly","2028-02",windows))) == ("2028-02-01T01:00:00+08:00","2028-03-01T04:00:00+08:00")
    assert tuple(map(pr.stamp,pr.selected_period("morning","2027-01-01",windows))) == ("2026-12-31T22:00:00+08:00","2027-01-01T08:00:00+08:00")
    assert schedule.times == rs.DEFAULT_TIMES


def test_missed_editions_use_the_windows_effective_at_their_launch(db):
    config = changed(at="2026-10-31T16:00:00+08:00", evening={"start_day":0,"start":"00:00","end":"21:00"})
    with closing(db()) as conn:
        pr.enqueue_due(conn,pr.local("2026-10-31T22:00:00+08:00"),schedule=rs.Schedule(config))
        rows = {row["edition_key"]:row for row in conn.execute("SELECT * FROM periodic_reports")}
        assert rows["evening:2026-10-30"]["period_end"] == "2026-10-30T21:30:00+08:00"
        today = rows["evening:2026-10-31"]
        assert today["scheduled_at"] == "2026-10-31T21:30:00+08:00"
        assert (today["period_start"],today["period_end"]) == ("2026-10-31T00:00:00+08:00","2026-10-31T21:00:00+08:00")
    # Editing after today's occurrence cannot rewrite an already-due window.
    late = changed(at="2026-10-31T22:00:00+08:00", evening={"end":"21:00"})
    assert pr.slots(date(2026,10,31),pr.calendar_data(),rs.Schedule(late))[0][3].hour == 21
    assert pr.slots(date(2026,10,31),pr.calendar_data(),rs.Schedule(late))[0][3].minute == 30


def test_settings_save_windows_preserves_launches_old_clients_and_rejects_invalid(tmp_path,monkeypatch):
    import app
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH",str(tmp_path/"settings.json"))
    monkeypatch.setattr(app,"serialize_runtime_settings",lambda:settings.load_app_settings())
    monkeypatch.setattr(app.CLASSIFICATION_SERVICE,"configure",lambda **kwargs:None)
    monkeypatch.setattr(app,"refresh_agent_session_expiries",lambda:None)
    monkeypatch.setattr(pr,"now",lambda:pr.local("2026-10-31T16:00:00+08:00"))
    client=app.app.test_client();llm=settings.default_app_settings()["llm"]
    response=client.put("/api/settings",json={"llm":llm,"report_windows":{"evening":{"start_day":0,"start":"00:00","end":"21:00"},"monthly":{"end":"02:00"}}})
    assert response.status_code == 200
    saved=settings.load_app_settings()
    assert saved["report_schedule"] == rs.DEFAULT_TIMES
    assert saved["report_windows"]["evening"] == {"start_day":0,"start":"00:00","end":"21:00"}
    assert len(saved["report_window_history"]) == 2
    assert app.REPORT_SERVICE.schedule_wake.is_set()
    assert client.put("/api/settings",json={"llm":llm}).status_code == 200
    assert settings.load_app_settings()["report_window_history"] == saved["report_window_history"]
    before=(tmp_path/"settings.json").read_bytes()
    for windows,error in [({"evening":{"end":"22:00"}},"invalid_report_schedule_window"),
                           ({"monthly":{"end":"09:00"}},"invalid_report_schedule_window"),
                           ({"noon":{"start":"15:00"}},"invalid_report_window_order")]:
        response=client.put("/api/settings",json={"llm":llm,"report_windows":windows})
        assert response.status_code == 400 and response.json["error"] == error
        assert (tmp_path/"settings.json").read_bytes() == before


def test_open_form_binds_its_window_and_comparisons_share_snapshot_after_settings_change(db,monkeypatch):
    monkeypatch.setattr(pr,"now",lambda:pr.local("2026-10-31T12:00:00+08:00"))
    config=changed(evening={"start_day":0,"start":"00:00","end":"21:00"})
    service=pr.Service(db,lambda:config,lambda:None,runner)
    options=service.manual_options();window=options["windows"]["evening"]
    config=changed(config,at="2026-10-31T11:00:00+08:00",evening={"end":"20:00"})
    with closing(db()) as conn:
        news(conn,"in-window","2026-10-30T20:30:00+08:00")
        news(conn,"outside-window","2026-10-30T21:00:00+08:00")
    for _ in range(2):
        report=service.create_selected("evening","2026-10-30",uuid.uuid4().hex,options["database_token"],window=window)
        assert report["period_start"] == "2026-10-30T00:00:00+08:00"
        assert report["period_end"] == "2026-10-30T21:00:00+08:00"
        assert service.run_one()
    with closing(db()) as conn:
        rows=conn.execute("SELECT * FROM periodic_reports ORDER BY created_at").fetchall()
        assert len(rows) == 2 and rows[0]["snapshot_id"] == rows[1]["snapshot_id"]
        assert [item["id"] for item in pr.stored_snapshot(conn,rows[0])["sources"]] == ["in-window"]
    with pytest.raises(ValueError,match="report_window_changed"):
        service.create_selected("evening","2026-10-30",uuid.uuid4().hex,options["database_token"])


def test_monthly_manual_and_automatic_titles_remain_in_the_covered_month(db,monkeypatch):
    monkeypatch.setattr(pr,"now",lambda:pr.local("2026-11-01T10:00:00+08:00"))
    config=changed(monthly={"start":"01:00","end":"04:00"})
    service=pr.Service(db,lambda:config,lambda:None)
    options=service.manual_options()
    report=service.create_selected("monthly","2026-10",uuid.uuid4().hex,options["database_token"],window=options["windows"]["monthly"])
    assert report["title"] == "2026年10月月报"
    with closing(db()) as conn:
        pr.enqueue_due(conn,pr.now(),schedule=rs.Schedule(config))
        row=conn.execute("SELECT * FROM periodic_reports WHERE edition_key='monthly:2026-11-01'").fetchone()
        assert row["title"] == "2026年10月月报" and row["period_end"].endswith("04:00:00+08:00")
