import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

import pytest
from flask import Flask

from personal_assistant import Service, register
from personal_assistant_tools import Memory, ToolServer, query, schema
import personal_assistant_runtime as runtime
from settings import default_app_settings


@pytest.fixture
def service(tmp_path):
    db = tmp_path / "news.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.executescript((Path(__file__).parents[1] / "schema.sql").read_text())
        conn.execute("INSERT INTO items(id,source_file,item_order,published_at,date,title,created_at,updated_at,url) VALUES('n1','fixture',1,'2026-11-12','2026-11-12','芯片新闻','now','now','https://example.com/news')")
        conn.execute("INSERT INTO item_state(item_id,important_at,favorite_at,updated_at) VALUES('n1','today','today','today')")
        conn.execute("INSERT INTO standalone_ideas(id,title,note,created_at,updated_at) VALUES(1,'猜想','需求增长可能先于价格增长','today','today')")
    settings = default_app_settings()
    settings["agent"]["runtime"] = "codex"
    assistant = Service(tmp_path / "assistant", lambda: db, lambda: settings)
    assistant.save_profile(assistant.profile() | {"name": "测试助手"})
    return assistant


def wait_done(service, thread):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        snapshot = service.snapshot(thread)
        if snapshot["job"]["status"] not in ("queued", "running"):
            return snapshot
        time.sleep(.01)
    pytest.fail("personal assistant worker did not finish")


def send(service, text="我更关注政策的长期影响", request_id=None, thread=None, quote=None):
    return service.send(thread or "main", text, request_id or str(uuid.uuid4()), quote=quote)["thread"]["id"]


def test_unnamed_first_send_creates_main_and_preserves_identity(service, tmp_path, monkeypatch):
    monkeypatch.setenv("NEWS_READER_PERSONAL_ASSISTANT_DIR", str(tmp_path / "unnamed"))
    app = Flask(__name__)
    assistant = register(app, service.db_path, service.settings)
    client = app.test_client()
    profile = client.get("/api/personal-assistant/profile").json["profile"]
    assert profile["name"] == ""
    prompts = []
    assistant.runner = lambda **args: prompts.append(args["prompt"]) or "你好"
    assert client.get("/api/personal-assistant/threads/main").json["thread"] is None
    assert client.post("/api/personal-assistant/threads/main/messages", json={"text":" ", "request_id":str(uuid.uuid4())}).status_code == 400
    assert not assistant.list_threads()["threads"]
    response = client.post("/api/personal-assistant/threads/main/messages", json={"text":"你好", "request_id":str(uuid.uuid4())})
    assert response.status_code == 200
    thread = response.json["thread"]["id"]
    assert wait_done(assistant, thread)["job"]["status"] == "done"
    assert "「个人助手」" in prompts[0]
    profile = client.put("/api/personal-assistant/profile", json=profile | {"name":"  "}).json["profile"]
    monkeypatch.setattr("personal_assistant.model_catalogs.safe_catalog", lambda _: {"options":[]})
    assert client.post("/api/personal-assistant/model-options", json=profile).status_code == 200
    updated = client.put("/api/personal-assistant/profile", json=profile | {"name":"我的助手"}).json["profile"]
    assert updated["id"] == profile["id"]
    restarted = Service(assistant.root, assistant.db_path, assistant.settings, lambda **args: "你好")
    assert restarted.profile()["name"] == "我的助手"
    send(restarted, thread=thread)
    assert wait_done(restarted, thread)["job"]["status"] == "done"


@pytest.mark.parametrize('field', ['profile', 'provider'])
def test_personal_settings_reject_retired_codex_overrides(service, field):
    original = service.profile()
    with pytest.raises(ValueError, match='codex_advanced_config_removed'):
        service.save_profile(original | {'codex': {**original['codex'], field: 'old'}})
    assert service.profile() == original


def test_personal_settings_allow_only_catalog_models_and_migrate_old_defaults(service, monkeypatch, tmp_path):
    monkeypatch.setenv('NEWS_READER_APP_SETTINGS_PATH', str(tmp_path / 'settings.json'))
    monkeypatch.setattr('personal_assistant.model_catalogs.safe_catalog', lambda _: {
        'options': [{'value': 'known', 'reasoning_efforts': ['high']}]})
    original = service.profile()
    with pytest.raises(ValueError, match='codex_model_not_in_catalog'):
        service.save_profile(original | {'codex': {'model': 'manual'}})
    assert service.profile() == original
    updated = service.save_profile(original | {'codex': {'model': 'known', 'reasoning_effort': 'high'}})
    assert updated['codex']['model'] == 'known'
    with service.connect() as conn:
        legacy = json.loads(conn.execute('SELECT data FROM profile WHERE id=1').fetchone()[0])
        legacy['codex'].update(profile='retired', provider='retired')
        conn.execute('UPDATE profile SET data=? WHERE id=1', (json.dumps(legacy),))
    assert service.profile()['codex'] == updated['codex']


def test_read_only_full_library_and_limits(service, tmp_path):
    db = service.db_path()
    assert any(t["name"] == "standalone_ideas" for t in schema(db)["tables"])
    result = query(db, "SELECT i.id,i.title,s.favorite_at FROM items i JOIN item_state s ON s.item_id=i.id WHERE title LIKE ?", ["%芯片%"])
    assert result["rows"] == [["n1", "芯片新闻", "today"]]
    assert query(db, "SELECT note FROM standalone_ideas")["rows"][0] == ["需求增长可能先于价格增长"]
    assert query(db, "SELECT '一' UNION ALL SELECT '二'", limit=1)["truncated"]
    assert query(db, "SELECT printf('%20000s','a')")["truncated"]
    for sql in ["DELETE FROM items", "UPDATE items SET title='oops'", "PRAGMA journal_mode=OFF", "ATTACH DATABASE ':memory:' AS other", "SELECT load_extension('bad')", "CREATE TEMP TABLE bad(a)", "SELECT * FROM items; DROP TABLE items"]:
        with pytest.raises(sqlite3.Error):
            query(db, sql)
    assert query(db, "SELECT count(*) FROM items")["rows"] == [[1]]
    with pytest.raises(sqlite3.OperationalError):
        query(db, "WITH RECURSIVE cnt(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM cnt) SELECT sum(x) FROM cnt")


def test_memory_paths_atomic_conflict_and_forget(service, tmp_path):
    service.ensure()
    memory = service.memory
    index = memory.read("MEMORY.md")
    memory.write("MEMORY.md", index["content"] + "\n[政策](notes/政策.md)\n", index["revision"])
    with pytest.raises(ValueError, match="已更新"):
        memory.write("MEMORY.md", "lost update", index["revision"])
    note = memory.write("notes/政策.md", "依据 → 推断 → 预判", "")
    for path in ["../outside.md", "notes/../MEMORY.md", "/tmp/no.md", "notes/.hidden.md", "notes/child/no.md", "notes//no.md", "notes\\no.md"]:
        with pytest.raises(ValueError): memory.write(path, "bad", "")
    outside = tmp_path / "outside.md"
    outside.write_text("untouched")
    (service.workspace / "notes/link.md").symlink_to(outside)
    with pytest.raises(ValueError): memory.write("notes/link.md", "bad", "")
    assert outside.read_text() == "untouched"
    before = service.profile()
    service.edit_memory("notes/政策.md", {"revision": note["revision"]}, delete=True)
    assert not (service.workspace / "notes/政策.md").exists()
    assert "notes/政策.md" not in memory.read("MEMORY.md")["content"]
    assert service.profile()["epoch"] == before["epoch"] + 1


def test_persistent_identity_threads_memory_and_runtime_switch(service):
    invocations = []
    def runner(**args):
        invocations.append(args)
        assert args["workspace"] == service.workspace
        server = ToolServer(args["db"], args["workspace"], args["state_db"])
        with service.connect() as conn:
            user_id = conn.execute("SELECT max(id) FROM messages WHERE role='user'").fetchone()[0]
        current = server.call("memory_read", {"path": "notes/偏好.md"})
        server.call("memory_write", {"path":"notes/偏好.md", "revision":current["revision"], "content":f"用户关注政策的长期影响。来源：消息 {user_id}", "source_message_ids":[user_id]})
        args["emit"]({"type":"text_delta", "delta":"正在查询"})
        return "已查阅新闻，并保存了你的偏好。"
    service.runner = runner
    profile = service.profile()
    first = send(service)
    result = wait_done(service, first)
    assert result["job"]["status"] == "done"
    assert [m["role"] for m in result["messages"]] == ["user", "assistant"]
    with service.connect() as conn:
        event, = conn.execute("SELECT kind,event_key,target FROM browser_push_events").fetchall()
    assert event["kind"] == "personal_reply" and event["event_key"] == result["job"]["id"]
    assert json.loads(event["target"]) == {"id":first, "job_id":result["job"]["id"], "message_id":result["messages"][-1]["id"]}
    assert (service.workspace / "notes/偏好.md").exists()
    service.save_profile(profile | {"name":"小秋", "runtime":"pi", "pi":{"provider":"test","model":"test-model"}})
    restarted = Service(service.root, service.db_path, service.settings, runner)
    assert restarted.profile()["id"] == profile["id"]
    assert restarted.profile()["name"] == "小秋"
    assert restarted.snapshot(first)["messages"] == result["messages"]
    second = send(restarted, "同一讨论的新模型")
    assert wait_done(restarted, second)["job"]["status"] == "done"
    assert "notes/偏好.md" in invocations[-1]["prompt"]
    assert "我更关注政策的长期影响" in invocations[-1]["prompt"]
    assert invocations[-1]["profile"]["runtime"] == "pi"
    assert invocations[-1]["state_dir"] != invocations[0]["state_dir"]
    assert first == second and len(restarted.list_threads()["threads"]) == 1


def test_resumed_discussions_refresh_only_changed_memory(service):
    note = "notes/关注方向.md"
    service.memory.write(note, "最初的专题内容", "")
    received = []

    def runner(**args):
        received.append(args["resume_prompt"])
        if received[-1].endswith("修正关注方向"):
            current = service.memory.read(note)
            service.memory.write(note, "修改后的专题内容", current["revision"])
        return "完成"

    def ask(thread, text="继续"):
        send(service, text, thread=thread)
        assert wait_done(service, thread)["job"]["status"] == "done"
        return received[-1]

    service.runner = runner
    first = "main"
    prompt = ask(first)
    assert "<memory>" in prompt and note in prompt
    assert "最初的专题内容" not in prompt

    unchanged = ask(first, "修正关注方向")
    assert "<memory>" not in unchanged and note not in unchanged
    assert "本轮用户消息（ID " in unchanged and unchanged.endswith("修正关注方向")
    refreshed = ask(first)
    assert "<memory>" in refreshed and note in refreshed
    assert "修改后的专题内容" not in refreshed

    service = Service(service.root, service.db_path, service.settings, runner)
    current = service.memory.read(note)
    service.memory.write(note, current["content"], current["revision"])
    assert "<memory>" not in ask(first)

    index = service.memory.read("MEMORY.md")
    service.memory.write("MEMORY.md", "今后先看证据", index["revision"])
    assert "今后先看证据" in ask(first)
    assert "今后先看证据" not in ask(first)
    service.memory.delete(note, current["revision"])
    refreshed = ask(first)
    assert "<memory>今后先看证据</memory>" in refreshed and note not in refreshed
    assert "<memory>" not in ask(first)


def test_busy_cancel_idempotency_and_config_guard(service):
    started = threading.Event()
    def runner(**args):
        started.set()
        while not args["stopped"](): time.sleep(.01)
        raise RuntimeError("assistant_stopped")
    service.runner = runner
    request_id = str(uuid.uuid4())
    first = send(service, request_id=request_id)
    assert started.wait(2)
    service.send(first, "我更关注政策的长期影响", request_id)
    assert len(service.snapshot(first)["messages"]) == 2
    with pytest.raises(RuntimeError, match="assistant_busy"): service.send("main", "另一个问题", str(uuid.uuid4()))
    assert service.save_profile(service.profile() | {"name":"更名"})["name"] == "更名"
    with pytest.raises(RuntimeError, match="assistant_busy"): service.edit_memory("MEMORY.md", service.memory.read("MEMORY.md"))
    service.stop(first)
    assert wait_done(service, first)["job"]["status"] == "stopped"
    service.runner = lambda **args: "继续回复"
    send(service, "继续", thread=first)
    assert wait_done(service, first)["job"]["status"] == "done"


def test_failed_turn_is_not_replayed_as_authoritative(service):
    def fail(**args):
        args["emit"]({"type":"text_delta", "delta":"partial private claim"})
        args["state_dir"].mkdir(parents=True)
        (args["state_dir"] / "broken.json").write_text("bad")
        raise RuntimeError("secret=DO_NOT_EXPOSE")
    service.runner = fail
    thread = send(service)
    result = wait_done(service, thread)
    assert result["job"]["error"] == "assistant_failed"
    assert "DO_NOT_EXPOSE" not in json.dumps(result)
    assert not list((service.root / "runtime" / thread).glob("*/broken.json"))
    def succeed(**args):
        assert "partial private claim" not in args["prompt"]
        assert "<memory>" in args["resume_prompt"]
        return "完成"
    service.runner = succeed
    send(service, "重试", thread=thread)
    assert wait_done(service, thread)["job"]["status"] == "done"


def test_restart_recovers_abandoned_job(service):
    profile = service.profile()
    service.runner = lambda **args: "完成"
    thread = send(service)
    wait_done(service, thread)
    with service.connect() as conn:
        conn.execute("INSERT INTO jobs(id,thread_id,status,created_at,updated_at) VALUES('old',?,'running','then','then')", (thread,))
        conn.execute("INSERT INTO messages(thread_id,job_id,role,content,status,created_at) VALUES(?,'old','assistant','partial','running','then')", (thread,))
    restarted = Service(service.root, service.db_path, service.settings)
    assert restarted.snapshot(thread)["job"]["error"] == "assistant_restarted"
    assert restarted.snapshot("main")["thread"]["id"] == thread
    assert restarted.profile()["id"] == profile["id"]


def test_interrupted_runtime_cannot_write_memory_after_stop(service):
    service.runner = lambda **args: "done"
    thread = send(service)
    snapshot = wait_done(service, thread)
    server = ToolServer(service.db_path(), service.workspace, service.state_db, snapshot["job"]["id"])
    with pytest.raises(ValueError, match="停止或中断"):
        server.call("memory_write", {"path":"notes/late.md", "content":"late write", "revision":"",
                    "source_message_ids":[snapshot["messages"][0]["id"]]})
    assert not (service.workspace / "notes/late.md").exists()


def test_stop_waits_for_atomic_memory_commit_and_blocks_later_writes(service, monkeypatch):
    committing, release, returned = threading.Event(), threading.Event(), threading.Event()
    original = Memory._write
    def paused_write(memory, path, content):
        if path.name == 'in-flight.md':
            committing.set()
            assert release.wait(3)
        original(memory, path, content)
    monkeypatch.setattr(Memory, '_write', paused_write)
    def runner(**args):
        server = ToolServer(args['db'], args['workspace'], args['state_db'], args['job_id'])
        server.call('memory_write', {'path': 'notes/in-flight.md', 'content': '政策偏好',
                                    'revision': '', 'source_message_ids': [1]})
        while not args['stopped']():
            time.sleep(.01)
        raise RuntimeError('assistant_stopped')
    service.runner = runner
    thread = send(service)
    assert committing.wait(2)
    def stop():
        service.stop(thread)
        returned.set()
    worker = threading.Thread(target=stop)
    worker.start()
    try:
        assert not returned.wait(.15)
    finally:
        release.set()
        worker.join(timeout=3)
    assert returned.is_set()
    snapshot = wait_done(service, thread)
    assert snapshot['job']['status'] == 'stopped'
    assert '政策偏好' in service.memory.read('notes/in-flight.md')['content']
    server = ToolServer(service.db_path(), service.workspace, service.state_db, snapshot['job']['id'])
    with pytest.raises(ValueError, match='停止或中断'):
        server.call('memory_write', {'path': 'notes/late.md', 'content': 'later',
                                    'revision': '', 'source_message_ids': [1]})
    assert not (service.workspace / 'notes/late.md').exists()


def test_mcp_protocol_and_source_validation(service):
    service.ensure()
    server = ToolServer(service.db_path(), service.workspace, service.state_db)
    init = server.respond({"id":1,"method":"initialize"})
    assert init["result"]["capabilities"] == {"tools": {}}
    assert server.respond({"method":"notifications/initialized"}) is None
    assert len(server.respond({"id":2,"method":"tools/list"})["result"]["tools"]) == 6
    bad = server.respond({"id":3,"method":"tools/call","params":{"name":"memory_write","arguments":{"path":"notes/no.md","content":"猜测","revision":"","source_message_ids":[999]}}})
    assert bad["result"]["isError"]
    assert not (service.workspace / "notes/no.md").exists()
    command = runtime.tool_config(service.db_path(), service.workspace, service.state_db)
    response = subprocess.run([command["command"], *command["args"]], input=json.dumps({"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"nr_query","arguments":{"sql":"SELECT count(*) FROM items"}}})+"\n", text=True, capture_output=True, timeout=5)
    assert response.returncode == 0
    assert json.loads(json.loads(response.stdout)["result"]["content"][0]["text"])["rows"] == [[1]]


def test_codex_adapter_registers_tools_and_resumes_in_own_workspace(service, tmp_path, monkeypatch):
    script = tmp_path / 'codex'
    script.write_text(f'#!{sys.executable}\n' + (Path(__file__).parent / 'fixtures/fake_codex_app_server.py').read_text())
    script.chmod(0o755)
    log = tmp_path / 'rpc.jsonl'
    monkeypatch.setenv('NEWS_READER_CODEX', str(script))
    monkeypatch.setenv('NR_RPC_LOG', str(log))
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex-home'))
    service.runner = runtime.run
    thread = send(service)
    assert wait_done(service, thread)['job']['status'] == 'done'
    send(service, '再说说', thread=thread)
    assert wait_done(service, thread)['job']['status'] == 'done'
    with service.connect() as connection:
        connection.execute('DROP TABLE thread_memory_versions')
    service = Service(service.root, service.db_path, service.settings, runtime.run)
    send(service, '升级后继续', thread=thread)
    assert wait_done(service, thread)['job']['status'] == 'done'
    state = service.root / 'runtime' / thread / str(service.profile()['epoch'])
    (state / 'codex-session.json').unlink()
    send(service, '重建后继续', thread=thread)
    assert wait_done(service, thread)['job']['status'] == 'done'
    records = [json.loads(line) for line in log.read_text().splitlines()]
    opens = [r for r in records if r['method'] in ('thread/start', 'thread/resume')]
    assert [r['method'] for r in opens] == ['thread/start', 'thread/resume', 'thread/resume', 'thread/start']
    for record in opens:
        assert record['params']['cwd'] == str(service.workspace)
        assert 'sandbox_mode="read-only"' in record['args']
        assert 'project_doc_max_bytes=0' in record['args']
        tools = record['params']['config']['mcp_servers']['nr_assistant']
        assert tools['command'] == str(Path(sys.executable).resolve())
        assert '--job-id' in tools['args']
        assert tools['enabled'] is True
    turns = [r['params']['input'][0]['text'] for r in records if r['method'] == 'turn/start']
    assert ['<memory>' in turn for turn in turns] == [True, False, True, True]
    assert '同一讨论最近消息' not in turns[1]
    assert '再说说' in turns[1]
    assert '同一讨论最近消息' not in turns[2] and '升级后继续' in turns[2]
    assert '同一讨论最近消息' in turns[3] and '重建后继续' in turns[3]


def test_blueprint_validation_and_independent_configuration(service, monkeypatch):
    monkeypatch.setenv("NEWS_READER_PERSONAL_ASSISTANT_DIR", str(service.root))
    app = Flask(__name__)
    registered = register(app, service.db_path, service.settings)
    registered.runner = lambda **args: "已完成"
    client = app.test_client()
    profile = client.get("/api/personal-assistant/profile").json["profile"]
    saved = client.put("/api/personal-assistant/profile", json=profile | {"name":"望远"})
    assert saved.status_code == 200
    assert service.settings()["agent"]["runtime"] == "codex"
    assert client.put("/api/personal-assistant/profile", json=profile).status_code == 400
    assert client.post("/api/personal-assistant/threads/missing/messages", json={"text":"hi","request_id":str(uuid.uuid4())}).status_code == 404
    assert client.put("/api/personal-assistant/profile", json=[]).status_code == 400
    assert client.put("/api/personal-assistant/memory/notes/no.md", json={"content":"test","revision":""}).status_code == 200
    assert client.get("/api/personal-assistant/memory").json["files"][-1]["path"] == "notes/no.md"


def test_simultaneous_first_sends_and_searchable_quote_survive_source_deletion(service):
    from concurrent.futures import ThreadPoolExecutor
    service = Service(service.root.parent / "cold-assistant", service.db_path, service.settings)
    quote = {"item_id":"n1", "title":"原来的标题" + "甲" * 2100, "url":"https://example.test/n1", "quote_text":"作者提到了久期",
             "quote_source":"conclusion", "quote_paragraph":1}
    request_id = str(uuid.uuid4())
    release = threading.Event()
    calls = []
    def runner(**kwargs):
        calls.append(kwargs)
        assert release.wait(5)
        return "回复"
    service.runner = runner
    other = Service(service.root, service.db_path, service.settings, runner)
    gate = threading.Barrier(2)
    def submit(instance):
        gate.wait(timeout=5)
        return instance.send("main", "这是什么意思？", request_id, quote=quote)["thread"]["id"]
    try:
        with ThreadPoolExecutor(2) as workers:
            ids = list(workers.map(submit, (service, other)))
        assert len(set(ids)) == 1 and len(service.list_threads()["threads"]) == 1
        with pytest.raises(RuntimeError, match="assistant_busy"):
            send(other, "另一个标签页的问题", quote=quote)
        with pytest.raises(ValueError, match="不同内容"):
            other.send("main", "这是什么意思？", request_id, quote=quote | {"quote_source":"points"})
    finally:
        release.set()
    wait_done(service, ids[0])
    assert len(calls) == 1
    with sqlite3.connect(service.db_path()) as db:
        db.execute("DELETE FROM items WHERE id='n1'")
    second = quote | {"quote_source":"translated", "quote_text":"另一份材料"}
    send(other, "这是什么意思？", quote=second)
    snapshot = wait_done(other, ids[0])
    assert [m["quote"] for m in snapshot["messages"] if m["role"] == "user"] == [quote,second]
    tools = ToolServer(service.db_path(), service.workspace, service.state_db)
    for term in ("久期", "原来的标题", "这是什么意思"):
        results = tools.call("discussion_search", {"text":term})
        row = dict(zip(results["columns"], results["rows"][-1]))
        assert row["id"] == snapshot["messages"][0]["id"]
        assert row["quote_text"] == quote["quote_text"] and row["content"] == "这是什么意思？"


def test_retry_snapshot_and_edit_during_execution(service):
    seen, release, started = [], threading.Event(), threading.Event()
    original = {"item_id":"n1", "title":"标题", "url":"", "quote_text":"用户递入的材料",
                "quote_source":"points", "quote_paragraph":None}
    def runner(**args):
        seen.append(args)
        if len(seen) == 1:
            started.set()
            assert release.wait(4)
            raise RuntimeError("assistant_timeout")
        return "完成"
    service.runner = runner
    thread = send(service, "原来的问题", quote=original)
    assert started.wait(3)
    service.save_profile(service.profile() | {"runtime":"pi","pi":{"provider":"fixture","model":"other"}})
    release.set()
    failed = wait_done(service, thread)
    service.send(thread, None, str(uuid.uuid4()), failed["job"]["id"])
    retry = wait_done(service, thread)
    assert [m["id"] for m in retry["messages"]] == [m["id"] for m in failed["messages"]]
    assert retry["messages"][0]["quote"] == original
    assert "用户递入的材料" in seen[1]["prompt"] and "原来的问题" in seen[1]["resume_prompt"]
    send(service, "新模型的新轮")
    assert wait_done(service, thread)["job"]["status"] == "done"
    assert [call["profile"]["runtime"] for call in seen] == ["codex","codex","pi"]
    assert seen[0]["state_dir"] == seen[1]["state_dir"] != seen[2]["state_dir"]


def test_pi_profile_validates_explicit_effort_and_accepts_default(service, monkeypatch):
    monkeypatch.setattr("pi_models.metadata", lambda _: {"reasoning_efforts":["off", "low"]})
    base = service.profile() | {"runtime":"pi", "pi":{"provider":"local","model":"sample"}}
    with pytest.raises(ValueError, match="invalid_pi_reasoning_effort"):
        service.save_profile(base | {"pi":base["pi"] | {"reasoning_effort":"high"}})
    assert "reasoning_effort" not in service.save_profile(base)["pi"]


def test_rebuild_history_budget_includes_quotes_and_search_can_page_long_originals(service):
    prompts = []
    def runner(**args):
        prompts.append(args["prompt"])
        return "答" * 30000 if len(prompts) == 3 else "完成"
    service.runner = runner
    quote = {"item_id":"n1","title":"历史标题","url":"","quote_source":"original","quote_paragraph":None,"quote_text":"第一篇" + "甲" * 15000}
    thread = send(service, "最早问题", quote=quote)
    wait_done(service, thread)
    send(service, "第二问", quote=quote | {"quote_text":"新引文" + "乙" * 15000})
    wait_done(service, thread)
    send(service, "第三问")
    wait_done(service, thread)
    send(service, "第四问")
    wait_done(service, thread)
    def history_json(prompt):
        data = prompt.split("同一讨论最近消息（更早的内容按需查 discussion_search）：\n", 1)[1].split("\n本轮用户消息", 1)[0]
        assert len(data) <= 24000
        return json.loads(data)
    history = history_json(prompts[2])
    assert any(m.get("quote", {}).get("quote_text", "").startswith("新引文") for m in history)
    assert all("最早问题" != m["content"] for m in history)
    last, = history_json(prompts[3])
    assert last["truncated"] and len(last["content"]) > 23000
    tools = ToolServer(service.db_path(), service.workspace, service.state_db)
    result = tools.call("discussion_search", {"message_id":1,"offset":12000})
    record = dict(zip(result["columns"], result["rows"][0]))
    assert record["quote_text"] == quote["quote_text"][12000:] and record["quote_length"] == len(quote["quote_text"])


def test_upgrade_selects_latest_user_statement_and_paginates_old_notifications(service):
    with service.connect() as conn:
        conn.execute("DROP TABLE assistant_main_thread")
        conn.execute("DROP TABLE message_quotes")
        for thread, stamp in (("old","2026-10-01"), ("recent","2026-11-02"), ("empty","2026-11-03")):
            conn.execute("INSERT INTO threads VALUES(?,?,?,?,?)", (thread,thread,"fixture",stamp,stamp))
            if thread == "empty":
                continue
            for i in range(55):
                job = f"{thread}-{i}"
                conn.execute("INSERT INTO jobs(id,thread_id,status,created_at,updated_at) VALUES(?,?,'done',?,?)",(job,thread,stamp,stamp))
                for role in ("user","assistant"):
                    conn.execute("INSERT INTO messages(thread_id,job_id,role,content,status,created_at) VALUES(?,?,?,?,'done',?)",
                                 (thread,job,role,f"{thread}-{role}-{i}",stamp))
        conn.execute("UPDATE threads SET updated_at='2026-11-04' WHERE id='old'")
        before = [tuple(row) for row in conn.execute("SELECT * FROM messages ORDER BY id")]
    for _ in range(2):
        service = Service(service.root, service.db_path, service.settings)
        latest = service.snapshot("main")
        assert latest["thread"]["id"] == "recent" and latest["has_more"]
        older = service.snapshot("recent", before=latest["messages"][0]["id"])
        assert not older["has_more"] and len(older["messages"] + latest["messages"]) == 110
        target = service.snapshot("old", message_id=4)
        assert not target["thread"]["is_main"] and target["messages"][-1]["id"] == 4
        with pytest.raises(ValueError, match="返回主对话"):
            send(service, "不能发往旧讨论", thread="old")
        with service.connect() as conn:
            assert before == [tuple(row) for row in conn.execute("SELECT * FROM messages ORDER BY id")]
