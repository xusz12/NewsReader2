import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import pytest

import personal_assistant_runtime
import codex_client
from test_personal_assistant import service, send
from test_oar_codex_native import native_runtime
from test_oar_codex import runtime as codex_transport
from test_oar_pi_news import pi_runtime


def finished(service, thread):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        snapshot = service.snapshot(thread)
        if snapshot['job']['status'] not in ('queued', 'running'):
            return snapshot
        time.sleep(.05)
    pytest.fail('OAR assistant did not finish')


def tool_calls():
    return [('mcp__nr_assistant__nr_query', {'sql': 'SELECT count(*) FROM items'}),
            ('mcp__nr_assistant__discussion_search', {'text': '政策'}),
            ('mcp__nr_assistant__memory_read', {'path': 'notes/关注方向.md'}),
            ('mcp__nr_assistant__memory_write', {'path': 'notes/关注方向.md', 'content': '用户关注政策的长期影响。',
                                              'revision': '', 'source_message_ids': [1]})]


def check_saved(service, thread):
    result = finished(service, thread)
    assert result['job']['status'] == 'done', result['job']
    memory = service.memory.read('notes/关注方向.md')['content']
    assert '政策' in memory and '消息 #1' in memory
    assert result['messages'][-1]['role'] == 'assistant'


def assert_stopped_processes(pids):
    for pid in json.loads(pids.read_text()):
        for _ in range(20):
            status = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True).stdout.strip()
            if not status or status.startswith('Z'):
                break
            time.sleep(.05)
        assert not status or status.startswith('Z')


@pytest.mark.parametrize('reason', ['timeout', 'stop'])
def test_codex_assistant_interrupt_reaps_detached_native_processes(service, codex_transport, monkeypatch, reason):
    command, request, env = codex_transport
    for name, value in {**env, 'NR_RPC_MODE': 'hang'}.items():
        monkeypatch.setenv(name, value)
    pids = Path(env['NR_RPC_PIDS'])
    state = service.root / 'runtime' / 'interrupted'
    with pytest.raises(RuntimeError, match='assistant_timeout' if reason == 'timeout' else 'assistant_stopped'):
        personal_assistant_runtime.run(profile=service.profile(), prompt='test', resume_prompt='test',
            state_dir=state, workspace=service.workspace, db=service.db_path(), state_db=service.state_db,
            emit=lambda value: None, stopped=pids.exists if reason == 'stop' else lambda: False, timeout=3)
    assert pids.exists()
    assert_stopped_processes(pids)
    assert not (state / 'codex-session.json').exists()


@pytest.mark.parametrize('reason', ['timeout', 'stop'])
def test_pi_assistant_interrupt_reaps_rpc_mcp_and_detached_tool(service, pi_runtime, monkeypatch, reason):
    source, cwd, state, request, calls, responses, run = pi_runtime
    env = {'PATH': os.environ['PATH'], 'HOME': str(source.parent), 'PI_OFFLINE': '1',
           'NEWS_READER_PI_AGENT_DIR': str(source)}
    monkeypatch.setattr(personal_assistant_runtime, '_pi_subprocess_env', lambda: env)
    pids = source.parent / 'tool-pids.json'
    monkeypatch.setattr(personal_assistant_runtime, 'tool_config', lambda *args: {
        'command': sys.executable,
        'args': [str(Path(__file__).parent / 'fixtures/pi_news_mcp.py'), str(pids)]})
    responses.append(('mcp__nr_assistant__web_search', {'query': 'fixture'}))
    profile = service.profile() | {'runtime': 'pi', 'pi': {'provider': 'nr-fixture', 'model': 'fixture'}}
    with pytest.raises(RuntimeError, match='assistant_timeout' if reason == 'timeout' else 'assistant_stopped'):
        personal_assistant_runtime.run(profile=profile, prompt='test', resume_prompt='test', state_dir=state,
            workspace=service.workspace, db=service.db_path(), state_db=service.state_db, emit=lambda value: None,
            stopped=pids.exists if reason == 'stop' else lambda: False, timeout=6)
    assert pids.exists()
    assert_stopped_processes(pids)
    assert not (state / 'session.json').exists()
    assert not list(state.glob('config-*'))


@pytest.mark.parametrize('legacy_cli', [False, True])
def test_installed_codex_assistant_executes_nr_mcp_and_resumes(service, native_runtime, monkeypatch, legacy_cli):
    command, request, env, calls, native_home, config = native_runtime
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    calls.responses.extend(tool_calls())
    def legacy_runner(**args):
        tools = personal_assistant_runtime.tool_config(args['db'], args['workspace'], args['state_db'], args['job_id'])
        import legacy_codex
        command = legacy_codex.command(args['profile']['codex'], cwd=args['workspace'])
        command[2:2] = ['--skip-git-repo-check', '-c', 'mcp_servers.nr_assistant.command=' + json.dumps(tools['command']),
                        '-c', 'mcp_servers.nr_assistant.args=' + json.dumps(tools['args']),
                        '-c', 'mcp_servers.nr_assistant.enabled=true', '-c', 'project_doc_max_bytes=0']
        result = subprocess.run(command, input=args['prompt'], capture_output=True, text=True, env=env, timeout=25)
        assert result.returncode == 0, result.stderr
        native_id, answer = legacy_codex.result(result.stdout)
        codex_client.write_thread(args['state_dir'], args['profile']['codex'], native_id)
        return answer
    service.runner = legacy_runner if legacy_cli else personal_assistant_runtime.run
    thread = send(service)
    check_saved(service, thread)
    state = service.root / 'runtime' / thread / str(service.profile()['epoch'])
    mapping = json.loads((state / 'codex-session.json').read_text())
    service.runner = personal_assistant_runtime.run
    send(service, '继续讨论刚才的关注方向', thread=thread)
    assert finished(service, thread)['job']['status'] == 'done'
    assert json.loads((state / 'codex-session.json').read_text())['thread_id'] == mapping['thread_id']
    serialized = json.dumps(calls, ensure_ascii=False)
    assert 'function_call_output' in serialized
    assert '政策' in serialized
    assert '同一讨论最近消息' not in calls[-1]['input'][-1]['content'][0]['text']
    for call in calls:
        server = next(t for t in call['tools'] if t.get('name') == 'mcp__nr_assistant')
        assert {t['name'] for t in server['tools']} == {name.removeprefix('mcp__nr_assistant__') for name, _ in tool_calls()} | {'nr_schema', 'memory_list'}


def test_installed_pi_assistant_has_only_nr_tools_and_migrates_mapping(service, pi_runtime, monkeypatch):
    source, cwd, state, request, calls, responses, run = pi_runtime
    env = {'PATH': os.environ['PATH'], 'HOME': str(source.parent), 'PI_OFFLINE': '1',
           'NEWS_READER_PI_AGENT_DIR': str(source)}
    monkeypatch.setattr(personal_assistant_runtime, '_pi_subprocess_env', lambda: env)
    marker = source.parent / 'unexpected-extension'
    (source / 'extensions').mkdir()
    (source / 'extensions/unrelated.ts').write_text(
        "import fs from 'node:fs';export default function(){fs.writeFileSync(" + json.dumps(str(marker)) + ", 'loaded');}")
    profile = service.profile()
    service.save_profile(profile | {'runtime': 'pi', 'pi': {'provider': 'nr-fixture', 'model': 'fixture', 'reasoning_effort': 'off'}})
    responses.extend([*tool_calls(), '已保存关注方向。'])
    def legacy_runner(**args):
        tools = personal_assistant_runtime.tool_config(args['db'], args['workspace'], args['state_db'], args['job_id'])
        (source / 'mcp.json').write_text(json.dumps({'mcpServers': {'nr_assistant': {**tools, 'exposure': 'direct'}}}))
        state = args['state_dir']
        session_id = str(uuid.uuid4())
        sessions = state / 'sessions' / ('--' + str(args['workspace']).lstrip('/').replace('/', '-') + '--')
        sessions.mkdir(parents=True)
        cli = Path(__file__).resolve().parents[1] / 'node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js'
        result = subprocess.run(['node', str(cli), '-p', '--mode', 'json', '--session-dir', str(sessions),
            '--session-id', session_id, '--provider', 'nr-fixture', '--model', 'fixture', '--thinking', 'off',
            '--no-extensions', '-e', 'builtin:mcp', '--no-skills', '--tools', 'mcp__nr_assistant__*'],
            input=args['prompt'], cwd=args['workspace'], env={**env, 'PI_CODING_AGENT_DIR': str(source)},
            capture_output=True, text=True, timeout=25)
        assert result.returncode == 0, result.stderr
        events = [json.loads(line) for line in result.stdout.splitlines()]
        message = [event['message'] for event in events if event['type'] == 'message_end' and event['message']['role'] == 'assistant'][-1]
        assert message['stopReason'] == 'stop'
        (state / 'session.json').write_text(json.dumps({'id': session_id, 'runtime': 'cli'}))
        (state / 'route.json').write_text(json.dumps({'runtime': 'cli'}))
        return ''.join(part['text'] for part in message['content'] if part['type'] == 'text')
    service.runner = legacy_runner
    thread = send(service)
    check_saved(service, thread)
    state = service.root / 'runtime' / thread / str(service.profile()['epoch'])
    mapping = json.loads((state / 'session.json').read_text())
    assert mapping['runtime'] == 'cli'
    managed_start = len(calls)
    responses.append('继续讨论。')
    service.runner = personal_assistant_runtime.run
    send(service, '继续讨论', thread=thread)
    assert finished(service, thread)['job']['status'] == 'done'
    saved = json.loads((state / 'session.json').read_text())
    assert saved['id'] == mapping['id'] and saved['runtime'] == 'oar'
    assert not (state / 'route.json').exists()
    assert not marker.exists()
    assert not list(state.glob('config-*'))
    for call in calls[managed_start:]:
        names = {t['function']['name'] for t in call.get('tools', [])}
        assert {n for n in names if not n.startswith('mcp__nr_assistant__')} == {'mcp__nr_web__web_search', 'mcp__nr_web__web_fetch'}
        assert len(names) == 8
    assert '已保存关注方向。' in json.dumps(calls[-1]['messages'], ensure_ascii=False)
