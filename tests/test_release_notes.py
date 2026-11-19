from __future__ import annotations

import importlib
import json
from pathlib import Path

from api_fixtures import _isolate_app_settings


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
    monkeypatch.setattr(app_module.runtime.updates, 'changelog_path', changelog)

    notes = app_module.runtime.updates.parse_release_notes()

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
    manifest = json.loads((Path(app_module.runtime.db.root) / "version.json").read_text(encoding="utf-8"))
    assert first["version"] == manifest["version"]
    assert first["date"]
    assert first["lines"]
    assert "title" in first and "category" in first
    assert first["category"] in {"NEW", "IMPROVE", "FIX"}
