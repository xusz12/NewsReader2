from __future__ import annotations

from nr import preferences

from legacy_news_seed import seed_news, confirm_all_read
import importlib
from pathlib import Path
from api_fixtures import _isolate_app_settings


def test_news_reminders_crud_and_snapshot_summary(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    daily_file = daily_dir / "dailyFreshNews_2026-06-20.md"
    daily_file.write_text(
        """## Reuters · World（1条）
### [Reminder Alpha](https://example.com/reminder-alpha)
- 发布时间：2026-06-20 09:00:00
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
    item = client.get("/api/news?per=20").get_json()["items"][0]
    assert item["active_reminder_count"] == 0
    assert item["due_reminder_count"] == 0
    assert item["next_remind_at"] is None

    create = client.post(
        f"/api/news/{item['id']}/reminders",
        json={
            "event_title": "英伟达财报",
            "event_date": "2026-06-25",
            "remind_at": "2026-06-24T21:30",
            "note": "回看毛利率和指引",
        },
    )
    assert create.status_code == 200
    created_payload = create.get_json()
    reminder = created_payload["reminder"]
    assert reminder["status"] == "active"
    assert reminder["event_date"] == "2026-06-25"
    assert reminder["remind_at"] == "2026-06-24 21:30:00"
    assert created_payload["summary"]["active_total"] == 1

    reminders = client.get("/api/reminders").get_json()
    assert reminders["summary"]["active_total"] == 1
    assert reminders["items"][0]["item_title_snapshot"] == "Reminder Alpha"
    assert reminders["items"][0]["item_url_snapshot"] == "https://example.com/reminder-alpha"
    assert reminders["items"][0]["item"]["id"] == item["id"]

    feed_item = client.get("/api/news?per=20").get_json()["items"][0]
    assert feed_item["active_reminder_count"] == 1
    assert feed_item["next_remind_at"] == "2026-06-24 21:30:00"

    status_item = client.get(f"/api/news/status?ids={item['id']}").get_json()["items"][0]
    assert status_item["active_reminder_count"] == 1
    assert status_item["next_remind_at"] == "2026-06-24 21:30:00"

    detail = client.get(f"/api/news/{item['id']}/detail").get_json()
    assert detail["reminder_summary"]["active_total"] == 1
    assert detail["reminders"][0]["event_title"] == "英伟达财报"

    done = client.patch(
        f"/api/reminders/{reminder['id']}",
        json={"status": "done"},
    )
    assert done.status_code == 200
    assert done.get_json()["reminder"]["status"] == "done"
    assert done.get_json()["reminder"]["completed_at"] is not None

    reopened = client.patch(
        f"/api/reminders/{reminder['id']}",
        json={
            "status": "active",
            "event_title": "英伟达财报更新",
            "remind_at": "2026-06-24 22:00:00",
        },
    )
    assert reopened.status_code == 200
    reopened_payload = reopened.get_json()["reminder"]
    assert reopened_payload["status"] == "active"
    assert reopened_payload["completed_at"] is None
    assert reopened_payload["event_title"] == "英伟达财报更新"
    assert reopened_payload["remind_at"] == "2026-06-24 22:00:00"

    deleted = client.delete(f"/api/reminders/{reminder['id']}")
    assert deleted.status_code == 200
    assert deleted.get_json()["summary"]["active_total"] == 0
    assert client.get("/api/reminders").get_json()["items"] == []


def test_reminder_snapshot_survives_stale_item_deletion(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年6月"
    daily_dir.mkdir(parents=True)
    daily_file = daily_dir / "dailyFreshNews_2026-06-20.md"
    daily_file.write_text(
        """## Reuters · World（1条）
### [Snapshot Beta](https://example.com/snapshot-beta)
- 发布时间：2026-06-20 10:00:00
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
    item = client.get("/api/news?per=20").get_json()["items"][0]
    create = client.post(
        f"/api/news/{item['id']}/reminders",
        json={
            "event_title": "回看政策落地",
            "event_date": "2026-06-30",
            "remind_at": "2026-06-29 09:00:00",
            "note": "",
        },
    )
    assert create.status_code == 200

    daily_file.unlink()
    assert seed_news(client, json={"full": True}).status_code == 200
    assert client.get("/api/news?per=20").get_json()["total"] == 0

    reminders = client.get("/api/reminders").get_json()
    assert reminders["summary"]["active_total"] == 1
    assert reminders["items"][0]["item_exists"] == 0
    assert reminders["items"][0]["item"] is None
    assert reminders["items"][0]["item_title_snapshot"] == "Snapshot Beta"
    assert reminders["items"][0]["item_url_snapshot"] == "https://example.com/snapshot-beta"


def test_apply_schema_creates_news_reminders_table_and_indexes(tmp_path: Path, monkeypatch):
    daily_root = tmp_path / "DailyNews"
    daily_root.mkdir(parents=True)
    db_path = tmp_path / "news_index.sqlite3"

    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(daily_root))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    app_module.ensure_db()

    conn = app_module.data.connect()
    try:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "news_reminders" in tables

        indexes = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert "idx_news_reminders_status_remind_at" in indexes
        assert "idx_news_reminders_item_id" in indexes
    finally:
        conn.close()
