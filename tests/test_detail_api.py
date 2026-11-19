from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

import llm_client
from api_fixtures import _isolate_app_settings
from legacy_news_seed import seed_news
from nr import article_fetch, database, details


def test_detail_api_includes_ai_fields(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年5月"
    daily_dir.mkdir(parents=True)
    url = "https://www.reuters.com/world/example-ai"
    (daily_dir / "dailyFreshNews_2026-05-25.md").write_text(
        f"""## Reuters · World（1条）
### [Item 1]({url})
- 发布时间：2026-05-25 12:00:00
""",
        encoding="utf-8",
    )
    db_path = tmp_path / "news_index.sqlite3"
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()
    assert seed_news(client, json={}).status_code == 200

    item = client.get("/api/news").get_json()["items"][0]
    item_id = item["id"]

    conn = app_module.runtime.db.connect()
    try:
        ts = database.now_ts()
        with conn:
            conn.execute(
                """
                INSERT INTO article_details(url, source, title, author, published_at, content, content_length, raw_json, fetched_at, updated_at)
                VALUES (?, 'Reuters', 'T', 'A', '2026-05-25', ?, ?, '{}', ?, ?)
                """,
                (url, "English body " * 30, len("English body " * 30), ts, ts),
            )
            conn.execute(
                """
                INSERT INTO article_ai(url, model, key_points_zh, conclusion_zh, body_zh, raw_json, generated_at, updated_at)
                VALUES (?, 'deepseek-chat', ?, '结论', ?, '{}', ?, ?)
                """,
                (url, '["要点1","要点2","要点3"]', "中" * 260, ts, ts),
            )
            conn.execute(
                """
                INSERT INTO ai_jobs(url, status, attempts, queued_at, updated_at)
                VALUES (?, 'success', 0, ?, ?)
                """,
                (url, ts, ts),
            )
    finally:
        conn.close()

    detail = client.get(f"/api/news/{item_id}/detail")
    assert detail.status_code == 200
    payload = detail.get_json()
    assert payload["ai_status"] == "success"
    assert payload["ai"] is not None
    assert payload["ai"]["conclusion_zh"] == "结论"


def test_generate_pi_fallback_translation_layered(monkeypatch):
    import llm_client as llm
    import runtime_tasks
    calls = []
    structured = json.dumps({"key_points_zh": ["一", "二", "三"], "conclusion_zh": "结论", "body_zh": "译文"})
    def run(config, prompt, **kwargs):
        calls.append((config, prompt, kwargs))
        return structured
    monkeypatch.setattr(runtime_tasks, "run", run)
    args = dict(title="t", source="s", content="body", pi_provider="ollama", pi_model="minimax-m3:cloud")
    out = llm.generate_pi_fallback_translation(**args)
    assert out["key_points_zh"] == ["一", "二", "三"] and out["body_zh"] == "译文"
    assert json.loads(out["raw_json"])["transport"] == "oar"
    assert calls[0][0]["pi"]["model"] == args["pi_model"]
    assert calls[0][2] == {"task": "translation", "timeout": 90}
    monkeypatch.setattr(runtime_tasks, "run", lambda *a, **kw: "这是一段中文译文，不是 JSON")
    out = llm.generate_pi_fallback_translation(**args)
    assert out["key_points_zh"] == [] and out["body_zh"] == "这是一段中文译文，不是 JSON"
    def fail(*a, **kw):
        raise RuntimeError("oar_incomplete_answer")
    monkeypatch.setattr(runtime_tasks, "run", fail)
    with pytest.raises(llm.LLMClientError, match="PI_FALLBACK_FAILED"):
        llm.generate_pi_fallback_translation(**args)


def test_deepseek_failure_uses_pi_fallback_and_persists_result(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews"
    daily_dir.mkdir(parents=True)
    db_path = tmp_path / "news_index.sqlite3"
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(daily_dir))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    stamp = "2026-06-04 17:00:00"
    with app_module.runtime.db.connect() as conn:
        with conn:
            conn.execute(
                """
                INSERT INTO article_details(url, title, source, content, content_length, fetched_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("https://example.com/pi-fallback", "Fallback title", "Reuters", "English body", 12, stamp, stamp),
            )
            conn.execute(
                "INSERT INTO ai_jobs(url, status, attempts, queued_at, updated_at) VALUES (?, 'pending', 0, ?, ?)",
                ("https://example.com/pi-fallback", stamp, stamp),
            )

    def fail_primary(**kwargs):
        raise llm_client.LLMClientError("INVALID_TOOL_ARGUMENTS_JSON: bad json")

    fallback_called = {}

    def succeed_fallback(**kwargs):
        fallback_called.update(kwargs)
        return {
            "model": "minimax-m3:cloud",
            "key_points_zh": ["要点一", "要点二", "要点三"],
            "conclusion_zh": "兜底结论",
            "body_zh": "这是 Pi 兜底译文。",
            "raw_json": '{"provider":"pi-fallback-structured"}',
        }

    monkeypatch.setattr(details, 'generate_article_ai', fail_primary)
    monkeypatch.setattr(details, 'generate_pi_fallback_translation', succeed_fallback)
    assert app_module.runtime.details.process_pending_ai_once() is True

    with app_module.runtime.db.connect() as conn:
        ai_row = conn.execute(
            "SELECT model, body_zh, raw_json FROM article_ai WHERE url=?",
            ("https://example.com/pi-fallback",),
        ).fetchone()
        job_row = conn.execute(
            "SELECT status, last_error FROM ai_jobs WHERE url=?",
            ("https://example.com/pi-fallback",),
        ).fetchone()
    assert ai_row["model"] == "minimax-m3:cloud"
    assert ai_row["body_zh"] == "这是 Pi 兜底译文。"
    assert "pi-fallback-structured" in ai_row["raw_json"]
    assert job_row["status"] == "success"
    assert job_row["last_error"] is None
    assert fallback_called["pi_provider"] == ""


def test_process_pending_jobs_once_twitter_success_and_comment_summary(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-06-11.md").write_text(
        """## X · Social（1条）
### [Tweet Update](https://x.com/example/status/123)
- 发布时间：2026-06-11 09:00:00
""",
        encoding="utf-8",
    )
    db_path = tmp_path / "news_index.sqlite3"
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()
    assert seed_news(client, json={}).status_code == 200

    item = client.get("/api/news?per=20").get_json()["items"][0]
    with app_module.runtime.db.connect() as conn:
        conn.execute(
            "UPDATE items SET source=?, source_name=?, source_type=? WHERE id=?",
            ("Twitter", "Twitter", "twitter", item["id"]),
        )
        details.enqueue_detail_job(conn, item["id"], item["url"], "Twitter")
        conn.commit()

    def fake_twitter_detail(url):
        return (
            True,
            {
                "source": "Twitter/X",
                "title": "Tweet Update",
                "author": "alice",
                "published_at": "2026-06-11 09:00:00",
                "content": "【主推文】\n主推文内容\n\n【引用推文】\n引用内容\n\n【长文补充】\n长文内容\n\n【评论区观点】\n基于已抓取的 6 条评论总结：评论区主要围绕利好与估值分歧展开。",
                "content_length": 120,
                "raw_json": json.dumps(
                    {
                        "tweet": {"text": "主推文内容"},
                        "quoted_tweet": {"text": "引用内容"},
                        "article": {"content": "长文内容"},
                        "comments": [{"text": f"评论 {i}"} for i in range(6)],
                    },
                    ensure_ascii=False,
                ),
            },
            "",
        )

    monkeypatch.setattr(app_module.runtime.fetcher, 'run_opencli_twitter_detail', fake_twitter_detail)
    assert app_module.runtime.details.process_pending_jobs_once() is True

    detail = client.get(f"/api/news/{item['id']}/detail").get_json()
    assert detail["detail_status"] == "success"
    assert detail["detail"]["content"].count("【主推文】") == 1
    assert "基于已抓取的 6 条评论总结" in detail["detail"]["content"]
    assert detail["ai_status"] == "none"


def test_process_pending_jobs_once_twitter_article_failure_does_not_fail_detail(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-06-11.md").write_text(
        """## X · Social（1条）
### [Tweet Update](https://x.com/example/status/123)
- 发布时间：2026-06-11 09:00:00
""",
        encoding="utf-8",
    )
    db_path = tmp_path / "news_index.sqlite3"
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()
    assert seed_news(client, json={}).status_code == 200

    item = client.get("/api/news?per=20").get_json()["items"][0]
    with app_module.runtime.db.connect() as conn:
        conn.execute(
            "UPDATE items SET source=?, source_name=?, source_type=? WHERE id=?",
            ("Twitter", "Twitter", "twitter", item["id"]),
        )
        details.enqueue_detail_job(conn, item["id"], item["url"], "Twitter")
        conn.commit()

    def fake_twitter_detail(url):
        return (
            True,
            {
                "source": "Twitter/X",
                "title": "Tweet Update",
                "author": "alice",
                "published_at": "2026-06-11 09:00:00",
                "content": "【主推文】\n主推文内容\n\n【评论区观点】\n评论区观点总结失败，以下仅展示已抓取评论样本（共 6 条）。",
                "content_length": 80,
                "raw_json": json.dumps(
                    {
                        "tweet": {"text": "主推文内容"},
                        "article_error": "Article not found",
                        "comment_summary_error": "MISSING_DEEPSEEK_API_KEY",
                        "comments": [{"text": f"评论 {i}"} for i in range(6)],
                    },
                    ensure_ascii=False,
                ),
            },
            "",
        )

    monkeypatch.setattr(app_module.runtime.fetcher, 'run_opencli_twitter_detail', fake_twitter_detail)
    assert app_module.runtime.details.process_pending_jobs_once() is True

    detail = client.get(f"/api/news/{item['id']}/detail").get_json()
    assert detail["detail_status"] == "success"
    assert "评论区观点总结失败" in detail["detail"]["content"]


def test_process_pending_ai_once_twitter_generates_body_only(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-06-11.md").write_text(
        """## X · Social（1条）
### [Tweet Update](https://x.com/example/status/123)
- 发布时间：2026-06-11 09:00:00
""",
        encoding="utf-8",
    )
    db_path = tmp_path / "news_index.sqlite3"
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()
    assert seed_news(client, json={}).status_code == 200

    item = client.get("/api/news?per=20").get_json()["items"][0]
    ts = database.now_ts()
    with app_module.runtime.db.connect() as conn:
        conn.execute("UPDATE items SET source=?, source_name=?, source_type=? WHERE id=?", ("Twitter", "Twitter", "twitter", item["id"]))
        conn.execute(
            """
            INSERT INTO article_details(url, source, title, author, published_at, content, content_length, raw_json, fetched_at, updated_at)
            VALUES (?, 'Twitter/X', 'Tweet Update', 'alice', '2026-06-11 09:00:00', ?, ?, '{}', ?, ?)
            """,
            (item["url"], "【主推文】\n主推文内容", len("【主推文】\n主推文内容"), ts, ts),
        )
        conn.execute(
            """
            INSERT INTO ai_jobs(url, status, attempts, queued_at, updated_at)
            VALUES (?, 'pending', 0, ?, ?)
            """,
            (item["url"], ts, ts),
        )
        conn.commit()

    def fake_generate_body_translation_only(**kwargs):
        return {
            "model": "deepseek-chat",
            "key_points_zh": [],
            "conclusion_zh": "",
            "body_zh": "这是推文中文翻译。",
            "raw_json": "{}",
        }

    monkeypatch.setattr(details, 'generate_body_translation_only', fake_generate_body_translation_only)
    assert app_module.runtime.details.process_pending_ai_once() is True

    detail = client.get(f"/api/news/{item['id']}/detail").get_json()
    assert detail["ai_status"] == "success"
    assert detail["ai"]["body_zh"] == "这是推文中文翻译。"
    assert detail["ai"]["key_points_zh"] == "[]"
    assert detail["ai"]["conclusion_zh"] == ""


def test_process_pending_jobs_once_twitter_success_does_not_enqueue_ai_job(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-06-11.md").write_text(
        """## X · Social（1条）
### [Tweet Update](https://x.com/example/status/123)
- 发布时间：2026-06-11 09:00:00
""",
        encoding="utf-8",
    )
    db_path = tmp_path / "news_index.sqlite3"
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()
    assert seed_news(client, json={}).status_code == 200

    item = client.get("/api/news?per=20").get_json()["items"][0]
    with app_module.runtime.db.connect() as conn:
        conn.execute("UPDATE items SET source=?, source_name=?, source_type=? WHERE id=?", ("Twitter", "Twitter", "twitter", item["id"]))
        details.enqueue_detail_job(conn, item["id"], item["url"], "Twitter")
        conn.commit()

    def fake_twitter_detail(url):
        return (
            True,
            {
                "source": "Twitter/X",
                "title": "Tweet Update",
                "author": "alice",
                "published_at": "2026-06-11 09:00:00",
                "content": "【主推文】\n主推文内容\n\n【评论区观点】\n未获取到评论；opencli thread 本次返回 0 条评论，可能是该推文无可见评论、登录态/权限限制或 X 分页未返回。",
                "content_length": 120,
                "raw_json": json.dumps({"tweet": {"text": "主推文内容"}, "comments": [], "comment_count": 0}, ensure_ascii=False),
            },
            "",
        )

    monkeypatch.setattr(app_module.runtime.fetcher, 'run_opencli_twitter_detail', fake_twitter_detail)
    assert app_module.runtime.details.process_pending_jobs_once() is True

    detail = client.get(f"/api/news/{item['id']}/detail").get_json()
    assert detail["detail_status"] == "success"
    assert detail["ai_status"] == "none"


def test_detail_api_returns_twitter_media_images_and_hides_raw_json(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    url = "https://x.com/example/status/123"
    (daily_dir / "dailyFreshNews_2026-06-11.md").write_text(
        f"""## X · Social（1条）
### [Tweet Update]({url})
- 发布时间：2026-06-11 09:00:00
""",
        encoding="utf-8",
    )
    db_path = tmp_path / "news_index.sqlite3"
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()
    assert seed_news(client, json={}).status_code == 200

    item = client.get("/api/news?per=20").get_json()["items"][0]
    ts = database.now_ts()
    raw = json.dumps(
        {"media_images": [{"url": "https://pbs.twimg.com/media/a.jpg", "source": "tweet"}]},
        ensure_ascii=False,
    )
    conn = app_module.runtime.db.connect()
    try:
        conn.execute(
            "UPDATE items SET source=?, source_name=?, source_type=? WHERE id=?",
            ("Twitter", "Twitter", "twitter", item["id"]),
        )
        conn.execute(
            """
            INSERT INTO article_details(url, source, title, author, published_at, content, content_length, raw_json, fetched_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (url, "Twitter/X", "Tweet Update", "alice", "2026-06-11 09:00:00", "content", len("content"), raw, ts, ts),
        )
        conn.commit()
    finally:
        conn.close()

    detail = client.get(f"/api/news/{item['id']}/detail").get_json()
    assert detail["ok"] is True
    assert detail["detail"]["media_images"] == [{"url": "https://pbs.twimg.com/media/a.jpg", "source": "tweet"}]
    assert "raw_json" not in detail["detail"]


def test_detail_api_non_twitter_has_no_media_images(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年5月"
    daily_dir.mkdir(parents=True)
    url = "https://www.reuters.com/world/example"
    (daily_dir / "dailyFreshNews_2026-05-25.md").write_text(
        f"""## Reuters · World（1条）
### [Item 1]({url})
- 发布时间：2026-05-25 12:00:00
""",
        encoding="utf-8",
    )
    db_path = tmp_path / "news_index.sqlite3"
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()
    assert seed_news(client, json={}).status_code == 200

    item = client.get("/api/news?per=20").get_json()["items"][0]
    ts = database.now_ts()
    conn = app_module.runtime.db.connect()
    try:
        conn.execute(
            """
            INSERT INTO article_details(url, source, title, author, published_at, content, content_length, raw_json, fetched_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (url, "Reuters", "T", "A", "2026-05-25", "English body " * 30, len("English body " * 30), "{}", ts, ts),
        )
        conn.commit()
    finally:
        conn.close()

    detail = client.get(f"/api/news/{item['id']}/detail").get_json()
    assert detail["ok"] is True
    assert detail["detail"].get("media_images") in (None, [])


def test_detail_retry_twitter_with_detail_requeues_detail_job(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    url = "https://x.com/example/status/123"
    (daily_dir / "dailyFreshNews_2026-06-11.md").write_text(
        f"""## X · Social（1条）
### [Tweet Update]({url})
- 发布时间：2026-06-11 09:00:00
""",
        encoding="utf-8",
    )
    db_path = tmp_path / "news_index.sqlite3"
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()
    assert seed_news(client, json={}).status_code == 200

    item = client.get("/api/news?per=20").get_json()["items"][0]
    ts = database.now_ts()
    conn = app_module.runtime.db.connect()
    try:
        conn.execute(
            "UPDATE items SET source=?, source_name=?, source_type=? WHERE id=?",
            ("Twitter", "Twitter", "twitter", item["id"]),
        )
        conn.execute(
            """
            INSERT INTO article_details(url, source, title, author, published_at, content, content_length, raw_json, fetched_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (url, "Twitter/X", "Tweet Update", "alice", "2026-06-11 09:00:00", "content", len("content"), "{}", ts, ts),
        )
        conn.commit()
    finally:
        conn.close()

    retry = client.post(f"/api/news/{item['id']}/detail/retry")
    assert retry.status_code == 200
    assert retry.get_json()["ok"] is True

    conn = app_module.runtime.db.connect()
    try:
        job = conn.execute("SELECT status FROM detail_jobs WHERE url=?", (url,)).fetchone()
        assert job is not None
        assert job["status"] == "pending"
    finally:
        conn.close()


def test_detail_retry_twitter_with_detail_mode_ai_requeues_ai_job(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    url = "https://x.com/example/status/123"
    (daily_dir / "dailyFreshNews_2026-06-11.md").write_text(
        f"""## X · Social（1条）
### [Tweet Update]({url})
- 发布时间：2026-06-11 09:00:00
""",
        encoding="utf-8",
    )
    db_path = tmp_path / "news_index.sqlite3"
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()
    assert seed_news(client, json={}).status_code == 200

    item = client.get("/api/news?per=20").get_json()["items"][0]
    ts = database.now_ts()
    conn = app_module.runtime.db.connect()
    try:
        conn.execute(
            "UPDATE items SET source=?, source_name=?, source_type=? WHERE id=?",
            ("Twitter", "Twitter", "twitter", item["id"]),
        )
        conn.execute(
            """
            INSERT INTO article_details(url, source, title, author, published_at, content, content_length, raw_json, fetched_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (url, "Twitter/X", "Tweet Update", "alice", "2026-06-11 09:00:00", "content", len("content"), "{}", ts, ts),
        )
        conn.commit()
    finally:
        conn.close()

    called = {}

    def fake_generate_body_translation_only(**kwargs):
        called.update(kwargs)
        return {
            "model": "deepseek-v4-flash",
            "key_points_zh": [],
            "conclusion_zh": "",
            "body_zh": "重试后的推文中文翻译。",
            "raw_json": "{}",
        }

    monkeypatch.setattr(details, 'generate_body_translation_only', fake_generate_body_translation_only)
    retry = client.post(f"/api/news/{item['id']}/detail/retry", json={"mode": "ai"})
    assert retry.status_code == 200
    assert retry.get_json()["ok"] is True

    conn = app_module.runtime.db.connect()
    try:
        job = conn.execute("SELECT status FROM ai_jobs WHERE url=?", (url,)).fetchone()
        assert job is not None
        assert job["status"] == "pending"
    finally:
        conn.close()

    assert app_module.runtime.details.process_pending_ai_once() is True
    assert called["title"] == "Tweet Update"
    assert called["source"] == "Twitter/X"
    assert called["content"] == "content"
    assert called["model"] == ""


def test_detail_retry_non_twitter_with_detail_still_requeues_ai_job(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年5月"
    daily_dir.mkdir(parents=True)
    url = "https://www.reuters.com/world/example"
    (daily_dir / "dailyFreshNews_2026-05-25.md").write_text(
        f"""## Reuters · World（1条）
### [Item 1]({url})
- 发布时间：2026-05-25 12:00:00
""",
        encoding="utf-8",
    )
    db_path = tmp_path / "news_index.sqlite3"
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()
    assert seed_news(client, json={}).status_code == 200

    item = client.get("/api/news?per=20").get_json()["items"][0]
    ts = database.now_ts()
    conn = app_module.runtime.db.connect()
    try:
        conn.execute(
            """
            INSERT INTO article_details(url, source, title, author, published_at, content, content_length, raw_json, fetched_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (url, "Reuters", "T", "A", "2026-05-25", "English body " * 30, len("English body " * 30), "{}", ts, ts),
        )
        conn.commit()
    finally:
        conn.close()

    called = {}

    def fake_generate_article_ai(**kwargs):
        called.update(kwargs)
        return {
            "model": "deepseek-v4-flash",
            "key_points_zh": ["重试要点一", "重试要点二", "重试要点三"],
            "conclusion_zh": "重试结论。",
            "body_zh": "重试后的普通新闻中文翻译。",
            "raw_json": "{}",
        }

    monkeypatch.setattr(details, 'generate_article_ai', fake_generate_article_ai)
    retry = client.post(f"/api/news/{item['id']}/detail/retry")
    assert retry.status_code == 200
    assert retry.get_json()["ok"] is True

    conn = app_module.runtime.db.connect()
    try:
        job = conn.execute("SELECT status FROM ai_jobs WHERE url=?", (url,)).fetchone()
        assert job is not None
        assert job["status"] == "pending"
    finally:
        conn.close()

    assert app_module.runtime.details.process_pending_ai_once() is True
    assert called["title"] == "T"
    assert called["source"] == "Reuters"
    assert called["content"] == ("English body " * 30).strip()
    assert called["model"] == ""


def test_detail_api_sanitizes_twitter_media_images_and_hides_raw_json(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    url = "https://x.com/example/status/123"
    (daily_dir / "dailyFreshNews_2026-06-11.md").write_text(
        f"""## X · Social（1条）
### [Tweet Update]({url})
- 发布时间：2026-06-11 09:00:00
""",
        encoding="utf-8",
    )
    db_path = tmp_path / "news_index.sqlite3"
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()
    assert seed_news(client, json={}).status_code == 200

    item = client.get("/api/news?per=20").get_json()["items"][0]
    ts = database.now_ts()
    raw = json.dumps(
        {
            "media_images": [
                {"url": "https://pbs.twimg.com/media/valid.jpg", "source": "tweet"},
                {"url": "https://example.com/image.jpg", "source": "tweet"},
                {"url": "https://video.twimg.com/ext_tw_video/x.mp4", "source": "tweet"},
                {"url": "https://pbs.twimg.com/media/valid2.png", "source": "quoted_tweet"},
                {"url": "https://pbs.twimg.com/media/valid.jpg", "source": "tweet"},
                {"url": "https://pbs.twimg.com/media/other.jpg", "source": "comments"},
            ]
        },
        ensure_ascii=False,
    )
    conn = app_module.runtime.db.connect()
    try:
        conn.execute(
            "UPDATE items SET source=?, source_name=?, source_type=? WHERE id=?",
            ("Twitter", "Twitter", "twitter", item["id"]),
        )
        conn.execute(
            """
            INSERT INTO article_details(url, source, title, author, published_at, content, content_length, raw_json, fetched_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (url, "Twitter/X", "Tweet Update", "alice", "2026-06-11 09:00:00", "content", len("content"), raw, ts, ts),
        )
        conn.commit()
    finally:
        conn.close()

    detail = client.get(f"/api/news/{item['id']}/detail").get_json()
    assert detail["ok"] is True
    assert detail["detail"]["media_images"] == [
        {"url": "https://pbs.twimg.com/media/valid.jpg", "source": "tweet"},
        {"url": "https://pbs.twimg.com/media/valid2.png", "source": "quoted_tweet"},
    ]
    assert "raw_json" not in detail["detail"]


def test_detail_retry_twitter_resets_detail_job_attempts_and_timestamps(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    url = "https://x.com/example/status/123"
    (daily_dir / "dailyFreshNews_2026-06-11.md").write_text(
        f"""## X · Social（1条）
### [Tweet Update]({url})
- 发布时间：2026-06-11 09:00:00
""",
        encoding="utf-8",
    )
    db_path = tmp_path / "news_index.sqlite3"
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()
    assert seed_news(client, json={}).status_code == 200

    item = client.get("/api/news?per=20").get_json()["items"][0]
    ts = database.now_ts()
    conn = app_module.runtime.db.connect()
    try:
        conn.execute(
            "UPDATE items SET source=?, source_name=?, source_type=? WHERE id=?",
            ("Twitter", "Twitter", "twitter", item["id"]),
        )
        conn.execute(
            """
            INSERT INTO article_details(url, source, title, author, published_at, content, content_length, raw_json, fetched_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (url, "Twitter/X", "Tweet Update", "alice", "2026-06-11 09:00:00", "content", len("content"), "{}", ts, ts),
        )
        conn.execute(
            """
            INSERT INTO detail_jobs(url, item_id, source, status, attempts, last_error, queued_at, started_at, finished_at, updated_at)
            VALUES (?, ?, ?, 'failed', 2, 'old error', ?, ?, ?, ?)
            """,
            (url, item["id"], "Twitter", ts, ts, ts, ts),
        )
        conn.commit()
    finally:
        conn.close()

    retry = client.post(f"/api/news/{item['id']}/detail/retry")
    assert retry.status_code == 200

    conn = app_module.runtime.db.connect()
    try:
        job = conn.execute("SELECT status, attempts, last_error, started_at, finished_at FROM detail_jobs WHERE url=?", (url,)).fetchone()
        assert job["status"] == "pending"
        assert job["attempts"] == 0
        assert job["last_error"] is None
        assert job["started_at"] is None
        assert job["finished_at"] is None
    finally:
        conn.close()


def test_detail_api_returns_cached_urls_for_twitter_images(tmp_path: Path, monkeypatch):
    media_dir = tmp_path / "media-cache"
    monkeypatch.setenv("NEWS_READER_MEDIA_CACHE_DIR", str(media_dir))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(tmp_path / "news_index.sqlite3"))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()

    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    url = "https://x.com/example/status/123"
    (daily_dir / "dailyFreshNews_2026-06-11.md").write_text(
        f"""## X · Social（1条）
### [Tweet Update]({url})
- 发布时间：2026-06-11 09:00:00
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()
    assert seed_news(client, json={}).status_code == 200

    item = client.get("/api/news?per=20").get_json()["items"][0]
    ts = database.now_ts()
    cache_key = article_fetch._media_cache_key_for_url("https://pbs.twimg.com/media/a.jpg")
    raw = json.dumps(
        {
            "media_images": [
                {"url": "https://pbs.twimg.com/media/a.jpg", "source": "tweet"},
                {"url": "https://video.twimg.com/v.mp4", "source": "tweet"},
                {"url": "https://pbs.twimg.com/media/missing.jpg", "source": "tweet"},
            ]
        },
        ensure_ascii=False,
    )
    conn = app_module.runtime.db.connect()
    try:
        conn.execute(
            "UPDATE items SET source=?, source_name=?, source_type=? WHERE id=?",
            ("Twitter", "Twitter", "twitter", item["id"]),
        )
        conn.execute(
            """
            INSERT INTO article_details(url, source, title, author, published_at, content, content_length, raw_json, fetched_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (url, "Twitter/X", "Tweet Update", "alice", "2026-06-11 09:00:00", "content", len("content"), raw, ts, ts),
        )
        conn.execute(
            """
            INSERT INTO media_cache(url, cache_key, relative_path, mime_type, size_bytes, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("https://pbs.twimg.com/media/a.jpg", cache_key, f"{cache_key}.jpg", "image/jpeg", 3, "success", ts, ts),
        )
        conn.commit()
    finally:
        conn.close()

    detail = client.get(f"/api/news/{item['id']}/detail").get_json()
    assert detail["ok"] is True
    images = detail["detail"]["media_images"]
    assert len(images) == 2  # video dropped, missing not sanitized to valid because no cache
    valid = [img for img in images if img["url"] == "https://pbs.twimg.com/media/a.jpg"][0]
    assert valid["cached_url"] == f"/api/media-cache/{cache_key}"
    assert "raw_json" not in detail["detail"]
