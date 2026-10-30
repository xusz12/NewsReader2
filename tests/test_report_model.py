"""Transport checks use local processes and a local HTTP model, never user credentials."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import shutil
import subprocess
import sys
import threading
import time

import pytest

import report_model as model


def fake_codex(tmp_path, monkeypatch, events, *, linger=False):
    script = tmp_path / "fake.py"
    script.write_text("import sys,json,time\nsys.stdin.read()\n" +
                      "\n".join("print(" + repr(json.dumps(event)) + ",flush=True)" for event in events) +
                      ("\ntime.sleep(30)\n" if linger else "\n"))
    monkeypatch.setattr(model.codex_client, "require_ready", lambda:None)
    monkeypatch.setattr(model.codex_client, "command", lambda *args, **kwargs:[sys.executable, str(script), "-"])
    monkeypatch.setattr(model, "codex_restrictions", lambda config:[])
    return {"runtime":"codex", "codex":{}}


START = [{"type":"thread.started", "thread_id":"019a1234-1234-7123-8123-123456789abc"}, {"type":"turn.started"}]


def test_terminal_answer_finishes_without_waiting_for_cli_teardown(tmp_path, monkeypatch):
    events = START + [
        {"type":"item.completed", "item":{"id":"reason", "type":"reasoning", "text":"working"}},
        {"type":"item.completed", "item":{"id":"answer", "type":"agent_message", "text":'{"ok":true}'}},
        {"type":"turn.completed"},
    ]
    config = fake_codex(tmp_path, monkeypatch, events, linger=True)
    began = time.monotonic()
    assert model.run(config, "private input", timeout=3) == '{"ok":true}'
    assert time.monotonic() - began < 3


@pytest.mark.parametrize("events,error", [
    (START + [{"type":"item.completed", "item":{"id":"partial", "type":"agent_message", "text":"partial"}}], "report_model_failed"),
    (START + [{"type":"turn.failed", "error":{"message":"private provider diagnostic"}}], "report_model_failed"),
    (START + [{"type":"item.started", "item":{"id":"tool", "type":"command_execution", "command":"never run"}}], "report_tools_not_allowed"),
])
def test_partial_failed_or_tool_turn_cannot_become_report(tmp_path, monkeypatch, events, error):
    config = fake_codex(tmp_path, monkeypatch, events)
    with pytest.raises(RuntimeError, match="^" + error + "$"):
        model.run(config, "private input", timeout=3)


def test_timeout_terminates_only_owned_process(tmp_path, monkeypatch):
    config = fake_codex(tmp_path, monkeypatch, START, linger=True)
    with pytest.raises(RuntimeError, match="report_model_timeout"):
        model.run(config, "input", timeout=.2)


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
    args = model.codex_restrictions({"profile":"saved"})
    assert calls == [["codex", "--profile", "saved", "mcp", "list", "--json"]]
    config = tomllib.loads(next(arg for arg in args if arg.startswith("mcp_servers=")))
    assert set(config["mcp_servers"]) == {"http-service", "local-service"}
    assert all(entry == {"enabled":False, "command":"false"} for entry in config["mcp_servers"].values())
    assert "private" not in " ".join(args)
    assert 'web_search="disabled"' in args


@pytest.mark.parametrize("finish_reason", ["stop", "length"])
def test_pi_sdk_uses_configured_model_without_tools_and_rejects_truncation(tmp_path, monkeypatch, finish_reason):
    if not shutil.which("node") or not (model.ROOT / "node_modules/@earendil-works/pi-coding-agent").exists():
        pytest.skip("Pi SDK requires npm ci")
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
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
    (tmp_path / "models.json").write_text(json.dumps({"providers":{"report-test":{
        "baseUrl":f"http://127.0.0.1:{server.server_port}/v1", "api":"openai-completions", "apiKey":"fixture-key",
        "models":[{"id":"fixture", "name":"Fixture", "reasoning":False, "input":["text"],
                   "cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0}, "contextWindow":131072, "maxTokens":16384}]
    }}}))
    monkeypatch.setenv("NEWS_READER_PI_AGENT_DIR", str(tmp_path))
    monkeypatch.setenv("PI_OFFLINE", "1")
    monkeypatch.delenv("NEWS_READER_NODE", raising=False)
    try:
        config = {"runtime":"pi", "provider":"report-test", "model":"fixture"}
        config["limits"] = model.capacity(config)
        assert config["limits"]["context_window"] == 131072
        assert requests == []  # Model registry lookup never invokes inference.
        config["limits"]["output_tokens"] = 4096
        if finish_reason == "stop":
            assert model.run(config, "isolated fixture", timeout=20) == '{"ok":true}'
        else:
            with pytest.raises(RuntimeError, match="report_model_failed"):
                model.run(config, "isolated fixture", timeout=20)
        assert len(requests) == 1
        assert requests[0]["model"] == "fixture"
        assert not requests[0].get("tools")
        assert requests[0]["messages"][-1]["content"] == "isolated fixture"
        assert (requests[0].get("max_tokens") or requests[0].get("max_completion_tokens")) == 4096
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


def test_codex_profile_context_override_is_authoritative(tmp_path, monkeypatch):
    commands = codex_metadata(tmp_path, monkeypatch, {"model": "custom", "model_provider": "custom-provider", "model_context_window": 1000000})
    result = model.capacity({"runtime": "codex", "codex": {"profile": "work"}})
    assert result["context_window"] == 1000000 and result["source"] == "codex-resolved-config"
    assert commands[0][3:5] == ["--profile", "work"]


@pytest.mark.parametrize("selected,native,unavailable", [
    ({"model": "missing"}, {}, False),
    ({"model": "fixture", "provider": "custom"}, {}, False),
    ({"model": "fixture", "profile": "work"}, {}, True),
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
