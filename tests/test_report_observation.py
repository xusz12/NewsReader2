"""Schedule observation is metadata-only and must not advance any work."""
from contextlib import closing
import json
import sqlite3
from types import SimpleNamespace

import pytest
import periodic_reports as pr
from test_periodic_reports import db, due, SETTINGS
from test_report_previews import create


@pytest.fixture
def service(db, monkeypatch):
    monkeypatch.setattr(pr, "now", lambda: pr.local("2026-10-30T16:00:00+08:00"))
    def forbidden(*args):
        pytest.fail("Observation must not resolve model configuration, refresh news, or call the model")
    return pr.Service(db, lambda: SETTINGS, forbidden, forbidden)


def snapshot(db):
    with closing(db()) as conn:
        return {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")]
                for table in ("periodic_report_meta", "periodic_reports", "periodic_report_steps", "periodic_report_snapshot_cleanup", "periodic_report_snapshots", "periodic_report_schedule_pauses", "item_state")}


def test_read_only_calendar_and_empty_state(service, db, monkeypatch):
    monkeypatch.setattr(pr.report_model, "configuration", lambda *args, **kwargs: pytest.fail("Observation must not resolve model configuration"))
    before = snapshot(db)
    data = service.observation()
    assert snapshot(db) == before
    assert data["scheduler"]["status"] == "not_running"
    assert not data["worker_running"] and not data["calendar_warning"]
    assert data["queue"] == {"running": 0, "pending": 0, "failed": 0, "skipped": 0}
    assert data["recent"] == [] and data["current"] is None
    times = {row["kind"]: row["next_run_at"] for row in data["schedules"]}
    assert times == {"morning": "2026-11-02T08:30:00+08:00", "noon": "2026-11-02T14:30:00+08:00",
                     "evening": "2026-10-30T21:30:00+08:00", "weekly": "2026-11-02T08:30:00+08:00",
                     "monthly": "2026-11-01T08:30:00+08:00", "snapshot_cleanup": "2026-11-06T10:00:00+08:00"}


def test_queue_errors_phases_and_automatic_latest_exclude_samples(service, db):
    formal = due(db)
    service.settings = lambda: SETTINGS  # Creating fixtures, not observing.
    trial = create(service, "noon")
    imported = create(service, "weekly")
    failed = create(service, "morning")
    with closing(db()) as conn:
        conn.execute("UPDATE periodic_reports SET status='pending',error='report_index_permission_denied',attempts=1,updated_at='2026-10-30T16:00:00+08:00',next_attempt=? WHERE id=?",
                     (pr.now().timestamp()+300, formal["id"]))
        conn.execute("UPDATE periodic_reports SET status='running',snapshot_json='SECRET-MATERIAL',config_json='SECRET-CONFIG',result_json='INVALID-JSON',updated_at='2026-10-30T15:59:00+08:00' WHERE id=?", (trial["id"],))
        conn.execute("UPDATE periodic_reports SET status='done',edition_key='requested-preview:weekly' WHERE id=?", (imported["id"],))
        conn.execute("UPDATE periodic_reports SET status='failed',error='report_model_timeout',attempts=3 WHERE id=?", (failed["id"],))
        conn.commit()
    before = snapshot(db)
    data = service.observation()
    assert snapshot(db) == before
    assert data["queue"] == {"running": 1, "pending": 1, "failed": 1, "skipped": 0}
    assert data["retrying"] == 1 and data["current"]["phase"] == "editing"
    assert data["current"]["trigger"] == "preview"
    recent = {row["id"]: row for row in data["recent"]}
    assert trial["id"] in recent
    assert recent[formal["id"]]["next_retry_at"] == "2026-10-30T16:05:00+08:00"
    assert recent[failed["id"]]["next_retry_at"] is None
    assert recent[imported["id"]]["trigger"] == "imported"
    latest = {row["kind"]: row["latest"] for row in data["schedules"]}
    assert latest["noon"]["id"] == formal["id"] and latest["weekly"] is None
    assert "SECRET" not in json.dumps(data) and "INVALID-JSON" not in json.dumps(data)
    with closing(db()) as conn:
        conn.execute("UPDATE periodic_reports SET snapshot_json=NULL WHERE id=?", (trial["id"],)); conn.commit()
    assert service.observation()["current"]["phase"] == "indexing"


def test_health_uses_successful_check_for_current_database(service, db, monkeypatch):
    service.scheduler_thread = SimpleNamespace(is_alive=lambda: True)
    service.threads = [SimpleNamespace(is_alive=lambda: True)]
    service.settings = lambda: SETTINGS
    assert service.observation()["scheduler"]["status"] == "starting"
    service.schedule_once()
    assert service.observation()["scheduler"]["status"] == "running"
    assert service.observation()["worker_running"]
    monkeypatch.setattr(pr, "now", lambda: pr.local("2026-10-30T16:02:00+08:00"))
    assert service.observation()["scheduler"]["status"] == "delayed"
    service.last_schedule_error = "report_schedule_failed"
    assert service.observation()["scheduler"]["status"] == "error"
    service.schedule_once()
    assert service.observation()["scheduler"]["error"] == ""
    with closing(db()) as conn:
        conn.execute("UPDATE periodic_report_meta SET database_token='replacement'"); conn.commit()
    assert service.observation()["scheduler"]["last_checked_at"] is None
    assert service.observation()["scheduler"]["status"] == "starting"
    service.stopped.set()
    assert service.observation()["scheduler"]["status"] == "stopped"
    assert not service.observation()["worker_running"]


@pytest.mark.parametrize("broken", [False, True])
def test_missing_or_broken_calendar_visible_without_guessing_trading_days(service, monkeypatch, broken):
    def calendar():
        if broken: raise OSError("SECRET-PATH")
        return {}
    monkeypatch.setattr(pr, "calendar_data", calendar)
    data = service.observation()
    assert data["calendar_warning"]
    assert data["scheduler"]["error"] == ("report_calendar_failed" if broken else "")
    times = {row["kind"]: row["next_run_at"] for row in data["schedules"]}
    assert times["morning"] is None and times["noon"] is None
    assert times["evening"] and times["weekly"] and times["monthly"]
    assert "SECRET" not in json.dumps(data)


def test_history_is_bounded_and_endpoint_cannot_start_work(service, db, monkeypatch):
    service.settings = lambda: SETTINGS
    for _ in range(25): create(service)
    token = service.manual_options()["database_token"]
    custom = service.create_manual("custom", "2026-10-29T10:00:00+08:00", "2026-10-29T11:00:00+08:00", "a"*32, token)
    with closing(db()) as conn:
        conn.execute("UPDATE periodic_reports SET updated_at='2026-10-30T16:01:00+08:00' WHERE id=?", (custom["id"],))
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM periodic_reports").fetchone()[0] == 26
    recent = service.observation()["recent"]
    assert len(recent) == 20
    assert recent[0]["id"] == custom["id"]
    assert sum(row["kind"] == "morning" for row in recent) == 19
    import app
    monkeypatch.setattr(app, "REPORT_SERVICE", service)
    monkeypatch.setattr(service, "start", lambda: pytest.fail("Status route must not start work"))
    before = snapshot(db)
    client = app.app.test_client()
    response = client.get("/api/reports/schedule-status")
    assert response.status_code == 200 and response.json["ok"]
    assert len(response.json["recent"]) == 20
    assert snapshot(db) == before
    def unavailable(): raise sqlite3.OperationalError("SECRET-DATABASE-PATH")
    monkeypatch.setattr(service, "connect", unavailable)
    response = client.get("/api/reports/schedule-status")
    assert response.status_code == 503
    assert response.json == {"ok": False, "error": "report_schedule_unavailable"}
