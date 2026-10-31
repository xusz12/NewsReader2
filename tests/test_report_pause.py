"""An explicit pause skips editions, unlike normal downtime or a slow model."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import sqlite3

import pytest
import periodic_reports as pr
from test_periodic_reports import db, SETTINGS, runner


@pytest.fixture
def service(db):
    return pr.Service(db, lambda: SETTINGS, lambda: None, runner)


def change(service, db, monkeypatch, paused, at):
    monkeypatch.setattr(pr, "now", lambda: pr.local(at))
    with closing(db()) as conn:
        token = conn.execute("SELECT database_token FROM periodic_report_meta").fetchone()[0]
    return service.set_schedule_paused(paused, token)


def editions(db):
    with closing(db()) as conn:
        return {row[0] for row in conn.execute("SELECT edition_key FROM periodic_reports")}


def test_pause_survives_restart_and_skips_all_five_kinds_then_future_runs(service, db, monkeypatch):
    change(service, db, monkeypatch, True, "2026-10-30T12:00:00+08:00")
    with closing(db()) as conn:
        pr.initialize(conn, pr.local("2026-11-02T08:00:00+08:00"))
        assert pr.schedule_control(conn)["paused"]
    restarted = pr.Service(db, lambda: SETTINGS, lambda: None, runner)
    assert restarted.schedule_once(pr.local("2026-11-02T08:59:59+08:00")) == 30
    assert editions(db) == set()
    change(restarted, db, monkeypatch, False, "2026-11-02T09:00:00+08:00")
    restarted.schedule_once()
    assert editions(db) == set()  # Includes missed month/week, not just intraday.
    restarted.schedule_once(pr.local("2026-11-02T14:30:00+08:00"))
    assert editions(db) == {"noon:2026-11-02"}
    restarted.schedule_once(pr.local("2026-11-02T21:30:00+08:00"))
    assert editions(db) == {"noon:2026-11-02", "evening:2026-11-02"}
    with closing(db()) as conn:
        evening = conn.execute("SELECT * FROM periodic_reports WHERE kind='evening'").fetchone()
        assert evening["period_start"] == "2026-11-01T21:30:00+08:00"  # No multi-day makeup window.


def test_downtime_before_and_after_pause_still_catches_up(service, db, monkeypatch):
    change(service, db, monkeypatch, True, "2026-10-31T12:00:00+08:00")
    change(service, db, monkeypatch, False, "2026-11-02T09:00:00+08:00")
    # No scheduler call until after another normal shutdown.
    service.schedule_once(pr.local("2026-11-04T09:00:00+08:00"))
    assert editions(db) == {"evening:2026-10-30", "evening:2026-11-02", "evening:2026-11-03"}


def test_repeated_requests_do_not_move_pause_boundaries(service, db, monkeypatch):
    first = change(service, db, monkeypatch, True, "2026-10-30T12:00:00+08:00")
    second = change(service, db, monkeypatch, True, "2026-10-31T12:00:00+08:00")
    assert first == second
    change(service, db, monkeypatch, False, "2026-11-02T09:00:00+08:00")
    change(service, db, monkeypatch, False, "2026-11-04T09:00:00+08:00")
    with closing(db()) as conn:
        assert [tuple(row) for row in conn.execute("SELECT paused_at,resumed_at FROM periodic_report_schedule_pauses")] == [
            ("2026-10-30T12:00:00+08:00", "2026-11-02T09:00:00+08:00")]
    service.schedule_once()
    assert editions(db) == {"evening:2026-11-02", "evening:2026-11-03"}


def test_multiple_pauses_and_exact_resume_instant_are_excluded(service, db, monkeypatch):
    change(service, db, monkeypatch, True, "2026-10-30T12:00:00+08:00")
    change(service, db, monkeypatch, False, "2026-10-30T21:30:00+08:00")
    service.schedule_once()
    assert not editions(db)
    change(service, db, monkeypatch, True, "2026-11-01T08:30:00+08:00")
    change(service, db, monkeypatch, False, "2026-11-02T08:30:00+08:00")
    service.schedule_once()
    assert editions(db) == {"evening:2026-10-31"}
    # Even an old cursor cannot recreate deliberately skipped editions.
    with closing(db()) as conn, conn:
        conn.execute("UPDATE periodic_report_meta SET scheduled_through=activated_at")
    service.schedule_once()
    assert editions(db) == {"evening:2026-10-31"}


def test_pause_has_no_model_calendar_dependency_and_maintenance_still_runs(service, db, monkeypatch):
    result = change(service, db, monkeypatch, True, "2026-10-30T12:00:00+08:00")
    monkeypatch.setattr(pr, "calendar_data", lambda: pytest.fail("Paused scheduling must not read calendar"))
    service.settings = lambda: pytest.fail("Paused scheduling must not resolve models")
    assert service.schedule_once(pr.local("2026-11-06T10:00:00+08:00")) == 30
    assert service.ready.is_set() and not editions(db)
    with closing(db()) as conn:
        assert conn.execute("SELECT last_run_at FROM periodic_report_snapshot_cleanup").fetchone()[0]
    assert service.set_schedule_paused(False, result["database_token"])["schedule_control"]["paused"] is False


def test_manual_and_existing_jobs_continue_while_paused(service, db, monkeypatch):
    monkeypatch.setattr(pr, "now", lambda: pr.local("2026-10-30T14:30:00+08:00"))
    service.schedule_once()
    change(service, db, monkeypatch, True, "2026-10-30T14:31:00+08:00")
    token = service.manual_options()["database_token"]
    manual = service.create_manual("custom", "2026-10-29T10:00:00+08:00", "2026-10-29T11:00:00+08:00", "a"*32, token)
    assert service.run_one() and service.run_one()
    with closing(db()) as conn:
        assert {row[0] for row in conn.execute("SELECT status FROM periodic_reports")} == {"done"}
        assert conn.execute("SELECT status FROM periodic_reports WHERE id=?", (manual["id"],)).fetchone()[0] == "done"
    data = service.observation()
    assert data["schedule_control"]["paused"]
    assert all(row["next_run_at"] is None and row["paused"] for row in data["schedules"] if row["kind"] in pr.KINDS)
    assert data["schedules"][0]["kind"] == "snapshot_cleanup"
    assert service.listing("daily")["next_edition_at"] is None


def test_pause_between_scheduler_read_and_enqueue_prevents_insertion(service, db, monkeypatch):
    original = pr.report_model.configuration
    def pause_before_insert(*args, **kwargs):
        change(service, db, monkeypatch, True, "2026-10-30T21:30:00+08:00")
        return original(*args, **kwargs)
    monkeypatch.setattr(pr.report_model, "configuration", pause_before_insert)
    assert service.schedule_once(pr.local("2026-10-30T21:30:00+08:00")) == 30
    assert not editions(db)


def test_concurrent_pause_requests_create_one_interval(service, db, monkeypatch):
    monkeypatch.setattr(pr, "now", lambda: pr.local("2026-10-30T12:00:00+08:00"))
    token = service.manual_options()["database_token"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        calls = [pool.submit(service.set_schedule_paused, True, token) for _ in range(2)]
        assert calls[0].result(timeout=5) == calls[1].result(timeout=5)
    with closing(db()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM periodic_report_schedule_pauses").fetchone()[0] == 1


def test_schedule_edits_during_pause_do_not_bypass_skipping(service, db, monkeypatch):
    change(service, db, monkeypatch, True, "2026-10-30T12:00:00+08:00")
    times = {**pr.report_schedule.DEFAULT_TIMES, "evening": "22:00"}
    history = pr.report_schedule.changed_history({}, times, pr.local("2026-10-31T12:00:00+08:00"))
    service.settings = lambda: {**SETTINGS, "report_schedule": times, "report_schedule_history": history}
    change(service, db, monkeypatch, False, "2026-11-02T09:00:00+08:00")
    service.schedule_once()
    assert not editions(db)
    service.schedule_once(pr.local("2026-11-02T22:00:00+08:00"))
    assert editions(db) == {"evening:2026-11-02"}


def test_api_validates_identity_payload_and_sanitizes_storage_errors(service, db, monkeypatch):
    import app
    monkeypatch.setattr(app, "REPORT_SERVICE", service)
    client = app.app.test_client()
    for payload in (None, [], {}, {"paused": "false"}, {"paused": 1}):
        assert client.post("/api/reports/schedule-pause", json=payload).status_code == 400
    wrong = client.post("/api/reports/schedule-pause", json={"paused": True, "database_token": "old-db"})
    assert wrong.status_code == 409 and wrong.json["error"] == "report_database_changed"
    token = service.manual_options()["database_token"]
    service.schedule_wake.clear()
    result = client.post("/api/reports/schedule-pause", json={"paused": True, "database_token": token})
    assert result.status_code == 200 and result.json["schedule_control"]["paused"]
    assert service.schedule_wake.is_set()
    def broken(*args): raise sqlite3.OperationalError("private database path")
    monkeypatch.setattr(service, "set_schedule_paused", broken)
    response = client.post("/api/reports/schedule-pause", json={"paused": False, "database_token": token})
    assert response.status_code == 503 and "private" not in response.text
