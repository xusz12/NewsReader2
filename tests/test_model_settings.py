import json
import os
from pathlib import Path
import subprocess
import threading
import time
import pytest
import codex_client
import connection_tests as checks
import model_catalogs as catalogs
import settings

CONFIG={'model':'m','profile':'','sandbox':'read-only'}

@pytest.fixture(autouse=True)
def isolate_catalog_cache(tmp_path, monkeypatch):
    monkeypatch.setenv('NEWS_READER_APP_SETTINGS_PATH', str(tmp_path / 'settings.json'))

def test_empty_pi_default_and_legacy_config(monkeypatch,tmp_path):
    p=tmp_path/'settings.json';monkeypatch.setenv('NEWS_READER_APP_SETTINGS_PATH',str(p))
    assert settings.load_app_settings()['llm']['pi_chat']=={'provider':'','model':''}
    p.write_text(json.dumps({'llm':{'pi_chat':{'provider':'ollama','model':'old'}}}))
    assert settings.load_app_settings()['llm']['pi_chat']=={'provider':'ollama','model':'old'}
    p.write_text(json.dumps({'llm':{'pi_chat':{'provider':'','model':''}}}))
    assert settings.load_app_settings()['llm']['pi_chat']=={'provider':'','model':''}

@pytest.mark.parametrize('thread',['','0199a213-81c0-7800-8aa1-bbab2a035a53'])
def test_reasoning_actual_command_legacy(thread,monkeypatch):
    monkeypatch.setattr(codex_client,'executable',lambda:'codex')
    assert codex_client.normalize_config(CONFIG)==CONFIG
    args=codex_client.command({**CONFIG,'reasoning_effort':'high'},thread_id=thread,ephemeral=not thread)
    assert 'model_reasoning_effort="high"' in args
    assert 'reasoning_effort' not in codex_client.normalize_config({**CONFIG,'reasoning_effort':''})

@pytest.mark.parametrize('value',[[],{},False,'guess'])
def test_invalid_reasoning(value):
    with pytest.raises(ValueError):codex_client.normalize_config({**CONFIG,'reasoning_effort':value})

def test_catalog_pagination_and_native_default(monkeypatch):
    class Fake:
        calls=[]
        def __init__(self,args): self.args=args
        def send(self,obj):pass
        def close(self): self.calls.append(('close',{}))
        def call(self,method,params):
            self.calls.append((method,params))
            if method=='initialize':return {}
            if params.get('cursor')=='next':return {'data':[{'model':'other'}],'nextCursor':None}
            return {'data':[{'id':'m','isDefault':True,'supportedReasoningEfforts':[{'reasoningEffort':'high'}]}],'nextCursor':'next'}
    monkeypatch.setattr(catalogs,'Rpc',Fake);monkeypatch.setattr(codex_client,'executable',lambda:'codex')
    result=catalogs.codex_catalog(CONFIG)
    assert result['resolved_default_model']==''
    assert [x['value'] for x in result['options']]==['m','other']
    assert result['options'][0]['reasoning_efforts']==['high']
    assert any(p.get('cursor')=='next' for m,p in Fake.calls)
    assert Fake.calls[-1][0]=='close'

def test_catalog_error_redacted(monkeypatch):
    def fail(c):raise RuntimeError('api-key=SECRET private url')
    monkeypatch.setattr(catalogs,'codex_catalog',fail)
    result=catalogs.safe_catalog(CONFIG)
    assert result['options']==[] and result['error'] and 'SECRET' not in json.dumps(result)

def test_unknown_model_and_invalid_effort_rejected(monkeypatch):
    monkeypatch.setattr(catalogs,'safe_catalog',lambda c:{'options':[{'value':'m','reasoning_efforts':['high']}]})
    assert catalogs.validate_effort({**CONFIG,'reasoning_effort':'high'})['reasoning_effort']=='high'
    with pytest.raises(ValueError):catalogs.validate_effort({**CONFIG,'reasoning_effort':'max'})
    with pytest.raises(ValueError):catalogs.validate_effort({**CONFIG,'model':'manual','reasoning_effort':'high'})
    assert catalogs.validate_effort({**CONFIG,'model':'manual'})['model']=='manual'

def test_codex_hi_independent_selected_config(monkeypatch):
    calls=[]
    def run(config,prompt,**kwargs):
        calls.append((config,prompt,kwargs));return ('Hello', 'unused-thread')
    monkeypatch.setattr(codex_client,'run_once',run)
    chosen={**CONFIG,'provider':'custom','reasoning_effort':'high'}
    result=checks.test_connection({'runtime':'codex','codex':chosen})
    assert result['status']=='success'
    assert result['message']=='本次模型请求成功。'
    assert calls==[(chosen,'hi',{'timeout':checks.TIMEOUT,'ephemeral':True,'finish_on_completion':True})]

@pytest.mark.parametrize('mode,category', [('auth','credentials'),('empty','invalid_answer'),('missing','invalid_answer'),('nonzero','unknown'),('hang','timeout')])
def test_codex_connection_failure_real_child(monkeypatch,tmp_path,mode,category):
    from test_codex_client import fake_codex
    log=fake_codex(tmp_path,monkeypatch)
    monkeypatch.setenv('CODEX_TEST_MODE',mode)
    monkeypatch.setattr(checks,'TIMEOUT',.15 if mode=='hang' else 3)
    result=checks.test_connection({'runtime':'codex','codex':CONFIG})
    assert result['status']=='failed' and result['category']==category
    requests=[json.loads(line) for line in log.read_text().splitlines()]
    assert len(requests)==1 and requests[0]['prompt']=='hi'
    assert '--ephemeral' in requests[0]['args'] and 'resume' not in requests[0]['args']
    assert '--sandbox' in requests[0]['args'] and 'read-only' in requests[0]['args']

@pytest.mark.parametrize('text,category',[('401 unauthorized','credentials'),('ECONNRESET','connection'),('capacity exceeded','capacity'),('model not supported','model'),('generic failure','unknown')])
def test_classifications(text,category):assert checks.classify(text)==category

def fake_pi(tmp_path,monkeypatch,mode='ok'):
    script=tmp_path/'pi';log=tmp_path/'args.json'
    script.write_text('''#!/usr/bin/env python3
import json,sys,os,time
if '--help' in sys.argv:
 print('--no-tools --no-mcp --no-extensions --no-session --no-skills --no-prompt-templates');sys.exit()
json.dump(sys.argv[1:],open(os.environ['TEST_ARGS'],'w'))
sys.stdin.read()
mode=os.environ['TEST_MODE']
if mode=='timeout':time.sleep(5)
if mode=='tool':print(json.dumps({'type':'tool_execution_start'}))
print(json.dumps({'type':'message_end','message':{'role':'assistant','content':[{'type':'text','text':'NEWSREADER_CONNECTION_OK' if mode!='empty' else ''}],'stopReason':'stop'}}))
if mode!='partial':print(json.dumps({'type':'agent_end'}))
sys.exit(1 if mode=='nonzero' else 0)
''');script.chmod(0o700)
    monkeypatch.setenv('NEWS_READER_PI',str(script));monkeypatch.setenv('TEST_MODE',mode);monkeypatch.setenv('TEST_ARGS',str(log))
    return log

@pytest.mark.parametrize('mode,expected',[('ok','success'),('empty','failed'),('partial','failed'),('nonzero','failed'),('tool','failed'),('timeout','failed')])
def test_pi_no_tools_real_child_and_failures(tmp_path,monkeypatch,mode,expected):
    log=fake_pi(tmp_path,monkeypatch,mode);monkeypatch.setattr(checks,'TIMEOUT',.15 if mode=='timeout' else 3)
    result=checks.test_connection({'runtime':'pi','pi':{'provider':'p','model':'m'}})
    assert result['status']==expected
    args=json.loads(log.read_text())
    assert all(flag in args for flag in ['--no-tools','--no-mcp','--no-extensions','--no-session'])
    assert 'news' not in ' '.join(args).lower()

def test_duplicate_and_cleanup_after_error(monkeypatch):
    checks.LOCK.acquire()
    try:
        with pytest.raises(checks.Busy):checks.test_connection({'runtime':'pi'})
    finally:checks.LOCK.release()
    with pytest.raises(ValueError):checks.test_connection({'runtime':'pi','pi':{}})
    assert not checks.LOCK.locked()

def test_new_settings_api_does_not_write(monkeypatch,tmp_path):
    import app
    monkeypatch.setenv('NEWS_READER_APP_SETTINGS_PATH',str(tmp_path/'settings.json'))
    monkeypatch.setattr(catalogs,'safe_catalog',lambda c:{'options':[{'value':'m','reasoning_efforts':['high']}],'source':'test','error':''})
    client=app.app.test_client()
    response=client.post('/api/settings/models',json={'runtime':'codex','codex':CONFIG})
    assert response.get_json()['catalog']['options'][0]['value']=='m'
    monkeypatch.setattr(codex_client,'run_once',lambda *a,**kw:('hello','unused-thread'))
    response=client.post('/api/settings/connection-test',json={'runtime':'codex','codex':CONFIG})
    assert response.get_json()['status']=='success'
    assert not (tmp_path/'settings.json').exists()
    assert client.post('/api/settings/models',json=[]).status_code==400
    assert client.post('/api/settings/connection-test',json={'runtime':'bad'}).status_code==400

def test_new_codex_effort_reaches_summary_and_saved_session(monkeypatch,tmp_path):
    import app,news_summaries
    from test_api import _setup_agent_api_fixture
    app,client,item,*_=_setup_agent_api_fixture(tmp_path,monkeypatch)
    value=app.default_app_settings()
    value['agent'].update(runtime='codex',codex={**CONFIG,'reasoning_effort':'high'})
    monkeypatch.setattr(app,'load_app_settings',lambda:value)
    summary=news_summaries.capture_config(value)
    assert summary['codex']['reasoning_effort']=='high'
    created=client.post(f"/api/news/{item['id']}/agent/session",json={})
    assert created.status_code==200
    sid=created.get_json()['session']['id']
    value['agent']['codex']['reasoning_effort']='low'
    with app.agent_db_conn() as conn:
        record=conn.execute('SELECT executor_config FROM agent_sessions WHERE id=?',(sid,)).fetchone()
    assert json.loads(record[0])['reasoning_effort']=='high'


def test_pi_sync_resume_and_archive_keep_saved_selection(monkeypatch,tmp_path):
    from test_api import _setup_agent_api_fixture,_install_fake_oar
    app,client,item,*_=_setup_agent_api_fixture(tmp_path,monkeypatch)
    log=_install_fake_oar(tmp_path,monkeypatch)
    value=app.default_app_settings();value['llm']['pi_chat']={'provider':'old-provider','model':'old-model'}
    monkeypatch.setattr(app,'load_app_settings',lambda:value)
    first=client.post(f"/api/news/{item['id']}/chat",json={'question':'first'}).get_json()
    assert first['ok'] and first['model']=='old-model'
    value['llm']['pi_chat']={'provider':'','model':''};value['agent']['runtime']='codex'
    second=client.post(f"/api/news/{item['id']}/chat",json={'question':'next','session_id':first['session_id']}).get_json()
    assert second['ok'] and second['provider']=='pi' and second['model']=='old-model'
    archive=client.post(f"/api/news/{item['id']}/chat/archive",json={'session_id':first['session_id'],
      'messages':[{'role':'user','content':'q'},{'role':'assistant','content':'a'}]})
    assert archive.status_code==200 and archive.get_json()['provider']=='pi'
    requests=[json.loads(line) for line in log.read_text().splitlines()]
    assert len(requests)==3 and all(x['provider']=='old-provider' and x['model']=='old-model' for x in requests)

@pytest.mark.parametrize('mode,status,category',[('completed-linger','success',''),('delayed-completed-linger','success',''),('partial-linger','failed','timeout'),('failed-linger','failed','credentials'),('nonzero-linger','failed','unknown'),('malformed-tail','failed','invalid_answer')])
def test_codex_connection_does_not_wait_for_teardown_but_reaps_group(tmp_path,monkeypatch,mode,status,category):
    script=tmp_path/'codex-linger';pids=tmp_path/'pids.json'
    script.write_text('''#!/usr/bin/env python3
import sys,json,os,subprocess,time
if '--help' in sys.argv:
 print('exec --json --sandbox --cd resume');sys.exit()
assert sys.stdin.read()=='hi'
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
json.dump([os.getpid(),child.pid],open(os.environ['LINGER_PIDS'],'w'))
def emit(x):print(json.dumps(x),flush=True)
emit({'type':'thread.started','thread_id':'0199a213-81c0-7800-8aa1-bbab2a035a53'})
emit({'type':'turn.started'})
mode=os.environ['LINGER_MODE']
if mode=='delayed-completed-linger':time.sleep(.35)
emit({'type':'item.completed','item':{'id':'a','type':'agent_message','text':'hello'}})
if mode=='failed-linger':emit({'type':'turn.failed','error':{'message':'401 unauthorized'}})
elif mode!='partial-linger':emit({'type':'turn.completed','usage':{}})
if mode=='nonzero-linger':sys.exit(1)
if mode=='malformed-tail':sys.stdout.write('{');sys.stdout.flush()
time.sleep(30)
''');script.chmod(0o700)
    monkeypatch.setenv('NEWS_READER_CODEX',str(script));monkeypatch.setenv('LINGER_MODE',mode);monkeypatch.setenv('LINGER_PIDS',str(pids));monkeypatch.setattr(checks,'TIMEOUT',.8)
    started=time.monotonic();result=checks.test_connection({'runtime':'codex','codex':CONFIG})
    assert result['status']==status and result['category']==category
    # Existing process-group reaper can use up to 3 seconds after the turn.
    assert time.monotonic()-started<5
    for pid in json.loads(pids.read_text()):
        state=subprocess.run(['ps','-o','stat=','-p',str(pid)],capture_output=True,text=True).stdout.strip()
        assert not state or state.startswith('Z'),(pid,state)

@pytest.mark.parametrize('text,category', [
 ('HTTP 429', 'unknown'), ('403', 'unknown'),
 ('discussion of capacity planning', 'unknown'), ('timed out', 'timeout'),
 ('{"error":{"code":"insufficient_quota"}}', 'quota'),
 ('{"error":{"type":"rate_limit_error"}}', 'rate'),
 ('{"error":{"code":"context_length_exceeded"}}', 'input'),
])
def test_error_classification_requires_explicit_evidence(text, category):
    assert checks.classify(text) == category
