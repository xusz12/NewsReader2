from contextlib import closing
from datetime import date
import json
import uuid

import pytest

import periodic_reports as pr
import report_schedule as rs
import settings
from test_periodic_reports import db, SETTINGS, news, runner


def revised(previous=None, at="2026-10-30T16:00:00+08:00", **times):
    previous = previous or {}
    times = rs.normalize(times, previous.get("report_schedule"))
    return {**SETTINGS, "report_schedule": times,
            "report_schedule_history": rs.changed_history(previous, times, pr.local(at))}


def test_launch_times_change_without_changing_news_windows():
    schedule = rs.Schedule({"report_schedule": rs.normalize({"morning": "09:15", "noon": "15:10", "evening": "22:05", "weekly": "09:00", "monthly": "10:15"})})
    rows = {kind: (pr.stamp(scheduled), pr.stamp(start), pr.stamp(end))
            for kind, scheduled, start, end in pr.slots(date(2026, 6, 1), pr.calendar_data(), schedule)}
    assert rows["morning"] == ("2026-06-01T09:15:00+08:00", "2026-05-31T21:30:00+08:00", "2026-06-01T08:30:00+08:00")
    assert rows["noon"][1:] == ("2026-06-01T08:30:00+08:00", "2026-06-01T14:30:00+08:00")
    assert rows["evening"][1:] == ("2026-05-31T21:30:00+08:00", "2026-06-01T21:30:00+08:00")
    assert rows["weekly"] == ("2026-06-01T09:00:00+08:00", "2026-05-25T00:00:00+08:00", "2026-06-01T00:00:00+08:00")
    assert rows["monthly"] == ("2026-06-01T10:15:00+08:00", "2026-05-01T00:00:00+08:00", "2026-06-01T00:00:00+08:00")
    assert [row[0] for row in pr.slots(date(2026, 10, 31), pr.calendar_data(), schedule)] == ["evening"]


@pytest.mark.parametrize("value,error", [
    (None, "invalid_report_schedule"), ([], "invalid_report_schedule"),
    ({"custom": "10:00"}, "invalid_report_schedule"), ({"morning": "8:30"}, "invalid_report_schedule"),
    ({"morning": "24:00"}, "invalid_report_schedule"), ({"morning": "08:60"}, "invalid_report_schedule"),
    ({"morning": 830}, "invalid_report_schedule"), ({"morning": "08:30\n"}, "invalid_report_schedule"),
    ({"morning": "14:30"}, "invalid_report_schedule_order"), ({"evening": "13:00"}, "invalid_report_schedule_order"),
])
def test_reject_invalid_or_out_of_order_times(value, error):
    with pytest.raises(ValueError, match=error):
        rs.normalize(value)


def test_setting_a_future_time_only_changes_execution():
    schedule = rs.Schedule(revised(evening="22:00"))
    row = next(row for row in pr.slots(date(2026, 10, 30), pr.calendar_data(), schedule) if row[0] == "evening")
    assert tuple(map(pr.stamp, row[1:])) == ("2026-10-30T22:00:00+08:00", "2026-10-29T21:30:00+08:00", "2026-10-30T21:30:00+08:00")
    tomorrow = pr.slots(date(2026, 10, 31), pr.calendar_data(), schedule)[0]
    assert tomorrow[2] == pr.local("2026-10-30T21:30:00+08:00")


def test_moving_time_into_past_skips_today_without_stretching_next_window():
    old = revised(evening="23:00")
    schedule = rs.Schedule(revised(old, at="2026-10-30T22:30:00+08:00", evening="22:00"))
    assert schedule.occurrence(date(2026, 10, 30), "evening") is None
    row = pr.slots(date(2026, 10, 31), pr.calendar_data(), schedule)[0]
    assert tuple(map(pr.stamp, row[1:])) == ("2026-10-31T22:00:00+08:00", "2026-10-30T21:30:00+08:00", "2026-10-31T21:30:00+08:00")


def test_restart_uses_historical_times_and_never_duplicates_dated_editions(db):
    config = revised(at="2026-10-31T22:00:00+08:00", evening="23:00", weekly="09:15", monthly="10:45")
    schedule = rs.Schedule(config)
    with closing(db()) as conn:
        assert pr.enqueue_due(conn, pr.local("2026-10-31T22:00:00+08:00"), schedule=schedule) == 2
        original = [tuple(row) for row in conn.execute("SELECT * FROM periodic_reports ORDER BY id")]
        # Moving an already-due evening later must not make a second edition.
        assert pr.enqueue_due(conn, pr.local("2026-10-31T23:30:00+08:00"), schedule=schedule) == 0
        assert [tuple(row) for row in conn.execute("SELECT * FROM periodic_reports ORDER BY id")] == original
        pr.enqueue_due(conn, pr.local("2026-11-02T12:00:00+08:00"), schedule=schedule)
        rows = {row["edition_key"]: dict(row) for row in conn.execute("SELECT * FROM periodic_reports")}
    assert rows["evening:2026-10-30"]["scheduled_at"].endswith("21:30:00+08:00")
    assert rows["evening:2026-10-31"]["scheduled_at"].endswith("21:30:00+08:00")
    assert rows["evening:2026-11-01"]["scheduled_at"].endswith("23:00:00+08:00")
    assert rows["weekly:2026-11-02"]["scheduled_at"].endswith("09:15:00+08:00")
    assert rows["monthly:2026-11-01"]["scheduled_at"].endswith("10:45:00+08:00")
    assert not any(row["kind"] in {"morning", "noon"} for row in rows.values())


def test_scheduler_uses_saved_times_but_manual_windows_are_independent(db, monkeypatch):
    config = revised(evening="22:01")
    service = pr.Service(db, lambda: config, lambda: pytest.fail("Scheduling must not refresh the index"))
    assert service.schedule_once(pr.local("2026-10-30T22:00:50+08:00")) == 10
    service.schedule_once(pr.local("2026-10-30T22:01:00+08:00"))
    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM periodic_reports WHERE kind='evening'").fetchone()
        assert row["scheduled_at"] == "2026-10-30T22:01:00+08:00"
        assert row["period_end"] == "2026-10-30T21:30:00+08:00"
        token = conn.execute("SELECT database_token FROM periodic_report_meta").fetchone()[0]
    monkeypatch.setattr(pr, "now", lambda: pr.local("2026-10-31T16:00:00+08:00"))
    options = service.manual_options()
    assert options["schedule_times"]["evening"] == "22:01"
    morning = service.create_selected("morning", "2026-10-30", uuid.uuid4().hex, token)
    assert morning["period_start"] == "2026-10-29T21:30:00+08:00"
    # Legacy forms with the original window remain compatible.
    evening = service.create_selected("evening", "2026-10-30", uuid.uuid4().hex, token, rs.DEFAULT_TIMES)
    assert evening["period_end"] == "2026-10-30T21:30:00+08:00"


def test_observation_orders_nearest_first_null_last_and_listing_matches(db, monkeypatch):
    monkeypatch.setattr(pr, "now", lambda: pr.local("2026-10-30T16:00:00+08:00"))
    config = revised(evening="22:00", weekly="07:00", monthly="09:10")
    service = pr.Service(db, lambda: config, lambda: None)
    rows = service.observation()["schedules"]
    assert [row["kind"] for row in rows] == ["evening", "monthly", "weekly", "morning", "noon", "snapshot_cleanup"]
    assert rows[0]["next_run_at"] == service.listing("daily")["next_edition_at"] == "2026-10-30T22:00:00+08:00"
    assert rows[0]["rule"] == "每天 22:00"
    monkeypatch.setattr(pr, "calendar_data", lambda: {})
    assert [row["kind"] for row in service.observation()["schedules"]][-2:] == ["morning", "noon"]


def test_settings_api_save_reload_preserves_history_and_wakes_scheduler(tmp_path, monkeypatch):
    import app
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(app, "serialize_runtime_settings", lambda: settings.load_app_settings())
    monkeypatch.setattr(app.CLASSIFICATION_SERVICE, "configure", lambda **kwargs: None)
    monkeypatch.setattr(app, "refresh_agent_session_expiries", lambda: None)
    monkeypatch.setattr(pr, "now", lambda: pr.local("2026-10-30T16:00:00+08:00"))
    app.REPORT_SERVICE.schedule_wake.clear()
    client = app.app.test_client()
    payload = {"llm": settings.default_app_settings()["llm"], "report_schedule": {"evening": "22:00"}}
    response = client.put("/api/settings", json=payload)
    assert response.status_code == 200
    assert app.REPORT_SERVICE.schedule_wake.is_set()
    saved = settings.load_app_settings()
    assert saved["report_schedule"]["evening"] == "22:00"
    assert len(saved["report_schedule_history"]) == 2
    assert rs.Schedule(saved).occurrence(date(2026, 10, 29), "evening")[0].hour == 21
    # Older clients and unrelated saves retain the schedule and revision log.
    assert client.put("/api/settings", json={"llm": payload["llm"]}).status_code == 200
    assert settings.load_app_settings()["report_schedule_history"] == saved["report_schedule_history"]
    before = (tmp_path / "settings.json").read_bytes()
    response = client.put("/api/settings", json={**payload, "report_schedule": {"noon": "06:00"}})
    assert response.status_code == 400 and response.json["error"] == "invalid_report_schedule_order"
    assert (tmp_path / "settings.json").read_bytes() == before
    for kind, value in (("morning", "08:29"), ("noon", "14:29"), ("evening", "21:29")):
        response = client.put("/api/settings", json={**payload, "report_schedule": {kind: value}})
        assert response.status_code == 400 and response.json["error"] == "invalid_report_schedule_window"
        assert (tmp_path / "settings.json").read_bytes() == before


def test_old_early_schedules_wait_for_complete_window_without_rewriting_settings(db):
    config = revised(morning="07:30", noon="13:30", evening="20:00")
    before = json.dumps(config, sort_keys=True)
    rows = pr.slots(date(2026, 11, 2), pr.calendar_data(), rs.Schedule(config))
    assert all(scheduled >= end for _, scheduled, _, end in rows)
    assert next(row[1] for row in rows if row[0] == "morning").hour == 8
    assert json.dumps(config, sort_keys=True) == before
    with closing(db()) as conn:
        assert pr.enqueue_due(conn, pr.local("2026-10-30T20:30:00+08:00"), schedule=rs.Schedule(config)) == 0
        assert pr.enqueue_due(conn, pr.local("2026-10-30T21:30:00+08:00"), schedule=rs.Schedule(config)) == 1


def test_delay_does_not_expand_snapshot_and_old_report_retry_keeps_its_window(db, monkeypatch):
    monkeypatch.setattr(pr, "now", lambda: pr.local("2026-10-30T14:35:00+08:00"))
    config = revised(at="2026-10-30T12:00:00+08:00", noon="14:35", evening="22:00")
    service = pr.Service(db, lambda: config, lambda: None, runner)
    with closing(db()) as conn:
        for ident, published in (("inside", "14:29"), ("cutoff", "14:30"), ("after", "14:34")):
            news(conn, ident, "2026-10-30T"+published+":00+08:00")
        pr.enqueue_due(conn, pr.now(), schedule=rs.Schedule(config))
        row = conn.execute("SELECT * FROM periodic_reports WHERE kind='noon'").fetchone()
        assert row["scheduled_at"].endswith("14:35:00+08:00")
        assert row["period_end"].endswith("14:30:00+08:00")
        assert [item["id"] for item in pr.capture(conn, row)["sources"]] == ["inside"]
        # A previously created task retains its original cutoff after the upgrade.
        conn.execute("UPDATE periodic_reports SET status='failed',attempts=3,period_end=? WHERE id=?",
                     ("2026-10-30T14:35:00+08:00", row["id"]))
        conn.commit()
    service.retry(row["id"], service.manual_options()["database_token"])
    assert service.run_one()
    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM periodic_reports WHERE id=?", (row["id"],)).fetchone()
        assert row["period_end"].endswith("14:35:00+08:00")
        assert {item["id"] for item in pr.stored_snapshot(conn, row)["sources"]} == {"inside", "cutoff", "after"}


def test_stale_coupled_manual_form_cannot_silently_change_window(db, monkeypatch):
    import app
    monkeypatch.setattr(pr, "now", lambda: pr.local("2026-10-31T12:00:00+08:00"))
    service = pr.Service(db, lambda: revised(evening="22:00"), lambda: None)
    monkeypatch.setattr(app, "REPORT_SERVICE", service)
    monkeypatch.setattr(service, "start", lambda: None)
    data = service.manual_options()
    assert data["schedule_times"]["evening"] == "22:00" and data["window_times"]["evening"] == "21:30"
    payload = {"kind": "evening", "selection": "2026-10-30", "request_id": uuid.uuid4().hex,
               "database_token": data["database_token"], "schedule_times": data["schedule_times"]}
    client = app.app.test_client()
    response = client.post("/api/reports/manual", json=payload)
    assert response.status_code == 409 and response.json["error"] == "report_window_changed"
    del payload["schedule_times"]
    response = client.post("/api/reports/manual", json=payload)
    assert response.status_code == 202
    assert response.json["report"]["period_end"] == "2026-10-30T21:30:00+08:00"
