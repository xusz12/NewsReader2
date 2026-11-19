from __future__ import annotations

from nr import configuration, database

from api_fixtures import _isolate_app_settings, _install_fake_oar, _setup_agent_api_fixture
from legacy_news_seed import seed_news, confirm_all_read

import importlib
import json
import os
import pytest
import sqlite3
import subprocess
import sys
import textwrap
import types
from datetime import datetime, timedelta
from pathlib import Path


DAILY_BRIEFING_0629 = """
## 简报信息
- 执行模式：`daily`
- 使用文件：`dailyFreshNews_2026-06-29.md`
- 执行时间：`6月29日 21:03`

## 当前关注

- 特朗普称伊朗已提出会谈请求，明日将于卡塔尔多哈举行——外交窗口开启

## 地缘政治

- **中东**：特朗普称伊朗提出会谈请求，明日多哈会谈
"""


DAILY_BRIEFING_0630 = """
## 简报信息
- 执行模式：daily（今日终版）
- 使用文件：dailyFreshNews_2026-06-30.md
- 执行时间：6月30日 21:02
- 全日采集：197条，10轮运行（14/14源全部成功，0错误）

---

## 📋 6月30日 全日摘要

### 💾 存储/AI芯片（全日主线）
- **韩国芯片风暴**：科技巨头承诺超5500亿美元缓解"内存末日"

### 🔍 关注追踪
【市场】韩国芯片巨投：内存末日应对+李在明8800亿AI豪赌
【科技】内存短缺推动AI基础设施投资，韩国核电建设加速

---

*今日数据：197条，10轮运行（00:01→21:00），14/14源全部成功*
"""


DAILY_BRIEFING_0701 = """
## 简报信息
- 执行模式：daily
- 使用文件：dailyFreshNews_2026-07-01.md
- 执行时间：7月1日 8:03
- 全日累计：102条（含00:01/04:00夜间轮），29条新增（14/14源全部成功）

---

## 📋 7月1日 晨间简报

### 🤖 AI动态
- **美国解除对Anthropic Fable和Mythos AI模型的出口管制**
- **Anthropic推出Claude Science**用于科学研究自动化；同时发布`Claude Sonnet 5`

### 💰 市场/金融
- **耐克业绩超预期**，Q2收官
"""


def test_api_news_and_reindex(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年5月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-05-25.md").write_text(
        """## Reuters · World（1条）
### [API 测试新闻](https://example.com/api)
- 发布时间：2026-05-25 12:00:00
""",
        encoding="utf-8",
    )
    db_path = tmp_path / "news_index.sqlite3"

    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))

    import app as app_module

    importlib.reload(app_module)
    monkeypatch.setattr(app_module, "has_secret", lambda name: False)
    app_module.ensure_db()
    client = app_module.app.test_client()

    r = seed_news(client, json={})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    r2 = client.get("/api/news?page=1&per=20")
    assert r2.status_code == 200
    data = r2.get_json()
    assert data["total"] == 1
    assert data["items"][0]["url"] == "https://example.com/api"
    assert data["items"][0]["date_key"] == "2026-05-25"
    assert isinstance(data["items"][0]["date_label"], str)
    assert data["items"][0]["date_label"]
    item_id = data["items"][0]["id"]
    assert data["items"][0]["read_at"] is None
    assert data["items"][0]["favorite_at"] is None
    assert data["items"][0]["important_at"] is None
    assert data["items"][0]["read_later_at"] is None
    assert data["items"][0].get("read_later_done_at") is None

    r3 = client.patch(f"/api/news/{item_id}/state", json={"read": True})
    assert r3.status_code == 200
    assert r3.get_json()["ok"] is True
    assert r3.get_json()["read_at"] is not None

    r4 = client.get("/api/news?read_filter=read")
    assert r4.status_code == 200
    assert r4.get_json()["total"] == 1

    r5 = client.patch(f"/api/news/{item_id}/state", json={"read": False})
    assert r5.status_code == 400
    assert r5.get_json()["error"] == "read_state_irreversible"

    r6 = client.get("/api/news?read_filter=unread")
    assert r6.status_code == 200
    assert r6.get_json()["total"] == 0

    # Flags remain combinable; queuing acknowledges the headline without
    # completing the saved article.
    r7 = client.patch(
        f"/api/news/{item_id}/state",
        json={"favorite": True, "important": True, "read_later": True},
    )
    assert r7.status_code == 200
    assert r7.get_json()["favorite_at"] is not None
    assert r7.get_json()["important_at"] is not None
    assert r7.get_json()["read_later_at"] is not None
    assert r7.get_json()["read_at"] is not None
    assert r7.get_json()["read_later_done_at"] is None

    favorites = client.get("/api/news?collection=favorites")
    assert favorites.status_code == 200
    assert favorites.get_json()["total"] == 1

    important = client.get("/api/news?collection=important")
    assert important.status_code == 200
    assert important.get_json()["total"] == 1

    read_later = client.get("/api/news?collection=read_later")
    assert read_later.status_code == 200
    assert read_later.get_json()["total"] == 1

    done = client.patch(f"/api/news/{item_id}/state", json={"read_later": False})
    assert done.status_code == 200
    assert done.get_json()["read_later_at"] is None
    assert done.get_json()["read_later_done_at"] is None

    read_later_unread = client.get("/api/news?collection=read_later&read_filter=unread")
    assert read_later_unread.status_code == 200
    assert read_later_unread.get_json()["total"] == 0

    assert client.get("/api/news?collection=read_later&read_filter=read").get_json()["total"] == 0
    client.patch(f"/api/news/{item_id}/state", json={"read_later": False, "read_later_done": True})
    read_later_read = client.get("/api/news?collection=read_later&read_filter=read")
    assert read_later_read.status_code == 200
    assert read_later_read.get_json()["total"] == 1

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO article_details(
                url, source, title, published_at, content, content_length, raw_json, fetched_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "https://example.com/api",
                "API Source",
                "API News",
                "2026-05-25 10:00:00",
                "cached detail",
                len("cached detail"),
                "{}",
                "2026-05-25 10:05:00",
                "2026-05-25 10:05:00",
            ),
        )

    read_later_read = client.get("/api/news?collection=read_later&read_filter=read")
    assert read_later_read.status_code == 200
    assert read_later_read.get_json()["total"] == 1

    read_later_all = client.get("/api/news?collection=read_later&read_filter=all")
    assert read_later_all.status_code == 200
    assert read_later_all.get_json()["total"] == 1

    combo = client.get("/api/news?collection=important&read_filter=unread")
    assert combo.status_code == 200
    assert combo.get_json()["total"] == 0


def test_sidecar_source_identity_survives_section_only_source(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    md_path = daily_dir / "dailyFreshNews_2026-06-30.md"
    sidecar_path = daily_dir / "dailyFreshNews_2026-06-30.newsreader.json"
    md_path.write_text("", encoding="utf-8")
    sidecar_path.write_text(
        json.dumps(
            {
                "schema_version": "newsreader.daily.v1",
                "items": [
                    {
                        "item_order": 1,
                        "section": "world",
                        "source_type": "reuters",
                        "source_name": "Reuters",
                        "title": "Sidecar section source",
                        "summary": "JSON 摘要",
                        "published_at": "2026-06-30 09:30:00",
                        "url": "https://www.reuters.com/world/example-2026-06-30/",
                    }
                ],
            },
            ensure_ascii=False,
        ),
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

    item = client.get("/api/news?read_filter=all&per=20").get_json()["items"][0]
    assert item["source"] == "world"
    assert item["source_type"] == "reuters"
    assert item["source_name"] == "Reuters"
    assert item["source_key"] == "reuters"


def test_apply_schema_adds_favorite_at_and_read_later_done_at_and_migrates_legacy_bookmarked(tmp_path: Path, monkeypatch):
    daily_root = tmp_path / "DailyNews"
    daily_root.mkdir(parents=True)
    db_path = tmp_path / "news_index.sqlite3"

    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(daily_root))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))

    import app as app_module

    importlib.reload(app_module)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE item_state (
              item_id TEXT PRIMARY KEY,
              bookmarked INTEGER DEFAULT 0,
              skipped INTEGER DEFAULT 0,
              read_at TEXT,
              important_at TEXT,
              read_later_at TEXT,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO item_state(item_id, bookmarked, updated_at)
            VALUES ('legacy-1', 1, '2026-06-20 10:00:00')
            """
        )
        conn.commit()
    finally:
        conn.close()

    app_module.ensure_db()
    app_module.ensure_db()

    conn = app_module.db_conn()
    try:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(item_state)").fetchall()}
        assert "favorite_at" in cols
        assert "read_later_done_at" in cols
        migrated = conn.execute(
            "SELECT favorite_at FROM item_state WHERE item_id='legacy-1'"
        ).fetchone()
        assert migrated is not None
        assert migrated["favorite_at"] == "2026-06-20 10:00:00"
    finally:
        conn.close()


def test_read_later_enqueues_detail_job_and_retry(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年5月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-05-25.md").write_text(
        """## Reuters · World（1条）
### [Item 1](https://www.reuters.com/world/example)
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
    assert item["detail_ready"] == 0

    patch = client.patch(f"/api/news/{item_id}/state", json={"read_later": True})
    assert patch.status_code == 200
    assert patch.get_json()["read_later_at"] is not None

    detail = client.get(f"/api/news/{item_id}/detail")
    assert detail.status_code == 200
    payload = detail.get_json()
    assert payload["ok"] is True
    assert payload["detail_status"] in {"pending", "running", "failed", "success"}

    # Plan B: cancel read_later should cancel pending job, but keep detail table untouched.
    cancel = client.patch(f"/api/news/{item_id}/state", json={"read_later": False})
    assert cancel.status_code == 200
    assert cancel.get_json()["read_later_at"] is None
    assert cancel.get_json()["read_later_done_at"] is None

    read_later_done = client.get("/api/news?collection=read_later&read_filter=read").get_json()
    assert read_later_done["total"] == 0

    detail_after_cancel = client.get(f"/api/news/{item_id}/detail")
    assert detail_after_cancel.status_code == 200
    after_payload = detail_after_cancel.get_json()
    assert after_payload["detail_status"] in {"canceled", "success", "failed", "running"}

    retry = client.post(f"/api/news/{item_id}/detail/retry")
    assert retry.status_code == 200
    assert retry.get_json()["ok"] is True


def test_twitter_read_later_enqueues_pending_detail_job(tmp_path: Path, monkeypatch):
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
    with app_module.db_conn() as conn:
        conn.execute(
            "UPDATE items SET source=?, source_name=?, source_type=?, summary=? WHERE id=?",
            ("Twitter", "Twitter", "twitter", "Tweet summary context.", item["id"]),
        )
        conn.commit()

    patch = client.patch(f"/api/news/{item['id']}/state", json={"read_later": True})
    assert patch.status_code == 200
    detail = client.get(f"/api/news/{item['id']}/detail").get_json()
    assert detail["detail_status"] == "pending"


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

    conn = app_module.db_conn()
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


def test_favorite_state_surfaces_in_status_detail_and_sources(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年5月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-05-25.md").write_text(
        """## Reuters · World（1条）
### [Fav Reuters](https://www.reuters.com/world/fav-r)
- 发布时间：2026-05-25 12:00:00
## Bloomberg · Markets（1条）
### [Fav Bloomberg](https://www.bloomberg.com/news/articles/fav-b)
- 发布时间：2026-05-25 13:00:00
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

    items = client.get("/api/news?per=20").get_json()["items"]
    first_id = items[0]["id"]
    second_id = items[1]["id"]

    first_patch = client.patch(f"/api/news/{first_id}/state", json={"favorite": True})
    second_patch = client.patch(f"/api/news/{second_id}/state", json={"favorite": True})
    assert first_patch.status_code == 200
    assert second_patch.status_code == 200
    assert first_patch.get_json()["favorite_at"] is not None

    favorites = client.get("/api/news?collection=favorites&per=20").get_json()
    assert favorites["total"] == 2
    assert all(item["favorite_at"] is not None for item in favorites["items"])

    status = client.get(f"/api/news/status?ids={first_id},{second_id}")
    assert status.status_code == 200
    status_items = {item["id"]: item for item in status.get_json()["items"]}
    assert status_items[first_id]["favorite_at"] is not None
    assert status_items[second_id]["favorite_at"] is not None

    detail = client.get(f"/api/news/{first_id}/detail")
    assert detail.status_code == 200
    assert detail.get_json()["favorite_at"] is not None

    sources = client.get("/api/sources?collection=favorites&read_filter=all")
    assert sources.status_code == 200
    source_keys = {item["key"] for item in sources.get_json()["sources"]}
    assert "reuters" in source_keys
    assert "bloomberg" in source_keys

    cancel = client.patch(f"/api/news/{first_id}/state", json={"favorite": False})
    assert cancel.status_code == 200
    assert cancel.get_json()["favorite_at"] is None

    favorites_after = client.get("/api/news?collection=favorites&per=20").get_json()
    assert favorites_after["total"] == 1
    assert favorites_after["items"][0]["id"] == second_id


def test_bloomberg_video_pages_are_hidden_server_side_without_deleting_history(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年8月"
    daily_dir.mkdir(parents=True)
    normal_rows = []
    for idx in range(11):
        normal_rows.extend(
            [
                f"### [Bloomberg Filter Normal {idx + 1}](https://www.bloomberg.com/news/articles/2026-08-20/normal-{idx + 1})",
                f"- 发布时间：2026-08-20 09:{idx:02d}:00",
            ]
        )
    normal_rows.extend(
        [
            "### [Bloomberg Filter Ordinary Video Topic](https://www.bloomberg.com/news/articles/2026-08-20/meta-removes-a-video)",
            "- 发布时间：2026-08-20 10:00:00",
        ]
    )
    video_urls = [
        "https://www.bloomberg.com/news/videos/2026-08-20/hidden-one-video",
        "https://bloomberg.com/news/videos/2026-08-20/hidden-two-video?leadSource=uverify",
        "https://markets.bloomberg.com/news/videos/2026-08-20/hidden-three-video",
    ]
    video_rows = []
    for idx, url in enumerate(video_urls):
        video_rows.extend(
            [
                f"### [Bloomberg Filter Hidden {idx + 1}]({url})",
                f"- 发布时间：2026-08-20 11:{idx:02d}:00",
            ]
        )
    (daily_dir / "dailyFreshNews_2026-08-20.md").write_text(
        "## Bloomberg · Markets（15条）\n" + "\n".join([*normal_rows, *video_rows]) + "\n",
        encoding="utf-8",
    )

    db_path = tmp_path / "news_index.sqlite3"
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()
    indexed = seed_news(client, json={})
    assert indexed.status_code == 200
    assert indexed.json["upserted"] == 12 and indexed.json["skipped_videos"] == 3

    with app_module.db_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 12
        # Simulate video records imported by older versions. Query filters must
        # still protect every surface without requiring a destructive migration.
        conn.executemany("""INSERT INTO items
            (id,source_file,item_order,published_at,date,title,url,source,created_at,updated_at)
            VALUES (?,'2026年8月/dailyFreshNews_2026-08-20.md',0,'2026-08-20 11:00:00',
                    '2026-08-20','Bloomberg Filter Hidden',?,'Bloomberg','before','before')""",
            [(f"legacy-video-{index}", url) for index, url in enumerate(video_urls)])
        conn.commit()
        rows = conn.execute("SELECT id, url FROM items ORDER BY url").fetchall()
        assert len(rows) == 15
        ids_by_url = {row["url"]: row["id"] for row in rows}
        normal_url = "https://www.bloomberg.com/news/articles/2026-08-20/meta-removes-a-video"
        now = "2026-08-20 12:00:00"
        tagged_urls = [normal_url, *video_urls]
        conn.execute(
            "INSERT OR REPLACE INTO market_tag_definitions(key, display_name, active, sort_order, created_at, updated_at) VALUES (?, ?, 1, 0, ?, ?)",
            ("video-filter-test", "Video Filter Test", now, now),
        )
        conn.executemany(
            "INSERT OR REPLACE INTO article_market_tags(url, tag, direction, created_at, updated_at) VALUES (?, ?, 'bullish', ?, ?)",
            [(url, "video-filter-test", now, now) for url in tagged_urls],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO article_notes(url, note, created_at, updated_at) VALUES (?, ?, ?, ?)",
            [(url, "Bloomberg Filter Note", now, now) for url in tagged_urls],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO item_state(item_id, favorite_at, important_at, updated_at) VALUES (?, ?, ?, ?)",
            [(ids_by_url[url], now, now, now) for url in tagged_urls],
        )
        conn.commit()

    feed_page_1 = client.get("/api/news?q=Bloomberg+Filter&read_filter=all&page=1&per=10").get_json()
    feed_page_2 = client.get("/api/news?q=Bloomberg+Filter&read_filter=all&page=2&per=10").get_json()
    assert feed_page_1["total"] == 12
    assert feed_page_1["pages"] == 2
    assert len(feed_page_1["items"]) == 10
    assert len(feed_page_2["items"]) == 2
    assert all("/news/videos/" not in item["url"] for item in [*feed_page_1["items"], *feed_page_2["items"]])
    assert normal_url in {item["url"] for item in [*feed_page_1["items"], *feed_page_2["items"]]}

    search = client.get("/api/search?q=Bloomberg+Filter&range=all&time=all&page=1&per=20").get_json()
    assert search["total"] == 12
    assert all("/news/videos/" not in item["url"] for item in search["items"])

    for collection in ("favorites", "important", "notes", "market_tags"):
        payload = client.get(f"/api/news?collection={collection}&read_filter=all&per=20").get_json()
        assert payload["total"] == 1
        assert payload["items"][0]["url"] == normal_url

    sources = client.get("/api/sources?collection=feed&read_filter=all").get_json()["sources"]
    bloomberg = next(source for source in sources if source["key"] == "bloomberg")
    assert bloomberg["count"] == 12
    assert client.get("/api/nav-summary").get_json()["summary"]["feed_unread"] == 12

    trend = client.get(
        "/api/market-trends/detail?date=2026-08-20&tag=video-filter-test&direction=bullish"
    ).get_json()
    assert trend["total"] == 1
    assert trend["items"][0]["url"] == normal_url
    workbench = client.get("/api/market-workbench?tag=video-filter-test&per=20").get_json()
    assert workbench["total"] == 1
    assert workbench["items"][0]["url"] == normal_url

    for url in video_urls:
        assert client.get(f"/api/news/{ids_by_url[url]}/detail").status_code == 404
    assert client.get(f"/api/news/{ids_by_url[normal_url]}/detail").status_code == 200

    status = client.get(
        "/api/news/status?ids=" + ",".join([ids_by_url[normal_url], *[ids_by_url[url] for url in video_urls]])
    ).get_json()
    assert [item["id"] for item in status["items"]] == [ids_by_url[normal_url]]

    checkpoint = client.put(
        "/api/reading-checkpoint",
        json={"scope": "feed", "item_id": ids_by_url[video_urls[0]], "url": video_urls[0], "title": "hidden"},
    )
    assert checkpoint.status_code == 404
    visible_checkpoint = client.put(
        "/api/reading-checkpoint",
        json={"scope": "feed", "item_id": ids_by_url[normal_url], "url": normal_url, "title": "visible"},
    )
    assert visible_checkpoint.status_code == 200
    assert client.get("/api/reading-checkpoint?scope=feed").get_json()["checkpoint"]["url"] == normal_url

    with app_module.db_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO reading_checkpoints(scope, item_id, url, title, updated_at)
            VALUES ('feed', ?, ?, 'legacy hidden', '2026-08-20 12:30:00')
            """,
            (ids_by_url[video_urls[0]], video_urls[0]),
        )
        conn.commit()
    assert client.get("/api/reading-checkpoint?scope=feed").get_json()["checkpoint"] is None
    locate = client.get("/api/reading-checkpoint/locate?scope=feed&read_filter=all&per=20").get_json()
    assert locate["found"] is False
    assert locate["reason"] == "no_checkpoint"
    assert "checkpoint" not in locate


def test_news_status_batch_endpoint(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年5月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-05-25.md").write_text(
        """## Reuters · World（2条）
### [A](https://example.com/a)
- 发布时间：2026-05-30 08:00:00
### [B](https://example.com/b)
- 发布时间：2026-05-30 09:00:00
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

    items = client.get("/api/news?per=20").get_json()["items"]
    ids = [x["id"] for x in items]
    bad = client.get("/api/news/status")
    assert bad.status_code == 400
    assert bad.get_json()["error"] == "missing_ids"

    ok = client.get(f"/api/news/status?ids={ids[0]},{ids[1]}")
    assert ok.status_code == 200
    payload = ok.get_json()
    assert payload["ok"] is True
    assert len(payload["items"]) == 2
    returned_ids = {x["id"] for x in payload["items"]}
    assert returned_ids == set(ids)


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
    with app_module.db_conn() as conn:
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
        raise app_module.LLMClientError("INVALID_TOOL_ARGUMENTS_JSON: bad json")

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

    monkeypatch.setattr(app_module, "generate_article_ai", fail_primary)
    monkeypatch.setattr(app_module, "generate_pi_fallback_translation", succeed_fallback)
    assert app_module.process_pending_ai_once() is True

    with app_module.db_conn() as conn:
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
    with app_module.db_conn() as conn:
        conn.execute(
            "UPDATE items SET source=?, source_name=?, source_type=? WHERE id=?",
            ("Twitter", "Twitter", "twitter", item["id"]),
        )
        app_module.enqueue_detail_job(conn, item["id"], item["url"], "Twitter")
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

    monkeypatch.setattr(app_module, "run_opencli_twitter_detail", fake_twitter_detail)
    assert app_module.process_pending_jobs_once() is True

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
    with app_module.db_conn() as conn:
        conn.execute(
            "UPDATE items SET source=?, source_name=?, source_type=? WHERE id=?",
            ("Twitter", "Twitter", "twitter", item["id"]),
        )
        app_module.enqueue_detail_job(conn, item["id"], item["url"], "Twitter")
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

    monkeypatch.setattr(app_module, "run_opencli_twitter_detail", fake_twitter_detail)
    assert app_module.process_pending_jobs_once() is True

    detail = client.get(f"/api/news/{item['id']}/detail").get_json()
    assert detail["detail_status"] == "success"
    assert "评论区观点总结失败" in detail["detail"]["content"]


def test_run_opencli_twitter_detail_parses_list_thread_payload(monkeypatch):
    import app as app_module

    thread_payload = [
        {
            "id": "tweet-1",
            "author": "alice",
            "text": "主推文内容比较完整，足够通过正文长度校验。",
            "quoted_tweet": {"text": "引用内容"},
            "created_at": "2026-06-11 09:00:00",
        },
        {"id": "reply-1", "author": "bob", "text": "评论一"},
        {"id": "reply-2", "author": "carol", "text": "评论二"},
    ]
    article_payload = {"content": "长文内容"}

    def fake_run(cmd, timeout):
        if "thread" in cmd:
            return True, thread_payload, ""
        return True, article_payload, ""

    monkeypatch.setattr(app_module, "_run_opencli_json_command", fake_run)
    ok, detail, error = app_module.run_opencli_twitter_detail("https://x.com/example/status/123")
    assert ok is True
    assert error == ""
    assert "【评论区观点】" in detail["content"]
    assert "评论一" in detail["content"]
    assert "评论二" in detail["content"]


def test_run_opencli_twitter_detail_includes_zero_comment_notice(monkeypatch):
    import app as app_module

    thread_payload = [
        {
            "id": "tweet-1",
            "author": "alice",
            "text": "主推文内容比较完整，足够通过正文长度校验。",
            "created_at": "2026-06-11 09:00:00",
        }
    ]

    def fake_run(cmd, timeout):
        if "thread" in cmd:
            return True, thread_payload, ""
        return False, None, "Article not found"

    monkeypatch.setattr(app_module, "_run_opencli_json_command", fake_run)
    ok, detail, error = app_module.run_opencli_twitter_detail("https://x.com/example/status/123")
    assert ok is True
    assert error == ""
    assert "【评论区观点】" in detail["content"]
    assert "opencli thread 本次返回 0 条评论" in detail["content"]
    payload = json.loads(detail["raw_json"])
    assert payload["comment_count"] == 0


def test_run_opencli_twitter_detail_deduplicates_summary_prefix(monkeypatch):
    import app as app_module

    thread_payload = [
        {
            "id": "tweet-1",
            "author": "alice",
            "text": "主推文内容比较完整，足够通过正文长度校验。",
            "created_at": "2026-06-11 09:00:00",
        },
        *({"id": f"reply-{i}", "author": "bob", "text": f"评论 {i}"} for i in range(6)),
    ]

    def fake_run(cmd, timeout):
        if "thread" in cmd:
            return True, thread_payload, ""
        return False, None, "Article not found"

    def fake_summary(**kwargs):
        count = kwargs["comment_count"]
        return {
            "model": "deepseek-chat",
            "summary_text": f"基于已抓取的 {count} 条评论总结：评论区主要围绕利好与估值分歧展开。",
            "raw_json": "{}",
        }

    monkeypatch.setattr(app_module, "_run_opencli_json_command", fake_run)
    monkeypatch.setattr(app_module, "generate_twitter_comments_summary", fake_summary)
    ok, detail, error = app_module.run_opencli_twitter_detail("https://x.com/example/status/123")
    assert ok is True
    assert error == ""
    assert detail["content"].count("基于已抓取的 6 条评论总结：") == 1


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
    with app_module.db_conn() as conn:
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

    monkeypatch.setattr(app_module, "generate_body_translation_only", fake_generate_body_translation_only)
    assert app_module.process_pending_ai_once() is True

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
    with app_module.db_conn() as conn:
        conn.execute("UPDATE items SET source=?, source_name=?, source_type=? WHERE id=?", ("Twitter", "Twitter", "twitter", item["id"]))
        app_module.enqueue_detail_job(conn, item["id"], item["url"], "Twitter")
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

    monkeypatch.setattr(app_module, "run_opencli_twitter_detail", fake_twitter_detail)
    assert app_module.process_pending_jobs_once() is True

    detail = client.get(f"/api/news/{item['id']}/detail").get_json()
    assert detail["detail_status"] == "success"
    assert detail["ai_status"] == "none"


def test_news_chat_archive_rejects_missing_assistant(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-06-11.md").write_text(
        """## Reuters · World（1条）
### [Chat Archive Missing](https://example.com/chat-archive-missing)
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
    res = client.post(
        f"/api/news/{item['id']}/chat/archive",
        json={"messages": [{"role": "user", "content": "还没回答"}]},
    )
    assert res.status_code == 400
    assert res.get_json()["error"] == "empty_archive_source"


def test_release_notes_parser_scans_all_sections_and_filters_invalid_entries(tmp_path: Path, monkeypatch):
    import app as app_module

    importlib.reload(app_module)
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        """# Changelog

### 待发布 — 内置安全更新
- 这不是一个已发布版本。

### 2026-10-02 — v2.2.6 最新发布
#### feat
- 当前版本正文。
#### improve
- 现有体验改进。

### 2026-10-01 — v2.2.6 重复记录
- 重复版本应只展示一次。

### 2026-09-30 — v2.2.4 空记录

## What's Changed

### 2026-09-29 — v2.2.5 历史版本
- 历史正文。

### 2026-09-27 — v2.2.3 更早历史版本
- 更早历史正文。

## 其他章节
- 不属于版本记录。
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(app_module, "CHANGELOG_PATH", changelog)

    notes = app_module.parse_release_notes()

    assert [note["version"] for note in notes] == ["v2.2.6", "v2.2.5", "v2.2.3"]
    assert notes[0]["date"] == "2026-10-02"
    assert notes[0]["lines"] == ["新增功能", "当前版本正文。", "体验优化", "现有体验改进。"]
    assert notes[1]["lines"] == ["历史正文。"]


def test_release_notes_api_returns_current_version_first(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    db_path = tmp_path / "news_index.sqlite3"
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(tmp_path / "app_settings.json"))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()

    payload = client.get("/api/release-notes").get_json()
    assert payload["ok"] is True
    assert payload["items"]
    first = payload["items"][0]
    manifest = json.loads((Path(app_module.BASE_DIR) / "version.json").read_text(encoding="utf-8"))
    assert first["version"] == manifest["version"]
    assert first["date"]
    assert first["lines"]
    assert "title" in first and "category" in first
    assert first["category"] in {"NEW", "IMPROVE", "FIX"}

def test_feed_source_subkey_visibility_settings_only_filter_feed(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年7月"
    daily_dir.mkdir(parents=True)
    db_path = tmp_path / "news_index.sqlite3"
    settings_path = tmp_path / "app_settings.json"
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(settings_path))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    ts = "2026-07-30 10:00:00"
    rows = [
        ("1", "manual.md", 1, "2026-07-30 09:00:00", "2026-07-30", "09:00", "Reuters · China", "rss", "Reuters", "China visible", "s", "https://www.reuters.com/world/china/visible", ts, ts),
        ("2", "manual.md", 2, "2026-07-30 09:01:00", "2026-07-30", "09:01", "Reuters · Middle East", "rss", "Reuters", "ME hidden", "s", "https://www.reuters.com/world/middle-east/hidden", ts, ts),
        ("3", "manual.md", 3, "2026-07-30 09:02:00", "2026-07-30", "09:02", "MacroMargin", "twitter", "X", "X hidden", "s", "https://x.com/MacroMargin/status/1", ts, ts),
        ("4", "manual.md", 4, "2026-07-30 09:03:00", "2026-07-30", "09:03", "Reuters · Middle East", "rss", "Reuters", "ME important", "s", "https://www.reuters.com/world/middle-east/important", ts, ts),
    ]
    conn = app_module.db_conn()
    try:
        with conn:
            conn.executemany(
                """
                INSERT INTO items(
                  id, source_file, item_order, published_at, date, time, source, source_type,
                  source_name, title, summary, url, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.execute("INSERT INTO item_state(item_id, important_at, updated_at) VALUES (?, ?, ?)", ("4", ts, ts))
            conn.execute("INSERT INTO item_state(item_id, read_later_at, updated_at) VALUES (?, ?, ?)", ("2", ts, ts))
    finally:
        conn.close()

    settings = app_module.default_app_settings()
    settings["feed"]["hidden_source_subkeys"] = ["reuters/middle-east", "x/macromargin"]
    app_module.save_app_settings(settings)
    client = app_module.app.test_client()

    feed = client.get("/api/news?collection=feed&read_filter=all&per=10").get_json()
    assert feed["total"] == 1
    assert [item["id"] for item in feed["items"]] == ["1"]

    unread_feed = client.get("/api/news?collection=feed&read_filter=unread&per=10").get_json()
    assert unread_feed["total"] == 1
    assert unread_feed["date_counts"] == {"2026-07-30": 1}

    sources = client.get("/api/sources?collection=feed&read_filter=unread").get_json()["sources"]
    assert sources == [{"key": "reuters", "label": "Reuters", "count": 1}]

    important = client.get("/api/news?collection=important&read_filter=all&per=10").get_json()
    assert [item["id"] for item in important["items"]] == ["4"]

    read_later = client.get("/api/news?collection=read_later&read_filter=all&per=10").get_json()
    assert [item["id"] for item in read_later["items"]] == ["2"]

    settings_payload = client.get("/api/settings").get_json()
    assert settings_payload["feed"]["hidden_source_subkeys"] == ["reuters/middle-east", "x/macromargin"]
    groups = {group["source_key"]: group for group in settings_payload["feed_source_subkeys"]["groups"]}
    reuters_items = {item["key"]: item for item in groups["reuters"]["items"]}
    x_items = {item["key"]: item for item in groups["x"]["items"]}
    assert reuters_items["reuters/middle-east"]["hidden"] is True
    assert reuters_items["reuters/middle-east"]["unread_count"] == 2
    assert x_items["x/macromargin"]["label"] == "@MacroMargin"
    assert x_items["x/macromargin"]["hidden"] is True

    marked = confirm_all_read(client,
        json={"collection": "feed", "read_filter": "unread", "source_filter": "all"},
    ).get_json()
    assert marked == {"ok": True, "marked": 1}
    conn = app_module.db_conn()
    try:
        states = {
            row["item_id"]: row["read_at"]
            for row in conn.execute("SELECT item_id, read_at FROM item_state WHERE item_id IN ('1','2','3','4')").fetchall()
        }
    finally:
        conn.close()
    assert states["1"]
    assert states["2"] is None
    assert "3" not in states or states["3"] is None
    assert states["4"] is None


def test_feed_source_subkeys_use_collection_source_for_x_and_merge_bloomberg(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年7月"
    daily_dir.mkdir(parents=True)
    db_path = tmp_path / "news_index.sqlite3"
    settings_path = tmp_path / "app_settings.json"
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(settings_path))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    ts = "2026-07-30 10:00:00"
    rows = [
        ("x1", "manual.md", 1, "2026-07-30 09:00:00", "2026-07-30", "09:00", "卡比卡比", "twitter", "卡比卡比", "Kabi retweeted external", "s", "https://x.com/aiandcloud/status/1", ts, ts),
        ("x2", "manual.md", 2, "2026-07-30 09:01:00", "2026-07-30", "09:01", "seekinganythingbutalpha", "twitter", "seekinganythingbutalpha", "Seeking quoted external", "s", "https://x.com/DonMiami3/status/1", ts, ts),
        ("x3", "manual.md", 3, "2026-07-30 09:02:00", "2026-07-30", "09:02", "ChinaMacroFacts", "other", "ChinaMacroFacts", "New handle source", "s", "https://x.com/ChinaMacroFacts/status/1", ts, ts),
        ("b1", "manual.md", 4, "2026-07-30 09:03:00", "2026-07-30", "09:03", "bloomberg_tech", "bloomberg", "Bloomberg", "Bloomberg sidecar tech", "s", "https://www.bloomberg.com/news/articles/1", ts, ts),
        ("b2", "manual.md", 5, "2026-07-30 09:04:00", "2026-07-30", "09:04", "Bloomberg · Tech", None, None, "Bloomberg display tech", "s", "https://www.bloomberg.com/news/articles/2", ts, ts),
        ("b3", "manual.md", 6, "2026-07-30 09:05:00", "2026-07-30", "09:05", "Bloomberg · Economics", None, None, "Bloomberg economics", "s", "https://www.bloomberg.com/news/articles/3", ts, ts),
    ]
    conn = app_module.db_conn()
    try:
        with conn:
            conn.executemany(
                """
                INSERT INTO items(
                  id, source_file, item_order, published_at, date, time, source, source_type,
                  source_name, title, summary, url, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
    finally:
        conn.close()

    settings = app_module.default_app_settings()
    settings["feed"]["hidden_source_subkeys"] = ["x/aiandcloud", "bloomberg/bloomberg_tech"]
    app_module.save_app_settings(settings)
    client = app_module.app.test_client()

    settings_payload = client.get("/api/settings").get_json()
    assert settings_payload["feed"]["hidden_source_subkeys"] == ["x/aiandcloud", "bloomberg/tech"]
    assert settings_payload["feed_source_subkeys"]["hidden_source_subkeys"] == ["bloomberg/tech"]
    groups = {group["source_key"]: group for group in settings_payload["feed_source_subkeys"]["groups"]}
    x_items = {item["key"]: item for item in groups["x"]["items"]}
    assert set(x_items) == {"x/jakevin7", "x/ivanalog_com", "x/chinamacrofacts"}
    assert x_items["x/jakevin7"]["label"] == "卡比卡比 (@jakevin7)"
    assert x_items["x/ivanalog_com"]["label"] == "seekinganythingbutalpha (@ivanalog_com)"
    assert "x/aiandcloud" not in x_items
    assert "x/donmiami3" not in x_items
    bloomberg_items = {item["key"]: item for item in groups["bloomberg"]["items"]}
    assert bloomberg_items["bloomberg/tech"]["count"] == 2
    assert bloomberg_items["bloomberg/tech"]["hidden"] is True
    assert bloomberg_items["bloomberg/economics"]["count"] == 1

    feed = client.get("/api/news?collection=feed&read_filter=all&per=20").get_json()
    assert feed["total"] == 4
    assert {item["id"] for item in feed["items"]} == {"x1", "x2", "x3", "b3"}

    settings["feed"]["hidden_source_subkeys"] = ["x/jakevin7"]
    app_module.save_app_settings(settings)
    feed = client.get("/api/news?collection=feed&read_filter=all&per=20").get_json()
    assert feed["total"] == 5
    assert {item["id"] for item in feed["items"]} == {"x2", "x3", "b1", "b2", "b3"}


def test_feed_source_subkey_settings_roundtrip_normalizes_values(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年7月"
    daily_dir.mkdir(parents=True)
    db_path = tmp_path / "news_index.sqlite3"
    settings_path = tmp_path / "app_settings.json"
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(settings_path))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-test")

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()

    res = client.put(
        "/api/settings",
        json={
            "llm": {
                "translation": {"provider": "deepseek", "model": ""},
                "pi_chat": {"provider": "ollama", "model": "minimax-m3:cloud"},
            },
            "feed": {"hidden_source_subkeys": ["Reuters / Middle East", "x/MacroMargin", "Bloomberg / bloomberg_tech", "bad", "x/MacroMargin"]},
        },
    )
    assert res.status_code == 200
    payload = res.get_json()
    assert payload["feed"]["hidden_source_subkeys"] == ["reuters/middle-east", "x/macromargin", "bloomberg/tech"]
    saved = json.loads(settings_path.read_text(encoding="utf-8"))
    assert saved["feed"]["hidden_source_subkeys"] == ["reuters/middle-east", "x/macromargin", "bloomberg/tech"]


def test_settings_tracked_default_rule_params_roundtrip_and_new_topic_defaults(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    db_path = tmp_path / "news_index.sqlite3"
    settings_path = tmp_path / "app_settings.json"
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(settings_path))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()

    save_res = client.put(
        "/api/settings/tracked-default-rule-params",
        json={
            "default_rule_params": {
                "title_weight": 1.8,
                "note_weight": 1.2,
                "summary_weight": 0.9,
                "content_weight": 0.5,
                "strong_score": 1.4,
                "core_score": 1.1,
                "context_score": 0.8,
                "exclude_penalty": 1.6,
                "threshold": 9,
            }
        },
    )
    assert save_res.status_code == 200
    saved = save_res.get_json()
    assert saved["tracked"]["default_rule_params"]["threshold"] == 9
    assert saved["tracked"]["default_rule_params"]["title_weight"] == 1.8
    saved_file = json.loads(settings_path.read_text(encoding="utf-8"))
    assert saved_file["tracked"]["default_rule_params"]["exclude_penalty"] == 1.6

    created = client.post(
        "/api/tracked-topics",
        json={
            "title": "默认参数测试",
            "core_terms": ["苹果"],
            "context_terms": ["财报"],
            "scope": "important",
            "active": True,
        },
    )
    assert created.status_code == 200
    rules = created.get_json()["topic"]["rules"]
    assert rules["threshold"] == 9
    assert rules["title_weight"] == 1.8
    assert rules["note_weight"] == 1.2
    assert rules["summary_weight"] == 0.9
    assert rules["content_weight"] == 0.5
    assert rules["strong_score"] == 1.4
    assert rules["core_score"] == 1.1
    assert rules["context_score"] == 0.8
    assert rules["exclude_penalty"] == 1.6

    reset_res = client.put(
        "/api/settings/tracked-default-rule-params",
        json={"default_rule_params": {"threshold": 6}},
    )
    assert reset_res.status_code == 200
    assert reset_res.get_json()["tracked"]["default_rule_params"]["threshold"] == 6


def test_settings_translation_resolved_default_model_respects_env(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    db_path = tmp_path / "news_index.sqlite3"
    settings_path = tmp_path / "app_settings.json"
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(settings_path))
    monkeypatch.setenv("NEWS_READER_LLM_MODEL", "deepseek-v4-flash")

    import app as app_module

    importlib.reload(app_module)
    monkeypatch.setattr(app_module, "has_secret", lambda name: False)
    monkeypatch.setattr(app_module.shutil, "which", lambda name: None)
    app_module.ensure_db()
    client = app_module.app.test_client()

    payload = client.get("/api/settings").get_json()
    assert payload["model_catalogs"]["translation"]["resolved_default_model"] == "deepseek-v4-flash"
    assert payload["model_catalogs"]["translation"]["default_label"] == "deepseek-v4-flash"
    assert payload["llm"]["translation"]["model"] == ""


def test_deepseek_model_catalog_fallback_keeps_saved_model(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    db_path = tmp_path / "news_index.sqlite3"
    settings_path = tmp_path / "app_settings.json"
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(settings_path))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-test")

    import app as app_module

    importlib.reload(app_module)
    monkeypatch.setattr(app_module, "urlopen", lambda request, timeout=0: (_ for _ in ()).throw(app_module.HTTPError(app_module.DEEPSEEK_MODELS_URL, 503, "boom", None, None)))

    snapshot = app_module.deepseek_settings_snapshot("deepseek-custom-x")
    assert snapshot["service"]["configured"] is True
    assert snapshot["service"]["models_endpoint_reachable"] is False
    assert snapshot["service"]["used_fallback"] is True
    assert snapshot["service"]["last_error"] == "http_503"
    assert snapshot["catalog"]["source"] == "fallback"
    assert snapshot["catalog"]["options"][0]["value"] == "deepseek-v4-flash"
    assert snapshot["catalog"]["options"][-1]["value"] == "deepseek-custom-x"
    assert snapshot["catalog"]["options"][-1]["source"] == "saved"


def test_settings_default_pi_and_ignore_unknown_executor_fields(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews"
    daily_dir.mkdir(parents=True)
    db_path = tmp_path / "news_index.sqlite3"
    settings_path = tmp_path / "app_settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "llm": {
                    "translation": {"provider": "deepseek", "model": "deepseek-saved"},
                    "legacy_chat": {"provider": "unused"},
                    "second_executor": {"model": "unused"},
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(daily_dir))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(settings_path))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    import app as app_module

    importlib.reload(app_module)
    monkeypatch.setattr(app_module, "has_secret", lambda name: False)
    monkeypatch.setattr(app_module.shutil, "which", lambda name: None)
    app_module.ensure_db()
    client = app_module.app.test_client()

    initial = client.get("/api/settings").get_json()
    assert set(initial["llm"]) == {"translation", "pi_chat"}
    assert set(initial["api_status"]) == {"deepseek", "jev", "openai-decisions", "opencode-go", "pi", "codex"}
    assert initial["agent"]["runtime"] == "pi"
    assert set(initial["model_catalogs"]) == {"translation", "jev", "openai-decisions", "opencode-go", "pi_chat", "codex"}
    assert initial["llm"]["translation"]["model"] == "deepseek-saved"

    saved = client.put(
        "/api/settings",
        json={
            "llm": {
                "translation": {"provider": "deepseek", "model": "deepseek-new"},
                "pi_chat": {"provider": "ollama", "model": "qwen3.5:4b"},
                "legacy_chat": {"provider": "ignored"},
                "second_executor": {"model": "ignored"},
            }
        },
    )
    assert saved.status_code == 200
    payload = saved.get_json()
    assert set(payload["llm"]) == {"translation", "pi_chat"}
    persisted = json.loads(settings_path.read_text(encoding="utf-8"))
    assert set(persisted["llm"]) == {"translation", "pi_chat"}
    assert persisted["llm"]["pi_chat"] == {"provider": "ollama", "model": "qwen3.5:4b"}


def test_settings_secret_api_save_and_delete(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    db_path = tmp_path / "news_index.sqlite3"
    settings_path = tmp_path / "app_settings.json"
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(settings_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()

    saved = {}

    monkeypatch.setattr(app_module, "has_secret", lambda name: name in saved)
    monkeypatch.setattr(app_module, "write_secret", lambda name, value: saved.__setitem__(name, value))
    monkeypatch.setattr(app_module, "delete_secret", lambda name: saved.pop(name, None))

    initial = client.get("/api/settings").get_json()
    assert initial["api_status"]["deepseek"]["configured"] is False

    save_res = client.put("/api/settings/secrets/deepseek", json={"key": "sk-test-deepseek"})
    assert save_res.status_code == 200
    saved_payload = save_res.get_json()
    assert saved_payload["api_status"]["deepseek"]["configured"] is True
    assert "DEEPSEEK_API_KEY" in saved
    dumped = json.dumps(saved_payload, ensure_ascii=False)
    assert "sk-test-deepseek" not in dumped
    assert settings_path.exists() is False

    delete_res = client.delete("/api/settings/secrets/deepseek")
    assert delete_res.status_code == 200
    deleted_payload = delete_res.get_json()
    assert deleted_payload["api_status"]["deepseek"]["configured"] is False
    assert "DEEPSEEK_API_KEY" not in saved


def test_settings_secret_api_rejects_invalid_provider_and_empty_key(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    db_path = tmp_path / "news_index.sqlite3"
    settings_path = tmp_path / "app_settings.json"
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(settings_path))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()

    bad_provider = client.put("/api/settings/secrets/unknown", json={"key": "x"})
    assert bad_provider.status_code == 400
    assert bad_provider.get_json()["error"] == "unsupported_provider"

    empty_key = client.put("/api/settings/secrets/deepseek", json={"key": "   "})
    assert empty_key.status_code == 400
    assert empty_key.get_json()["error"] == "empty_key"


def test_settings_secret_api_keychain_failure_does_not_leak_key(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    db_path = tmp_path / "news_index.sqlite3"
    settings_path = tmp_path / "app_settings.json"
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(settings_path))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()

    def raise_failure(name, value):
        raise app_module.SecretStoreError("write_failed")

    monkeypatch.setattr(app_module, "write_secret", raise_failure)

    res = client.put("/api/settings/secrets/deepseek", json={"key": "sk-sensitive-value"})
    assert res.status_code == 500
    payload = res.get_json()
    assert payload["error"] == "write_failed"
    dumped = json.dumps(payload, ensure_ascii=False)
    assert "sk-sensitive-value" not in dumped


def test_parse_pi_providers():
    import app as app_module

    stdout = (
        "provider  model              context  max-out  thinking  images\n"
        "deepseek  deepseek-v4-flash  1M       384K     yes       no\n"
        "deepseek  deepseek-v4-pro    1M       384K     yes       no\n"
        "ollama    minimax-m3:cloud   524.3K   16.4K    yes       yes\n"
        "ollama    qwen3.5:4b         262.1K   16.4K    yes       yes\n"
    )
    assert app_module.parse_pi_providers(stdout) == ["deepseek", "ollama"]
    assert app_module.parse_pi_providers("") == []
    assert app_module.parse_pi_providers("provider  model\n") == []


def test_parse_pi_model_catalog():
    import app as app_module

    stdout = (
        "provider  model              context  max-out  thinking  images\n"
        "deepseek  deepseek-v4-flash  1M       384K     yes       no\n"
        "deepseek  deepseek-v4-pro    1M       384K     yes       no\n"
        "deepseek  deepseek-v4-flash  1M       384K     yes       no\n"
        "\n"
        "ollama    minimax-m3:cloud   524.3K   16.4K    yes       yes\n"
        "ollama    minimax-m3:cloud   524.3K   16.4K    yes       yes\n"
        "broken-row\n"
        "ollama    qwen3.5:4b         262.1K   16.4K    yes       yes\n"
    )
    providers, grouped = app_module.parse_pi_model_catalog(stdout)
    assert providers == ["deepseek", "ollama"]
    assert grouped == {
        "deepseek": ["deepseek-v4-flash", "deepseek-v4-pro"],
        "ollama": ["minimax-m3:cloud", "qwen3.5:4b"],
    }
    # 表头/空行/畸形行（不足两列）/空输出都安全跳过；两列以上的行取前两列。
    assert app_module.parse_pi_model_catalog("provider  model\n") == ([], {})
    assert app_module.parse_pi_model_catalog("") == ([], {})
    assert app_module.parse_pi_model_catalog("onlyprovider\n") == ([], {})
    providers_two, grouped_two = app_module.parse_pi_model_catalog("deepseek  deepseek-v4-pro  1M  384K  yes  no\n")
    assert providers_two == ["deepseek"]
    assert grouped_two == {"deepseek": ["deepseek-v4-pro"]}


def _pi_catalog_fixture(monkeypatch, app_module, rows):
    monkeypatch.setattr(app_module.pi_models, "catalog", lambda: {
        "error": "", "options": [{"provider": provider, "value": model, "label": model,
                                   "reasoning_efforts": ["off", "low", "high"]} for provider, model in rows]})


def test_pi_chat_settings_snapshot_detects_providers(monkeypatch):
    import app as app_module
    _pi_catalog_fixture(monkeypatch, app_module, [("deepseek", "deepseek-v4-flash"), ("ollama", "minimax-m3:cloud")])
    catalog = app_module.pi_chat_settings_snapshot("minimax-m3:cloud", "deepseek")["catalog"]
    assert catalog["saved_provider"] == "deepseek"
    assert catalog["resolved_default_provider"] == catalog["resolved_default_model"] == ""
    assert catalog["source"] == "pi-model-registry"
    assert [option["value"] for option in catalog["provider_options"]] == ["deepseek", "ollama"]
    assert [option["value"] for option in catalog["options"]] == ["deepseek-v4-flash"]
    assert catalog["options"][0]["reasoning_efforts"] == ["off", "low", "high"]
    assert catalog["model_options_by_provider"]["ollama"][0]["value"] == "minimax-m3:cloud"


def test_pi_chat_settings_snapshot_error_does_not_reuse_candidates(monkeypatch):
    import app as app_module
    def fail():
        raise RuntimeError("private error")
    monkeypatch.setattr(app_module.pi_models, "catalog", fail)
    catalog = app_module.pi_chat_settings_snapshot("old-model", "custom-provider")["catalog"]
    assert catalog["provider_options"] == catalog["options"] == []
    assert catalog["saved_provider"] == "custom-provider"
    assert catalog["saved_model"] == "old-model"
    assert catalog["error"] and catalog["source"] == "unavailable"
    assert "private" not in catalog["error"]


def test_pi_chat_settings_snapshot_removes_logged_out_provider_even_when_saved(monkeypatch):
    import app as app_module
    _pi_catalog_fixture(monkeypatch, app_module, [("deepseek", "deepseek-v4-pro"), ("ollama", "local")])
    assert len(app_module.pi_chat_settings_snapshot("deepseek-v4-pro", "deepseek")["catalog"]["provider_options"]) == 2
    _pi_catalog_fixture(monkeypatch, app_module, [("ollama", "local")])
    catalog = app_module.pi_chat_settings_snapshot("deepseek-v4-pro", "deepseek")["catalog"]
    assert [option["value"] for option in catalog["provider_options"]] == ["ollama"]
    assert catalog["options"] == [] and "deepseek" not in catalog["model_options_by_provider"]
    _pi_catalog_fixture(monkeypatch, app_module, [])
    empty = app_module.pi_chat_settings_snapshot("deepseek-v4-pro", "deepseek")["catalog"]
    assert empty["provider_options"] == [] and empty["model_options_by_provider"] == {}
    assert empty["error"] == "" and empty["source"] == "pi-model-registry"
    assert empty["saved_provider"] == "deepseek"


def test_run_pi_chat_success(tmp_path: Path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "AGENT_RUNTIME_DIR", tmp_path / "runtime")
    captured = {}
    def fake_run(command, request, env, timeout):
        captured.update(command=command, request=request, env=env)
        return "Pi 回答"
    monkeypatch.setattr(app_module, "run_oar_once", fake_run)
    result = app_module.run_pi_chat(item_id="item-1", question="怎么看？", title="News", source="Reuters",
        published_at="2026-06-11", content="body", context_level="full_detail", pi_provider="ollama", pi_model="minimax-m3:cloud")
    assert result["provider"] == "pi"
    assert result["session_id"].startswith("newsreader-oar-chat-")
    assert result["model"] == "minimax-m3:cloud"
    assert result["answer"] == "Pi 回答"
    assert captured["env"].get("PI_PACKAGE_DIR") is None
    assert captured["request"]["provider"] == "ollama"
    assert captured["request"]["model"] == "minimax-m3:cloud"
    assert "怎么看" not in " ".join(captured["command"])


def test_run_pi_chat_archive_success(tmp_path: Path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "AGENT_RUNTIME_DIR", tmp_path / "runtime")
    calls = []
    def fake_run(command, request, env, timeout):
        calls.append((command, request, env))
        return "归档结论"
    monkeypatch.setattr(app_module, "run_oar_once", fake_run)
    args = dict(title="News", source="Reuters", published_at="2026-06-11",
        messages=[{"role": "user", "content": "总结"}, {"role": "assistant", "content": "结论"}],
        pi_provider="ollama", pi_model="minimax-m3:cloud")
    result = app_module.run_pi_chat_archive(**args)
    app_module.run_pi_chat_archive(**args)
    assert result == {"provider": "pi", "model": "minimax-m3:cloud", "summary": "归档结论",
        "runtime": "oar-pi", "runtime_compatibility": ""}
    assert calls[0][1]["state_dir"] != calls[1][1]["state_dir"]
    assert "newsreader-oar-archive-" in calls[0][1]["state_dir"]
    assert not Path(calls[0][1]["state_dir"]).exists()
    assert calls[0][2].get("PI_PACKAGE_DIR") is None


def test_run_pi_chat_archive_empty_raises(tmp_path: Path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "AGENT_RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(app_module, "run_oar_once", lambda *args: "")
    with pytest.raises(RuntimeError, match="pi_empty_archive"):
        app_module.run_pi_chat_archive(title="News", source="Reuters", published_at="2026-06-11",
            messages=[{"role": "user", "content": "总结"}, {"role": "assistant", "content": "结论"}],
            pi_provider="ollama", pi_model="minimax-m3:cloud")
    assert not list((tmp_path / "runtime").iterdir())


def test_news_chat_dispatches_to_pi_when_configured(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-06-11.md").write_text(
        """## Reuters · World（1条）
### [Pi Dispatch](https://example.com/pi-dispatch)
- 发布时间：2026-06-11 09:00:00
""",
        encoding="utf-8",
    )
    db_path = tmp_path / "news_index.sqlite3"
    settings_path = tmp_path / "app_settings.json"
    settings_path.write_text(
        json.dumps(
            {"llm": {"pi_chat": {"provider": "ollama", "model": "minimax-m3:cloud"}}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(settings_path))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()
    assert seed_news(client, json={}).status_code == 200

    item = client.get("/api/news?per=20").get_json()["items"][0]
    with app_module.db_conn() as conn:
        ts = database.now_ts()
        with conn:
            conn.execute(
                """
                INSERT INTO article_details(
                  url, source, title, author, published_at, content,
                  content_length, raw_json, fetched_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["url"],
                    "Reuters",
                    "Pi Dispatch",
                    "Reporter",
                    "2026-06-11 09:00:00",
                    "Full body for pi dispatch test.",
                    33,
                    "{}",
                    ts,
                    ts,
                ),
            )

    captured = {}

    def fake_run_pi_chat(**kwargs):
        captured.update(kwargs)
        return {
            "provider": "pi",
            "session_id": "pi-sess-1",
            "model": kwargs["pi_model"],
            "answer": "Pi 已回答",
        }

    monkeypatch.setattr(app_module, "run_pi_chat", fake_run_pi_chat)
    res = client.post(f"/api/news/{item['id']}/chat", json={"question": "最新进展？"})
    assert res.status_code == 200
    payload = res.get_json()
    assert payload["ok"] is True
    assert payload["provider"] == "pi"
    assert payload["answer"] == "Pi 已回答"
    assert payload["session_id"] == "pi-sess-1"
    assert captured["item_id"] == item["id"]
    assert captured["pi_provider"] == "ollama"
    assert captured["pi_model"] == "minimax-m3:cloud"


def test_pi_chat_first_and_follow_up_reuse_the_same_session(tmp_path: Path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "AGENT_RUNTIME_DIR", tmp_path / "runtime")
    calls = []
    def fake_run(command, request, env, timeout):
        calls.append((command, request))
        return "回答"
    monkeypatch.setattr(app_module, "run_oar_once", fake_run)
    args = dict(item_id="item-1", title="标题", source="Reuters", published_at="2026-06-11 09:00:00",
        content="English body", context_level="full_detail", pi_provider="ollama", pi_model="minimax-m3:cloud")
    first = app_module.run_pi_chat(question="首问", **args)
    second = app_module.run_pi_chat(question="追问", session_id=first["session_id"], **args)
    assert first["session_id"] == second["session_id"]
    assert calls[0][1]["state_dir"] == calls[1][1]["state_dir"]
    assert "English body" in calls[0][1]["prompt"]
    assert calls[1][1]["resume_prompt"] == "追问"
    assert all("追问" not in " ".join(command) for command, _ in calls)


def test_news_chat_archive_always_uses_pi_and_appends_note(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    db_path = tmp_path / "news_index.sqlite3"
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))

    (daily_dir / "dailyFreshNews_2026-06-11.md").write_text(
        """## Reuters · World（1条）
### [Chat Archive](https://example.com/chat-archive)
- 发布时间：2026-06-11 09:00:00
""",
        encoding="utf-8",
    )
    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()
    assert seed_news(client, json={}).status_code == 200
    item = client.get("/api/news?per=20").get_json()["items"][0]
    captured = {}

    def fake_archive(**kwargs):
        captured.update(kwargs)
        return {"provider": "pi", "model": kwargs["pi_model"], "summary": "归档结论。"}

    monkeypatch.setattr(app_module, "run_pi_chat_archive", fake_archive)
    response = client.post(
        f"/api/news/{item['id']}/chat/archive",
        json={"messages": [{"role": "user", "content": "总结"}, {"role": "assistant", "content": "归档结论。"}]},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["provider"] == "pi"
    assert payload["archive_summary"] == "归档结论。"
    assert captured["pi_provider"] == ""
    assert captured["pi_model"] == ""
    assert "归档结论。" in payload["note"]["note"]


def test_news_chat_pi_timeout_maps_to_provider_timeout(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-06-11.md").write_text(
        """## Reuters · World（1条）
### [Pi Timeout](https://example.com/pi-timeout)
- 发布时间：2026-06-11 09:00:00
""",
        encoding="utf-8",
    )
    db_path = tmp_path / "news_index.sqlite3"
    settings_path = tmp_path / "app_settings.json"
    settings_path.write_text(
        json.dumps(
            {"llm": {"pi_chat": {"provider": "ollama", "model": "minimax-m3:cloud"}}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(settings_path))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()
    assert seed_news(client, json={}).status_code == 200

    item = client.get("/api/news?per=20").get_json()["items"][0]
    with app_module.db_conn() as conn:
        ts = database.now_ts()
        with conn:
            conn.execute(
                """
                INSERT INTO article_details(url, source, title, author, published_at, content,
                  content_length, raw_json, fetched_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (item["url"], "Reuters", "Pi Timeout", "Reporter", "2026-06-11 09:00:00",
                 "body", 4, "{}", ts, ts),
            )

    monkeypatch.setattr(
        app_module, "run_pi_chat", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("pi_timeout"))
    )
    res = client.post(f"/api/news/{item['id']}/chat", json={"question": "？"})
    assert res.status_code == 504
    assert res.get_json()["error"] == "provider_timeout"


def test_twitter_image_url_filtering():
    import app as app_module

    assert app_module._is_twitter_image_url("https://pbs.twimg.com/media/abc.jpg") is True
    assert app_module._is_twitter_image_url("https://pbs.twimg.com/media/abc.png") is True
    assert app_module._is_twitter_image_url("https://pbs.twimg.com/media/abc.webp?format=webp") is True
    assert app_module._is_twitter_image_url("https://example.com/image.jpeg") is False
    assert app_module._is_twitter_image_url("http://pbs.twimg.com/media/abc.jpg") is False
    assert app_module._is_twitter_image_url("https://pbs.twimg.com/profile_images/abc.jpg") is False
    assert app_module._is_twitter_image_url("https://video.twimg.com/ext_tw_video/abc.mp4") is False
    assert app_module._is_twitter_image_url("https://pbs.twimg.com/media/abc.mp4") is False
    assert app_module._is_twitter_image_url("https://pbs.twimg.com/media/abc.m3u8") is False
    assert app_module._is_twitter_image_url("https://pbs.twimg.com/media/abc.jpg/amplify_video/123") is False
    assert app_module._is_twitter_image_url("https://pbs.twimg.com/media/abc.ext_tw_video.jpg") is False


def test_build_twitter_media_images_extracts_deduplicates_and_marks_source():
    import app as app_module

    main = {
        "media_urls": [
            "https://pbs.twimg.com/media/a.jpg",
            "https://video.twimg.com/v.mp4",
            "https://pbs.twimg.com/media/a.jpg",
        ]
    }
    quoted = {"media_urls": ["https://pbs.twimg.com/media/b.webp", "https://pbs.twimg.com/media/a.jpg"]}
    images = app_module._build_twitter_media_images(main, quoted)
    assert [img["url"] for img in images] == [
        "https://pbs.twimg.com/media/a.jpg",
        "https://pbs.twimg.com/media/b.webp",
    ]
    assert images[0]["source"] == "tweet"
    assert images[1]["source"] == "quoted_tweet"


def test_build_twitter_media_images_ignores_media_posters():
    import app as app_module

    main = {
        "media_urls": ["https://pbs.twimg.com/media/poster.jpg"],
        "media_posters": ["https://pbs.twimg.com/media/poster.jpg"],
    }
    # media_posters itself is not used as an image source; only media_urls is read.
    images = app_module._build_twitter_media_images(main, None)
    assert [img["url"] for img in images] == ["https://pbs.twimg.com/media/poster.jpg"]


def test_run_opencli_twitter_detail_includes_media_images(monkeypatch):
    import app as app_module

    thread_payload = [
        {
            "text": "主推文内容比较完整，足够通过正文长度校验。",
            "media_urls": [
                "https://pbs.twimg.com/media/a.jpg",
                "https://video.twimg.com/v.mp4",
            ],
            "quoted_tweet": {
                "text": "引用推文内容",
                "media_urls": ["https://pbs.twimg.com/media/b.webp"],
            },
        }
    ]

    def fake_run(cmd, timeout):
        if "thread" in cmd:
            return True, thread_payload, ""
        return False, None, "Article not found"

    monkeypatch.setattr(app_module, "_run_opencli_json_command", fake_run)
    ok, detail, error = app_module.run_opencli_twitter_detail("https://x.com/example/status/123")
    assert ok is True
    assert error == ""
    payload = json.loads(detail["raw_json"])
    assert payload["media_images"] == [
        {"url": "https://pbs.twimg.com/media/a.jpg", "source": "tweet"},
        {"url": "https://pbs.twimg.com/media/b.webp", "source": "quoted_tweet"},
    ]


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
    conn = app_module.db_conn()
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
    conn = app_module.db_conn()
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
    conn = app_module.db_conn()
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

    conn = app_module.db_conn()
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
    conn = app_module.db_conn()
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

    monkeypatch.setattr(app_module, "generate_body_translation_only", fake_generate_body_translation_only)
    retry = client.post(f"/api/news/{item['id']}/detail/retry", json={"mode": "ai"})
    assert retry.status_code == 200
    assert retry.get_json()["ok"] is True

    conn = app_module.db_conn()
    try:
        job = conn.execute("SELECT status FROM ai_jobs WHERE url=?", (url,)).fetchone()
        assert job is not None
        assert job["status"] == "pending"
    finally:
        conn.close()

    assert app_module.process_pending_ai_once() is True
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
    conn = app_module.db_conn()
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

    monkeypatch.setattr(app_module, "generate_article_ai", fake_generate_article_ai)
    retry = client.post(f"/api/news/{item['id']}/detail/retry")
    assert retry.status_code == 200
    assert retry.get_json()["ok"] is True

    conn = app_module.db_conn()
    try:
        job = conn.execute("SELECT status FROM ai_jobs WHERE url=?", (url,)).fetchone()
        assert job is not None
        assert job["status"] == "pending"
    finally:
        conn.close()

    assert app_module.process_pending_ai_once() is True
    assert called["title"] == "T"
    assert called["source"] == "Reuters"
    assert called["content"] == ("English body " * 30).strip()
    assert called["model"] == ""


def test_sanitize_twitter_media_images_filters_invalid_items():
    import app as app_module

    raw = [
        {"url": "https://pbs.twimg.com/media/valid.jpg", "source": "tweet"},
        {"url": "https://example.com/image.jpg", "source": "tweet"},
        {"url": "https://video.twimg.com/ext_tw_video/x.mp4", "source": "tweet"},
        {"url": "https://pbs.twimg.com/media/valid2.png", "source": "quoted_tweet"},
        {"url": "https://pbs.twimg.com/media/duplicate.jpg", "source": "tweet"},
        {"url": "https://pbs.twimg.com/media/other.jpg", "source": "comments"},
        {"url": "https://pbs.twimg.com/media/amplify_video.jpg", "source": "tweet"},
        "not a dict",
    ]
    result = app_module._sanitize_twitter_media_images(raw)
    assert result == [
        {"url": "https://pbs.twimg.com/media/valid.jpg", "source": "tweet"},
        {"url": "https://pbs.twimg.com/media/valid2.png", "source": "quoted_tweet"},
        {"url": "https://pbs.twimg.com/media/duplicate.jpg", "source": "tweet"},
    ]


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
    conn = app_module.db_conn()
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
    conn = app_module.db_conn()
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

    conn = app_module.db_conn()
    try:
        job = conn.execute("SELECT status, attempts, last_error, started_at, finished_at FROM detail_jobs WHERE url=?", (url,)).fetchone()
        assert job["status"] == "pending"
        assert job["attempts"] == 0
        assert job["last_error"] is None
        assert job["started_at"] is None
        assert job["finished_at"] is None
    finally:
        conn.close()


def test_media_cache_key_is_deterministic_sha256():
    import app as app_module

    key1 = app_module._media_cache_key_for_url("https://pbs.twimg.com/media/a.jpg")
    key2 = app_module._media_cache_key_for_url("https://pbs.twimg.com/media/a.jpg")
    assert isinstance(key1, str) and len(key1) == 64
    assert key1 == key2
    assert app_module._media_cache_key_for_url("https://pbs.twimg.com/media/b.jpg") != key1


def test_extension_for_mime_type():
    import app as app_module

    assert app_module._extension_for_mime_type("image/jpeg") == ".jpg"
    assert app_module._extension_for_mime_type("image/png") == ".png"
    assert app_module._extension_for_mime_type("image/webp") == ".webp"
    assert app_module._extension_for_mime_type("video/mp4") == ""
    assert app_module._extension_for_mime_type(None) == ""


def test_cache_twitter_image_downloads_and_records(tmp_path: Path, monkeypatch):
    media_dir = tmp_path / "media-cache"
    monkeypatch.setenv("NEWS_READER_MEDIA_CACHE_DIR", str(media_dir))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(tmp_path / "news_index.sqlite3"))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()

    def fake_download(url, dest_path):
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(b"fake-image")
        return True, "image/jpeg", len(b"fake-image")

    monkeypatch.setattr(app_module, "_download_media_file", fake_download)

    url = "https://pbs.twimg.com/media/sample.jpg"
    conn = app_module.db_conn()
    try:
        result = app_module._cache_twitter_image(conn, url)
        conn.commit()
    finally:
        conn.close()

    assert result is not None
    assert result["cached_url"].startswith("/api/media-cache/")
    cache_key = result["cache_key"]

    conn = app_module.db_conn()
    try:
        row = conn.execute("SELECT * FROM media_cache WHERE url=?", (url,)).fetchone()
        assert row["status"] == "success"
        assert row["mime_type"] == "image/jpeg"
        assert row["size_bytes"] == len(b"fake-image")
        assert (media_dir / row["relative_path"]).exists()
    finally:
        conn.close()

    # Second call should return existing cache without re-downloading.
    conn = app_module.db_conn()
    try:
        result2 = app_module._cache_twitter_image(conn, url)
    finally:
        conn.close()
    assert result2["cache_key"] == cache_key


def test_cache_twitter_image_failure_records_failed_status(tmp_path: Path, monkeypatch):
    media_dir = tmp_path / "media-cache"
    monkeypatch.setenv("NEWS_READER_MEDIA_CACHE_DIR", str(media_dir))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(tmp_path / "news_index.sqlite3"))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()

    def fake_download(url, dest_path):
        return False, "http_404", 0

    monkeypatch.setattr(app_module, "_download_media_file", fake_download)

    url = "https://pbs.twimg.com/media/missing.jpg"
    conn = app_module.db_conn()
    try:
        result = app_module._cache_twitter_image(conn, url)
        conn.commit()
    finally:
        conn.close()

    assert result is None
    conn = app_module.db_conn()
    try:
        row = conn.execute("SELECT status, last_error FROM media_cache WHERE url=?", (url,)).fetchone()
        assert row["status"] == "failed"
        assert "404" in row["last_error"]
    finally:
        conn.close()


def test_cleanup_media_cache_removes_old_files_and_records(tmp_path: Path, monkeypatch):
    media_dir = tmp_path / "media-cache"
    monkeypatch.setenv("NEWS_READER_MEDIA_CACHE_DIR", str(media_dir))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(tmp_path / "news_index.sqlite3"))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()

    old_file = media_dir / "ab" / "cd" / "old.jpg"
    old_file.parent.mkdir(parents=True, exist_ok=True)
    old_file.write_bytes(b"old")
    new_file = media_dir / "ef" / "gh" / "new.jpg"
    new_file.parent.mkdir(parents=True, exist_ok=True)
    new_file.write_bytes(b"new")

    conn = app_module.db_conn()
    try:
        ts = database.now_ts()
        old_ts = (datetime.now() - timedelta(days=31)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """
            INSERT INTO media_cache(url, cache_key, relative_path, mime_type, size_bytes, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("https://pbs.twimg.com/media/old.jpg", "oldkey", "ab/cd/old.jpg", "image/jpeg", 3, "success", old_ts, old_ts),
        )
        conn.execute(
            """
            INSERT INTO media_cache(url, cache_key, relative_path, mime_type, size_bytes, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("https://pbs.twimg.com/media/new.jpg", "newkey", "ef/gh/new.jpg", "image/jpeg", 3, "success", ts, ts),
        )
        conn.commit()
        app_module._cleanup_media_cache(conn)
        conn.commit()
    finally:
        conn.close()

    conn = app_module.db_conn()
    try:
        rows = conn.execute("SELECT url FROM media_cache").fetchall()
        assert [r["url"] for r in rows] == ["https://pbs.twimg.com/media/new.jpg"]
    finally:
        conn.close()
    assert not old_file.exists()
    assert new_file.exists()


def test_run_opencli_twitter_detail_caches_images(monkeypatch, tmp_path):
    monkeypatch.setenv("NEWS_READER_MEDIA_CACHE_DIR", str(tmp_path / "media-cache"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(tmp_path / "news_index.sqlite3"))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    # Media belongs to an indexed article; a deleted/unowned article must not
    # create orphan cache files when an in-flight download completes.
    with app_module.db_conn() as conn:
        conn.execute("""INSERT INTO items(id,source_file,item_order,published_at,date,title,url,created_at,updated_at)
            VALUES ('media-item','fixture.md',1,'2026-06-11 09:00:00','2026-06-11','Media fixture',
                    'https://x.com/example/status/123','2026-06-11','2026-06-11')""")

    thread_payload = [
        {
            "text": "主推文内容比较完整，足够通过正文长度校验。",
            "media_urls": [
                "https://pbs.twimg.com/media/a.jpg",
                "https://video.twimg.com/v.mp4",
            ],
            "quoted_tweet": {"text": "引用内容", "media_urls": ["https://pbs.twimg.com/media/b.png"]},
        }
    ]

    def fake_run(cmd, timeout):
        if "thread" in cmd:
            return True, thread_payload, ""
        return False, None, "Article not found"

    downloaded: set[str] = set()

    def fake_download(url, dest_path):
        downloaded.add(url)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(b"img")
        return True, "image/jpeg", 3

    monkeypatch.setattr(app_module, "_run_opencli_json_command", fake_run)
    monkeypatch.setattr(app_module, "_download_media_file", fake_download)

    ok, detail, error = app_module.run_opencli_twitter_detail("https://x.com/example/status/123")
    assert ok is True
    payload = json.loads(detail["raw_json"])
    assert len(payload["media_images"]) == 2
    for img in payload["media_images"]:
        assert "cache_key" in img
        assert img["cached_url"].startswith("/api/media-cache/")
    assert "https://video.twimg.com/v.mp4" not in downloaded


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
    cache_key = app_module._media_cache_key_for_url("https://pbs.twimg.com/media/a.jpg")
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
    conn = app_module.db_conn()
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


def test_media_cache_route_serves_cached_file(tmp_path: Path, monkeypatch):
    media_dir = tmp_path / "media-cache"
    monkeypatch.setenv("NEWS_READER_MEDIA_CACHE_DIR", str(media_dir))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(tmp_path / "news_index.sqlite3"))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()

    url = "https://pbs.twimg.com/media/route.jpg"
    cache_key = app_module._media_cache_key_for_url(url)
    relative_path = f"{cache_key[:2]}/{cache_key[2:4]}/{cache_key}.jpg"
    full_path = media_dir / relative_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_bytes(b"cached-data")

    ts = database.now_ts()
    conn = app_module.db_conn()
    try:
        conn.execute(
            """
            INSERT INTO media_cache(url, cache_key, relative_path, mime_type, size_bytes, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (url, cache_key, relative_path, "image/jpeg", len(b"cached-data"), "success", ts, ts),
        )
        conn.commit()
    finally:
        conn.close()

    client = app_module.app.test_client()
    res = client.get(f"/api/media-cache/{cache_key}")
    assert res.status_code == 200
    assert res.data == b"cached-data"
    assert res.mimetype == "image/jpeg"


def test_media_cache_route_rejects_invalid_and_missing_keys(tmp_path: Path, monkeypatch):
    media_dir = tmp_path / "media-cache"
    monkeypatch.setenv("NEWS_READER_MEDIA_CACHE_DIR", str(media_dir))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(tmp_path / "news_index.sqlite3"))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()

    assert client.get("/api/media-cache/not-hex").status_code == 400
    assert client.get("/api/media-cache/" + "0" * 64).status_code == 404


def test_media_cache_route_blocks_traversal_and_sibling_prefix(tmp_path: Path, monkeypatch):
    media_dir = tmp_path / "media-cache"
    evil_sibling = tmp_path / "media-cache-evil"
    evil_sibling.mkdir(parents=True, exist_ok=True)
    evil_file = evil_sibling / "stolen.jpg"
    evil_file.write_bytes(b"evil")

    monkeypatch.setenv("NEWS_READER_MEDIA_CACHE_DIR", str(media_dir))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(tmp_path / "news_index.sqlite3"))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()

    ts = database.now_ts()
    conn = app_module.db_conn()
    try:
        conn.execute(
            """
            INSERT INTO media_cache(url, cache_key, relative_path, mime_type, size_bytes, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("https://pbs.twimg.com/media/evil.jpg", "e" * 64, "../media-cache-evil/stolen.jpg", "image/jpeg", 4, "success", ts, ts),
        )
        conn.commit()
    finally:
        conn.close()

    client = app_module.app.test_client()
    res = client.get(f"/api/media-cache/{'e' * 64}")
    assert res.status_code == 400
    assert evil_file.exists()


def test_cleanup_media_cache_does_not_delete_outside_files(tmp_path: Path, monkeypatch):
    media_dir = tmp_path / "media-cache"
    monkeypatch.setenv("NEWS_READER_MEDIA_CACHE_DIR", str(media_dir))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(tmp_path / "news_index.sqlite3"))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()

    outside_file = tmp_path / "outside.jpg"
    outside_file.write_bytes(b"outside")

    conn = app_module.db_conn()
    try:
        old_ts = (datetime.now() - timedelta(days=31)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """
            INSERT INTO media_cache(url, cache_key, relative_path, mime_type, size_bytes, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("https://pbs.twimg.com/media/outside.jpg", "0" * 64, "../outside.jpg", "image/jpeg", 7, "success", old_ts, old_ts),
        )
        conn.commit()
        app_module._cleanup_media_cache(conn)
        conn.commit()
    finally:
        conn.close()

    assert outside_file.exists()
    conn = app_module.db_conn()
    try:
        row = conn.execute("SELECT url FROM media_cache WHERE cache_key=?", ("0" * 64,)).fetchone()
        assert row is None
    finally:
        conn.close()


def test_cache_twitter_image_rejects_non_whitelisted_url(tmp_path: Path, monkeypatch):
    media_dir = tmp_path / "media-cache"
    monkeypatch.setenv("NEWS_READER_MEDIA_CACHE_DIR", str(media_dir))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(tmp_path / "news_index.sqlite3"))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()

    downloaded: set[str] = set()

    def fake_download(url, dest_path):
        downloaded.add(url)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(b"img")
        return True, "image/jpeg", 3

    monkeypatch.setattr(app_module, "_download_media_file", fake_download)

    conn = app_module.db_conn()
    try:
        assert app_module._cache_twitter_image(conn, "https://example.com/image.jpeg") is None
        assert app_module._cache_twitter_image(conn, "http://pbs.twimg.com/media/a.jpg") is None
        assert app_module._cache_twitter_image(conn, "https://pbs.twimg.com/profile_images/a.jpg") is None
        conn.commit()
    finally:
        conn.close()

    assert not downloaded


def test_run_opencli_twitter_detail_allows_short_tweet_with_media(monkeypatch, tmp_path):
    monkeypatch.setenv("NEWS_READER_MEDIA_CACHE_DIR", str(tmp_path / "media-cache"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(tmp_path / "news_index.sqlite3"))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()

    thread_payload = [
        {
            "text": "看图。",
            "media_urls": ["https://pbs.twimg.com/media/a.jpg"],
        }
    ]

    def fake_run(cmd, timeout):
        if "thread" in cmd:
            return True, thread_payload, ""
        return False, None, "Article not found"

    monkeypatch.setattr(app_module, "_run_opencli_json_command", fake_run)
    def fake_download(url, dest_path):
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(b"img")
        return True, "image/jpeg", 3

    monkeypatch.setattr(app_module, "_download_media_file", fake_download)

    ok, detail, error = app_module.run_opencli_twitter_detail("https://x.com/example/status/123")
    assert ok is True
    assert error == ""
    payload = json.loads(detail["raw_json"])
    assert len(payload["media_images"]) == 1
    assert payload["media_images"][0]["url"] == "https://pbs.twimg.com/media/a.jpg"


def test_run_opencli_twitter_detail_allows_short_tweet_with_quoted_text(monkeypatch, tmp_path):
    monkeypatch.setenv("NEWS_READER_MEDIA_CACHE_DIR", str(tmp_path / "media-cache"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(tmp_path / "news_index.sqlite3"))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()

    thread_payload = [
        {
            "text": "短评。",
            "quoted_tweet": {"text": "这是一条内容足够长的引用推文，用来验证短主推文不会因为正文长度不足而被判定为空线程。"},
        }
    ]

    def fake_run(cmd, timeout):
        if "thread" in cmd:
            return True, thread_payload, ""
        return False, None, "Article not found"

    monkeypatch.setattr(app_module, "_run_opencli_json_command", fake_run)

    ok, detail, error = app_module.run_opencli_twitter_detail("https://x.com/example/status/123")
    assert ok is True
    assert error == ""
    assert "短评" in detail["content"]


def test_run_opencli_twitter_detail_rejects_truly_empty_thread(monkeypatch, tmp_path):
    monkeypatch.setenv("NEWS_READER_MEDIA_CACHE_DIR", str(tmp_path / "media-cache"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(tmp_path / "news_index.sqlite3"))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()

    thread_payload = [{"text": ""}]

    def fake_run(cmd, timeout):
        if "thread" in cmd:
            return True, thread_payload, ""
        return False, None, "Article not found"

    monkeypatch.setattr(app_module, "_run_opencli_json_command", fake_run)

    ok, detail, error = app_module.run_opencli_twitter_detail("https://x.com/example/status/123")
    assert ok is False
    assert error == "EMPTY_TWITTER_THREAD"


def test_run_opencli_twitter_detail_allows_single_character_tweet(monkeypatch, tmp_path):
    monkeypatch.setenv("NEWS_READER_MEDIA_CACHE_DIR", str(tmp_path / "media-cache"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(tmp_path / "news_index.sqlite3"))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()

    thread_payload = [{"text": "早"}]

    def fake_run(cmd, timeout):
        if "thread" in cmd:
            return True, thread_payload, ""
        return False, None, "Article not found"

    monkeypatch.setattr(app_module, "_run_opencli_json_command", fake_run)

    ok, detail, error = app_module.run_opencli_twitter_detail("https://x.com/example/status/123")
    assert ok is True
    assert error == ""
    assert "早" in detail["content"]


def test_normalize_deepseek_model_maps_deprecated_names():
    import app as app_module

    assert configuration.normalize_deepseek_model("deepseek-chat") == "deepseek-v4-flash"
    assert configuration.normalize_deepseek_model("deepseek-reasoner") == "deepseek-v4-pro"
    assert configuration.normalize_deepseek_model("deepseek-v4-flash") == "deepseek-v4-flash"
    assert configuration.normalize_deepseek_model("  DeepSeek-Chat  ") == "deepseek-v4-flash"
    assert configuration.normalize_deepseek_model("") == ""


def test_settings_load_normalizes_deprecated_deepseek_model(tmp_path: Path, monkeypatch):
    settings_path = tmp_path / "app_settings.json"
    settings_path.write_text(
        json.dumps(
            {"llm": {"translation": {"provider": "deepseek", "model": "deepseek-chat"}}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "news_index.sqlite3"
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(settings_path))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))

    import app as app_module

    importlib.reload(app_module)
    settings = configuration.current_runtime_settings()
    assert settings["llm"]["translation"]["model"] == "deepseek-v4-flash"


def test_settings_save_normalizes_deprecated_deepseek_model(tmp_path: Path, monkeypatch):
    settings_path = tmp_path / "app_settings.json"
    settings_path.write_text("{}", encoding="utf-8")
    db_path = tmp_path / "news_index.sqlite3"
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(settings_path))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))

    import app as app_module

    importlib.reload(app_module)
    normalized = app_module.validate_runtime_settings(
        {"llm": {"translation": {"provider": "deepseek", "model": "deepseek-chat"}}}
    )
    assert normalized["llm"]["translation"]["model"] == "deepseek-v4-flash"


# ── 复盘功能测试 (v2.1.0) ──


def test_index_theme_init_script_precedes_stylesheet():
    """Apply stored theme before fetching CSS, with no first-paint flash."""
    source = (Path(__file__).resolve().parents[1] / "static/index.html").read_text()
    assert source.index("document.documentElement.dataset.theme") < source.index('/static/style.css')
    assert source.index('/static/style.css') < source.index('/static/app.js')


def test_agent_session_new_and_clear_are_real_and_idempotent(tmp_path: Path, monkeypatch):
    app_module, client, item, _db_path, agent_db_path = _setup_agent_api_fixture(tmp_path, monkeypatch)

    empty_clear = client.delete(f"/api/news/{item['id']}/agent/session")
    assert empty_clear.status_code == 200
    assert empty_clear.get_json() == {"ok": True, "cleared": True}

    first = client.post(f"/api/news/{item['id']}/agent/session", json={})
    assert first.status_code == 200
    first_id = first.get_json()["session"]["id"]
    old_job = client.post(
        f"/api/news/{item['id']}/agent/jobs",
        json={
            "question": "旧会话问题",
            "quote_text": "旧引用",
            "quote_source": "英文原文引用",
        },
    )
    assert old_job.status_code == 202
    old_job_id = old_job.get_json()["job_id"]
    with app_module.agent_db_conn() as conn:
        with conn:
            conn.execute(
                "UPDATE agent_jobs SET status='failed', error='old_error', finished_at=?, updated_at=? WHERE id=?",
                (database.now_ts(), database.now_ts(), old_job_id),
            )
    old_runtime = app_module.agent_session_runtime_dir(first_id)
    old_runtime.mkdir(parents=True)
    (old_runtime / "stale-session.json").write_text("stale", encoding="utf-8")

    replaced = client.post(
        f"/api/news/{item['id']}/agent/session",
        json={"new_session": True},
    )
    assert replaced.status_code == 200
    second_id = replaced.get_json()["session"]["id"]
    assert second_id != first_id
    with sqlite3.connect(agent_db_path) as conn:
        assert conn.execute("SELECT id, status FROM agent_sessions").fetchall() == [(second_id, "active")]
        assert conn.execute("SELECT 1 FROM agent_sessions WHERE id=?", (first_id,)).fetchone() is None
        assert conn.execute("SELECT 1 FROM agent_jobs WHERE id=?", (old_job_id,)).fetchone() is None
        assert conn.execute("SELECT COUNT(*) FROM agent_jobs WHERE item_id=?", (item["id"],)).fetchone() == (0,)
        assert conn.execute("SELECT COUNT(*) FROM agent_messages WHERE session_id=?", (first_id,)).fetchone() == (0,)
        assert conn.execute("SELECT COUNT(*) FROM agent_messages").fetchone() == (0,)
        assert conn.execute(
            "SELECT status FROM agent_sessions WHERE id=?", (second_id,)
        ).fetchone() == ("active",)
    assert not old_runtime.exists()

    first_clear = client.delete(f"/api/news/{item['id']}/agent/session")
    second_clear = client.delete(f"/api/news/{item['id']}/agent/session")
    assert first_clear.status_code == second_clear.status_code == 200
    assert first_clear.get_json()["cleared"] is True
    assert second_clear.get_json()["cleared"] is True
    with sqlite3.connect(agent_db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM agent_sessions").fetchone() == (0,)
        assert conn.execute("SELECT COUNT(*) FROM agent_jobs").fetchone() == (0,)
        assert conn.execute("SELECT COUNT(*) FROM agent_messages").fetchone() == (0,)


@pytest.mark.parametrize("answer", ["", " \t\n"])
def test_oar_empty_terminal_does_not_promote_streamed_text(tmp_path: Path, monkeypatch, answer):
    app_module, client, item, _db_path, _agent_db_path = _setup_agent_api_fixture(tmp_path, monkeypatch)
    _install_fake_oar(tmp_path, monkeypatch)
    fake = tmp_path / "fake-node"
    fake.write_text(fake.read_text().replace('"answer": ""', '"answer": ' + repr(answer)))
    monkeypatch.setenv("OAR_FAKE_MODE", "empty_terminal")
    submitted = client.post(f"/api/news/{item['id']}/agent/jobs", json={"question": "空终态"})
    assert submitted.status_code == 202
    assert app_module.process_pending_agent_once() is True
    job = app_module.load_agent_payload(item["id"])["jobs"][-1]
    assert job["status"] == "failed"
    assert job["error"] == "empty_answer"
    assert job["answer_text"] == "完成"
    payload = app_module.load_agent_payload(item["id"])
    assert payload["messages"][-1]["status"] == "failed"
    with app_module.agent_db_conn() as conn:
        assert conn.execute("SELECT executor_session_id FROM agent_sessions").fetchone()[0] == ""
    runtime = app_module.agent_session_runtime_dir(app_module._full_pi_executor_session_id(payload["session"]["id"]))
    assert not (runtime / "session.json").exists()
    monkeypatch.setenv("OAR_FAKE_MODE", "success")
    assert client.post(f"/api/news/{item['id']}/agent/jobs/{submitted.get_json()['job_id']}/retry").status_code == 202
    assert app_module.process_pending_agent_once()
    assert app_module.load_agent_payload(item["id"])["jobs"][-1]["status"] == "succeeded"


@pytest.mark.parametrize("window", ["command", "registration", "cleanup"])
def test_oar_stop_during_launch_keeps_session_busy_until_worker_finishes(tmp_path: Path, monkeypatch, window):
    import threading

    app_module, client, item, _db_path, _agent_db_path = _setup_agent_api_fixture(tmp_path, monkeypatch)
    _install_fake_oar(tmp_path, monkeypatch)
    submitted = client.post(f"/api/news/{item['id']}/agent/jobs", json={"question": "启动竞态"})
    assert submitted.status_code == 202
    payload = submitted.get_json()
    entered, release = threading.Event(), threading.Event()
    if window == "command":
        original = app_module._agent_process_command
        def held(*args, **kwargs):
            entered.set()
            assert release.wait(8)
            return original(*args, **kwargs)
        monkeypatch.setattr(app_module, "_agent_process_command", held)
    elif window == "registration":
        original = app_module.subprocess.Popen
        def held(*args, **kwargs):
            proc = original(*args, **kwargs)
            entered.set()
            assert release.wait(8)
            return proc
        monkeypatch.setattr(app_module.subprocess, "Popen", held)
    else:
        original = app_module.cleanup_oar_config
        def held(*args, **kwargs):
            entered.set()
            assert release.wait(8)
            return original(*args, **kwargs)
        monkeypatch.setattr(app_module, "cleanup_oar_config", held)
    worker = threading.Thread(target=app_module.process_pending_agent_once)
    worker.start()
    try:
        assert entered.wait(3)
        stopped = client.post(f"/api/news/{item['id']}/agent/jobs/{payload['job_id']}/stop")
        assert stopped.status_code == 200
        assert stopped.get_json()["job"]["status"] == "stopping"
        assert not stopped.get_json()["job"]["finished_at"]
        assert app_module.load_agent_payload(item["id"])["messages"][-1]["status"] == "stopping"
        assert client.post(f"/api/news/{item['id']}/agent/jobs/{payload['job_id']}/retry").status_code == 409
        blocked = client.post(
            f"/api/news/{item['id']}/agent/jobs",
            json={"question": "旧任务仍在启动", "session_id": payload["session"]["id"]},
        )
        assert blocked.status_code == 409
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    settled = app_module.load_agent_payload(item["id"])["jobs"][-1]
    assert settled["status"] == "stopped" and settled["finished_at"]
    assert app_module.load_agent_payload(item["id"])["messages"][-1]["status"] == "stopped"
    retry = client.post(f"/api/news/{item['id']}/agent/jobs/{payload['job_id']}/retry")
    assert retry.status_code == 202


def test_agent_first_and_follow_up_use_one_full_pi_session(tmp_path: Path, monkeypatch):
    app_module, client, item, _db_path, agent_db_path = _setup_agent_api_fixture(tmp_path, monkeypatch)
    log = _install_fake_oar(tmp_path, monkeypatch)
    first = client.post(f"/api/news/{item['id']}/agent/jobs", json={"question": "首问"})
    assert first.status_code == 202
    session_id = first.get_json()["session"]["id"]
    assert app_module.process_pending_agent_once() is True
    second = client.post(f"/api/news/{item['id']}/agent/jobs", json={"question": "依赖上一轮的追问", "session_id": session_id})
    assert second.status_code == 202
    assert app_module.process_pending_agent_once() is True
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    expected = f"newsreader-oar-pi-{session_id}"
    assert len(calls) == 2
    assert all(call["argv"][call["argv"].index("--session-id") + 1] == expected for call in calls)
    assert calls[0]["state_dir"] == calls[1]["state_dir"]
    assert "complete original article" in calls[0]["effective_prompt"]
    assert "首问" in calls[0]["effective_prompt"]
    assert "complete original article" not in calls[1]["effective_prompt"]
    assert "首问" not in calls[1]["effective_prompt"]
    assert calls[1]["effective_prompt"] == "依赖上一轮的追问"
    # Full fallback history is supplied to the bridge, not sent on a native resume.
    assert "首问" in calls[1]["prompt"]
    assert all("首问" not in " ".join(call["argv"]) for call in calls)
    with sqlite3.connect(agent_db_path) as conn:
        assert conn.execute("SELECT executor_session_id FROM agent_sessions WHERE id=?", (session_id,)).fetchone() == (expected,)


def test_agent_existing_temp_db_is_migrated_before_session_use(tmp_path: Path, monkeypatch):
    agent_db_path = tmp_path / "agent_sessions.sqlite3"
    with sqlite3.connect(agent_db_path) as conn:
        conn.execute(
            """
            CREATE TABLE agent_sessions (
              id TEXT PRIMARY KEY,
              item_id TEXT NOT NULL,
              url TEXT NOT NULL,
              provider TEXT NOT NULL,
              model TEXT NOT NULL DEFAULT '',
              executor_session_id TEXT NOT NULL DEFAULT '',
              context_hash TEXT NOT NULL,
              context_json TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'active',
              created_at TEXT NOT NULL,
              last_activity_at TEXT NOT NULL,
              expires_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
    monkeypatch.setenv("NEWS_READER_AGENT_DB_PATH", str(agent_db_path))
    monkeypatch.setenv("NEWS_READER_AGENT_RUNTIME_DIR", str(tmp_path / "agent-runtime"))
    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_agent_db()
    with sqlite3.connect(agent_db_path) as conn:
        session_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(agent_sessions)").fetchall()
        }
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    assert "executor_provider" in session_columns
    assert {"agent_sessions", "agent_jobs", "agent_messages"}.issubset(tables)


def test_agent_legacy_pi_session_switches_with_one_time_visible_history(tmp_path: Path, monkeypatch):
    app_module, _client, item, _db_path, _agent_db_path = _setup_agent_api_fixture(tmp_path, monkeypatch)
    with app_module.db_conn() as conn:
        detail = conn.execute("SELECT * FROM article_details WHERE url=?", (item["url"],)).fetchone()
    context = app_module.build_agent_context(item, detail)
    legacy_id = "newsreader-agent-legacy-session"
    legacy_runtime = app_module.agent_session_runtime_dir(legacy_id)
    legacy_runtime.mkdir(parents=True)
    (legacy_runtime / "old-session.jsonl").write_text("legacy")
    session = {"id": "session-legacy", "executor_provider": "ollama", "executor_session_id": legacy_id}
    job = {"provider": "pi", "model": "minimax-m3:cloud", "question": "新问题", "quote_text": ""}
    history = [{"role": "user", "content": "旧问题"}, {"role": "assistant", "content": "旧回答"}]
    command, env, runtime_dir = app_module._agent_process_command(job, session, context, history)
    assert command[command.index("--session-id") + 1] == "newsreader-oar-pi-session-legacy"
    request = app_module._agent_oar_request(job, session, context, history, runtime_dir)
    assert "complete original article" in request["prompt"]
    assert "此前会话记录：" in request["prompt"]
    assert "旧问题" in request["prompt"] and "旧回答" in request["prompt"]
    assert request["history_replayed"] is True
    assert "旧问题" not in request["resume_prompt"]
    assert "旧对话保留" in app_module._serialize_agent_session(session)["history_compatibility"]
    # No deletion or mutation of pre-migration native files.
    assert (legacy_runtime / "old-session.jsonl").read_text() == "legacy"


def test_agent_legacy_pi_migration_persists_and_stops_replaying_history(tmp_path: Path, monkeypatch):
    app_module, client, item, _db_path, agent_db_path = _setup_agent_api_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(app_module, "_agent_executor_settings", lambda: dict(
        provider="pi", model="minimax-m3:cloud", executor_provider="ollama", executor_config={}))
    log = _install_fake_oar(tmp_path, monkeypatch)

    first = client.post(f"/api/news/{item['id']}/agent/jobs", json={"question": "旧问题"})
    assert first.status_code == 202
    first_payload = first.get_json()
    session_id = first_payload["session"]["id"]
    legacy_id = "newsreader-agent-legacy-session"
    with sqlite3.connect(agent_db_path) as conn:
        stamp = database.now_ts()
        with conn:
            conn.execute(
                "UPDATE agent_sessions SET executor_session_id=? WHERE id=?",
                (legacy_id, session_id),
            )
            conn.execute(
                "UPDATE agent_jobs SET status='succeeded', answer_text='旧回答', finished_at=?, updated_at=? WHERE id=?",
                (stamp, stamp, first_payload["job_id"]),
            )
            conn.execute(
                "UPDATE agent_messages SET content='旧回答', status='succeeded', updated_at=? WHERE job_id=? AND role='assistant'",
                (stamp, first_payload["job_id"]),
            )

    second = client.post(
        f"/api/news/{item['id']}/agent/jobs",
        json={"question": "新问题", "session_id": session_id},
    )
    assert second.status_code == 202
    assert app_module.process_pending_agent_once() is True

    third = client.post(
        f"/api/news/{item['id']}/agent/jobs",
        json={"question": "再问", "session_id": session_id},
    )
    assert third.status_code == 202
    assert app_module.process_pending_agent_once() is True

    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(calls) == 2
    assert "旧问题" in calls[0]["effective_prompt"] and "旧回答" in calls[0]["effective_prompt"]
    assert "此前会话记录：" not in calls[1]["effective_prompt"]
    assert "旧问题" not in calls[1]["effective_prompt"] and "旧回答" not in calls[1]["effective_prompt"]
    visible = app_module.load_agent_payload(item["id"])
    assert any(message["content"] == "旧回答" for message in visible["messages"])
    assert "旧对话" in visible["session"]["history_compatibility"]
    with sqlite3.connect(agent_db_path) as conn:
        stored = conn.execute(
            "SELECT executor_session_id FROM agent_sessions WHERE id=?", (session_id,)
        ).fetchone()
    assert stored == (f"newsreader-oar-pi-{session_id}",)


def test_agent_ttl_setting_persists_and_ui_exposes_cleanup_controls(tmp_path: Path, monkeypatch):
    app_module, client, _item, _db_path, _agent_db_path = _setup_agent_api_fixture(tmp_path, monkeypatch)
    assert client.get("/api/settings").get_json()["agent"]["session_ttl_hours"] == 72
    saved = client.put(
        "/api/settings",
        json={
            "llm": {"translation": {"provider": "deepseek", "model": ""}},
            "agent": {"session_ttl_hours": 24},
        },
    )
    assert saved.status_code == 200
    assert saved.get_json()["agent"]["session_ttl_hours"] == 24
    assert app_module.load_app_settings()["agent"]["session_ttl_hours"] == 24


def test_database_settings_connect_selected_db_in_fresh_process(tmp_path: Path, monkeypatch):
    """Verify persisted settings and a fresh DB connection; browser coverage lives in tests/ui."""
    project_root = Path(__file__).resolve().parents[1]
    initial_db = tmp_path / "initial.sqlite3"
    selected_db = tmp_path / "selected folder" / "selected.sqlite3"
    selected_db.parent.mkdir()
    for path, label in ((initial_db, "initial"), (selected_db, "selected")):
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE switch_marker (value TEXT)")
            conn.execute("INSERT INTO switch_marker VALUES (?)", (label,))
    settings_path = tmp_path / "app_settings.json"
    settings_path.write_text(json.dumps({"database": {"path": str(initial_db)}}), encoding="utf-8")
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(settings_path))
    monkeypatch.setenv("NEWS_READER_AGENT_DB_PATH", str(tmp_path / "agent.sqlite3"))
    monkeypatch.setenv("NEWS_READER_MEDIA_CACHE_DIR", str(tmp_path / "media-cache"))
    monkeypatch.delenv("NEWS_READER_DB_PATH", raising=False)

    payload = {"llm": {"translation": {"provider": "deepseek", "model": ""}},
               "database": {"path": str(selected_db)}}

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()
    with app_module.db_conn() as conn:
        assert conn.execute("SELECT value FROM switch_marker").fetchone()[0] == "initial"
    saved = client.put("/api/settings", json=payload)
    assert saved.status_code == 200
    assert saved.get_json()["database"]["configured_path"] == str(selected_db.resolve())
    assert json.loads(settings_path.read_text())["database"]["path"] == str(selected_db.resolve())
    # A browser refresh retains the choice but cannot switch a running process.
    refreshed = client.get("/api/settings").get_json()["database"]
    assert refreshed["configured_path"] == str(selected_db.resolve())
    assert refreshed["path"] == str(initial_db.resolve())
    with app_module.db_conn() as conn:
        assert conn.execute("SELECT value FROM switch_marker").fetchone()[0] == "initial"

    fresh_process = subprocess.run(
        [sys.executable, "-c", textwrap.dedent("""
            import json
            from app import DB_PATH, db_conn, database_runtime_snapshot
            with db_conn() as conn:
                marker = conn.execute("SELECT value FROM switch_marker").fetchone()[0]
            print(json.dumps({"path": str(DB_PATH.resolve()), "marker": marker,
                              "database": database_runtime_snapshot()}))
        """)],
        cwd=project_root, env=os.environ.copy(), capture_output=True, text=True, timeout=15, check=True,
    )
    restarted = json.loads(fresh_process.stdout)
    assert restarted["path"] == str(selected_db.resolve())
    assert restarted["marker"] == "selected"
    assert restarted["database"]["path"] == str(selected_db.resolve())
    assert restarted["database"]["configured_path"] == str(selected_db.resolve())


def test_database_settings_roundtrip_and_restart_uses_saved_existing_sqlite(tmp_path: Path, monkeypatch):
    settings_path = tmp_path / "app_settings.json"
    initial_db = tmp_path / "initial.sqlite3"
    selected_db = tmp_path / "selected.sqlite3"
    with sqlite3.connect(selected_db) as conn:
        conn.execute("CREATE TABLE marker (id INTEGER PRIMARY KEY)")
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(settings_path))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(initial_db))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()
    current = client.get("/api/settings").get_json()
    assert current["database"]["path"] == str(initial_db)
    assert current["database"]["environment_override"] is True
    assert current["database"]["editable"] is False

    monkeypatch.delenv("NEWS_READER_DB_PATH", raising=False)
    payload = {key: current[key] for key in ("llm", "feed", "agent")}
    payload["database"] = {"path": str(selected_db)}
    saved = client.put("/api/settings", json=payload)
    assert saved.status_code == 200
    assert saved.get_json()["database"]["configured_path"] == str(selected_db)
    assert saved.get_json()["database"]["path"] == str(initial_db)

    importlib.reload(app_module)
    assert app_module.DB_PATH == selected_db
    assert app_module.MEDIA_CACHE_DIR == selected_db.parent / "media-cache"
    assert app_module.AGENT_DB_PATH == selected_db.parent / "agent_sessions.sqlite3"

    project_root = Path(__file__).resolve().parents[1]
    child_env = os.environ.copy()
    child_env.pop("NEWS_READER_DB_PATH", None)
    child_env["PYTHONPATH"] = os.pathsep.join(
        [str(project_root), child_env.get("PYTHONPATH", "")]
    ).strip(os.pathsep)
    fresh_process = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app import DB_PATH; print(DB_PATH.expanduser().resolve())",
        ],
        cwd=project_root,
        env=child_env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert Path(fresh_process.stdout.strip()) == selected_db.resolve()


def test_default_database_is_created_when_no_database_exists(tmp_path: Path, monkeypatch):
    import settings as settings_module

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    project_db = project_dir / "news_index.sqlite3"
    legacy_db = tmp_path / "legacy-news.sqlite3"
    monkeypatch.setattr(settings_module, "PROJECT_DB_PATH", project_db)
    monkeypatch.setattr(settings_module, "LEGACY_DB_PATH", legacy_db)
    monkeypatch.delenv("NEWS_READER_DB_PATH", raising=False)
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(tmp_path / "settings.json"))

    import app as app_module

    importlib.reload(app_module)
    assert app_module.DB_PATH == project_db
    assert not project_db.exists()
    app_module.ensure_db()
    assert project_db.is_file()
    with sqlite3.connect(project_db) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='items'").fetchone()


def test_database_settings_reject_invalid_existing_file(tmp_path: Path, monkeypatch):
    settings_path = tmp_path / "app_settings.json"
    db_path = tmp_path / "news.sqlite3"
    sqlite3.connect(db_path).close()
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(settings_path))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()
    current = client.get("/api/settings").get_json()
    monkeypatch.delenv("NEWS_READER_DB_PATH", raising=False)
    payload = {key: current[key] for key in ("llm", "feed", "agent")}
    payload["database"] = {"path": str(tmp_path / "missing.sqlite3")}
    response = client.put("/api/settings", json=payload)
    assert response.status_code == 400
    assert response.get_json()["error"] == "database_path_must_exist"

    payload["database"] = {"path": str(tmp_path)}
    response = client.put("/api/settings", json=payload)
    assert response.status_code == 400
    assert response.get_json()["error"] == "database_path_must_exist"

    invalid = tmp_path / "not-sqlite.db"
    invalid.write_text("not a sqlite database", encoding="utf-8")
    payload["database"] = {"path": str(invalid)}
    response = client.put("/api/settings", json=payload)
    assert response.status_code == 400
    assert response.get_json()["error"] == "database_path_not_sqlite"


def test_database_native_picker_returns_validated_path(tmp_path: Path, monkeypatch):
    settings_path = tmp_path / "app_settings.json"
    initial_db = tmp_path / "initial.sqlite3"
    selected_db = tmp_path / "selected.sqlite3"
    with sqlite3.connect(selected_db) as conn:
        conn.execute("CREATE TABLE marker (id INTEGER PRIMARY KEY)")
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(settings_path))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(initial_db))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()
    monkeypatch.delenv("NEWS_READER_DB_PATH", raising=False)
    monkeypatch.setattr(app_module.sys, "platform", "darwin")
    monkeypatch.setattr(app_module.shutil, "which", lambda name: "/usr/bin/osascript")

    def fake_run(command, **kwargs):
        assert command[0] == "osascript"
        assert kwargs["check"] is False
        return types.SimpleNamespace(returncode=0, stdout=f"{selected_db}\n")

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)
    response = client.post("/api/settings/database/pick")
    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "path": str(selected_db.resolve())}


def test_database_native_picker_returns_json_on_unexpected_failure(tmp_path: Path, monkeypatch):
    settings_path = tmp_path / "app_settings.json"
    initial_db = tmp_path / "initial.sqlite3"
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(settings_path))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(initial_db))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()
    monkeypatch.delenv("NEWS_READER_DB_PATH", raising=False)
    monkeypatch.setattr(app_module, "choose_existing_database_path", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    response = client.post("/api/settings/database/pick")
    assert response.status_code == 500
    assert response.content_type.startswith("application/json")
    assert response.get_json() == {"ok": False, "error": "database_picker_failed"}


def test_database_native_picker_is_blocked_by_environment_override(tmp_path: Path, monkeypatch):
    settings_path = tmp_path / "app_settings.json"
    initial_db = tmp_path / "initial.sqlite3"
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(settings_path))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(initial_db))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()
    response = client.post("/api/settings/database/pick")
    assert response.status_code == 400
    assert response.get_json()["error"] == "database_path_controlled_by_environment"


def test_database_settings_reject_unwritable_file(tmp_path: Path, monkeypatch):
    settings_path = tmp_path / "app_settings.json"
    db_path = tmp_path / "news.sqlite3"
    selected_db = tmp_path / "selected.sqlite3"
    sqlite3.connect(db_path).close()
    with sqlite3.connect(selected_db) as conn:
        conn.execute("CREATE TABLE marker (id INTEGER PRIMARY KEY)")
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(settings_path))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()
    current = client.get("/api/settings").get_json()
    monkeypatch.delenv("NEWS_READER_DB_PATH", raising=False)
    payload = {key: current[key] for key in ("llm", "feed", "agent")}
    payload["database"] = {"path": str(selected_db)}
    monkeypatch.setattr(app_module.os, "access", lambda path, mode: False)
    response = client.put("/api/settings", json=payload)
    assert response.status_code == 400
    assert response.get_json()["error"] == "database_path_not_readable"


@pytest.mark.parametrize('mode,expected_error', [
    ('partial', 'oar_protocol_error'), ('malformed', 'oar_protocol_error'),
    ('auth', 'oar_auth_required'), ('duplicate', 'oar_protocol_error'),
    ('exit_failure', 'oar_runtime_failed'),
])
def test_oar_agent_failure_is_persisted_and_retryable(tmp_path, monkeypatch, mode, expected_error):
    app_module, client, item, _db_path, agent_db_path = _setup_agent_api_fixture(tmp_path, monkeypatch)
    _install_fake_oar(tmp_path, monkeypatch)
    monkeypatch.setenv('OAR_FAKE_MODE', mode)
    first = client.post(f"/api/news/{item['id']}/agent/jobs", json={'question':'question'})
    assert first.status_code == 202
    assert client.post(f"/api/news/{item['id']}/agent/jobs", json={'question':'duplicate'}).status_code == 409
    assert app_module.process_pending_agent_once()
    payload = app_module.load_agent_payload(item['id'])
    assert payload['jobs'][-1]['status'] == 'failed'
    assert payload['jobs'][-1]['error'] == expected_error
    with sqlite3.connect(agent_db_path) as conn:
        assert conn.execute('SELECT executor_session_id FROM agent_sessions').fetchone() == ('',)
    monkeypatch.setenv('OAR_FAKE_MODE', 'success')
    retry = client.post(f"/api/news/{item['id']}/agent/jobs/{first.get_json()['job_id']}/retry")
    assert retry.status_code == 202
    assert app_module.process_pending_agent_once()
    assert app_module.load_agent_payload(item['id'])['jobs'][-1]['status'] == 'succeeded'


def test_oar_node_missing_settles_job(tmp_path, monkeypatch):
    app_module, client, item, _db_path, _agent_db_path = _setup_agent_api_fixture(tmp_path, monkeypatch)
    monkeypatch.setenv('NEWS_READER_NODE', '/not/a/node')
    client.post(f"/api/news/{item['id']}/agent/jobs", json={'question':'question'})
    assert app_module.process_pending_agent_once()
    job = app_module.load_agent_payload(item['id'])['jobs'][-1]
    assert job['status'] == 'failed' and job['error'] == 'oar_node_missing'


def test_oar_timeout_and_stop_cleanup(tmp_path, monkeypatch):
    import threading
    import time
    app_module, client, item, _db_path, _agent_db_path = _setup_agent_api_fixture(tmp_path, monkeypatch)
    log = _install_fake_oar(tmp_path, monkeypatch)
    monkeypatch.setenv('OAR_FAKE_MODE', 'hang')
    monkeypatch.setattr(app_module, 'AGENT_JOB_TIMEOUT_SECONDS', .2)
    first = client.post(f"/api/news/{item['id']}/agent/jobs", json={'question':'timeout'})
    assert app_module.process_pending_agent_once()
    payload = app_module.load_agent_payload(item['id'])
    assert payload['jobs'][-1]['status'] == 'failed'
    assert payload['jobs'][-1]['error'] == 'agent_timeout'
    assert not list(app_module.AGENT_RUNTIME_DIR.glob('*/config-*'))
    monkeypatch.setattr(app_module, 'AGENT_JOB_TIMEOUT_SECONDS', 10)
    # The short timeout can expire before the first fake runtime writes its log.
    # Wait for this invocation, not an assumed second line in an existing file.
    previous_count = len(log.read_text().splitlines()) if log.exists() else 0
    second = client.post(f"/api/news/{item['id']}/agent/jobs", json={'question':'stop'})
    worker = threading.Thread(target=app_module.process_pending_agent_once)
    worker.start()
    deadline = time.monotonic() + 3
    while (not log.exists() or len(log.read_text().splitlines()) <= previous_count) and time.monotonic() < deadline:
        time.sleep(.02)
    started = log.exists() and len(log.read_text().splitlines()) > previous_count
    stopped = client.post(f"/api/news/{item['id']}/agent/jobs/{second.get_json()['job_id']}/stop")
    assert stopped.status_code == 200
    worker.join(4)
    assert not worker.is_alive()
    assert started
    assert app_module.load_agent_payload(item['id'])['jobs'][-1]['status'] == 'stopped'
    assert not app_module.AGENT_PROCESSES
    assert not list(app_module.AGENT_RUNTIME_DIR.glob('*/config-*'))


def test_oar_stop_wins_completion_race(tmp_path, monkeypatch):
    app_module, client, item, _db_path, agent_db_path = _setup_agent_api_fixture(tmp_path, monkeypatch)
    _install_fake_oar(tmp_path, monkeypatch)
    original = app_module._update_agent_job_output
    def cancel_after_output(job_id, session_id, output, **kwargs):
        original(job_id, session_id, output, **kwargs)
        with sqlite3.connect(agent_db_path) as conn:
            conn.execute('UPDATE agent_jobs SET cancel_requested=1 WHERE id=?', (job_id,))
    monkeypatch.setattr(app_module, '_update_agent_job_output', cancel_after_output)
    client.post(f"/api/news/{item['id']}/agent/jobs", json={'question':'race'})
    assert app_module.process_pending_agent_once()
    job = app_module.load_agent_payload(item['id'])['jobs'][-1]
    assert job['status'] == 'stopped'


def test_oar_sync_legacy_id_is_imported_not_discarded(tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, 'AGENT_RUNTIME_DIR', tmp_path / 'runtime')
    requests = []
    def fake_run(command, request, env, timeout):
        requests.append(request)
        return 'continued'
    monkeypatch.setattr(app_module, 'run_oar_once', fake_run)
    args = dict(item_id='item', title='fixture', source='fixture', published_at='2026-10-10', content='sample',
                context_level='full_detail', pi_provider='fixture', pi_model='fixture')
    result = app_module.run_pi_chat(question='next', session_id='old-native-id', **args)
    assert requests[0]['legacy_native_id'] == 'old-native-id'
    assert result['session_id'].startswith('newsreader-oar-chat-')
    app_module.run_pi_chat(question='again', session_id=result['session_id'], **args)
    assert 'legacy_native_id' not in requests[1]
    assert requests[0]['state_dir'] == requests[1]['state_dir']
    with pytest.raises(RuntimeError, match='pi_session_invalid'):
        app_module.run_pi_chat(question='unsafe', session_id='../../unsafe', **args)


def test_pi_archive_uses_oar_and_cleans_private_state(tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, 'AGENT_RUNTIME_DIR', tmp_path / 'runtime')
    states = []
    def fake_run(command, request, env, timeout):
        state = Path(request['state_dir'])
        states.append(state)
        (state / 'route.json').write_text(json.dumps({'runtime': 'cli'}))
        return '归档结论'
    monkeypatch.setattr(app_module, 'run_oar_once', fake_run)
    result = app_module.run_pi_chat_archive(title='Fixture', source='Fixture', published_at='2026-10-10',
        messages=[{'role': 'user', 'content': '归档'}, {'role': 'assistant', 'content': '结论'}],
        pi_provider='fixture', pi_model='fixture')
    assert result['runtime'] == 'oar-pi'
    assert result['runtime_compatibility'] == ''
    assert all(not state.exists() for state in states)


def test_legacy_background_session_always_reports_oar(tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, 'AGENT_RUNTIME_DIR', tmp_path / 'runtime')
    session_id = 'fixture-legacy-label'
    state = app_module.agent_session_runtime_dir(app_module._full_pi_executor_session_id(session_id))
    state.mkdir(parents=True)
    (state / 'route.json').write_text(json.dumps({'runtime': 'cli'}))
    result = app_module._serialize_agent_session({'id': session_id, 'executor_session_id': 'legacy-native-fixture',
        'executor_provider': 'fixture', 'context_json': '{}'})
    assert result['runtime'] == 'oar-pi'
    assert result['runtime_compatibility'] == ''
    assert '旧对话' in result['history_compatibility']
    assert 'executor_session_id' not in result and 'context_json' not in result


def test_oar_old_worker_cleanup_does_not_remove_another_invocations_config(tmp_path, monkeypatch):
    app_module, client, item, _, _ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    _install_fake_oar(tmp_path, monkeypatch)
    submitted = client.post(f"/api/news/{item['id']}/agent/jobs", json={"question": "清理归属"}).get_json()
    runtime = app_module.agent_session_runtime_dir(app_module._full_pi_executor_session_id(submitted["session"]["id"]))
    other_config = runtime / ("config-" + "a" * 32 + "-another-worker")
    other_config.mkdir(parents=True)
    (other_config / "auth.json").write_text("another worker credential")
    # Ignore SIGTERM so Python must clean its own token after SIGKILL.
    fake = tmp_path / "fake-node"
    fake.write_text(fake.read_text().replace("signal.signal(signal.SIGTERM, cleanup)",
                                           "signal.signal(signal.SIGTERM, signal.SIG_IGN)"))
    monkeypatch.setenv("OAR_FAKE_MODE", "hang")
    monkeypatch.setattr(app_module, "AGENT_JOB_TIMEOUT_SECONDS", .2)
    assert app_module.process_pending_agent_once()
    assert app_module.load_agent_payload(item["id"])["jobs"][-1]["status"] == "failed"
    assert list(runtime.glob("config-*")) == [other_config]
    assert (other_config / "auth.json").read_text() == "another worker credential"


def test_oar_queued_stop_is_immediate_and_never_runs(tmp_path, monkeypatch):
    app_module, client, item, _, _ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    log = _install_fake_oar(tmp_path, monkeypatch)
    submitted = client.post(f"/api/news/{item['id']}/agent/jobs", json={"question": "排队取消"}).get_json()
    stopped = client.post(f"/api/news/{item['id']}/agent/jobs/{submitted['job_id']}/stop").get_json()["job"]
    assert stopped["status"] == "stopped" and stopped["finished_at"]
    assert app_module.process_pending_agent_once() is False
    assert not log.exists()
    assert client.post(f"/api/news/{item['id']}/agent/jobs/{submitted['job_id']}/retry").status_code == 202
