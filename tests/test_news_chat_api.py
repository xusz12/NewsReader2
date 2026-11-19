from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from api_fixtures import _isolate_app_settings
from legacy_news_seed import seed_news
from nr import database, news_chat


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


def test_run_pi_chat_success(tmp_path: Path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module.runtime.db, 'agent_dir', tmp_path / "runtime")
    captured = {}
    def fake_run(command, request, env, timeout):
        captured.update(command=command, request=request, env=env)
        return "Pi 回答"
    monkeypatch.setattr(news_chat, 'run_oar_once', fake_run)
    result = app_module.runtime.chat.run_pi_chat(item_id="item-1", question="怎么看？", title="News", source="Reuters",
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
    monkeypatch.setattr(app_module.runtime.db, 'agent_dir', tmp_path / "runtime")
    calls = []
    def fake_run(command, request, env, timeout):
        calls.append((command, request, env))
        return "归档结论"
    monkeypatch.setattr(news_chat, 'run_oar_once', fake_run)
    args = dict(title="News", source="Reuters", published_at="2026-06-11",
        messages=[{"role": "user", "content": "总结"}, {"role": "assistant", "content": "结论"}],
        pi_provider="ollama", pi_model="minimax-m3:cloud")
    result = app_module.runtime.chat.run_pi_chat_archive(**args)
    app_module.runtime.chat.run_pi_chat_archive(**args)
    assert result == {"provider": "pi", "model": "minimax-m3:cloud", "summary": "归档结论",
        "runtime": "oar-pi", "runtime_compatibility": ""}
    assert calls[0][1]["state_dir"] != calls[1][1]["state_dir"]
    assert "newsreader-oar-archive-" in calls[0][1]["state_dir"]
    assert not Path(calls[0][1]["state_dir"]).exists()
    assert calls[0][2].get("PI_PACKAGE_DIR") is None


def test_run_pi_chat_archive_empty_raises(tmp_path: Path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module.runtime.db, 'agent_dir', tmp_path / "runtime")
    monkeypatch.setattr(news_chat, 'run_oar_once', lambda *args: "")
    with pytest.raises(RuntimeError, match="pi_empty_archive"):
        app_module.runtime.chat.run_pi_chat_archive(title="News", source="Reuters", published_at="2026-06-11",
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
    with app_module.runtime.db.connect() as conn:
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

    monkeypatch.setattr(app_module.runtime.chat, 'run_pi_chat', fake_run_pi_chat)
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
    monkeypatch.setattr(app_module.runtime.db, 'agent_dir', tmp_path / "runtime")
    calls = []
    def fake_run(command, request, env, timeout):
        calls.append((command, request))
        return "回答"
    monkeypatch.setattr(news_chat, 'run_oar_once', fake_run)
    args = dict(item_id="item-1", title="标题", source="Reuters", published_at="2026-06-11 09:00:00",
        content="English body", context_level="full_detail", pi_provider="ollama", pi_model="minimax-m3:cloud")
    first = app_module.runtime.chat.run_pi_chat(question="首问", **args)
    second = app_module.runtime.chat.run_pi_chat(question="追问", session_id=first["session_id"], **args)
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

    monkeypatch.setattr(app_module.runtime.chat, 'run_pi_chat_archive', fake_archive)
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
    with app_module.runtime.db.connect() as conn:
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
        app_module.runtime.chat, 'run_pi_chat', lambda **kwargs: (_ for _ in ()).throw(RuntimeError("pi_timeout"))
    )
    res = client.post(f"/api/news/{item['id']}/chat", json={"question": "？"})
    assert res.status_code == 504
    assert res.get_json()["error"] == "provider_timeout"


def test_pi_archive_uses_oar_and_cleans_private_state(tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module.runtime.db, 'agent_dir', tmp_path / 'runtime')
    states = []
    def fake_run(command, request, env, timeout):
        state = Path(request['state_dir'])
        states.append(state)
        (state / 'route.json').write_text(json.dumps({'runtime': 'cli'}))
        return '归档结论'
    monkeypatch.setattr(news_chat, 'run_oar_once', fake_run)
    result = app_module.runtime.chat.run_pi_chat_archive(title='Fixture', source='Fixture', published_at='2026-10-10',
        messages=[{'role': 'user', 'content': '归档'}, {'role': 'assistant', 'content': '结论'}],
        pi_provider='fixture', pi_model='fixture')
    assert result['runtime'] == 'oar-pi'
    assert result['runtime_compatibility'] == ''
    assert all(not state.exists() for state in states)
