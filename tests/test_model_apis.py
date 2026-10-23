"""Provider contracts, persistence and credentials; no real model calls."""
import json
from urllib.error import HTTPError

import pytest
import model_apis
from test_api import _setup_agent_api_fixture


@pytest.fixture
def api_fixture(tmp_path, monkeypatch):
    app, client, *_ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    for name in ("DEEPSEEK_API_KEY", "TYPESAFE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    secrets = {}
    monkeypatch.setattr(app, "has_secret", lambda name: name in secrets)
    monkeypatch.setattr(app, "read_secret", lambda name: secrets.get(name))
    monkeypatch.setattr(app, "write_secret", lambda name, value: secrets.__setitem__(name, value))
    monkeypatch.setattr(app, "delete_secret", lambda name: secrets.pop(name, None))
    monkeypatch.setattr(app, "pi_chat_settings_snapshot", lambda *args: {"service": {}, "catalog": {}})
    monkeypatch.setattr(app, "urlopen", lambda *a, **kw: pytest.fail("Unexpected network call"))
    return app, client, secrets


class Reply:
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def read(self): return json.dumps(self.payload).encode()


def test_jev_settings_roundtrip_and_legacy_writes_preserve_config(api_fixture, tmp_path):
    app, client, _ = api_fixture
    initial = client.get('/api/settings').json
    assert initial['api_models'] == {'jev': {'model': '', 'concurrency': 8, 'unread_only': False}}
    assert [p['id'] for p in initial['api_providers']] == ['deepseek', 'jev']
    assert initial['api_status']['jev']['configured'] is False
    assert initial['model_catalogs']['jev']['options'] == []
    initial['api_models']['jev']['model'] = ' jev-custom '
    saved = client.put('/api/settings', json={'llm': initial['llm'], 'api_models': initial['api_models']})
    assert saved.status_code == 200
    assert saved.json['api_models']['jev']['model'] == 'jev-custom'
    assert app.load_app_settings()['api_models']['jev']['model'] == 'jev-custom'
    # Clients predating API management must not clear the new configuration.
    client.put('/api/settings', json={'llm': initial['llm']})
    assert client.get('/api/settings').json['api_models']['jev']['model'] == 'jev-custom'
    assert app.current_runtime_settings()['llm']['translation'] == initial['llm']['translation']


def test_concurrency_persists_and_applies_live_without_starting_a_stopped_service(api_fixture):
    app, client, _ = api_fixture
    llm = app.load_app_settings()['llm']
    saved = client.put('/api/settings', json={'llm': llm, 'api_models': {'jev': {'model': 'jev-test', 'concurrency': 16}}})
    assert saved.status_code == 200
    assert app.load_app_settings()['api_models']['jev']['concurrency'] == 16
    assert app.CLASSIFICATION_SERVICE.gate.snapshot()['configured_concurrency'] == 16
    assert app.CLASSIFICATION_SERVICE.gate.snapshot()['effective_concurrency'] == 16
    assert app.CLASSIFICATION_SERVICE.threads == []
    # A model-only write from an older client preserves the new setting.
    saved = client.put('/api/settings', json={'llm': llm, 'api_models': {'jev': {'model': 'jev-legacy'}}})
    assert saved.status_code == 200
    assert saved.json['api_models']['jev']['concurrency'] == 16
    assert client.get('/api/news/classification').json['throttle']['configured_concurrency'] == 16


def test_unread_only_persists_applies_live_and_survives_legacy_writes(api_fixture):
    app, client, _ = api_fixture
    llm = app.load_app_settings()['llm']
    saved = client.put('/api/settings', json={'llm': llm, 'api_models': {'jev': {'unread_only': True}}})
    assert saved.status_code == 200
    assert app.load_app_settings()['api_models']['jev']['unread_only'] is True
    assert app.CLASSIFICATION_SERVICE.unread_only is True
    assert app.CLASSIFICATION_SERVICE.threads == []
    saved = client.put('/api/settings', json={'llm': llm, 'api_models': {'jev': {'model': 'jev-legacy'}}})
    assert saved.status_code == 200 and saved.json['api_models']['jev']['unread_only'] is True
    assert client.get('/api/news/classification').json['unread_only'] is True
    saved = client.put('/api/settings', json={'llm': llm, 'api_models': {'jev': {'unread_only': False}}})
    assert saved.status_code == 200 and app.CLASSIFICATION_SERVICE.unread_only is False


@pytest.mark.parametrize('value', [None, 0, 1, 'true', 'false', [], {}])
def test_invalid_unread_only_is_not_saved(api_fixture, value):
    app, client, _ = api_fixture
    before = app.load_app_settings()
    result = client.put('/api/settings', json={'llm': before['llm'], 'api_models': {'jev': {'unread_only': value}}})
    assert result.status_code == 400 and result.json['error'] == 'invalid_classification_unread_only'
    assert app.load_app_settings() == before


@pytest.mark.parametrize('concurrency', [0, 33, -1, True, '8', 1.5, None])
def test_invalid_classification_concurrency_is_not_saved(api_fixture, concurrency):
    app, client, _ = api_fixture
    before = app.load_app_settings()
    result = client.put('/api/settings', json={'llm': before['llm'], 'api_models': {'jev': {'concurrency': concurrency}}})
    assert result.status_code == 400
    assert result.json['error'] == 'invalid_classification_concurrency'
    assert app.load_app_settings() == before


@pytest.mark.parametrize('value', [None, [], {'jev': []}, {'jev': {'model': 42}}, {'jev': {'model': 'a' * 121}}, {'jev': {'model': 'bad\nname'}}])
def test_invalid_config_does_not_persist(api_fixture, value):
    app, client, _ = api_fixture
    initial = app.load_app_settings()
    result = client.put('/api/settings', json={'llm': initial['llm'], 'api_models': value})
    assert result.status_code == 400
    assert app.load_app_settings() == initial


def test_jev_key_isolated_masked_environment_precedence(api_fixture, monkeypatch):
    app, client, secrets = api_fixture
    original = app.load_app_settings()
    key = 'ts-test-secret-middle-1234'
    saved = client.put('/api/settings/secrets/jev', json={'key': key})
    assert saved.status_code == 200
    assert secrets == {'TYPESAFE_API_KEY': key}
    assert key not in saved.text
    assert saved.json['api_status']['jev']['masked_key'] == 'ts-t••••••••1234'
    assert saved.json['api_status']['deepseek']['configured'] is False
    monkeypatch.setenv('TYPESAFE_API_KEY', 'environment-secret-value')
    assert app.provider_secret_value('jev') == 'environment-secret-value'
    removed = client.delete('/api/settings/secrets/jev')
    assert removed.status_code == 200 and secrets == {}
    assert removed.json['api_status']['jev']['environment_override'] is True
    assert removed.json['api_status']['jev']['configured'] is True
    assert app.load_app_settings() == original


def test_jev_catalog_uses_native_names_and_bearer(api_fixture, monkeypatch):
    app, client, secrets = api_fixture
    secrets['TYPESAFE_API_KEY'] = 'ts-test-only'
    calls = []
    def open_request(request, timeout):
        calls.append(request)
        return Reply({'models': [{'name': 'jev-latest'}, {'name': 'jev-version'}, {'name': 'jev-latest'}, {'name': 4}, None]})
    monkeypatch.setattr(app, 'urlopen', open_request)
    result = client.post('/api/settings/apis/jev/models', json={})
    assert result.status_code == 200
    assert [o['value'] for o in result.json['catalog']['options']] == ['jev-latest', 'jev-version']
    assert calls[0].full_url == 'https://api.typesafe.ai/v1/models'
    assert calls[0].get_header('Authorization') == 'Bearer ts-test-only'


@pytest.mark.parametrize('provider,model,answer,url', [
    ('jev', '', {'answers': {'connection_check': {'type': 'noul', 'noul': .98}}}, 'https://api.typesafe.ai/v1/systemone'),
    ('deepseek', 'chosen-deepseek', {'choices': [{'message': {'content': 'OK'}}]}, 'https://api.deepseek.com/beta/chat/completions'),
])
def test_shared_probe_dispatches_native_protocol_without_saving(api_fixture, monkeypatch, provider, model, answer, url):
    app, client, secrets = api_fixture
    secrets[model_apis.API_PROVIDERS[provider]['secret_name']] = 'isolated-key'
    before = app.load_app_settings()
    calls = []
    def open_request(request, timeout):
        calls.append(request)
        return Reply(answer)
    monkeypatch.setattr(app, 'urlopen', open_request)
    result = client.post(f'/api/settings/apis/{provider}/test', json={'model': model})
    assert result.status_code == 200
    assert result.json['provider'] == provider
    assert calls[0].full_url == url
    assert calls[0].get_header('Authorization') == 'Bearer isolated-key'
    payload = json.loads(calls[0].data)
    assert payload['model'] == (model or 'jev-latest')
    if provider == 'jev':
        assert payload['questions']['connection_check']['type'] == 'noul'
        assert 'state' in payload and 'messages' not in payload
    else:
        assert payload['messages'][0]['content'] == 'Reply OK.'
    assert app.load_app_settings() == before


@pytest.mark.parametrize('answer', [{}, {'answers': {}}, {'answers': {'other': {'type': 'noul', 'noul': .9}}},
    {'answers': {'connection_check': {'type': 'noul', 'noul': True}}},
    {'answers': {'connection_check': {'type': 'noul', 'noul': 1.1}}},
    {'answers': {'connection_check': {'type': 'choice', 'noul': .9}}},
    {'choices': [{'message': {'content': 'OK'}}]}, []])
def test_jev_probe_rejects_invalid_native_answers(api_fixture, monkeypatch, answer):
    app, client, secrets = api_fixture
    secrets['TYPESAFE_API_KEY'] = 'test-key'
    monkeypatch.setattr(app, 'urlopen', lambda *args, **kw: Reply(answer))
    response = client.post('/api/settings/apis/jev/test', json={})
    assert response.status_code == 502
    assert response.json['error'] == 'empty_model_response'


@pytest.mark.parametrize('failure,status', [(HTTPError('url', 401, 'PRIVATE_KEY', None, None), 502), (TimeoutError('PRIVATE_KEY'), 504)])
def test_probe_error_redacted(api_fixture, monkeypatch, failure, status):
    app, client, secrets = api_fixture
    secrets['TYPESAFE_API_KEY'] = 'PRIVATE_KEY'
    def fail(*a, **kw): raise failure
    monkeypatch.setattr(app, 'urlopen', fail)
    response = client.post('/api/settings/apis/jev/test', json={})
    assert response.status_code == status
    assert 'PRIVATE_KEY' not in response.text


def test_missing_keys_and_bad_payloads_never_call_provider(api_fixture):
    _, client, _ = api_fixture
    for provider in ['deepseek', 'jev']:
        for action in ['models', 'test']:
            response = client.post(f'/api/settings/apis/{provider}/{action}', json={})
            assert response.status_code == 400 and response.json['error'] == 'missing_api_key'
    assert client.post('/api/settings/apis/unknown/test', json={}).status_code == 400
    assert client.post('/api/settings/apis/unknown/models', json={}).status_code == 400
    for body in [[], None, {'model': 0}, {'model': ['m']}]:
        assert client.post('/api/settings/apis/jev/test', json=body).status_code == 400
    assert client.put('/api/settings/secrets/jev', json=[]).status_code == 400


def test_catalog_failure_does_not_report_fallback_as_fetched(api_fixture, monkeypatch):
    app, client, secrets = api_fixture
    secrets['TYPESAFE_API_KEY'] = 'PRIVATE_KEY'
    monkeypatch.setattr(app, 'urlopen', lambda *a, **kw: Reply({'models': []}))
    response = client.post('/api/settings/apis/jev/models', json={})
    assert response.status_code == 502 and response.json['error'] == 'models_unavailable'
