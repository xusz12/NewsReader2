from __future__ import annotations

import importlib
import json
import sqlite3
from pathlib import Path

from api_fixtures import _isolate_app_settings
from legacy_news_seed import seed_news
from nr import preferences


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
    monkeypatch.setattr(preferences, 'has_secret', lambda name: False)
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

    conn = app_module.runtime.db.connect()
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
    with app_module.runtime.db.connect() as conn:
        conn.execute(
            "UPDATE items SET source=?, source_name=?, source_type=?, summary=? WHERE id=?",
            ("Twitter", "Twitter", "twitter", "Tweet summary context.", item["id"]),
        )
        conn.commit()

    patch = client.patch(f"/api/news/{item['id']}/state", json={"read_later": True})
    assert patch.status_code == 200
    detail = client.get(f"/api/news/{item['id']}/detail").get_json()
    assert detail["detail_status"] == "pending"


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

    with app_module.runtime.db.connect() as conn:
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

    with app_module.runtime.db.connect() as conn:
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


def test_index_theme_init_script_precedes_stylesheet():
    """Apply stored theme before fetching CSS, with no first-paint flash."""
    source = (Path(__file__).resolve().parents[1] / "static/index.html").read_text()
    assert source.index("document.documentElement.dataset.theme") < source.index('/static/style.css')
    assert source.index('/static/style.css') < source.index('/static/app.js')
