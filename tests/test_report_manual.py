"""Manual windows, deletion durability and persisted progress; isolated news only."""
from contextlib import closing
import json
import threading
import uuid

import pytest
import periodic_reports as pr
from test_periodic_reports import db, due, news, runner, SETTINGS


@pytest.fixture
def service(db, monkeypatch):
    monkeypatch.setattr(pr, "now", lambda: pr.local("2026-10-30T16:00:00+08:00"))
    return pr.Service(db, lambda: SETTINGS, lambda: None, runner)


def create(service, kind="evening", start="2026-10-19T00:00:00+08:00", end="2026-10-26T00:00:00+08:00", request_id=None):
    return service.create_manual(kind, start, end, request_id or uuid.uuid4().hex, service.manual_options()["database_token"])


def test_archive_uses_covered_period_not_creation_date_and_returns_all_groups(service):
    current = create(service, "morning", "2026-09-30T21:30:00+08:00", "2026-10-01T08:30:00+08:00")
    old = create(service, "evening", "2026-08-30T21:30:00+08:00", "2026-08-31T21:30:00+08:00")
    custom = create(service, "custom", "2026-09-30T10:00:00+08:00", "2026-10-02T10:00:00+08:00")
    listing = service.listing("daily", group="current")
    assert listing["group"] == "2026-10"
    assert [row["id"] for row in listing["items"]] == [current["id"]]
    assert listing["groups"] == [{"key": month, "count": 1, "active_count": 1} for month in ("2026-10", "2026-09", "2026-08")]
    assert [row["id"] for row in service.listing("daily")["items"]] == [current["id"], custom["id"], old["id"]]
    weekly = create(service, "weekly", "2026-09-28T00:00:00+08:00", "2026-10-05T00:00:00+08:00")
    assert service.listing("weekly", group="current")["items"] == []
    assert service.listing("weekly", group="2026-09")["items"][0]["id"] == weekly["id"]
    monthly = create(service, "monthly", "2025-12-01T00:00:00+08:00", "2026-01-01T00:00:00+08:00")
    assert service.listing("monthly", group="current")["items"] == []
    assert service.listing("monthly", group="2025")["items"][0]["id"] == monthly["id"]


def test_archive_pagination_keeps_complete_counts_and_no_duplicate_rows(service):
    for _ in range(33):
        create(service, "evening", "2026-09-29T21:30:00+08:00", "2026-09-30T21:30:00+08:00")
    first = service.listing("daily", group="2026-09")
    second = service.listing("daily", offset=30, group="2026-09")
    assert first["total"] == second["total"] == first["groups"][0]["count"] == 33
    assert first["has_more"] and not second["has_more"]
    assert len({row["id"] for row in first["items"] + second["items"]}) == 33


@pytest.mark.parametrize("kind,group", [("daily", "2026-13"), ("weekly", "2026"), ("monthly", "2026-10"), ("daily", "x' OR 1=1")])
def test_archive_rejects_invalid_groups(service, kind, group):
    with pytest.raises(ValueError, match="invalid_report_group"):
        service.listing(kind, group=group)


def test_defaults_cover_six_types_without_changing_schedule(service, db, monkeypatch):
    monkeypatch.setattr(pr.report_model, "capacity", lambda *args: pytest.fail("Opening the form must not probe a model"))
    with closing(db()) as conn: before = tuple(conn.execute("SELECT * FROM periodic_report_meta").fetchone())
    data = service.manual_options()
    assert [p["kind"] for p in data["periods"]] == list(pr.REPORT_KINDS)
    assert data["model_label"] == "Pi · test"
    assert all(pr.local(p["period_start"]) < pr.local(p["period_end"]) <= pr.now() for p in data["periods"])
    monkeypatch.setattr(pr, "calendar_data", lambda: {})
    assert len(service.manual_options()["periods"]) == 6
    service.settings = lambda: {**SETTINGS, "feature_models": {"reports": {
        "runtime": "codex", "codex": {"model": "report-model", "reasoning_effort": "high", "provider": "private-provider"},
        "pi": {"model": "unused-model"}}}}
    assert service.manual_options()["model_label"] == "Codex · report-model · high"
    service.settings = lambda: {"agent": {"runtime": "pi"}, "llm": {"pi_chat": {}}}
    assert service.manual_options()["model_label"] == "Pi · 未配置模型"
    with closing(db()) as conn:
        assert tuple(conn.execute("SELECT * FROM periodic_report_meta").fetchone()) == before
        assert conn.execute("SELECT COUNT(*) FROM periodic_reports").fetchone()[0] == 0


def test_custom_window_refresh_capture_and_actual_progress(service, db):
    row = create(service, "weekly")
    calls, stages = [], []
    def refresh():
        calls.append("refresh")
        with closing(db()) as conn:
            news(conn, "inside", "2026-10-19T00:00:00+08:00")
            news(conn, "outside", "2026-10-26T00:00:00+08:00")
    def model(config, prompt):
        calls.append("model")
        with closing(db()) as conn:
            current = pr.serialize(conn.execute("SELECT * FROM periodic_reports WHERE id=?", (row["id"],)).fetchone())
            assert current["progress"] == {"stage": "writing", "source_count": 1}
            assert current["started_at"] and current["status"] == "running"
        with pytest.raises(ValueError, match="report_delete_running"):
            service.delete(row["id"], service.manual_options()["database_token"])
        return runner(config, prompt)
    original = service.update_progress
    def progress(job, stage, **fields):
        stages.append(stage); original(job, stage, **fields)
    service.refresh_index, service.runner, service.update_progress = refresh, model, progress
    with closing(db()) as conn: before = tuple(conn.execute("SELECT * FROM periodic_report_meta").fetchone())
    assert service.run_one()
    assert calls == ["refresh", "model"]
    assert stages == ["waiting_material", "indexing", "capturing", "preparing", "writing", "validating", "saving"]
    with closing(db()) as conn:
        result = conn.execute("SELECT * FROM periodic_reports WHERE id=?", (row["id"],)).fetchone()
        assert result["status"] == "done"
        assert json.loads(result["progress_json"])["stage"] == "done"
        assert [s["id"] for s in pr.stored_snapshot(conn, result)["sources"]] == ["inside"]
        assert tuple(conn.execute("SELECT * FROM periodic_report_meta").fetchone()) == before
        assert conn.execute("SELECT COUNT(*) FROM item_state").fetchone()[0] == 0


def test_same_request_is_idempotent_but_changed_window_is_rejected(service, db):
    request_id = uuid.uuid4().hex
    rows = []
    threads = [threading.Thread(target=lambda: rows.append(create(service, request_id=request_id))) for _ in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(3)
    assert len(rows) == 2 and rows[0]["id"] == rows[1]["id"]
    assert rows[0]["is_manual"] and not rows[0]["is_preview"]
    with pytest.raises(ValueError, match="report_manual_conflict"):
        create(service, start="2026-10-20T00:00:00+08:00", request_id=request_id)
    service.delete(rows[0]["id"], service.manual_options()["database_token"])
    with pytest.raises(ValueError, match="report_deleted"):
        create(service, request_id=request_id)
    assert create(service)["id"] != rows[0]["id"]


@pytest.mark.parametrize("kind,start,end,request_id", [
    ([], "2026-10-19", "2026-10-26", uuid.uuid4().hex),
    ("daily", "2026-10-19", "2026-10-26", uuid.uuid4().hex),
    ("evening", "2026-10-26", "2026-10-26", uuid.uuid4().hex),
    ("evening", "2026-10-27", "2026-10-26", uuid.uuid4().hex),
    ("evening", "2026-10-29", "2026-10-30T16:00:01+08:00", uuid.uuid4().hex),
    ("weekly", None, "2026-10-26", uuid.uuid4().hex),
    ("monthly", "invalid", "2026-10-26", uuid.uuid4().hex),
    ("morning", "2026-10-19", "2026-10-26", "invalid"),
])
def test_invalid_windows_rejected_before_any_write(service, db, kind, start, end, request_id):
    with pytest.raises(ValueError, match="report_window_invalid"):
        create(service, kind, start, end, request_id)
    with closing(db()) as conn: assert conn.execute("SELECT COUNT(*) FROM periodic_reports").fetchone()[0] == 0


def test_delete_formal_report_survives_schedule_replay_and_keeps_news(service, db):
    formal = due(db, "2026-10-30T21:30:00+08:00")
    token = service.manual_options()["database_token"]
    with closing(db()) as conn:
        news(conn, "keep")
        conn.execute("INSERT INTO periodic_report_steps VALUES (?, 'cached', '{}')", (formal["id"],)); conn.commit()
    service.delete(formal["id"], token)
    with closing(db()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM periodic_report_steps WHERE report_id=?", (formal["id"],)).fetchone()[0] == 0
        conn.execute("UPDATE periodic_report_meta SET scheduled_through=activated_at"); conn.commit()
        pr.initialize(conn)
        pr.enqueue_due(conn, pr.local("2026-10-30T21:30:00+08:00"), intraday_since=pr.local("2026-10-30T10:00:00+08:00"))
        assert conn.execute("SELECT COUNT(*) FROM periodic_reports WHERE edition_key=?", (formal["edition_key"],)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1


def test_manual_morning_not_skipped_by_restart_and_excluded_from_auto_latest(service, db):
    row = create(service, "morning")
    with closing(db()) as conn: pr.enqueue_due(conn, pr.now(), intraday_since=pr.now())
    data = service.observation()
    assert next(item for item in data["recent"] if item["id"] == row["id"])["trigger"] == "manual"
    assert next(item for item in data["schedules"] if item["kind"] == "morning")["latest"] is None
    assert service.listing("daily")["items"][0]["status"] == "pending"


def test_manual_api_and_delete_scope(service, db, monkeypatch):
    import app
    monkeypatch.setattr(app, "REPORT_SERVICE", service)
    monkeypatch.setattr(app, "db_conn", db)
    monkeypatch.setattr(service, "start", lambda: None)
    client = app.app.test_client()
    data = client.get("/api/reports/manual-options").json
    period = data["periods"][0]
    payload = {**period, "request_id": uuid.uuid4().hex, "database_token": data["database_token"]}
    assert client.post("/api/reports/manual", json={**payload, "database_token": "stale"}).status_code == 409
    response = client.post("/api/reports/manual", json=payload)
    assert response.status_code == 202 and response.json["report"]["is_manual"]
    ident = response.json["report"]["id"]
    assert client.delete("/api/reports/"+ident, json={"database_token": "stale"}).status_code == 409
    assert client.get("/api/reports/"+ident).status_code == 200
    assert client.delete("/api/reports/"+ident, json={"database_token": data["database_token"]}).status_code == 200
    assert client.get("/api/reports/"+ident).status_code == 404


@pytest.mark.parametrize("kind,selection,start,end", [
    ("morning", "2026-01-01", "2025-12-31T21:30:00+08:00", "2026-01-01T08:30:00+08:00"),
    ("noon", "2026-10-29", "2026-10-29T08:30:00+08:00", "2026-10-29T14:30:00+08:00"),
    ("evening", "2026-10-29", "2026-10-28T21:30:00+08:00", "2026-10-29T21:30:00+08:00"),
    ("weekly", "2026-10-21", "2026-10-21T00:00:00+08:00", "2026-10-28T00:00:00+08:00"),
    ("weekly", "2025-12-29", "2025-12-29T00:00:00+08:00", "2026-01-05T00:00:00+08:00"),
    ("monthly", "2024-02", "2024-02-01T00:00:00+08:00", "2024-03-01T00:00:00+08:00"),
    ("monthly", "2025-02", "2025-02-01T00:00:00+08:00", "2025-03-01T00:00:00+08:00"),
    ("monthly", "2025-12", "2025-12-01T00:00:00+08:00", "2026-01-01T00:00:00+08:00"),
])
def test_date_selection_resolves_exact_beijing_window(service, kind, selection, start, end):
    row = service.create_selected(kind, selection, uuid.uuid4().hex, service.manual_options()["database_token"])
    assert row["period_start"] == start and row["period_end"] == end


@pytest.mark.parametrize("kind,selection", [("weekly", "2026-10-24"), ("monthly", "2026-10"),
    ("evening", "2026-10-30"), ("monthly", "2026-13"), ("morning", "2026-02-29"),
    ("custom", "2026-10-29"), ([], "2026-10-29"), ("weekly", "20261020"), ("noon", None)])
def test_invalid_or_unfinished_selected_windows_rejected(service, kind, selection):
    with pytest.raises(ValueError, match="report_window_invalid"):
        service.create_selected(kind, selection, uuid.uuid4().hex, service.manual_options()["database_token"])


def test_custom_report_runs_and_appears_in_daily_without_adding_an_automatic_schedule(service, db):
    row = create(service, "custom", "2026-10-29T10:15:00+08:00", "2026-10-29T11:45:00+08:00")
    assert row["kind_label"] == "自定义" and "自定义报告" in row["title"]
    with closing(db()) as conn: news(conn, "custom-window", "2026-10-29T10:30:00+08:00")
    assert service.run_one()
    result = service.listing("daily")["items"][0]
    assert result["id"] == row["id"] and result["status"] == "done"
    assert {item["kind"] for item in service.observation()["schedules"]} == set(pr.KINDS) | {"snapshot_cleanup"}


def test_api_resolves_selection_instead_of_accepting_conflicting_client_range(service, monkeypatch):
    import app
    monkeypatch.setattr(app, "REPORT_SERVICE", service)
    monkeypatch.setattr(service, "start", lambda: None)
    response = app.app.test_client().post("/api/reports/manual", json={"kind": "weekly", "selection": "2026-10-21",
        "period_start": "2026-01-01T00:00:00+08:00", "period_end": "2026-01-02T00:00:00+08:00",
        "request_id": uuid.uuid4().hex, "database_token": service.manual_options()["database_token"]})
    assert response.status_code == 202
    assert response.json["report"]["period_start"] == "2026-10-21T00:00:00+08:00"
    assert response.json["report"]["period_end"] == "2026-10-28T00:00:00+08:00"
