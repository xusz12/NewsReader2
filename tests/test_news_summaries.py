from __future__ import annotations
import sqlite3, json, threading, time, uuid, subprocess, sys, os
from pathlib import Path
import pytest
import news_summaries as ns

NATIVE_CONFIG_STAMP = ns.native_config_stamp
from scanner import apply_schema


@pytest.fixture
def setup(tmp_path, monkeypatch):
    # Unit tests use deterministic metadata; real runtime metadata is exercised separately.
    monkeypatch.setattr(ns, "native_config_stamp", lambda config: [])
    p = tmp_path / "news.sqlite3"
    c = sqlite3.connect(p)
    apply_schema(c, Path(__file__).resolve().parents[1] / "schema.sql")

    def add(c, i, title=None, dt="2026-10-01"):
        c.execute(
            "INSERT INTO items(id,source_file,item_order,published_at,date,source,source_name,source_type,title,url,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(i),
                "synthetic.md",
                i,
                dt + " 10:00",
                dt,
                "test",
                "Synthetic",
                "site",
                title or f"公司报告订单增加{i}项",
                "https://example.test/" + str(i),
                dt,
                dt,
            ),
        )

    add(c, 1)
    add(c, 2, dt="2026-10-02")
    add(c, 3)
    c.execute(
        "INSERT INTO item_state(item_id,read_at,important_at,updated_at) VALUES(?,?,?,?)",
        ("3", "old-read", "old-important", "old"),
    )
    c.commit()
    c.close()
    state = {"path": p, "route": "route-a"}
    settings = {
        "agent": {
            "runtime": "codex",
            "codex": {"model": "test", "profile": "", "sandbox": "read-only"},
        },
        "llm": {"pi_chat": {"provider": "test", "model": "test"}},
    }
    gate = threading.Event()
    gate.set()
    entered = threading.Event()
    calls = []

    def valid_body():
        return "## 本期变化\n\n2026-10-01，公司报告订单增加1项。[[N0001]]\n\n2026-10-02，公司报告订单增加2项。[[N0002]]"

    def audit():
        return {"body": valid_body()}

    def run(config, prompt, cancel, diag):
        calls.append((config, prompt))
        entered.set()
        while not gate.wait(0.01):
            if cancel():
                raise RuntimeError("summary_cancelled")
        if cancel():
            raise RuntimeError("summary_cancelled")
        return valid_body()

    service = ns.Service(
        lambda: state["path"], lambda: settings, lambda: state["route"], run
    )
    return p, state, service, gate, entered, calls, add, audit


def create(service):
    return service.create(str(uuid.uuid4()), service.listing()["database_token"])[0]


def wait(service, id):
    end = time.time() + 4
    while time.time() < end:
        j = service.job(id)
        if j["status"] not in ns.ACTIVE:
            return j
        time.sleep(0.01)
    raise AssertionError("worker timeout")


def reads(path):
    c = sqlite3.connect(path)
    try:
        return dict(c.execute("SELECT item_id,read_at FROM item_state"))
    finally:
        c.close()


def test_success_atomic_and_history(setup):
    p, state, s, gate, entered, calls, add, audit = setup
    j = create(s)
    r = wait(s, j["id"])
    assert r["status"] == "completed"
    d = s.detail(j["id"])
    assert d["item_count"] == 2 and len(d["sources"]) == 2
    assert d["validation"]["method"].startswith("basic")
    assert len(calls) == 1 and "1项" in calls[0][1] and "2项" in calls[0][1]
    assert reads(p)["1"] and reads(p)["2"] and reads(p)["3"] == "old-read"
    c = sqlite3.connect(p)
    assert (
        c.execute("SELECT important_at FROM item_state WHERE item_id='3'").fetchone()[0]
        == "old-important"
    )
    c.close()
    assert len(s.listing()["items"]) == 1


def test_new_news_manual_read_and_idempotency(setup):
    p, state, s, gate, entered, calls, add, audit = setup
    gate.clear()
    token = s.listing()["database_token"]
    key = str(uuid.uuid4())
    j, _ = s.create(key, token)
    assert entered.wait(2)
    same, created = s.create(key, token)
    assert same["id"] == j["id"] and not created
    same, created = s.create(str(uuid.uuid4()), token)
    assert same["id"] == j["id"] and not created
    c = sqlite3.connect(p)
    add(c, 4)
    c.execute(
        "INSERT INTO item_state(item_id,read_at,important_at,updated_at) VALUES('1','manual','keep','old')"
    )
    c.commit()
    c.close()
    gate.set()
    assert wait(s, j["id"])["status"] == "completed"
    assert "4" not in reads(p) and reads(p)["1"] == "manual"
    same, created = s.create(key, token)
    assert same["id"] == j["id"] and not created
    c = sqlite3.connect(p)
    assert (
        c.execute("SELECT important_at FROM item_state WHERE item_id='1'").fetchone()[0]
        == "keep"
    )
    c.close()


def test_cancel_during_execution(setup):
    p, _, s, gate, entered, _, _, _ = setup
    gate.clear()
    j = create(s)
    assert entered.wait(2)
    s.cancel(j["id"], j["database_token"])
    assert wait(s, j["id"])["status"] == "cancelled"
    assert "1" not in reads(p) and not s.listing()["items"]
    gate.set()


def test_failed_generation(setup):
    p, _, s, *_ = setup
    s.runner = lambda *a: (_ for _ in ()).throw(RuntimeError("summary_runtime_failed"))
    j = create(s)
    assert wait(s, j["id"])["status"] == "failed"
    assert "1" not in reads(p) and not s.listing()["items"]


@pytest.mark.parametrize("bad", ["empty", "whitespace", "not_string", "no_ref", "unknown", "malformed", "oversize"])
def test_basic_validation_fails_closed(setup, bad):
    p, _, s, _, _, _, _, make = setup
    body = make()["body"]
    cases = {
        "empty": "", "whitespace": "  \n ", "not_string": {"body": body},
        "no_ref": "没有引用", "unknown": body.replace("N0001", "N9999"),
        "malformed": body + " [[FAKE]]", "oversize": body + "文" * 800000,
    }
    count = []
    def runner(*a):
        count.append(1)
        return cases[bad]
    s.runner = runner
    j = create(s)
    result = wait(s, j["id"])
    assert result["status"] == "failed" and result["error"] == "summary_validation_failed"
    assert len(count) == 1 and "1" not in reads(p) and not s.listing()["items"]

@pytest.mark.parametrize("body", [
    "2026-10-01报道，公司订单有所变化。[[N0001]]",
    "公司近期订单有所变化，并无披露更多材料。[[N0001]]",
    "公司订单变化。[[N0001]]\n\n独立事件进展。[[N0002]]",
    "## 本期变化[[N0001]]\n\n同日，公司订单变化。[[N0001]]",
    "报道日期不明；材料只给出了标题。[[N0001]]",
])
def test_natural_body_no_audit_report_or_lexical_date_gate(setup, body):
    p, _, s, _, _, calls, _, _ = setup
    s.runner = lambda *a: body
    job = create(s)
    assert wait(s, job["id"])["status"] == "completed"
    assert s.detail(job["id"])["body"] == body
    assert "不保证" in s.detail(job["id"])["validation"]["limitation"]
    assert reads(p)["1"] and reads(p)["2"]


def test_database_switch(setup, tmp_path):
    p, state, s, gate, entered, _, _, _ = setup
    gate.clear()
    j = create(s)
    assert entered.wait(2)
    other = tmp_path / "other.sqlite"
    c = sqlite3.connect(other)
    apply_schema(c, Path(__file__).resolve().parents[1] / "schema.sql")
    c.commit()
    c.close()
    state["path"] = other
    gate.set()
    end = time.time() + 2
    while s.workers and time.time() < end:
        time.sleep(0.01)
    assert not s.listing()["items"] and "1" not in reads(p) and reads(other) == {}
    c = sqlite3.connect(p)
    assert (
        c.execute("SELECT error FROM news_summary_jobs").fetchone()[0]
        == "summary_database_changed"
    )
    c.close()
    with pytest.raises(ValueError, match="summary_database_changed"):
        s.create(str(uuid.uuid4()), j["database_token"])


def test_saved_db_route_change(setup):
    p, state, s, gate, entered, _, _, _ = setup
    gate.clear()
    j = create(s)
    assert entered.wait(2)
    state["route"] = "route-b"
    gate.set()
    end = time.time() + 2
    while s.workers and time.time() < end:
        time.sleep(0.01)
    assert "1" not in reads(p)
    with pytest.raises(ValueError, match="summary_database_changed"):
        s.listing()


def test_original_material_change(setup):
    p, _, s, gate, entered, _, _, _ = setup
    gate.clear()
    j = create(s)
    assert entered.wait(2)
    c = sqlite3.connect(p)
    c.execute("UPDATE items SET title='changed' WHERE id='1'")
    c.commit()
    c.close()
    gate.set()
    assert wait(s, j["id"])["error"] == "summary_source_changed" and "1" not in reads(p)


def test_save_failure_rolls_back_all(setup):
    p, _, s, gate, entered, _, _, _ = setup
    gate.clear()
    j = create(s)
    assert entered.wait(2)
    c = sqlite3.connect(p)
    c.execute(
        "CREATE TRIGGER reject_read BEFORE INSERT ON item_state WHEN NEW.item_id='2' BEGIN SELECT RAISE(ABORT,'blocked');END"
    )
    c.commit()
    c.close()
    gate.set()
    assert (
        wait(s, j["id"])["error"] == "summary_save_failed"
        and "1" not in reads(p)
        and not s.listing()["items"]
    )


def test_readback_failure_rolls_back(setup):
    p, _, s, gate, entered, _, _, _ = setup
    gate.clear()
    j = create(s)
    assert entered.wait(2)
    c = sqlite3.connect(p)
    c.execute(
        "CREATE TRIGGER corrupt_summary AFTER INSERT ON news_summaries BEGIN UPDATE news_summaries SET body='corrupted' WHERE id=NEW.id;END"
    )
    c.commit()
    c.close()
    gate.set()
    assert (
        wait(s, j["id"])["error"] == "summary_save_failed"
        and "1" not in reads(p)
        and not s.listing()["items"]
    )


def test_recovery_does_not_mark_read(setup):
    p, _, s, gate, entered, _, _, _ = setup
    gate.clear()
    j = create(s)
    assert entered.wait(2)
    s.cancel(j["id"], j["database_token"])
    wait(s, j["id"])
    c = sqlite3.connect(p)
    c.execute(
        "UPDATE news_summary_jobs SET status='checking',owner_pid=?", (os.getpid(),)
    )
    c.commit()
    c.close()
    result = s.listing()
    assert result["latest_job"]["error"] == "summary_interrupted" and "1" not in reads(
        p
    )
    gate.set()


def test_empty_and_bad_input(setup):
    p, _, s, *_ = setup
    token = s.listing()["database_token"]
    with pytest.raises(ValueError, match="invalid_request_id"):
        s.create([], token)
    with pytest.raises(ValueError, match="summary_database_changed"):
        s.create(str(uuid.uuid4()), "wrong")
    c = sqlite3.connect(p)
    c.execute(
        "INSERT INTO item_state(item_id,read_at,updated_at) VALUES('1','r','x'),('2','r','x')"
    )
    c.commit()
    c.close()
    with pytest.raises(ValueError, match="summary_empty"):
        create(s)


def test_corruption_after_read_insert_rolls_back(setup):
    p, _, s, gate, entered, _, _, _ = setup
    gate.clear()
    j = create(s)
    assert entered.wait(2)
    c = sqlite3.connect(p)
    c.execute(
        "CREATE TRIGGER corrupt_after_mark AFTER INSERT ON item_state BEGIN UPDATE news_summaries SET body='broken';END"
    )
    c.commit()
    c.close()
    gate.set()
    assert (
        wait(s, j["id"])["error"] == "summary_save_failed"
        and "1" not in reads(p)
        and not s.listing()["items"]
    )


def test_database_replacement_never_writes_new_file(setup, tmp_path):
    p, state, s, gate, entered, _, _, _ = setup
    gate.clear()
    j = create(s)
    assert entered.wait(2)
    copy = tmp_path / "replacement.sqlite"
    source = sqlite3.connect(p)
    dest = sqlite3.connect(copy)
    source.backup(dest)
    dest.close()
    source.close()
    old = tmp_path / "old.sqlite"
    p.rename(old)
    copy.rename(p)
    gate.set()
    end = time.time() + 2
    while s.workers and time.time() < end:
        time.sleep(0.01)
    assert "1" not in reads(p) and "1" not in reads(old)
    view = s.listing()
    assert (
        not view["items"] and view["latest_job"]["error"] == "summary_database_changed"
    )


def test_runtime_config_is_snapshotted(setup):
    p, _, s, gate, entered, calls, _, _ = setup
    gate.clear()
    j = create(s)
    assert entered.wait(2)
    values = s.settings_getter()
    values["agent"]["runtime"] = "pi"
    values["agent"]["codex"]["model"] = "changed"
    gate.set()
    assert wait(s, j["id"])["status"] == "completed"
    assert all(
        c[0]["runtime"] == "codex" and c[0]["codex"]["model"] == "test" for c in calls
    )


def test_native_config_metadata_change_stops(setup, monkeypatch):
    p, _, s, gate, entered, _, _, _ = setup
    gate.clear()
    j = create(s)
    assert entered.wait(2)
    monkeypatch.setattr(ns, "native_config_stamp", lambda config: ["changed"])
    gate.set()
    assert wait(s, j["id"])["error"] == "summary_runtime_failed" and "1" not in reads(p)


def test_cancel_before_launch_registration(setup):
    p, _, s, gate, entered, _, _, _ = setup
    gate.clear()
    j = create(s)
    s.cancel(j["id"], j["database_token"])
    gate.set()
    assert wait(s, j["id"])["status"] == "cancelled" and "1" not in reads(p)


@pytest.mark.parametrize("during_transaction", [False, True])
def test_real_process_crash_recovery(setup, tmp_path, during_transaction):
    p, _, s, *_ = setup
    root = str(Path(__file__).resolve().parents[1])
    code = """
import sys,os,sqlite3,json,uuid
sys.path.insert(0,sys.argv[2]);import news_summaries as n
p=sys.argv[1];c=sqlite3.connect(p);c.row_factory=sqlite3.Row;c.executescript(n.SCHEMA)
s=n.snapshot(c);key=uuid.uuid4().hex
c.execute("INSERT OR IGNORE INTO news_summary_meta VALUES('database_uid','crashdb')")
token=n.sha([n.fingerprint(p),'crashdb'])
c.execute("INSERT INTO news_summary_jobs(id,request_id,database_token,status,owner_pid,snapshot_json,config_json,route_key,created_at,updated_at) VALUES(?,?,?,'checking',?,?,?,?,?,?)",(key,str(uuid.uuid4()),token,os.getpid(),n.js(s),'{}','route-a',n.now(),n.now()));c.commit()
if sys.argv[3]=='1':
 c.execute('BEGIN IMMEDIATE')
 c.execute("INSERT INTO news_summaries VALUES(?,?,?,?,?,?,?,?,?,?,?)",(key,'partial',s['start'],s['end'],2,n.now(),'partial',n.js(s['items']),'{}','{}','bad'))
 c.execute("INSERT INTO item_state(item_id,read_at,updated_at) VALUES('1','bad','bad')")
os._exit(19)
"""
    r = subprocess.run(
        [sys.executable, "-c", code, str(p), root, "1" if during_transaction else "0"]
    )
    assert r.returncode == 19
    x = s.listing()
    assert (
        x["latest_job"]["error"] == "summary_interrupted"
        and not x["items"]
        and "1" not in reads(p)
    )


def test_no_second_request_even_if_it_would_fail(setup):
    p, _, service, _, _, _, _, make = setup
    calls = []
    def run(*a):
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("summary_runtime_failed")
        return make()["body"]
    service.runner = run
    job = create(service)
    assert wait(service, job["id"])["status"] == "completed"
    assert len(calls) == 1 and reads(p)["1"] and reads(p)["2"]

def test_first_prompt_contains_content_requirements(setup):
    p, _, service, _, _, calls, _, _ = setup
    job=create(service)
    assert wait(service,job["id"])["status"]=="completed"
    assert len(calls)==1
    prompt=calls[0][1]
    for requirement in ("日期", "数字", "同案", "事件", "因果", "来源", "不输出JSON"):
        assert requirement in prompt


def test_full_input_limit_before_model_call(setup):
    p, _, s, _, _, calls, _, _ = setup
    c = sqlite3.connect(p)
    c.execute("UPDATE items SET summary=?", ("文" * (800000),))
    c.commit()
    c.close()
    with pytest.raises(ValueError, match="summary_input_limit"):
        create(s)
    assert not calls and "1" not in reads(p)


def test_api_surface(setup, monkeypatch, tmp_path):
    p, _, s, gate, entered, _, _, _ = setup
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(p))
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(tmp_path / "absent.json"))
    import app as module

    monkeypatch.setattr(module, "NEWS_SUMMARY_SERVICE", s)
    client = module.app.test_client()
    x = client.get("/api/news-summaries").get_json()
    token = x["database_token"]
    assert client.post("/api/news-summaries", json=[]).status_code == 400
    assert (
        client.post(
            "/api/news-summaries", json={"request_id": "bad", "database_token": token}
        ).status_code
        == 400
    )
    assert client.get("/api/news-summaries/jobs/missing").status_code == 404
    assert client.get("/api/news-summaries/missing").status_code == 404
    assert client.get("/api/news-summaries?offset=bad").status_code == 400
    key = str(uuid.uuid4())
    gate.clear()
    prepared = client.post("/api/news-summaries/prepare", json={"request_id":key,"database_token":token})
    assert prepared.status_code == 200
    preview_id = prepared.get_json()["preview"]["id"]
    response = client.post(
        "/api/news-summaries", json={"request_id": key, "database_token": token, "preview_id":preview_id}
    )
    assert response.status_code == 202
    j = response.get_json()["job"]
    assert entered.wait(2)
    dup = client.post(
        "/api/news-summaries", json={"request_id": key, "database_token": token, "preview_id":preview_id}
    )
    assert dup.get_json()["job"]["id"] == j["id"]
    cancelled = client.post(
        f'/api/news-summaries/jobs/{j["id"]}/cancel', json={"database_token": token}
    )
    assert cancelled.status_code == 200
    assert wait(s, j["id"])["status"] == "cancelled"
    assert (
        client.get(f'/api/news-summaries/jobs/{j["id"]}').get_json()["job"]["status"]
        == "cancelled"
    )
    assert "1" not in reads(p)
    gate.set()


@pytest.mark.parametrize("runtime", ["codex", "pi"])
def test_native_owned_child_success(setup, monkeypatch, tmp_path, runtime):
    p, _, _, _, _, _, _, make = setup
    fake = tmp_path / "fake.py"
    if runtime == "codex":
        events = [
            {
                "type": "thread.started",
                "thread_id": "11111111-1111-4111-8111-111111111111",
            },
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"id": "x", "type": "agent_message", "text": "完整答案"},
            },
            {"type": "turn.completed"},
        ]
    else:
        events = [{"type": "oar_result", "outcome": "completed", "answer": "完整答案"}]
    fake.write_text(
        "import sys,json\nsys.stdin.read()\nfor e in "
        + repr(events)
        + ":print(json.dumps(e),flush=True)"
    )
    command = lambda *a, **k: [sys.executable, str(fake)]
    monkeypatch.setattr(ns.codex_client, "require_ready", lambda: None)
    monkeypatch.setattr(ns.codex_client, "command", command)
    monkeypatch.setattr(ns, "bridge_command", command)
    diag = []
    answer = ns.run_native(
        {
            "runtime": runtime,
            "codex": {"model": "test", "profile": "", "sandbox": "read-only"},
            "pi_provider": "test",
            "pi_model": "test",
        },
        "test",
        lambda: False,
        diag,
    )
    assert answer == "完整答案" and diag[0]["exit_code"] == 0 and diag[0]["terminal"]


@pytest.mark.parametrize(
    "mode",
    [
        "empty_answer",
        "stream_no_final",
        "bad_json",
        "nonzero",
        "startup",
        "post_terminal",
    ],
)
def test_native_failure_is_not_success(monkeypatch, tmp_path, mode):
    events = [
        {"type": "thread.started", "thread_id": "11111111-1111-4111-8111-111111111111"},
        {"type": "turn.started"},
    ]
    if mode == "empty_answer":
        events += [
            {
                "type": "item.completed",
                "item": {"id": "x", "type": "agent_message", "text": ""},
            },
            {"type": "turn.completed"},
        ]
    elif mode == "stream_no_final":
        events += [
            {
                "type": "item.updated",
                "item": {"id": "x", "type": "agent_message", "text": "流式片段"},
            }
        ]
    elif mode in ("nonzero", "post_terminal"):
        events += [
            {
                "type": "item.completed",
                "item": {"id": "x", "type": "agent_message", "text": "answer"},
            },
            {"type": "turn.completed"},
        ]
    code = "import sys,json\nsys.stdin.read()\n"
    if mode == "startup":
        code = 'import sys\nsys.stderr.write("Model provider custom not found API_KEY=secret")\nsys.exit(1)\n'
    elif mode == "bad_json":
        code += 'print("not-json")\n'
    else:
        code += "for e in " + repr(events) + ": print(json.dumps(e),flush=True)\n"
        if mode == "nonzero":
            code += "sys.exit(2)\n"
        if mode == "post_terminal":
            code += 'print(json.dumps({"type":"turn.completed"}),flush=True)\n'
    fake = tmp_path / "fake.py"
    fake.write_text(code)
    monkeypatch.setattr(ns.codex_client, "require_ready", lambda: None)
    monkeypatch.setattr(
        ns.codex_client, "command", lambda *a, **k: [sys.executable, str(fake)]
    )
    diag = []
    with pytest.raises(RuntimeError):
        ns.run_native(
            {
                "runtime": "codex",
                "codex": {"model": "test", "profile": "", "sandbox": "read-only"},
            },
            "test",
            lambda: False,
            diag,
        )
    assert diag and not any("secret" in str(x) for x in diag)
    if mode == "startup":
        assert (
            diag[0]["exit_code"] == 1 and diag[0]["launch_hint"] == "provider_not_found"
        )


def test_native_cancel_reaps_owned_process(monkeypatch, tmp_path):
    fake = tmp_path / "fake.py"
    fake.write_text("import sys,time\nsys.stdin.read()\ntime.sleep(8)")
    monkeypatch.setattr(ns.codex_client, "require_ready", lambda: None)
    monkeypatch.setattr(
        ns.codex_client, "command", lambda *a, **k: [sys.executable, str(fake)]
    )
    start = time.monotonic()
    diag = []
    with pytest.raises(RuntimeError, match="summary_cancelled"):
        ns.run_native(
            {
                "runtime": "codex",
                "codex": {"model": "test", "profile": "", "sandbox": "read-only"},
            },
            "test",
            lambda: time.monotonic() - start > 0.15,
            diag,
        )
    assert diag[0]["exit_code"] is not None and time.monotonic() - start < 4


def test_chinese_adjacent_dates_are_not_lexically_rejected(setup):
    p, _, service, _, _, _, _, make=setup
    c=service.connection(p)
    try: snap=ns.snapshot(c)
    finally:c.close()
    body=make()["body"].replace("2026-10-01，","2026-10-01报道，")
    assert ns.checked_body(body,snap)[0]==body


def test_pid_reuse_does_not_keep_task_running(setup):
    p, _, s, gate, entered, _, _, _ = setup
    gate.clear()
    j = create(s)
    assert entered.wait(2)
    s.cancel(j["id"], j["database_token"])
    wait(s, j["id"])
    c = sqlite3.connect(p)
    c.execute(
        "UPDATE news_summary_jobs SET status='checking',owner_pid=?,owner_start='old unrelated process'",
        (os.getppid(),),
    )
    c.commit()
    c.close()
    assert s.listing()["latest_job"][
        "error"
    ] == "summary_interrupted" and "1" not in reads(p)
    gate.set()


@pytest.mark.parametrize("runtime", ["codex", "pi"])
def test_tool_events_stop_data_only_summary(monkeypatch, tmp_path, runtime):
    fake = tmp_path / "fake.py"
    if runtime == "codex":
        events = [
            {
                "type": "thread.started",
                "thread_id": "11111111-1111-4111-8111-111111111111",
            },
            {"type": "turn.started"},
            {
                "type": "item.started",
                "item": {
                    "id": "tool",
                    "type": "command_execution",
                    "command": "fake_tool",
                },
            },
        ]
    else:
        events = [{"type": "oar_tool", "tool": "fake_tool"}]
    fake.write_text(
        "import sys,json,time\nsys.stdin.read()\nfor e in "
        + repr(events)
        + ":print(json.dumps(e),flush=True)\ntime.sleep(8)"
    )
    monkeypatch.setattr(ns.codex_client, "require_ready", lambda: None)
    command = lambda *a, **kw: [sys.executable, str(fake)]
    monkeypatch.setattr(ns.codex_client, "command", command)
    monkeypatch.setattr(ns, "bridge_command", command)
    diagnostics = []
    with pytest.raises(RuntimeError, match="summary_tool_used"):
        ns.run_native(
            {
                "runtime": runtime,
                "codex": {"model": "test", "profile": "", "sandbox": "read-only"},
                "pi_provider": "test",
                "pi_model": "test",
            },
            "test",
            lambda: False,
            diagnostics,
        )
    assert diagnostics[0]["exit_code"] is not None and not diagnostics[0]["terminal"]


def test_complete_final_line_without_newline(monkeypatch, tmp_path):
    events = [
        {"type": "thread.started", "thread_id": "11111111-1111-4111-8111-111111111111"},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {"id": "x", "type": "agent_message", "text": "完整答案"},
        },
        {"type": "turn.completed"},
    ]
    fake = tmp_path / "fake.py"
    fake.write_text(
        'import sys,json\nsys.stdin.read()\nsys.stdout.write("\\n".join(json.dumps(e) for e in '
        + repr(events)
        + "))"
    )
    monkeypatch.setattr(ns.codex_client, "require_ready", lambda: None)
    monkeypatch.setattr(
        ns.codex_client, "command", lambda *a, **k: [sys.executable, str(fake)]
    )
    diag = []
    assert (
        ns.run_native(
            {
                "runtime": "codex",
                "codex": {"model": "test", "profile": "", "sandbox": "read-only"},
            },
            "test",
            lambda: False,
            diag,
        )
        == "完整答案"
    )




def test_settings_save_serializes_with_summary_commit(setup, monkeypatch, tmp_path):
    p, state, s, gate, entered, _, _, _ = setup
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(p))
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(tmp_path / "absent.json"))
    import app as module

    monkeypatch.setattr(module, "NEWS_SUMMARY_SERVICE", s)
    monkeypatch.setattr(
        module,
        "validate_runtime_settings",
        lambda body: {"database": {"path": "new-route"}, "api_models": {"jev": {"concurrency": 8, "unread_only": False}}},
    )

    def save(value):
        assert s.lock._is_owned()
        state["route"] = "route-b"

    monkeypatch.setattr(module, "save_app_settings", save)
    monkeypatch.setattr(module, "refresh_agent_session_expiries", lambda: None)
    monkeypatch.setattr(module, "serialize_runtime_settings", lambda: {})
    gate.clear()
    j = create(s)
    assert entered.wait(2)
    assert module.app.test_client().put("/api/settings", json={}).status_code == 200
    gate.set()
    end = time.time() + 2
    while s.workers and time.time() < end:
        time.sleep(0.01)
    assert "1" not in reads(p)


@pytest.mark.parametrize("kind", ["short", "missing"])
def test_natural_dates_do_not_require_a_second_report(setup,kind):
    p,_,service,_,_,_,_,make=setup
    c=service.connection(p)
    try:snap=ns.snapshot(c)
    finally:c.close()
    body=make()["body"].replace("2026-10-02，","10-02，" if kind=="short" else "")
    assert ns.checked_body(body,snap)[0]==body




def test_cancel_after_commit_reports_completed(setup):
    p, _, s, _, _, _, _, _ = setup
    j = create(s)
    assert wait(s, j["id"])["status"] == "completed"
    before = reads(p)
    result = s.cancel(j["id"], j["database_token"])
    assert (
        result["status"] == "completed"
        and not result["cancel_requested"]
        and reads(p) == before
    )


def test_cancel_before_commit_wins(setup, monkeypatch):
    p, _, s, _, _, _, _, _ = setup
    ready = threading.Event()
    release = threading.Event()
    original = ns.checked_body

    def review(value, snapshot):
        result = original(value, snapshot)
        ready.set()
        assert release.wait(3)
        return result

    monkeypatch.setattr(ns, "checked_body", review)
    j = create(s)
    assert ready.wait(2)
    s.cancel(j["id"], j["database_token"])
    release.set()
    assert (
        wait(s, j["id"])["status"] == "cancelled"
        and "1" not in reads(p)
        and not s.listing()["items"]
    )


def test_native_config_fingerprint_is_metadata_only(tmp_path, monkeypatch):
    home = tmp_path / "native"
    project = tmp_path / "project"
    home.mkdir()
    (project / ".codex").mkdir(parents=True)
    global_config = home / "config.toml"
    project_config = project / ".codex/config.toml"
    global_config.write_text('model="fake"')
    project_config.write_text('model="fake2"')
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr(ns.codex_client, "ROOT", project)

    def forbidden(*a, **k):
        raise AssertionError("configuration content read")

    monkeypatch.setattr(Path, "read_text", forbidden)
    before = NATIVE_CONFIG_STAMP({"runtime": "codex"})
    global_config.write_bytes(b'model="fake3 with longer metadata"')
    after = NATIVE_CONFIG_STAMP({"runtime": "codex"})
    assert ns.sha(before) != ns.sha(after) and str(project_config) in str(after)









# Range/preview integration on synthetic fixture only.
def preview(s):
    key=str(uuid.uuid4()); token=s.listing()["database_token"]
    x=s.prepare(key,token)["preview"]
    return key,token,x

def stored_preview(p,x):
    c=sqlite3.connect(p)
    try:return json.loads(c.execute("SELECT snapshot_json FROM news_summary_previews WHERE id=?",(x["id"],)).fetchone()[0])
    finally:c.close()

def selected_runner(s, material):
    calls=[]
    def run(config,prompt,cancel,diag):
        calls.append(prompt)
        if cancel():raise RuntimeError("summary_cancelled")
        i=material["items"][0];ref=i["ref"]
        body=(i["date"]+"报道" if i["date"] else "报道日期不明")+"，"+i["title"]+"。[["+ref+"]]"
        return body
    s.runner=run
    return calls

def test_preview_immutable_new_arrivals_and_all_locked_read(setup):
    p,state,s,gate,entered,calls,add,audit=setup
    s.settings_getter()["news_summary"]={"max_days":1,"max_items":1}
    key,token,x=preview(s)
    material=stored_preview(p,x)
    assert x["scope"]["locked_count"]==2 and x["scope"]["summarized_count"]==1
    assert material["summary_ids"]==["2"] and material["all_ids"]==["1","2"]
    assert x["start_date"]==x["end_date"]=="2026-10-02"
    assert "excluded_ids" not in x["scope"] and not calls and "1" not in reads(p)
    c=sqlite3.connect(p); add(c,4,dt="2026-10-03");c.commit();c.close()
    assert s.prepare(key,token)["preview"]["id"]==x["id"]
    run_calls=selected_runner(s,material)
    j,_=s.create(key,token,x["id"])
    assert wait(s,j["id"])["status"]=="completed"
    assert len(run_calls)==1 and reads(p)["1"] and reads(p)["2"] and "4" not in reads(p)
    d=s.detail(j["id"])
    assert d["item_count"]==1 and len(d["sources"])==1
    assert d["validation"]["scope"]["locked_count"]==2 and d["start_date"]=="2026-10-02"

@pytest.mark.parametrize("change",["settings","expiry","discard","source","wrong-id","wrong-key","wrong-db"])
def test_preview_rejection_does_not_start_or_clear(setup,change):
    p,state,s,gate,entered,calls,add,audit=setup
    key,token,x=preview(s)
    if change=="settings":s.settings_getter()["news_summary"]={"max_items":1}
    if change=="discard":s.discard_preview(x["id"],token)
    if change in ("expiry","source"):
        c=sqlite3.connect(p)
        if change=="expiry":c.execute("UPDATE news_summary_previews SET expires_at=0")
        else:c.execute("UPDATE items SET title='changed' WHERE id='1'")
        c.commit();c.close()
    with pytest.raises(ValueError):
        s.create(str(uuid.uuid4()) if change=="wrong-key" else key,
                 "bad" if change=="wrong-db" else token,
                 "bad" if change=="wrong-id" else x["id"])
    assert not calls and "1" not in reads(p)
    assert s.listing()["total"]==0 and not s.listing()["active"]

def test_limits_change_after_confirm_keeps_fixed_job(setup):
    p,state,s,gate,entered,calls,add,audit=setup
    gate.clear(); key,token,x=preview(s)
    j,_=s.create(key,token,x["id"]);assert entered.wait(2)
    s.settings_getter()["news_summary"]={"max_days":1,"max_items":1}
    gate.set()
    assert wait(s,j["id"])["status"]=="completed" and reads(p)["1"] and reads(p)["2"]
    assert s.detail(j["id"])["config"]["limits"]["max_items"]==6000

@pytest.mark.parametrize("failure",["model","save","source","overflow"])
def test_excluded_locked_rows_never_read_on_failure(setup,monkeypatch,failure):
    p,state,s,gate,entered,calls,add,audit=setup
    s.settings_getter()["news_summary"]={"max_items":1}
    key,token,x=preview(s); material=stored_preview(p,x)
    original=selected_runner(s,material)
    fn=s.runner
    def run(*args):
        if failure=="model":raise RuntimeError("summary_runtime_failed")
        if failure=="source" and not original:
            c=sqlite3.connect(p);c.execute("UPDATE items SET title='changed-excluded' WHERE id='1'");c.commit();c.close()
        return fn(*args)
    s.runner=run
    if failure=="save":
        c=sqlite3.connect(p)
        c.execute("CREATE TRIGGER fail_range_save BEFORE INSERT ON news_summaries BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        c.commit();c.close()
    if failure=="overflow":
        encoder=ns.encoded_input_size
        monkeypatch.setattr(ns,"encoded_input_size",lambda prompt,cfg:ns.MAX_INPUT_BYTES+1)
    j,_=s.create(key,token,x["id"])
    r=wait(s,j["id"])
    assert r["status"]=="failed" and "1" not in reads(p) and "2" not in reads(p)
    assert s.listing()["total"]==0
    if failure=="overflow":assert len(original)==0 and r["error"]=="summary_input_limit"

def test_missing_dates_service_success_without_guesses(setup):
    p,state,s,gate,entered,calls,add,audit=setup
    c=sqlite3.connect(p);c.execute("UPDATE items SET date='invalid',published_at='' WHERE id IN ('1','2')");c.commit();c.close()
    key,token,x=preview(s)
    selected_runner(s,stored_preview(p,x))
    j,_=s.create(key,token,x["id"])
    assert wait(s,j["id"])["status"]=="completed"
    assert x["start_date"]=="日期不明" and "日期不明" in s.detail(j["id"])["body"]
    assert reads(p)["1"] and reads(p)["2"]

def test_size_excluded_whole_rows_success_only(setup):
    p,state,s,gate,entered,calls,add,audit=setup
    c=sqlite3.connect(p);c.execute("UPDATE items SET summary=? WHERE id='1'",("文"*800000,));c.commit();c.close()
    key,token,x=preview(s); material=stored_preview(p,x)
    assert material["summary_ids"]==["2"] and material["scope"]["excluded_ids"]["size"]==["1"]
    assert len(material["all_items"][0]["summary"])==800000
    assert x["scope"]["excluded_by"]=={"date":0,"count":0,"size":1}
    run_calls=selected_runner(s,material)
    j,_=s.create(key,token,x["id"])
    assert wait(s,j["id"])["status"]=="completed" and len(run_calls)==1
    assert reads(p)["1"] and reads(p)["2"]

def test_cancel_with_excluded_rows_no_clear(setup):
    p,state,s,gate,entered,calls,add,audit=setup
    s.settings_getter()["news_summary"]={"max_items":1}
    gate.clear();key,token,x=preview(s)
    j,_=s.create(key,token,x["id"]);assert entered.wait(2)
    s.cancel(j["id"],token)
    assert wait(s,j["id"])["status"]=="cancelled"
    assert "1" not in reads(p) and "2" not in reads(p) and s.listing()["total"]==0
    gate.set()

def test_preview_discard_no_model_or_read(setup):
    p,state,s,gate,entered,calls,add,audit=setup
    key,token,x=preview(s);s.discard_preview(x["id"],token)
    c=sqlite3.connect(p)
    assert c.execute("SELECT count(*) FROM news_summary_previews").fetchone()[0]==0
    assert c.execute("SELECT count(*) FROM news_summary_jobs").fetchone()[0]==0
    c.close()
    assert not calls and "1" not in reads(p)

def test_preview_database_route_rebind_rejected(setup):
    p,state,s,gate,entered,calls,add,audit=setup
    key,token,x=preview(s);state["route"]="route-b"
    with pytest.raises(ValueError,match="summary_database_changed"):s.create(key,token,x["id"])
    assert not calls and "1" not in reads(p)

def test_empty_unread_preview_no_creation(setup):
    p,state,s,gate,entered,calls,add,audit=setup
    c=sqlite3.connect(p)
    c.executemany("INSERT INTO item_state(item_id,read_at,updated_at) VALUES(?,'manual','old')",[("1",),("2",)])
    c.commit();c.close()
    with pytest.raises(ValueError,match="summary_empty"):preview(s)
    assert s.listing()["total"]==0 and not calls

@pytest.mark.parametrize("bad",[{"max_days":0},{"max_items":True},{"max_days":1.5},{"max_items":"10"},None,[]])
def test_limits_settings_api_rejects_invalid(setup,monkeypatch,tmp_path,bad):
    p,_,s,_,_,calls,_,_=setup
    monkeypatch.setenv("NEWS_READER_DB_PATH",str(p))
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH",str(tmp_path/"runtime.json"))
    import app as module
    monkeypatch.setattr(module,"NEWS_SUMMARY_SERVICE",s)
    monkeypatch.setattr(module,"has_secret",lambda *a:False)
    monkeypatch.setattr(module,"refresh_agent_session_expiries",lambda:None)
    client=module.app.test_client()
    payload={"llm":client.get("/api/settings").get_json()["llm"],"news_summary":bad}
    response=client.put("/api/settings",json=payload)
    assert response.status_code==400 and response.get_json()["error"]=="invalid_summary_limits"
    assert not (tmp_path/"runtime.json").exists() and not calls

def test_limits_settings_api_persist_and_unrelated_save(setup,monkeypatch,tmp_path):
    p,_,s,_,_,_,_,_=setup
    monkeypatch.setenv("NEWS_READER_DB_PATH",str(p))
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH",str(tmp_path/"runtime.json"))
    import app as module
    monkeypatch.setattr(module,"NEWS_SUMMARY_SERVICE",s)
    monkeypatch.setattr(module,"has_secret",lambda *a:False)
    monkeypatch.setattr(module,"refresh_agent_session_expiries",lambda:None)
    client=module.app.test_client()
    payload=client.get("/api/settings").get_json()
    assert payload["news_summary"]=={"max_days":20,"max_items":6000}
    payload={"llm":payload["llm"],"news_summary":{"max_days":3,"max_items":2}}
    assert client.put("/api/settings",json=payload).status_code==200
    assert client.get("/api/settings").get_json()["news_summary"]=={"max_days":3,"max_items":2}
    del payload["news_summary"]
    assert client.put("/api/settings",json=payload).status_code==200
    assert json.loads((tmp_path/"runtime.json").read_text())["news_summary"]=={"max_days":3,"max_items":2}


@pytest.mark.parametrize("runtime",["codex","pi"])
def test_entire_job_has_one_complete_owned_process(setup,monkeypatch,tmp_path,runtime):
    p,_,service,_,_,_,_,make=setup
    marker=tmp_path/"calls.txt"
    fake=tmp_path/"once.py"
    answer=make()["body"]
    if runtime=="codex":
        events=[
            {"type":"thread.started","thread_id":"11111111-1111-4111-8111-111111111111"},
            {"type":"turn.started"},
            {"type":"item.completed","item":{"id":"x","type":"agent_message","text":answer}},
            {"type":"turn.completed"},
        ]
    else:
        events=[{"type":"oar_result","outcome":"completed","answer":answer}]
    fake.write_text("import sys,json\nfrom pathlib import Path\nsys.stdin.read()\nwith Path("+repr(str(marker))+").open('a') as f:f.write('call\\n')\nfor event in "+repr(events)+":print(json.dumps(event),flush=True)")
    monkeypatch.setattr(ns.codex_client,"require_ready",lambda:None)
    monkeypatch.setattr(ns.codex_client,"command",lambda *a,**k:[sys.executable,str(fake)])
    monkeypatch.setattr(ns,"bridge_command",lambda *a,**k:[sys.executable,str(fake)])
    service.settings_getter()["agent"]["runtime"]=runtime
    service.runner=ns.run_native
    job=create(service)
    assert wait(service,job["id"])["status"]=="completed"
    assert marker.read_text().splitlines()==["call"]
    c=sqlite3.connect(p)
    diag=json.loads(c.execute("SELECT diagnostic_json FROM news_summary_jobs WHERE id=?",(job["id"],)).fetchone()[0]);c.close()
    assert len(diag)==1 and diag[0]["exit_code"]==0 and diag[0]["terminal"]
    assert reads(p)["1"] and reads(p)["2"]
    assert service.detail(job["id"])["validation"]["method"].startswith("basic")

def test_excluded_sources_cannot_be_cited_as_if_summarized(setup):
    p,_,service,_,_,_,_,_=setup
    service.settings_getter()["news_summary"]={"max_items":1}
    key,token,x=preview(service)
    material=stored_preview(p,x)
    assert material["summary_ids"]==["2"]
    service.runner=lambda *a:"被排除的新闻也声称进入正文。[[N0001]]"
    job,_=service.create(key,token,x["id"])
    assert wait(service,job["id"])["error"]=="summary_validation_failed"
    assert "1" not in reads(p) and "2" not in reads(p)

def test_legacy_success_validation_is_still_readable(setup):
    p,_,service,_,_,calls,_,_=setup
    job=create(service)
    assert wait(service,job["id"])["status"]=="completed"
    old={"method":"automatic-fact-review-and-reference-date-check-v1","checks":[],"limitation":"旧版核对仍不保证无误"}
    c=sqlite3.connect(p);c.execute("UPDATE news_summaries SET validation_json=? WHERE id=?",(json.dumps(old),job["id"]));c.commit();c.close()
    assert service.detail(job["id"])["validation"]==old
    assert len(calls)==1 and service.listing()["total"]==1


def test_single_call_has_no_audit_format_or_draft_reserve(setup):
    p,_,service,_,_,calls,_,_=setup
    key,token,x=preview(service)
    assert set(x["scope"]["input_budget"])=={"generation_bytes","transport_limit_bytes"}
    job,_=service.create(key,token,x["id"])
    assert wait(service,job["id"])["status"]=="completed"
    assert len(calls)==1 and "checks" not in calls[0][1] and "待核草稿" not in calls[0][1]
    assert not hasattr(ns,"audit_prompt") and not hasattr(ns,"audited")

@pytest.mark.parametrize("failure", [False, True])
def test_feed_scope_includes_hidden_source_and_protects_videos_and_arrivals(setup, monkeypatch, failure):
    import app
    p, state, s, gate, entered, calls, add, audit = setup
    settings = s.settings_getter()
    settings["feed"] = {"hidden_source_subkeys": ["x/fxtrader"]}
    settings["news_summary"] = {"max_days": 30, "max_items": 1}
    monkeypatch.setattr(app, "current_runtime_settings", lambda: settings)
    s.scope_getter = app.news_summary_feed_scope
    with sqlite3.connect(p) as c:
        for i in range(4, 35):
            add(c, i, dt="2026-10-02")
        for i in (31, 32, 33):
            c.execute("UPDATE items SET url=? WHERE id=?", (f"https://www.bloomberg.com/news/videos/2026-10-02/{i}", str(i)))
        c.execute("UPDATE items SET url='https://x.com/fxtrader/status/34',source_type='x',source='x',source_name='fxtrader',date='2026-10-03',published_at='2026-10-03 10:00' WHERE id='34'")
    with s.connection(p) as c:
        assert len(ns.select_items(c)) == 33
    # 1,2 and 4..30 visible; 3 was already read.
    key, token, x = preview(s)
    material = stored_preview(p, x)
    assert x["scope"]["locked_count"] == 30
    assert x["scope"]["summarized_count"] == 1
    assert x["scope"]["excluded_count"] == 29
    assert not set(material["all_ids"]) & {"31", "32", "33"}
    with s.connection(p) as c:
        where, args, counts = app.news_summary_feed_scope(c)
        count = c.execute("SELECT COUNT(DISTINCT items.id) FROM items LEFT JOIN item_state st ON st.item_id=items.id " + where, args).fetchone()[0]
    assert count == 30
    assert counts["feed_visible_unread_count"] == 29
    assert x["scope"]["feed_visible_unread_count"] == 29
    assert x["scope"]["hidden_source_unread_count"] == 1
    assert "34" in material["all_ids"]
    assert material["summary_ids"] == ["34"]
    with sqlite3.connect(p) as c:
        add(c, 35, dt="2026-10-03")
    selected_runner(s, material)
    if failure:
        def fail(*args):
            raise RuntimeError("summary_runtime_failed")
        s.runner = fail
    j, _ = s.create(key, token, x["id"])
    result = wait(s, j["id"])
    assert result["status"] == ("failed" if failure else "completed")
    current = reads(p)
    for i in material["all_ids"]:
        assert bool(current.get(i)) is (not failure)
    assert all(not current.get(str(i)) for i in (31, 32, 33, 35))


@pytest.mark.parametrize("action", ["prepare", "create"])
def test_old_visibility_preview_requires_repreview(setup, action):
    p, state, s, gate, entered, calls, add, audit = setup
    key, token, x = preview(s)
    material = stored_preview(p, x)
    material["visibility_scope"] = "feed-visible-v1"
    with sqlite3.connect(p) as c:
        c.execute("UPDATE news_summary_previews SET snapshot_json=? WHERE id=?", (json.dumps(material), x["id"]))
    with pytest.raises(ValueError, match="summary_preview_invalid"):
        if action == "prepare":
            s.prepare(key, token)
        else:
            s.create(key, token, x["id"])
    assert not calls and not reads(p).get("1")
    assert preview(s)[2]["scope"]["locked_count"] == 2



def test_public_diagnostic_whitelists_scalar_evidence_only():
    raw = [{"exit_code": 1, "seconds": 2.5, "terminal": True, "stderr_present": True,
            "stderr": "Authorization: secret", "body": "private news", "config": {"key":"secret"}}]
    assert ns.public_diagnostic(json.dumps(raw)) == {"exit_code":1,"seconds":2.5,"terminal":True,"stderr_present":True}
    assert ns.public_diagnostic('["private news"]') == {}
    assert ns.public_diagnostic('{bad') == {}


def test_overview_does_not_prepare_or_generate_and_tracks_saved_limits(setup):
    p, _, s, _, _, calls, _, _ = setup
    s.settings_getter()["news_summary"] = {"max_days": 1, "max_items": 1}
    overview = s.overview()
    assert overview["locked_count"] == overview["feed_visible_unread_count"] == 2
    assert overview["hidden_source_unread_count"] == 0
    assert overview["limits"] == {"max_days": 1, "max_items": 1}
    assert overview["runtime"] == "codex" and overview["model"] == "test"
    with s.connection(p) as c:
        assert c.execute("SELECT count(*) FROM news_summary_previews").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM news_summary_jobs").fetchone()[0] == 0
    assert not calls and not reads(p).get("1")
    key, token, prepared = preview(s)
    assert prepared["model"] == "test"


def test_history_search_full_body_date_recent_and_pagination(setup):
    p, _, s, *_ = setup
    job = create(s)
    assert wait(s, job["id"])["status"] == "completed"
    assert s.listing(query="公司报告")["total"] == 1
    assert s.listing(query="2026-10-01")["total"] == 1
    assert s.listing(query="no match")["total"] == 0
    assert s.listing(query="%_'")["total"] == 0
    assert s.listing(offset=50)["items"] == []
    assert s.listing()["items"][0]["excerpt"].startswith("## 本期变化")
    with s.connection(p) as c:
        c.execute("UPDATE news_summaries SET created_at='2000-01-01T12:00:00'")
        c.commit()
    assert s.listing(recent=True)["total"] == 0
    assert s.listing(recent=True)["archive_total"] == 1


def test_scope_api_reports_hidden_sources_without_changing_unread(setup, monkeypatch):
    import app
    p, _, s, _, _, calls, _, _ = setup
    settings = s.settings_getter()
    settings["feed"] = {"hidden_source_subkeys": ["x/fxtrader"]}
    monkeypatch.setattr(app, "current_runtime_settings", lambda: settings)
    monkeypatch.setattr(app, "NEWS_SUMMARY_SERVICE", s)
    s.scope_getter = app.news_summary_feed_scope
    with sqlite3.connect(p) as c:
        c.execute("UPDATE items SET url='https://x.com/fxtrader/status/1',source_type='x',source='x',source_name='fxtrader' WHERE id='1'")
    response = app.app.test_client().get('/api/news-summaries/scope')
    assert response.status_code == 200
    data = response.get_json()
    assert data["locked_count"] == 2 and data["hidden_source_unread_count"] == 1
    assert data["feed_visible_unread_count"] == 1
    assert not calls and not reads(p).get("1")


def test_draft_scope_recalculates_inclusive_dates_counts_and_never_saves(setup, monkeypatch):
    import app
    from datetime import date

    class Today(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 3)

    monkeypatch.setattr(ns.selection, "date", Today)
    p, _, s, _, _, calls, add, _ = setup
    monkeypatch.setattr(app, "NEWS_SUMMARY_SERVICE", s)
    s.settings_getter()["news_summary"] = {"max_days": 20, "max_items": 6000}
    with sqlite3.connect(p) as c:
        add(c, 4, dt="2026-10-03")
        add(c, 5, dt="2026-10-03")
        add(c, 6, dt="2026-10-03")
        c.execute("UPDATE items SET date='invalid',published_at='' WHERE id='6'")
    before = reads(p)
    client = app.app.test_client()

    def scope(days, items):
        r = client.post('/api/news-summaries/scope', json={"news_summary": {"max_days": days, "max_items": items}})
        assert r.status_code == 200
        return r.get_json()

    broad = scope(20, 6000)
    assert broad["summarized_count"] == broad["locked_count"] == 5
    assert broad["start_date"] == "2026-10-01"
    assert broad["undated_summarized"] == 1
    narrowed = scope(2, 6000)
    assert narrowed["window_start"] == narrowed["start_date"] == "2026-10-02"
    assert narrowed["window_end"] == narrowed["end_date"] == "2026-10-03"
    assert narrowed["summarized_count"] == 4
    assert narrowed["excluded_by"] == {"date": 1, "count": 0}
    capped = scope(2, 1)
    assert capped["summarized_count"] == 1 and capped["excluded_count"] == 4
    assert capped["start_date"] == capped["end_date"] == "2026-10-03"
    assert capped["excluded_by"] == {"date": 1, "count": 3}
    assert capped["undated_summarized"] == 0
    assert scope(1, 6000)["summarized_count"] == 3
    assert s.settings_getter()["news_summary"] == {"max_days": 20, "max_items": 6000}
    assert client.get('/api/news-summaries/scope').get_json()["summarized_count"] == 5
    assert reads(p) == before and not calls
    with s.connection(p) as c:
        assert c.execute("SELECT count(*) FROM news_summary_previews").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM news_summary_jobs").fetchone()[0] == 0


@pytest.mark.parametrize("body", [None, [], {}, {"news_summary": None}, {"news_summary": {"max_days": 0}}, {"news_summary": {"max_items": 1.5}}, {"news_summary": {"max_days": True}}, {"news_summary": {"max_items": "4"}}])
def test_draft_scope_rejects_invalid_limits(setup, monkeypatch, body):
    import app
    monkeypatch.setattr(app, "NEWS_SUMMARY_SERVICE", setup[2])
    response = app.app.test_client().post('/api/news-summaries/scope', json=body)
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_summary_limits"


def test_draft_scope_handles_empty_unread_and_unconfigured_agent(setup):
    p, _, s, _, _, calls, *_ = setup
    # Settings selection should work before choosing a model.
    s.settings_getter()["agent"]["runtime"] = "pi"
    s.settings_getter()["llm"]["pi_chat"] = {"provider": "", "model": ""}
    assert s.overview({"max_days": 1, "max_items": 1})["summarized_count"] == 1
    with sqlite3.connect(p) as c:
        c.execute("INSERT INTO item_state(item_id,read_at,updated_at) VALUES('1','read','now'),('2','read','now')")
    data = s.overview({"max_days": 1, "max_items": 1})
    assert data["summarized_count"] == data["locked_count"] == 0
    assert data["start_date"] == data["end_date"] == ""
    assert data["excluded_by"] == {"date": 0, "count": 0}
    assert not calls
