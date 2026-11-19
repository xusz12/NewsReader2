from __future__ import annotations

from legacy_news_seed import seed_news, confirm_all_read
import importlib
import json
import sqlite3
from pathlib import Path
from api_fixtures import _isolate_app_settings


def test_apply_schema_creates_tracked_topic_tables_and_indexes(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "news_index.sqlite3"
    daily_root = tmp_path / "DailyNews"
    daily_root.mkdir()

    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(daily_root))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    app_module.ensure_db()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "tracked_topics" in tables
        assert "tracked_topic_items" in tables
        assert "tracked_topic_daily_summaries" in tables
        tracked_topic_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(tracked_topics)").fetchall()
        }
        assert "rules_json" in tracked_topic_cols

        indexes = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert "idx_tracked_topics_active_updated_at" in indexes
        assert "idx_tracked_topic_items_topic_hidden" in indexes
        assert "idx_tracked_topic_items_item_id" in indexes
        assert "idx_tracked_topic_daily_summaries_topic_date" in indexes
    finally:
        conn.close()


def test_tracked_topics_backfill_incremental_and_overrides(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    old_daily = tmp_path / "DailyNews" / "2025年12月"
    old_daily.mkdir(parents=True)

    (old_daily / "dailyFreshNews_2025-12-01.md").write_text(
        """## Reuters · World（1条）
### [乌克兰俄罗斯谈判旧战况](https://example.com/ru-old)
- 发布时间：2025-12-01 09:00:00
- 摘要：旧战况整理
""",
        encoding="utf-8",
    )
    (daily_dir / "dailyFreshNews_2026-06-20.md").write_text(
        """## Reuters · World（8条）
### [乌克兰无人机袭击俄军机场](https://example.com/ru-now)
- 发布时间：2026-06-20 08:00:00
- 摘要：俄罗斯多个机场遭袭

### [俄罗斯股市观察](https://example.com/russia-market)
- 发布时间：2026-06-20 08:30:00
- 摘要：俄罗斯市场波动

### [俄罗斯方块大赛开幕](https://example.com/tetris)
- 发布时间：2026-06-20 09:00:00
- 摘要：体育游戏活动

### [欧洲防务观察](https://example.com/note-match)
- 发布时间：2026-06-20 09:30:00
- 摘要：欧洲局势观察

### [欧洲防务观察二](https://example.com/summary-match)
- 发布时间：2026-06-20 10:00:00
- 摘要：普通摘要

### [国际局势综述](https://example.com/body-only)
- 发布时间：2026-06-20 10:30:00
- 摘要：普通摘要

### [乌克兰基辅停火进展](https://example.com/all-news-match)
- 发布时间：2026-06-20 10:45:00
- 摘要：俄罗斯与乌克兰讨论停火

### [无关宏观观察](https://example.com/macro)
- 发布时间：2026-06-20 11:00:00
- 摘要：美元指数波动
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

    first_reindex = seed_news(client, json={})
    assert first_reindex.status_code == 200
    assert first_reindex.get_json()["tracked_incremental_matches"] == 0

    items = client.get("/api/news?per=20").get_json()["items"]
    by_title = {item["title"]: item for item in items}
    old_item = by_title["乌克兰俄罗斯谈判旧战况"]
    recent_item = by_title["乌克兰无人机袭击俄军机场"]
    broad_item = by_title["俄罗斯股市观察"]
    exclude_item = by_title["俄罗斯方块大赛开幕"]
    note_match_item = by_title["欧洲防务观察"]
    summary_match_item = by_title["欧洲防务观察二"]
    body_only_item = by_title["国际局势综述"]
    all_news_match_item = by_title["乌克兰基辅停火进展"]
    manual_item = by_title["无关宏观观察"]

    for item in (
        old_item,
        recent_item,
        broad_item,
        exclude_item,
        note_match_item,
        summary_match_item,
        body_only_item,
    ):
        assert client.patch(
            f"/api/news/{item['id']}/state",
            json={"important": True},
        ).status_code == 200

    conn = sqlite3.connect(db_path)
    try:
        ts = "2026-06-20 12:00:00"
        conn.execute(
            """
            INSERT INTO article_notes(url, note, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                note_match_item["url"],
                "乌克兰 无人机 袭击升级",
                ts,
                ts,
            ),
        )
        conn.execute(
            """
            INSERT INTO article_notes(url, note, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                exclude_item["url"],
                "乌克兰 无人机 袭击升级",
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
                summary_match_item["url"],
                "deepseek-chat",
                json.dumps(["乌克兰", "俄罗斯", "基辅"], ensure_ascii=False),
                "袭击升级",
                "补充正文",
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
                body_only_item["url"],
                "deepseek-chat",
                json.dumps([], ensure_ascii=False),
                "",
                "乌克兰 俄罗斯 无人机 袭击",
                "{}",
                ts,
                ts,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    create_topic = client.post(
        "/api/tracked-topics",
        json={
            "title": "俄乌战争",
            "description": "长期观察俄乌事件",
            "core_terms": ["乌克兰", "俄罗斯"],
            "context_terms": ["无人机", "袭击", "谈判", "机场", "基辅"],
            "exclude_terms": ["俄罗斯方块", "旅游", "文学", "体育"],
            "threshold": 6,
            "scope": "all",
            "active": True,
        },
    )
    assert create_topic.status_code == 200
    topic = create_topic.get_json()["topic"]
    topic_id = topic["id"]
    assert topic["rules"]["core_terms"] == ["乌克兰", "俄罗斯"]
    assert topic["rules"]["threshold"] == 6
    assert topic["rules"]["title_weight"] == 1
    assert topic["rules"]["note_weight"] == 1
    assert topic["rules"]["strong_score"] == 1
    assert topic["rules"]["exclude_penalty"] == 1

    all_backfill = client.post(
        f"/api/tracked-topics/{topic_id}/backfill",
        json={"mode": "all_important"},
    )
    assert all_backfill.status_code == 200
    all_payload = all_backfill.get_json()
    assert [item["title"] for item in all_payload["items"]] == [
        "欧洲防务观察二",
        "欧洲防务观察",
        "乌克兰无人机袭击俄军机场",
        "乌克兰俄罗斯谈判旧战况",
    ]
    assert any(
        marker in all_payload["items"][0]["tracked_reason"]
        for marker in ("标题命中", "笔记命中", "摘要命中")
    )
    assert "score=" in all_payload["items"][0]["tracked_reason"]
    assert broad_item["title"] not in [item["title"] for item in all_payload["items"]]
    assert exclude_item["title"] not in [item["title"] for item in all_payload["items"]]
    assert body_only_item["title"] not in [item["title"] for item in all_payload["items"]]
    assert all_news_match_item["title"] not in [item["title"] for item in all_payload["items"]]

    all_news_backfill = client.post(
        f"/api/tracked-topics/{topic_id}/backfill",
        json={"mode": "all_news"},
    )
    assert all_news_backfill.status_code == 200
    assert [item["title"] for item in all_news_backfill.get_json()["items"]] == [
        "乌克兰基辅停火进展",
        "欧洲防务观察二",
        "欧洲防务观察",
        "乌克兰无人机袭击俄军机场",
        "乌克兰俄罗斯谈判旧战况",
    ]

    hide_recent = client.patch(
        f"/api/tracked-topics/{topic_id}/items/{recent_item['id']}",
        json={"hidden": True},
    )
    assert hide_recent.status_code == 200

    no_revive = client.post(
        f"/api/tracked-topics/{topic_id}/backfill",
        json={"mode": "all_news"},
    )
    assert no_revive.status_code == 200
    assert [item["title"] for item in no_revive.get_json()["items"]] == [
        "乌克兰基辅停火进展",
        "欧洲防务观察二",
        "欧洲防务观察",
        "乌克兰俄罗斯谈判旧战况",
    ]

    manual_add = client.post(
        f"/api/tracked-topics/{topic_id}/items",
        json={"item_id": manual_item["id"]},
    )
    assert manual_add.status_code == 200

    after_manual = client.get(f"/api/tracked-topics/{topic_id}/items")
    assert after_manual.status_code == 200
    assert [item["title"] for item in after_manual.get_json()["items"]] == [
        "无关宏观观察",
        "乌克兰基辅停火进展",
        "欧洲防务观察二",
        "欧洲防务观察",
        "乌克兰俄罗斯谈判旧战况",
    ]

    update_topic = client.patch(
        f"/api/tracked-topics/{topic_id}",
        json={
            "core_terms": ["乌克兰", "俄罗斯"],
            "context_terms": ["停火"],
            "threshold": 6,
        },
    )
    assert update_topic.status_code == 200

    recomputed = client.post(
        f"/api/tracked-topics/{topic_id}/backfill",
        json={"mode": "all_news"},
    )
    assert recomputed.status_code == 200
    recomputed_titles = [item["title"] for item in recomputed.get_json()["items"]]
    assert recomputed_titles == [
        "无关宏观观察",
        "乌克兰基辅停火进展",
    ]

    (daily_dir / "dailyFreshNews_2026-06-21.md").write_text(
        """## Reuters · World（1条）
### [乌克兰停火新消息](https://example.com/ru-new)
- 发布时间：2026-06-21 11:00:00
- 摘要：俄罗斯与乌克兰回应停火
""",
        encoding="utf-8",
    )
    second_reindex = seed_news(client, json={})
    assert second_reindex.status_code == 200
    assert second_reindex.get_json()["tracked_incremental_matches"] == 1

    final_timeline = client.get(f"/api/tracked-topics/{topic_id}/items").get_json()
    assert [item["title"] for item in final_timeline["items"]] == [
        "乌克兰停火新消息",
        "无关宏观观察",
        "乌克兰基辅停火进展",
    ]
    assert final_timeline["items"][1]["tracked_match_method"] == "manual"
    assert "标题命中" in final_timeline["items"][0]["tracked_reason"]

    custom_weight_topic = client.post(
        "/api/tracked-topics",
        json={
            "title": "笔记权重测试",
            "core_terms": ["乌克兰"],
            "context_terms": ["袭击"],
            "exclude_terms": ["俄罗斯方块"],
            "threshold": 7,
            "note_weight": 1.5,
            "scope": "all",
            "active": False,
        },
    )
    assert custom_weight_topic.status_code == 200
    custom_topic = custom_weight_topic.get_json()["topic"]
    assert custom_topic["rules"]["note_weight"] == 1.5
    custom_topic_id = custom_topic["id"]

    custom_backfill = client.post(
        f"/api/tracked-topics/{custom_topic_id}/backfill",
        json={"mode": "all_important"},
    )
    assert custom_backfill.status_code == 200
    custom_items = custom_backfill.get_json()["items"]
    assert [item["title"] for item in custom_items] == ["欧洲防务观察"]
    assert custom_items[0]["tracked_score"] == 7.5
    assert "笔记命中" in custom_items[0]["tracked_reason"]
    assert "score=7.5" in custom_items[0]["tracked_reason"]

    zero_weight_update = client.patch(
        f"/api/tracked-topics/{custom_topic_id}",
        json={
            "title": custom_topic["title"],
            "description": custom_topic["description"],
            "core_terms": ["乌克兰"],
            "context_terms": ["袭击"],
            "exclude_terms": ["俄罗斯方块"],
            "threshold": 7,
            "note_weight": 0,
            "scope": "all",
            "active": False,
        },
    )
    assert zero_weight_update.status_code == 200
    assert zero_weight_update.get_json()["topic"]["rules"]["note_weight"] == 0

    zero_weight_backfill = client.post(
        f"/api/tracked-topics/{custom_topic_id}/backfill",
        json={"mode": "all_important"},
    )
    assert zero_weight_backfill.status_code == 200
    assert zero_weight_backfill.get_json()["items"] == []

    detail = client.get(f"/api/news/{manual_item['id']}/detail")
    assert detail.status_code == 200
    assert detail.get_json()["tracked_topic_choices"][0]["title"] == "俄乌战争"


def test_tracked_required_terms_gate_and_backfill_preserves_manual_hidden(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-06-20.md").write_text(
        """## Reuters · World（4条）
### [霍尔木兹海峡美伊战争升级](https://example.com/hormuz)
- 发布时间：2026-06-20 08:00:00
- 摘要：局势升级

### [德黑兰美伊战争升级](https://example.com/tehran)
- 发布时间：2026-06-20 08:30:00
- 摘要：局势升级

### [波斯湾美伊战争观察](https://example.com/gulf)
- 发布时间：2026-06-20 09:00:00
- 摘要：局势观察

### [霍尔木兹海峡美伊演习升级](https://example.com/drill)
- 发布时间：2026-06-20 09:30:00
- 摘要：演习升级
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
    by_title = {item["title"]: item for item in items}
    hormuz_item = by_title["霍尔木兹海峡美伊战争升级"]
    tehran_item = by_title["德黑兰美伊战争升级"]
    gulf_item = by_title["波斯湾美伊战争观察"]
    drill_item = by_title["霍尔木兹海峡美伊演习升级"]

    for item in (hormuz_item, tehran_item, gulf_item, drill_item):
        assert client.patch(f"/api/news/{item['id']}/state", json={"important": True}).status_code == 200

    create_topic = client.post(
        "/api/tracked-topics",
        json={
            "title": "美伊战争",
            "core_terms": ["美伊"],
            "context_terms": ["战争", "升级"],
            "exclude_terms": ["演习"],
            "required_terms": ["霍尔木兹"],
            "threshold": 6,
            "scope": "all",
            "active": True,
        },
    )
    assert create_topic.status_code == 200
    topic_id = create_topic.get_json()["topic"]["id"]

    first_backfill = client.post(
        f"/api/tracked-topics/{topic_id}/backfill",
        json={"mode": "all_important"},
    )
    assert first_backfill.status_code == 200
    first_items = first_backfill.get_json()["items"]
    assert [item["title"] for item in first_items] == ["霍尔木兹海峡美伊战争升级"]
    assert "必要词命中：霍尔木兹" in first_items[0]["tracked_reason"]

    hide_hormuz = client.patch(
        f"/api/tracked-topics/{topic_id}/items/{hormuz_item['id']}",
        json={"hidden": True},
    )
    assert hide_hormuz.status_code == 200

    manual_add = client.post(
        f"/api/tracked-topics/{topic_id}/items",
        json={"item_id": gulf_item["id"]},
    )
    assert manual_add.status_code == 200

    update_topic = client.patch(
        f"/api/tracked-topics/{topic_id}",
        json={
            "title": "美伊战争",
            "core_terms": ["美伊"],
            "context_terms": ["战争", "升级"],
            "exclude_terms": ["演习"],
            "required_terms": ["霍尔木兹", "德黑兰"],
            "threshold": 6,
            "scope": "all",
            "active": True,
        },
    )
    assert update_topic.status_code == 200

    second_backfill = client.post(
        f"/api/tracked-topics/{topic_id}/backfill",
        json={"mode": "all_important"},
    )
    assert second_backfill.status_code == 200
    second_items = second_backfill.get_json()["items"]
    assert [item["title"] for item in second_items] == [
        "波斯湾美伊战争观察",
        "德黑兰美伊战争升级",
    ]
    assert second_items[0]["tracked_match_method"] == "manual"
    assert "必要词命中：德黑兰" in second_items[1]["tracked_reason"]
    assert hormuz_item["title"] not in [item["title"] for item in second_items]
    assert drill_item["title"] not in [item["title"] for item in second_items]


def test_tracked_topic_manual_add_trend_note_rejected(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-06-20.md").write_text(
        """## Reuters · World（1条）
### [普通新闻](https://example.com/plain)
- 发布时间：2026-06-20 09:00:00
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

    create_tag = client.post("/api/market-tags", json={"display_name": "AI 跟踪测试"})
    assert create_tag.status_code == 200
    tag_key = create_tag.get_json()["tag"]["key"]
    note_res = client.put(
        "/api/market-trends/note",
        json={"date_key": "2026-06-20", "tag_key": tag_key, "direction": "bullish", "note": "AI 独立趋势判断"},
    )
    assert note_res.status_code == 200
    note_id = note_res.get_json()["trend_note"]["id"]

    topic_res = client.post(
        "/api/tracked-topics",
        json={
            "title": "AI",
            "strong_phrases": ["AI"],
            "threshold": 6,
            "scope": "all",
            "active": True,
        },
    )
    assert topic_res.status_code == 200
    topic_id = topic_res.get_json()["topic"]["id"]

    add_res = client.post(
        f"/api/tracked-topics/{topic_id}/items",
        json={"item_id": f"trend_note:{note_id}"},
    )
    assert add_res.status_code == 400
    assert add_res.get_json()["error"] == "invalid_item_id"
