from __future__ import annotations

import importlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import textwrap
import types
import urllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pi_models
from api_fixtures import _isolate_app_settings
from legacy_news_seed import confirm_all_read
from nr import configuration, preferences
from settings import default_app_settings


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
    conn = app_module.runtime.db.connect()
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

    settings = default_app_settings()
    settings["feed"]["hidden_source_subkeys"] = ["reuters/middle-east", "x/macromargin"]
    preferences.save_app_settings(settings)
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
    conn = app_module.runtime.db.connect()
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
    conn = app_module.runtime.db.connect()
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

    settings = default_app_settings()
    settings["feed"]["hidden_source_subkeys"] = ["x/aiandcloud", "bloomberg/bloomberg_tech"]
    preferences.save_app_settings(settings)
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
    preferences.save_app_settings(settings)
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
    monkeypatch.setattr(preferences, 'has_secret', lambda name: False)
    monkeypatch.setattr(shutil, "which", lambda name: None)
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
    monkeypatch.setattr(preferences, 'urlopen', lambda request, timeout=0: (_ for _ in ()).throw(urllib.error.HTTPError(preferences.DEEPSEEK_MODELS_URL, 503, "boom", None, None)))

    snapshot = preferences.deepseek_settings_snapshot("deepseek-custom-x")
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
    monkeypatch.setattr(preferences, 'has_secret', lambda name: False)
    monkeypatch.setattr(shutil, "which", lambda name: None)
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


def test_concurrent_page_saves_merge_llm_settings_and_keep_unrelated_values(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(tmp_path / "news.sqlite3"))
    monkeypatch.setattr(preferences, "has_secret", lambda name: False)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()
    classification = app_module.runtime.jobs.classification
    revision = classification.config_revision
    limits = {"max_items": 207, "max_days": 14}
    assert client.put("/api/settings", json={"news_summary": limits}).status_code == 200
    assert classification.config_revision == revision
    initial = client.get("/api/settings").get_json()
    start = Barrier(2)

    def save_page(payload):
        with app_module.app.test_client() as page:
            start.wait(timeout=10)
            return page.put("/api/settings", json=payload).status_code

    chat = {"provider": "ollama", "model": "page-model"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        response_codes = list(pool.map(save_page, [
            {"llm": {"pi_chat": chat}},
            {"llm": {"translation": {"model": "page-translation"}}},
        ]))
    assert response_codes == [200, 200]
    saved = client.get("/api/settings").get_json()
    assert saved["llm"]["pi_chat"] == chat
    assert saved["llm"]["translation"] == {**initial["llm"]["translation"], "model": "page-translation"}
    assert saved["news_summary"] == limits
    assert saved["x_accounts_revision"] == initial["x_accounts_revision"]


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

    monkeypatch.setattr(preferences, 'has_secret', lambda name: name in saved)
    monkeypatch.setattr(preferences, 'write_secret', lambda name, value: saved.__setitem__(name, value))
    monkeypatch.setattr(preferences, 'delete_secret', lambda name: saved.pop(name, None))

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
        raise preferences.SecretStoreError("write_failed")

    monkeypatch.setattr(preferences, 'write_secret', raise_failure)

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
    assert preferences.parse_pi_providers(stdout) == ["deepseek", "ollama"]
    assert preferences.parse_pi_providers("") == []
    assert preferences.parse_pi_providers("provider  model\n") == []


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
    providers, grouped = preferences.parse_pi_model_catalog(stdout)
    assert providers == ["deepseek", "ollama"]
    assert grouped == {
        "deepseek": ["deepseek-v4-flash", "deepseek-v4-pro"],
        "ollama": ["minimax-m3:cloud", "qwen3.5:4b"],
    }
    # 表头/空行/畸形行（不足两列）/空输出都安全跳过；两列以上的行取前两列。
    assert preferences.parse_pi_model_catalog("provider  model\n") == ([], {})
    assert preferences.parse_pi_model_catalog("") == ([], {})
    assert preferences.parse_pi_model_catalog("onlyprovider\n") == ([], {})
    providers_two, grouped_two = preferences.parse_pi_model_catalog("deepseek  deepseek-v4-pro  1M  384K  yes  no\n")
    assert providers_two == ["deepseek"]
    assert grouped_two == {"deepseek": ["deepseek-v4-pro"]}


def _pi_catalog_fixture(monkeypatch, app_module, rows):
    monkeypatch.setattr(pi_models, "catalog", lambda: {
        "error": "", "options": [{"provider": provider, "value": model, "label": model,
                                   "reasoning_efforts": ["off", "low", "high"]} for provider, model in rows]})


def test_pi_chat_settings_snapshot_detects_providers(monkeypatch):
    import app as app_module
    _pi_catalog_fixture(monkeypatch, app_module, [("deepseek", "deepseek-v4-flash"), ("ollama", "minimax-m3:cloud")])
    catalog = preferences.pi_chat_settings_snapshot("minimax-m3:cloud", "deepseek")["catalog"]
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
    monkeypatch.setattr(pi_models, "catalog", fail)
    catalog = preferences.pi_chat_settings_snapshot("old-model", "custom-provider")["catalog"]
    assert catalog["provider_options"] == catalog["options"] == []
    assert catalog["saved_provider"] == "custom-provider"
    assert catalog["saved_model"] == "old-model"
    assert catalog["error"] and catalog["source"] == "unavailable"
    assert "private" not in catalog["error"]


def test_pi_chat_settings_snapshot_removes_logged_out_provider_even_when_saved(monkeypatch):
    import app as app_module
    _pi_catalog_fixture(monkeypatch, app_module, [("deepseek", "deepseek-v4-pro"), ("ollama", "local")])
    assert len(preferences.pi_chat_settings_snapshot("deepseek-v4-pro", "deepseek")["catalog"]["provider_options"]) == 2
    _pi_catalog_fixture(monkeypatch, app_module, [("ollama", "local")])
    catalog = preferences.pi_chat_settings_snapshot("deepseek-v4-pro", "deepseek")["catalog"]
    assert [option["value"] for option in catalog["provider_options"]] == ["ollama"]
    assert catalog["options"] == [] and "deepseek" not in catalog["model_options_by_provider"]
    _pi_catalog_fixture(monkeypatch, app_module, [])
    empty = preferences.pi_chat_settings_snapshot("deepseek-v4-pro", "deepseek")["catalog"]
    assert empty["provider_options"] == [] and empty["model_options_by_provider"] == {}
    assert empty["error"] == "" and empty["source"] == "pi-model-registry"
    assert empty["saved_provider"] == "deepseek"


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
    normalized = app_module.runtime.preferences.validate_runtime_settings(
        {"llm": {"translation": {"provider": "deepseek", "model": "deepseek-chat"}}}
    )
    assert normalized["llm"]["translation"]["model"] == "deepseek-v4-flash"


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
    with app_module.runtime.db.connect() as conn:
        assert conn.execute("SELECT value FROM switch_marker").fetchone()[0] == "initial"
    saved = client.put("/api/settings", json=payload)
    assert saved.status_code == 200
    assert saved.get_json()["database"]["configured_path"] == str(selected_db.resolve())
    assert json.loads(settings_path.read_text())["database"]["path"] == str(selected_db.resolve())
    # A browser refresh retains the choice but cannot switch a running process.
    refreshed = client.get("/api/settings").get_json()["database"]
    assert refreshed["configured_path"] == str(selected_db.resolve())
    assert refreshed["path"] == str(initial_db.resolve())
    with app_module.runtime.db.connect() as conn:
        assert conn.execute("SELECT value FROM switch_marker").fetchone()[0] == "initial"

    fresh_process = subprocess.run(
        [sys.executable, "-c", textwrap.dedent("""
            import json
            from app import runtime
            with runtime.db.connect() as conn:
                marker = conn.execute("SELECT value FROM switch_marker").fetchone()[0]
            print(json.dumps({"path": str(runtime.db.path.resolve()), "marker": marker,
                              "database": runtime.preferences.database_runtime_snapshot()}))
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
    assert app_module.runtime.db.path == selected_db
    assert app_module.runtime.db.media_dir == selected_db.parent / "media-cache"
    assert app_module.runtime.db.agent_path == selected_db.parent / "agent_sessions.sqlite3"

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
            "from app import runtime; print(runtime.db.path.expanduser().resolve())",
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
    assert app_module.runtime.db.path == project_db
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
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/osascript")

    def fake_run(command, **kwargs):
        assert command[0] == "osascript"
        assert kwargs["check"] is False
        return types.SimpleNamespace(returncode=0, stdout=f"{selected_db}\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
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
    monkeypatch.setattr(app_module.runtime.preferences, 'choose_existing_database_path', lambda: (_ for _ in ()).throw(RuntimeError("boom")))

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
    monkeypatch.setattr(os, "access", lambda path, mode: False)
    response = client.put("/api/settings", json=payload)
    assert response.status_code == 400
    assert response.get_json()["error"] == "database_path_not_readable"
