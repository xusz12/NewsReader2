"""Exercise the CLI boundary and persisted queue, without a browser or live DB."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

from scanner import apply_schema


STAMP = "2026-10-18 13:00:00"
URL = "https://techcrunch.com/2026/10/17/example/"
BODY = ("An actual article paragraph with enough content to pass validation. " * 5).strip()


@pytest.fixture
def reader(tmp_path, monkeypatch):
    import app

    monkeypatch.setattr(app, "DB_PATH", tmp_path / "news.sqlite3")
    monkeypatch.setattr(app, "MEDIA_CACHE_DIR", tmp_path / "media")
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(app, "now_ts", lambda: STAMP)
    with app.db_conn() as conn:
        apply_schema(conn, app.SCHEMA_PATH)
    return app


def cli_response(payload, returncode=0, stderr=""):
    return subprocess.CompletedProcess(["opencli"], returncode, json.dumps(payload), stderr)


def queue(reader, url=URL):
    with reader.db_conn() as conn:
        reader.enqueue_detail_job(conn, "item", url, "test")


def job(reader, url=URL):
    with reader.db_conn() as conn:
        return dict(conn.execute("SELECT * FROM detail_jobs WHERE url=?", (url,)).fetchone())


def advance(reader, monkeypatch, seconds):
    timestamp = datetime.strptime(STAMP, "%Y-%m-%d %H:%M:%S") + timedelta(seconds=seconds)
    monkeypatch.setattr(reader, "now_ts", lambda: timestamp.strftime("%Y-%m-%d %H:%M:%S"))


@pytest.mark.parametrize("payload, expected", [
    ([{"content": BODY}], [{"content": BODY}]),
    ({"content": BODY}, {"content": BODY}),
    ({"ok": True, "data": [{"content": BODY}]}, [{"content": BODY}]),
    ({"ok": True, "data": {"body": BODY}}, {"body": BODY}),
    ({"ok": True, "data": []}, []),
])
def test_cli_legacy_and_v2_results(reader, monkeypatch, payload, expected):
    run = Mock(return_value=cli_response(payload, stderr="Extension update available"))
    monkeypatch.setattr(reader.subprocess, "run", run)
    assert reader._run_opencli_json_command(["opencli"], 45) == (True, expected, "")
    assert run.call_count == 1


@pytest.mark.parametrize("returncode", [0, 1])
def test_structured_error_beats_stderr_update_notice(reader, monkeypatch, returncode):
    payload = {"ok": False, "error": {"code": "COMMAND_EXEC", "message": "Navigation rejected.",
                                      "hint": "Check the browser", "exitCode": 1}}
    monkeypatch.setattr(reader.subprocess, "run", Mock(return_value=cli_response(payload, returncode, "Extension update available")))
    ok, data, error = reader._run_opencli_json_command(["opencli"], 45)
    assert not ok and data is None
    assert error == "COMMAND_EXEC: Navigation rejected. | Check the browser"


@pytest.mark.parametrize("stdout, returncode, stderr, prefix", [
    ('{"ok":true}', 0, "", "INVALID_JSON:"),
    ('{"ok":false,"error":"broken"}', 0, "", "OPENCLI_ERROR: broken"),
    ("not json", 0, "", "INVALID_JSON:"),
    ("", 0, "", "INVALID_JSON:"),
    ("", 1, "old CLI error", "old CLI error"),
    ('{"ok":true,"data":[]}', 1, "process failed", "process failed"),
])
def test_cli_invalid_and_legacy_errors(reader, monkeypatch, stdout, returncode, stderr, prefix):
    monkeypatch.setattr(reader.subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], returncode, stdout, stderr)))
    ok, data, error = reader._run_opencli_json_command(["opencli"], 45)
    assert not ok and data is None and error.startswith(prefix)


@pytest.mark.parametrize("exc, error", [
    (subprocess.TimeoutExpired("opencli", 45), "TIMEOUT"),
    (FileNotFoundError("opencli"), "SUBPROCESS_ERROR:"),
])
def test_subprocess_failures(reader, monkeypatch, exc, error):
    monkeypatch.setattr(reader.subprocess, "run", Mock(side_effect=exc))
    assert reader._run_opencli_json_command(["opencli"], 45)[2].startswith(error)


@pytest.mark.parametrize("host, command, field", [
    ("arstechnica.com", ["ArsPublic", "article"], "content"),
    ("techcrunch.com", ["TechcrunchPublic", "article"], "content"),
    ("www.reuters.com", ["reuters", "article-detail"], "body"),
    ("www.bloomberg.com", ["bloomberg", "news"], "content"),
])
def test_v2_article_runs_through_worker_to_database(reader, monkeypatch, host, command, field):
    url = f"https://{host}/news/example/"
    record = {"title": "Article", "authors": "Reporter", field: BODY}
    run = Mock(return_value=cli_response({"ok": True, "data": [record]}))
    monkeypatch.setattr(reader.subprocess, "run", run)
    queue(reader, url)
    assert reader.process_pending_jobs_once()
    assert run.call_args.args[0] == ["opencli", *command, url, "-f", "json"]
    row = job(reader, url)
    assert (row["status"], row["attempts"], row["last_error"], row["worker_pid"]) == ("success", 1, None, None)
    with reader.db_conn() as conn:
        saved = conn.execute("SELECT * FROM article_details WHERE url=?", (url,)).fetchone()
        assert saved["content"] == BODY
        assert json.loads(saved["raw_json"]) == record
        assert conn.execute("SELECT status FROM ai_jobs WHERE url=?", (url,)).fetchone()[0] == "pending"


@pytest.mark.parametrize("has_article", [False, True])
def test_v2_x_thread_and_optional_article(reader, monkeypatch, has_article):
    url = "https://x.com/example/status/123"
    image = "https://pbs.twimg.com/media/example.jpg"
    tweets = [{"id": "123", "author": "example", "text": "短帖", "quoted_tweet": {"text": "引用内容"},
               "media_urls": [image]}, {"id": "124", "author": "reply", "text": "评论"}]
    def respond(cmd, **kwargs):
        if cmd[2] == "thread":
            return cli_response({"ok": True, "data": tweets})
        if has_article:
            return cli_response({"ok": True, "data": [{"content": BODY}]})
        return cli_response({"ok": False, "error": {"code": "COMMAND_EXEC", "message": "Tweet has no article content"}}, 1)
    run = Mock(side_effect=respond)
    monkeypatch.setattr(reader.subprocess, "run", run)
    monkeypatch.setattr(reader, "_cache_twitter_image", lambda *args: None)
    queue(reader, url)
    assert reader.process_pending_jobs_once()
    assert job(reader, url)["status"] == "success"
    assert run.call_count == 2
    with reader.db_conn() as conn:
        saved = conn.execute("SELECT * FROM article_details WHERE url=?", (url,)).fetchone()
        assert all(s in saved["content"] for s in ["短帖", "引用内容", "评论"])
        assert (BODY in saved["content"]) == has_article
        payload = json.loads(saved["raw_json"])
        assert payload["media_images"][0]["url"] == image
        assert bool(payload["article_error"]) != has_article
        assert conn.execute("SELECT count(*) FROM ai_jobs").fetchone()[0] == 0


def test_transient_failure_backoff_then_success(reader, monkeypatch):
    run = Mock(side_effect=[cli_response({"ok": False, "error": {"code": "COMMAND_EXEC", "message": "Navigation rejected."}}, 1),
                            cli_response({"ok": True, "data": [{"content": BODY}]})])
    monkeypatch.setattr(reader.subprocess, "run", run)
    queue(reader)
    assert reader.process_pending_jobs_once()
    row = job(reader)
    assert (row["status"], row["attempts"], row["next_attempt_at"]) == ("pending", 1, "2026-10-18 13:00:05")
    assert row["finished_at"] is None
    assert not reader.process_pending_jobs_once()
    advance(reader, monkeypatch, 5)
    assert reader.process_pending_jobs_once()
    row = job(reader)
    assert (row["status"], row["attempts"], row["last_error"], row["next_attempt_at"]) == ("success", 2, None, None)
    assert run.call_count == 2


def test_transient_failure_stops_at_three_attempts(reader, monkeypatch):
    run = Mock(side_effect=subprocess.TimeoutExpired("opencli", 45))
    monkeypatch.setattr(reader.subprocess, "run", run)
    queue(reader)
    for seconds in (0, 5, 20):
        advance(reader, monkeypatch, seconds)
        assert reader.process_pending_jobs_once()
    row = job(reader)
    assert (row["status"], row["attempts"], row["last_error"]) == ("failed", 3, "TIMEOUT")
    assert row["next_attempt_at"] is None and row["finished_at"] is not None
    advance(reader, monkeypatch, 100)
    assert not reader.process_pending_jobs_once()
    assert run.call_count == 3


@pytest.mark.parametrize("error, retry", [
    ("BROWSER_CONNECT: Browser Bridge extension not connected", True),
    ("UNKNOWN: Browser connection dropped after the navigate command was dispatched", True),
    ("HTTP_ERROR: HTTP 503", True), ("NETWORK_ERROR: fetch failed", True),
    ("HTTP_ERROR: HTTP 404", False), ("AUTH_REQUIRED: login required", False),
    ("FETCH_ERROR: Bloomberg served the bot-protection page", False),
    ("CONTENT_TOO_SHORT: 0", False), ("EMPTY_TWITTER_THREAD", False),
    ("INVALID_JSON: Expecting value", False), ("SUBPROCESS_ERROR: executable missing", False),
    ("COMMAND_EXEC: Tweet has no article content", False),
])
def test_retry_only_transient_failures(reader, monkeypatch, error, retry):
    monkeypatch.setattr(reader, "run_opencli_detail", lambda url: (False, {}, error))
    queue(reader)
    reader.process_pending_jobs_once()
    assert job(reader)["status"] == ("pending" if retry else "failed")


def test_uncaught_adapter_exception_does_not_leave_running(reader, monkeypatch):
    monkeypatch.setattr(reader, "run_opencli_detail", Mock(side_effect=RuntimeError("invalid adapter output")))
    queue(reader)
    reader.process_pending_jobs_once()
    row = job(reader)
    assert row["status"] == "failed" and row["worker_pid"] is None
    assert row["last_error"].startswith("DETAIL_JOB_ERROR:")


def test_storage_exception_rolls_back_and_marks_failure(reader, monkeypatch):
    monkeypatch.setattr(reader, "run_opencli_detail", lambda url: (True, {"source": "test"}, ""))
    queue(reader)
    reader.process_pending_jobs_once()
    assert job(reader)["status"] == "failed"
    with reader.db_conn() as conn:
        assert conn.execute("SELECT count(*) FROM article_details").fetchone()[0] == 0


def test_interruptions_preserve_budget_across_worker_restarts(reader, monkeypatch):
    monkeypatch.setattr(reader, "run_opencli_detail", Mock(side_effect=KeyboardInterrupt))
    queue(reader)
    for attempt, seconds in enumerate((0, 5, 20), 1):
        advance(reader, monkeypatch, seconds)
        with pytest.raises(KeyboardInterrupt):
            reader.process_pending_jobs_once()
        assert job(reader)["status"] == "running"
        assert job(reader)["attempts"] == attempt
        assert reader.recover_interrupted_detail_jobs() == 1
        assert reader.recover_interrupted_detail_jobs() == 0
    assert job(reader)["status"] == "failed"
    assert job(reader)["last_error"].startswith("INTERRUPTED:")


def test_recovery_leaves_live_and_terminal_jobs_alone(reader, monkeypatch):
    monkeypatch.setattr(reader, "_detail_worker_process_alive", lambda pid: pid == 999999)
    with reader.db_conn() as conn:
        for url, status, owner in [("legacy", "running", None), ("dead", "running", 999998),
                                   ("live", "running", 999999), ("failed", "failed", None),
                                   ("success", "success", None), ("cancel", "canceled", None),
                                   ("pending", "pending", None)]:
            conn.execute("INSERT INTO detail_jobs(url,status,attempts,worker_pid,updated_at) VALUES(?,?,0,?,?)",
                         (url, status, owner, STAMP))
    assert reader.recover_interrupted_detail_jobs() == 2
    assert job(reader, "legacy")["attempts"] == 1
    assert job(reader, "dead")["status"] == "pending"
    for url, status in [("live", "running"), ("failed", "failed"), ("success", "success"),
                        ("cancel", "canceled"), ("pending", "pending")]:
        assert job(reader, url)["status"] == status


def test_recovered_job_can_finish_without_resetting_attempts(reader, monkeypatch):
    queue(reader)
    with reader.db_conn() as conn:
        conn.execute("UPDATE detail_jobs SET status='running', attempts=1, worker_pid=NULL")
    assert reader.recover_interrupted_detail_jobs() == 1
    monkeypatch.setattr(reader.subprocess, "run", Mock(return_value=cli_response({"ok": True, "data": [{"content": BODY}]})))
    advance(reader, monkeypatch, 5)
    assert reader.process_pending_jobs_once()
    row = job(reader)
    assert (row["status"], row["attempts"], row["last_error"]) == ("success", 2, None)


def test_adapter_failure_inside_success_envelope_is_not_saved(reader, monkeypatch):
    record = {"status": "failed", "error_code": "NETWORK_ERROR", "reason": "fetch failed"}
    monkeypatch.setattr(reader.subprocess, "run", Mock(return_value=cli_response({"ok": True, "data": [record]})))
    queue(reader)
    reader.process_pending_jobs_once()
    assert job(reader)["status"] == "pending"
    assert job(reader)["last_error"] == "NETWORK_ERROR: fetch failed"
    with reader.db_conn() as conn:
        assert conn.execute("SELECT count(*) FROM article_details").fetchone()[0] == 0


def test_start_worker_recovers_once_before_thread_start(reader, monkeypatch):
    events = []
    worker = Mock()
    worker.start.side_effect = lambda: events.append("start")
    worker.is_alive.return_value = True
    monkeypatch.setattr(reader, "WORKER_THREAD", None)
    monkeypatch.setattr(reader, "recover_interrupted_detail_jobs", lambda: events.append("recover"))
    monkeypatch.setattr(reader.threading, "Thread", Mock(return_value=worker))
    reader.start_detail_worker()
    reader.start_detail_worker()
    assert events == ["recover", "start"]


def test_concurrent_workers_cannot_claim_same_job(reader, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def fetch(url):
        entered.set()
        assert release.wait(5)
        return False, {}, "CONTENT_TOO_SHORT: 0"
    monkeypatch.setattr(reader, "run_opencli_detail", fetch)
    queue(reader)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(reader.process_pending_jobs_once)
        try:
            assert entered.wait(5)
            assert pool.submit(reader.process_pending_jobs_once).result(timeout=5) is False
        finally:
            release.set()
        assert first.result(timeout=5)
    assert job(reader)["attempts"] == 1


@pytest.mark.parametrize("status", ["failed", "pending", "running"])
@pytest.mark.parametrize("twitter", [False, True])
def test_retry_api_resets_only_finished_jobs(reader, status, twitter):
    url = "https://x.com/example/status/123" if twitter else URL
    with reader.db_conn() as conn:
        conn.execute("INSERT INTO items(id,source_file,item_order,published_at,date,title,url,created_at,updated_at) VALUES('item','test',1,?,?,?,?,?,?)",
                     (STAMP, STAMP[:10], "Article", url, STAMP, STAMP))
        conn.execute("INSERT INTO detail_jobs(url,status,attempts,last_error,started_at,finished_at,next_attempt_at,worker_pid,updated_at) VALUES(?,?,3,'old',?,?,?,?,?)",
                     (url, status, STAMP, STAMP, STAMP, os.getpid(), STAMP))
        if twitter:
            conn.execute("INSERT INTO article_details(url,content,fetched_at,updated_at) VALUES(?,?,?,?)", (url, BODY, STAMP, STAMP))
    assert reader.app.test_client().post("/api/news/item/detail/retry").status_code == 200
    row = job(reader, url)
    if status == "failed":
        assert row["status"] == "pending" and row["attempts"] == 0
        assert all(row[k] is None for k in ["last_error", "started_at", "finished_at", "next_attempt_at", "worker_pid"])
    else:
        assert row["status"] == status and row["attempts"] == 3 and row["last_error"] == "old"


def test_schema_migration_preserves_legacy_jobs_and_content(reader):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE detail_jobs(url TEXT PRIMARY KEY,item_id TEXT,source TEXT,status TEXT NOT NULL,attempts INTEGER DEFAULT 0,last_error TEXT,queued_at TEXT,started_at TEXT,finished_at TEXT,updated_at TEXT NOT NULL)")
    conn.execute("INSERT INTO detail_jobs(url,status,attempts,last_error,updated_at) VALUES('old','running',2,'old error',?)", (STAMP,))
    apply_schema(conn, reader.SCHEMA_PATH)
    conn.execute("INSERT INTO article_details(url,content,fetched_at,updated_at) VALUES('old',?,?,?)", (BODY, STAMP, STAMP))
    conn.commit()
    apply_schema(conn, reader.SCHEMA_PATH)
    assert conn.execute("SELECT status,attempts,last_error,next_attempt_at,worker_pid FROM detail_jobs").fetchone() == ("running", 2, "old error", None, None)
    assert conn.execute("SELECT content FROM article_details").fetchone()[0] == BODY
    conn.close()
