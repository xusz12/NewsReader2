"""Changing models keeps the transcript and respects active tasks and explicit overrides."""
import copy
import json
import pytest
from test_api import _setup_agent_api_fixture


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    app, client, item, *_ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    settings = copy.deepcopy(app.current_runtime_settings())
    monkeypatch.setattr(app, 'current_runtime_settings', lambda:settings)
    monkeypatch.setattr(app.report_model, 'catalog', lambda *args: {'options':[{'value':'chosen','provider':'test','reasoning_efforts':['low','high']}], 'error':''})
    monkeypatch.setattr(app.report_model, 'pi_metadata', lambda *args: {'reasoning_efforts':['low','high']})
    return app, client, '/api/news/' + item['id'] + '/agent', settings


@pytest.mark.parametrize('runtime', ['codex','pi'])
def test_change_model_keeps_transcript_and_next_job_uses_choice(fixture, runtime):
    app, client, base, settings = fixture
    first = client.post(base + '/jobs', json={'question':'第一轮问题'}).json
    selection = {'runtime':runtime, 'model':'chosen','reasoning_effort':'high', **({'provider':'test'} if runtime=='pi' else {})}
    request = {'session_id':first['session']['id'], 'selection':selection}
    assert client.patch(base+'/session/model', json=request).status_code == 409
    with app.agent_db_conn() as conn:
        conn.execute("UPDATE agent_jobs SET status='succeeded'")
        conn.execute("UPDATE agent_messages SET content='已有回答' WHERE role='assistant'")
    before = client.get(base+'/session').json['messages']
    response = client.patch(base+'/session/model', json=request)
    assert response.status_code == 200
    session = response.json['session']
    assert session['id'] == first['session']['id']
    assert response.json['messages'] == before
    assert session['reasoning_effort'] == 'high'
    assert settings['llm']['pi_chat']['model'] != 'chosen'
    next_job = client.post(base + '/jobs', json={'session_id':session['id'], 'question':'接续问题'}).json
    assert next_job['job']['provider'] == runtime
    assert next_job['job']['model'] == 'chosen'
    assert next_job['messages'][0]['content'] == '第一轮问题'
    with app.agent_db_conn() as conn:
        saved = dict(conn.execute('SELECT * FROM agent_sessions WHERE id=?', (session['id'],)).fetchone())
    assert json.loads(saved['executor_config'])['reasoning_effort'] == 'high'


def test_unused_manual_choice_persists_and_invalid_or_stale_updates_do_not_apply(fixture):
    app, client, base, settings = fixture
    selection = {'runtime':'codex','model':'chosen','reasoning_effort':'low'}
    result = client.patch(base+'/session/model', json={'selection':selection})
    assert result.status_code == 200
    session = result.json['session']
    # Saved defaults change, but the panel's explicit per-session choice stays put.
    settings['agent']['runtime'] = 'pi'
    assert client.post(base+'/session', json={}).json['session']['model'] == 'chosen'
    assert client.patch(base+'/session/model', json={'selection':selection}).status_code == 409
    response = client.patch(base+'/session/model', json={'session_id':session['id'], 'selection':{**selection,'reasoning_effort':'ultra'}})
    assert response.status_code == 400
    assert client.get(base+'/session').json['session']['reasoning_effort'] == 'low'


def test_explicit_model_preserves_choice_but_refreshes_unused_context(fixture):
    app, client, base, settings = fixture
    selected = client.patch(base+'/session/model', json={'selection':{'runtime':'codex','model':'chosen','reasoning_effort':'low'}}).json['session']
    with app.db_conn() as conn:
        conn.execute('UPDATE article_details SET content=? WHERE url=?',('首次发送前已更新的全文',selected['url']))
    settings['agent']['codex']['model'] = 'another-default'
    started = client.post(base+'/jobs', json={'session_id':selected['id'],'question':'根据最新全文解释'})
    assert started.status_code == 202
    assert started.json['job']['model'] == 'chosen'
    with app.agent_db_conn() as conn:
        row = conn.execute('SELECT context_json FROM agent_sessions WHERE id=?',(selected['id'],)).fetchone()
    assert json.loads(row[0])['content'] == '首次发送前已更新的全文'


@pytest.mark.parametrize('default_runtime', ['codex', 'pi'])
@pytest.mark.parametrize('temporary_runtime', ['codex', 'pi'])
@pytest.mark.parametrize('other_started', [False, True])
def test_temporary_model_is_scoped_to_one_session(fixture, default_runtime, temporary_runtime, other_started):
    app, client, base, settings = fixture
    settings['agent']['runtime'] = default_runtime
    settings['agent']['codex'].update(model='default-codex', reasoning_effort='low', profile='saved-profile')
    settings['llm']['pi_chat'].update(provider='saved-provider', model='default-pi', reasoning_effort='low')
    saved_settings = copy.deepcopy(settings)
    with app.db_conn() as conn:
        item = dict(conn.execute('SELECT * FROM items LIMIT 1').fetchone())
        item.update(id='other-news', url=item['url'] + '/other', source_file=item['source_file'] + '.other')
        conn.execute(f"INSERT INTO items ({','.join(item)}) VALUES ({','.join('?' for _ in item)})", tuple(item.values()))
    other = '/api/news/other-news/agent'
    existing = client.post(other + '/jobs', json={'question': '其他新闻的对话'}).json if other_started else None

    original = client.post(base + '/session', json={}).json['session']
    selection = {'runtime': temporary_runtime, 'model': 'chosen', 'reasoning_effort': 'high',
                 **({'provider': 'test'} if temporary_runtime == 'pi' else {})}
    response = client.patch(base + '/session/model', json={'session_id': original['id'], 'selection': selection})
    assert response.status_code == 200
    assert settings == saved_settings
    # Reopening the same conversation retains its own temporary choice.
    reopened = client.post(base + '/session', json={}).json['session']
    assert reopened['id'] == original['id']
    assert (reopened['provider'], reopened['model'], reopened['reasoning_effort']) == (temporary_runtime, 'chosen', 'high')

    # Neither an existing conversation nor a first conversation on other news inherits it.
    choices = client.get(other + '/model-options').json['choices']
    assert choices['runtime'] == default_runtime
    assert choices[default_runtime]['model'] == 'default-' + default_runtime
    other_session = client.post(other + '/session', json={}).json['session']
    assert (other_session['provider'], other_session['model'], other_session['reasoning_effort']) == (default_runtime, 'default-' + default_runtime, 'low')
    assert not other_session['model_override']
    if existing:
        assert other_session['id'] == existing['session']['id']
        assert client.get(other + '/session').json['messages'] == existing['messages']

    # New conversation reads the latest saved defaults, not the previous override.
    default_config = settings['agent']['codex'] if default_runtime == 'codex' else settings['llm']['pi_chat']
    default_config['model'] = 'latest-default'
    saved_settings = copy.deepcopy(settings)
    reset = client.post(base + '/session', json={'new_session': True})
    assert reset.status_code == 200
    fresh = reset.json['session']
    assert fresh['id'] != original['id']
    assert (fresh['provider'], fresh['model'], fresh['reasoning_effort']) == (default_runtime, 'latest-default', 'low')
    assert not fresh['model_override']
    assert reset.json['messages'] == reset.json['jobs'] == []
    # A delayed old picker request cannot apply its choice to the new session.
    assert client.patch(base + '/session/model', json={'session_id': original['id'], 'selection': selection}).status_code == 409
    started = client.post(base + '/jobs', json={'session_id': fresh['id'], 'question': '新会话问题'})
    assert started.status_code == 202
    assert (started.json['job']['provider'], started.json['job']['model']) == (default_runtime, 'latest-default')
    assert settings == saved_settings
