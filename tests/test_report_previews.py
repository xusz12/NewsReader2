"""Manual tests reuse the production pipeline, without advancing its schedule."""
from contextlib import closing
import json
import threading
import uuid

import pytest

import periodic_reports as pr
from test_periodic_reports import db, news, due, runner, SETTINGS


@pytest.fixture
def service(db, monkeypatch):
    monkeypatch.setattr(pr, "now", lambda: pr.local("2026-10-30T16:00:00+08:00"))
    return pr.Service(db, lambda: SETTINGS, lambda: None, runner)


def create(service, kind="morning", request_id=None):
    options = service.preview_options()
    period = next(p for p in options["periods"] if p["kind"] == kind)
    return service.create_preview(kind, period["scheduled_at"], request_id or uuid.uuid4().hex, options["database_token"])


def test_offers_complete_periods_without_waiting_for_schedule(service, db):
    options = {p["kind"]: p for p in service.preview_options()["periods"]}
    assert set(options) == set(pr.KINDS)
    assert options["morning"]["scheduled_at"] == "2026-10-30T08:30:00+08:00"
    assert options["noon"]["scheduled_at"] == "2026-10-30T14:30:00+08:00"
    assert options["evening"]["scheduled_at"] == "2026-10-29T21:30:00+08:00"
    assert options["weekly"]["period_start"] == "2026-10-19T00:00:00+08:00"
    assert options["weekly"]["period_end"] == "2026-10-26T00:00:00+08:00"
    assert options["monthly"]["period_start"] == "2026-09-01T00:00:00+08:00"
    with closing(db()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM periodic_reports").fetchone()[0] == 0


@pytest.mark.parametrize("kind,at", [("weekly", "2026-11-02T07:00:00+08:00"), ("monthly", "2026-11-01T07:00:00+08:00")])
def test_completed_week_or_month_can_be_tested_before_0830(service, monkeypatch, kind, at):
    monkeypatch.setattr(pr, "now", lambda: pr.local(at))
    row = create(service, kind)
    assert pr.local(row["period_end"]) < pr.now() < pr.local(row["scheduled_at"])


def test_trial_refreshes_and_runs_full_window_even_before_activation(service, db):
    with closing(db()) as conn:
        before = tuple(conn.execute("SELECT * FROM periodic_report_meta").fetchone())
    calls = []
    def refresh():
        calls.append("refresh")
        with closing(db()) as conn: news(conn, "newly-indexed", "2026-10-30T07:00:00+08:00")
    def model(config, prompt): calls.append("model"); return runner(config, prompt)
    service.refresh_index, service.runner = refresh, model
    row = create(service)
    assert row["status"] == "pending" and row["is_preview"]
    assert service.run_one()
    with closing(db()) as conn:
        job = conn.execute("SELECT * FROM periodic_reports WHERE id=?", (row["id"],)).fetchone()
        assert job["status"] == "done"
        snapshot = pr.stored_snapshot(conn, job)
        assert snapshot["period_start"] == "2026-10-29T21:30:00+08:00"
        assert [s["id"] for s in snapshot["sources"]] == ["newly-indexed"]
        assert tuple(conn.execute("SELECT * FROM periodic_report_meta").fetchone()) == before
        assert conn.execute("SELECT COUNT(*) FROM item_state").fetchone()[0] == 0
    assert calls == ["refresh", "model"]


def test_request_replay_and_concurrent_clicks_create_one_trial(service, db):
    request_id = uuid.uuid4().hex
    results, errors = [], []
    def submit():
        try: results.append(create(service, request_id=request_id))
        except Exception as exc: errors.append(exc)
    threads = [threading.Thread(target=submit) for _ in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(3)
    assert not errors and len(results) == 2 and results[0]["id"] == results[1]["id"]
    service.run_one()
    assert create(service, request_id=request_id)["status"] == "done"
    with closing(db()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM periodic_reports").fetchone()[0] == 1
    with pytest.raises(ValueError, match="report_preview_conflict"):
        create(service, "noon", request_id)


def test_restart_keeps_requested_intraday_trial_and_skips_automatic_intraday(service, db):
    automatic = due(db)
    manual = create(service)
    with closing(db()) as conn:
        pr.enqueue_due(conn, pr.now(), intraday_since=pr.now())
        assert conn.execute("SELECT status FROM periodic_reports WHERE id=?", (manual["id"],)).fetchone()[0] == "pending"
        assert conn.execute("SELECT status FROM periodic_reports WHERE id=?", (automatic["id"],)).fetchone()[0] == "skipped"


@pytest.mark.parametrize("kind,scheduled,request_id", [
    ("evening", "2026-10-30T21:30:00+08:00", uuid.uuid4().hex),
    ("morning", "2026-10-25T08:30:00+08:00", uuid.uuid4().hex),
    ("morning", "2026-10-30T08:31:00+08:00", uuid.uuid4().hex),
    ("weekly", "invalid", uuid.uuid4().hex),
    ([], "2026-10-30T08:30:00+08:00", uuid.uuid4().hex),
    ("morning", "2026-10-30T08:30:00+08:00", "invalid"),
])
def test_invalid_or_future_editions_cannot_be_requested(service, kind, scheduled, request_id):
    with pytest.raises(ValueError, match="report_preview_invalid"):
        service.create_preview(kind, scheduled, request_id, service.preview_options()["database_token"])


def test_delete_trial_cleans_only_its_steps_and_protects_formal_reports(service, db):
    formal = due(db)
    preview = create(service)
    token = service.preview_options()["database_token"]
    with closing(db()) as conn, conn:
        conn.execute("INSERT INTO periodic_report_steps VALUES (?, 'step', '{}')", (preview["id"],))
    with pytest.raises(ValueError, match="report_preview_only"):
        service.delete_preview(formal["id"], token)
    with pytest.raises(ValueError, match="report_database_changed"):
        service.delete_preview(preview["id"], "different-database")
    service.delete_preview(preview["id"], token)
    with closing(db()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM periodic_report_steps").fetchone()[0] == 0
        assert conn.execute("SELECT id FROM periodic_reports").fetchone()[0] == formal["id"]


def test_cannot_delete_running_trial(service):
    preview = create(service)
    assert service.claim()["id"] == preview["id"]
    with pytest.raises(ValueError, match="report_preview_running"):
        service.delete_preview(preview["id"], service.preview_options()["database_token"])


def test_manual_api_queues_real_pipeline_without_changing_schedule(service, db, monkeypatch):
    import app
    monkeypatch.setattr(app, "db_conn", db)
    monkeypatch.setattr(app, "REPORT_SERVICE", service)
    monkeypatch.setattr(service, "start", lambda: None)
    client = app.app.test_client()
    options = client.get('/api/reports/preview-options').json
    period = options["periods"][0]
    request = {"kind": period["kind"], "scheduled_at": period["scheduled_at"], "request_id": uuid.uuid4().hex, "database_token": options["database_token"]}
    response = client.post('/api/reports/previews', json=request)
    assert response.status_code == 202 and response.json["report"]["is_preview"]
    ident = response.json["report"]["id"]
    assert client.post('/api/reports/previews', json=request).json["report"]["id"] == ident
    assert client.get('/api/reports/' + ident).json["report"]["status"] == "pending"
    assert client.delete('/api/reports/' + ident + '/preview', json={"database_token": options["database_token"]}).json["ok"]
