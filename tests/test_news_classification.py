import importlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import pytest

import news_classification as nc
from scanner import apply_schema, reindex

ROOT = Path(__file__).resolve().parents[1]


def response(topic="AI", china=.1):
    return {"model": "jev-1.14.0", "answers": {
        "topic": {"type": "choice", "choice": topic, "confidence": .95,
                  "probabilities": {k: float(k == topic) for k in nc.TOPICS}},
        "china_focus": {"type": "noul", "noul": china}}, "usage": {"input_tokens": 1500}}


def insert(conn, ident="a", title="标题", source="Reuters"):
    conn.execute("""INSERT INTO items(id,source_file,item_order,published_at,date,time,source,source_type,
        source_name,title,summary,url,created_at,updated_at) VALUES (?,?,0,?,?,?,?,?,?,?,?,?,?,?)""",
                 (ident, "fixture.md", "2026-10-23 12:00:00", "2026-10-23", "12:00", source, "news", source,
                  title, "分类共同测试词", f"https://example.com/{ident}", "now", "now"))
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    with closing(sqlite3.connect(tmp_path / "index.sqlite3")) as conn:
        conn.row_factory = sqlite3.Row
        apply_schema(conn, ROOT / "schema.sql")
        yield conn


@pytest.mark.parametrize("topic,china,expected", [
    ("AI", .99, "ai"), ("政治与国际", .06, "politics"), ("经济与金融", .11, "economy"),
    ("商业与产业", .51, "china"), ("商业与产业", .5, "business"), ("科技与科学", .2, "science"),
    ("其他", .2, "other"),
])
def test_single_primary_category(topic, china, expected):
    assert nc.parse_response(response(topic, china))["category"] == expected


@pytest.mark.parametrize("mutation", [
    lambda p: p.pop("model"),
    lambda p: p["answers"]["china_focus"].update(noul=True),
    lambda p: p["answers"]["china_focus"].update(noul=float("nan")),
    lambda p: p["answers"]["topic"].update(choice="中国"),
    lambda p: p["answers"]["topic"].update(probabilities={"AI": 1}),
    lambda p: p["answers"]["topic"].update(confidence=2),
])
def test_malformed_results_are_retriable_not_other(mutation):
    payload = response()
    mutation(payload)
    with pytest.raises(nc.ClassificationError) as exc:
        nc.parse_response(payload)
    assert exc.value.retryable


def test_idempotence_changed_titles_and_stale_completion(conn):
    insert(conn)
    with conn:
        assert nc.enqueue(conn) == 1
    first = nc.claim(conn)
    with conn:
        conn.execute("UPDATE items SET title='新标题' WHERE id='a'")
        assert nc.enqueue(conn) == 1
    second = nc.claim(conn)
    assert not nc.finish(conn, first, nc.parse_response(response()))
    assert nc.finish(conn, second, nc.parse_response(response("政治与国际")))
    with conn:
        assert nc.enqueue(conn) == 0
    row = conn.execute("SELECT * FROM item_classifications").fetchone()
    assert row["category"] == "politics" and row["model"] == "jev-1.14.0"
    assert row["title_hash"] == nc.fingerprint("新标题")
    assert json.loads(row["probabilities_json"])["政治与国际"] == 1


def test_claim_is_exclusive_and_expired_lease_recovers(conn):
    insert(conn)
    with conn:
        nc.enqueue(conn)
    first = nc.claim(conn)
    assert nc.claim(conn) is None
    with conn:
        conn.execute("UPDATE item_classifications SET lease_until=0")
    recovered = nc.claim(conn)
    assert recovered["attempts"] == 2
    assert not nc.finish(conn, first, nc.parse_response(response()))
    assert nc.finish(conn, recovered, nc.parse_response(response()))


def test_finite_retry_backoff_manual_retry_and_config_pause(conn):
    insert(conn)
    with conn:
        nc.enqueue(conn)
    for i in range(nc.MAX_ATTEMPTS):
        job = nc.claim(conn)
        nc.finish(conn, job, None, nc.ClassificationError("http_429", retryable=True))
        assert nc.claim(conn) is None
        with conn:
            conn.execute("UPDATE item_classifications SET next_attempt_at=0")
    row = conn.execute("SELECT * FROM item_classifications").fetchone()
    assert row["status"] == "failed" and row["category"] is None
    assert nc.retry_failed(conn) == 1
    job = nc.claim(conn)
    nc.finish(conn, job, None, nc.ClassificationError("http_401", block_config=True))
    row = conn.execute("SELECT * FROM item_classifications").fetchone()
    assert row["status"] == "pending" and row["attempts"] == 0


def test_worker_persists_result_and_pauses_bad_configuration(conn):
    insert(conn)
    with conn:
        nc.enqueue(conn)
    path = conn.execute("PRAGMA database_list").fetchone()[2]

    def connect():
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        return db

    calls = []

    def runner(title, key, model):
        calls.append((title, model))
        if key == "invalid":
            raise nc.ClassificationError("http_401", block_config=True)
        return nc.parse_response(response("经济与金融"))

    service = nc.Service(connect, lambda: ("valid", "configured-model"), runner=runner)
    assert service.process_one("invalid", "configured-model")
    assert service.error == "http_401"
    assert service.blocked_config == (nc.fingerprint("invalid"), "configured-model")
    service.resume()
    assert not service.error and service.blocked_config is None
    with conn:
        conn.execute("UPDATE item_classifications SET next_attempt_at=0")
    assert service.process_one("valid", "configured-model")
    assert not service.process_one("valid", "configured-model")
    assert calls == [("标题", "configured-model"), ("标题", "configured-model")]
    assert nc.status(conn)["done"] == 1
    assert conn.execute("SELECT category FROM item_classifications").fetchone()[0] == "economy"


def test_rule_change_and_deleted_item(conn):
    insert(conn)
    with conn:
        nc.enqueue(conn)
    job = nc.claim(conn)
    with conn:
        conn.execute("UPDATE item_classifications SET rule_version='old'")
        assert nc.enqueue(conn) == 1
    assert not nc.finish(conn, job, nc.parse_response(response()))
    with conn:
        conn.execute("DELETE FROM items")
        nc.enqueue(conn)
    assert nc.status(conn)["total"] == 0


def test_full_reindex_reuses_labels_and_preserves_user_state(conn, tmp_path):
    daily = tmp_path / "daily"
    daily.mkdir()
    path = daily / "dailyFreshNews_2026-10-23.md"
    path.write_text("## Reuters · World（1条）\n### [模型发布](https://example.com/model)\n- 发布时间：2026-10-23 12:00:00\n")
    assert reindex(conn, daily).classification_queued == 1
    job = nc.claim(conn)
    nc.finish(conn, job, nc.parse_response(response()))
    with conn:
        conn.execute("INSERT INTO item_state(item_id,read_at,favorite_at,updated_at) VALUES (?,?,?,?)",
                     (job["item_id"], "read", "favorite", "now"))
    assert reindex(conn, daily, full=True).classification_queued == 0
    assert nc.status(conn)["done"] == 1
    assert tuple(conn.execute("SELECT read_at,favorite_at FROM item_state").fetchone()) == ("read", "favorite")
    path.write_text(path.read_text().replace("模型发布", "交易日：AI风暴平息"))
    assert reindex(conn, daily).classification_queued == 1
    assert nc.status(conn)["pending"] == 1


def test_native_request_and_http_error_sanitization(monkeypatch):
    from urllib.error import HTTPError
    captured = {}

    def fake_open(req, **kwargs):
        captured.update(body=json.loads(req.data), headers=req.headers, timeout=kwargs["timeout"])
        raise HTTPError(req.full_url, 401, "sensitive upstream text", {}, None)

    monkeypatch.setattr(nc, "urlopen", fake_open)
    with pytest.raises(nc.ClassificationError) as exc:
        nc.classify("交易日：AI风暴平息", "secret-for-test", "jev-1.14.0")
    assert str(exc.value) == "http_401" and exc.value.block_config
    assert captured["body"]["state"] == "交易日：AI风暴平息"
    assert set(captured["body"]["questions"]) == {"topic", "china_focus"}
    assert captured["headers"]["Authorization"] == "Bearer secret-for-test"


@pytest.fixture
def api(tmp_path, monkeypatch):
    for key, path in {"DB_PATH": "news.sqlite3", "AGENT_DB_PATH": "agent.sqlite3",
                      "APP_SETTINGS_PATH": "settings.json", "MEDIA_CACHE_DIR": "media",
                      "DAILY_NEWS_DIR": "daily"}.items():
        monkeypatch.setenv("NEWS_READER_" + key, str(tmp_path / path))
    import app
    module = importlib.reload(app)
    module.ensure_db()
    monkeypatch.setattr(module, "provider_has_configured_key", lambda _: False)
    with closing(module.db_conn()) as conn:
        for i in range(36):
            insert(conn, f"n{i:02}", "模型发布" if i < 12 else "市场新闻")
        with conn:
            nc.enqueue(conn)
        for _ in range(34):
            job = nc.claim(conn)
            category = "AI" if int(job["item_id"][1:]) < 12 else "经济与金融"
            nc.finish(conn, job, nc.parse_response(response(category)))
    return module, module.app.test_client()


def test_filtered_pagination_search_counts_and_scoped_mark_read(api):
    module, client = api
    all_items = client.get("/api/news?read_filter=unread").json
    assert all_items["total"] == 36
    first = client.get("/api/news?category=economy&read_filter=unread&per=10").json
    assert first["total"] == 24 and len(first["items"]) == 10
    assert all(i["category"] == "economy" for i in first["items"])
    from urllib.parse import urlencode
    cursor = first["next_cursor"]
    second = client.get("/api/news?" + urlencode(dict(category="economy", read_filter="unread", per=10,
                        cursor_date=cursor["date_key"], cursor_published_at=cursor["published_at"], cursor_id=cursor["id"]))).json
    assert not {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}
    search = client.get("/api/search?q=分类共同测试词&category=economy&feed_filters=1&count_only=1").json
    assert search["total"] == 24
    assert client.get("/api/news?category=other&count_only=1").json["total"] == 0
    marked = client.post("/api/news/mark-all-read", json={"category": "economy", "read_filter": "unread"}).json
    assert marked["marked"] == 24
    assert client.get("/api/nav-summary").json["summary"]["feed_unread"] == 12
    assert client.get("/api/news?category=ai&count_only=1&read_filter=unread").json["total"] == 10
    progress = client.get("/api/news/classification").json
    assert (progress["pending"], progress["done"], progress["configured"]) == (2, 34, False)


@pytest.mark.parametrize("url,method", [("/api/news?category=typo", "get"),
    ("/api/search?q=x&category=typo", "get"), ("/api/news/mark-all-read", "post")])
def test_invalid_filter_never_falls_back_to_marking_everything(api, url, method):
    _, client = api
    result = getattr(client, method)(url, **({"json": {"category": "typo"}} if method == "post" else {}))
    assert result.status_code == 400 and result.json["error"] == "invalid_category"


def test_stale_classification_hidden_from_filter_and_serialization(api):
    module, client = api
    with closing(module.db_conn()) as conn, conn:
        conn.execute("UPDATE items SET title='标题被上游修改' WHERE id='n35'")
    assert client.get("/api/news?category=economy&count_only=1").json["total"] == 23
    item = client.get("/api/search?q=标题被上游修改").json["items"][0]
    assert item["category"] is None and item["classification_status"] == "pending"
