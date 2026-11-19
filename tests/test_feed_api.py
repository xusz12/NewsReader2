from __future__ import annotations

from nr import preferences

from nr import database

from legacy_news_seed import seed_news, confirm_all_read
import importlib
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from api_fixtures import _create_standalone_idea, _isolate_app_settings


def test_global_search_mvp(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-06-04.md").write_text(
        """## Reuters · World（2条）
### [AlphaTitle](https://example.com/alpha)
- 发布时间：2026-06-04 09:00:00
### [BetaTitle](https://example.com/beta)
- 发布时间：2026-06-04 11:00:00
""",
        encoding="utf-8",
    )
    db_path = tmp_path / "news_index.sqlite3"

    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))

    import app as app_module

    importlib.reload(app_module)
    monkeypatch.setattr(preferences, 'has_secret', lambda name: name == "DEEPSEEK_API_KEY")
    app_module.ensure_db()
    client = app_module.app.test_client()
    assert seed_news(client, json={}).status_code == 200

    items = client.get("/api/news?per=20").get_json()["items"]
    alpha = next(item for item in items if item["title"] == "AlphaTitle")

    ts = database.now_ts()
    conn = app_module.data.connect()
    try:
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
                    alpha["url"],
                    "Reuters",
                    "AlphaTitle",
                    "Reporter",
                    "2026-06-04 09:00:00",
                    "AlphaX body-needle full english content for search",
                    48,
                    "{}",
                    ts,
                    ts,
                ),
            )
            conn.execute(
                """
                INSERT INTO article_ai(url, model, key_points_zh, conclusion_zh, body_zh, raw_json, generated_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    alpha["url"],
                    "deepseek-chat",
                    json.dumps(["锂电扩产"], ensure_ascii=False),
                    "中文结论",
                    "中文正文命中 AlphaX",
                    "{}",
                    ts,
                    ts,
                ),
            )

    finally:
        conn.close()

    note_res = client.put(f"/api/news/{alpha['id']}/note", json={"note": "我的独特想法 AlphaX"})
    assert note_res.status_code == 200

    create_tag = client.post("/api/market-tags", json={"display_name": "宏观Beta"})
    assert create_tag.status_code == 200
    tag_key = create_tag.get_json()["tag"]["key"]
    assert client.put(
        f"/api/news/{alpha['id']}/market-tag",
        json={"tag": tag_key, "direction": "bullish"},
    ).status_code == 200
    assert client.patch(
        f"/api/market-tags/{tag_key}",
        json={"display_name": "大宏观"},
    ).status_code == 200

    title_hit = client.get("/api/search?q=AlphaTitle&per=20")
    assert title_hit.status_code == 200
    assert [item["title"] for item in title_hit.get_json()["items"]] == ["AlphaTitle"]

    english_hit = client.get("/api/search?q=body-needle&per=20").get_json()
    assert english_hit["total"] == 1
    assert english_hit["items"][0]["title"] == "AlphaTitle"

    ai_body_hit = client.get("/api/search?q=中文正文命中&per=20").get_json()
    assert ai_body_hit["total"] == 1
    assert ai_body_hit["items"][0]["title"] == "AlphaTitle"

    ai_points_hit = client.get("/api/search?q=锂电扩产&per=20").get_json()
    assert ai_points_hit["total"] == 1
    assert ai_points_hit["items"][0]["title"] == "AlphaTitle"

    note_hit = client.get("/api/search?q=独特想法&per=20").get_json()
    assert note_hit["total"] == 1
    assert note_hit["items"][0]["title"] == "AlphaTitle"
    assert note_hit["items"][0]["note_preview"] == "我的独特想法 AlphaX"

    tag_key_hit = client.get(f"/api/search?q={tag_key}&per=20").get_json()
    assert tag_key_hit["total"] == 1
    assert tag_key_hit["items"][0]["title"] == "AlphaTitle"

    tag_label_hit = client.get("/api/search?q=大宏观&per=20").get_json()
    assert tag_label_hit["total"] == 1
    assert tag_label_hit["items"][0]["title"] == "AlphaTitle"
    assert tag_label_hit["items"][0]["market_tags"][0]["tag"] == "大宏观"

    dedup_hit = client.get("/api/search?q=AlphaX&per=20").get_json()
    assert dedup_hit["total"] == 1
    assert [item["title"] for item in dedup_hit["items"]] == ["AlphaTitle"]

    empty_hit = client.get("/api/search?q=")
    assert empty_hit.status_code == 200
    assert empty_hit.get_json()["items"] == []
    assert empty_hit.get_json()["total"] == 0

    missing_hit = client.get("/api/search?q=no-such-needle&per=20").get_json()
    assert missing_hit["total"] == 0
    assert missing_hit["items"] == []


def test_feed_and_non_feed_sorting_split(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-06-02.md").write_text(
        """## Reuters · World（3条）
### [Morning](https://example.com/morning)
- 发布时间：2026-06-02 09:00:00
### [Noon](https://example.com/noon)
- 发布时间：2026-06-02 12:00:00
### [Evening](https://example.com/evening)
- 发布时间：2026-06-02 18:00:00
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

    assert seed_news(client, json={}).status_code == 200

    feed_items = client.get("/api/news?read_filter=unread&per=20").get_json()["items"]
    assert [item["title"] for item in feed_items] == ["Morning", "Noon", "Evening"]

    for item in feed_items:
        res = client.patch(f"/api/news/{item['id']}/state", json={"important": True})
        assert res.status_code == 200

    for item in feed_items:
        res = client.patch(f"/api/news/{item['id']}/state", json={"favorite": True})
        assert res.status_code == 200

    favorite_items = client.get("/api/news?collection=favorites&per=20").get_json()["items"]
    assert [item["title"] for item in favorite_items] == ["Evening", "Noon", "Morning"]

    favorite_reverse = client.get("/api/news?collection=favorites&sort_order=reverse&per=20").get_json()["items"]
    assert [item["title"] for item in favorite_reverse] == ["Morning", "Noon", "Evening"]

    important_items = client.get("/api/news?collection=important&per=20").get_json()["items"]
    assert [item["title"] for item in important_items] == ["Evening", "Noon", "Morning"]

    for item in feed_items:
        res = client.patch(f"/api/news/{item['id']}/state", json={"read_later": True})
        assert res.status_code == 200

    read_later_items = client.get("/api/news?collection=read_later&read_filter=unread&per=20").get_json()["items"]
    assert [item["title"] for item in read_later_items] == ["Morning", "Noon", "Evening"]


def test_read_later_cross_date_order_matches_feed_old_to_new(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-06-02.md").write_text(
        """## Reuters · World（4条）
### [D2-later](https://example.com/d2-later)
- 发布时间：2026-06-02 18:00:00
### [D1-middle](https://example.com/d1-middle)
- 发布时间：2026-06-03 12:00:00
### [D1-early](https://example.com/d1-early)
- 发布时间：2026-06-03 08:00:00
### [D2-early](https://example.com/d2-early)
- 发布时间：2026-06-02 06:00:00
""",
        encoding="utf-8",
    )
    db_path = tmp_path / "news_index.sqlite3"

    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))

    import app as app_module

    importlib.reload(app_module)
    monkeypatch.setattr(preferences, 'has_secret', lambda name: name == "DEEPSEEK_API_KEY")
    app_module.ensure_db()
    client = app_module.app.test_client()

    assert seed_news(client, json={}).status_code == 200
    feed_items = client.get("/api/news?read_filter=unread&per=20").get_json()["items"]
    assert [item["title"] for item in feed_items] == ["D2-early", "D2-later", "D1-early", "D1-middle"]

    for item in feed_items:
        res = client.patch(f"/api/news/{item['id']}/state", json={"read_later": True})
        assert res.status_code == 200

    read_later_items = client.get("/api/news?collection=read_later&read_filter=unread&per=20").get_json()["items"]
    assert [item["title"] for item in read_later_items] == ["D2-early", "D2-later", "D1-early", "D1-middle"]


def test_feed_unread_cursor_paging_survives_auto_read_shrink(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)

    reuters_items = []
    for idx in range(31):
        minute = idx % 60
        reuters_items.append(
            "\n".join(
                [
                    f"### [Reuters {idx + 1}](https://www.reuters.com/world/test-{idx + 1})",
                    f"- 发布时间：2026-06-02 09:{minute:02d}:00",
                ]
            )
        )

    bloomberg_items = []
    for idx in range(5):
        minute = idx % 60
        bloomberg_items.append(
            "\n".join(
                [
                    f"### [Bloomberg {idx + 1}](https://www.bloomberg.com/news/test-{idx + 1})",
                    f"- 发布时间：2026-06-02 18:{minute:02d}:00",
                ]
            )
        )

    (daily_dir / "dailyFreshNews_2026-06-02.md").write_text(
        "## Reuters · World（31条）\n"
        + "\n".join(reuters_items)
        + "\n\n## Bloomberg · Markets（5条）\n"
        + "\n".join(bloomberg_items)
        + "\n",
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
    assert seed_news(client, json={}).status_code == 200

    page1 = client.get("/api/news?collection=feed&read_filter=unread&source_filter=reuters&page=1&per=30")
    assert page1.status_code == 200
    page1_data = page1.get_json()
    assert page1_data["total"] == 31
    assert page1_data["has_more"] is True
    assert page1_data["next_cursor"] is not None
    assert len(page1_data["items"]) == 30
    assert page1_data["items"][0]["title"] == "Reuters 1"
    assert page1_data["items"][-1]["title"] == "Reuters 30"

    for item in page1_data["items"][:10]:
        res = client.patch(f"/api/news/{item['id']}/state", json={"read": True})
        assert res.status_code == 200

    cursor = page1_data["next_cursor"]
    page2 = client.get(
        "/api/news?collection=feed&read_filter=unread&source_filter=reuters&page=2&per=30"
        f"&cursor_date={cursor['date_key']}&cursor_published_at={cursor['published_at']}&cursor_id={cursor['id']}"
    )
    assert page2.status_code == 200
    page2_data = page2.get_json()
    assert [item["title"] for item in page2_data["items"]] == ["Reuters 31"]
    assert page2_data["has_more"] is False
    assert page2_data["next_cursor"] is None

    loaded_ids = [str(item["id"]) for item in page1_data["items"]] + [str(item["id"]) for item in page2_data["items"]]
    mark_loaded = client.post("/api/news/mark-read-by-ids", json={"item_ids": loaded_ids})
    assert mark_loaded.status_code == 200
    assert mark_loaded.get_json()["marked"] == 31

    reuters_unread = client.get("/api/news?collection=feed&read_filter=unread&source_filter=reuters&page=1&per=30")
    assert reuters_unread.status_code == 200
    assert reuters_unread.get_json()["total"] == 0

    bloomberg_unread = client.get("/api/news?collection=feed&read_filter=unread&source_filter=bloomberg&page=1&per=30")
    assert bloomberg_unread.status_code == 200
    assert bloomberg_unread.get_json()["total"] == 5


def test_feed_unread_reverse_sort_keeps_cursor_direction(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)

    reuters_items = []
    for idx in range(31):
        minute = idx % 60
        reuters_items.append(
            "\n".join(
                [
                    f"### [Reuters Reverse {idx + 1}](https://www.reuters.com/world/reverse-{idx + 1})",
                    f"- 发布时间：2026-06-03 09:{minute:02d}:00",
                ]
            )
        )

    (daily_dir / "dailyFreshNews_2026-06-03.md").write_text(
        "## Reuters · World（31条）\n" + "\n".join(reuters_items) + "\n",
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
    assert seed_news(client, json={}).status_code == 200

    page1 = client.get("/api/news?collection=feed&read_filter=unread&sort_order=reverse&page=1&per=30")
    assert page1.status_code == 200
    page1_data = page1.get_json()
    assert page1_data["items"][0]["title"] == "Reuters Reverse 31"
    assert page1_data["items"][-1]["title"] == "Reuters Reverse 2"
    assert page1_data["has_more"] is True
    assert page1_data["next_cursor"] is not None

    for item in page1_data["items"][:10]:
        res = client.patch(f"/api/news/{item['id']}/state", json={"read": True})
        assert res.status_code == 200

    cursor = page1_data["next_cursor"]
    page2 = client.get(
        "/api/news?collection=feed&read_filter=unread&sort_order=reverse&page=2&per=30"
        f"&cursor_date={cursor['date_key']}&cursor_published_at={cursor['published_at']}&cursor_id={cursor['id']}"
    )
    assert page2.status_code == 200
    page2_data = page2.get_json()
    assert [item["title"] for item in page2_data["items"]] == ["Reuters Reverse 1"]
    assert page2_data["has_more"] is False
    assert page2_data["next_cursor"] is None


def test_news_sort_order_switches_default_and_reverse_by_collection(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-06-04.md").write_text(
        """## Reuters · World（4条）
### [Alpha Early](https://example.com/sort-alpha-early)
- 发布时间：2026-06-04 08:00:00
### [Alpha Late](https://example.com/sort-alpha-late)
- 发布时间：2026-06-04 18:00:00

## Bloomberg · Markets（2条）
### [Beta Early](https://example.com/sort-beta-early)
- 发布时间：2026-06-05 09:00:00
### [Beta Late](https://example.com/sort-beta-late)
- 发布时间：2026-06-05 20:00:00
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
    assert seed_news(client, json={}).status_code == 200

    feed_default = client.get("/api/news?collection=feed&read_filter=all&per=20").get_json()["items"]
    assert [item["title"] for item in feed_default[:4]] == ["Beta Late", "Beta Early", "Alpha Late", "Alpha Early"]

    feed_reverse = client.get("/api/news?collection=feed&read_filter=all&sort_order=reverse&per=20").get_json()["items"]
    assert [item["title"] for item in feed_reverse[:4]] == ["Alpha Early", "Alpha Late", "Beta Early", "Beta Late"]

    title_to_id = {item["title"]: item["id"] for item in feed_default}
    assert client.patch(f"/api/news/{title_to_id['Alpha Early']}/state", json={"important": True, "read_later": True}).status_code == 200
    assert client.patch(f"/api/news/{title_to_id['Beta Late']}/state", json={"important": True, "read_later": True}).status_code == 200
    assert client.patch(f"/api/news/{title_to_id['Beta Early']}/state", json={"important": True}).status_code == 200
    assert client.put(f"/api/news/{title_to_id['Alpha Late']}/note", json={"note": "排序测试想法 A"}).status_code == 200
    assert client.put(f"/api/news/{title_to_id['Beta Early']}/note", json={"note": "排序测试想法 B"}).status_code == 200

    create_tag = client.post("/api/market-tags", json={"display_name": "排序板块"})
    assert create_tag.status_code == 200
    tag_key = create_tag.get_json()["tag"]["key"]
    assert client.put(
        f"/api/news/{title_to_id['Beta Early']}/market-tag",
        json={"tag": tag_key, "direction": "bullish"},
    ).status_code == 200
    assert client.put(
        f"/api/news/{title_to_id['Alpha Early']}/market-tag",
        json={"tag": tag_key, "direction": "bearish"},
    ).status_code == 200

    important_default = client.get("/api/news?collection=important&per=20").get_json()["items"]
    assert [item["title"] for item in important_default] == ["Beta Late", "Beta Early", "Alpha Early"]
    important_reverse = client.get("/api/news?collection=important&sort_order=reverse&per=20").get_json()["items"]
    assert [item["title"] for item in important_reverse] == ["Alpha Early", "Beta Early", "Beta Late"]

    read_later_default = client.get("/api/news?collection=read_later&per=20").get_json()["items"]
    assert [item["title"] for item in read_later_default] == ["Beta Late", "Alpha Early"]
    read_later_reverse = client.get("/api/news?collection=read_later&sort_order=reverse&per=20").get_json()["items"]
    assert [item["title"] for item in read_later_reverse] == ["Alpha Early", "Beta Late"]

    notes_default = client.get("/api/news?collection=notes&per=20").get_json()["items"]
    assert [item["title"] for item in notes_default] == ["Beta Early", "Alpha Late"]
    notes_reverse = client.get("/api/news?collection=notes&sort_order=reverse&per=20").get_json()["items"]
    assert [item["title"] for item in notes_reverse] == ["Alpha Late", "Beta Early"]

    tags_default = client.get("/api/news?collection=market_tags&per=20").get_json()["items"]
    assert [item["title"] for item in tags_default] == ["Beta Early", "Alpha Early"]
    tags_reverse = client.get("/api/news?collection=market_tags&sort_order=reverse&per=20").get_json()["items"]
    assert [item["title"] for item in tags_reverse] == ["Alpha Early", "Beta Early"]

    for collection, unread_titles in [
        ("feed", ["Alpha Late"]),
        ("read_later", ["Alpha Early", "Beta Late"]),
    ]:
        for order, expected in [("default", unread_titles), ("reverse", unread_titles[::-1])]:
            unread = client.get("/api/news", query_string={
                "collection": collection, "read_filter": "unread", "sort_order": order,
            }).get_json()["items"]
            assert [item["title"] for item in unread] == expected

    for title in ["Alpha Early", "Beta Late"]:
        assert client.patch(f"/api/news/{title_to_id[title]}/state", json={"read": True}).status_code == 200
        assert client.patch(f"/api/news/{title_to_id[title]}/state", json={
            "read_later": False, "read_later_done": True,
        }).status_code == 200
    for collection in ["feed", "read_later"]:
        read_titles = ["Beta Late", "Beta Early", "Alpha Early"] if collection == "feed" else ["Beta Late", "Alpha Early"]
        for order, expected in [("default", read_titles), ("reverse", read_titles[::-1])]:
            read = client.get("/api/news", query_string={
                "collection": collection, "read_filter": "read", "sort_order": order,
            }).get_json()["items"]
            assert [item["title"] for item in read] == expected

    invalid = client.get("/api/news?collection=feed&sort_order=sideways")
    assert invalid.status_code == 400
    assert invalid.get_json()["error"] == "invalid_sort_order"


def test_unified_ideas_feed_combines_article_and_trend_notes(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-06-06.md").write_text(
        """## Reuters · World（2条）
### [Idea Alpha](https://example.com/idea-alpha)
- 发布时间：2026-06-06 08:00:00
### [Idea Beta](https://example.com/idea-beta)
- 发布时间：2026-06-06 10:00:00
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
    assert seed_news(client, json={}).status_code == 200

    items = client.get("/api/news?per=20").get_json()["items"]
    alpha = next(item for item in items if item["title"] == "Idea Alpha")
    beta = next(item for item in items if item["title"] == "Idea Beta")

    assert client.put(f"/api/news/{alpha['id']}/note", json={"note": "新闻想法 Alpha"}).status_code == 200
    assert client.put(f"/api/news/{beta['id']}/note", json={"note": "新闻想法 Beta"}).status_code == 200
    assert client.put(
        f"/api/news/{alpha['id']}/market-tag",
        json={"tag": "AI", "direction": "bullish"},
    ).status_code == 200

    trend_create = client.put(
        "/api/market-trends/note",
        json={"date_key": "2026-06-06", "tag_key": "AI", "direction": "bullish", "note": "趋势想法 Bull"},
    )
    assert trend_create.status_code == 200
    trend_note_id = trend_create.get_json()["trend_note"]["id"]

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE article_notes SET created_at=?, updated_at=? WHERE url=?",
            ("2026-06-06 09:00:00", "2026-06-06 09:00:00", alpha["url"]),
        )
        conn.execute(
            "UPDATE article_notes SET created_at=?, updated_at=? WHERE url=?",
            ("2026-06-06 11:00:00", "2026-06-06 11:00:00", beta["url"]),
        )
        conn.execute(
            "UPDATE market_trend_notes SET created_at=?, updated_at=? WHERE id=?",
            ("2026-06-06 12:30:00", "2026-06-06 12:30:00", trend_note_id),
        )
        conn.commit()

    ideas = client.get("/api/ideas?per=20")
    assert ideas.status_code == 200
    payload = ideas.get_json()
    assert payload["total"] == 3
    assert [item["idea_type"] for item in payload["items"]] == ["trend_note", "article_note", "article_note"]
    assert [item["idea_id"] for item in payload["items"]] == [f"trend:{trend_note_id}", f"article:{beta['id']}", f"article:{alpha['id']}"]
    trend_item = payload["items"][0]
    assert trend_item["tag_key"] == "AI"
    assert trend_item["trend_date_key"] == "2026-06-06"
    assert trend_item["direction"] == "bullish"
    assert trend_item["note"] == "趋势想法 Bull"
    assert trend_item["created_at"] == "2026-06-06 12:30:00"
    assert trend_item["updated_at"] == "2026-06-06 12:30:00"
    article_item = payload["items"][1]
    assert article_item["title"] == "Idea Beta"
    assert article_item["url"] == beta["url"]
    assert article_item["note"] == "新闻想法 Beta"
    assert article_item["created_at"] == "2026-06-06 11:00:00"
    assert article_item["updated_at"] == "2026-06-06 11:00:00"

    article_only = client.get("/api/ideas?type=article&per=20")
    assert article_only.status_code == 200
    assert [item["title"] for item in article_only.get_json()["items"]] == ["Idea Beta", "Idea Alpha"]

    trend_only = client.get("/api/ideas?type=trend&per=20")
    assert trend_only.status_code == 200
    assert [item["idea_id"] for item in trend_only.get_json()["items"]] == [f"trend:{trend_note_id}"]

    reverse = client.get("/api/ideas?per=20&sort_order=reverse")
    assert reverse.status_code == 200
    assert [item["idea_id"] for item in reverse.get_json()["items"]] == [f"article:{alpha['id']}", f"article:{beta['id']}", f"trend:{trend_note_id}"]

    legacy_notes = client.get("/api/news?collection=notes&per=20")
    assert legacy_notes.status_code == 200
    assert [item["title"] for item in legacy_notes.get_json()["items"]] == ["Idea Beta", "Idea Alpha"]

    delete_res = client.delete(f"/api/market-trends/note/{trend_note_id}")
    assert delete_res.status_code == 200
    trend_after_delete = client.get("/api/ideas?type=trend&per=20")
    assert trend_after_delete.status_code == 200
    assert trend_after_delete.get_json()["total"] == 0

    invalid_type = client.get("/api/ideas?type=weird")
    assert invalid_type.status_code == 400
    assert invalid_type.get_json()["error"] == "invalid_idea_type"


def test_search_range_and_time_filters(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "news_index.sqlite3"
    daily_dir = tmp_path / "DailyNews"
    daily_dir.mkdir(parents=True)

    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(daily_dir))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))

    import app as app_module

    importlib.reload(app_module)
    monkeypatch.setattr(preferences, 'has_secret', lambda name: name == "DEEPSEEK_API_KEY")
    app_module.ensure_db()
    client = app_module.app.test_client()

    today = datetime.now().date()
    d5 = today - timedelta(days=5)
    d20 = today - timedelta(days=20)
    d40 = today - timedelta(days=40)
    ts = f"{today.isoformat()} 12:00:00"

    def make_item(item_id: int, title: str, day) -> tuple:
        day_text = day.isoformat()
        published_at = f"{day_text} 09:00:00"
        return (
            item_id,
            "search.md",
            item_id,
            published_at,
            day_text,
            "09:00",
            "Reuters",
            "rss",
            "Reuters",
            title,
            f"summary {title}",
            f"https://example.com/{item_id}",
            ts,
            ts,
        )

    conn = app_module.data.connect()
    try:
        conn.executemany(
            """
            INSERT INTO items(
              id, source_file, item_order, published_at, date, time, source, source_type,
              source_name, title, summary, url, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                make_item(1, "RangeNeedle A", today),
                make_item(2, "RangeNeedle B", d5),
                make_item(3, "RangeNeedle C", d20),
                make_item(4, "RangeNeedle D", d40),
            ],
        )
        conn.execute(
            "INSERT INTO item_state(item_id, important_at, updated_at) VALUES (?, ?, ?)",
            (1, ts, ts),
        )
        conn.executemany(
            "INSERT INTO article_notes(url, note, created_at, updated_at) VALUES (?, ?, ?, ?)",
            [
                ("https://example.com/1", "note A", ts, ts),
                ("https://example.com/2", "note B", ts, ts),
            ],
        )
        conn.executemany(
            "INSERT INTO article_market_tags(url, tag, direction, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            [
                ("https://example.com/1", "ai", "bullish", ts, ts),
                ("https://example.com/3", "ai", "bearish", ts, ts),
            ],
        )
        conn.executemany(
            "INSERT INTO article_details(url, title, author, published_at, content, content_length, fetched_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("https://example.com/1", "RangeNeedle A", "r", ts, "body", 4, ts, ts),
                ("https://example.com/3", "RangeNeedle C", "r", ts, "body", 4, ts, ts),
                ("https://example.com/4", "RangeNeedle D", "r", ts, "body", 4, ts, ts),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    assert client.get("/api/search?q=RangeNeedle&range=all&time=all&per=20").get_json()["total"] == 4
    assert client.get("/api/search?q=RangeNeedle&range=important&time=all&per=20").get_json()["total"] == 1
    assert client.get("/api/search?q=RangeNeedle&range=notes&time=all&per=20").get_json()["total"] == 2
    assert client.get("/api/search?q=RangeNeedle&range=market_tags&time=all&per=20").get_json()["total"] == 2
    assert client.get("/api/search?q=RangeNeedle&range=detail_ready&time=all&per=20").get_json()["total"] == 3

    assert client.get("/api/search?q=RangeNeedle&range=all&time=today&per=20").get_json()["total"] == 1
    assert client.get("/api/search?q=RangeNeedle&range=all&time=7d&per=20").get_json()["total"] == 2
    assert client.get("/api/search?q=RangeNeedle&range=all&time=30d&per=20").get_json()["total"] == 3

    ignored = client.get(
        "/api/search?q=RangeNeedle&range=all&time=all&collection=important&source_filter=bloomberg&read_filter=read&per=20"
    )
    assert ignored.status_code == 200
    assert ignored.get_json()["total"] == 4


def test_mark_all_read_cross_page_with_filter(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年5月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-05-25.md").write_text(
        """## Reuters · World（3条）
### [Alpha 1](https://example.com/a1)
- 发布时间：2026-05-25 12:00:00
### [Alpha 2](https://example.com/a2)
- 发布时间：2026-05-25 11:00:00
### [Beta 1](https://example.com/b1)
- 发布时间：2026-05-25 10:00:00
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
    r = seed_news(client, json={})
    assert r.status_code == 200

    # Cross-page condition: per=1 but mark-all-read should touch all filtered hits.
    page1 = client.get("/api/news?per=1&q=Alpha")
    assert page1.status_code == 200
    assert page1.get_json()["total"] == 2

    mark = confirm_all_read(client,
        json={"q": "Alpha", "read_filter": "all"},
    )
    assert mark.status_code == 200
    assert mark.get_json()["marked"] == 2

    read_alpha = client.get("/api/news?read_filter=read&q=Alpha")
    assert read_alpha.status_code == 200
    assert read_alpha.get_json()["total"] == 2

    unread_beta = client.get("/api/news?read_filter=unread&q=Beta")
    assert unread_beta.status_code == 200
    assert unread_beta.get_json()["total"] == 1


def test_mark_all_read_respects_collection(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年5月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-05-25.md").write_text(
        """## Reuters · World（2条）
### [Item 1](https://example.com/i1)
- 发布时间：2026-05-25 12:00:00
### [Item 2](https://example.com/i2)
- 发布时间：2026-05-25 11:00:00
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

    items = client.get("/api/news").get_json()["items"]
    item1 = items[0]["id"]
    item2 = items[1]["id"]
    assert client.patch(f"/api/news/{item1}/state", json={"important": True}).status_code == 200

    mark = confirm_all_read(client,
        json={"collection": "important", "read_filter": "all"},
    )
    assert mark.status_code == 200
    assert mark.get_json()["marked"] == 1

    important_read = client.get("/api/news?collection=important&read_filter=read")
    assert important_read.get_json()["total"] == 1

    feed_unread = client.get("/api/news?collection=feed&read_filter=unread")
    ids = {it["id"] for it in feed_unread.get_json()["items"]}
    assert item2 in ids


def test_mark_read_by_ids_only_touches_loaded_rows(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年5月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-05-25.md").write_text(
        """## Reuters · World（3条）
### [Alpha 1](https://example.com/a1)
- 发布时间：2026-05-25 12:00:00
### [Alpha 2](https://example.com/a2)
- 发布时间：2026-05-25 11:00:00
### [Alpha 3](https://example.com/a3)
- 发布时间：2026-05-25 10:00:00
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

    all_items = client.get("/api/news?per=20&q=Alpha").get_json()["items"]
    loaded_ids = [all_items[0]["id"], all_items[1]["id"]]
    untouched_id = all_items[2]["id"]

    mark = client.post(
        "/api/news/mark-read-by-ids",
        json={"item_ids": loaded_ids},
    )
    assert mark.status_code == 200
    assert mark.get_json()["marked"] == 2

    read_alpha = client.get("/api/news?read_filter=read&q=Alpha").get_json()["items"]
    assert {it["id"] for it in read_alpha} == set(loaded_ids)

    unread_alpha = client.get("/api/news?read_filter=unread&q=Alpha").get_json()["items"]
    assert {it["id"] for it in unread_alpha} == {untouched_id}


def test_news_date_counts_follow_filter_and_collection(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年5月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-05-25.md").write_text(
        """## Reuters · World（4条）
### [D1 Morning](https://example.com/d1-morning)
- 发布时间：2026-05-25 09:00:00
### [D1 Noon](https://example.com/d1-noon)
- 发布时间：2026-05-25 12:00:00
### [D2 Morning](https://example.com/d2-morning)
- 发布时间：2026-05-26 09:00:00
### [D2 Noon](https://example.com/d2-noon)
- 发布时间：2026-05-26 12:00:00
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

    feed_all = client.get("/api/news?per=20&read_filter=all").get_json()
    assert feed_all["date_counts"] == {"2026-05-26": 2, "2026-05-25": 2}

    items_by_title = {item["title"]: item for item in feed_all["items"]}
    assert client.patch(f"/api/news/{items_by_title['D1 Noon']['id']}/state", json={"read": True}).status_code == 200
    assert client.patch(f"/api/news/{items_by_title['D2 Noon']['id']}/state", json={"read": True}).status_code == 200
    assert client.patch(f"/api/news/{items_by_title['D2 Noon']['id']}/state", json={"important": True}).status_code == 200
    assert client.patch(f"/api/news/{items_by_title['D2 Morning']['id']}/state", json={"important": True}).status_code == 200

    feed_unread = client.get("/api/news?per=20&read_filter=unread").get_json()
    assert feed_unread["date_counts"] == {"2026-05-25": 1}

    important_all = client.get("/api/news?per=20&collection=important&read_filter=all").get_json()
    assert important_all["date_counts"] == {"2026-05-26": 2}

    loaded_ids = [
        items_by_title["D1 Morning"]["id"],
        items_by_title["D2 Morning"]["id"],
    ]
    mark = client.post("/api/news/mark-read-by-ids", json={"item_ids": loaded_ids})
    assert mark.status_code == 200
    assert mark.get_json()["marked"] == 2

    feed_unread_after = client.get("/api/news?per=20&read_filter=unread").get_json()
    assert feed_unread_after["date_counts"] == {}

    feed_all_after = client.get("/api/news?per=20&read_filter=all").get_json()
    assert feed_all_after["date_counts"] == {"2026-05-26": 2, "2026-05-25": 2}


def test_sources_and_source_filter(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年5月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-05-25.md").write_text(
        """## Reuters · World（1条）
### [R1](https://www.reuters.com/world/r1)
- 发布时间：2026-05-25 12:00:00
## Bloomberg · Markets（1条）
### [B1](https://www.bloomberg.com/news/articles/b1)
- 发布时间：2026-05-25 11:00:00
## Twitter · 外汇交易员（1条）
### [X1](https://x.com/fxtrader/status/1)
- 发布时间：2026-05-25 10:00:00
## UnknownFeed（1条）
### [U1](https://example.org/news/u1)
- 发布时间：2026-05-25 09:00:00
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

    src_resp = client.get("/api/sources?collection=feed&read_filter=all")
    assert src_resp.status_code == 200
    src_data = src_resp.get_json()
    assert src_data["ok"] is True
    keys = {x["key"] for x in src_data["sources"]}
    assert "reuters" in keys
    assert "bloomberg" in keys
    assert "x" in keys
    assert "host:example.org" in keys

    reuters = client.get("/api/news?source_filter=reuters")
    assert reuters.status_code == 200
    r_items = reuters.get_json()["items"]
    assert len(r_items) == 1
    assert r_items[0]["source_key"] == "reuters"

    xfeed = client.get("/api/news?source_filter=x")
    assert xfeed.status_code == 200
    x_items = xfeed.get_json()["items"]
    assert len(x_items) == 1
    assert x_items[0]["source_key"] == "x"

    unknown = client.get("/api/news?source_filter=host:example.org")
    assert unknown.status_code == 200
    u_items = unknown.get_json()["items"]
    assert len(u_items) == 1
    assert u_items[0]["url"].startswith("https://example.org/")


def test_news_section_order_date_asc_and_intra_date_asc_for_feed(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年5月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-05-25.md").write_text(
        """## Reuters · World（4条）
### [D2-later](https://example.com/d2-later)
- 发布时间：2026-05-29 18:00:00
### [D1-middle](https://example.com/d1-middle)
- 发布时间：2026-05-30 12:00:00
### [D1-early](https://example.com/d1-early)
- 发布时间：2026-05-30 08:00:00
### [D2-early](https://example.com/d2-early)
- 发布时间：2026-05-29 06:00:00
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

    res = client.get("/api/news?read_filter=unread&per=20")
    assert res.status_code == 200
    items = res.get_json()["items"]
    titles = [x["title"] for x in items]
    # section 间日期旧->新；同一日期内时间旧->新
    assert titles == ["D2-early", "D2-later", "D1-early", "D1-middle"]


def test_reading_checkpoint_save_get_and_locate(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年5月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-05-25.md").write_text(
        """## Reuters · World（4条）
### [A](https://example.com/a)
- 发布时间：2026-05-30 08:00:00
### [B](https://example.com/b)
- 发布时间：2026-05-30 12:00:00
### [C](https://example.com/c)
- 发布时间：2026-05-29 06:00:00
### [D](https://example.com/d)
- 发布时间：2026-05-29 18:00:00
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

    put = client.put(
        "/api/reading-checkpoint",
        json={"scope": "feed", "url": "https://example.com/d", "item_id": "x", "title": "D"},
    )
    assert put.status_code == 200
    assert put.get_json()["ok"] is True

    got = client.get("/api/reading-checkpoint?scope=feed")
    assert got.status_code == 200
    cp = got.get_json()["checkpoint"]
    assert cp["url"] == "https://example.com/d"

    loc = client.get("/api/reading-checkpoint/locate?scope=feed&per=2")
    assert loc.status_code == 200
    payload = loc.get_json()
    assert payload["ok"] is True
    assert payload["found"] is True
    assert payload["url"] == "https://example.com/d"
    # 使用当前后端排序规则定位到目标并返回分页位置
    assert payload["page"] == 1
    assert payload["offset"] == 1


def test_reading_checkpoint_locate_not_found(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年5月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-05-25.md").write_text(
        """## Reuters · World（1条）
### [A](https://example.com/a)
- 发布时间：2026-05-30 08:00:00
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
    assert client.put(
        "/api/reading-checkpoint",
        json={"scope": "feed", "url": "https://example.com/not-exist", "title": "X"},
    ).status_code == 200

    loc = client.get("/api/reading-checkpoint/locate?scope=feed&per=20")
    assert loc.status_code == 200
    payload = loc.get_json()
    assert payload["ok"] is True
    assert payload["found"] is False
    assert payload["reason"] == "not_in_current_scope"


def test_article_note_save_read_and_clear(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-06-01.md").write_text(
        """## Reuters · World（1条）
### [Note Item](https://example.com/n1)
- 发布时间：2026-06-01 08:00:00
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
    item_id = item["id"]
    assert int(item.get("has_note") or 0) == 0

    save = client.put(
        f"/api/news/{item_id}/note",
        json={"note": "这是我的第一条想法。"},
    )
    assert save.status_code == 200
    save_payload = save.get_json()
    assert save_payload["ok"] is True
    assert save_payload["has_note"] == 1
    assert save_payload["note"]["note"] == "这是我的第一条想法。"
    assert save_payload["note_preview"] == "这是我的第一条想法。"

    detail = client.get(f"/api/news/{item_id}/detail")
    assert detail.status_code == 200
    detail_payload = detail.get_json()
    assert detail_payload["ok"] is True
    assert detail_payload["has_note"] == 1
    assert detail_payload["note"]["note"] == "这是我的第一条想法。"

    listed = client.get("/api/news?per=20").get_json()["items"][0]
    assert int(listed.get("has_note") or 0) == 1
    assert listed["note_preview"] == "这是我的第一条想法。"

    clear = client.put(f"/api/news/{item_id}/note", json={"note": "   "})
    assert clear.status_code == 200
    clear_payload = clear.get_json()
    assert clear_payload["ok"] is True
    assert clear_payload["has_note"] == 0
    assert clear_payload["note"] is None
    assert clear_payload["note_preview"] == ""

    detail2 = client.get(f"/api/news/{item_id}/detail").get_json()
    assert detail2["has_note"] == 0
    assert detail2["note"] is None


def test_notes_collection_and_sources_filter(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-06-01.md").write_text(
        """## Reuters · World（2条）
### [R1](https://www.reuters.com/world/r1)
- 发布时间：2026-06-01 09:00:00
## Bloomberg · Tech（1条）
### [B1](https://www.bloomberg.com/news/articles/b1)
- 发布时间：2026-06-01 08:00:00
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
    reuters_item = next(x for x in items if "reuters.com" in (x.get("url") or ""))
    bloomberg_item = next(x for x in items if "bloomberg.com" in (x.get("url") or ""))

    assert client.put(f"/api/news/{reuters_item['id']}/note", json={"note": "R note"}).status_code == 200
    assert client.put(f"/api/news/{bloomberg_item['id']}/note", json={"note": "B note"}).status_code == 200

    notes_all = client.get("/api/news?collection=notes&per=20")
    assert notes_all.status_code == 200
    notes_items = notes_all.get_json()["items"]
    assert notes_all.get_json()["total"] == 2
    assert all(int(x.get("has_note") or 0) == 1 for x in notes_items)

    notes_reuters = client.get("/api/news?collection=notes&source_filter=reuters&per=20")
    assert notes_reuters.status_code == 200
    assert notes_reuters.get_json()["total"] == 1
    assert notes_reuters.get_json()["items"][0]["source_key"] == "reuters"

    sources_notes = client.get("/api/sources?collection=notes&read_filter=all")
    assert sources_notes.status_code == 200
    payload = sources_notes.get_json()
    assert payload["ok"] is True
    keys = {x["key"] for x in payload["sources"]}
    assert "reuters" in keys
    assert "bloomberg" in keys

    assert client.put(f"/api/news/{reuters_item['id']}/note", json={"note": ""}).status_code == 200
    notes_after_clear = client.get("/api/news?collection=notes&per=20").get_json()
    assert notes_after_clear["total"] == 1
    assert notes_after_clear["items"][0]["source_key"] == "bloomberg"


def test_nav_summary_counts_use_independent_collection_semantics(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "news_index.sqlite3"
    settings_path = tmp_path / "app_settings.json"
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(settings_path))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    ts = "2026-07-31 10:00:00"
    rows = [
        ("visible", "manual.md", 1, "2026-07-31 09:00:00", "2026-07-31", "09:00", "Reuters · China", "rss", "Reuters", "Visible unread", "s", "https://www.reuters.com/world/china/visible", ts, ts),
        ("hidden", "manual.md", 2, "2026-07-31 09:01:00", "2026-07-31", "09:01", "Reuters · Middle East", "rss", "Reuters", "Hidden unread", "s", "https://www.reuters.com/world/middle-east/hidden", ts, ts),
        ("read", "manual.md", 3, "2026-07-31 09:02:00", "2026-07-31", "09:02", "Reuters · China", "rss", "Reuters", "Read feed item", "s", "https://www.reuters.com/world/china/read", ts, ts),
        ("later", "manual.md", 4, "2026-07-31 09:03:00", "2026-07-31", "09:03", "Reuters · China", "rss", "Reuters", "Read later queue", "s", "https://www.reuters.com/world/china/later", ts, ts),
    ]
    conn = app_module.data.connect()
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
            conn.execute("INSERT INTO item_state(item_id, read_at, updated_at) VALUES (?, ?, ?)", ("read", ts, ts))
            conn.execute(
                "INSERT INTO item_state(item_id, read_at, read_later_at, updated_at) VALUES (?, ?, ?, ?)",
                ("later", ts, ts, ts),
            )
    finally:
        conn.close()

    settings = app_module.default_app_settings()
    settings["feed"]["hidden_source_subkeys"] = ["reuters/middle-east"]
    preferences.save_app_settings(settings)
    client = app_module.app.test_client()

    idea_id = _create_standalone_idea(client, "待复盘角标想法")
    pending = client.post("/api/reviews", json={
        "source_type": "standalone_idea",
        "source_key": str(idea_id),
        "judgment": "到期判断",
        "plan_review_date": "2020-01-01",
    })
    assert pending.status_code == 200
    pending_id = pending.get_json()["review"]["id"]
    future_idea_id = _create_standalone_idea(client, "未来复盘想法")
    future = client.post("/api/reviews", json={
        "source_type": "standalone_idea",
        "source_key": str(future_idea_id),
        "judgment": "未来判断",
        "plan_review_date": "2099-01-01",
    })
    assert future.status_code == 200

    summary = client.get("/api/nav-summary")
    assert summary.status_code == 200
    assert summary.get_json()["summary"] == {
        "feed_unread": 1,
        "read_later_unread": 1,
        "favorites": 0,
        "pending_review": 1,
        "topics_unseen": 0,
    }

    settings["feed"]["hidden_source_subkeys"] = []
    preferences.save_app_settings(settings)
    restored = client.get("/api/nav-summary").get_json()["summary"]
    assert restored["feed_unread"] == 2
    assert restored["read_later_unread"] == 1

    removed = client.patch("/api/news/later/state", json={"read_later": False})
    assert removed.status_code == 200
    after_later_done = client.get("/api/nav-summary").get_json()["summary"]
    assert after_later_done["read_later_unread"] == 0

    completed = client.post(f"/api/reviews/{pending_id}/complete", json={
        "result": "confirmed",
        "actual_text": "事实",
        "experience": "经验",
    })
    assert completed.status_code == 200
    assert client.get("/api/nav-summary").get_json()["summary"]["pending_review"] == 0


def test_standalone_idea_crud_and_merge(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "news_index.sqlite3"
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))
    import app as app_module

    importlib.reload(app_module)
    monkeypatch.setattr(preferences, 'has_secret', lambda name: False)
    app_module.ensure_db()
    client = app_module.app.test_client()

    # create
    resp = client.post("/api/standalone-ideas", json={"note": "  第一个独立想法  "})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    idea = data["idea"]
    assert idea["idea_type"] == "standalone_note"
    assert idea["idea_id"].startswith("standalone:")
    assert idea["standalone_id"] is not None
    assert idea["note"] == "第一个独立想法"
    assert idea["title"] == ""
    idea_id = idea["standalone_id"]

    # empty note rejected
    assert client.post("/api/standalone-ideas", json={"note": "   "}).status_code == 400
    assert client.post("/api/standalone-ideas", json={"note": ""}).status_code == 400
    # invalid type
    assert client.post("/api/standalone-ideas", json={"note": 123}).status_code == 400
    # too long
    assert client.post("/api/standalone-ideas", json={"note": "x" * 5001}).status_code == 400
    # max length boundary ok
    resp_max = client.post("/api/standalone-ideas", json={"note": "y" * 5000})
    assert resp_max.status_code == 200

    # list merged in /api/ideas
    ideas = client.get("/api/ideas?per=100")
    assert ideas.status_code == 200
    payload = ideas.get_json()
    standalone_items = [item for item in payload["items"] if item["idea_type"] == "standalone_note"]
    assert len(standalone_items) == 2

    # filter standalone only
    standalone_only = client.get("/api/ideas?type=standalone&per=100")
    assert standalone_only.status_code == 200
    assert standalone_only.get_json()["total"] == 2
    assert all(item["idea_type"] == "standalone_note" for item in standalone_only.get_json()["items"])

    # filter article only — no standalone
    article_only = client.get("/api/ideas?type=article&per=100")
    assert article_only.status_code == 200
    assert all(item["idea_type"] == "article_note" for item in article_only.get_json()["items"])

    # update
    resp = client.patch(f"/api/standalone-ideas/{idea_id}", json={"note": "  更新后的想法  "})
    assert resp.status_code == 200
    updated = resp.get_json()["idea"]
    assert updated["note"] == "更新后的想法"

    # update empty rejected
    assert client.patch(f"/api/standalone-ideas/{idea_id}", json={"note": "  "}).status_code == 400
    # update too long
    assert client.patch(f"/api/standalone-ideas/{idea_id}", json={"note": "x" * 5001}).status_code == 400
    # update non-existent
    assert client.patch("/api/standalone-ideas/99999", json={"note": "test"}).status_code == 404

    # delete
    assert client.delete(f"/api/standalone-ideas/{idea_id}").status_code == 200
    # delete again → 404
    assert client.delete(f"/api/standalone-ideas/{idea_id}").status_code == 404

    # verify deleted from ideas list
    after_delete = client.get("/api/ideas?type=standalone&per=100")
    assert after_delete.get_json()["total"] == 1

    # invalid filter type still rejected
    assert client.get("/api/ideas?type=weird").status_code == 400
