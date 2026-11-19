from __future__ import annotations

from nr import database

import pytest

from legacy_news_seed import seed_news, confirm_all_read
import importlib
import json
from pathlib import Path


def make_api_daily(path: Path, title: str, url: str, summary: str | None = None):
    lines = [
        "## Reuters · World（1条）",
        f"### [{title}]({url})",
        "- 发布时间：2026-05-25 12:00:00",
    ]
    if summary:
        lines.append(f"- 摘要：{summary}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_api_sidecar(path: Path, *, title: str, url: str, summary: str | None = None):
    path.write_text(
        json.dumps(
            {
                "schema_version": "newsreader.daily.v1",
                "items": [
                    {
                        "item_order": 1,
                        "section": "Reuters · World",
                        "title": title,
                        "summary": summary,
                        "published_at": "2026-05-25 12:00:00",
                        "url": url,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def make_daily_briefing(path: Path, body: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body.strip() + "\n", encoding="utf-8")


def _setup_agent_api_fixture(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "DailyNews" / "2026年8月"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-08-27.md").write_text(
        """## Reuters · World（1条）
### [Agent API 新闻](https://example.com/agent-api)
- 发布时间：2026-08-27 09:00:00
- 摘要：不应被研究 Agent 当作完整原文上下文。
""",
        encoding="utf-8",
    )
    db_path = tmp_path / "news_index.sqlite3"
    agent_db_path = tmp_path / "agent_sessions.sqlite3"
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(tmp_path / "DailyNews"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))
    monkeypatch.setenv("NEWS_READER_AGENT_DB_PATH", str(agent_db_path))
    monkeypatch.setenv("NEWS_READER_AGENT_RUNTIME_DIR", str(tmp_path / "agent-runtime"))
    settings_path = tmp_path / "agent-fixture-settings.json"
    settings_path.write_text(json.dumps({"llm": {"pi_chat": {"provider": "ollama", "model": "minimax-m3:cloud"}}}))
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(settings_path))
    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()
    assert seed_news(client, json={}).status_code == 200
    item = client.get("/api/news?per=20").get_json()["items"][0]
    with app_module.data.connect() as conn:
        stamp = database.now_ts()
        with conn:
            conn.execute(
                """
                INSERT INTO article_details(
                  url, source, title, author, published_at, content,
                  content_length, raw_json, fetched_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["url"],
                    "Reuters",
                    item["title"],
                    "Reporter",
                    "2026-08-27 09:00:00",
                    "The complete original article used as the authoritative context.",
                    68,
                    "{}",
                    stamp,
                    stamp,
                ),
            )
    return app_module, client, item, db_path, agent_db_path


def _install_fake_oar(tmp_path, monkeypatch):
    import sys
    fake = tmp_path / "fake-node"
    fake.write_text("#!" + sys.executable + "\n" + r'''
import json, os, pathlib, shutil, signal, sys, time
request = json.load(sys.stdin)
state = pathlib.Path(request["state_dir"])
state.mkdir(parents=True, exist_ok=True)
mapping = state / "session.json"
request["effective_prompt"] = request["resume_prompt"] if mapping.exists() else request["prompt"]
request["argv"] = sys.argv[1:]
with open(os.environ["OAR_REQUEST_LOG"], "a") as log:
    log.write(json.dumps(request, ensure_ascii=False) + "\n")
mode = os.environ.get("OAR_FAKE_MODE", "success")
config = state / ("config-" + request["config_id"] + "-fixture")
def cleanup(_signum=None, _frame=None):
    shutil.rmtree(config, ignore_errors=True)
    raise SystemExit(0)
signal.signal(signal.SIGTERM, cleanup)
signal.signal(signal.SIGINT, cleanup)
if mode == "hang":
    config.mkdir(exist_ok=True)
    (config / "auth.json").write_text("private")
    print(json.dumps({"type": "text_delta", "delta": "partial"}), flush=True)
    time.sleep(60)
if mode == "malformed":
    print("not json"); sys.exit(0)
if mode == "auth":
    print(json.dumps({"type": "oar_result", "outcome": "failed", "error": "oar_auth_required"})); sys.exit(1)
print(json.dumps({"type": "text_delta", "delta": "完成"}), flush=True)
if mode == "empty_terminal":
    print(json.dumps({"type": "oar_result", "outcome": "completed", "answer": ""}), flush=True)
    sys.exit(0)
if mode == "partial": sys.exit(0)
result = {"type": "oar_result", "outcome": "completed", "answer": "完成"}
print(json.dumps(result), flush=True)
if mode == "duplicate": print(json.dumps(result), flush=True)
if mode == "exit_failure": sys.exit(1)
mapping.write_text(json.dumps({"id": "fixture-native", "history_replayed": request.get("history_replayed", False)}))
''', encoding="utf-8")
    fake.chmod(0o755)
    log = tmp_path / "oar-requests.jsonl"
    monkeypatch.setenv("NEWS_READER_NODE", str(fake))
    monkeypatch.setenv("OAR_REQUEST_LOG", str(log))
    return log


@pytest.fixture(autouse=True)
def _isolate_app_settings(tmp_path: Path, monkeypatch):
    # 默认隔离 app_settings：指向不存在的路径 → load_app_settings 返回默认设置。
    # 避免测试读到本机运行态 app_settings.json（可能被设成 provider=pi）而分叉。
    # 需要 provider=pi 的测试在自己的 body 里 setenv NEWS_READER_APP_SETTINGS_PATH 覆盖。
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(tmp_path / "absent-app_settings.json"))


def _create_standalone_idea(client, note: str = "这项政策长期可能利好新能源") -> int:
    resp = client.post("/api/standalone-ideas", json={"note": note})
    assert resp.status_code == 200
    return resp.get_json()["idea"]["standalone_id"]
