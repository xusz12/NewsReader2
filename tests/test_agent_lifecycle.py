from __future__ import annotations

import importlib
import json
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest

from api_fixtures import _install_fake_oar, _isolate_app_settings, _setup_agent_api_fixture
from nr import agent_jobs, agent_sessions, configuration, database, news_chat


def test_agent_session_new_and_clear_are_real_and_idempotent(tmp_path: Path, monkeypatch):
    app_module, client, item, _db_path, agent_db_path = _setup_agent_api_fixture(tmp_path, monkeypatch)

    empty_clear = client.delete(f"/api/news/{item['id']}/agent/session")
    assert empty_clear.status_code == 200
    assert empty_clear.get_json() == {"ok": True, "cleared": True}

    first = client.post(f"/api/news/{item['id']}/agent/session", json={})
    assert first.status_code == 200
    first_id = first.get_json()["session"]["id"]
    old_job = client.post(
        f"/api/news/{item['id']}/agent/jobs",
        json={
            "question": "旧会话问题",
            "quote_text": "旧引用",
            "quote_source": "英文原文引用",
        },
    )
    assert old_job.status_code == 202
    old_job_id = old_job.get_json()["job_id"]
    with app_module.runtime.db.agent_connect() as conn:
        with conn:
            conn.execute(
                "UPDATE agent_jobs SET status='failed', error='old_error', finished_at=?, updated_at=? WHERE id=?",
                (database.now_ts(), database.now_ts(), old_job_id),
            )
    old_runtime = app_module.runtime.sessions.agent_session_runtime_dir(first_id)
    old_runtime.mkdir(parents=True)
    (old_runtime / "stale-session.json").write_text("stale", encoding="utf-8")

    replaced = client.post(
        f"/api/news/{item['id']}/agent/session",
        json={"new_session": True},
    )
    assert replaced.status_code == 200
    second_id = replaced.get_json()["session"]["id"]
    assert second_id != first_id
    with sqlite3.connect(agent_db_path) as conn:
        assert conn.execute("SELECT id, status FROM agent_sessions").fetchall() == [(second_id, "active")]
        assert conn.execute("SELECT 1 FROM agent_sessions WHERE id=?", (first_id,)).fetchone() is None
        assert conn.execute("SELECT 1 FROM agent_jobs WHERE id=?", (old_job_id,)).fetchone() is None
        assert conn.execute("SELECT COUNT(*) FROM agent_jobs WHERE item_id=?", (item["id"],)).fetchone() == (0,)
        assert conn.execute("SELECT COUNT(*) FROM agent_messages WHERE session_id=?", (first_id,)).fetchone() == (0,)
        assert conn.execute("SELECT COUNT(*) FROM agent_messages").fetchone() == (0,)
        assert conn.execute(
            "SELECT status FROM agent_sessions WHERE id=?", (second_id,)
        ).fetchone() == ("active",)
    assert not old_runtime.exists()

    first_clear = client.delete(f"/api/news/{item['id']}/agent/session")
    second_clear = client.delete(f"/api/news/{item['id']}/agent/session")
    assert first_clear.status_code == second_clear.status_code == 200
    assert first_clear.get_json()["cleared"] is True
    assert second_clear.get_json()["cleared"] is True
    with sqlite3.connect(agent_db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM agent_sessions").fetchone() == (0,)
        assert conn.execute("SELECT COUNT(*) FROM agent_jobs").fetchone() == (0,)
        assert conn.execute("SELECT COUNT(*) FROM agent_messages").fetchone() == (0,)


@pytest.mark.parametrize("answer", ["", " \t\n"])
def test_oar_empty_terminal_does_not_promote_streamed_text(tmp_path: Path, monkeypatch, answer):
    app_module, client, item, _db_path, _agent_db_path = _setup_agent_api_fixture(tmp_path, monkeypatch)
    _install_fake_oar(tmp_path, monkeypatch)
    fake = tmp_path / "fake-node"
    fake.write_text(fake.read_text().replace('"answer": ""', '"answer": ' + repr(answer)))
    monkeypatch.setenv("OAR_FAKE_MODE", "empty_terminal")
    submitted = client.post(f"/api/news/{item['id']}/agent/jobs", json={"question": "空终态"})
    assert submitted.status_code == 202
    assert app_module.runtime.agent.process_pending_agent_once() is True
    job = app_module.runtime.sessions.load_agent_payload(item["id"])["jobs"][-1]
    assert job["status"] == "failed"
    assert job["error"] == "empty_answer"
    assert job["answer_text"] == "完成"
    payload = app_module.runtime.sessions.load_agent_payload(item["id"])
    assert payload["messages"][-1]["status"] == "failed"
    with app_module.runtime.db.agent_connect() as conn:
        assert conn.execute("SELECT executor_session_id FROM agent_sessions").fetchone()[0] == ""
    runtime = app_module.runtime.sessions.agent_session_runtime_dir(agent_sessions._full_pi_executor_session_id(payload["session"]["id"]))
    assert not (runtime / "session.json").exists()
    monkeypatch.setenv("OAR_FAKE_MODE", "success")
    assert client.post(f"/api/news/{item['id']}/agent/jobs/{submitted.get_json()['job_id']}/retry").status_code == 202
    assert app_module.runtime.agent.process_pending_agent_once()
    assert app_module.runtime.sessions.load_agent_payload(item["id"])["jobs"][-1]["status"] == "succeeded"


@pytest.mark.parametrize("window", ["command", "registration", "cleanup"])
def test_oar_stop_during_launch_keeps_session_busy_until_worker_finishes(tmp_path: Path, monkeypatch, window):
    import threading

    app_module, client, item, _db_path, _agent_db_path = _setup_agent_api_fixture(tmp_path, monkeypatch)
    _install_fake_oar(tmp_path, monkeypatch)
    submitted = client.post(f"/api/news/{item['id']}/agent/jobs", json={"question": "启动竞态"})
    assert submitted.status_code == 202
    payload = submitted.get_json()
    entered, release = threading.Event(), threading.Event()
    if window == "command":
        original = app_module.runtime.agent._agent_process_command
        def held(*args, **kwargs):
            entered.set()
            assert release.wait(8)
            return original(*args, **kwargs)
        monkeypatch.setattr(app_module.runtime.agent, '_agent_process_command', held)
    elif window == "registration":
        original = subprocess.Popen
        def held(*args, **kwargs):
            proc = original(*args, **kwargs)
            entered.set()
            assert release.wait(8)
            return proc
        monkeypatch.setattr(subprocess, "Popen", held)
    else:
        original = agent_jobs.cleanup_oar_config
        def held(*args, **kwargs):
            entered.set()
            assert release.wait(8)
            return original(*args, **kwargs)
        monkeypatch.setattr(agent_jobs, 'cleanup_oar_config', held)
    worker = threading.Thread(target=app_module.runtime.agent.process_pending_agent_once)
    worker.start()
    try:
        assert entered.wait(3)
        stopped = client.post(f"/api/news/{item['id']}/agent/jobs/{payload['job_id']}/stop")
        assert stopped.status_code == 200
        assert stopped.get_json()["job"]["status"] == "stopping"
        assert not stopped.get_json()["job"]["finished_at"]
        assert app_module.runtime.sessions.load_agent_payload(item["id"])["messages"][-1]["status"] == "stopping"
        assert client.post(f"/api/news/{item['id']}/agent/jobs/{payload['job_id']}/retry").status_code == 409
        blocked = client.post(
            f"/api/news/{item['id']}/agent/jobs",
            json={"question": "旧任务仍在启动", "session_id": payload["session"]["id"]},
        )
        assert blocked.status_code == 409
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    settled = app_module.runtime.sessions.load_agent_payload(item["id"])["jobs"][-1]
    assert settled["status"] == "stopped" and settled["finished_at"]
    assert app_module.runtime.sessions.load_agent_payload(item["id"])["messages"][-1]["status"] == "stopped"
    retry = client.post(f"/api/news/{item['id']}/agent/jobs/{payload['job_id']}/retry")
    assert retry.status_code == 202


def test_agent_first_and_follow_up_use_one_full_pi_session(tmp_path: Path, monkeypatch):
    app_module, client, item, _db_path, agent_db_path = _setup_agent_api_fixture(tmp_path, monkeypatch)
    log = _install_fake_oar(tmp_path, monkeypatch)
    first = client.post(f"/api/news/{item['id']}/agent/jobs", json={"question": "首问"})
    assert first.status_code == 202
    session_id = first.get_json()["session"]["id"]
    assert app_module.runtime.agent.process_pending_agent_once() is True
    second = client.post(f"/api/news/{item['id']}/agent/jobs", json={"question": "依赖上一轮的追问", "session_id": session_id})
    assert second.status_code == 202
    assert app_module.runtime.agent.process_pending_agent_once() is True
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    expected = f"newsreader-oar-pi-{session_id}"
    assert len(calls) == 2
    assert all(call["argv"][call["argv"].index("--session-id") + 1] == expected for call in calls)
    assert calls[0]["state_dir"] == calls[1]["state_dir"]
    assert "complete original article" in calls[0]["effective_prompt"]
    assert "首问" in calls[0]["effective_prompt"]
    assert "complete original article" not in calls[1]["effective_prompt"]
    assert "首问" not in calls[1]["effective_prompt"]
    assert calls[1]["effective_prompt"] == "依赖上一轮的追问"
    # Full fallback history is supplied to the bridge, not sent on a native resume.
    assert "首问" in calls[1]["prompt"]
    assert all("首问" not in " ".join(call["argv"]) for call in calls)
    with sqlite3.connect(agent_db_path) as conn:
        assert conn.execute("SELECT executor_session_id FROM agent_sessions WHERE id=?", (session_id,)).fetchone() == (expected,)


def test_agent_existing_temp_db_is_migrated_before_session_use(tmp_path: Path, monkeypatch):
    agent_db_path = tmp_path / "agent_sessions.sqlite3"
    with sqlite3.connect(agent_db_path) as conn:
        conn.execute(
            """
            CREATE TABLE agent_sessions (
              id TEXT PRIMARY KEY,
              item_id TEXT NOT NULL,
              url TEXT NOT NULL,
              provider TEXT NOT NULL,
              model TEXT NOT NULL DEFAULT '',
              executor_session_id TEXT NOT NULL DEFAULT '',
              context_hash TEXT NOT NULL,
              context_json TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'active',
              created_at TEXT NOT NULL,
              last_activity_at TEXT NOT NULL,
              expires_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
    monkeypatch.setenv("NEWS_READER_AGENT_DB_PATH", str(agent_db_path))
    monkeypatch.setenv("NEWS_READER_AGENT_RUNTIME_DIR", str(tmp_path / "agent-runtime"))
    import app as app_module

    importlib.reload(app_module)
    app_module.runtime.sessions.ensure_agent_db()
    with sqlite3.connect(agent_db_path) as conn:
        session_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(agent_sessions)").fetchall()
        }
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    assert "executor_provider" in session_columns
    assert {"agent_sessions", "agent_jobs", "agent_messages"}.issubset(tables)


def test_agent_legacy_pi_session_switches_with_one_time_visible_history(tmp_path: Path, monkeypatch):
    app_module, _client, item, _db_path, _agent_db_path = _setup_agent_api_fixture(tmp_path, monkeypatch)
    with app_module.runtime.db.connect() as conn:
        detail = conn.execute("SELECT * FROM article_details WHERE url=?", (item["url"],)).fetchone()
    context = news_chat.build_agent_context(item, detail)
    legacy_id = "newsreader-agent-legacy-session"
    legacy_runtime = app_module.runtime.sessions.agent_session_runtime_dir(legacy_id)
    legacy_runtime.mkdir(parents=True)
    (legacy_runtime / "old-session.jsonl").write_text("legacy")
    session = {"id": "session-legacy", "executor_provider": "ollama", "executor_session_id": legacy_id}
    job = {"provider": "pi", "model": "minimax-m3:cloud", "question": "新问题", "quote_text": ""}
    history = [{"role": "user", "content": "旧问题"}, {"role": "assistant", "content": "旧回答"}]
    command, env, runtime_dir = app_module.runtime.agent._agent_process_command(job, session, context, history)
    assert command[command.index("--session-id") + 1] == "newsreader-oar-pi-session-legacy"
    request = agent_jobs._agent_oar_request(job, session, context, history, runtime_dir)
    assert "complete original article" in request["prompt"]
    assert "此前会话记录：" in request["prompt"]
    assert "旧问题" in request["prompt"] and "旧回答" in request["prompt"]
    assert request["history_replayed"] is True
    assert "旧问题" not in request["resume_prompt"]
    assert "旧对话保留" in app_module.runtime.sessions._serialize_agent_session(session)["history_compatibility"]
    # No deletion or mutation of pre-migration native files.
    assert (legacy_runtime / "old-session.jsonl").read_text() == "legacy"


def test_agent_legacy_pi_migration_persists_and_stops_replaying_history(tmp_path: Path, monkeypatch):
    app_module, client, item, _db_path, agent_db_path = _setup_agent_api_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(agent_sessions, '_agent_executor_settings', lambda: dict(
        provider="pi", model="minimax-m3:cloud", executor_provider="ollama", executor_config={}))
    log = _install_fake_oar(tmp_path, monkeypatch)

    first = client.post(f"/api/news/{item['id']}/agent/jobs", json={"question": "旧问题"})
    assert first.status_code == 202
    first_payload = first.get_json()
    session_id = first_payload["session"]["id"]
    legacy_id = "newsreader-agent-legacy-session"
    with sqlite3.connect(agent_db_path) as conn:
        stamp = database.now_ts()
        with conn:
            conn.execute(
                "UPDATE agent_sessions SET executor_session_id=? WHERE id=?",
                (legacy_id, session_id),
            )
            conn.execute(
                "UPDATE agent_jobs SET status='succeeded', answer_text='旧回答', finished_at=?, updated_at=? WHERE id=?",
                (stamp, stamp, first_payload["job_id"]),
            )
            conn.execute(
                "UPDATE agent_messages SET content='旧回答', status='succeeded', updated_at=? WHERE job_id=? AND role='assistant'",
                (stamp, first_payload["job_id"]),
            )

    second = client.post(
        f"/api/news/{item['id']}/agent/jobs",
        json={"question": "新问题", "session_id": session_id},
    )
    assert second.status_code == 202
    assert app_module.runtime.agent.process_pending_agent_once() is True

    third = client.post(
        f"/api/news/{item['id']}/agent/jobs",
        json={"question": "再问", "session_id": session_id},
    )
    assert third.status_code == 202
    assert app_module.runtime.agent.process_pending_agent_once() is True

    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(calls) == 2
    assert "旧问题" in calls[0]["effective_prompt"] and "旧回答" in calls[0]["effective_prompt"]
    assert "此前会话记录：" not in calls[1]["effective_prompt"]
    assert "旧问题" not in calls[1]["effective_prompt"] and "旧回答" not in calls[1]["effective_prompt"]
    visible = app_module.runtime.sessions.load_agent_payload(item["id"])
    assert any(message["content"] == "旧回答" for message in visible["messages"])
    assert "旧对话" in visible["session"]["history_compatibility"]
    with sqlite3.connect(agent_db_path) as conn:
        stored = conn.execute(
            "SELECT executor_session_id FROM agent_sessions WHERE id=?", (session_id,)
        ).fetchone()
    assert stored == (f"newsreader-oar-pi-{session_id}",)


def test_agent_ttl_setting_persists_and_ui_exposes_cleanup_controls(tmp_path: Path, monkeypatch):
    app_module, client, _item, _db_path, _agent_db_path = _setup_agent_api_fixture(tmp_path, monkeypatch)
    assert client.get("/api/settings").get_json()["agent"]["session_ttl_hours"] == 72
    saved = client.put(
        "/api/settings",
        json={
            "llm": {"translation": {"provider": "deepseek", "model": ""}},
            "agent": {"session_ttl_hours": 24},
        },
    )
    assert saved.status_code == 200
    assert saved.get_json()["agent"]["session_ttl_hours"] == 24
    assert configuration.load_app_settings()["agent"]["session_ttl_hours"] == 24


@pytest.mark.parametrize('mode,expected_error', [
    ('partial', 'oar_protocol_error'), ('malformed', 'oar_protocol_error'),
    ('auth', 'oar_auth_required'), ('duplicate', 'oar_protocol_error'),
    ('exit_failure', 'oar_runtime_failed'),
])
def test_oar_agent_failure_is_persisted_and_retryable(tmp_path, monkeypatch, mode, expected_error):
    app_module, client, item, _db_path, agent_db_path = _setup_agent_api_fixture(tmp_path, monkeypatch)
    _install_fake_oar(tmp_path, monkeypatch)
    monkeypatch.setenv('OAR_FAKE_MODE', mode)
    first = client.post(f"/api/news/{item['id']}/agent/jobs", json={'question':'question'})
    assert first.status_code == 202
    assert client.post(f"/api/news/{item['id']}/agent/jobs", json={'question':'duplicate'}).status_code == 409
    assert app_module.runtime.agent.process_pending_agent_once()
    payload = app_module.runtime.sessions.load_agent_payload(item['id'])
    assert payload['jobs'][-1]['status'] == 'failed'
    assert payload['jobs'][-1]['error'] == expected_error
    with sqlite3.connect(agent_db_path) as conn:
        assert conn.execute('SELECT executor_session_id FROM agent_sessions').fetchone() == ('',)
    monkeypatch.setenv('OAR_FAKE_MODE', 'success')
    retry = client.post(f"/api/news/{item['id']}/agent/jobs/{first.get_json()['job_id']}/retry")
    assert retry.status_code == 202
    assert app_module.runtime.agent.process_pending_agent_once()
    assert app_module.runtime.sessions.load_agent_payload(item['id'])['jobs'][-1]['status'] == 'succeeded'


def test_oar_node_missing_settles_job(tmp_path, monkeypatch):
    app_module, client, item, _db_path, _agent_db_path = _setup_agent_api_fixture(tmp_path, monkeypatch)
    monkeypatch.setenv('NEWS_READER_NODE', '/not/a/node')
    client.post(f"/api/news/{item['id']}/agent/jobs", json={'question':'question'})
    assert app_module.runtime.agent.process_pending_agent_once()
    job = app_module.runtime.sessions.load_agent_payload(item['id'])['jobs'][-1]
    assert job['status'] == 'failed' and job['error'] == 'oar_node_missing'


def test_oar_timeout_and_stop_cleanup(tmp_path, monkeypatch):
    import threading
    import time
    app_module, client, item, _db_path, _agent_db_path = _setup_agent_api_fixture(tmp_path, monkeypatch)
    log = _install_fake_oar(tmp_path, monkeypatch)
    monkeypatch.setenv('OAR_FAKE_MODE', 'hang')
    monkeypatch.setattr(agent_jobs, 'AGENT_JOB_TIMEOUT_SECONDS', .2)
    first = client.post(f"/api/news/{item['id']}/agent/jobs", json={'question':'timeout'})
    assert app_module.runtime.agent.process_pending_agent_once()
    payload = app_module.runtime.sessions.load_agent_payload(item['id'])
    assert payload['jobs'][-1]['status'] == 'failed'
    assert payload['jobs'][-1]['error'] == 'agent_timeout'
    assert not list(app_module.runtime.db.agent_dir.glob('*/config-*'))
    monkeypatch.setattr(agent_jobs, 'AGENT_JOB_TIMEOUT_SECONDS', 10)
    # The short timeout can expire before the first fake runtime writes its log.
    # Wait for this invocation, not an assumed second line in an existing file.
    previous_count = len(log.read_text().splitlines()) if log.exists() else 0
    second = client.post(f"/api/news/{item['id']}/agent/jobs", json={'question':'stop'})
    worker = threading.Thread(target=app_module.runtime.agent.process_pending_agent_once)
    worker.start()
    deadline = time.monotonic() + 3
    while (not log.exists() or len(log.read_text().splitlines()) <= previous_count) and time.monotonic() < deadline:
        time.sleep(.02)
    started = log.exists() and len(log.read_text().splitlines()) > previous_count
    stopped = client.post(f"/api/news/{item['id']}/agent/jobs/{second.get_json()['job_id']}/stop")
    assert stopped.status_code == 200
    worker.join(4)
    assert not worker.is_alive()
    assert started
    assert app_module.runtime.sessions.load_agent_payload(item['id'])['jobs'][-1]['status'] == 'stopped'
    assert not app_module.runtime.agent.processes
    assert not list(app_module.runtime.db.agent_dir.glob('*/config-*'))


def test_oar_stop_wins_completion_race(tmp_path, monkeypatch):
    app_module, client, item, _db_path, agent_db_path = _setup_agent_api_fixture(tmp_path, monkeypatch)
    _install_fake_oar(tmp_path, monkeypatch)
    original = app_module.runtime.agent._update_agent_job_output
    def cancel_after_output(job_id, session_id, output, **kwargs):
        original(job_id, session_id, output, **kwargs)
        with sqlite3.connect(agent_db_path) as conn:
            conn.execute('UPDATE agent_jobs SET cancel_requested=1 WHERE id=?', (job_id,))
    monkeypatch.setattr(app_module.runtime.agent, '_update_agent_job_output', cancel_after_output)
    client.post(f"/api/news/{item['id']}/agent/jobs", json={'question':'race'})
    assert app_module.runtime.agent.process_pending_agent_once()
    job = app_module.runtime.sessions.load_agent_payload(item['id'])['jobs'][-1]
    assert job['status'] == 'stopped'


def test_oar_sync_legacy_id_is_imported_not_discarded(tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module.runtime.db, 'agent_dir', tmp_path / 'runtime')
    requests = []
    def fake_run(command, request, env, timeout):
        requests.append(request)
        return 'continued'
    monkeypatch.setattr(news_chat, 'run_oar_once', fake_run)
    args = dict(item_id='item', title='fixture', source='fixture', published_at='2026-10-10', content='sample',
                context_level='full_detail', pi_provider='fixture', pi_model='fixture')
    result = app_module.runtime.chat.run_pi_chat(question='next', session_id='old-native-id', **args)
    assert requests[0]['legacy_native_id'] == 'old-native-id'
    assert result['session_id'].startswith('newsreader-oar-chat-')
    app_module.runtime.chat.run_pi_chat(question='again', session_id=result['session_id'], **args)
    assert 'legacy_native_id' not in requests[1]
    assert requests[0]['state_dir'] == requests[1]['state_dir']
    with pytest.raises(RuntimeError, match='pi_session_invalid'):
        app_module.runtime.chat.run_pi_chat(question='unsafe', session_id='../../unsafe', **args)


def test_legacy_background_session_always_reports_oar(tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module.runtime.db, 'agent_dir', tmp_path / 'runtime')
    session_id = 'fixture-legacy-label'
    state = app_module.runtime.sessions.agent_session_runtime_dir(agent_sessions._full_pi_executor_session_id(session_id))
    state.mkdir(parents=True)
    (state / 'route.json').write_text(json.dumps({'runtime': 'cli'}))
    result = app_module.runtime.sessions._serialize_agent_session({'id': session_id, 'executor_session_id': 'legacy-native-fixture',
        'executor_provider': 'fixture', 'context_json': '{}'})
    assert result['runtime'] == 'oar-pi'
    assert result['runtime_compatibility'] == ''
    assert '旧对话' in result['history_compatibility']
    assert 'executor_session_id' not in result and 'context_json' not in result


def test_oar_old_worker_cleanup_does_not_remove_another_invocations_config(tmp_path, monkeypatch):
    app_module, client, item, _, _ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    _install_fake_oar(tmp_path, monkeypatch)
    submitted = client.post(f"/api/news/{item['id']}/agent/jobs", json={"question": "清理归属"}).get_json()
    runtime = app_module.runtime.sessions.agent_session_runtime_dir(agent_sessions._full_pi_executor_session_id(submitted["session"]["id"]))
    other_config = runtime / ("config-" + "a" * 32 + "-another-worker")
    other_config.mkdir(parents=True)
    (other_config / "auth.json").write_text("another worker credential")
    # Ignore SIGTERM so Python must clean its own token after SIGKILL.
    fake = tmp_path / "fake-node"
    fake.write_text(fake.read_text().replace("signal.signal(signal.SIGTERM, cleanup)",
                                           "signal.signal(signal.SIGTERM, signal.SIG_IGN)"))
    monkeypatch.setenv("OAR_FAKE_MODE", "hang")
    monkeypatch.setattr(agent_jobs, 'AGENT_JOB_TIMEOUT_SECONDS', .2)
    assert app_module.runtime.agent.process_pending_agent_once()
    assert app_module.runtime.sessions.load_agent_payload(item["id"])["jobs"][-1]["status"] == "failed"
    assert list(runtime.glob("config-*")) == [other_config]
    assert (other_config / "auth.json").read_text() == "another worker credential"


def test_oar_queued_stop_is_immediate_and_never_runs(tmp_path, monkeypatch):
    app_module, client, item, _, _ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    log = _install_fake_oar(tmp_path, monkeypatch)
    submitted = client.post(f"/api/news/{item['id']}/agent/jobs", json={"question": "排队取消"}).get_json()
    stopped = client.post(f"/api/news/{item['id']}/agent/jobs/{submitted['job_id']}/stop").get_json()["job"]
    assert stopped["status"] == "stopped" and stopped["finished_at"]
    assert app_module.runtime.agent.process_pending_agent_once() is False
    assert not log.exists()
    assert client.post(f"/api/news/{item['id']}/agent/jobs/{submitted['job_id']}/retry").status_code == 202


def test_session_schema_skips_warm_migrations_and_repairs_a_replaced_library(tmp_path, monkeypatch):
    path = tmp_path / "sessions.sqlite3"
    monkeypatch.setenv("NEWS_READER_AGENT_DB_PATH", str(path))
    monkeypatch.setenv("NEWS_READER_AGENT_RUNTIME_DIR", str(tmp_path / "agent"))
    sessions = agent_sessions.AgentSessions(database.Database())
    sessions.ensure_agent_db()
    sessions.ensure_agent_db()
    commands = []
    connect = sessions.db.agent_connect

    def traced_connection():
        conn = connect()
        conn.set_trace_callback(commands.append)
        return conn

    monkeypatch.setattr(sessions.db, "agent_connect", traced_connection)
    with closing(sessions.connect()) as conn:
        assert not any(sql.lstrip().upper().startswith(("CREATE", "ALTER")) for sql in commands)
        conn.execute("DROP INDEX idx_agent_notifications_unread")
    with closing(sessions.connect()) as conn:
        assert conn.execute("SELECT 1 FROM sqlite_schema WHERE name='idx_agent_notifications_unread'").fetchone()
        version = conn.execute("PRAGMA schema_version").fetchone()[0]

    replacement = tmp_path / "replacement.sqlite3"
    with closing(sqlite3.connect(replacement)) as conn:
        conn.execute("CREATE TABLE preserved (value TEXT)")
        conn.execute(f"PRAGMA schema_version={version}")
    replacement.replace(path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: sessions.ensure_agent_db(), range(8)))
    with closing(sessions.connect()) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        assert {"preserved", "agent_sessions", "agent_jobs", "agent_messages", "agent_notifications"} <= tables
