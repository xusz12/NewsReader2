"""Unused Agent sessions follow saved settings; established conversations stay stable."""
import copy
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_api import _setup_agent_api_fixture


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    app, client, item, *_ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    settings = copy.deepcopy(app.current_runtime_settings())
    monkeypatch.setattr(app, 'current_runtime_settings', lambda: settings)
    return app, client, f"/api/news/{item['id']}/agent", settings


def configure(settings, runtime, suffix):
    settings['agent']['runtime'] = runtime
    settings['agent']['codex'].update(model=f'codex-{suffix}', profile=f'profile-{suffix}', reasoning_effort='high')
    settings['llm']['pi_chat'].update(provider=f'provider-{suffix}', model=f'pi-{suffix}')


@pytest.mark.parametrize('old_runtime,new_runtime', [('codex', 'pi'), ('pi', 'codex'), ('codex', 'codex'), ('pi', 'pi')])
@pytest.mark.parametrize('entry', ['session', 'jobs'])
def test_unused_session_adopts_current_settings_before_first_question(fixture, old_runtime, new_runtime, entry):
    app, client, path, settings = fixture
    configure(settings, old_runtime, 'old')
    original = client.post(path + '/session', json={}).json['session']
    configure(settings, new_runtime, 'saved')
    payload = {} if entry == 'session' else {'session_id': original['id'], 'question': '引用中的判断成立吗？', 'quote_text': '原文', 'quote_source': 'selection'}
    response = client.post(path + '/' + entry, json=payload)
    assert response.status_code == (200 if entry == 'session' else 202)
    session = response.json['session']
    assert session['id'] == original['id']
    assert session['provider'] == new_runtime
    assert session['model'] == f'{new_runtime}-saved'
    if new_runtime == 'codex':
        assert session['executor_config']['profile'] == 'profile-saved'
        assert session['executor_config']['reasoning_effort'] == 'high'
    else:
        assert session['executor_provider'] == 'provider-saved'
    if entry == 'jobs':
        assert response.json['job']['provider'] == new_runtime
        assert response.json['job']['model'] == f'{new_runtime}-saved'
        assert response.json['messages'][0]['quote_text'] == '原文'
    else:
        assert response.json['messages'] == response.json['jobs'] == []
    with app.agent_db_conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM agent_sessions').fetchone()[0] == 1


@pytest.mark.parametrize('runtime', ['pi', 'codex'])
def test_started_conversation_keeps_executor_and_messages_after_settings_change(fixture, runtime):
    app, client, path, settings = fixture
    configure(settings, runtime, 'original')
    first = client.post(path + '/jobs', json={'question': '首次提问'}).json
    with app.agent_db_conn() as conn:
        conn.execute("UPDATE agent_jobs SET status='succeeded' WHERE id=?", (first['job_id'],))
        conn.execute("UPDATE agent_messages SET content='原回答', status='complete' WHERE role='assistant'")
    messages = client.get(path + '/session').json['messages']
    configure(settings, 'codex' if runtime == 'pi' else 'pi', 'changed')
    prepared = client.post(path + '/session', json={}).json
    assert prepared['session']['id'] == first['session']['id']
    assert prepared['session']['provider'] == runtime
    assert prepared['session']['model'] == f'{runtime}-original'
    assert prepared['messages'] == messages
    continued = client.post(path + '/jobs', json={'session_id': first['session']['id'], 'question': '继续提问'})
    assert continued.status_code == 202
    assert continued.json['job']['provider'] == runtime
    assert continued.json['job']['model'] == f'{runtime}-original'


def test_invalid_new_settings_do_not_silently_use_an_unused_old_executor(fixture):
    _, client, path, settings = fixture
    configure(settings, 'codex', 'old')
    original = client.post(path + '/session', json={}).json['session']
    settings['agent']['runtime'] = 'pi'
    settings['llm']['pi_chat'] = {'provider': '', 'model': ''}
    for entry, payload in [('session', {}), ('jobs', {'session_id': original['id'], 'question': '首次提问'})]:
        response = client.post(path + '/' + entry, json=payload)
        assert response.status_code == 400
        assert response.json['error'] == 'pi_model_not_selected'
    assert client.get(path + '/session').json['jobs'] == []


def test_concurrent_preparation_reuses_one_empty_session(fixture):
    app, _, path, settings = fixture
    configure(settings, 'codex', 'saved')
    def prepare(_):
        with app.app.test_client() as client:
            response = client.post(path + '/session', json={})
            assert response.status_code == 200
            return response.json['session']['id']
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(prepare, range(4)))
    assert len(set(ids)) == 1
