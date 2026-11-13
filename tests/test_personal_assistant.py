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


def send(service, text="我更关注政策的长期影响", request_id=None, thread=None):
    thread = thread or service.new_thread()["id"]
    service.send(thread, text, request_id or str(uuid.uuid4()))
    return thread


def test_first_use_requires_user_name_and_preserves_identity(service, tmp_path, monkeypatch):
    monkeypatch.setenv("NEWS_READER_PERSONAL_ASSISTANT_DIR", str(tmp_path / "unnamed"))
    app = Flask(__name__)
    assistant = register(app, service.db_path, service.settings)
    client = app.test_client()
    profile = client.get("/api/personal-assistant/profile").json["profile"]
    assert profile["name"] == ""
    thread = assistant.new_thread()["id"]
    response = client.post(f"/api/personal-assistant/threads/{thread}/messages", json={"text":"你好", "request_id":str(uuid.uuid4())})
    assert response.status_code == 400
    assert response.json["error"] == "assistant_name_required"
    assert assistant.snapshot(thread)["messages"] == []
    assert client.put("/api/personal-assistant/profile", json=profile | {"name":"  "}).status_code == 400
    monkeypatch.setattr("personal_assistant.model_catalogs.safe_catalog", lambda _: {"options":[]})
    assert client.post("/api/personal-assistant/model-options", json=profile).status_code == 200
    updated = client.put("/api/personal-assistant/profile", json=profile | {"name":"我的助手"}).json["profile"]
    assert updated["id"] == profile["id"]
    restarted = Service(assistant.root, assistant.db_path, assistant.settings, lambda **args: "你好")
    assert restarted.profile()["name"] == "我的助手"
    send(restarted, thread=thread)
    assert wait_done(restarted, thread)["job"]["status"] == "done"


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
    assert (service.workspace / "notes/偏好.md").exists()
    service.save_profile(profile | {"name":"小秋", "runtime":"pi", "pi":{"provider":"test","model":"test-model"}})
    restarted = Service(service.root, service.db_path, service.settings, runner)
    assert restarted.profile()["id"] == profile["id"]
    assert restarted.profile()["name"] == "小秋"
    assert restarted.snapshot(first)["messages"] == result["messages"]
    second = send(restarted, "另一条讨论")
    assert wait_done(restarted, second)["job"]["status"] == "done"
    assert "notes/偏好.md" in invocations[-1]["prompt"]
    assert "我更关注政策的长期影响" not in invocations[-1]["prompt"]  # unrelated thread never replayed
    assert invocations[-1]["profile"]["runtime"] == "pi"
    assert invocations[-1]["state_dir"] != invocations[0]["state_dir"]
    assert len(restarted.list_threads()["threads"]) == 2


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
    service.send(first, "重复发送", request_id)
    assert len(service.snapshot(first)["messages"]) == 2
    second = service.new_thread()["id"]
    with pytest.raises(RuntimeError, match="assistant_busy"): service.send(second, "另一个问题", str(uuid.uuid4()))
    with pytest.raises(RuntimeError, match="assistant_busy"): service.save_profile(service.profile() | {"name":"更名"})
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
        return "完成"
    service.runner = succeed
    send(service, "重试", thread=thread)
    assert wait_done(service, thread)["job"]["status"] == "done"


def test_restart_recovers_abandoned_job_and_library_switch(service, tmp_path):
    profile = service.profile()
    thread = service.new_thread()["id"]
    with service.connect() as conn:
        conn.execute("INSERT INTO jobs(id,thread_id,status,created_at,updated_at) VALUES('old',?,'running','then','then')", (thread,))
        conn.execute("INSERT INTO messages(thread_id,job_id,role,content,status,created_at) VALUES(?,'old','assistant','partial','running','then')", (thread,))
    restarted = Service(service.root, service.db_path, service.settings)
    assert restarted.snapshot(thread)["job"]["error"] == "assistant_restarted"
    restarted.db_path = lambda: tmp_path / "other.sqlite3"
    assert not restarted.snapshot(thread)["thread"]["current_library"]
    assert restarted.profile()["id"] == profile["id"]
    with pytest.raises(ValueError, match="另一新闻库"): send(restarted, thread=thread)


def test_interrupted_runtime_cannot_write_memory_after_stop(service):
    service.runner = lambda **args: "done"
    thread = send(service)
    snapshot = wait_done(service, thread)
    server = ToolServer(service.db_path(), service.workspace, service.state_db, snapshot["job"]["id"])
    with pytest.raises(ValueError, match="停止或中断"):
        server.call("memory_write", {"path":"notes/late.md", "content":"late write", "revision":"",
                    "source_message_ids":[snapshot["messages"][0]["id"]]})
    assert not (service.workspace / "notes/late.md").exists()


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
    from test_codex_client import fake_codex
    log = fake_codex(tmp_path, monkeypatch)
    service.runner = runtime.run
    thread = send(service)
    assert wait_done(service, thread)["job"]["status"] == "done"
    send(service, "再说说", thread=thread)
    assert wait_done(service, thread)["job"]["status"] == "done"
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert records[0]["cwd"] == str(service.workspace)
    assert "--sandbox" in records[0]["args"] and "read-only" in records[0]["args"]
    assert "--skip-git-repo-check" in records[0]["args"]  # persistent workspaces are not code repositories
    assert any(arg.startswith("mcp_servers.nr_assistant.args=") for arg in records[0]["args"])
    assert "resume" in records[1]["args"]
    assert "同一讨论最近消息" not in records[1]["prompt"]
    assert "再说说" in records[1]["prompt"]


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
