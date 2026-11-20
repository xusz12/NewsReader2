import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import httpx
import pytest

import deepseek_web_tools
import dsh_models
import llm_client
import news_summaries
import oar_client
import report_model
import runtime_tasks
import settings
from test_oar_pi_news import pi_runtime, managed_tools
from test_personal_assistant import service, send, wait_done


@pytest.mark.parametrize("name,args,status,payload,expected", [
    ("web_search", {"query":"更新"}, 200, {"results":[{"title":"公告","url":"https://example.org/a","date":"2026-11-18","site_name":"原始来源"}]}, "公告"),
    ("web_fetch", {"url":"https://example.org/a"}, 200, {"content":"网页正文","url":"https://example.org/b"}, "网页正文"),
    ("web_search", {"query":"更新"}, 402, {}, "余额不足"),
    ("web_search", {"query":"更新"}, 429, {}, "请求受限"),
    ("web_search", {"query":"更新"}, 200, {"results":[]}, "没有找到"),
    ("web_fetch", {"url":"https://example.org/a"}, 200, {"content":""}, "正文"),
    ("web_fetch", {"url":"https://example.org/a"}, 504, {}, "504"),
    ("web_search", {"query":"更新"}, 0, {}, "超时"),
])
def test_deepseek_response_and_evidence(tmp_path, monkeypatch, name, args, status, payload, expected):
    monkeypatch.setattr(llm_client, "_resolve_api_key", lambda _: "fixture-secret")
    calls = []
    def remote(request):
        calls.append((request.url, json.loads(request.content)))
        assert request.headers["authorization"] == "Bearer fixture-secret"
        if not status:
            raise httpx.ReadTimeout("fixture-secret")
        return httpx.Response(status, json=payload)
    with httpx.Client(transport=httpx.MockTransport(remote)) as client:
        monkeypatch.setattr(deepseek_web_tools.httpx, "stream", client.stream)
        receipt = tmp_path / "evidence"
        server = deepseek_web_tools.WebTools(receipt)
        response = server.respond({"id":1, "method":"tools/call", "params":{"name":name, "arguments":args}})["result"]
    assert expected in response["content"][0]["text"]
    assert len(calls) == 1 and str(calls[0][0]) == "https://api.deepseek.com/v1/tools/" + name.removeprefix("web_")
    assert ("isError" not in response) == (receipt.is_file())
    assert "fixture-secret" not in str(response)
    if receipt.is_file():
        assert receipt.read_text().strip() == name
        result = json.loads(response["content"][0]["text"])
        assert result.get("results", [{}])[0].get("date") == payload.get("results", [{}])[0].get("date")


def test_no_key_and_invalid_urls_never_call_network(monkeypatch):
    monkeypatch.setattr(llm_client, "_resolve_api_key", lambda _: "")
    monkeypatch.setattr(httpx, "stream", lambda *args, **kwargs: pytest.fail("unexpected network request"))
    server = deepseek_web_tools.WebTools()
    for name, args in [("web_search", {"query":"更新"}), ("web_search", {"query":"a"*129}), ("web_fetch", {"url":"file:///tmp/article"})]:
        assert server.respond({"id":1,"method":"tools/call","params":{"name":name,"arguments":args}})["result"]["isError"]


def test_dsh_catalog_lazy_cache_and_refresh(monkeypatch):
    calls = []
    monkeypatch.setattr(dsh_models, "_cache", {})
    monkeypatch.setattr(dsh_models, "_query", lambda request: calls.append(request) or {"options": [], "notice": "fixture"})
    config = settings.default_app_settings()
    config["agent"]["dsh"] = {"provider":"fixture", "model":"chat"}
    selected = report_model.selection(config, "chat")
    assert selected["dsh"]["model"] == "chat" and not calls
    dsh_models.catalog()
    dsh_models.catalog()
    assert len(calls) == 1
    dsh_models.catalog(refresh=True)
    assert len(calls) == 2


def test_dsh_missing_is_optional(tmp_path, monkeypatch):
    monkeypatch.setenv("OAR_DSH_BIN", str(tmp_path / "absent"))
    catalog = dsh_models.catalog(refresh=True)
    assert catalog["error"] == "oar_dsh_missing" and "npm install" in catalog["notice"]
    with pytest.raises(RuntimeError, match="oar_dsh_missing"):
        runtime_tasks.run({"runtime":"dsh","dsh":{"provider":"test","model":"test"}}, "probe", task="connection_test", timeout=10)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS desktop bundle")
def test_dsh_desktop_discovery_and_cli_precedence(tmp_path):
    node = shutil.which("node")
    if not node or not (oar_client.ROOT / "node_modules/@botiverse/oar").exists():
        pytest.skip("Requires Node and OAR")
    desktop = tmp_path / "Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh"
    cli = tmp_path / "bin/dsh"
    desktop.parent.mkdir(parents=True)
    cli.parent.mkdir()
    version = "#!/bin/sh\nprintf '0.2.2-rc.5\\n'\n"
    desktop.write_text(version)
    desktop.chmod(0o755)
    env = {**os.environ, "HOME": str(tmp_path), "PATH": str(cli.parent)}
    env.pop("OAR_DSH_BIN", None)
    probe = [node, "--input-type=module", "-e",
             "import {dshInstallation} from './scripts/dsh_runtime.mjs'; console.log((await dshInstallation()).command);"]

    def detected():
        return Path(subprocess.check_output(probe, cwd=oar_client.ROOT, env=env, text=True, timeout=10).strip())

    assert detected() == desktop
    cli.write_text(version)
    cli.chmod(0o755)
    assert detected() == cli
    env["OAR_DSH_BIN"] = str(desktop)
    assert detected() == desktop


@pytest.fixture
def external_dsh(pi_runtime, monkeypatch, tmp_path):
    source, cwd, state, request, calls, responses, _ = pi_runtime
    home = tmp_path / "dsh"
    home.mkdir()
    pi = json.loads((source / "models.json").read_text())["providers"]["nr-fixture"]
    models = [{"id": "fixture", "contextWindow": 65536, "maxTokens": 8192},
              {"id": "context-only", "contextWindow": 65536}]
    patch = [{"id":"llm-pi-ai","config":{"providers":{"nr-fixture":{"apiKeyEnv":"NR_FIXTURE_KEY",
             "api":"openai-completions","baseURL":pi["baseUrl"],"models":models}}}}]
    (home / "cordis.patch.yml").write_text(json.dumps(patch))
    monkeypatch.setenv("DSH_HOME", str(home))
    monkeypatch.setenv("NR_FIXTURE_KEY", "local-fixture")
    catalog = dsh_models.catalog(refresh=True)
    if catalog["error"] == "oar_dsh_missing":
        pytest.skip("Requires a DSH CLI or the official macOS desktop app")
    assert any(row["provider"] == "nr-fixture" for row in catalog["options"]), catalog["error"]
    request.update(runtime="dsh", reasoning_effort="")
    return state, request, calls, responses, home


def test_dsh_native_capacity_and_material_tasks(external_dsh):
    state, request, calls, responses, home = external_dsh
    selected = {"runtime":"dsh","provider":"nr-fixture","model":"fixture"}
    limits = report_model.capacity(selected)
    assert limits["context_window"] == 65536 and limits["max_output_tokens"] == 8192
    assert not calls
    for task in ("chat_archive", "material_summary", "connection_test", "translation", "report"):
        payload = request | {"task":task}
        if task == "report":
            payload.update(output_tokens=4096, delegation=False)
        responses.append(task)
        assert oar_client.run_once(oar_client.bridge_command("test"), payload, os.environ.copy(), 35) == task
        assert not calls[-1].get("tools")
    assert not (state / "session.json").exists()
    assert not (state / "sessions").exists()
    assert not (home / "sessions").exists()
    assert not list(state.glob("config-*"))


def test_dsh_tools_resume_and_research_evidence(external_dsh, monkeypatch):
    state, request, calls, responses, home = external_dsh
    search, fetch = "mcp__nr_web__web_search", "mcp__nr_web__web_fetch"
    request.update(web_tools=managed_tools())
    responses.extend([(search, {"query":"fixture"}), (fetch, {"url":"https://example.test/news"}), "first answer", "second answer"])
    assert oar_client.run_once(oar_client.bridge_command("test"), request, os.environ.copy(), 45) == "first answer"
    saved = json.loads((state / "session.json").read_text())
    assert oar_client.run_once(oar_client.bridge_command("test"), request, os.environ.copy(), 45) == "second answer"
    updated = json.loads((state / "session.json").read_text())
    assert updated["id"] == saved["id"] and updated["generation"] != saved["generation"]
    assert not (state / saved["generation"]).exists()
    assert saved["runtime"] == "oar-dsh" and "first answer" in json.dumps(calls[-1]["messages"])
    for call in calls:
        assert {t["function"]["name"] for t in call.get("tools", [])} == {search, fetch}
    assert not (home / "sessions").exists()
    config = {"runtime":"dsh","pi_provider":"nr-fixture","pi_model":"fixture"}
    original_request = news_summaries.runtime_request
    def scoped(*args, **kwargs):
        payload = original_request(*args, **kwargs)
        payload["web_tools"] = managed_tools(*payload["web_tools"]["args"][1:])
        return payload
    monkeypatch.setattr(news_summaries, "runtime_request", scoped)
    responses.extend([(search, {"query":"research"}), "summary"])
    diagnostics = []
    assert news_summaries.run_oar(config, "research", lambda:False, diagnostics, allow_research=True) == "summary"
    assert diagnostics[0]["research_tools"] == 1


def test_assistant_retry_keeps_thread_model_and_transcript(service):
    seen = []
    def runner(**args):
        seen.append((args["profile"]["runtime"], args["state_dir"]))
        if len(seen) == 1:
            raise RuntimeError("assistant_timeout")
        return "已完成"
    service.runner = runner
    profile = service.profile()
    service.save_profile(profile | {"runtime":"dsh", "dsh":{"provider":"fixture","model":"m"}})
    thread = send(service, "原问题")
    first = wait_done(service, thread)
    service.save_profile(service.profile() | {"runtime":"codex"})
    service.send(thread, None, str(uuid.uuid4()), first["job"]["id"])
    second = wait_done(service, thread)
    assert second["thread"]["runtime"] == "dsh"
    assert [m["id"] for m in second["messages"]] == [m["id"] for m in first["messages"]]
    assert [m["content"] for m in second["messages"]] == ["原问题","已完成"]
    assert seen[0] == seen[1] and seen[0][0] == "dsh"


def test_dsh_report_workspace_for_all_periods(external_dsh):
    import report_workspace
    state, request, calls, responses, home = external_dsh
    config = {"runtime":"dsh", "provider":"nr-fixture", "model":"context-only"}
    config["limits"] = report_model.capacity(config)
    assert config["limits"]["context_window"] == 65536 and config["limits"]["output_tokens"] == 16384
    assert "max_output_tokens" not in config["limits"] and not calls
    for kind in ("morning", "noon", "evening", "weekly", "monthly"):
        snapshot = {"kind":kind, "sources":[], "period":{"label":kind}}
        workspace = report_workspace.Workspace(snapshot, config)
        responses.extend([("mcp__newsreader_report__report_status", {}), "工作区已读取"])
        assert report_model.run({**config, "_workspace":workspace}, "读取工作区", timeout=35) == "工作区已读取"
        names = {t["function"]["name"] for t in calls[-1].get("tools", [])}
        assert names == {"mcp__newsreader_report__" + t["name"] for t in report_workspace.TOOLS}
    assert not (home / "sessions").exists()


def test_dsh_translation_fallback_uses_chat_model(monkeypatch):
    import collection_translation
    from nr import details
    config = settings.default_app_settings()
    config["llm"]["translation"].update(provider="deepseek", fallback_runtime="dsh")
    config["agent"].update(runtime="codex", dsh={"provider":"route","model":"article-model"})
    routes = []
    def unavailable(**kwargs):
        routes.append(kwargs["provider"])
        raise llm_client.LLMClientError("TRANSLATION_FAILED")
    def finish(**kwargs):
        routes.append(kwargs["runtime"])
        assert kwargs["pi_provider"] == "route" and kwargs["pi_model"] == "article-model"
        return {"raw_json":"{}"}
    monkeypatch.setattr(details, "generate_pi_fallback_translation", finish)
    monkeypatch.setattr(details, "generate_article_ai", unavailable)
    details.generate_news_translation(config, title="news", source="Reuters", content="material")
    assert routes == ["deepseek","opencode-go","dsh"]
    seen = []
    def translate(selection, prompt, **options):
        seen.append(selection)
        return '{"title":"已更新","summary":"","quoted_text":""}'
    monkeypatch.setattr(runtime_tasks, "run", translate)
    monkeypatch.setattr(collection_translation, "http_translate", lambda *args: (_ for _ in ()).throw(ValueError("unavailable")))
    raw = {"title":"News updated","summary":"","quoted_text":""}
    result = collection_translation.translate(raw, config)
    assert seen == [{"runtime":"dsh", "dsh":config["agent"]["dsh"]}]
    assert "已更新" in result.values()


@pytest.mark.parametrize("stop", [False, True])
def test_dsh_interrupt_reaps_tools_and_preserves_last_turn(external_dsh, monkeypatch, service, stop):
    import personal_assistant_runtime
    from test_oar_assistant import assert_stopped_processes

    state, request, calls, responses, home = external_dsh
    responses.append("已完成的第一轮")
    request.update(web_tools=managed_tools())
    assert oar_client.run_once(oar_client.bridge_command("test"), request, os.environ.copy(), 25) == "已完成的第一轮"
    mapping = (state / "session.json").read_bytes()
    pids = home / "running-tool"
    original = oar_client.bridge_request
    def slow(**kwargs):
        return {**original(**kwargs), "web_tools":managed_tools("--pid-file", pids)}
    monkeypatch.setattr(oar_client, "bridge_request", slow)
    responses.append(("mcp__nr_web__web_search", {"query":"pending"}))
    with pytest.raises(RuntimeError, match="assistant_stopped" if stop else "assistant_timeout"):
        personal_assistant_runtime.run(profile={"runtime":"dsh","dsh":{"provider":"nr-fixture","model":"fixture"}},
            prompt="pending", resume_prompt="pending", state_dir=state, workspace=Path(request["cwd"]), db=service.db_path(),
            state_db=service.state_db, emit=lambda event:None, stopped=lambda:stop and pids.exists(), timeout=15 if stop else 6)
    assert pids.is_file()
    assert_stopped_processes(pids)
    assert (state / "session.json").read_bytes() == mapping
    assert not list(state.glob("config-*"))
