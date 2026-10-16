"""Original SQLite layouts must survive startup and either DailyNews input format."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

import app
from scanner import reindex

ROOT = Path(__file__).resolve().parents[1]
STAMP = "2026-05-25 09:30:00"
URL = "https://example.com/original-news"
ITEM_ID = hashlib.sha256(f"2026-05-25|{URL}".encode()).hexdigest()


def seed_original_database(path: Path, source_file: str) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript((ROOT / "schema.sql").read_text())
        # Original layouts before the ingest metadata and favorite columns existed.
        conn.executescript("""
            DROP TABLE source_files;
            CREATE TABLE source_files (
                path TEXT PRIMARY KEY, mtime REAL NOT NULL, size INTEGER NOT NULL,
                last_scanned_at TEXT NOT NULL, item_count INTEGER DEFAULT 0
            );
            DROP TABLE item_state;
            CREATE TABLE item_state (
                item_id TEXT PRIMARY KEY, bookmarked INTEGER DEFAULT 0,
                skipped INTEGER DEFAULT 0, read_at TEXT, important_at TEXT,
                read_later_at TEXT, updated_at TEXT NOT NULL
            );
        """)
        conn.execute(
            "INSERT INTO items(id,source_file,item_order,published_at,date,time,source,title,url,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ITEM_ID, source_file, 1, "2026-05-25 08:00", "2026-05-25", "08:00", "Reuters · World", "旧库新闻", URL, STAMP, STAMP),
        )
        conn.execute("INSERT INTO item_state VALUES (?,1,0,?,?,?,?)", (ITEM_ID, STAMP, STAMP, STAMP, STAMP))
        conn.execute("INSERT INTO article_details(url,content,fetched_at,updated_at) VALUES (?,?,?,?)", (URL, "Original cached article", STAMP, STAMP))
        conn.execute("INSERT INTO article_ai(url,key_points_zh,conclusion_zh,body_zh,updated_at) VALUES (?,?,?,?,?)", (URL, '["历史要点"]', "历史结论", "历史中文正文", STAMP))
        conn.execute("INSERT INTO article_notes VALUES (?,?,?,?)", (URL, "原有想法，保留内容和时间", STAMP, STAMP))
        conn.execute("INSERT INTO news_reminders(item_id,item_title_snapshot,item_url_snapshot,event_title,event_date,remind_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)", (ITEM_ID, "旧库新闻", URL, "历史提醒", "2026-05-26", "2026-05-26 09:00:00", STAMP, STAMP))


@pytest.mark.parametrize("input_mode", ["markdown_only", "sidecar_json", "markdown_fallback"])
def test_existing_database_and_daily_input_keep_history(tmp_path: Path, monkeypatch, input_mode: str):
    daily_root = tmp_path / "DailyNews"
    daily_dir = daily_root / "2026年5月"
    daily_dir.mkdir(parents=True)
    source = daily_dir / "dailyFreshNews_2026-05-25.md"
    source.write_text(f"## Reuters · World（1条）\n### [原 Markdown 新闻]({URL})\n- 发布时间：2026-05-25 08:00:00\n- 摘要：原输入格式\n")
    if input_mode != "markdown_only":
        payload = {"schema_version": "newsreader.daily.v1", "items": [{
            "section": "Reuters · World", "title": "原 JSON 新闻", "url": URL,
            "published_at": "2026-05-25 08:00:00", "summary": "结构化输入",
        }]}
        if input_mode == "markdown_fallback":
            payload["schema_version"] = "unsupported"
        source.with_suffix(".newsreader.json").write_text(json.dumps(payload, ensure_ascii=False))
    original_inputs = {p: p.read_bytes() for p in daily_dir.iterdir()}
    database = tmp_path / "original.sqlite3"
    seed_original_database(database, str(source.relative_to(daily_root)))
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(tmp_path / "settings.json"))
    for key, path in {
        "DB_PATH": database, "DAILY_NEWS_DIR": daily_root,
        "MEDIA_CACHE_DIR": tmp_path / "media", "AGENT_DB_PATH": tmp_path / "agent.sqlite3",
        "AGENT_RUNTIME_DIR": tmp_path / "agent",
    }.items():
        monkeypatch.setattr(app, key, path)
    monkeypatch.setattr(app, "has_secret", lambda name: False)
    app.ensure_db()
    app.ensure_db()  # Repeated startup must not duplicate or erase history.
    client = app.app.test_client()

    def verify_reading_data():
        response = client.get(f"/api/news/{ITEM_ID}/detail")
        assert response.status_code == 200
        data = response.get_json()
        for field in ("read_at", "important_at", "read_later_at", "favorite_at"):
            assert data[field] == STAMP
        assert data["detail"]["content"] == "Original cached article"
        assert data["ai"]["body_zh"] == "历史中文正文"
        assert data["note"]["note"] == "原有想法，保留内容和时间"
        assert data["note"]["created_at"] == STAMP
        assert len(data["reminders"]) == 1  # Removed UI features do not delete old data.
        for collection in ("favorites", "important", "read_later"):
            news = client.get(f"/api/news?collection={collection}&read_filter=all").get_json()
            assert any(item["id"] == ITEM_ID for item in news["items"])

    verify_reading_data()
    with app.db_conn() as conn:
        first = reindex(conn, daily_root)
        second = reindex(conn, daily_root)
        assert first.upserted == 1
        assert first.deleted_stale == 0
        assert first.ingest_counts[input_mode] == 1
        assert second.changed_files == 0
        assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
        expected_title = "原 JSON 新闻" if input_mode == "sidecar_json" else "原 Markdown 新闻"
        assert conn.execute("SELECT title FROM items WHERE id=?", (ITEM_ID,)).fetchone()[0] == expected_title
    verify_reading_data()
    assert {p: p.read_bytes() for p in daily_dir.iterdir()} == original_inputs
