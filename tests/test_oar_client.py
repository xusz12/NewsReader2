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
