from __future__ import annotations

from nr import database

import importlib
import json
import sqlite3
from pathlib import Path

import pytest

from api_fixtures import _isolate_app_settings
from legacy_news_seed import seed_news
from nr import highlights


def test_article_highlights_api_persists_validates_and_reanchors(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年8月"
    daily_dir.mkdir(parents=True)
    first_url = "https://example.com/highlight-one"
    second_url = "https://example.com/highlight-two"
    (daily_dir / "dailyFreshNews_2026-08-07.md").write_text(
        f"""## Reuters · World（2条）
### [高亮新闻一]({first_url})
- 发布时间：2026-08-07 09:00:00
### [高亮新闻二]({second_url})
- 发布时间：2026-08-07 08:00:00
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
    items = client.get("/api/news?per=20&read_filter=all").get_json()["items"]
    by_url = {item["url"]: item for item in items}
    first = by_url[first_url]
    second = by_url[second_url]
    body = "第一句中文。\n第二句需要标记。\n第三句。"
    ts = database.now_ts()
    start = body.index("第二句需要标记。")
    end = start + len("第二句需要标记。")

    with app_module.db_conn() as conn:
        conn.execute(
            """
            INSERT INTO article_details(
              url, source, title, author, published_at, content, content_length,
              raw_json, fetched_at, updated_at
            ) VALUES (?, 'Reuters', '高亮新闻一', '作者', '2026-08-07', ?, ?, '{}', ?, ?)
            """,
            (first_url, "English source body", len("English source body"), ts, ts),
        )
        conn.execute(
            """
            INSERT INTO article_ai(
              url, model, key_points_zh, conclusion_zh, body_zh, raw_json,
              generated_at, updated_at
            ) VALUES (?, 'test', ?, '最后总结段', ?, '{}', ?, ?)
            """,
            (first_url, json.dumps(["要点一内容", "第二个要点"], ensure_ascii=False), body, ts, ts),
        )
        conn.execute(
            """
            INSERT INTO ai_jobs(url, status, attempts, finished_at, updated_at)
            VALUES (?, 'success', 1, ?, ?)
            """,
            (first_url, ts, ts),
        )
        conn.commit()

    detail = client.get(f"/api/news/{first['id']}/detail").get_json()
    assert detail["highlight_body_ready"] is True
    assert detail["highlights"] == []
    assert detail["highlight_summary"] == {"active": 0, "orphan": 0, "total": 0}

    created = client.post(
        f"/api/news/{first['id']}/highlights",
        json={
            "body_kind": "ai_zh",
            "start_offset": start,
            "end_offset": end,
            "selected_text": "第二句需要标记。",
        },
    )
    assert created.status_code == 201
    created_payload = created.get_json()
    assert created_payload["highlight"]["status"] == "active"
    highlight_id = created_payload["highlight"]["id"]
    assert created_payload["highlight"]["color"] == "yellow"
    assert created_payload["highlight"]["annotation_text"] == ""
    assert created_payload["highlight"]["content_hash"] == highlights.article_highlight_content_hash(body)

    annotation_get = client.get(
        f"/api/news/{first['id']}/highlights/{highlight_id}/annotation"
    )
    assert annotation_get.status_code == 200
    assert annotation_get.get_json()["annotation_text"] == ""
    annotation_saved = client.put(
        f"/api/news/{first['id']}/highlights/{highlight_id}/annotation",
        json={"annotation_text": "  先记录这段判断  "},
    )
    assert annotation_saved.status_code == 200
    assert annotation_saved.get_json()["highlight"]["annotation_text"] == "先记录这段判断"
    assert annotation_saved.get_json()["highlight"]["updated_at"]
    annotation_edited = client.put(
        f"/api/news/{first['id']}/highlights/{highlight_id}/annotation",
        json={"annotation_text": "修改后的纯文本批注"},
    )
    assert annotation_edited.status_code == 200
    assert annotation_edited.get_json()["highlight"]["annotation_text"] == "修改后的纯文本批注"
    invalid_annotation_type = client.put(
        f"/api/news/{first['id']}/highlights/{highlight_id}/annotation",
        json={"annotation_text": ["不允许"]},
    )
    assert invalid_annotation_type.status_code == 400
    assert invalid_annotation_type.get_json()["error"] == "invalid_annotation_type"
    empty_annotation = client.put(
        f"/api/news/{first['id']}/highlights/{highlight_id}/annotation",
        json={"annotation_text": "  \n  "},
    )
    assert empty_annotation.status_code == 400
    assert empty_annotation.get_json()["error"] == "empty_annotation"
    too_long_annotation = client.put(
        f"/api/news/{first['id']}/highlights/{highlight_id}/annotation",
        json={"annotation_text": "字" * 2001},
    )
    assert too_long_annotation.status_code == 400
    assert too_long_annotation.get_json()["error"] == "annotation_too_long"

    for color in ("red", "blue", "green"):
        recolored = client.patch(
            f"/api/news/{first['id']}/highlights/{highlight_id}",
            json={"color": color},
        )
        assert recolored.status_code == 200
        assert recolored.get_json()["highlight"]["color"] == color

    same_hash = client.get(f"/api/news/{first['id']}/highlights").get_json()
    assert same_hash["highlights"][0]["resolved_start_offset"] == start
    assert same_hash["highlights"][0]["resolved_end_offset"] == end
    assert same_hash["highlights"][0]["color"] == "green"
    assert same_hash["highlights"][0]["annotation_text"] == "修改后的纯文本批注"

    annotation_cleared = client.delete(
        f"/api/news/{first['id']}/highlights/{highlight_id}/annotation"
    )
    assert annotation_cleared.status_code == 200
    assert annotation_cleared.get_json()["highlight"]["id"] == highlight_id
    assert annotation_cleared.get_json()["highlight"]["annotation_text"] == ""
    assert client.get(
        f"/api/news/{first['id']}/highlights/{highlight_id}/annotation"
    ).get_json()["annotation_text"] == ""

    points_body = "要点一内容第二个要点"
    points_start = points_body.index("第二个要点")
    points_end = points_start + len("第二个要点")
    points_created = client.post(
        f"/api/news/{first['id']}/highlights",
        json={
            "body_kind": "ai_points_zh",
            "color": "blue",
            "start_offset": points_start,
            "end_offset": points_end,
            "selected_text": "第二个要点",
        },
    )
    assert points_created.status_code == 201
    points_payload = points_created.get_json()
    assert points_payload["highlight"]["body_kind"] == "ai_points_zh"
    assert points_payload["highlight"]["color"] == "blue"
    points_id = points_payload["highlight"]["id"]
    assert points_payload["highlight_surfaces"]["ai_points_zh"]["highlights"][0]["status"] == "active"
    points_annotation = client.put(
        f"/api/news/{first['id']}/highlights/{points_id}/annotation",
        json={"annotation_text": "要点批注"},
    )
    assert points_annotation.status_code == 200
    assert points_annotation.get_json()["highlight"]["annotation_text"] == "要点批注"
    points_list = client.get(f"/api/news/{first['id']}/highlights?body_kind=ai_points_zh").get_json()
    assert points_list["body_kind"] == "ai_points_zh"
    assert points_list["highlights"][0]["color"] == "blue"
    with app_module.db_conn() as conn:
        conn.execute(
            "UPDATE article_ai SET key_points_zh=?, updated_at=? WHERE url=?",
            (json.dumps(["前置要点", "要点一内容", "第二个要点"], ensure_ascii=False), database.now_ts(), first_url),
        )
        conn.commit()
    points_reanchored = client.get(f"/api/news/{first['id']}/highlights?body_kind=ai_points_zh").get_json()
    assert points_reanchored["highlight_summary"] == {"active": 1, "orphan": 0, "total": 1}
    assert points_reanchored["highlights"][0]["resolved_start_offset"] == len("前置要点要点一内容")

    conclusion_body = "最后总结段"
    conclusion_start = 0
    conclusion_end = len(conclusion_body)
    conclusion_created = client.post(
        f"/api/news/{first['id']}/highlights",
        json={
            "body_kind": "ai_conclusion_zh",
            "color": "pink",
            "start_offset": conclusion_start,
            "end_offset": conclusion_end,
            "selected_text": conclusion_body,
        },
    )
    assert conclusion_created.status_code == 201
    conclusion_payload = conclusion_created.get_json()
    assert conclusion_payload["highlight"]["body_kind"] == "ai_conclusion_zh"
    assert conclusion_payload["highlight"]["color"] == "pink"
    conclusion_id = conclusion_payload["highlight"]["id"]
    assert conclusion_payload["highlight_surfaces"]["ai_conclusion_zh"]["highlights"][0]["status"] == "active"
    conclusion_annotation = client.put(
        f"/api/news/{first['id']}/highlights/{conclusion_id}/annotation",
        json={"annotation_text": "总结批注"},
    )
    assert conclusion_annotation.status_code == 200
    assert conclusion_annotation.get_json()["highlight"]["annotation_text"] == "总结批注"
    conclusion_list = client.get(
        f"/api/news/{first['id']}/highlights?body_kind=ai_conclusion_zh"
    ).get_json()
    assert conclusion_list["body_kind"] == "ai_conclusion_zh"
    assert conclusion_list["highlights"][0]["selected_text"] == conclusion_body
    deleted_annotated_highlight = client.delete(
        f"/api/news/{first['id']}/highlights/{conclusion_id}"
    )
    assert deleted_annotated_highlight.status_code == 200
    assert client.get(
        f"/api/news/{first['id']}/highlights/{conclusion_id}/annotation"
    ).status_code == 404

    invalid_color = client.post(
        f"/api/news/{first['id']}/highlights",
        json={
            "body_kind": "ai_points_zh",
            "color": "rainbow",
            "start_offset": 0,
            "end_offset": 2,
            "selected_text": "要点",
        },
    )
    assert invalid_color.status_code == 400
    assert invalid_color.get_json()["error"] == "invalid_color"

    invalid_recolor = client.patch(
        f"/api/news/{first['id']}/highlights/{highlight_id}",
        json={"color": "rainbow"},
    )
    assert invalid_recolor.status_code == 400
    assert invalid_recolor.get_json()["error"] == "invalid_color"

    invalid_body_kind = client.post(
        f"/api/news/{first['id']}/highlights",
        json={
            "body_kind": ["ai_zh"],
            "start_offset": 0,
            "end_offset": 2,
            "selected_text": "要点",
        },
    )
    assert invalid_body_kind.status_code == 400
    assert invalid_body_kind.get_json()["error"] == "invalid_body_kind"

    invalid_offset = client.post(
        f"/api/news/{first['id']}/highlights",
        json={"start_offset": -1, "end_offset": 2, "selected_text": "第一"},
    )
    assert invalid_offset.status_code == 400
    assert invalid_offset.get_json()["error"] == "invalid_offset"
    mismatch = client.post(
        f"/api/news/{first['id']}/highlights",
        json={"start_offset": start, "end_offset": end, "selected_text": "错误选区"},
    )
    assert mismatch.status_code == 400
    assert mismatch.get_json()["error"] == "selected_text_mismatch"
    overlap_text = body[start + 1 : end + 1]
    overlap = client.post(
        f"/api/news/{first['id']}/highlights",
        json={
            "start_offset": start + 1,
            "end_offset": end + 1,
            "selected_text": overlap_text,
        },
    )
    assert overlap.status_code == 409
    assert overlap.get_json()["error"] == "highlight_overlap"

    not_ready = client.post(
        f"/api/news/{second['id']}/highlights",
        json={"start_offset": 0, "end_offset": 1, "selected_text": "高"},
    )
    assert not_ready.status_code == 409
    assert not_ready.get_json()["error"] == "body_not_ready"
    cross_news_delete = client.delete(f"/api/news/{second['id']}/highlights/{highlight_id}")
    assert cross_news_delete.status_code == 404
    assert cross_news_delete.get_json()["error"] == "highlight_not_found"
    cross_news_annotation = client.put(
        f"/api/news/{second['id']}/highlights/{highlight_id}/annotation",
        json={"annotation_text": "越权"},
    )
    assert cross_news_annotation.status_code == 404
    assert cross_news_annotation.get_json()["error"] == "highlight_not_found"
    cross_news_recolor = client.patch(
        f"/api/news/{second['id']}/highlights/{highlight_id}",
        json={"color": "pink"},
    )
    assert cross_news_recolor.status_code == 404
    assert cross_news_recolor.get_json()["error"] == "highlight_not_found"

    with app_module.db_conn() as conn:
        conn.execute("SELECT body_zh FROM article_ai WHERE url=?", (first_url,)).fetchone()
        original_ai = conn.execute("SELECT body_zh FROM article_ai WHERE url=?", (first_url,)).fetchone()["body_zh"]
        original_detail = conn.execute("SELECT content FROM article_details WHERE url=?", (first_url,)).fetchone()["content"]
        assert original_ai == body
        assert original_detail == "English source body"

    deleted = client.delete(f"/api/news/{first['id']}/highlights/{highlight_id}")
    assert deleted.status_code == 200
    assert deleted.get_json()["highlights"] == []
    deleted_annotation = client.get(
        f"/api/news/{first['id']}/highlights/{highlight_id}/annotation"
    )
    assert deleted_annotation.status_code == 404

    reanchor_body = "甲乙丙丁\n唯一句子\n收尾"
    reanchor_start = reanchor_body.index("唯一句子")
    reanchor_end = reanchor_start + len("唯一句子")
    with app_module.db_conn() as conn:
        conn.execute(
            "UPDATE article_ai SET body_zh=?, updated_at=? WHERE url=?",
            (reanchor_body, database.now_ts(), first_url),
        )
        conn.execute(
            "UPDATE ai_jobs SET status='success', updated_at=? WHERE url=?",
            (database.now_ts(), first_url),
        )
        conn.commit()
    created = client.post(
        f"/api/news/{first['id']}/highlights",
        json={
            "start_offset": reanchor_start,
            "end_offset": reanchor_end,
            "selected_text": "唯一句子",
        },
    )
    assert created.status_code == 201
    reanchor_id = created.get_json()["highlight"]["id"]
    reanchor_annotation = client.put(
        f"/api/news/{first['id']}/highlights/{reanchor_id}/annotation",
        json={"annotation_text": "重定位后仍应保留"},
    )
    assert reanchor_annotation.status_code == 200
    with app_module.db_conn() as conn:
        conn.execute(
            "UPDATE article_ai SET body_zh=?, updated_at=? WHERE url=?",
            ("前缀\n" + reanchor_body + "\n后缀", database.now_ts(), first_url),
        )
        conn.commit()
    reanchored = client.get(f"/api/news/{first['id']}/highlights").get_json()
    assert reanchored["highlight_summary"] == {"active": 1, "orphan": 0, "total": 1}
    assert reanchored["highlights"][0]["id"] == reanchor_id
    assert reanchored["highlights"][0]["resolved_start_offset"] == len("前缀\n甲乙丙丁\n")
    assert reanchored["highlights"][0]["annotation_text"] == "重定位后仍应保留"

    with app_module.db_conn() as conn:
        conn.execute(
            "UPDATE article_ai SET body_zh=?, updated_at=? WHERE url=?",
            (
                "甲乙丙丁\n唯一句子\n收尾\n甲乙丙丁\n唯一句子\n收尾",
                database.now_ts(),
                first_url,
            ),
        )
        conn.commit()
    ambiguous = client.get(f"/api/news/{first['id']}/highlights").get_json()
    assert ambiguous["highlight_summary"] == {"active": 0, "orphan": 1, "total": 1}
    assert ambiguous["highlights"][0]["status"] == "orphan"

    with app_module.db_conn() as conn:
        conn.execute(
            "UPDATE ai_jobs SET status='running', updated_at=? WHERE url=?",
            (database.now_ts(), first_url),
        )
        conn.commit()
    pending = client.get(f"/api/news/{first['id']}/highlights").get_json()
    assert pending["body_ready"] is False
    assert pending["highlights"] == []


def test_article_highlights_schema_migrates_legacy_body_kind_and_color(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "legacy-highlights.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE article_highlights (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              url TEXT NOT NULL,
              body_kind TEXT NOT NULL DEFAULT 'ai_zh' CHECK (body_kind = 'ai_zh'),
              content_hash TEXT NOT NULL,
              start_offset INTEGER NOT NULL CHECK (start_offset >= 0),
              end_offset INTEGER NOT NULL CHECK (end_offset > start_offset),
              selected_text TEXT NOT NULL CHECK (length(selected_text) > 0),
              prefix TEXT NOT NULL DEFAULT '',
              suffix TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE (url, body_kind, content_hash, start_offset, end_offset)
            );
            INSERT INTO article_highlights(
              url, body_kind, content_hash, start_offset, end_offset,
              selected_text, prefix, suffix, created_at, updated_at
            ) VALUES ('https://example.com/legacy', 'ai_zh', 'hash', 0, 2, '旧', '', '', 'now', 'now');
            """
        )

    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))
    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    with app_module.db_conn() as conn:
        table_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='article_highlights'"
        ).fetchone()[0]
        columns = {row[1] for row in conn.execute("PRAGMA table_info(article_highlights)").fetchall()}
        row = conn.execute("SELECT body_kind, color FROM article_highlights").fetchone()
    assert "ai_points_zh" in table_sql
    assert "ai_conclusion_zh" in table_sql
    assert "twitter_detail" in table_sql
    assert "color" in columns
    assert "annotation_text" in columns
    row = conn.execute("SELECT body_kind, color, annotation_text FROM article_highlights").fetchone()
    assert tuple(row) == ("ai_zh", "yellow", "")


def test_article_highlights_color_migration_preserves_existing_records_and_is_idempotent():
    with sqlite3.connect(":memory:") as conn:
        legacy_schema = (Path(__file__).resolve().parents[1] / "schema.sql").read_text().replace(
            "'yellow', 'red', 'green', 'blue', 'pink'", "'yellow', 'green', 'blue', 'pink'")
        conn.executescript(legacy_schema)
        for index, color in enumerate(("yellow", "green", "blue", "pink"), 1):
            conn.execute("""INSERT INTO article_highlights
                (id,url,body_kind,content_hash,start_offset,end_offset,selected_text,color,annotation_text,prefix,suffix,created_at,updated_at)
                VALUES (?,?,'original','hash',1,3,'原文',?,'既有批注','前','后','created','updated')""",
                (index * 10, f"newsreader-report:legacy-{index}" if index % 2 else f"https://example.com/{index}", color))
        conn.execute("UPDATE sqlite_sequence SET seq=999 WHERE name='article_highlights'")
        before = conn.execute("SELECT * FROM article_highlights ORDER BY id").fetchall()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE article_highlights SET color='red' WHERE id=10")
        highlights.migrate_article_highlights_schema(conn)
        assert conn.execute("SELECT * FROM article_highlights ORDER BY id").fetchall() == before
        statements = []
        conn.set_trace_callback(statements.append)
        highlights.migrate_article_highlights_schema(conn)
        conn.set_trace_callback(None)
        assert not any("ALTER TABLE" in statement.upper() or "DROP TABLE" in statement.upper() for statement in statements)
        assert conn.execute("SELECT * FROM article_highlights ORDER BY id").fetchall() == before
        conn.execute("UPDATE article_highlights SET color='red' WHERE id=10")
        assert conn.execute("SELECT color,annotation_text FROM article_highlights WHERE id=10").fetchone() == ("red", "既有批注")
        inserted = conn.execute("""INSERT INTO article_highlights
            (url,content_hash,start_offset,end_offset,selected_text,color,created_at,updated_at)
            VALUES ('https://example.com/new','hash',0,2,'新增','red','now','now')""")
        assert inserted.lastrowid > 999
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_twitter_detail_highlights_use_stable_article_detail_and_gate_ordinary_news(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "twitter-highlights.sqlite3"
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()
    ts = database.now_ts()
    twitter_id = "twitter-highlight-item"
    twitter_url = "https://x.com/example/status/123"
    missing_id = "twitter-highlight-missing"
    missing_url = "https://x.com/example/status/456"
    ordinary_id = "ordinary-highlight-item"
    ordinary_url = "https://example.com/ordinary-highlight"
    body = "推文开头\n唯一推文片段\n推文结尾"
    selected = "唯一推文片段"
    start = body.index(selected)
    end = start + len(selected)

    with app_module.db_conn() as conn:
        for item_id, url, source_type, title in (
            (twitter_id, twitter_url, "twitter", "推文高亮"),
            (missing_id, missing_url, "twitter", "无正文推文"),
            (ordinary_id, ordinary_url, "rss", "普通新闻"),
        ):
            conn.execute(
                """
                INSERT INTO items(
                  id, source_file, item_order, published_at, date, time,
                  source, source_type, source_name, title, summary, url,
                  created_at, updated_at
                ) VALUES (?, 'test.md', 1, '2026-08-10 10:00', '2026-08-10', '10:00',
                          'test', ?, 'test', ?, '', ?, ?, ?)
                """,
                (item_id, source_type, title, url, ts, ts),
            )
        for url, title, content in (
            (twitter_url, "推文高亮", body),
            (ordinary_url, "普通新闻", "Ordinary source body"),
        ):
            conn.execute(
                """
                INSERT INTO article_details(
                  url, source, title, author, published_at, content, content_length,
                  raw_json, fetched_at, updated_at
                ) VALUES (?, 'test', ?, '', '2026-08-10', ?, ?, '{}', ?, ?)
                """,
                (url, title, content, len(content), ts, ts),
            )
        conn.commit()

    missing = client.post(
        f"/api/news/{missing_id}/highlights",
        json={
            "body_kind": "twitter_detail",
            "start_offset": 0,
            "end_offset": 1,
            "selected_text": "缺",
        },
    )
    assert missing.status_code == 409
    assert missing.get_json()["error"] == "body_not_ready"

    ordinary = client.post(
        f"/api/news/{ordinary_id}/highlights",
        json={
            "body_kind": "twitter_detail",
            "start_offset": 0,
            "end_offset": 8,
            "selected_text": "Ordinary",
        },
    )
    assert ordinary.status_code == 409
    assert ordinary.get_json()["error"] == "body_not_ready"

    created = client.post(
        f"/api/news/{twitter_id}/highlights",
        json={
            "body_kind": "twitter_detail",
            "color": "blue",
            "start_offset": start,
            "end_offset": end,
            "selected_text": selected,
        },
    )
    assert created.status_code == 201
    payload = created.get_json()
    highlight_id = payload["highlight"]["id"]
    assert payload["highlight"]["body_kind"] == "twitter_detail"
    assert payload["highlight"]["color"] == "blue"
    assert payload["highlight_surfaces"]["twitter_detail"]["body_ready"] is True

    overlap = client.post(
        f"/api/news/{twitter_id}/highlights",
        json={
            "body_kind": "twitter_detail",
            "start_offset": start + 1,
            "end_offset": end,
            "selected_text": selected[1:],
        },
    )
    assert overlap.status_code == 409
    assert overlap.get_json()["error"] == "highlight_overlap"

    recolored = client.patch(
        f"/api/news/{twitter_id}/highlights/{highlight_id}",
        json={"color": "pink"},
    )
    assert recolored.status_code == 200
    assert recolored.get_json()["highlight"]["color"] == "pink"

    annotated = client.put(
        f"/api/news/{twitter_id}/highlights/{highlight_id}/annotation",
        json={"annotation_text": "推文批注"},
    )
    assert annotated.status_code == 200
    assert annotated.get_json()["highlight"]["annotation_text"] == "推文批注"

    listed = client.get(
        f"/api/news/{twitter_id}/highlights?body_kind=twitter_detail"
    ).get_json()
    assert listed["body_kind"] == "twitter_detail"
    assert listed["highlights"][0]["annotation_text"] == "推文批注"

    with app_module.db_conn() as conn:
        conn.execute(
            "UPDATE article_details SET content=?, content_length=?, updated_at=? WHERE url=?",
            ("新增前缀\n" + body, len("新增前缀\n" + body), database.now_ts(), twitter_url),
        )
        conn.commit()
    reanchored = client.get(
        f"/api/news/{twitter_id}/highlights?body_kind=twitter_detail"
    ).get_json()
    assert reanchored["highlights"][0]["status"] == "active"
    assert reanchored["highlights"][0]["resolved_start_offset"] == len("新增前缀\n推文开头\n")

    with app_module.db_conn() as conn:
        ambiguous_body = body + "\n" + body
        conn.execute(
            "UPDATE article_details SET content=?, content_length=?, updated_at=? WHERE url=?",
            (ambiguous_body, len(ambiguous_body), database.now_ts(), twitter_url),
        )
        conn.commit()
    orphaned = client.get(
        f"/api/news/{twitter_id}/highlights?body_kind=twitter_detail"
    ).get_json()
    assert orphaned["highlights"][0]["status"] == "orphan"

    deleted = client.delete(f"/api/news/{twitter_id}/highlights/{highlight_id}")
    assert deleted.status_code == 200
    assert deleted.get_json()["highlight_surfaces"]["twitter_detail"]["highlights"] == []
