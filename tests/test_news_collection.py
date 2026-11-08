import hashlib
import importlib
import json
import sqlite3
import time
import threading
import queue
import subprocess
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pytest

import collection_translation
import database_schema
import news_collection as collection
import settings


SOURCE = {"section": "world", "display_name": "Reuters · World", "source_type": "reuters",
          "source_name": "Reuters", "translation_policy": "never"}


def article(url="https://example.com/story", title="新闻", published="2026-11-06T23:00:00Z"):
    return {"title": title, "url": url, "time": published}


@pytest.fixture
def service(tmp_path):
    def connect():
        conn = sqlite3.connect(tmp_path / "news.sqlite3", timeout=5)
        conn.row_factory = sqlite3.Row
        conn.create_function("is_bloomberg_video_url", 1, lambda _: 0)
        return conn
    with closing(connect()) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        database_schema.apply_schema(conn, Path(__file__).resolve().parents[1] / "schema.sql")
        collection.initialize(conn)
    config = settings.default_app_settings()
    svc = collection.Service(connect, lambda: config, sources=[SOURCE],
                             collector=lambda _: {"ok": True, "items": [article()]})
    yield svc
    svc.stop()


def query(svc, sql, args=()):
    with closing(svc.connect()) as conn:
        return conn.execute(sql, args).fetchall()


def test_stage_translate_publish_and_single_identity(service):
    run = service.request()
    assert service.request() == run
    assert service.collect_once()
    assert not query(service, "SELECT * FROM items")
    assert len(query(service, "SELECT * FROM collection_items")) == 1
    assert service.translate_once()
    assert not query(service, "SELECT * FROM items")
    assert service.publish_once()
    item = dict(query(service, "SELECT * FROM items")[0])
    assert item["date"] == "2026-11-07"
    assert item["published_at"] == "2026-11-07 07:00:00"
    assert item["source_file"] == ""
    assert query(service, "SELECT item_id FROM topic_ingestions")[0][0] == item["id"]
    with closing(service.connect()) as conn, conn:
        conn.execute("INSERT INTO item_state(item_id,read_at,updated_at) VALUES (?,'read','now')", (item["id"],))
    service.collector = lambda _: {"ok": True, "items": [article(title="标题更新", published="2026-11-08T08:00:00Z")]}
    service.request(); service.collect_once()
    assert not service.translate_once()
    assert len(query(service, "SELECT * FROM items")) == 1
    assert query(service, "SELECT read_at FROM item_state")[0][0] == "read"


def test_translation_failure_does_not_block_collection_and_retry_is_local(service):
    def fail(*_):
        raise RuntimeError("HTTP_429")
    service.translator = fail
    service.request(); service.collect_once(); service.translate_once()
    assert service.status()["counts"]["failed"] == 1
    assert not query(service, "SELECT * FROM items")
    service.collector = lambda _: {"ok": True, "items": [article("https://example.com/second")]}
    service.request(); service.collect_once()
    assert service.status()["counts"]["pending"] == 1
    service.translator = lambda raw, _: collection_translation.original_fields(raw)
    service.translate_once()
    service.publish_once()
    assert len(query(service, "SELECT * FROM items")) == 1
    assert service.retry() == 1
    service.translate_once()
    service.publish_once()
    assert len(query(service, "SELECT * FROM items")) == 2
    assert service.status()["counts"]["failed"] == 0
    assert query(service, "SELECT SUM(attempts) FROM collection_items")[0][0] == 3


def test_expired_leases_resume_and_saved_translation_is_not_repeated(service):
    service.request(); service.collect_once()
    with closing(service.connect()) as conn, conn:
        conn.execute("UPDATE collection_items SET status='running',lease='dead',lease_until=0,translation_json=?",
                     (json.dumps({"title": "译文", "summary": "", "quoted_text": ""}),))
    service.translator = lambda *_: pytest.fail("saved translation must be reused")
    assert service.translate_once()
    assert service.publish_once()
    assert query(service, "SELECT title FROM items")[0][0] == "译文"


def test_source_errors_recover_and_empty_new_result_is_success(service):
    service.sources = [SOURCE, {**SOURCE, "section": "other"}]
    service.collector = lambda s: {"ok": s["section"] == "world", "items": [article()] if s["section"] == "world" else [], "error_code": "TIMEOUT"}
    service.request(); service.collect_once()
    assert service.status()["last_run"]["status"] == "partial"
    assert service.status()["source_errors"] == 1
    service.collector = lambda _: {"ok": True, "items": []}
    service.request(); service.collect_once()
    assert service.status()["source_errors"] == 0
    assert service.status()["last_run"]["status"] == "done"
    assert service.errors(collection.now().date().isoformat())["days"]


def test_cooldown_is_persisted_across_runs(service):
    with closing(service.connect()) as conn, conn:
        conn.execute("INSERT INTO collection_source_cooldowns VALUES ('world',?)", (time.time()+3600,))
    service.collector = lambda _: pytest.fail("cooldown must prevent invocation")
    service.request(); service.collect_once()
    assert service.status()["last_run"]["status"] == "failed"
    assert service.status()["sources"][0]["error"] == "RETRY_AFTER"


def test_deleted_identity_cannot_be_published_from_inflight_translation(service):
    service.request(); service.collect_once()
    def translated(raw, _):
        with closing(service.connect()) as conn, conn:
            conn.execute("UPDATE news_identities SET deleted=1")
        return collection_translation.original_fields(raw)
    service.translator = translated
    service.translate_once()
    service.publish_once()
    assert not query(service, "SELECT * FROM items")
    assert query(service, "SELECT status FROM collection_items")[0][0] == "deleted"
    service.request(); service.collect_once()
    assert not service.translate_once()


def test_legacy_identity_migration_preserves_id_and_read_state(service):
    with closing(service.connect()) as conn, conn:
        conn.execute("DELETE FROM collection_meta")
        conn.execute("INSERT INTO items VALUES ('old-id','old.md',1,'2026-11-01 10:00','2026-11-01','10:00','Reuters','reuters','Reuters','旧标题','',?,'now','now')", (article()["url"],))
        collection.initialize(conn)
    service.request(); service.collect_once()
    assert not service.translate_once()
    assert query(service, "SELECT item_id FROM news_identities")[0][0] == "old-id"
    assert query(service, "SELECT id FROM items")[0][0] == "old-id"


def test_schedule_trading_days_dedup_pause_and_catchup(service):
    config = service.settings()["collection"]
    assert len(service.times(datetime(2026,11,6).date(), config)) == 11
    assert len(service.times(datetime(2026,11,7).date(), config)) == 6
    assert len(service.times(datetime(2026,10,1).date(), config)) == 6
    with closing(service.connect()) as conn, conn:
        conn.execute("UPDATE collection_meta SET checked_at=?,schedule_json=?", ("2026-11-06T08:59:00+08:00",collection.encoded(config)))
    service.schedule_once(datetime.fromisoformat("2026-11-06T09:00:00+08:00"))
    service.schedule_once(datetime.fromisoformat("2026-11-06T09:00:30+08:00"))
    assert len(query(service,"SELECT * FROM collection_runs")) == 1
    service.collect_once()
    service.schedule_once(datetime.fromisoformat("2026-11-06T18:01:00+08:00"))
    assert len(query(service,"SELECT * FROM collection_runs")) == 2
    assert query(service,"SELECT trigger FROM collection_runs ORDER BY rowid DESC")[0][0] == "catchup"
    service.collect_once()
    config["enabled"] = False
    service.schedule_once(datetime.fromisoformat("2026-11-06T21:01:00+08:00"))
    assert len(query(service,"SELECT * FROM collection_runs")) == 2
    config["enabled"] = True
    service.schedule_once(datetime.fromisoformat("2026-11-06T21:02:00+08:00"))
    assert len(query(service,"SELECT * FROM collection_runs")) == 2


def test_calendar_recovery_catches_trading_slot_once(service, monkeypatch):
    calendar = {}
    monkeypatch.setattr(collection.periodic_reports, "calendar_data", lambda: calendar)
    config = service.settings()["collection"]
    with closing(service.connect()) as conn, conn:
        conn.execute("UPDATE collection_meta SET checked_at=?,schedule_json=?",
                     ("2027-01-04T08:59:00+08:00", collection.encoded(config)))
    service.schedule_once(datetime.fromisoformat("2027-01-04T09:00:00+08:00"))
    assert not query(service, "SELECT * FROM collection_runs")
    assert service.schedule_error == "collection_calendar_unavailable"
    calendar["2027"] = {"closed_ranges": []}  # Synthetic trading-day fixture.
    service.schedule_once(datetime.fromisoformat("2027-01-04T09:00:30+08:00"))
    assert datetime.fromisoformat(query(service, "SELECT slot FROM collection_runs")[0][0]) == datetime.fromisoformat("2027-01-04T09:00:00+08:00")
    service.collect_once()
    service.schedule_once(datetime.fromisoformat("2027-01-04T09:01:00+08:00"))
    assert len(query(service, "SELECT * FROM collection_runs")) == 1
    assert service.calendar_wait_since is None and not service.schedule_error


def test_calendar_recovery_does_not_catch_paused_collection(service, monkeypatch):
    calendar = {}
    monkeypatch.setattr(collection.periodic_reports, "calendar_data", lambda: calendar)
    config = service.settings()["collection"]
    with closing(service.connect()) as conn, conn:
        conn.execute("UPDATE collection_meta SET checked_at=?,schedule_json=?",
                     ("2027-01-04T08:59:00+08:00", collection.encoded(config)))
    service.schedule_once(datetime.fromisoformat("2027-01-04T09:00:00+08:00"))
    config["enabled"] = False
    service.schedule_once(datetime.fromisoformat("2027-01-04T09:00:10+08:00"))
    calendar["2027"] = {"closed_ranges": []}
    config["enabled"] = True
    service.schedule_once(datetime.fromisoformat("2027-01-04T09:00:30+08:00"))
    service.schedule_once(datetime.fromisoformat("2027-01-04T09:01:00+08:00"))
    assert not query(service, "SELECT * FROM collection_runs")


def test_unknown_calendar_keeps_daily_collection(service, monkeypatch):
    monkeypatch.setattr(collection.periodic_reports, "calendar_data", lambda: {})
    config = service.settings()["collection"]
    with closing(service.connect()) as conn, conn:
        conn.execute("UPDATE collection_meta SET checked_at=?,schedule_json=?",
                     ("2027-01-04T11:59:00+08:00", collection.encoded(config)))
    service.schedule_once(datetime.fromisoformat("2027-01-04T12:00:00+08:00"))
    assert datetime.fromisoformat(query(service, "SELECT slot FROM collection_runs")[0][0]) == datetime.fromisoformat("2027-01-04T12:00:00+08:00")


def test_unknown_publication_date_and_canonical_identity():
    assert collection.published_time("") == ""
    assert collection.canonical_url("https://twitter.com/user/status/123?utm_source=x") == collection.canonical_url("https://x.com/another/status/123")
    assert collection.canonical_url("http://www.example.com/a?utm_source=x&id=1#s") == "https://example.com/a?id=1"
    with pytest.raises(ValueError):
        collection.normalize_schedule({"daily": ["24:00"]})


def test_acquisition_does_not_hold_database_lock_and_duplicate_request_coalesces(service):
    entered, release = threading.Event(), threading.Event()
    def slow(_):
        entered.set()
        assert release.wait(5)
        return {"ok": True, "items": [article()]}
    service.collector = slow
    run = service.request()
    worker = threading.Thread(target=service.collect_once)
    worker.start()
    try:
        assert entered.wait(2)
        assert service.request() == run
        assert not service.collect_once()  # A second worker cannot share the browser run.
        with closing(service.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
    finally:
        release.set(); worker.join(5)
    assert not worker.is_alive()


def test_restart_skips_completed_sources_and_lost_lease_discards_result(service):
    service.sources = [SOURCE, {**SOURCE, "section": "other"}]
    run = service.request()
    with closing(service.connect()) as conn, conn:
        conn.execute("UPDATE collection_runs SET status='running',lease='old',lease_until=0 WHERE id=?", (run,))
        conn.execute("UPDATE collection_run_sources SET status='done' WHERE source_id='world'")
    calls = []
    def result(source):
        calls.append(source["section"])
        with closing(service.connect()) as conn, conn:
            conn.execute("UPDATE collection_runs SET lease='another-worker'")
        return {"ok": True, "items": [article()]}
    service.collector = result
    service.collect_once()
    assert calls == ["other"]
    assert not query(service,"SELECT * FROM collection_items")


def test_translation_validation_and_error_history_are_safe(service):
    raw = {**article(), "title":"An English headline", "translation_policy":"always"}
    for value in ({"title":"翻译标题"}, {"title":"", "summary":"", "quoted_text":""},
                  {"title":"Not translated", "summary":"", "quoted_text":""}):
        with pytest.raises(ValueError): collection_translation.validate(value,raw)
    service.collector = lambda _: {"ok":False,"items":[],"error_code":"token=private-secret","error":"private"}
    service.request(); service.collect_once()
    history = service.errors(collection.now().date().isoformat())
    assert "private" not in json.dumps(history)
    assert history["stats"]["availability"] == 0


@pytest.mark.parametrize("stop_reason", ["stop", "length", "aborted", None])
def test_pi_collection_translation_requires_a_complete_response(monkeypatch, stop_reason):
    raw = {**article(title="An English headline"), "translation_policy": "always"}
    translated = {"title": "翻译后的新闻标题", "summary": "", "quoted_text": ""}
    message = {"role": "assistant", "content": [{"type": "text", "text": json.dumps(translated)}]}
    if stop_reason is not None:
        message["stopReason"] = stop_reason
    stdout = json.dumps({"type": "message_end", "message": message})
    monkeypatch.setattr(collection_translation, "run_command",
                        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, stdout, ""))
    monkeypatch.setattr(collection_translation.llm_client, "_pi_subprocess_env", lambda: {})
    config = settings.default_app_settings()
    config["llm"]["pi_chat"].update(provider="test", model="test-model")
    if stop_reason == "stop":
        assert collection_translation.pi_translate(raw, config) == translated
    else:
        # Even valid JSON must not let a truncated or interrupted answer publish a batch.
        with pytest.raises(ValueError, match="translation_pi_failed"):
            collection_translation.pi_translate(raw, config)


def test_actual_ingestion_day_is_independent_of_publication_day(service):
    service.request(); service.collect_once(); service.translate_once()
    service.publish_once()
    history = service.errors(collection.now().date().isoformat())
    assert history["stats"]["ingested_count"] == 1


def test_batch_is_atomic_and_enqueues_downstream_once(service, monkeypatch):
    service.collector = lambda _: {"ok": True, "items": [article(f"https://example.com/{i}") for i in range(3)]}
    run = service.request()
    service.collect_once()
    enqueued = []
    for module, name in ((collection.news_classification, "enqueue"), (collection.topic_tracking, "enqueue_checks")):
        original = getattr(module, name)
        def enqueue(conn, original=original, name=name):
            enqueued.append((name, conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]))
            return original(conn)
        monkeypatch.setattr(module, name, enqueue)
    original_publish = service.publish
    def publish(conn, job, translation, ts):
        result = original_publish(conn, job, translation, ts)
        assert not query(service, "SELECT id FROM items")  # Other connections see no partial batch.
        return result
    monkeypatch.setattr(service, "publish", publish)
    for i in range(3):
        assert service.translate_once()
        assert not query(service, "SELECT id FROM items")
        if i < 2:
            assert not service.publish_once()
    assert service.publish_once()
    assert not service.publish_once()
    assert len(query(service, "SELECT id FROM items")) == 3
    assert len(query(service, "SELECT DISTINCT ingested_at FROM collection_items")) == 1
    assert enqueued == [("enqueue", 3), ("enqueue_checks", 3)]
    assert query(service, "SELECT publication_status FROM collection_runs WHERE id=?", (run,))[0][0] == "done"


def test_ready_items_wait_for_acquisition_to_finish(service):
    entered, release = threading.Event(), threading.Event()
    service.sources = [SOURCE, {**SOURCE, "section": "second"}]
    def collect(source):
        if source["section"] == "second":
            entered.set()
            assert release.wait(5)
            return {"ok": False, "items": [], "error_code": "TIMEOUT"}
        return {"ok": True, "items": [article()]}
    service.collector = collect
    service.request()
    worker = threading.Thread(target=service.collect_once)
    worker.start()
    try:
        assert entered.wait(2)
        assert service.translate_once()
        assert service.status()["counts"]["ready"] == 1
        assert not service.publish_once()
        assert not query(service, "SELECT * FROM items")
    finally:
        release.set(); worker.join(5)
    assert service.publish_once()  # A failed source contributes no news to wait for.
    assert len(query(service, "SELECT id FROM items")) == 1


def test_failed_translation_holds_whole_batch_and_retry_reuses_ready_items(service):
    calls = []
    service.collector = lambda _: {"ok": True, "items": [article(f"https://example.com/{i}") for i in range(2)]}
    def translate(raw, _):
        calls.append(raw["url"])
        if raw["url"].endswith("/1") and calls.count(raw["url"]) == 1:
            raise RuntimeError("HTTP_429")
        return collection_translation.original_fields(raw)
    service.translator = translate
    service.request(); service.collect_once()
    service.translate_once(); service.translate_once()
    assert service.status()["counts"]["ready"] == service.status()["counts"]["failed"] == 1
    assert not service.publish_once()
    assert not query(service, "SELECT id FROM items")
    assert service.retry() == 1
    service.translate_once()
    assert service.publish_once()
    assert len(query(service, "SELECT id FROM items")) == 2
    assert calls.count("https://example.com/0") == 1
    assert calls.count("https://example.com/1") == 2


def test_publication_failure_rolls_back_and_retries_without_translating(service, monkeypatch):
    service.collector = lambda _: {"ok": True, "items": [article(f"https://example.com/{i}") for i in range(2)]}
    service.request(); service.collect_once()
    service.translate_once(); service.translate_once()
    original = collection.topic_tracking.enqueue_checks
    monkeypatch.setattr(collection.topic_tracking, "enqueue_checks", lambda _: (_ for _ in ()).throw(RuntimeError("disk_failure")))
    assert service.publish_once()
    for table in ("items", "item_classifications", "topic_ingestions"):
        assert not query(service, f"SELECT * FROM {table}")
    assert service.status()["publication_failed"] == 1
    assert service.errors(collection.now().date().isoformat())["stats"]["ingested_count"] == 0
    assert service.status()["counts"]["ready"] == 2
    assert not service.publish_once()
    monkeypatch.setattr(collection.topic_tracking, "enqueue_checks", original)
    service.translator = lambda *_: pytest.fail("already translated")
    restarted = collection.Service(service.connect, service.settings, translator=service.translator, sources=[SOURCE])
    assert restarted.retry() == 1
    assert not restarted.translate_once()
    assert restarted.publish_once()
    assert len(query(service, "SELECT * FROM items")) == 2
    assert restarted.status()["publication_failed"] == 0
    assert restarted.errors(collection.now().date().isoformat())["stats"]["ingested_count"] == 2


def test_concurrent_translation_respects_live_limit_and_publishes_once(service):
    config = service.settings()["collection"]
    config["translation_concurrency"] = 2
    service.collector = lambda _: {"ok": True, "items": [article(f"https://example.com/{i}") for i in range(6)]}
    service.request(); service.collect_once()
    started = queue.Queue()
    releases = {f"https://example.com/{i}": threading.Event() for i in range(6)}
    def translate(raw, _):
        started.put(raw["url"])
        assert releases[raw["url"]].wait(10)
        return collection_translation.original_fields(raw)
    service.translator = translate
    worker = threading.Thread(target=service.translation_loop)
    worker.start()
    try:
        first = [started.get(timeout=3) for _ in range(2)]
        with pytest.raises(queue.Empty): started.get(timeout=0.15)
        config["translation_concurrency"] = 3
        service.wake.set()
        third = started.get(timeout=3)
        config["translation_concurrency"] = 1
        releases[first[0]].set(); releases[first[1]].set()
        with pytest.raises(queue.Empty): started.get(timeout=0.2)
        assert not query(service, "SELECT * FROM items")
        releases[third].set()
        for _ in range(3):
            ident = started.get(timeout=3)
            with pytest.raises(queue.Empty): started.get(timeout=0.1)
            releases[ident].set()
        deadline = time.monotonic()+4
        while time.monotonic()<deadline and not query(service, "SELECT * FROM items"):
            time.sleep(0.02)
        assert len(query(service, "SELECT * FROM items")) == 6
        assert all(row[0] == 1 for row in query(service, "SELECT attempts FROM collection_items"))
    finally:
        service.stop()
        for release in releases.values(): release.set()
        worker.join(5)
    assert not worker.is_alive()


@pytest.mark.parametrize("value", [0,33,-1,1.5,True,"4",None])
def test_invalid_translation_concurrency_is_rejected(value):
    with pytest.raises(ValueError, match="collection_translation_concurrency_invalid"):
        collection.normalize_schedule({"translation_concurrency":value})


def test_translation_limit_change_preserves_due_schedule(service):
    config = service.settings()["collection"]
    service.schedule_once(datetime.fromisoformat("2026-11-09T08:59:00+08:00"))
    config["translation_concurrency"] = 7
    service.schedule_once(datetime.fromisoformat("2026-11-09T09:00:00+08:00"))
    assert len(query(service, "SELECT id FROM collection_runs")) == 1


def test_migration_keeps_previously_published_batches_and_is_idempotent(service):
    run = service.request(); service.collect_once(); service.translate_once(); service.publish_once()
    before = dict(query(service, "SELECT * FROM items")[0])
    with closing(service.connect()) as conn, conn:
        conn.execute("ALTER TABLE collection_runs DROP COLUMN publication_status")
        conn.execute("ALTER TABLE collection_runs DROP COLUMN publication_error")
        conn.execute("ALTER TABLE collection_runs DROP COLUMN published_at")
        collection.initialize(conn)
        collection.initialize(conn)
    after = dict(query(service, "SELECT * FROM items")[0])
    assert before == after
    migrated = dict(query(service, "SELECT * FROM collection_runs WHERE id=?", (run,))[0])
    assert migrated["publication_status"] == "done"
    assert migrated["published_at"] == before["created_at"]
    assert not service.publish_once()


@pytest.fixture
def api(tmp_path, monkeypatch):
    for name, filename in (("DB_PATH","api.sqlite3"),("AGENT_DB_PATH","agent.sqlite3"),
                           ("APP_SETTINGS_PATH","settings.json"),("MEDIA_CACHE_DIR","media"),("AGENT_RUNTIME_DIR","runtime")):
        monkeypatch.setenv("NEWS_READER_"+name, str(tmp_path/filename))
    import app
    module = importlib.reload(app)
    module.ensure_db()
    yield module, module.app.test_client()


def add(module, ident, published="2026-11-08 12:00:00"):
    with closing(module.db_conn()) as conn, conn:
        conn.execute("INSERT INTO items VALUES (?,'',0,?,?,'12:00','Reuters','reuters','Reuters','测试新闻','',?,'now','now')", (ident,published,published[:10],f"https://example.com/{ident}"))


def test_application_start_resumes_collection_without_a_browser_request(api, service, monkeypatch):
    module, _ = api
    monkeypatch.setattr(module, "COLLECTION_SERVICE", service)
    monkeypatch.setattr(module, "start_detail_worker", lambda: None)
    monkeypatch.setattr(module, "start_agent_worker", lambda: None)
    monkeypatch.setattr(module.CLASSIFICATION_SERVICE, "configure", lambda **_: None)
    for other in (module.CLASSIFICATION_SERVICE, module.TOPIC_SERVICE, module.REPORT_SERVICE, module.trading_calendar.SERVICE):
        monkeypatch.setattr(other, "start", lambda: None)
    service.request()

    def serve(**_):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if service.last_check and query(service, "SELECT id FROM items"):
                break
            time.sleep(0.02)
        assert service.last_check is not None
        assert len(service.threads) == 3
        assert all(thread.is_alive() for thread in service.threads)
        assert service.status()["counts"]["done"] == 1

    monkeypatch.setattr(module.app, "run", serve)
    try:
        module.main()
    finally:
        service.stop()
        for thread in service.threads:
            thread.join(timeout=2)
    assert not any(thread.is_alive() for thread in service.threads)


def test_manual_report_does_not_start_collection(api, monkeypatch):
    module, client = api
    monkeypatch.setattr(module.REPORT_SERVICE, "create_manual", lambda *_: {"id": "report"})
    monkeypatch.setattr(module.REPORT_SERVICE, "start", lambda: None)
    monkeypatch.setattr(module.COLLECTION_SERVICE, "start", lambda: pytest.fail("report must not start collection"))
    assert client.post("/api/reports/manual", json={"kind": "morning"}).status_code == 202


def test_translation_concurrency_settings_save_reload_and_preserve_schedule(api):
    module, client = api
    original = client.get("/api/settings").json
    payload = {"llm": original["llm"], "collection": {"translation_concurrency": 9}}
    response = client.put("/api/settings", json=payload)
    assert response.status_code == 200, response.json
    saved = client.get("/api/settings").json
    assert saved["collection"]["translation_concurrency"] == 9
    assert settings.load_app_settings()["collection"]["translation_concurrency"] == 9
    assert saved["collection"]["daily"] == original["collection"]["daily"]
    assert saved["report_schedule"] == original["report_schedule"]
    for value in (0,33,False,2.5,"9",None):
        response = client.put("/api/settings", json={**payload, "collection":{"translation_concurrency":value}})
        assert response.status_code == 400
        assert response.json["error"] == "collection_translation_concurrency_invalid"
    assert client.get("/api/settings").json["collection"]["translation_concurrency"] == 9


def test_feed_and_search_keep_ingestion_boundary_including_backdated_news(api):
    module, client = api
    for i in range(12): add(module,str(i))
    first = client.get("/api/news?read_filter=unread&per=10").json
    add(module,"late","2026-11-01 12:00:00")
    params = {"read_filter":"unread", "per":10, "batch":first["batch"],
              **{"cursor_"+k.replace("date_key","date"):v for k,v in first["next_cursor"].items()}}
    page = client.get("/api/news",query_string=params).json
    assert len(page["items"]) == 2
    assert all(x["id"] != "late" for x in page["items"])
    assert client.get("/api/news",query_string={"count_only":1,"after_batch":first["batch"]}).json["total"] == 1
    assert client.get("/api/search",query_string={"q":"测试","batch":first["batch"]}).json["total"] == 12
    assert client.get("/api/news").json["total"] == 13


def test_bulk_read_preview_excludes_arrivals_and_rejects_live_write(api):
    module, client = api
    add(module,"one")
    preview = client.post("/api/news/mark-all-read",json={"preview":True,"read_filter":"unread"}).json
    add(module,"two")
    result = client.post("/api/news/mark-all-read",json={"preview_id":preview["preview_id"]})
    assert result.json["marked"] == 1
    assert [x["id"] for x in client.get("/api/news?read_filter=unread").json["items"]] == ["two"]
    assert client.post("/api/news/mark-all-read",json={}).status_code == 400
    assert client.post("/api/reindex").status_code == 404
    assert client.post("/api/collection").status_code == 202


def test_unknown_publication_time_can_page_without_inventing_a_date(api):
    module, client = api
    for i in range(12): add(module,str(i),published="")
    first = client.get("/api/news?read_filter=unread&per=10").json
    assert first["date_counts"] == {"unknown":12}
    cursor = first["next_cursor"]
    assert cursor["date_key"] == cursor["published_at"] == ""
    result = client.get("/api/news",query_string={"read_filter":"unread","per":10,
        "batch":first["batch"],"cursor_date":"","cursor_published_at":"","cursor_id":cursor["id"]})
    assert result.status_code == 200
    assert len(result.json["items"]) == 2
    assert not {x["id"] for x in first["items"]} & {x["id"] for x in result.json["items"]}
    assert client.get("/api/news?read_filter=unread&cursor_id=only").status_code == 400


def test_collection_history_counts_duplicates_filters_and_real_publication(service):
    service.sources = [SOURCE, {**SOURCE, "section": "other", "display_name": "Other"}]
    service.collector = lambda source: {"ok": True, "items": (
        [article(), article("https://example.com/story?utm_source=again"),
         article("https://example.com/empty", title=""),
         article("https://www.bloomberg.com/news/videos/2026-11-09/test-video")]
        if source["section"] == "world" else [article(), article("https://example.com/second")])}
    run = service.request()
    day = collection.now().date().isoformat()
    queued = service.errors(day)
    assert len(queued["records"]) == 2
    assert all(r["status"] == "pending" for r in queued["records"])
    assert all(r["duplicate_count"] == 0 for r in queued["records"])
    service.collect_once()
    before = service.errors(day)
    assert before["stats"]["fetched_count"] == 6
    assert before["stats"]["duplicate_count"] == 2  # Within a source and across sources.
    assert before["stats"]["error_count"] == 0
    assert before["stats"]["ingested_count"] == 0
    source = next(r for r in before["records"] if r["source_id"] == "world")
    assert source["filtered_count"] == 2
    service.translate_once(); service.translate_once()
    assert service.errors(day)["stats"]["ingested_count"] == 0  # Ready is not ingested.
    assert service.publish_once()
    assert not service.publish_once()
    after = service.errors(day)
    assert after["stats"]["ingested_count"] == 2
    assert sum(r["ingested_count"] for r in after["records"]) == 2
    assert {r["run_id"] for r in after["records"]} == {run}
    assert not after["days"]  # Legacy failure-only response remains compatible.
    service.request(); service.collect_once(); service.publish_once()
    repeated = service.errors(day)
    assert repeated["stats"]["fetched_count"] == 12
    assert repeated["stats"]["duplicate_count"] == 6
    assert repeated["stats"]["ingested_count"] == 2


def test_collection_history_successful_empty_and_recovered_sources_remain_visible(service):
    day = collection.now().date().isoformat()
    service.collector = lambda _: {"ok": False, "items": [], "error_code": "TIMEOUT"}
    service.request(); service.collect_once()
    assert service.errors(day, details=False)["unresolved_count"] == 1
    service.collector = lambda _: {"ok": True, "items": []}
    service.request(); service.collect_once(); service.publish_once(); service.publish_once()
    history = service.errors(day)
    assert len(history["records"]) == 2
    assert {r["status"] for r in history["records"]} == {"done", "failed"}
    assert history["stats"]["fetched_count"] == history["stats"]["duplicate_count"] == 0
    assert history["stats"]["error_count"] == 1
    assert next(r for r in history["records"] if r["error_count"])["error"] == "TIMEOUT"
    assert history["unresolved_count"] == 0
    assert history["days"][0]["groups"][0]["entries"][0]["error"] == "TIMEOUT"
    light = service.errors(day, details=False)
    assert light["unresolved_count"] == 0
    assert "records" not in light and "stats" not in light


def test_collection_history_cross_midnight_and_deleted_items_keep_ingestion_totals(service, monkeypatch):
    at = datetime.fromisoformat("2026-11-09T23:59:00+08:00")
    monkeypatch.setattr(collection, "now", lambda: at)
    service.request(); service.collect_once(); service.translate_once()
    at = datetime.fromisoformat("2026-11-10T00:01:00+08:00")
    service.publish_once()
    previous = service.errors("2026-11-09")
    current = service.errors("2026-11-10")
    assert previous["stats"]["fetched_count"] == 1
    assert previous["stats"]["ingested_count"] == 0
    assert current["stats"]["fetched_count"] == current["stats"]["duplicate_count"] == 0
    assert current["stats"]["ingested_count"] == 1
    assert current["records"][0]["kind"] == "publication"
    assert current["records"][0]["time"] == "00:01:00"
    with closing(service.connect()) as conn, conn:
        conn.execute("UPDATE news_identities SET deleted=1")
        conn.execute("DELETE FROM collection_items")
        conn.execute("DELETE FROM items")
    assert service.errors("2026-11-10") == current
    service.request(); service.collect_once()
    history = service.errors("2026-11-10")
    assert history["stats"]["duplicate_count"] == history["stats"]["ingested_count"] == 1
    assert not query(service, "SELECT * FROM collection_items")


def test_collection_history_migrates_old_counts_without_guessing_or_changing_news(service):
    service.collector = lambda _: {"ok": True, "items": [article(), article()]}
    service.request(); service.collect_once(); service.translate_once(); service.publish_once()
    before = dict(query(service, "SELECT * FROM items")[0])
    with closing(service.connect()) as conn, conn:
        conn.execute("DROP INDEX idx_collection_sources_ingested")
        for column in ("duplicate_count", "filtered_count", "ingested_count", "ingested_at"):
            conn.execute(f"ALTER TABLE collection_run_sources DROP COLUMN {column}")
        collection.initialize(conn)
        collection.initialize(conn)
    history = service.errors(collection.now().date().isoformat())
    assert history["records"][0]["duplicate_count"] is None
    assert history["stats"]["duplicate_unknown"] == 1
    assert history["stats"]["ingested_count"] == 1
    assert dict(query(service, "SELECT * FROM items")[0]) == before


def test_collection_history_invalid_item_is_not_reported_as_a_duplicate(service):
    service.collector = lambda _: {"ok": True, "items": [article("javascript:invalid"), article()]}
    service.request(); service.collect_once()
    history = service.errors(collection.now().date().isoformat())
    assert history["stats"]["fetched_count"] == 2
    assert history["stats"]["duplicate_count"] == 0
    assert history["stats"]["error_count"] == 1
    assert history["records"][0]["error"] == "INVALID_ITEM"
    assert len(query(service, "SELECT * FROM collection_items")) == 1


def test_collection_history_api_dates_and_lightweight_badge(api, service, monkeypatch):
    module, client = api
    monkeypatch.setattr(module, "COLLECTION_SERVICE", service)
    service.request(); service.collect_once()
    response = client.get("/api/error-stats?details=1")
    assert response.status_code == 200
    assert response.json["records"][0]["status"] == "done"
    assert response.json["stats"]["fetched_count"] == 1
    light = client.get("/api/error-stats?details=0").json
    assert light["unresolved_count"] == 0 and "records" not in light
    for day in ("2026-02-30", "2026-1-1", "<script>", "2026-11-09T00:00:00"):
        assert client.get("/api/error-stats", query_string={"day": day}).status_code == 400
    empty = client.get("/api/error-stats?day=2026-01-01").json
    assert not empty["records"]
    assert all(empty["stats"][key] == 0 for key in ("fetched_count", "duplicate_count", "ingested_count", "error_count"))
    assert client.get("/api/error-stats?day=9999-12-31").json["records"] == []


def test_collection_notices_and_translation_retry_are_scoped_to_run_and_source(service):
    service.sources = [SOURCE, {**SOURCE, "section": "other"}, {**SOURCE, "section": "broken"}]
    generation, calls = 0, []
    def collect(source):
        if source["section"] == "broken":
            return {"ok": False, "items": [], "error_code": "TIMEOUT"}
        return {"ok": True, "items": [article(f"https://example.com/{generation}/{source['section']}/{i}") for i in range(2)]}
    def translate(raw, _):
        calls.append(raw["url"])
        if raw["url"].endswith("/0"):
            raise RuntimeError("HTTP_429")
        return collection_translation.original_fields(raw)
    service.collector, service.translator = collect, translate
    first = service.request(); service.collect_once()
    for _ in range(4): assert service.translate_once()
    generation = 1
    second = service.request(); service.collect_once()
    for _ in range(4): assert service.translate_once()
    history = service.errors(collection.now().date().isoformat())
    assert history["stats"]["error_count"] == 2  # Translation failures are notices, not acquisition errors.
    assert len({r["batch_started_at"] for r in history["records"] if r["run_id"] == first}) == 1
    for record in history["records"]:
        if record["source_id"] == "broken":
            assert record["error"] == "TIMEOUT" and record["notices"] == []
        else:
            assert record["error_count"] == 0
            assert record["notices"] == [{"stage": "translation", "count": 1, "error": "1 条：HTTP_429"}]
    errors_before = [tuple(row) for row in query(service, "SELECT * FROM collection_run_sources")]
    assert service.retry(run_id=first, source_id="world", stage="translation") == 1
    assert service.retry(run_id=first, source_id="world", stage="translation") == 0
    assert [tuple(row) for row in query(service, "SELECT * FROM collection_run_sources")] == errors_before
    remaining = query(service, "SELECT run_id,source_id FROM collection_items WHERE status='failed'")
    assert {(row[0], row[1]) for row in remaining} == {(first, "other"), (second, "world"), (second, "other")}
    assert len(query(service, "SELECT * FROM collection_items WHERE status='ready'")) == 4
    service.collector = lambda _: pytest.fail("retry must not recollect")
    service.translator = lambda raw, _: collection_translation.original_fields(raw)
    assert service.translate_once()
    assert not service.translate_once()
    assert not service.publish_once()  # Another source still needs repair.
    assert len(calls) == 8
    refreshed = service.errors(collection.now().date().isoformat())
    assert not next(r for r in refreshed["records"] if r["run_id"] == first and r["source_id"] == "world")["notices"]
    assert refreshed["stats"]["error_count"] == 2


def test_publication_notice_retry_only_republishes_selected_batch(service, monkeypatch):
    original = collection.topic_tracking.enqueue_checks
    monkeypatch.setattr(collection.topic_tracking, "enqueue_checks", lambda _: (_ for _ in ()).throw(RuntimeError("disk_failure")))
    runs = []
    for index in range(2):
        service.collector = lambda _, index=index: {"ok": True, "items": [article(f"https://example.com/batch/{index}")]}
        runs.append(service.request()); service.collect_once(); service.translate_once()
        assert service.publish_once()
    history = service.errors(collection.now().date().isoformat())
    assert history["stats"]["error_count"] == history["stats"]["ingested_count"] == 0
    assert all(r["notices"] == [{"stage": "publication", "error": "collection_publish_failed"}] for r in history["records"])
    monkeypatch.setattr(collection.topic_tracking, "enqueue_checks", original)
    service.translator = lambda *_: pytest.fail("saved translations must be reused")
    service.collector = lambda _: pytest.fail("retry must not recollect")
    assert service.retry(run_id=runs[0], stage="publication") == 1
    assert not service.translate_once()
    assert service.publish_once()
    assert not service.publish_once()
    assert len(query(service, "SELECT * FROM items")) == 1
    assert query(service, "SELECT publication_status FROM collection_runs WHERE id=?", (runs[1],))[0][0] == "failed"
    assert service.retry(run_id=runs[0], stage="publication") == 0
    history = service.errors(collection.now().date().isoformat())
    assert not next(r for r in history["records"] if r["run_id"] == runs[0])["notices"]
    assert next(r for r in history["records"] if r["run_id"] == runs[1])["notices"]


def test_scoped_retry_api_rejects_bad_targets_without_retrying_other_failures(api, service, monkeypatch):
    module, client = api
    monkeypatch.setattr(module, "COLLECTION_SERVICE", service)
    service.translator = lambda *_: (_ for _ in ()).throw(RuntimeError("HTTP_429"))
    run = service.request(); service.collect_once(); service.translate_once()
    endpoint = "/api/collection/retry-translation"
    for body in ([run], {"run_id": run}, {"stage": "translation"}, {"run_id": None},
                 {"run_id": run, "stage": "collection"}, {"run_id": run, "stage": []},
                 {"run_id": run, "stage": "translation", "source_id": []},
                 {"run_id": run, "stage": "publication", "source_id": "world"},
                 {"run_id": run, "stage": "publication", "unknown": True}):
        response = client.post(endpoint, json=body)
        assert response.status_code == 400 and response.json["error"] == "collection_retry_invalid"
    for body in ({"run_id": "missing", "stage": "publication"},
                 {"run_id": run, "stage": "translation", "source_id": "missing"}):
        assert client.post(endpoint, json=body).status_code == 404
    assert client.post(endpoint, data="{", content_type="application/json").status_code == 400
    assert service.status()["counts"]["failed"] == 1
    body = {"run_id": run, "stage": "translation", "source_id": "world"}
    response = client.post(endpoint, json=body)
    assert response.status_code == 202 and response.json["retried"] == 1
    assert client.post(endpoint, json=body).json["retried"] == 0
    service.translate_once()
    assert client.post(endpoint).json["retried"] == 1  # Existing global retry remains compatible.
