"""Installed Pi RPC managed by OAR, using only local synthetic model/tool servers."""
import json
import os
from pathlib import Path
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import oar_client


@pytest.fixture
def pi_runtime(tmp_path):
    node = shutil.which('node')
    if not node or not (oar_client.ROOT / 'node_modules/@botiverse/oar').exists():
        pytest.skip('Requires Node and installed OAR/Pi')
    calls, responses = [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            answer = responses.pop(0) if responses else 'fixture-answer'
            if callable(answer):
                answer = answer(calls[-1])
            if isinstance(answer, tuple):
                name, args = answer
                delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': f'call-{len(calls)}',
                    'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}
            else:
                delta = {'role': 'assistant', 'content': answer['text'] if isinstance(answer, dict) else answer}
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            finish_reason = 'tool_calls' if isinstance(answer, tuple) else answer.get('finish', 'stop') if isinstance(answer, dict) else 'stop'
            for content, finish in [(delta, None), ({}, finish_reason)]:
                body = {'id': 'fixture', 'object': 'chat.completion.chunk', 'model': 'fixture',
                    'choices': [{'index': 0, 'delta': content, 'finish_reason': finish}]}
                if finish and isinstance(answer, dict) and "input_tokens" in answer:
                    body["usage"] = {"prompt_tokens": answer["input_tokens"], "completion_tokens": 5, "total_tokens": answer["input_tokens"] + 5}
                self.wfile.write(('data: ' + json.dumps(body) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    source, cwd, state = [tmp_path / name for name in ['source', 'project', 'state']]
    for directory in [source, cwd, state]:
        directory.mkdir()
    (source / 'auth.json').write_text('{}')
    (source / 'settings.json').write_text(json.dumps({'defaultTools': ['read', 'codemode', 'tool_search']}))
    (source / 'models.json').write_text(json.dumps({'providers': {'nr-fixture': {
        'baseUrl': f'http://127.0.0.1:{server.server_port}/v1', 'api': 'openai-completions', 'apiKey': 'fixture-key',
        'models': [{'id': 'fixture', 'name': 'Fixture', 'reasoning': False, 'input': ['text'],
            'contextWindow': 32768, 'maxTokens': 4096,
            'cost': {'input': 0, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0}}]}}}))
    env = {'PATH': os.environ['PATH'], 'HOME': str(tmp_path), 'PI_OFFLINE': '1',
           'NEWS_READER_PI_AGENT_DIR': str(source)}
    request = {'cwd': str(cwd), 'state_dir': str(state), 'task': 'news_chat', 'provider': 'nr-fixture',
        'model': 'fixture', 'reasoning_effort': 'off', 'prompt': 'first question', 'resume_prompt': 'follow-up question'}

    def run(timeout=25):
        if request["task"] in {"news_chat", "web_research"} and "web_tools" not in request:
            request["web_tools"] = managed_tools()
        return oar_client.run_once([node, str(oar_client.ROOT / 'scripts/oar_bridge.mjs')], request, env, timeout)

    try:
        yield source, cwd, state, request, calls, responses, run
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def search_config(exposure='direct', extra_args=()):
    return {'mcpServers': {'search': {'command': sys.executable,
        'args': [str(oar_client.ROOT / 'tests/fixtures/pi_news_mcp.py'), *map(str, extra_args)],
        'exposure': exposure, 'toolExposure': {'hidden_tool': 'hidden'}}}}


def managed_tools(*args):
    return {"command": sys.executable, "args": [str(oar_client.ROOT / "tests/fixtures/pi_news_mcp.py"), "--managed", *map(str, args)]}


@pytest.mark.parametrize('exposure', ['direct', 'codemode', 'deferred'])
def test_only_managed_search_and_fetch_survive_resume(pi_runtime, exposure):
    source, cwd, state, request, calls, responses, run = pi_runtime
    original = json.dumps(search_config(exposure))
    (source / 'mcp.json').write_text(original)
    search = 'mcp__nr_web__web_search'
    fetch = 'mcp__nr_web__web_fetch'
    responses.extend([(search, {'query': 'fixture'}), (fetch, {'url': 'https://example.test/news'}), 'search-summary'])
    assert run() == 'search-summary'
    native = json.loads((state / 'session.json').read_text())
    responses.append('follow-up')
    assert run() == 'follow-up'
    assert json.loads((state / 'session.json').read_text()) == native
    assert 'search-summary' in json.dumps(calls[-1]['messages'])
    assert 'synthetic-search-evidence' in json.dumps(calls[-1]['messages'])
    for call in calls:
        assert {t['function']['name'] for t in call.get('tools', [])} == {search, fetch}
    assert (source / 'mcp.json').read_text() == original
    assert not list(state.glob('config-*'))


@pytest.mark.parametrize('trusted', [False, True])
def test_project_tools_cannot_replace_managed_search(pi_runtime, trusted):
    source, cwd, state, request, calls, responses, run = pi_runtime
    project = cwd / '.pi'
    project.mkdir()
    (project / 'mcp.json').write_text(json.dumps(search_config()))
    marker = cwd / 'extension-loaded'
    (project / 'extensions').mkdir()
    (project / 'extensions/probe.ts').write_text(
        "import fs from 'node:fs';export default function(){fs.writeFileSync(" + json.dumps(str(marker)) + ", 'loaded');}")
    (source / 'trust.json').write_text(json.dumps({str(cwd.resolve()): trusted}))
    assert run() == 'fixture-answer'
    names = [t['function']['name'] for t in calls[0].get('tools', [])]
    assert 'mcp__search__web_search' not in names
    assert not marker.exists()
    assert json.loads((state / 'session.json').read_text())['runtime'] == 'oar'
    assert not (state / 'route.json').exists()


@pytest.mark.parametrize("task", ["chat_archive", "material_summary"])
def test_pi_archive_has_no_tools_or_history_and_each_attempt_is_independent(pi_runtime, task):
    source, cwd, state, request, calls, responses, run = pi_runtime
    (source / 'mcp.json').write_text(json.dumps(search_config()))
    request['task'] = task
    responses.extend(['first-summary', 'shorter-summary'])
    assert run() == 'first-summary'
    assert run() == 'shorter-summary'
    assert len(calls) == 2
    assert all(not c.get('tools') for c in calls)
    assert 'first-summary' not in json.dumps(calls[1]['messages'])
    assert not (state / 'session.json').exists()
    assert not list(state.rglob('*.jsonl'))
    assert not list(state.glob('config-*'))


def test_pi_timeout_reaps_rpc_search_process_and_detached_tool(pi_runtime, tmp_path):
    source, cwd, state, request, calls, responses, run = pi_runtime
    pids = tmp_path / 'tool-pids.json'
    request['web_tools'] = managed_tools('--pid-file', pids)
    responses.append(('mcp__nr_web__web_search', {'query': 'fixture'}))
    with pytest.raises(RuntimeError, match='pi_timeout'):
        run(timeout=6)
    assert pids.exists(), 'Fixture never reached the tool; cancellation was not exercised'
    for pid in json.loads(pids.read_text()):
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    assert not (state / 'session.json').exists()
    assert not list(state.glob('config-*'))


def test_pi_research_is_ephemeral_and_uses_managed_search(pi_runtime):
    source, cwd, state, request, calls, responses, run = pi_runtime
    (source / 'mcp.json').write_text(json.dumps(search_config('deferred')))
    request['task'] = 'web_research'
    responses.extend([('mcp__nr_web__web_search', {'query': 'fixture'}), 'researched'])
    assert run() == 'researched'
    assert 'synthetic-search-evidence' in json.dumps(calls[-1]['messages'])
    assert not (state / 'session.json').exists()
    assert not list(state.rglob('*.jsonl'))
    assert not list(state.glob('config-*'))


@pytest.mark.parametrize('failed', [False, True])
def test_summary_runner_counts_only_completed_successful_pi_search(pi_runtime, monkeypatch, failed):
    import news_summaries
    source, cwd, state, request, calls, responses, run = pi_runtime
    (source / 'mcp.json').write_text(json.dumps(search_config(extra_args=['--fail'] if failed else [])))
    monkeypatch.setattr(news_summaries, '_pi_subprocess_env', lambda: {
        'PATH': os.environ['PATH'], 'HOME': str(source.parent), 'PI_OFFLINE': '1',
        'NEWS_READER_PI_AGENT_DIR': str(source)})
    original_request = news_summaries.runtime_request
    def managed_request(*args, **kwargs):
        result = original_request(*args, **kwargs)
        result["web_tools"] = managed_tools(*(["--fail"] if failed else []), *result["web_tools"]["args"][1:])
        return result
    monkeypatch.setattr(news_summaries, "runtime_request", managed_request)
    responses.extend([('mcp__nr_web__web_search', {'query': 'fixture'}), 'researched'])
    config = {'runtime': 'pi', 'pi_provider': 'nr-fixture', 'pi_model': 'fixture', 'pi_reasoning_effort': 'off'}
    diagnostics = []
    assert news_summaries.run_oar(config, 'research', lambda: False, diagnostics, allow_research=True) == 'researched'
    assert diagnostics[0]['research_tools'] == (0 if failed else 1)
    assert diagnostics[0]['tools'] == ['mcp__nr_web__web_search']
    for call in calls:
        assert not {'read', 'bash', 'write', 'edit'} & {t['function']['name'] for t in call.get('tools', [])}
