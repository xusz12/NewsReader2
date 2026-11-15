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
            if isinstance(answer, tuple):
                name, args = answer
                delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': f'call-{len(calls)}',
                    'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}
            else:
                delta = {'role': 'assistant', 'content': answer}
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            for content, finish in [(delta, None), ({}, 'tool_calls' if isinstance(answer, tuple) else 'stop')]:
                body = {'id': 'fixture', 'object': 'chat.completion.chunk', 'model': 'fixture',
                    'choices': [{'index': 0, 'delta': content, 'finish_reason': finish}]}
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


@pytest.mark.parametrize('exposure', ['direct', 'codemode', 'deferred'])
def test_native_search_filters_and_results_survive_oar_and_resume(pi_runtime, exposure):
    source, cwd, state, request, calls, responses, run = pi_runtime
    original = json.dumps(search_config(exposure))
    (source / 'mcp.json').write_text(original)
    tool = 'mcp__search__web_search'
    if exposure == 'codemode':
        responses.append(('codemode', {'code': f'text(await tools.{tool}({{query:"fixture"}}));'}))
    else:
        if exposure == 'deferred':
            responses.append(('tool_search', {'query': 'web_search'}))
        responses.append((tool, {'query': 'fixture'}))
    tool_turn = list(responses)
    responses.append('search-summary')
    assert run() == 'search-summary'
    native = json.loads((state / 'session.json').read_text())
    assert native['runtime'] == 'oar'
    assert not (state / 'route.json').exists()
    assert 'synthetic-search-evidence' in json.dumps(calls[-1]['messages'])
    for call in calls:
        names = [t['function']['name'] for t in call.get('tools', [])]
        assert 'mcp__search__hidden_tool' not in names
    responses.extend([*tool_turn, 'follow-up-search'])
    assert run() == 'follow-up-search'
    assert json.loads((state / 'session.json').read_text())['id'] == native['id']
    assert 'search-summary' in json.dumps(calls[-1]['messages'])
    assert sum('synthetic-search-evidence' in str(m.get('content')) for m in calls[-1]['messages'] if m['role'] == 'tool') >= 2
    assert (source / 'mcp.json').read_text() == original
    assert not list(state.glob('config-*'))


@pytest.mark.parametrize('trusted', [False, True])
def test_project_resources_follow_native_trust_without_cli_fallback(pi_runtime, trusted):
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
    assert ('mcp__search__web_search' in names) == trusted
    assert marker.exists() == trusted
    assert json.loads((state / 'session.json').read_text())['runtime'] == 'oar'
    assert not (state / 'route.json').exists()


def test_pi_archive_has_no_tools_or_history_and_each_attempt_is_independent(pi_runtime):
    source, cwd, state, request, calls, responses, run = pi_runtime
    (source / 'mcp.json').write_text(json.dumps(search_config()))
    request['task'] = 'chat_archive'
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
    (source / 'mcp.json').write_text(json.dumps(search_config(extra_args=[pids])))
    responses.append(('mcp__search__web_search', {'query': 'fixture'}))
    with pytest.raises(RuntimeError, match='pi_timeout'):
        run(timeout=6)
    assert pids.exists(), 'Fixture never reached the tool; cancellation was not exercised'
    for pid in json.loads(pids.read_text()):
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    assert not (state / 'session.json').exists()
    assert not list(state.glob('config-*'))
