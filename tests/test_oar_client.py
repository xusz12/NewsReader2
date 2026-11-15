from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
import oar_client


def command(tmp_path, body):
    script = tmp_path / 'fake.py'
    script.write_text('import sys,json,os,time,signal,subprocess\n' + body)
    return [sys.executable, str(script)]


@pytest.mark.parametrize('body,expected', [
    ('print(json.dumps({"type":"text_delta","delta":"partial"}))', 'oar_protocol_error'),
    ('print("not json")', 'oar_protocol_error'),
    ('print(json.dumps({"type":"oar_result","outcome":"completed","answer":""}))', 'oar_protocol_error'),
    ('print(json.dumps({"type":"oar_result","outcome":"completed","answer":"done"}));sys.exit(1)', 'oar_protocol_error'),
    ('r={"type":"oar_result","outcome":"completed","answer":"done"};print(json.dumps(r));print(json.dumps(r))', 'oar_protocol_error'),
    ('print(json.dumps({"type":"oar_result","outcome":"failed","error":"oar_auth_required"}))', 'oar_auth_required'),
    ('print(json.dumps({"type":"oar_result","outcome":"failed","error":"secret provider diagnostic"}))', 'oar_turn_failed'),
    ('sys.stdout.buffer.write(b"\\xff")', 'oar_protocol_error'),
    ('print("x" * (2*1024*1024+1))', 'oar_protocol_error'),
])
def test_strict_completion_contract(tmp_path, body, expected):
    request = {'state_dir': str(tmp_path), 'prompt': 'private'}
    with pytest.raises(RuntimeError, match=expected):
        oar_client.run_once(command(tmp_path, body), request, os.environ.copy(), 3)


def test_success_uses_stdin_and_does_not_return_stderr(tmp_path):
    script = command(tmp_path, 'r=json.load(sys.stdin);print("private-key",file=sys.stderr);print(json.dumps({"type":"oar_result","outcome":"completed","answer":r["prompt"]}))')
    assert oar_client.run_once(script, {'state_dir': str(tmp_path), 'prompt': 'answer'}, os.environ.copy(), 3) == 'answer'
    assert 'answer' not in str(script)


def test_timeout_cleans_private_config_and_kills_resistant_tool_after_parent_exits(tmp_path):
    pidfile = tmp_path / 'child-pid'
    other_config = tmp_path / 'config-another-worker'
    other_config.mkdir(); (other_config / 'auth.json').write_text('private')
    body = f'''request=json.load(sys.stdin)
from pathlib import Path
config=Path(request['state_dir'])/('config-'+request['config_id']+'-fixture')
config.mkdir();(config/'auth.json').write_text('private')
signal.signal(signal.SIGTERM,signal.SIG_IGN)
child = subprocess.Popen([sys.executable, '-c', "import signal,time;signal.signal(signal.SIGTERM, signal.SIG_IGN);time.sleep(60)"])
open({str(pidfile)!r},'w').write(str(child.pid))
time.sleep(60)
'''
    with pytest.raises(RuntimeError, match='pi_timeout'):
        oar_client.run_once(command(tmp_path, body), {'state_dir': str(tmp_path)}, os.environ.copy(), .4)
    assert list(tmp_path.glob('config-*')) == [other_config]
    assert (other_config / 'auth.json').read_text() == 'private'
    pid = int(pidfile.read_text())
    # A killed orphan may briefly remain a zombie; it is not an executing process.
    for _ in range(30):
        status = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True).stdout.strip()
        if not status or status.startswith('Z'): break
        time.sleep(.02)
    assert not status or status.startswith('Z')


def test_timeout_while_child_does_not_read_large_stdin(tmp_path):
    started = time.monotonic()
    with pytest.raises(RuntimeError, match='pi_timeout'):
        oar_client.run_once(command(tmp_path, 'time.sleep(60)'),
            {'state_dir': str(tmp_path), 'prompt': 'x' * 200_000}, os.environ.copy(), .1)
    assert time.monotonic() - started < 3


def test_missing_node_is_actionable(monkeypatch):
    monkeypatch.setenv('NEWS_READER_NODE', '/not/a/node')
    with pytest.raises(RuntimeError, match='oar_node_missing'):
        oar_client.bridge_command('test-session')


@pytest.mark.parametrize('event', [[], {}, {'type':'text_delta','delta':3},
    {'type':'oar_result','outcome':'ended'}, {'type':'oar_result','outcome':'completed','answer':None}])
def test_bad_protocol_events(event):
    with pytest.raises(RuntimeError, match='oar_protocol_error'):
        oar_client.parse_event(json.dumps(event))

@pytest.mark.parametrize('node_state,expected', [('missing','请安装 Node.js 24+'),('old','需要 Node.js 24+'),('missing-dependency','npm ci --ignore-scripts')])
def test_launcher_diagnoses_agent_runtime_but_keeps_reading_available(tmp_path, node_state, expected):
    import shutil
    project = tmp_path / 'project'; project.mkdir()
    script = project / '启动NewsReader.command'
    shutil.copy2(oar_client.ROOT / script.name, script)
    (project / 'launcher.py').write_text('# fixture only')
    marker = tmp_path / 'launcher-called'
    python = tmp_path / 'python'
    python.write_text('#!/bin/sh\nif [ "$1" != "-c" ]; then echo called > "'+str(marker)+'"; fi\nexit 0\n')
    python.chmod(0o755)
    node = tmp_path / 'node'
    if node_state != 'missing':
        node.write_text('#!/bin/sh\nexit '+('1' if node_state == 'old' else '0')+'\n'); node.chmod(0o755)
    env = {**os.environ, 'NEWS_READER_PYTHON':str(python),'NEWS_READER_NODE':str(node),'NEWS_READER_NO_ALERT':'1','NEWS_READER_HOST':'127.0.0.1'}
    result = subprocess.run(['/bin/zsh',str(script)],cwd=project,env=env,capture_output=True,text=True,timeout=10)
    assert result.returncode == 0
    assert expected in result.stderr
    assert '新闻阅读仍可启动' in result.stderr
    assert marker.exists()


def test_real_node_missing_dependencies_has_safe_error_and_cleans_config(tmp_path):
    import shutil
    node = shutil.which('node')
    if not node: pytest.skip('Node is optional in Python-only test environments')
    bridge = tmp_path / 'isolated' / 'oar_bridge.mjs'; bridge.parent.mkdir()
    shutil.copy2(oar_client.ROOT / 'scripts/oar_bridge.mjs',bridge)
    source = tmp_path / 'empty-agent';source.mkdir()
    state = tmp_path / 'state';state.mkdir()
    request = oar_client.bridge_request(prompt='fixture',resume_prompt='fixture',state_dir=state,provider='openai',model='gpt-4.1')
    request['cwd'] = str(tmp_path)
    with pytest.raises(RuntimeError,match='oar_dependency_missing'):
        oar_client.run_once([node,str(bridge)],request,{**os.environ,'NEWS_READER_PI_AGENT_DIR':str(source)},10)
    assert not list(state.glob('config-*'))


@pytest.mark.parametrize('route,tool_source', [('oar', 'builtin'), ('cli', 'builtin'), ('cli', 'nr_tools'),
    ('news', 'builtin'), ('migrate_cli', 'builtin'), ('import_cli', 'builtin')])
def test_installed_pi_runs_tools_and_resumes_across_bridge_processes(tmp_path, route, tool_source):
    """Exercise the installed OAR/SDK and native CLI against a local provider."""
    import shutil
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    node = shutil.which('node')
    if not node or not (oar_client.ROOT / 'node_modules/@botiverse/oar').exists():
        pytest.skip('Installed OAR and Pi require npm ci')
    source, cwd, state = (tmp_path / name for name in ('source', 'project', 'state'))
    for directory in (source, cwd, state):
        directory.mkdir()
    sample = cwd / 'sample.txt'
    sample.write_text('local-tool-evidence', encoding='utf-8')
    tool_name, tool_args = 'read', {'path': str(sample)}
    nr_tools = None
    if tool_source == 'nr_tools':
        import sqlite3
        from personal_assistant import Service
        from settings import default_app_settings
        database = tmp_path / 'news.sqlite3'
        with sqlite3.connect(database) as connection:
            connection.execute('CREATE TABLE sample (value TEXT)')
            connection.execute("INSERT INTO sample VALUES ('local-tool-evidence')")
        assistant = tmp_path / 'assistant'
        Service(assistant, lambda: database, default_app_settings).ensure()
        tool_name, tool_args = 'mcp__nr_assistant__nr_query', {'sql': 'SELECT value FROM sample'}
        nr_tools = {'command': sys.executable, 'args': [str(oar_client.ROOT / 'personal_assistant_tools.py'),
            '--db', str(database), '--workspace', str(assistant / 'workspace'),
            '--state-db', str(assistant / 'assistant.sqlite3')]}
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            first = len(requests) == 1
            delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'read-sample',
                'type': 'function', 'function': {'name': tool_name,
                    'arguments': json.dumps(tool_args)}}]} if first else {
                'role': 'assistant', 'content': 'first-answer' if len(requests) == 2 else 'resumed-answer'}
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            for value, finish in [(delta, None), ({}, 'tool_calls' if first else 'stop')]:
                chunk = {'id': 'fixture', 'object': 'chat.completion.chunk', 'model': 'fixture',
                         'choices': [{'index': 0, 'delta': value, 'finish_reason': finish}]}
                self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    (source / 'auth.json').write_text('{}')
    (source / 'settings.json').write_text(json.dumps({'defaultTools': ['read']}))
    (source / 'models.json').write_text(json.dumps({'providers': {'nr-upgrade-test': {
        'baseUrl': f'http://127.0.0.1:{server.server_port}/v1', 'api': 'openai-completions',
        'apiKey': 'fixture-key', 'models': [{'id': 'fixture', 'name': 'Fixture',
            'reasoning': False, 'input': ['text'], 'contextWindow': 32768, 'maxTokens': 4096,
            'cost': {'input': 0, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0}}]}}}))
    if route in ('cli', 'news', 'migrate_cli', 'import_cli') and tool_source == 'builtin':
        # Native MCP configuration deliberately keeps this session on the CLI route.
        (source / 'mcp.json').write_text('{"mcpServers":{}}')
    original_config = {file.name: file.read_bytes() for file in source.iterdir()}
    env = {'PATH': os.environ['PATH'], 'HOME': str(tmp_path), 'PI_OFFLINE': '1',
           'NEWS_READER_PI_AGENT_DIR': str(source)}
    request = {'cwd': str(cwd), 'state_dir': str(state), 'provider': 'nr-upgrade-test',
               'model': 'fixture', 'prompt': 'Read the sample and retain this first question.',
               'resume_prompt': 'Continue the same conversation.', 'reasoning_effort': 'off'}
    if nr_tools:
        request['nr_tools'] = nr_tools
    if route == 'news':
        request['task'] = 'news_chat'
    command = [node, str(oar_client.ROOT / 'scripts/oar_bridge.mjs')]
    try:
        assert oar_client.run_once(command, request, env, 20) == 'first-answer'
        saved = json.loads((state / 'session.json').read_text())
        assert saved['runtime'] == ('oar' if route == 'news' else 'cli' if route in ('migrate_cli', 'import_cli') else route)
        if route == 'migrate_cli':
            request['task'] = 'news_chat'
        if route == 'import_cli':
            shutil.copytree(state / 'sessions', source / 'sessions')
            original_history = {str(f.relative_to(source)): f.read_bytes() for f in (source / 'sessions').rglob('*.jsonl')}
            state = tmp_path / 'imported-state'
            request.update(task='news_chat', legacy_native_id=saved['id'], state_dir=str(state))
        assert oar_client.run_once(command, request, env, 20) == 'resumed-answer'
        assert json.loads((state / 'session.json').read_text())['id'] == saved['id']
        if route in ('news', 'migrate_cli', 'import_cli'):
            assert json.loads((state / 'session.json').read_text())['runtime'] == 'oar'
            assert not (state / 'route.json').exists()
        assert len(requests) == 3
        tools = [tool['function']['name'] for tool in requests[0]['tools']]
        assert tool_name in tools
        if nr_tools:
            assert all(name.startswith('mcp__nr_assistant__') for name in tools)
        assert any(message['role'] == 'tool' and 'local-tool-evidence' in str(message['content'])
                   for message in requests[1]['messages'])
        resumed = requests[2]['messages']
        assert any(message['role'] == 'assistant' and 'first-answer' in str(message['content'])
                   for message in resumed)
        assert sum(request['prompt'] in str(message['content']) for message in resumed) == 1
        assert request['resume_prompt'] in str(resumed[-1]['content'])
        assert not list(state.glob('config-*'))
        assert original_config == {file.name: file.read_bytes() for file in source.iterdir() if file.is_file()}
        if route == 'import_cli':
            assert original_history == {str(f.relative_to(source)): f.read_bytes() for f in (source / 'sessions').rglob('*.jsonl')}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
