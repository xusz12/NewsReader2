"""Transport checks use local processes and a local HTTP model, never user credentials."""
import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid

import pytest

import report_model as model


def fake_codex(tmp_path, monkeypatch, events, *, linger=False, exit_code=0):
    script = tmp_path / "fake.py"
    script.write_text("import sys,json,time\nsys.stdin.read()\n" +
                      "\n".join("print(" + repr(json.dumps(event)) + ",flush=True)" for event in events) +
                      ("\ntime.sleep(30)\n" if linger else "\n") + f"sys.exit({exit_code})\n")
    monkeypatch.setattr(model.codex_client, "require_ready", lambda:None)
    monkeypatch.setattr(model.oar_client, "bridge_command", lambda *_:[sys.executable, str(script)])
    monkeypatch.setattr(model, "codex_restrictions", lambda config, bridge=None:[])
    return {"runtime":"codex", "codex":{}}


START = [{"type":"oar_session", "id":"019a1234-1234-7123-8123-123456789abc", "runtime":"oar-codex"}]


def test_report_catalog_hides_opencode_free_models_without_changing_other_providers(monkeypatch):
    options = [
        {'provider':'opencode', 'value':'big-pickle'},
        {'provider':'opencode-go', 'value':'example-free'},
        {'provider':'opencode', 'value':'zero-priced', 'cost':{'input':0, 'output':0}},
        {'provider':'opencode', 'value':'paid-model', 'cost':{'input':1, 'output':2}},
        {'provider':'local', 'value':'local-free', 'cost':{'input':0, 'output':0}},
    ]
    monkeypatch.setattr(model, 'pi_metadata', lambda request:{'error':'', 'options':options})
    result = model.catalog({}, 'pi')
    assert [row['value'] for row in result['options']] == ['paid-model', 'local-free']
    assert len(options) == 5


@pytest.mark.parametrize("linger,exit_code,error", [(False, 0, ""), (True, 0, "report_model_timeout"),
                                                        (False, 1, "report_model_failed")])
def test_report_requires_clean_oar_completion_and_exit(tmp_path, monkeypatch, linger, exit_code, error):
    events = START + [{"type":"oar_result", "outcome":"completed", "answer":'{"ok":true}'}]
    config = fake_codex(tmp_path, monkeypatch, events, linger=linger, exit_code=exit_code)
    if error:
        with pytest.raises(RuntimeError, match="^" + error + "$"):
            model.run(config, "private input", timeout=.3)
    else:
        assert model.run(config, "private input", timeout=3) == '{"ok":true}'


@pytest.mark.parametrize("events,error", [
    (START + [{"type":"text_delta", "delta":"partial"}], "report_model_failed"),
    (START + [{"type":"oar_result", "outcome":"failed", "error":"codex_turn_failed"}], "report_model_failed"),
    (START + [{"type":"oar_tool", "tool":"commandExecution"}], "report_tools_not_allowed"),
    (START + [{"type":"oar_result", "outcome":"completed", "answer":"done"}]*2, "report_invalid_result"),
    (START + [{"type":"oar_result", "outcome":"completed", "answer":"done"}, {"type":"text_delta", "delta":"late"}], "report_invalid_result"),
])
def test_partial_failed_or_tool_turn_cannot_become_report(tmp_path, monkeypatch, events, error):
    config = fake_codex(tmp_path, monkeypatch, events)
    with pytest.raises(RuntimeError, match="^" + error + "$"):
        model.run(config, "private input", timeout=3)


def test_timeout_terminates_only_owned_process(tmp_path, monkeypatch):
    config = fake_codex(tmp_path, monkeypatch, START, linger=True)
    with pytest.raises(RuntimeError, match="report_model_timeout"):
        model.run(config, "input", timeout=.2)


def test_workspace_accepts_only_owned_mcp_and_root_completion(tmp_path, monkeypatch):
    from report_workspace import Workspace
    child = "019a1234-1234-7123-8123-123456789abd"
    events = START + [
        {"type": "oar_tool", "tool": "mcp__newsreader_report__report_sources"},
        {"type": "oar_report_agent", "id": child},
        {"type": "oar_report_agent", "id": child},
        {"type": "oar_report_collaboration", "tool": "wait", "phase": "completed", "receiver_thread_ids": [child]},
        {"type": "oar_result", "outcome": "completed", "answer": "root done", "usage": {"input": 123, "output": 20}},
    ]
    config = fake_codex(tmp_path, monkeypatch, events)
    config["limits"] = {"input_tokens": 100000}
    workspace = Workspace({"kind": "monthly", "sources": []}, config)
    assert model.run({**config, "_workspace": workspace}, "test", timeout=3) == "root done"
    assert workspace.state["calls"][0]["usage"]["input"] == 123
    assert workspace.state["calls"][0]["delegate_threads_observed"] == 1
    assert workspace.state["calls"][0]["collaboration_calls"] == {"wait": 1}
    events[1]["tool"] = "mcp__outside__report_sources"
    config = fake_codex(tmp_path, monkeypatch, events)
    with pytest.raises(RuntimeError, match="report_tools_not_allowed"):
        model.run({**config, "_workspace": workspace}, "test", timeout=3)


def test_service_stop_interrupts_running_transport(tmp_path, monkeypatch):
    config = fake_codex(tmp_path, monkeypatch, START, linger=True)
    with pytest.raises(RuntimeError, match="report_interrupted"):
        model.run({**config, "_cancelled": lambda: True}, "input", timeout=3)


def test_workspace_enables_native_delegation_and_only_private_tools(monkeypatch):
    from report_tools import Bridge
    from report_workspace import Workspace
    import tomllib
    monkeypatch.setattr(model.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess([], 0, b'[{"name":"private","url":"secret"}]'))
    workspace = Workspace({"kind": "monthly", "sources": []}, {"limits": {"input_tokens": 100000}})
    with Bridge(workspace) as bridge:
        args = model.codex_restrictions({"model": "gpt-6.1-terra", "reasoning_effort": "ultra"}, bridge)
    config = tomllib.loads(next(arg for arg in args if arg.startswith("mcp_servers=")))
    assert config["mcp_servers"]["private"] == {"enabled": False, "command": "false"}
    owned = config["mcp_servers"]["newsreader_report"]
    assert owned["enabled"] and owned["required"] and "report_finish" in owned["enabled_tools"]
    assert "agents.enabled=true" in args and "secret" not in " ".join(args)
    assert args[args.index("multi_agent")-1] == "--enable"



def test_saved_mcp_servers_disabled_without_copying_secrets_into_argv(monkeypatch):
    tomllib = pytest.importorskip("tomllib")
    calls = []
    def probe(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps([
            {"name":"http-service", "url":"https://example.test/private-token"},
            {"name":"local-service", "env":{"SECRET":"private-secret"}},
        ]).encode())
    monkeypatch.setattr(model.subprocess, "run", probe)
    monkeypatch.setattr(model.codex_client, "executable", lambda:"codex")
    args = model.codex_restrictions({})
    assert calls == [["codex", "mcp", "list", "--json"]]
    config = tomllib.loads(next(arg for arg in args if arg.startswith("mcp_servers=")))
    assert set(config["mcp_servers"]) == {"http-service", "local-service"}
    assert all(entry == {"enabled":False, "command":"false"} for entry in config["mcp_servers"].values())
    assert "private" not in " ".join(args)
    assert 'web_search="disabled"' in args


@pytest.mark.parametrize("finish_reason,effort", [("stop", ""), ("length", ""), ("stop", "high"), ("stop", "off")])
@pytest.mark.parametrize("provider", ["report-test", "opencode", "opencode-go"])
def test_pi_oar_agent_uses_configured_model_without_tools_and_rejects_truncation(tmp_path, monkeypatch, finish_reason, effort, provider):
    if not shutil.which("node") or not (model.ROOT / "node_modules/@earendil-works/pi-coding-agent").exists():
        pytest.skip("Pi SDK requires npm ci")
    requests = []
    sessions = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            sessions.append(self.headers.get("x-opencode-session"))
            if provider.startswith("opencode") and not sessions[-1]:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"Missing x-opencode-session header"}}')
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in [
                {"id":"fixture", "object":"chat.completion.chunk", "model":"fixture", "choices":[{"index":0, "delta":{"role":"assistant", "content":'{"ok":true}'}, "finish_reason":None}]},
                {"id":"fixture", "object":"chat.completion.chunk", "model":"fixture", "choices":[{"index":0, "delta":{}, "finish_reason":finish_reason}]},
            ]:
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    (tmp_path / "auth.json").write_text("{}")
    (tmp_path / "settings.json").write_text(json.dumps({"defaultThinkingLevel":"low"}))
    (tmp_path / "models.json").write_text(json.dumps({"providers":{provider:{
        "baseUrl":f"http://127.0.0.1:{server.server_port}/v1", "api":"openai-completions", "apiKey":"fixture-key",
        "models":[{"id":"fixture", "name":"Fixture", "reasoning":True, "input":["text"],
                   "cost":{"input":1,"output":1,"cacheRead":0,"cacheWrite":0}, "contextWindow":131072, "maxTokens":16384}]
    }}}))
    monkeypatch.setenv("NEWS_READER_PI_AGENT_DIR", str(tmp_path))
    monkeypatch.setenv("PI_OFFLINE", "1")
    monkeypatch.delenv("NEWS_READER_NODE", raising=False)
    monkeypatch.setattr(model, "_pi_subprocess_env", lambda: os.environ.copy())
    monkeypatch.setattr(model.pi_models, "_pi_subprocess_env", lambda: os.environ.copy())
    try:
        config = {"runtime":"pi", "provider":provider, "model":"fixture"}
        catalog = model.catalog({}, "pi")
        option = next(m for m in catalog["options"] if m["provider"] == provider and m["value"] == "fixture")
        assert "high" in option["reasoning_efforts"]
        if effort:
            config["reasoning_effort"] = effort
            config["session_id"] = "newsreader-report-fixture"
        config["limits"] = model.capacity(config)
        assert config["limits"]["context_window"] == 131072
        assert requests == []  # Model registry lookup never invokes inference.
        config["limits"]["output_tokens"] = 4096
        if finish_reason == "stop":
            assert model.run(config, "isolated fixture", timeout=20) == '{"ok":true}'
        else:
            with pytest.raises(RuntimeError, match="report_output_truncated"):
                model.run(config, "isolated fixture", timeout=20)
        assert len(requests) == 1
        assert requests[0]["model"] == "fixture"
        assert requests[0].get("reasoning_effort") == (None if effort == "off" else effort or "low")
        assert not requests[0].get("tools")
        assert requests[0]["messages"][-1]["content"] == [{"type": "text", "text": "isolated fixture"}]
        assert (requests[0].get("max_tokens") or requests[0].get("max_completion_tokens")) == 4096
        if provider.startswith("opencode"):
            if effort:
                assert sessions == [config["session_id"]]
            else:
                assert uuid.UUID(sessions[0])  # Standalone calls also supply an identity.
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def codex_metadata(tmp_path, monkeypatch, native, *, unavailable=False):
    cache = tmp_path / ".codex" / "models_cache.json"
    cache.parent.mkdir()
    cache.write_text(json.dumps({"models": [{"slug": "fixture", "context_window": 500000,
                                             "effective_context_window_percent": 95}]}))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(model.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(model.codex_client, "executable", lambda: "codex")
    commands = []
    class Rpc:
        def __init__(self, command, **kwargs):
            commands.append(command)
            if unavailable: raise OSError("unavailable")
        def call(self, method, args): return {"config": native} if method == "config/read" else {}
        def send(self, payload): pass
        def close(self): pass
    monkeypatch.setattr(model.model_catalogs, "Rpc", Rpc)
    return commands


def test_codex_uses_selected_model_metadata_and_reserves_output(tmp_path, monkeypatch):
    commands = codex_metadata(tmp_path, monkeypatch, {"model": "native-other-model"})
    result = model.capacity({"runtime": "codex", "codex": {"model": "fixture"}})
    assert result["context_window"] == 500000 and result["input_tokens"] == 392232
    assert 'model="fixture"' in commands[0]
    assert result["source"] == "codex-model-catalog"


def test_codex_native_context_override_is_authoritative(tmp_path, monkeypatch):
    commands = codex_metadata(tmp_path, monkeypatch, {"model": "custom", "model_provider": "custom-provider",
        "model_context_window": 1000000, "model_catalog_json": str(tmp_path / "missing.json")})
    result = model.capacity({"runtime": "codex", "codex": {}})
    assert result["context_window"] == 1000000 and result["source"] == "codex-resolved-config"
    assert "--profile" not in commands[0]


@pytest.mark.parametrize("provider,selected", [
    ("openai", {"model": "fixture"}),
    ("custom", {"model": "fixture"}),
    ("custom", {}),
])
def test_codex_uses_resolved_custom_catalog_before_default_cache(tmp_path, monkeypatch, provider, selected):
    catalog = tmp_path / "custom-models.json"
    catalog.write_text(json.dumps({"models": [{"slug": "fixture", "context_window": 1048576,
        "max_output_tokens": 16000, "effective_context_window_percent": 95}]}))
    codex_metadata(tmp_path, monkeypatch, {"model": "fixture" if not selected.get("model") else "other-model",
        "model_provider": provider, "model_catalog_json": str(catalog)})
    result = model.capacity({"runtime": "codex", "codex": selected})
    assert result == {"context_window": 1048576, "input_tokens": 875290,
                      "output_tokens": 16000, "source": "codex-model-catalog",
                      **({"resolved_model": "fixture"} if not selected.get("model") else {})}


@pytest.mark.parametrize("contents", [None, "invalid json", "[]", '{"models": [null]}',
    '{"models": [{"slug": "other", "context_window": 1000000}]}',
    '{"models": [{"slug": "fixture", "context_window": "1000000"}]}'])
def test_invalid_custom_catalog_does_not_fall_back_to_unrelated_cached_capacity(tmp_path, monkeypatch, contents):
    catalog = tmp_path / "custom-models.json"
    if contents is not None:
        catalog.write_text(contents)
    codex_metadata(tmp_path, monkeypatch, {"model_catalog_json": str(catalog)})
    with pytest.raises(RuntimeError, match="^report_model_capacity_unknown$"):
        model.capacity({"runtime": "codex", "codex": {"model": "fixture"}})


@pytest.mark.parametrize("missing", ["sdk", "node"])
@pytest.mark.parametrize("operation", ["capacity", "catalog", "run"])
def test_pi_missing_dependencies_are_not_reported_as_unknown_capacity(tmp_path, monkeypatch, missing, operation):
    if missing == "node":
        monkeypatch.setattr(model.pi_models.shutil, "which", lambda command: None)
    else:
        if not shutil.which("node"):
            pytest.skip("Node is required to exercise missing SDK imports")
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        shutil.copyfile(model.ROOT / "scripts/report_model.mjs", scripts / "report_model.mjs")
        monkeypatch.setattr(model, "ROOT", tmp_path)
        monkeypatch.setattr(model.oar_client, "ROOT", tmp_path)
        for name in ("oar_bridge.mjs", "oar_report.mjs", "oar_tool_events.mjs", "oar_assistant_context.mjs"):
            shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts" / name, scripts / name)
    config = {"runtime": "pi", "provider": "fixture", "model": "fixture"}
    with pytest.raises(RuntimeError, match="^report_model_dependencies_missing$"):
        if operation == "catalog":
            model.catalog({}, "pi")
        elif operation == "run":
            model.run(config, "fixture", timeout=5)
        else:
            model.capacity(config)


@pytest.mark.parametrize("selected,native,unavailable", [
    ({"model": "missing"}, {}, False),
    ({"model": "fixture"}, {"model_provider": "custom"}, False),
    ({}, {}, True),
])
def test_unknown_capacity_is_not_assumed_to_be_one_million(tmp_path, monkeypatch, selected, native, unavailable):
    codex_metadata(tmp_path, monkeypatch, native, unavailable=unavailable)
    with pytest.raises(RuntimeError, match="report_model_capacity_unknown"):
        model.capacity({"runtime": "codex", "codex": selected})


def test_token_estimate_and_conservative_fallback(monkeypatch):
    if model.tokenizer() is not None:
        assert 0 < model.estimate_tokens("标题：某公司公布计划。") < len("标题：某公司公布计划。".encode())
    monkeypatch.setattr(model, "tokenizer", lambda: None)
    assert model.estimate_tokens("中文") == len("中文".encode())


def test_pi_reserves_thinking_and_output_within_registry_capacity(monkeypatch):
    monkeypatch.setattr(model, "pi_metadata", lambda _: {"contextWindow": 1000000, "maxTokens": 384000, "reasoning_efforts": ["low", "high", "max"]})
    config = {"runtime": "pi", "provider": "fixture", "model": "fixture", "reasoning_effort": "max"}
    result = model.capacity(config)
    assert result["output_tokens"] == 65536 and result["input_tokens"] == 834464
    assert result["max_output_tokens"] == 384000 and result["output_budget_version"] == 2
    assert model.capacity({**config, "reasoning_effort": "off"})["output_tokens"] == 32768


@pytest.mark.parametrize("context,output_metadata,reserve", [
    (1048576, {}, 32768),
    (65536, {}, 16384),
    (1048576, {"maxTokens": None}, 32768),
    (1048576, {"maxTokens": True}, 32768),
    (1048576, {"maxTokens": 8192}, 8192),
    (1048576, {"maxTokens": 131072}, 65536),
])
def test_dsh_report_output_reserve(monkeypatch, context, output_metadata, reserve):
    metadata = {"contextWindow": context, "reasoning_efforts": ["xhigh"], **output_metadata}
    monkeypatch.setattr(model.dsh_models, "metadata", lambda _: metadata)
    config = {"runtime": "dsh", "provider": "fixture", "model": "selected", "reasoning_effort": "xhigh"}
    result = model.capacity(config)
    assert result["context_window"] == context
    assert result["output_tokens"] == reserve
    assert result["input_tokens"] == context - reserve - max(4096, context // 10)
    if type(output_metadata.get("maxTokens")) is int:
        assert result["max_output_tokens"] == output_metadata["maxTokens"]
    else:
        assert "max_output_tokens" not in result


@pytest.mark.parametrize("context", [{}, {"contextWindow": None}, {"contextWindow": 0}, {"contextWindow": 4096}])
def test_dsh_report_requires_native_context_even_with_known_output_limit(monkeypatch, context):
    monkeypatch.setattr(model.dsh_models, "metadata", lambda _: {"maxTokens": 8192, **context})
    with pytest.raises(RuntimeError, match="^report_model_capacity_unknown$"):
        model.capacity({"runtime": "dsh", "provider": "fixture", "model": "selected"})
