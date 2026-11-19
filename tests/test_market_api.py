from __future__ import annotations

from legacy_news_seed import seed_news, confirm_all_read
import importlib
import sqlite3
from pathlib import Path
from api_fixtures import _isolate_app_settings


def test_apply_schema_creates_market_tag_summary_table(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "news_index.sqlite3"
    daily_root = tmp_path / "DailyNews"
    daily_root.mkdir()

    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(daily_root))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        indexes = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
        }
        assert "market_tag_summaries" in tables
        assert "idx_market_tag_summaries_tag_range" in indexes
    finally:
        conn.close()


def test_market_tags_crud_and_collection_filter(tmp_path: Path, monkeypatch):
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

    p1 = client.put(
        f"/api/news/{reuters_item['id']}/market-tag",
        json={"tag": "AI", "direction": "bullish"},
    )
    assert p1.status_code == 200
    assert p1.get_json()["has_market_tags"] == 1
    assert p1.get_json()["market_tags"][0]["direction"] == "bullish"
    assert p1.get_json()["important_at"] is not None

    # 同 tag 覆盖方向
    p2 = client.put(
        f"/api/news/{reuters_item['id']}/market-tag",
        json={"tag": "AI", "direction": "bearish"},
    )
    assert p2.status_code == 200
    ai_tag = next(x for x in p2.get_json()["market_tags"] if x["tag"] == "AI")
    assert ai_tag["direction"] == "bearish"

    # 多 tag 共存
    p3 = client.put(
        f"/api/news/{reuters_item['id']}/market-tag",
        json={"tag": "新能源", "direction": "bullish"},
    )
    assert p3.status_code == 200
    assert len(p3.get_json()["market_tags"]) == 2

    p4 = client.put(
        f"/api/news/{bloomberg_item['id']}/market-tag",
        json={"tag": "房地产", "direction": "bearish"},
    )
    assert p4.status_code == 200

    m_all = client.get("/api/news?collection=market_tags&per=20")
    assert m_all.status_code == 200
    all_payload = m_all.get_json()
    assert all_payload["total"] == 2
    assert all(int(x.get("has_market_tags") or 0) == 1 for x in all_payload["items"])

    m_reuters = client.get("/api/news?collection=market_tags&source_filter=reuters&per=20")
    assert m_reuters.status_code == 200
    assert m_reuters.get_json()["total"] == 1
    assert m_reuters.get_json()["items"][0]["source_key"] == "reuters"

    d1 = client.get(f"/api/news/{reuters_item['id']}/detail")
    assert d1.status_code == 200
    d_payload = d1.get_json()
    assert d_payload["has_market_tags"] == 1
    assert len(d_payload["market_tags"]) == 2

    s1 = client.get("/api/sources?collection=market_tags&read_filter=all")
    assert s1.status_code == 200
    s_payload = s1.get_json()
    assert s_payload["ok"] is True
    keys = {x["key"] for x in s_payload["sources"]}
    assert "reuters" in keys
    assert "bloomberg" in keys

    # 删除单个 tag
    d_tag = client.delete(f"/api/news/{reuters_item['id']}/market-tag?tag=AI")
    assert d_tag.status_code == 200
    assert d_tag.get_json()["ok"] is True
    assert len(d_tag.get_json()["market_tags"]) == 1

    # 删除剩余 tag 后不再属于 market_tags 集合
    client.delete(f"/api/news/{reuters_item['id']}/market-tag?tag=新能源")
    m_after = client.get("/api/news?collection=market_tags&per=20").get_json()
    assert m_after["total"] == 1
    assert m_after["items"][0]["source_key"] == "bloomberg"

    # 删除标签不会自动取消 important
    feed = client.get("/api/news?per=20").get_json()["items"]
    r1_after = next(x for x in feed if x["id"] == reuters_item["id"])
    assert r1_after["important_at"] is not None


def test_market_workbench_pinned_notes(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-06-01.md").write_text(
        """## Reuters · World（1条）
### [R1](https://www.reuters.com/world/r1)
- 发布时间：2026-06-01 09:00:00
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
    reuters_item = items[0]
    assert client.put(
        f"/api/news/{reuters_item['id']}/market-tag",
        json={"tag": "AI", "direction": "bullish"},
    ).status_code == 200

    overview = client.get("/api/market-workbench")
    assert overview.status_code == 200
    overview_payload = overview.get_json()
    assert overview_payload["pin"]["scope"] == "overview"
    assert overview_payload["pin"]["tag_key"] == ""
    assert overview_payload["pin"]["note"] == ""
    assert overview_payload["pin"]["collapsed"] == 0

    save_overview = client.put(
        "/api/market-workbench/pin",
        json={"tag_key": "", "note": "总置顶说明", "collapsed": True},
    )
    assert save_overview.status_code == 200
    save_overview_payload = save_overview.get_json()["pin"]
    assert save_overview_payload["scope"] == "overview"
    assert save_overview_payload["note"] == "总置顶说明"
    assert save_overview_payload["collapsed"] == 1

    overview_after = client.get("/api/market-workbench").get_json()
    assert overview_after["pin"]["note"] == "总置顶说明"
    assert overview_after["pin"]["collapsed"] == 1

    tag_view = client.get("/api/market-workbench?tag=AI")
    assert tag_view.status_code == 200
    tag_payload = tag_view.get_json()
    assert tag_payload["pin"]["scope"] == "tag"
    assert tag_payload["pin"]["tag_key"] == "AI"
    assert tag_payload["pin"]["tag_label"] == "AI"
    assert tag_payload["pin"]["note"] == ""

    save_tag = client.put(
        "/api/market-workbench/pin",
        json={"tag_key": "AI", "note": "AI 板块置顶", "collapsed": False},
    )
    assert save_tag.status_code == 200
    save_tag_payload = save_tag.get_json()["pin"]
    assert save_tag_payload["scope"] == "tag"
    assert save_tag_payload["tag_key"] == "AI"
    assert save_tag_payload["note"] == "AI 板块置顶"
    assert save_tag_payload["collapsed"] == 0

    tag_after = client.get("/api/market-workbench?tag=AI").get_json()
    assert tag_after["pin"]["note"] == "AI 板块置顶"
    assert tag_after["pin"]["collapsed"] == 0
    assert client.get("/api/market-workbench").get_json()["pin"]["note"] == "总置顶说明"

    clear_tag = client.put(
        "/api/market-workbench/pin",
        json={"tag_key": "AI", "note": "", "collapsed": True},
    )
    assert clear_tag.status_code == 200
    clear_tag_payload = clear_tag.get_json()["pin"]
    assert clear_tag_payload["note"] == ""
    assert clear_tag_payload["collapsed"] == 1

    invalid = client.put(
        "/api/market-workbench/pin",
        json={"tag_key": "NOT_FOUND", "note": "bad", "collapsed": False},
    )
    assert invalid.status_code == 400
    assert invalid.get_json()["error"] == "invalid_tag"


def test_market_trends_matrix_and_detail(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-06-02.md").write_text(
        """## Reuters · World（2条）
### [R1](https://www.reuters.com/world/r1)
- 发布时间：2026-06-02 09:00:00
### [R2](https://www.reuters.com/world/r2)
- 发布时间：2026-06-02 10:00:00
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
    r1 = next(x for x in items if x["title"] == "R1")
    r2 = next(x for x in items if x["title"] == "R2")
    b1 = next(x for x in items if x["title"] == "B1")

    assert client.put(f"/api/news/{r1['id']}/market-tag", json={"tag": "AI", "direction": "bullish"}).status_code == 200
    assert client.put(f"/api/news/{r2['id']}/market-tag", json={"tag": "AI", "direction": "bullish"}).status_code == 200
    assert client.put(f"/api/news/{b1['id']}/market-tag", json={"tag": "AI", "direction": "bearish"}).status_code == 200
    assert client.put(f"/api/news/{b1['id']}/note", json={"note": "留意 Apple 链条影响"}).status_code == 200

    trends = client.get("/api/market-trends?days=7")
    assert trends.status_code == 200
    payload = trends.get_json()
    assert payload["ok"] is True
    assert payload["dates"] == ["2026-06-01", "2026-06-02"]
    ai_row = next(row for row in payload["rows"] if row["tag"] == "AI")
    counts_by_date = {slot["date"]: slot for slot in ai_row["values"]}
    assert counts_by_date["2026-06-02"]["bullish"] == 2
    assert counts_by_date["2026-06-02"]["bearish"] == 0
    assert counts_by_date["2026-06-01"]["bullish"] == 0
    assert counts_by_date["2026-06-01"]["bearish"] == 1

    detail = client.get("/api/market-trends/detail?date=2026-06-02&tag=AI&direction=bullish")
    assert detail.status_code == 200
    detail_payload = detail.get_json()
    assert detail_payload["ok"] is True
    assert detail_payload["total"] == 2
    assert [item["title"] for item in detail_payload["items"]] == ["R1", "R2"]
    assert all(item["date_key"] == "2026-06-02" for item in detail_payload["items"])
    assert all(item["source_key"] == "reuters" for item in detail_payload["items"])

    bearish_detail = client.get("/api/market-trends/detail?date=2026-06-01&tag=AI&direction=bearish")
    assert bearish_detail.status_code == 200
    bearish_payload = bearish_detail.get_json()
    assert bearish_payload["total"] == 1
    assert bearish_payload["items"][0]["title"] == "B1"
    assert bearish_payload["items"][0]["has_note"] == 1
    assert bearish_payload["items"][0]["note"]["note"] == "留意 Apple 链条影响"
    assert bearish_payload["items"][0]["market_tags"][0]["direction"] == "bearish"


def test_market_tag_definitions_crud_and_dynamic_usage(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-06-02.md").write_text(
        """## Reuters · World（1条）
### [R1](https://www.reuters.com/world/r1)
- 发布时间：2026-06-02 09:00:00
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

    tags_res = client.get("/api/market-tags")
    assert tags_res.status_code == 200
    tags_payload = tags_res.get_json()
    assert tags_payload["ok"] is True
    assert any(tag["display_name"] == "AI" for tag in tags_payload["tags"])

    create_res = client.post("/api/market-tags", json={"display_name": "宏观观察"})
    assert create_res.status_code == 200
    created_tag = create_res.get_json()["tag"]
    assert created_tag["display_name"] == "宏观观察"
    assert created_tag["active"] == 1

    item = client.get("/api/news?per=20").get_json()["items"][0]
    put_res = client.put(
        f"/api/news/{item['id']}/market-tag",
        json={"tag": created_tag["key"], "direction": "bullish"},
    )
    assert put_res.status_code == 200
    assert put_res.get_json()["market_tags"][0]["tag"] == "宏观观察"

    rename_res = client.patch(
        f"/api/market-tags/{created_tag['key']}",
        json={"display_name": "大宏观"},
    )
    assert rename_res.status_code == 200
    assert rename_res.get_json()["tag"]["display_name"] == "大宏观"

    detail_res = client.get(f"/api/news/{item['id']}/detail")
    assert detail_res.status_code == 200
    detail_payload = detail_res.get_json()
    renamed_tag = next(tag for tag in detail_payload["market_tags"] if tag["key"] == created_tag["key"])
    assert renamed_tag["tag"] == "大宏观"
    choice = next(tag for tag in detail_payload["market_tag_choices"] if tag["key"] == created_tag["key"])
    assert choice["display_name"] == "大宏观"

    deactivate_res = client.patch(
        f"/api/market-tags/{created_tag['key']}",
        json={"active": False},
    )
    assert deactivate_res.status_code == 200
    assert deactivate_res.get_json()["tag"]["active"] == 0

    active_tags = client.get("/api/market-tags?active_only=1").get_json()["tags"]
    assert all(tag["key"] != created_tag["key"] for tag in active_tags)

    trends = client.get("/api/market-trends?days=7").get_json()
    assert all(row["tag_key"] != created_tag["key"] for row in trends["rows"])

    detail_after_deactivate = client.get(f"/api/news/{item['id']}/detail").get_json()
    historical_tag = next(tag for tag in detail_after_deactivate["market_tags"] if tag["key"] == created_tag["key"])
    assert historical_tag["tag"] == "大宏观"


def test_market_tag_reorder_is_atomic_and_normalizes_after_delete_merge(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-06-02.md").write_text(
        """## Reuters · World（1条）
### [R1](https://www.reuters.com/world/r1)
- 发布时间：2026-06-02 09:00:00
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

    first = client.post("/api/market-tags", json={"display_name": "第一自定义"}).get_json()["tag"]
    second = client.post("/api/market-tags", json={"display_name": "第二自定义"}).get_json()["tag"]
    initial_keys = [tag["key"] for tag in client.get("/api/market-tags").get_json()["tags"]]
    assert initial_keys[-2:] == [first["key"], second["key"]]

    reordered_keys = [second["key"], *[key for key in initial_keys if key != second["key"]]]
    reorder = client.post("/api/market-tags/reorder", json={"ordered_keys": reordered_keys})
    assert reorder.status_code == 200
    payload = reorder.get_json()
    assert [tag["key"] for tag in payload["tags"]] == reordered_keys
    assert [tag["sort_order"] for tag in payload["tags"]] == list(range(len(reordered_keys)))

    persisted_keys = [tag["key"] for tag in client.get("/api/market-tags").get_json()["tags"]]
    assert persisted_keys == reordered_keys

    def assert_rejected(body, error):
        before = [tag["key"] for tag in client.get("/api/market-tags").get_json()["tags"]]
        res = client.post("/api/market-tags/reorder", json=body)
        assert res.status_code == 400
        assert res.get_json()["error"] == error
        after = [tag["key"] for tag in client.get("/api/market-tags").get_json()["tags"]]
        assert after == before

    assert_rejected({"ordered_keys": [reordered_keys[0], reordered_keys[0], *reordered_keys[1:]]}, "duplicate_ordered_keys")
    assert_rejected({"ordered_keys": [*reordered_keys, "missing-key"]}, "unknown_ordered_keys")
    assert_rejected({"ordered_keys": reordered_keys[:-1]}, "missing_ordered_keys")

    delete_res = client.delete(f"/api/market-tags/{first['key']}")
    assert delete_res.status_code == 200
    tags_after_delete = delete_res.get_json()["tags"]
    assert all(tag["key"] != first["key"] for tag in tags_after_delete)
    assert [tag["sort_order"] for tag in tags_after_delete] == list(range(len(tags_after_delete)))

    third = client.post("/api/market-tags", json={"display_name": "第三自定义"}).get_json()["tag"]
    merge_res = client.post(f"/api/market-tags/{third['key']}/merge", json={"target_key": second["key"]})
    assert merge_res.status_code == 200
    tags_after_merge = merge_res.get_json()["tags"]
    assert all(tag["key"] != third["key"] for tag in tags_after_merge)
    assert [tag["sort_order"] for tag in tags_after_merge] == list(range(len(tags_after_merge)))


def test_market_tag_delete_removes_associations_and_blocks_default_reseed(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-06-02.md").write_text(
        """## Reuters · World（1条）
### [R1](https://www.reuters.com/world/r1)
- 发布时间：2026-06-02 09:00:00
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
    assert client.put(
        f"/api/news/{item['id']}/market-tag",
        json={"tag": "AI", "direction": "bullish"},
    ).status_code == 200
    note_res = client.put(
        "/api/market-trend-note",
        json={"date_key": "2026-06-02", "tag": "AI", "direction": "bullish", "note": "继续看多 AI"},
    )
    assert note_res.status_code == 200

    impact = client.get("/api/market-tags/AI/impact")
    assert impact.status_code == 200
    assert impact.get_json()["affected"] == {"item_tag_count": 1, "trend_note_count": 1}

    delete_res = client.delete("/api/market-tags/AI")
    assert delete_res.status_code == 200
    payload = delete_res.get_json()
    assert payload["deleted_tag"]["key"] == "AI"
    assert payload["affected"] == {"item_tag_count": 1, "trend_note_count": 1}
    assert all(tag["key"] != "AI" for tag in payload["tags"])

    detail_payload = client.get(f"/api/news/{item['id']}/detail").get_json()
    assert all(tag["key"] != "AI" for tag in detail_payload["market_tags"])

    market_tags_payload = client.get("/api/news?collection=market_tags&per=20").get_json()
    assert market_tags_payload["items"] == []

    trend_payload = client.get("/api/market-trends?days=7").get_json()
    assert all(row["tag_key"] != "AI" for row in trend_payload["rows"])

    with app_module.db_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM article_market_tags WHERE tag='AI'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM market_trend_notes WHERE tag='AI'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM market_tag_deleted_keys WHERE key='AI'").fetchone()[0] == 1

    app_module.ensure_db()
    tags_after_reseed = client.get("/api/market-tags").get_json()["tags"]
    assert all(tag["key"] != "AI" for tag in tags_after_reseed)


def test_market_tag_merge_moves_links_and_notes_and_dedupes_urls(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-06-02.md").write_text(
        """## Reuters · World（2条）
### [R1](https://www.reuters.com/world/r1)
- 发布时间：2026-06-02 09:00:00
### [R2](https://www.reuters.com/world/r2)
- 发布时间：2026-06-02 10:00:00
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

    create_res = client.post("/api/market-tags", json={"display_name": "人工智能"})
    assert create_res.status_code == 200
    source_key = create_res.get_json()["tag"]["key"]

    items = client.get("/api/news?per=20").get_json()["items"]
    item1 = next(item for item in items if item["title"] == "R1")
    item2 = next(item for item in items if item["title"] == "R2")
    assert client.put(f"/api/news/{item1['id']}/market-tag", json={"tag": source_key, "direction": "bullish"}).status_code == 200
    assert client.put(f"/api/news/{item1['id']}/market-tag", json={"tag": "AI", "direction": "bearish"}).status_code == 200
    assert client.put(f"/api/news/{item2['id']}/market-tag", json={"tag": source_key, "direction": "bullish"}).status_code == 200
    assert client.put(
        "/api/market-trend-note",
        json={"date_key": "2026-06-02", "tag": source_key, "direction": "bullish", "note": "AI 主线继续强化"},
    ).status_code == 200

    merge_res = client.post(f"/api/market-tags/{source_key}/merge", json={"target_key": "AI"})
    assert merge_res.status_code == 200
    payload = merge_res.get_json()
    assert payload["moved_item_tag_count"] == 1
    assert payload["skipped_duplicate_item_tag_count"] == 1
    assert payload["moved_trend_note_count"] == 1
    assert all(tag["key"] != source_key for tag in payload["tags"])

    detail1 = client.get(f"/api/news/{item1['id']}/detail").get_json()
    item1_ai_tags = [tag for tag in detail1["market_tags"] if tag["key"] == "AI"]
    assert len(item1_ai_tags) == 1
    assert item1_ai_tags[0]["direction"] == "bearish"

    detail2 = client.get(f"/api/news/{item2['id']}/detail").get_json()
    item2_ai_tags = [tag for tag in detail2["market_tags"] if tag["key"] == "AI"]
    assert len(item2_ai_tags) == 1
    assert item2_ai_tags[0]["direction"] == "bullish"

    tag_detail = client.get("/api/market-trends/tag-detail?tag=AI").get_json()
    assert tag_detail["ok"] is True
    assert tag_detail["tag_key"] == "AI"
    assert any(note["tag_key"] == "AI" for note in tag_detail["trend_notes"])

    with app_module.db_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM market_tag_definitions WHERE key=?", (source_key,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM article_market_tags WHERE tag=?", (source_key,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM market_trend_notes WHERE tag=?", (source_key,)).fetchone()[0] == 0


def test_market_tag_merge_errors(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-06-02.md").write_text("", encoding="utf-8")
    db_path = tmp_path / "news_index.sqlite3"
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()

    same_res = client.post("/api/market-tags/AI/merge", json={"target_key": "AI"})
    assert same_res.status_code == 400
    assert same_res.get_json()["error"] == "same_source_target"

    missing_target = client.post("/api/market-tags/AI/merge", json={"target_key": "missing"})
    assert missing_target.status_code == 404
    assert missing_target.get_json()["error"] == "target_tag_not_found"

    missing_source = client.post("/api/market-tags/missing/merge", json={"target_key": "AI"})
    assert missing_source.status_code == 404
    assert missing_source.get_json()["error"] == "tag_not_found"


def test_market_trend_notes_manual_signal(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-06-02.md").write_text(
        """## Reuters · World（1条）
### [R1](https://www.reuters.com/world/r1)
- 发布时间：2026-06-02 09:00:00
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
    assert client.put(
        f"/api/news/{item['id']}/market-tag",
        json={"tag": "AI", "direction": "bullish"},
    ).status_code == 200

    bullish_note = client.put(
        "/api/market-trends/note",
        json={"date_key": "2026-06-02", "tag_key": "AI", "direction": "bullish", "note": "继续看多 AI 主线"},
    )
    assert bullish_note.status_code == 200
    assert bullish_note.get_json()["trend_note"]["note"] == "继续看多 AI 主线"
    bullish_note_2 = client.put(
        "/api/market-trends/note",
        json={"date_key": "2026-06-02", "tag_key": "AI", "direction": "bullish", "note": "继续加仓，但分批做"},
    )
    assert bullish_note_2.status_code == 200

    bearish_note = client.put(
        "/api/market-trends/note",
        json={"date_key": "2026-06-02", "tag_key": "AI", "direction": "bearish", "note": "短线也要警惕回撤"},
    )
    assert bearish_note.status_code == 200
    assert bearish_note.get_json()["has_trend_note"] == 1

    trends = client.get("/api/market-trends?days=7")
    assert trends.status_code == 200
    ai_row = next(row for row in trends.get_json()["rows"] if row["tag_key"] == "AI")
    slot = next(value for value in ai_row["values"] if value["date"] == "2026-06-02")
    assert slot["bullish"] == 1
    assert slot["bullish_notes"] == 2
    assert slot["bullish_has_item_note"] == 0
    assert slot["bearish"] == 0
    assert slot["bearish_notes"] == 1

    bullish_detail = client.get("/api/market-trends/detail?date=2026-06-02&tag=AI&direction=bullish")
    assert bullish_detail.status_code == 200
    bullish_payload = bullish_detail.get_json()
    assert bullish_payload["total"] == 1
    assert bullish_payload["trend_note_total"] == 2
    assert [note["note"] for note in bullish_payload["trend_notes"]] == ["继续加仓，但分批做", "继续看多 AI 主线"]
    edited_note = bullish_payload["trend_notes"][1]

    bearish_detail = client.get("/api/market-trends/detail?date=2026-06-02&tag=AI&direction=bearish")
    assert bearish_detail.status_code == 200
    bearish_payload = bearish_detail.get_json()
    assert bearish_payload["total"] == 0
    assert bearish_payload["trend_notes"][0]["note"] == "短线也要警惕回撤"

    patch_res = client.patch(
        f"/api/market-trends/note/{edited_note['id']}",
        json={"note": "继续看多 AI 主线，暂不追高"},
    )
    assert patch_res.status_code == 200
    assert patch_res.get_json()["trend_note"]["note"] == "继续看多 AI 主线，暂不追高"

    delete_res = client.delete(f"/api/market-trends/note/{bearish_payload['trend_notes'][0]['id']}")
    assert delete_res.status_code == 200
    assert delete_res.get_json()["has_trend_note"] == 0


def test_market_trend_tag_detail_overview(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-06-03.md").write_text(
        """## Reuters · World（2条）
### [R1](https://www.reuters.com/world/r1)
- 发布时间：2026-06-03 09:00:00
### [R2](https://www.reuters.com/world/r2)
- 发布时间：2026-06-02 10:00:00
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
    r1 = next(x for x in items if x["title"] == "R1")
    r2 = next(x for x in items if x["title"] == "R2")

    assert client.put(
        f"/api/news/{r1['id']}/market-tag",
        json={"tag": "AI", "direction": "bullish"},
    ).status_code == 200
    assert client.put(
        f"/api/news/{r2['id']}/market-tag",
        json={"tag": "AI", "direction": "bearish"},
    ).status_code == 200
    assert client.put(
        f"/api/news/{r2['id']}/note",
        json={"note": "旧新闻想法"},
    ).status_code == 200

    assert client.put(
        "/api/market-trends/note",
        json={"date_key": "2026-06-03", "tag_key": "AI", "direction": "bullish", "note": "最新看多想法"},
    ).status_code == 200
    assert client.put(
        "/api/market-trends/note",
        json={"date_key": "2026-06-01", "tag_key": "AI", "direction": "bearish", "note": "更早看空想法"},
    ).status_code == 200

    detail = client.get("/api/market-trends/tag-detail?tag=AI")
    assert detail.status_code == 200
    payload = detail.get_json()
    assert payload["ok"] is True
    assert payload["view"] == "tag"
    assert payload["tag_key"] == "AI"
    assert payload["item_total"] == 2
    assert payload["trend_note_total"] == 2
    assert [item["title"] for item in payload["items"]] == ["R1", "R2"]
    assert [item["direction"] for item in payload["items"]] == ["bullish", "bearish"]
    assert [note["date_key"] for note in payload["trend_notes"]] == ["2026-06-03", "2026-06-01"]
    assert payload["items"][1]["note"]["note"] == "旧新闻想法"


def test_market_workbench_overview_and_tag_feed(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-06-03.md").write_text(
        """## Reuters · World（3条）
### [R1](https://www.reuters.com/world/r1)
- 发布时间：2026-06-03 09:00:00
### [R2](https://www.reuters.com/world/r2)
- 发布时间：2026-06-02 10:00:00
### [R3](https://www.reuters.com/world/r3)
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
    r1 = next(x for x in items if x["title"] == "R1")
    r2 = next(x for x in items if x["title"] == "R2")
    r3 = next(x for x in items if x["title"] == "R3")

    assert client.put(f"/api/news/{r1['id']}/market-tag", json={"tag": "AI", "direction": "bullish"}).status_code == 200
    assert client.put(f"/api/news/{r2['id']}/market-tag", json={"tag": "AI", "direction": "bearish"}).status_code == 200
    assert client.put(f"/api/news/{r2['id']}/note", json={"note": "这条属于我的板块想法"}).status_code == 200
    assert client.put(f"/api/news/{r3['id']}/market-tag", json={"tag": "存储", "direction": "bullish"}).status_code == 200
    assert client.put(
        "/api/market-trends/note",
        json={"date_key": "2026-06-03", "tag_key": "AI", "direction": "bullish", "note": "独立看多 AI"},
    ).status_code == 200

    overview = client.get("/api/market-workbench?content_filter=all&per=20")
    assert overview.status_code == 200
    overview_payload = overview.get_json()
    assert overview_payload["mode"] == "all"
    assert overview_payload["tags"]
    assert [item["entry_type"] for item in overview_payload["items"]] == ["trend_note", "news", "news", "news"]
    assert overview_payload["items"][0]["idea_type"] == "trend_note"
    assert [item["title"] for item in overview_payload["items"][1:]] == ["R1", "R2", "R3"]

    overview_ideas = client.get("/api/market-workbench?content_filter=ideas&per=20").get_json()
    assert [item["entry_type"] for item in overview_ideas["items"]] == ["trend_note", "news"]
    assert overview_ideas["items"][1]["title"] == "R2"

    overview_bullish = client.get("/api/market-workbench?content_filter=bullish&per=20").get_json()
    assert [item["entry_type"] for item in overview_bullish["items"]] == ["trend_note", "news", "news"]
    assert [item["title"] for item in overview_bullish["items"][1:]] == ["R1", "R3"]

    overview_bearish = client.get("/api/market-workbench?content_filter=bearish&per=20").get_json()
    assert [item["entry_type"] for item in overview_bearish["items"]] == ["news"]
    assert overview_bearish["items"][0]["title"] == "R2"

    tag_feed = client.get("/api/market-workbench?tag=AI&content_filter=all&per=20")
    assert tag_feed.status_code == 200
    tag_payload = tag_feed.get_json()
    assert tag_payload["mode"] == "tag"
    assert tag_payload["selected_tag"]["key"] == "AI"
    assert [item["entry_type"] for item in tag_payload["items"]] == ["trend_note", "news", "news"]
    assert tag_payload["items"][0]["idea_type"] == "trend_note"
    assert tag_payload["items"][1]["title"] == "R1"
    assert tag_payload["items"][2]["title"] == "R2"

    ideas_only = client.get("/api/market-workbench?tag=AI&content_filter=ideas&per=20").get_json()
    assert [item["entry_type"] for item in ideas_only["items"]] == ["trend_note", "news"]
    assert ideas_only["items"][1]["title"] == "R2"

    bullish_only = client.get("/api/market-workbench?tag=AI&content_filter=bullish&per=20").get_json()
    assert [item["entry_type"] for item in bullish_only["items"]] == ["trend_note", "news"]
    assert bullish_only["items"][1]["title"] == "R1"

    bearish_only = client.get("/api/market-workbench?tag=AI&content_filter=bearish&per=20").get_json()
    assert [item["entry_type"] for item in bearish_only["items"]] == ["news"]
    assert bearish_only["items"][0]["title"] == "R2"


def test_market_trend_note_patch_date_tag_direction(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-06-02.md").write_text(
        """## Reuters · World（1条）
### [API 测试新闻](https://www.reuters.com/world/r1)
- 发布时间：2026-06-02 09:00:00
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
    assert client.put(
        f"/api/news/{item['id']}/market-tag",
        json={"tag": "AI", "direction": "bullish"},
    ).status_code == 200

    # Create initial note: AI bullish on 2026-06-02
    create_res = client.put(
        "/api/market-trends/note",
        json={"date_key": "2026-06-02", "tag_key": "AI", "direction": "bullish", "note": "初始想法"},
    )
    assert create_res.status_code == 200
    note_id = create_res.get_json()["trend_note"]["id"]

    # Backward compatibility: only update note text
    patch_only_note = client.patch(
        f"/api/market-trends/note/{note_id}",
        json={"note": "仅更新正文"},
    )
    assert patch_only_note.status_code == 200
    data = patch_only_note.get_json()
    assert data["trend_note"]["note"] == "仅更新正文"
    assert data["date"] == "2026-06-02"
    assert data["tag_key"] == "AI"
    assert data["direction"] == "bullish"

    # Update all four fields: move to new date/tag/direction
    patch_all = client.patch(
        f"/api/market-trends/note/{note_id}",
        json={
            "note": "迁移后的想法",
            "date_key": "2026-06-03",
            "tag_key": "AI",
            "direction": "bearish",
        },
    )
    assert patch_all.status_code == 200
    data = patch_all.get_json()
    assert data["trend_note"]["note"] == "迁移后的想法"
    assert data["date"] == "2026-06-03"
    assert data["direction"] == "bearish"

    # Old group should be empty
    old_detail = client.get("/api/market-trends/detail?date=2026-06-02&tag=AI&direction=bullish")
    assert old_detail.status_code == 200
    assert old_detail.get_json()["trend_note_total"] == 0

    # New group should contain the migrated note
    new_detail = client.get("/api/market-trends/detail?date=2026-06-03&tag=AI&direction=bearish")
    assert new_detail.status_code == 200
    new_payload = new_detail.get_json()
    assert new_payload["trend_note_total"] == 1
    assert new_payload["trend_notes"][0]["note"] == "迁移后的想法"

    # Invalid direction should be rejected
    invalid = client.patch(
        f"/api/market-trends/note/{note_id}",
        json={"note": "无效方向", "direction": "sideways"},
    )
    assert invalid.status_code == 400
    assert invalid.get_json()["ok"] is False

    # Invalid tag should be rejected
    invalid_tag = client.patch(
        f"/api/market-trends/note/{note_id}",
        json={"note": "无效板块", "tag_key": "Unknown"},
    )
    assert invalid_tag.status_code == 400
    assert invalid_tag.get_json()["ok"] is False

    # Empty note should be rejected
    empty = client.patch(
        f"/api/market-trends/note/{note_id}",
        json={"note": "   "},
    )
    assert empty.status_code == 400
    assert empty.get_json()["ok"] is False

    # Invalid date format should be rejected
    invalid_date_fmt = client.patch(
        f"/api/market-trends/note/{note_id}",
        json={"note": "日期格式非法", "date_key": "not-a-date"},
    )
    assert invalid_date_fmt.status_code == 400
    assert invalid_date_fmt.get_json()["ok"] is False

    # Invalid calendar date should be rejected
    invalid_calendar = client.patch(
        f"/api/market-trends/note/{note_id}",
        json={"note": "日历日期不存在", "date_key": "2026-02-30"},
    )
    assert invalid_calendar.status_code == 400
    assert invalid_calendar.get_json()["ok"] is False

    # Valid date update should be accepted
    valid_date = client.patch(
        f"/api/market-trends/note/{note_id}",
        json={"note": "日期有效", "date_key": "2026-06-01"},
    )
    assert valid_date.status_code == 200
    data = valid_date.get_json()
    assert data["date"] == "2026-06-01"
    assert data["trend_note"]["note"] == "日期有效"
