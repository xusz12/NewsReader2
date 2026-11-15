import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

import personal_assistant_runtime as runtime


NATIVE_ID = '0199a213-81c0-7800-8aa1-bbab2a035a53'


@pytest.fixture(params=['pi', 'codex'])
def invocation(tmp_path, monkeypatch, request):
    mode = request.param
    state = tmp_path / 'state'
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    script = tmp_path / 'bridge.py'
    monkeypatch.setattr(runtime.oar_client, 'bridge_command', lambda _: [sys.executable, str(script)])
    monkeypatch.setattr(runtime, '_pi_subprocess_env', lambda: os.environ.copy())
    profile = {'runtime': mode, 'codex': {'model': '', 'reasoning_effort': '', 'sandbox': 'read-only'},
               'pi': {'provider': 'fixture', 'model': 'fixture'}}
    args = dict(profile=profile, prompt='private question', resume_prompt='follow-up', state_dir=state,
                workspace=workspace, db=tmp_path / 'news.sqlite3', state_db=tmp_path / 'assistant.sqlite3',
                emit=lambda value: None, stopped=lambda: False, job_id='job', timeout=3)
    prefix = '''import sys,json,os,time,signal,subprocess
from pathlib import Path
request=json.load(sys.stdin)
state=Path(request['state_dir'])
(state / ('config-'+request['config_id']+'-fixture')).mkdir()
print(json.dumps({'type':'oar_session','id':''' + repr(NATIVE_ID) + ''','runtime':'oar-codex','resumed':False}),flush=True)
'''
    result = json.dumps({'type': 'oar_result', 'outcome': 'completed', 'answer': 'final answer', 'native_id': NATIVE_ID})
    return script, prefix, result, args


@pytest.mark.parametrize('ending,error', [
    ("print('invalid json',flush=True)", 'protocol_error'),
    ("print(result,flush=True);print(result,flush=True)", 'protocol_error'),
    ("print(result,flush=True);print(json.dumps({'type':'text_delta','delta':'late'}),flush=True)", 'protocol_error'),
    ("print(result,flush=True);sys.exit(2)", 'oar_runtime_failed'),
    ("pass", 'codex_runtime_failed|oar_protocol_error'),
])
def test_assistant_rejects_broken_completion_and_cleans_config(invocation, ending, error):
    script, prefix, result, args = invocation
    script.write_text(prefix + 'result=' + repr(result) + '\n' + ending)
    with pytest.raises(RuntimeError, match=error):
        runtime.run(**args)
    assert not list(args['state_dir'].glob('config-*'))
    assert not (args['state_dir'] / 'codex-session.json').exists()


@pytest.mark.parametrize('reason', ['timeout', 'stop'])
def test_assistant_interrupt_reaps_tools_after_result_and_cleans_config(invocation, reason):
    script, prefix, result, args = invocation
    pid_file = args['workspace'] / 'tool.pid'
    script.write_text(prefix + '''child=subprocess.Popen([sys.executable,'-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)'])
Path('tool.pid').write_text(str(child.pid))
print(''' + repr(result) + ''',flush=True)
time.sleep(60)
''')
    args['timeout'] = .4 if reason == 'timeout' else 3
    if reason == 'stop':
        args['stopped'] = pid_file.exists
    with pytest.raises(RuntimeError, match='assistant_timeout' if reason == 'timeout' else 'assistant_stopped'):
        runtime.run(**args)
    assert not list(args['state_dir'].glob('config-*'))
    assert not (args['state_dir'] / 'codex-session.json').exists()
    pid = int(pid_file.read_text())
    for _ in range(20):
        status = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True).stdout.strip()
        if not status or status.startswith('Z'):
            break
        time.sleep(.05)
    assert not status or status.startswith('Z')


def test_assistant_commits_only_clean_completion(invocation):
    script, prefix, result, args = invocation
    script.write_text(prefix + 'print(' + repr(result) + ',flush=True)')
    assert runtime.run(**args) == 'final answer'
    assert not list(args['state_dir'].glob('config-*'))
    if args['profile']['runtime'] == 'codex':
        assert json.loads((args['state_dir'] / 'codex-session.json').read_text())['thread_id'] == NATIVE_ID
