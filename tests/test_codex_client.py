from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import threading
import uuid

import pytest
import codex_client as codex
from test_api import _setup_agent_api_fixture, _install_fake_oar

THREAD = '0199a213-81c0-7800-8aa1-bbab2a035a53'
CONFIG = {'model': 'test-model', 'profile': 'test-profile', 'sandbox': 'read-only'}


def fake_codex(tmp_path, monkeypatch):
    script = tmp_path / 'fake-codex'
    log = tmp_path / 'codex-invocations.jsonl'
    script.write_text('''#!/usr/bin/env python3
import json,os,sys,time,signal
args=sys.argv[1:]
mode=os.environ.get('CODEX_TEST_MODE','success')
if args==['login','status']:
 print('unexpected private DO_NOT_EXPOSE' if mode=='probe_unknown' else 'Not logged in' if mode in ('auth','custom_provider') else 'Logged in using ChatGPT', file=sys.stderr)
 sys.exit(1 if mode in ('auth','custom_provider','probe_unknown') else 0)
if '--help' in args:
 print('exec --json --sandbox --cd resume')
 sys.exit(0)
prompt=sys.stdin.read()
with open(os.environ['CODEX_TEST_LOG'],'a') as out:
 out.write(json.dumps({'args':args,'prompt':prompt,'cwd':os.getcwd()})+'\\n')
thread='0199a213-81c0-7800-8aa1-bbab2a035a53'
if 'resume' in args: thread=args[args.index('resume')+1]
def emit(value): print(json.dumps(value),flush=True)
emit({'type':'thread.started','thread_id':thread})
if mode.startswith('diagnostics'):
 for index in range(2):
  emit({'type':'item.completed','item':{'id':'diagnostic_'+str(index),'type':'error',
       'message':'startup diagnostic private secret=DO_NOT_EXPOSE'}})
 mode=mode.removeprefix('diagnostics_')
emit({'type':'turn.started'})
if mode=='auth':
 emit({'type':'turn.failed','error':{'message':'401 unauthorized private DO_NOT_EXPOSE'}});sys.exit(1)
if mode=='malformed': print('not json',flush=True);sys.exit()
if mode=='unknown': emit({'type':'unknown'});sys.exit()
if mode=='runtime_error':
 print('secret=DO_NOT_EXPOSE',file=sys.stderr)
 emit({'type':'error','message':'private secret=DO_NOT_EXPOSE'})
 emit({'type':'turn.failed','error':{'message':'private secret=DO_NOT_EXPOSE'}});sys.exit(1)
if mode=='hang':
 emit({'type':'item.updated','item':{'id':'a','type':'agent_message','text':'partial'}})
 time.sleep(20);sys.exit()
emit({'type':'item.updated','item':{'id':'a','type':'agent_message','text':'partial'}})
if mode=='missing': sys.exit()
emit({'type':'item.completed','item':{'id':'a','type':'agent_message','text':'' if mode=='empty' else '有效最终结论'}})
if mode=='duplicate_item': emit({'type':'item.completed','item':{'id':'a','type':'agent_message','text':'重复'}})
emit({'type':'turn.completed','usage':{}})
if mode=='duplicate': emit({'type':'turn.completed','usage':{}})
if mode=='nonzero': sys.exit(1)
''')
    script.chmod(0o755)
    monkeypatch.setenv('NEWS_READER_CODEX', str(script))
    monkeypatch.setenv('CODEX_TEST_LOG', str(log))
    monkeypatch.setenv('CODEX_TEST_MODE', 'success')
    return log


def select_codex(app, monkeypatch, config=CONFIG):
    settings = app.default_app_settings()
    settings['agent'].update(runtime='codex', codex=dict(config))
    monkeypatch.setattr(app, 'load_app_settings', lambda: settings)
    return settings


def test_command_pins_cwd_sandbox_and_does_not_put_prompt_or_credentials_in_argv(tmp_path, monkeypatch):
    fake_codex(tmp_path, monkeypatch)
    for thread in ('', THREAD):
        args = codex.command(CONFIG, thread_id=thread)
        assert args[args.index('--cd')+1] == str(codex.ROOT.resolve())
        assert args[args.index('--sandbox')+1] == 'read-only'
        assert 'approval_policy="never"' in args and 'sandbox_mode="read-only"' in args
        assert '--add-dir' not in args and '--dangerously-bypass-approvals-and-sandbox' not in args
        assert args[-1] == '-'
        if thread:
            assert args[-3:] == ['resume', thread, '-']
    with pytest.raises(RuntimeError, match='codex_session_invalid'):
        codex.command(CONFIG, thread_id='newsreader-oar-pi-foreign')


@pytest.mark.parametrize('config', [None, {'model': []}, {'model': '--unsafe'}, {'profile':'../../unsafe'},
                                   {'profile':'bad name'}, {'sandbox':'danger-full-access'}, {'model':'x'*121}])
def test_invalid_config_rejected(config):
    with pytest.raises(ValueError): codex.normalize_config(config)


@pytest.mark.parametrize('mode,error', [('auth','codex_auth_required'),('malformed','codex_protocol_error'),
 ('unknown','codex_protocol_error'),('empty','codex_empty_answer'),('missing','codex_protocol_error'),
 ('duplicate','codex_protocol_error'),('duplicate_item','codex_protocol_error'),
 ('runtime_error','codex_turn_failed'),('nonzero','codex_runtime_failed')])
def test_sync_strict_failure_and_redaction(tmp_path, monkeypatch, mode, error):
    fake_codex(tmp_path, monkeypatch)
    monkeypatch.setenv('CODEX_TEST_MODE',mode)
    with pytest.raises(RuntimeError) as raised:
        codex.run_once(CONFIG, 'private prompt', timeout=4)
    assert str(raised.value)==error
    assert 'DO_NOT_EXPOSE' not in str(raised.value)


@pytest.mark.parametrize('mode', ['success', 'diagnostics_success'])
def test_sync_success_and_independent_ephemeral_archive(tmp_path, monkeypatch, mode):
    log=fake_codex(tmp_path, monkeypatch)
    monkeypatch.setenv('CODEX_TEST_MODE', mode)
    answer,thread=codex.run_once(CONFIG,'private prompt')
    assert answer=='有效最终结论' and thread==THREAD
    answer2,thread2=codex.run_once(CONFIG,'follow up',thread_id=thread)
    codex.run_once(CONFIG,'archive',ephemeral=True)
    rows=[json.loads(line) for line in log.read_text().splitlines()]
    assert 'resume' not in rows[0]['args'] and rows[1]['args'][-3:]==['resume',THREAD,'-']
    assert '--ephemeral' in rows[2]['args'] and 'resume' not in rows[2]['args']
    assert rows[0]['prompt']=='private prompt' and 'private prompt' not in rows[0]['args']
    assert (answer2,thread2)==(answer,thread)


def test_state_mapping_is_private_atomic_and_runtime_isolated(tmp_path):
    codex.write_thread(tmp_path,CONFIG,THREAD)
    assert codex.read_thread(tmp_path,CONFIG)==THREAD
    assert (tmp_path/'codex-session.json').stat().st_mode & 0o777 == 0o600
    with pytest.raises(RuntimeError): codex.read_thread(tmp_path,{**CONFIG,'model':'changed'})
    (tmp_path/'codex-session.json').write_text(json.dumps({'runtime':'pi','config':CONFIG,'thread_id':THREAD}))
    with pytest.raises(RuntimeError,match='codex_session_invalid'): codex.read_thread(tmp_path,CONFIG)


def test_parser_deltas_and_commentary_do_not_fill_empty_terminal():
    p=codex.EventParser()
    for event in [{'type':'thread.started','thread_id':THREAD},{'type':'turn.started'},
                  {'type':'item.updated','item':{'id':'a','type':'agent_message','text':'partial'}},
                  {'type':'item.completed','item':{'id':'a','type':'agent_message','text':'commentary','phase':'commentary'}}]:
        p.parse(json.dumps(event))
    assert p.parse('{"type":"turn.completed"}')['error']=='codex_empty_answer'
    p=codex.EventParser(str(uuid.uuid4()))
    with pytest.raises(RuntimeError): p.parse(json.dumps({'type':'thread.started','thread_id':THREAD}))


def test_settings_defaults_persistence_and_invalid_runtime(tmp_path, monkeypatch):
    import settings
    path=tmp_path/'settings.json'
    monkeypatch.setenv('NEWS_READER_APP_SETTINGS_PATH',str(path))
    assert settings.load_app_settings()['agent']['runtime']=='pi'
    value=settings.default_app_settings();value['agent'].update(runtime='codex',codex=CONFIG)
    settings.save_app_settings(value)
    assert settings.load_app_settings()['agent']['codex']==CONFIG
    assert settings.load_app_settings()['agent']['runtime']=='codex'
    import app
    monkeypatch.setattr(app,'load_app_settings',settings.load_app_settings)
    assert app.current_runtime_settings()['agent']['runtime']=='codex'
    assert app.validate_runtime_settings(value)['agent']==value['agent']
    for bad in ('other',[],{}):
        value['agent']['runtime']=bad
        with pytest.raises(ValueError): app.validate_runtime_settings(value)


@pytest.mark.parametrize('mode,error',[('empty','codex_empty_answer'),('missing','codex_protocol_error'),
 ('unknown','codex_protocol_error'),('duplicate','codex_protocol_error'),('auth','codex_auth_required'),
 ('runtime_error','codex_turn_failed'),('diagnostics_empty','codex_empty_answer'),
 ('diagnostics_duplicate_item','codex_protocol_error')])
def test_background_failure_no_session_promotion_and_safe_retry(tmp_path,monkeypatch,mode,error):
    app,client,item,_,_=_setup_agent_api_fixture(tmp_path,monkeypatch)
    fake_codex(tmp_path,monkeypatch);select_codex(app,monkeypatch)
    first=client.post(f"/api/news/{item['id']}/agent/jobs",json={'question':'request'}).get_json()
    monkeypatch.setenv('CODEX_TEST_MODE',mode)
    assert app.process_pending_agent_once()
    payload=app.load_agent_payload(item['id']);job=payload['jobs'][-1]
    assert (job['status'],job['error'])==('failed',error)
    assert 'DO_NOT_EXPOSE' not in json.dumps(payload)
    with app.agent_db_conn() as conn:
        assert conn.execute('SELECT executor_session_id FROM agent_sessions').fetchone()[0]==''
    monkeypatch.setenv('CODEX_TEST_MODE','success')
    assert client.post(f"/api/news/{item['id']}/agent/jobs/{first['job_id']}/retry").status_code==202
    assert app.process_pending_agent_once()
    assert app.load_agent_payload(item['id'])['jobs'][-1]['status']=='succeeded'


@pytest.mark.parametrize('mode', ['success', 'diagnostics_success'])
def test_background_resume_settings_sticky_archive_and_new_session(tmp_path,monkeypatch,mode):
    app,client,item,_,_=_setup_agent_api_fixture(tmp_path,monkeypatch)
    log=fake_codex(tmp_path,monkeypatch);settings=select_codex(app,monkeypatch)
    monkeypatch.setenv('CODEX_TEST_MODE', mode)
    first=client.post(f"/api/news/{item['id']}/agent/jobs",json={'question':'first'}).get_json()
    assert first['session']['provider']=='codex' and first['session']['executor_config']==CONFIG
    assert app.process_pending_agent_once()
    payload=client.get(f"/api/news/{item['id']}/agent/session").get_json()
    assert payload['jobs'][-1]['answer_text']=='有效最终结论'
    assert 'DO_NOT_EXPOSE' not in json.dumps(payload)
    settings['agent']['runtime']='pi';settings['agent']['codex']['model']='changed'
    settings['llm']['pi_chat']={'provider':'ollama','model':'minimax-m3:cloud'}
    second=client.post(f"/api/news/{item['id']}/agent/jobs",json={'question':'second','session_id':first['session']['id']})
    assert second.status_code==202 and second.get_json()['session']['provider']=='codex'
    assert app.process_pending_agent_once()
    archive=client.post(f"/api/news/{item['id']}/chat/archive",json={'agent_session_id':first['session']['id'],
        'messages':[{'role':'user','content':'question'},{'role':'assistant','content':'answer'}]})
    assert archive.status_code==200 and archive.get_json()['provider']=='codex'
    rows=[json.loads(line) for line in log.read_text().splitlines()]
    assert rows[1]['args'][-3:]==['resume',THREAD,'-']
    assert rows[1]['args'][rows[1]['args'].index('--model')+1]=='test-model'
    assert '--ephemeral' in rows[2]['args'] and 'resume' not in rows[2]['args']
    assert app.load_agent_payload(item['id'])['session']['id']==first['session']['id']
    fresh=client.post(f"/api/news/{item['id']}/agent/session",json={'new_session':True})
    assert fresh.status_code==200 and fresh.get_json()['session']['provider']=='pi'


@pytest.mark.parametrize('window',['command','registration','cleanup'])
def test_codex_stop_during_launch_or_cleanup_blocks_retry_until_reaped(tmp_path,monkeypatch,window):
    app,client,item,_,_=_setup_agent_api_fixture(tmp_path,monkeypatch)
    fake_codex(tmp_path,monkeypatch);select_codex(app,monkeypatch)
    submitted=client.post(f"/api/news/{item['id']}/agent/jobs",json={'question':'request'}).get_json()
    entered,release=threading.Event(),threading.Event()
    if window=='command':
        original=app._agent_process_command
        def held(*a,**kw):
            entered.set();assert release.wait(8);return original(*a,**kw)
        monkeypatch.setattr(app,'_agent_process_command',held)
    elif window=='registration':
        original=app.subprocess.Popen
        def held(*a,**kw):
            proc=original(*a,**kw)
            if len(a[0])>2 and a[0][1]=='exec' and '--help' not in a[0]:
                entered.set();assert release.wait(8)
            return proc
        monkeypatch.setattr(app.subprocess,'Popen',held)
    else:
        original=app.terminate_group
        def held(*a,**kw):
            result=original(*a,**kw)
            if threading.current_thread() is worker:
                entered.set();assert release.wait(8)
            return result
        monkeypatch.setattr(app,'terminate_group',held)
    worker=threading.Thread(target=app.process_pending_agent_once);worker.start()
    try:
        assert entered.wait(5)
        response=client.post(f"/api/news/{item['id']}/agent/jobs/{submitted['job_id']}/stop")
        assert response.get_json()['job']['status']=='stopping'
        assert client.post(f"/api/news/{item['id']}/agent/jobs/{submitted['job_id']}/retry").status_code==409
        assert client.post(f"/api/news/{item['id']}/agent/jobs",json={'question':'overlap'}).status_code==409
    finally:
        release.set();worker.join(8)
    assert not worker.is_alive()
    assert app.load_agent_payload(item['id'])['jobs'][-1]['status']=='stopped'
    assert client.post(f"/api/news/{item['id']}/agent/jobs/{submitted['job_id']}/retry").status_code==202
    assert app.process_pending_agent_once()
    assert app.load_agent_payload(item['id'])['jobs'][-1]['status']=='succeeded'


def test_sync_api_dispatch_resume_and_foreign_namespace(tmp_path, monkeypatch):
    app, client, item, _, _ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    log = fake_codex(tmp_path, monkeypatch)
    settings = select_codex(app, monkeypatch)
    url = f"/api/news/{item['id']}/chat"
    first = client.post(url, json={'question': 'first'}).get_json()
    assert first['ok'] and first['provider'] == 'codex'
    assert first['session_id'].startswith('newsreader-codex-chat-')
    settings['agent']['runtime'] = 'pi'
    settings['agent']['codex']['model'] = 'changed'
    second = client.post(url, json={'question': 'next', 'session_id': first['session_id']}).get_json()
    assert second['ok'] and second['session_id'] == first['session_id']
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert rows[1]['args'][-3:] == ['resume', THREAD, '-']
    assert rows[1]['args'][rows[1]['args'].index('--model') + 1] == 'test-model'
    for invalid in ('newsreader-codex-agent-' + str(uuid.uuid4()), 'newsreader-codex-chat-../../escape'):
        bad = client.post(url, json={'question': 'next', 'session_id': invalid})
        assert not bad.get_json()['ok']
    assert len(log.read_text().splitlines()) == 2


def test_pi_saved_session_and_archive_remain_pi_after_runtime_change(tmp_path, monkeypatch):
    app, client, item, _, _ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    pi_log = _install_fake_oar(tmp_path, monkeypatch)
    codex_log = fake_codex(tmp_path, monkeypatch)
    settings = select_codex(app, monkeypatch)
    settings['agent']['runtime'] = 'pi'
    settings['llm']['pi_chat'] = {'provider':'ollama','model':'minimax-m3:cloud'}
    first = client.post(f"/api/news/{item['id']}/agent/jobs", json={'question': 'Pi first'}).get_json()
    assert first['session']['provider'] == 'pi'
    assert app.process_pending_agent_once()
    settings['agent']['runtime'] = 'codex'
    second = client.post(f"/api/news/{item['id']}/agent/jobs", json={'question': 'Pi next', 'session_id': first['session']['id']})
    assert second.get_json()['session']['provider'] == 'pi'
    assert app.process_pending_agent_once()
    archived = client.post(f"/api/news/{item['id']}/chat/archive", json={
        'agent_session_id': first['session']['id'], 'runtime': 'codex',
        'messages': [{'role': 'user', 'content': 'question'}, {'role': 'assistant', 'content': 'answer'}]})
    assert archived.status_code == 200 and archived.get_json()['provider'] == 'pi'
    assert not codex_log.exists()
    assert len(pi_log.read_text().splitlines()) == 3


def test_failed_archive_leaves_saved_note_and_session_unchanged(tmp_path, monkeypatch):
    app, client, item, _, _ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    fake_codex(tmp_path, monkeypatch)
    select_codex(app, monkeypatch)
    first = client.post(f"/api/news/{item['id']}/agent/jobs", json={'question': 'first'}).get_json()
    assert app.process_pending_agent_once()
    with app.db_conn() as conn:
        conn.execute('INSERT INTO article_notes(url,note,created_at,updated_at) VALUES(?,?,?,?)',
                     (item['url'], '保留的旧笔记', app.now_ts(), app.now_ts()))
        conn.commit()
    before = app.load_agent_payload(item['id'])
    monkeypatch.setenv('CODEX_TEST_MODE', 'empty')
    failed = client.post(f"/api/news/{item['id']}/chat/archive", json={
        'agent_session_id': first['session']['id'],
        'messages': [{'role': 'user', 'content': 'question'}, {'role': 'assistant', 'content': 'answer'}]})
    assert failed.status_code == 502 and failed.get_json()['error'] == 'codex_empty_answer'
    with app.db_conn() as conn:
        assert conn.execute('SELECT note FROM article_notes WHERE url=?', (item['url'],)).fetchone()[0] == '保留的旧笔记'
    assert app.load_agent_payload(item['id']) == before


@pytest.mark.parametrize('orphan', [False, True])
def test_missing_or_orphaned_native_mapping_fails_without_starting_new_turn(tmp_path, monkeypatch, orphan):
    app, client, item, _, _ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    log = fake_codex(tmp_path, monkeypatch)
    select_codex(app, monkeypatch)
    first = client.post(f"/api/news/{item['id']}/agent/jobs", json={'question': 'first'}).get_json()
    assert app.process_pending_agent_once()
    session_id = first['session']['id']
    if orphan:
        with app.agent_db_conn() as conn:
            conn.execute("UPDATE agent_sessions SET executor_session_id='' WHERE id=?", (session_id,))
            conn.commit()
    else:
        (app.agent_session_runtime_dir('newsreader-codex-agent-' + session_id) / 'codex-session.json').unlink()
    assert client.post(f"/api/news/{item['id']}/agent/jobs", json={'question': 'next', 'session_id': session_id}).status_code == 202
    assert app.process_pending_agent_once()
    assert app.load_agent_payload(item['id'])['jobs'][-1]['error'] == 'codex_session_invalid'
    assert len(log.read_text().splitlines()) == 1


def test_sync_timeout_reaps_process_group(tmp_path, monkeypatch):
    import time
    fake_codex(tmp_path, monkeypatch)
    monkeypatch.setenv('CODEX_TEST_MODE', 'hang')
    start = time.monotonic()
    with pytest.raises(RuntimeError, match='codex_timeout'):
        codex.run_once(CONFIG, 'request', timeout=0.3)
    assert time.monotonic() - start < 5


def test_macos_zombie_leader_permission_error_reaps_and_retries(monkeypatch):
    import oar_client
    import signal
    calls = []
    class Child:
        pid = 987654
        reaped = False
        def poll(self):
            self.reaped = True
            return 0
        def wait(self, timeout):
            self.reaped = True
            return 0
    child = Child()
    def killpg(pid, sig):
        assert pid == child.pid
        calls.append(sig)
        if not child.reaped:
            raise PermissionError('zombie leader')
    monkeypatch.setattr(oar_client.os, 'killpg', killpg)
    oar_client.terminate_group(child)
    assert calls == [signal.SIGTERM, signal.SIGTERM, signal.SIGKILL]


def test_permission_error_for_live_process_is_not_ignored(monkeypatch):
    import oar_client
    class Child:
        pid = 987654
        def poll(self): return None
        def wait(self, timeout): raise subprocess.TimeoutExpired('live child', timeout)
    def denied(*args): raise PermissionError('live child')
    monkeypatch.setattr(oar_client.os, 'killpg', denied)
    with pytest.raises(PermissionError): oar_client.terminate_group(Child())


def test_error_event_cannot_be_overridden_by_completed_answer():
    parser = codex.EventParser()
    for event in [
        {'type': 'thread.started', 'thread_id': THREAD},
        {'type': 'turn.started'},
        {'type': 'error', 'message': 'private secret=DO_NOT_EXPOSE'},
        {'type': 'item.completed', 'item': {'id': 'a', 'type': 'agent_message', 'text': 'misleading answer'}},
    ]:
        parser.parse(json.dumps(event))
    assert parser.parse(json.dumps({'type': 'turn.completed'})) == {
        'type': 'oar_result', 'outcome': 'failed', 'error': 'codex_turn_failed'}


def test_nested_failed_auth_is_classified_without_diagnostic_leak():
    parser = codex.EventParser()
    result = parser.parse(json.dumps({'type': 'turn.failed', 'error': {
        'message': '401 unauthorized private secret=DO_NOT_EXPOSE'}}))
    assert result == {'type': 'oar_result', 'outcome': 'failed', 'error': 'codex_auth_required'}


def test_sync_archive_keeps_saved_codex_config_after_default_changes(tmp_path, monkeypatch):
    app, client, item, _, _ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    log = fake_codex(tmp_path, monkeypatch)
    settings = select_codex(app, monkeypatch)
    first = client.post(f"/api/news/{item['id']}/chat", json={'question': 'first'}).get_json()
    settings['agent']['runtime'] = 'pi'
    settings['agent']['codex']['model'] = 'changed'
    result = client.post(f"/api/news/{item['id']}/chat/archive", json={
        'session_id': first['session_id'],
        'messages': [{'role': 'assistant', 'content': first['answer']}]})
    assert result.status_code == 200 and result.get_json()['provider'] == 'codex'
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert '--ephemeral' in rows[-1]['args'] and 'resume' not in rows[-1]['args']
    assert rows[-1]['args'][rows[-1]['args'].index('--model') + 1] == 'test-model'


def test_sync_pi_archive_does_not_change_family_after_default_changes(tmp_path, monkeypatch):
    app, client, item, _, _ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    log = fake_codex(tmp_path, monkeypatch)
    select_codex(app, monkeypatch)
    calls = []
    monkeypatch.setattr(app, 'run_pi_chat_archive', lambda **kw: calls.append(kw) or {
        'summary': 'Pi saved archive', 'provider': 'pi', 'model': kw['pi_model']})
    result = client.post(f"/api/news/{item['id']}/chat/archive", json={
        'session_id': 'newsreader-oar-chat-saved-id',
        'messages': [{'role': 'assistant', 'content': 'Pi saved answer for archival.'}]})
    assert result.status_code == 200 and result.get_json()['provider'] == 'pi'
    assert len(calls) == 1 and not log.exists()


def test_codex_background_timeout_does_not_promote_session(tmp_path, monkeypatch):
    app, client, item, _, _ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    fake_codex(tmp_path, monkeypatch)
    select_codex(app, monkeypatch)
    monkeypatch.setenv('CODEX_TEST_MODE', 'hang')
    monkeypatch.setattr(app, 'AGENT_JOB_TIMEOUT_SECONDS', 0.3)
    client.post(f"/api/news/{item['id']}/agent/jobs", json={'question': 'timeout'})
    assert app.process_pending_agent_once()
    saved = app.load_agent_payload(item['id'])
    assert saved['jobs'][-1]['status'] == 'failed' and saved['jobs'][-1]['error'] == 'agent_timeout'
    with app.agent_db_conn() as conn:
        assert conn.execute('SELECT executor_session_id FROM agent_sessions WHERE id=?', (saved['session']['id'],)).fetchone()[0] == ''
    assert not app.AGENT_PROCESSES
    assert not list(app.AGENT_RUNTIME_DIR.glob('*/codex-session.json'))




@pytest.mark.parametrize('expected_thread', ['', THREAD])
def test_parser_accepts_exact_pre_turn_diagnostic_sequence_without_exposing_it(expected_thread):
    """Regression for CLI 0.158.0: startup error items can precede turn.started."""
    parser = codex.EventParser(expected_thread)
    parser.parse(json.dumps({'type': 'thread.started', 'thread_id': THREAD}))
    for item_id in ('diagnostic_0', 'diagnostic_1'):
        result = parser.parse(json.dumps({'type': 'item.completed', 'item': {
            'id': item_id, 'type': 'error', 'message': 'private secret=DO_NOT_EXPOSE'}}))
        assert result == {'type': 'oar_tool'}
        assert parser.answer == parser.stream_text == ''
        assert not parser.started and not parser.saw_error
    parser.parse(json.dumps({'type': 'turn.started'}))
    parser.parse(json.dumps({'type': 'item.completed', 'item': {
        'id': 'answer', 'type': 'agent_message', 'text': '有效最终结论'}}))
    assert parser.parse(json.dumps({'type': 'turn.completed'})) == {
        'type': 'oar_result', 'outcome': 'completed', 'answer': '有效最终结论'}


@pytest.mark.parametrize('event', [
    {'type': 'item.started', 'item': {'id': 'd', 'type': 'error', 'message': 'diagnostic'}},
    {'type': 'item.updated', 'item': {'id': 'd', 'type': 'error', 'message': 'diagnostic'}},
    {'type': 'item.completed', 'item': {'id': 'd', 'type': 'agent_message', 'text': 'premature'}},
    {'type': 'item.completed', 'item': {'id': 'd', 'type': 'reasoning', 'text': 'premature'}},
    {'type': 'item.completed', 'item': {'id': 'd', 'type': 'command_execution', 'command': 'unsafe'}},
    {'type': 'item.completed', 'item': {'id': 'd', 'type': 'unknown', 'message': 'diagnostic'}},
    {'type': 'item.completed', 'item': {'id': '', 'type': 'error', 'message': 'diagnostic'}},
    {'type': 'item.completed', 'item': {'id': 1, 'type': 'error', 'message': 'diagnostic'}},
    {'type': 'item.completed', 'item': {'type': 'error', 'message': 'diagnostic'}},
    {'type': 'item.completed', 'item': {'id': 'd', 'type': 'error'}},
    {'type': 'item.completed', 'item': {'id': 'd', 'type': 'error', 'message': None}},
    {'type': 'item.completed', 'item': {'id': 'd', 'type': 'error', 'message': {}}},
    {'type': 'item.completed', 'item': {'id': 'd', 'type': 'error', 'message': ' '}},
    {'type': 'item.completed', 'item': {'id': 'd', 'type': 'error', 'message': '界' * (codex.MAX_BYTES // 3 + 1)}},
    {'type': 'item.completed', 'item': []},
    {'type': 'unknown'},
    {'type': 'turn.completed'},
])
def test_pre_turn_diagnostic_exception_does_not_admit_invalid_events(event):
    parser = codex.EventParser()
    parser.parse(json.dumps({'type': 'thread.started', 'thread_id': THREAD}))
    with pytest.raises(RuntimeError, match='^codex_protocol_error$'):
        parser.parse(json.dumps(event))


def test_pre_turn_diagnostics_require_thread_and_unique_completed_ids():
    diagnostic = {'type': 'item.completed', 'item': {
        'id': 'diagnostic', 'type': 'error', 'message': 'diagnostic'}}
    with pytest.raises(RuntimeError, match='^codex_protocol_error$'):
        codex.EventParser().parse(json.dumps(diagnostic))
    for start_turn in (False, True):
        parser = codex.EventParser()
        parser.parse(json.dumps({'type': 'thread.started', 'thread_id': THREAD}))
        parser.parse(json.dumps(diagnostic))
        if start_turn:
            parser.parse(json.dumps({'type': 'turn.started'}))
        with pytest.raises(RuntimeError, match='^codex_protocol_error$'):
            parser.parse(json.dumps(diagnostic))
        with pytest.raises(RuntimeError, match='^codex_protocol_error$'):
            parser.parse(json.dumps({'type': 'item.completed', 'item': {
                'id': 'diagnostic', 'type': 'agent_message', 'text': 'duplicate reused id'}}))


@pytest.mark.parametrize('terminal', ['empty', 'top_level_error', 'failed'])
def test_pre_turn_diagnostics_do_not_fill_answer_or_override_failures(terminal):
    parser = codex.EventParser()
    parser.parse(json.dumps({'type': 'thread.started', 'thread_id': THREAD}))
    parser.parse(json.dumps({'type': 'item.completed', 'item': {
        'id': 'diagnostic', 'type': 'error', 'message': 'DO_NOT_EXPOSE'}}))
    parser.parse(json.dumps({'type': 'turn.started'}))
    if terminal == 'top_level_error':
        parser.parse(json.dumps({'type': 'error', 'message': 'private DO_NOT_EXPOSE'}))
        parser.parse(json.dumps({'type': 'item.completed', 'item': {
            'id': 'answer', 'type': 'agent_message', 'text': 'not a successful answer'}}))
    event = {'type': 'turn.failed'} if terminal == 'failed' else {'type': 'turn.completed'}
    result = parser.parse(json.dumps(event))
    assert result == {'type': 'oar_result', 'outcome': 'failed',
                      'error': 'codex_empty_answer' if terminal == 'empty' else 'codex_turn_failed'}
    assert 'DO_NOT_EXPOSE' not in json.dumps(result)


@pytest.mark.parametrize('mode,error', [
    ('empty', 'codex_empty_answer'), ('missing', 'codex_protocol_error'),
    ('duplicate_item', 'codex_protocol_error'), ('duplicate', 'codex_protocol_error'),
    ('unknown', 'codex_protocol_error'), ('nonzero', 'codex_runtime_failed'),
    ('runtime_error', 'codex_turn_failed'),
])
def test_sync_diagnostics_do_not_bypass_terminal_and_exit_checks(tmp_path, monkeypatch, mode, error):
    fake_codex(tmp_path, monkeypatch)
    monkeypatch.setenv('CODEX_TEST_MODE', 'diagnostics_' + mode)
    with pytest.raises(RuntimeError, match='^' + error + '$'):
        codex.run_once(CONFIG, 'request', timeout=4)


@pytest.mark.parametrize('mode,login_status', [('custom_provider', 'not_logged_in'), ('probe_unknown', 'unknown')])
def test_login_hint_does_not_block_successful_custom_provider(tmp_path, monkeypatch, mode, login_status):
    log = fake_codex(tmp_path, monkeypatch)
    monkeypatch.setenv('CODEX_TEST_MODE', mode)
    snapshot = codex.status_snapshot()
    assert snapshot['login_status'] == login_status
    assert snapshot['error'] != 'codex_auth_required'
    answer, native_id = codex.run_once({**CONFIG, 'provider': 'custom'}, 'hello')
    assert answer == '有效最终结论' and native_id == THREAD
    invocation = json.loads(log.read_text().splitlines()[0])
    assert 'model_provider="custom"' in invocation['args']
    assert 'DO_NOT_EXPOSE' not in json.dumps(snapshot)


def test_provider_is_safe_optional_and_persisted_without_changing_old_sessions(tmp_path, monkeypatch):
    import settings
    monkeypatch.setenv('NEWS_READER_APP_SETTINGS_PATH', str(tmp_path / 'settings.json'))
    fake_codex(tmp_path, monkeypatch)
    assert codex.normalize_config(CONFIG) == CONFIG
    codex.write_thread(tmp_path, CONFIG, THREAD)
    assert codex.read_thread(tmp_path, {**CONFIG, 'provider': ''}) == THREAD
    configured = {**CONFIG, 'provider': 'custom'}
    value = settings.default_app_settings()
    original_pi = dict(value['llm']['pi_chat'])
    value['agent'].update(runtime='codex', codex=configured)
    settings.save_app_settings(value)
    loaded = settings.load_app_settings()
    assert loaded['agent']['codex'] == configured
    assert loaded['llm']['pi_chat'] == original_pi
    with pytest.raises(RuntimeError, match='codex_session_invalid'):
        codex.read_thread(tmp_path, configured)
    for provider in ([], 'bad name', '--evil', 'x' * 81, 'bad"provider'):
        with pytest.raises(ValueError):
            codex.normalize_config({**CONFIG, 'provider': provider})


def test_status_missing_cli_is_not_confused_with_auth(monkeypatch):
    monkeypatch.setenv('NEWS_READER_CODEX', '/nonexistent/task407-codex')
    assert codex.status_snapshot()['error'] == 'codex_missing'
    with pytest.raises(RuntimeError, match='codex_missing'):
        codex.require_ready()


def test_status_probe_timeout_is_hint_only(tmp_path, monkeypatch):
    fake_codex(tmp_path, monkeypatch)
    original = codex.subprocess.run
    def run(args, **kwargs):
        if args[-2:] == ['login', 'status']:
            raise subprocess.TimeoutExpired(args, 5)
        return original(args, **kwargs)
    monkeypatch.setattr(codex.subprocess, 'run', run)
    assert codex.status_snapshot()['error'] == 'codex_probe_failed'
    assert codex.run_once(CONFIG, 'hello')[0] == '有效最终结论'


@pytest.mark.parametrize('mode', ['custom_provider', 'probe_unknown'])
def test_background_execution_not_gated_by_openai_login(tmp_path, monkeypatch, mode):
    import time
    app, client, item, _, _ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    fake_codex(tmp_path, monkeypatch)
    select_codex(app, monkeypatch, {**CONFIG, 'provider': 'custom'})
    monkeypatch.setenv('CODEX_TEST_MODE', mode)
    response = client.post(f"/api/news/{item['id']}/agent/jobs", json={'question': 'hello'})
    assert response.status_code == 202
    assert app.process_pending_agent_once()
    job = app.load_agent_payload(item['id'])['jobs'][-1]
    assert job['status'] == 'succeeded', job['error']
