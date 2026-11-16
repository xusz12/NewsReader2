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

CONFIG={'model':'m','sandbox':'read-only'}

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


def test_feature_models_migrate_once_and_do_not_share_mutable_settings(monkeypatch, tmp_path):
    import app
    path = tmp_path / 'settings.json'
    legacy = settings.default_app_settings()
    legacy['agent'].update(runtime='codex', codex={**CONFIG, 'profile':'old-profile'})
    legacy['llm']['pi_chat'] = {'provider':'old-provider', 'model':'old-pi'}
    path.write_text(json.dumps(legacy))
    first = settings.load_app_settings()
    for feature in settings.MODEL_FEATURES:
        assert first['feature_models'][feature] == settings.feature_model_config(first, 'chat')
    # Saving a changed Chat choice freezes the previously inherited values.
    monkeypatch.setattr(app, 'load_app_settings', settings.load_app_settings)
    saved = app.validate_runtime_settings({'agent':{'runtime':'pi'}, 'llm':{**legacy['llm'],'pi_chat':{'provider':'new','model':'new'}}})
    settings.save_app_settings(saved)
    loaded = settings.load_app_settings()
    assert loaded['agent']['runtime'] == 'pi'
    assert loaded['feature_models']['topics']['runtime'] == 'codex'
    assert loaded['feature_models']['reports']['pi']['model'] == 'old-pi'
    copy = settings.feature_model_config(loaded, 'topics')
    copy['codex']['model'] = 'changed'
    assert loaded['feature_models']['topics']['codex']['model'] == 'm'
    loaded['feature_models']['topics']['pi']['model'] = 'changed'
    assert loaded['feature_models']['summaries']['pi']['model'] == 'old-pi'


def test_feature_settings_reach_each_executor_and_preserve_chat(monkeypatch, tmp_path):
    monkeypatch.setattr(catalogs, 'safe_catalog', lambda _: {'options': [
        {'value': 'summary-model', 'reasoning_efforts': []}]})
    import news_summaries, report_model
    from test_api import _setup_agent_api_fixture
    app, client, item, *_ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    value = settings.default_app_settings()
    value['agent'].update(runtime='codex', codex={**CONFIG, 'model':'chat-model'})
    value['llm']['pi_chat'] = {'provider':'chat-provider', 'model':'chat-pi'}
    monkeypatch.setattr(app, 'load_app_settings', lambda:value)
    monkeypatch.setattr(news_summaries, 'native_config_stamp', lambda _: [])
    chosen = {
        'topics': {'runtime':'pi', 'pi':{'provider':'topic-provider','model':'topic-model'}},
        'summaries': {'runtime':'codex', 'codex':{**CONFIG,'model':'summary-model'}},
        'reports': {'runtime':'pi', 'pi':{'provider':'report-provider','model':'report-model'}},
    }
    normalized = app.validate_runtime_settings({'llm':value['llm'], 'feature_models':chosen})
    value.update(normalized)
    snapshot = client.get('/api/settings').get_json()
    assert set(snapshot['feature_model_catalogs']) == set(settings.MODEL_FEATURES)
    assert snapshot['feature_models'] == value['feature_models']
    topic = news_summaries.capture_config(value, 'topics')
    assert (topic['runtime'],topic['pi_provider'],topic['pi_model']) == ('pi','topic-provider','topic-model')
    summary = news_summaries.capture_config(value)
    assert summary['runtime'] == 'codex' and summary['codex']['model'] == 'summary-model'
    assert report_model.configuration(value, resolve=False) == {'runtime':'pi','provider':'report-provider','model':'report-model'}
    response = client.post(f"/api/news/{item['id']}/agent/session", json={})
    assert response.status_code == 200
    with app.agent_db_conn() as conn:
        row = conn.execute('SELECT executor_config FROM agent_sessions WHERE id=?', (response.get_json()['session']['id'],)).fetchone()
    assert json.loads(row[0])['model'] == 'chat-model'
    # A partial edit and an older client both preserve unrelated feature choices.
    updated = app.validate_runtime_settings({'llm':value['llm'], 'feature_models':{'topics':{'runtime':'codex'}}})
    assert updated['feature_models']['reports'] == value['feature_models']['reports']
    assert updated['feature_models']['topics']['pi'] == chosen['topics']['pi']
    assert updated['agent'] == value['agent']
    assert app.validate_runtime_settings({'llm':value['llm'],'agent':{'runtime':'pi'}})['feature_models'] == value['feature_models']
    # Report Codex settings must also come from its own feature.
    value['feature_models']['reports'].update(runtime='codex', codex={**CONFIG,'model':'report-codex'})
    assert report_model.configuration(value, resolve=False)['codex']['model'] == 'report-codex'


@pytest.mark.parametrize('invalid', [None, [], {'unknown':{}}, {'topics':None}, {'reports':{'runtime':'unknown'}},
    {'summaries':{'pi':[]}}, {'reports':{'pi':{'model':False}}}, {'topics':{'pi':{'model':'bad\nvalue'}}}])
def test_invalid_feature_models_rejected(invalid):
    with pytest.raises(ValueError, match='invalid_feature_models'):
        settings.normalize_feature_models(invalid, settings.default_app_settings())


def test_feature_effort_validated_for_selected_model(monkeypatch):
    import app
    value = settings.default_app_settings()
    monkeypatch.setattr(app, 'load_app_settings', lambda:value)
    calls = []
    def validate(config):
        calls.append(config)
        raise ValueError('invalid_codex_reasoning_effort')
    monkeypatch.setattr(app.model_catalogs, 'validate_effort', validate)
    chosen = {**CONFIG, 'reasoning_effort':'high'}
    with pytest.raises(ValueError, match='reasoning_effort'):
        app.validate_runtime_settings({'llm':value['llm'], 'feature_models':{'reports':{'codex':chosen}}})
    assert calls == [chosen]


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
    with pytest.raises(ValueError, match='codex_model_not_in_catalog'):
        catalogs.validate_effort({**CONFIG,'model':'manual'})


@pytest.mark.parametrize('field', ['profile', 'provider'])
@pytest.mark.parametrize('feature', ['chat', 'reports', 'reports_periodic', 'summaries', 'topics'])
def test_retired_codex_overrides_rejected_by_settings_and_probe_apis(tmp_path, monkeypatch, field, feature):
    from test_api import _setup_agent_api_fixture
    app, client, *_ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    value = settings.default_app_settings()
    monkeypatch.setattr(app, 'load_app_settings', lambda: value)
    chosen = {'model': '', field: 'retired'}
    payload = {'llm': value['llm']}
    if feature == 'chat':
        payload['agent'] = {'runtime': 'codex', 'codex': chosen}
    else:
        payload['feature_models'] = {feature: {'runtime': 'codex', 'codex': chosen}}
    response = client.put('/api/settings', json=payload)
    assert response.status_code == 400
    assert response.json['error'] == 'codex_advanced_config_removed'
    for endpoint in ('/api/settings/models', '/api/settings/connection-test'):
        response = client.post(endpoint, json={'runtime': 'codex', 'codex': chosen})
        assert response.status_code == 400
        assert response.json['error'] == 'codex_advanced_config_removed'


def test_settings_and_probe_reject_custom_model_even_without_effort(tmp_path, monkeypatch):
    from test_api import _setup_agent_api_fixture
    app, client, *_ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    value = settings.default_app_settings()
    monkeypatch.setattr(app, 'load_app_settings', lambda: value)
    monkeypatch.setattr(catalogs, 'safe_catalog', lambda _: {'options': [
        {'value': 'known', 'reasoning_efforts': ['high']}]})
    chosen = {'runtime': 'codex', 'codex': {'model': 'manual'}}
    response = client.put('/api/settings', json={'llm': value['llm'], 'agent': chosen})
    assert response.status_code == 400 and response.json['error'] == 'codex_model_not_in_catalog'
    response = client.post('/api/settings/connection-test', json=chosen)
    assert response.status_code == 400 and response.json['error'] == 'codex_model_not_in_catalog'
    response = client.put('/api/settings', json={'llm': value['llm'],
        'agent': {'runtime': 'codex', 'codex': {'model': 'known'}}})
    assert response.status_code == 200


def test_saved_feature_preferences_remove_retired_fields_without_resetting_other_features(tmp_path):
    value = settings.default_app_settings()
    value['feature_models'] = settings.normalize_feature_models({}, value)
    for feature in settings.MODEL_FEATURES:
        value['feature_models'][feature]['codex'] = {'model': feature, 'provider': 'old-service', 'profile': 'old-profile'}
    settings.save_app_settings(value)
    loaded = settings.load_app_settings()
    for feature in settings.MODEL_FEATURES:
        assert loaded['feature_models'][feature]['codex'] == {'model': feature, 'sandbox': 'read-only'}

def test_codex_connection_independent_selected_config(monkeypatch):
    calls = []
    def run(config, prompt, **kwargs):
        calls.append((config, prompt, kwargs))
        return "NEWSREADER_CONNECTION_OK"
    monkeypatch.setattr(checks.runtime_tasks, 'run', run)
    chosen = {**CONFIG, 'reasoning_effort': 'high'}
    result = checks.test_connection({'runtime': 'codex', 'codex': chosen})
    assert result['status'] == 'success'
    assert calls == [({'runtime': 'codex', 'codex': chosen}, 'Reply with exactly NEWSREADER_CONNECTION_OK.',
                     {'task': 'connection_test', 'timeout': checks.TIMEOUT})]


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
    monkeypatch.setattr(checks.runtime_tasks,'run',lambda *a,**kw:'NEWSREADER_CONNECTION_OK')
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


@pytest.mark.parametrize('code,category', [
 ('oar_overloaded', 'capacity'), ('oar_model_unavailable', 'model'), ('oar_input_too_large', 'input'),
 ('HTTP 429', 'unknown'), ('discussion of capacity planning', 'unknown'),
 ('{"error":{"code":"insufficient_quota"}}', 'unknown'),
])
def test_error_classification_requires_structured_oar_evidence(monkeypatch, code, category):
    def fail(*args, **kwargs):
        raise RuntimeError(code)
    monkeypatch.setattr(checks.runtime_tasks, 'run', fail)
    result = checks.test_connection({'runtime': 'codex', 'codex': CONFIG})
    assert result['category'] == category and result['status'] == 'failed'
    assert code not in result['message']
