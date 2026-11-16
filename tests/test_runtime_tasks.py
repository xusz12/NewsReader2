import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import codex_client
import connection_tests
import llm_client
import oar_client
import runtime_tasks
from test_oar_pi_news import pi_runtime
from test_oar_codex_native import native_runtime


@pytest.mark.parametrize('task', ['translation', 'connection_test'])
@pytest.mark.parametrize('finish', ['stop', 'length'])
def test_installed_pi_material_tasks_isolate_tools_history_and_resources(pi_runtime, monkeypatch, task, finish):
    source, cwd, state, request, calls, responses, _ = pi_runtime
    marker = source.parent / 'loaded-extension'
    (source / 'extensions').mkdir()
    (source / 'extensions/test.ts').write_text("import fs from 'node:fs';export default function(){fs.writeFileSync(" + json.dumps(str(marker)) + ", 'unexpected');}")
    original = (source / 'settings.json').read_bytes()
    env = {'PATH': os.environ['PATH'], 'HOME': str(source.parent), 'PI_OFFLINE': '1', 'NEWS_READER_PI_AGENT_DIR': str(source)}
    monkeypatch.setattr(runtime_tasks, '_pi_subprocess_env', lambda: env)
    config = {'runtime': 'pi', 'pi': {'provider': 'nr-fixture', 'model': 'fixture', 'reasoning_effort': 'off'}}
    for _ in range(2 if finish == 'stop' else 1):
        responses.append({'text': 'NEWSREADER_CONNECTION_OK', 'finish': finish})
        if finish == 'stop':
            assert runtime_tasks.run(config, 'isolated request', task=task, timeout=25) == 'NEWSREADER_CONNECTION_OK'
        else:
            with pytest.raises(RuntimeError, match='oar_incomplete_answer'):
                runtime_tasks.run(config, 'isolated request', task=task, timeout=25)
    assert not marker.exists()
    assert (source / 'settings.json').read_bytes() == original
    assert not list(source.rglob('*.jsonl'))
    assert all(not call.get('tools') for call in calls)
    assert all(call['model'] == 'fixture' for call in calls)
    assert all('NEWSREADER_CONNECTION_OK' not in json.dumps(call['messages']) for call in calls)


def test_installed_codex_connection_is_ephemeral_and_disables_external_mcp(native_runtime, monkeypatch):
    command, request, env, calls, native_home, selected = native_runtime
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    marker = native_home / 'outside-tool-loaded'
    script = native_home / 'outside.py'
    script.write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').touch()\n')
    original_config = (native_home / 'config.toml').read_text()
    with (native_home / 'config.toml').open('a') as f:
        f.write('\n[mcp_servers.outside]\ncommand=' + json.dumps(sys.executable) + '\nargs=[' + json.dumps(str(script)) + ']\n')
    for _ in range(2):
        assert runtime_tasks.run({'runtime': 'codex', 'codex': selected}, 'connection prompt',
                                 task='connection_test', timeout=25).startswith('Local response')
    assert not marker.exists()
    assert not list((native_home / 'sessions').rglob('*.jsonl'))
    assert all(call['model'] == selected['model'] and call['reasoning']['effort'] == selected['reasoning_effort'] for call in calls)
    for call in calls:
        assert 'Local response' not in json.dumps(call['input'])
        for tool in call.get('tools', []):
            assert tool.get('name') in {'tool_search', 'list_mcp_resources', 'list_mcp_resource_templates', 'read_mcp_resource', 'request_user_input'}
            assert not tool.get('type', '').startswith('web_search')
    (native_home / 'config.toml').write_text(original_config)


def fake_bridge(tmp_path, monkeypatch, mode):
    script = tmp_path / 'bridge.py'
    record = tmp_path / 'request.json'
    pids = tmp_path / 'pids.json'
    script.write_text('''import os,sys,json,time,subprocess
request=json.load(sys.stdin)
json.dump(request,open(sys.argv[1],'w'))
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
json.dump([os.getpid(),child.pid],open(sys.argv[2],'w'))
def emit(value):print(json.dumps(value),flush=True)
native='0199a213-81c0-7800-8aa1-bbab2a035a53'
emit({'type':'oar_session','runtime':'oar-'+request.get('runtime','pi'),'id':native})
mode=sys.argv[3]
if mode=='tool':emit({'type':'oar_tool','tool':'forbidden'})
if mode.startswith('error:'):emit({'type':'oar_result','outcome':'failed','error':mode[6:]})
elif mode!='partial':emit({'type':'oar_result','outcome':'completed','answer':'bad' if mode=='bad' else 'NEWSREADER_CONNECTION_OK','native_id':native})
if mode=='duplicate':emit({'type':'oar_result','outcome':'completed','answer':'second','native_id':native})
if mode=='linger' or mode=='partial':time.sleep(30)
sys.exit(1 if mode=='nonzero' else 0)
''')
    monkeypatch.setattr(oar_client, 'bridge_command', lambda *_: [sys.executable, str(script), str(record), str(pids), mode])
    monkeypatch.setattr(codex_client, 'require_ready', lambda: None)
    monkeypatch.setattr(codex_client, 'isolated_launch_args', lambda *_: [])
    monkeypatch.setattr(codex_client, 'executable', lambda: sys.executable)
    return record, pids


@pytest.mark.parametrize('runtime', ['pi', 'codex'])
@pytest.mark.parametrize('mode,category', [('ok', ''), ('bad', 'invalid_answer'), ('nonzero', 'invalid_answer'),
    ('duplicate', 'invalid_answer'), ('tool', 'tool_mode_violation'), ('partial', 'timeout'), ('linger', 'timeout'),
    ('error:oar_auth_required', 'credentials'), ('error:oar_quota', 'quota'), ('error:oar_rate_limited', 'rate'),
    ('error:oar_incomplete_answer', 'invalid_answer'), ('error:oar_dependency_missing', 'dependencies')])
def test_connection_requires_clean_completion_and_cleans_owned_processes(tmp_path, monkeypatch, runtime, mode, category):
    record, pids = fake_bridge(tmp_path, monkeypatch, mode)
    failures = []
    original = runtime_tasks.run
    def recorded(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except Exception as exc:
            failures.append((type(exc).__name__, str(exc)))
            raise
    monkeypatch.setattr(runtime_tasks, 'run', recorded)
    monkeypatch.setattr(connection_tests, 'TIMEOUT', .25 if mode in {'partial', 'linger'} else 3)
    config = {'runtime': runtime, 'pi': {'provider': 'p', 'model': 'm'}, 'codex': {'model': 'm'}}
    result = connection_tests.test_connection(config)
    assert result['category'] == category, failures
    assert result['status'] == ('failed' if category else 'success')
    request = json.loads(record.read_text())
    assert request['task'] == 'connection_test' and not request.get('resume')
    assert not Path(request['cwd']).exists() and not Path(request['state_dir']).exists()
    for pid in json.loads(pids.read_text()):
        state = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True).stdout.strip()
        assert not state or state.startswith('Z')


def test_pi_translation_does_not_publish_incomplete_chinese_json(tmp_path, monkeypatch):
    fake_bridge(tmp_path, monkeypatch, 'error:oar_incomplete_answer')
    with pytest.raises(llm_client.LLMClientError, match='PI_FALLBACK_FAILED'):
        llm_client.generate_pi_fallback_translation(title='title', source='source', content='body', pi_provider='p', pi_model='m')
