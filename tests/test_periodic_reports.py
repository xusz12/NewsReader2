import json
import errno
import sqlite3
import threading
import time
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest

import periodic_reports as pr
from scanner import apply_schema

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(pr.report_model, "capacity", lambda config: {"context_window": 128000, "input_tokens": 100000, "output_tokens": 16000, "source": "test"})
    path = tmp_path / "reports.sqlite3"
    def connect():
        conn = sqlite3.connect(path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    with closing(connect()) as conn:
        apply_schema(conn, ROOT / "schema.sql")
        pr.initialize(conn, pr.local("2026-10-30T10:00:00+08:00"))
    return connect


def news(conn, ident, published="2026-10-30T12:00:00+08:00", title="存储新工艺开始试产"):
    conn.execute("""INSERT INTO items(id,source_file,item_order,published_at,date,title,summary,url,source_name,created_at,updated_at)
        VALUES (?,'test.md',0,?,?,?,'公司公布试产安排',?,'示例来源','2026-11-01 00:01:00','2026-11-01 00:01:00')""",
        (ident,published,published[:10],title,"https://example.com/"+ident))
    conn.commit()


def due(db, at="2026-10-30T14:30:00+08:00"):
    with closing(db()) as conn:
        pr.enqueue_due(conn, pr.local(at))
        return dict(conn.execute("SELECT * FROM periodic_reports ORDER BY scheduled_at LIMIT 1").fetchone())


def runner(config, prompt):
    material = json.loads(prompt.rsplit("\n新闻资料：", 1)[1])
    if "本批仅做选材" in prompt:
        return pr.encoded({"events":[{"text":item.get("title",item.get("text")),"refs":item["refs"]} for item in material],"omitted":[]})
    return pr.encoded({"overview":[],"sections":[{"title":"试产取得进展","paragraphs":[{"text":item.get("title",item.get("text")),"emphasis":"", "refs":item["refs"]} for item in material]}],"briefs":[],"omitted":[]})


SETTINGS = {"agent":{"runtime":"pi"},"llm":{"pi_chat":{"provider":"test","model":"test"}}}


def test_activation_no_backfill_restart_and_idempotence(db):
    with closing(db()) as conn:
        assert pr.enqueue_due(conn, pr.local("2026-10-30T10:00:00+08:00")) == 0
        pr.initialize(conn, pr.local("2026-10-31T19:00:00+08:00"))
        assert pr.enqueue_due(conn, pr.local("2026-11-02T08:30:00+08:00")) == 6
        assert pr.enqueue_due(conn, pr.local("2026-11-02T08:30:00+08:00")) == 0
        rows = conn.execute("SELECT * FROM periodic_reports").fetchall()
        assert all(row["period_start"] >= "2026-10-30T10:00:00+08:00" for row in rows)
        assert not any(row["kind"] == "morning" and row["scheduled_at"].startswith("2026-10-30") for row in rows)
        assert not any(row["kind"] == "noon" for row in rows)
        monthly = next(row for row in rows if row["kind"] == "monthly")
        assert monthly["period_end"] == "2026-11-01T00:00:00+08:00"
        assert monthly["scheduled_at"] == "2026-11-01T08:30:00+08:00"
        weekly = next(row for row in rows if row["kind"] == "weekly")
        assert weekly["period_end"] == "2026-11-02T00:00:00+08:00"


def test_restart_only_catches_up_evening_weekly_monthly(db):
    at = pr.local("2026-11-02T09:00:00+08:00")
    with closing(db()) as conn:
        assert pr.enqueue_due(conn, at, intraday_since=at) == 5
        assert {row[0] for row in conn.execute("SELECT kind FROM periodic_reports")} == {"evening", "weekly", "monthly"}
        # Subsequent normal polling still creates today's noon, even a few
        # seconds after its exact slot, once only.
        noon = pr.local("2026-11-02T14:30:20+08:00")
        assert pr.enqueue_due(conn, noon, intraday_since=at) == 1
        assert pr.enqueue_due(conn, noon, intraday_since=at) == 0
        row = conn.execute("SELECT * FROM periodic_reports WHERE kind='noon'").fetchone()
        assert row["scheduled_at"] == "2026-11-02T14:30:00+08:00"


@pytest.mark.parametrize("status,lease_until,expected", [
    ("pending", None, "skipped"), ("failed", None, "skipped"),
    ("running", 0, "skipped"), ("running", 9999999999, "running"),
    ("done", None, "done"),
])
def test_restart_skips_unfinished_intraday_but_preserves_completed_or_live_job(db, status, lease_until, expected):
    job = due(db)
    at = pr.local("2026-10-30T16:00:00+08:00")
    with closing(db()) as conn, conn:
        conn.execute("UPDATE periodic_reports SET status=?,lease_until=?", (status, lease_until))
    with closing(db()) as conn:
        assert pr.enqueue_due(conn, at, intraday_since=at) == 0
        row = conn.execute("SELECT * FROM periodic_reports WHERE id=?", (job["id"],)).fetchone()
        assert row["status"] == expected
        if expected == "skipped":
            assert row["error"] == "report_intraday_expired"
    service = pr.Service(db, lambda:SETTINGS, lambda:None, runner)
    assert not service.run_one()


def test_actual_market_holidays_not_makeup_workdays():
    calendar = pr.calendar_data()
    for day in ("2026-10-01","2026-10-07","2026-10-10","2026-02-23","2026-09-20"):
        assert pr.trading_day(date.fromisoformat(day), calendar) is False
    assert pr.trading_day(date(2026,10,8),calendar) is True
    assert pr.trading_day(date(2027,1,4),calendar) is None
    assert [slot[0] for slot in pr.slots(date(2026,10,10),calendar)] == ["evening"]


def test_daily_full_day_and_morning_noon_boundaries():
    assert pr.stamp(pr.local("2026-10-30T00:30:00Z")) == "2026-10-30T08:30:00+08:00"
    slots = {kind:(start,end) for kind,_,start,end in pr.slots(date(2026,10,30),pr.calendar_data())}
    assert pr.stamp(slots["morning"][0]) == "2026-10-29T21:30:00+08:00"
    assert slots["morning"][1] == slots["noon"][0]
    assert pr.stamp(slots["noon"][1]) == "2026-10-30T14:30:00+08:00"
    assert (slots["evening"][1]-slots["evening"][0]).total_seconds() == 86400


def test_index_refresh_precedes_snapshot_and_keeps_newly_indexed_news(db):
    job = due(db)
    calls = []
    def refresh():
        calls.append("refresh")
        with closing(db()) as conn:
            news(conn,"new")
            news(conn,"old","2026-10-30T09:59:59+08:00")
            news(conn,"cutoff","2026-10-30T14:30:00+08:00")
            conn.execute("INSERT INTO item_state(item_id,read_at,updated_at) VALUES ('new','read','before')")
            conn.commit()
    def model(config,prompt):
        calls.append("model")
        assert "存储新工艺开始试产" in prompt
        return runner(config,prompt)
    service = pr.Service(db,lambda:SETTINGS,refresh,model)
    assert service.run_one()
    assert calls == ["refresh","model"]
    with closing(db()) as conn:
        saved = conn.execute("SELECT * FROM periodic_reports WHERE id=?",(job["id"],)).fetchone()
        assert saved["status"] == "done", saved["error"]
        assert [source["id"] for source in json.loads(saved["snapshot_json"])["sources"]] == ["new"]
        assert dict(conn.execute("SELECT * FROM item_state").fetchone())["updated_at"] == "before"
        assert len(json.loads(saved["result_json"])["sections"]) == 1
    assert service.run_one() is False


def test_index_failure_never_calls_model_or_saves_partial_report(db):
    due(db)
    def fail(): raise OSError("private directory details")
    service = pr.Service(db,lambda:SETTINGS,fail,lambda *_:pytest.fail("must not call model"))
    for attempt in range(3):
        assert service.run_one()
        with closing(db()) as conn, conn:
            row = conn.execute("SELECT * FROM periodic_reports").fetchone()
            assert row["error"] == "report_index_failed"
            assert row["result_json"] is None and row["snapshot_json"] is None
            conn.execute("UPDATE periodic_reports SET next_attempt=0")
    assert row["status"] == "failed"
    assert not service.run_one()


@pytest.mark.parametrize("exc,code", [
    (FileNotFoundError("private path"), "report_index_source_missing"),
    (NotADirectoryError("private path"), "report_index_not_directory"),
    (PermissionError("private path"), "report_index_permission_denied"),
    (OSError(errno.ENOSPC, "private path"), "report_index_disk_full"),
    (ValueError("private file contents"), "report_index_invalid_file"),
    (sqlite3.DatabaseError("private database details"), "report_index_database_error"),
])
def test_index_failure_exposes_reason_and_retry_without_raw_details(db, exc, code):
    job = due(db)
    def fail(): raise exc
    service = pr.Service(db, lambda:SETTINGS, fail, lambda *_:pytest.fail("no model call"))
    assert service.run_one()
    data = service.listing("daily")["items"][0]
    assert data["error"] == code and data["status"] == "pending"
    assert data["attempts"] == 1 and data["next_retry_at"]
    assert "private" not in pr.encoded(data)
    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM periodic_reports WHERE id=?", (job["id"],)).fetchone()
        assert row["result_json"] is None and row["snapshot_json"] is None


def test_index_database_busy_reason():
    error = sqlite3.OperationalError("database is locked")
    error.sqlite_errorcode = sqlite3.SQLITE_BUSY
    assert pr.index_error(error) == "report_index_database_locked"


def test_refresh_missing_directory_has_specific_cause(tmp_path, monkeypatch):
    import app
    monkeypatch.setattr(app, "DAILY_NEWS_DIR", tmp_path / "missing")
    with pytest.raises(FileNotFoundError):
        app.refresh_report_index()
    file = tmp_path / "not-a-directory"
    file.write_text("test")
    monkeypatch.setattr(app, "DAILY_NEWS_DIR", file)
    with pytest.raises(NotADirectoryError):
        app.refresh_report_index()


def test_manual_rebuild_cannot_interrupt_capture_but_can_run_during_generation(db, monkeypatch):
    due(db)
    guard = threading.RLock()
    started, rebuilt = threading.Event(), threading.Event()
    workers = []
    def manual_rebuild():
        started.set()
        with guard, closing(db()) as conn, conn:
            conn.execute("DELETE FROM items")
        rebuilt.set()
    def refresh():
        with closing(db()) as conn:
            news(conn, "must-survive")
        worker = threading.Thread(target=manual_rebuild)
        workers.append(worker)
        worker.start()
        assert started.wait(1)
    original = pr.capture
    def capture(conn, job):
        assert not rebuilt.wait(.05), "manual rebuild entered before snapshot finished"
        return original(conn, job)
    monkeypatch.setattr(pr, "capture", capture)
    def model(config, prompt):
        assert rebuilt.wait(2), "index lock held during model generation"
        return runner(config, prompt)
    try:
        pr.Service(db, lambda:SETTINGS, refresh, model, index_lock=guard).run_one()
        with closing(db()) as conn:
            row = conn.execute("SELECT * FROM periodic_reports").fetchone()
            assert row["status"] == "done", row["error"]
            assert json.loads(row["result_json"])["sources"][0]["id"] == "must-survive"
    finally:
        for worker in workers:
            worker.join(2)


def test_snapshot_keeps_title_only_and_rejects_later_body(db):
    job = due(db)
    with closing(db()) as conn:
        news(conn,"title")
        conn.execute("INSERT INTO article_details(url,content,fetched_at,updated_at) VALUES ('https://example.com/title','后来取得的正文','2026-10-30T15:00:00+08:00','later')")
        conn.commit()
        snapshot = pr.capture(conn,job)
        assert snapshot["sources"][0]["body"] == ""
        assert snapshot["sources"][0]["title"]


@pytest.mark.parametrize("bad", [
    {"events":[],"omitted":[]},
    {"events":[{"text":"新闻","refs":["N999"]}],"omitted":[]},
    {"events":[{"text":"新闻","refs":["N1"]}],"omitted":["N1"]},
])
def test_extraction_accounts_for_every_input_without_invalid_refs(bad):
    with pytest.raises(RuntimeError, match="report_invalid_result"):
        pr.validate_events(bad,{"N1"})


def test_invalid_final_citations_and_emphasis_not_saved(db):
    due(db)
    with closing(db()) as conn: news(conn,"one")
    def bad(config,prompt):
        return pr.encoded({"overview":[],"sections":[{"title":"标题","paragraphs":[{"text":"未证实信息","emphasis":"", "refs":["N99999"]}]}]})
    service=pr.Service(db,lambda:SETTINGS,lambda:None,bad)
    service.run_one()
    with closing(db()) as conn:
        row=conn.execute("SELECT * FROM periodic_reports").fetchone()
        assert row["result_json"] is None and row["error"] == "report_invalid_result"
        assert conn.execute("SELECT COUNT(*) FROM periodic_report_steps").fetchone()[0] == 1


def test_claim_excludes_other_workers_and_recovers_expired_lease(db):
    due(db)
    first=pr.Service(db,lambda:SETTINGS,lambda:None)
    second=pr.Service(db,lambda:SETTINGS,lambda:None)
    job=first.claim()
    assert second.claim() is None
    with closing(db()) as conn, conn: conn.execute("UPDATE periodic_reports SET lease_until=0")
    replacement=second.claim()
    assert replacement["id"] == job["id"] and replacement["lease"] != job["lease"]
    with pytest.raises(RuntimeError,match="report_interrupted"):
        first.checkpoint(job,"late",lambda:{})


def test_partition_preserves_all_sources_and_does_not_truncate(monkeypatch):
    monkeypatch.setattr(pr.report_model, "estimate_tokens", len)
    config = {"limits":{"input_tokens":1000}}
    inputs=[{"text":"消息"*100,"refs":[str(index)]} for index in range(15)]
    assert [item for batch in pr.partition(inputs,config,pr.encoded) for item in batch] == inputs
    with pytest.raises(RuntimeError,match="report_input_too_large"):
        pr.partition([{"title":"a"*40000}],config,pr.encoded)


def test_retry_token_and_immutable_completed_report(db):
    job=due(db)
    service=pr.Service(db,lambda:SETTINGS,lambda:None,runner)
    service.run_one()
    listing=service.listing("daily")
    assert listing["items"][0]["status"] == "done"
    with pytest.raises(ValueError,match="report_database_changed"): service.retry(job["id"],"other")
    service.retry(job["id"],listing["database_token"])
    assert service.run_one() is False


def test_api_empty_new_install_no_generation_endpoint(db,monkeypatch):
    import app
    monkeypatch.setattr(app,"db_conn",db)
    monkeypatch.setattr(app,"REPORT_SERVICE",pr.Service(db,lambda:SETTINGS,lambda:None,runner))
    client=app.app.test_client()
    response=client.get("/api/reports?kind=daily")
    assert response.status_code == 200 and response.json["items"] == []
    assert client.get("/api/reports?kind=old").status_code == 400
    assert client.get("/api/reports?offset=-1").status_code == 400
    assert client.post("/api/reports",json={"date":"2026-10-29"}).status_code == 405
    assert client.get("/api/reports/missing").status_code == 404


def test_scheduler_queues_exact_slot_while_generation_is_blocked(db, monkeypatch):
    with closing(db()) as conn, conn:
        conn.execute("UPDATE periodic_report_meta SET activated_at='2026-10-29T21:00:00+08:00',scheduled_through='2026-10-29T21:00:00+08:00'")
        news(conn, "first", "2026-10-29T21:01:00+08:00")
    clock = [pr.local("2026-10-30T08:29:50+08:00")]
    monkeypatch.setattr(pr, "now", lambda: clock[0])
    blocked, release = threading.Event(), threading.Event()
    def slow_model(config, prompt):
        blocked.set()
        assert release.wait(5)
        return runner(config, prompt)
    service = pr.Service(db, lambda: SETTINGS, lambda: None, slow_model)
    try:
        service.start()
        worker, scheduler = service.thread, service.scheduler_thread
        assert blocked.wait(3)
        service.start()
        assert (worker, scheduler) == (service.thread, service.scheduler_thread)
        clock[0] = pr.local("2026-10-30T08:30:01+08:00")
        service.schedule_wake.set()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with closing(db()) as conn:
                morning = conn.execute("SELECT * FROM periodic_reports WHERE kind='morning'").fetchone()
            if morning: break
            threading.Event().wait(.01)
        assert morning and morning["status"] == "pending"
        assert not release.is_set()  # The first model call is still blocked.
    finally:
        service.stopped.set()
        release.set()
        service.stop()
    assert not worker.is_alive() and not scheduler.is_alive()


def test_scheduler_next_wake_and_resume_skip_intraday(db):
    service = pr.Service(db, lambda: SETTINGS, lambda: None, runner)
    assert service.schedule_once(pr.local("2026-10-30T14:29:50+08:00")) == 10
    assert service.schedule_once(pr.local("2026-10-30T14:30:01+08:00")) == 30
    with closing(db()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM periodic_reports WHERE kind='noon'").fetchone()[0] == 1
    service.schedule_once(pr.local("2026-11-02T09:00:00+08:00"))
    with closing(db()) as conn:
        rows = conn.execute("SELECT kind,status FROM periodic_reports").fetchall()
    assert {row["kind"] for row in rows if row["status"] == "pending"} == {"evening", "weekly", "monthly"}
    assert next(row for row in rows if row["kind"] == "noon")["status"] == "skipped"


def test_manual_retry_keeps_checkpoints_only_for_same_model(db):
    job = due(db)
    config = pr.report_model.configuration(SETTINGS)
    with closing(db()) as conn, conn:
        conn.execute("UPDATE periodic_reports SET status='failed',config_json=?", (pr.encoded(config),))
        conn.execute("INSERT INTO periodic_report_steps VALUES (?, 'v2:direct:raw', '{}')", (job["id"],))
        token = conn.execute("SELECT database_token FROM periodic_report_meta").fetchone()[0]
    service = pr.Service(db, lambda: SETTINGS, lambda: None, runner)
    service.retry(job["id"], token)
    with closing(db()) as conn, conn:
        assert conn.execute("SELECT COUNT(*) FROM periodic_report_steps").fetchone()[0] == 1
        conn.execute("UPDATE periodic_reports SET status='failed'")
    service.settings = lambda: {"agent": {"runtime": "pi"}, "llm": {"pi_chat": {"provider": "test", "model": "new"}}}
    service.retry(job["id"], token)
    with closing(db()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM periodic_report_steps").fetchone()[0] == 0


def test_retry_reports_actionable_capacity_error_without_losing_saved_work(db, monkeypatch):
    import app
    job = due(db)
    with closing(db()) as conn, conn:
        conn.execute("UPDATE periodic_reports SET status='failed'")
        conn.execute("INSERT INTO periodic_report_steps VALUES (?, 'v2:direct:raw', '{}')", (job["id"],))
        token = conn.execute("SELECT database_token FROM periodic_report_meta").fetchone()[0]
    def unavailable(config): raise RuntimeError("report_model_capacity_unknown")
    monkeypatch.setattr(pr.report_model, "capacity", unavailable)
    monkeypatch.setattr(app, "REPORT_SERVICE", pr.Service(db, lambda: SETTINGS, lambda: None, runner))
    response = app.app.test_client().post('/api/reports/' + job["id"] + '/retry', json={"database_token": token})
    assert response.status_code == 409 and response.json["error"] == "report_model_capacity_unknown"
    with closing(db()) as conn:
        assert conn.execute("SELECT status FROM periodic_reports").fetchone()[0] == "failed"
        assert conn.execute("SELECT COUNT(*) FROM periodic_report_steps").fetchone()[0] == 1
