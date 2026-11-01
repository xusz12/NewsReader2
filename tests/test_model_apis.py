"""Provider contracts, persistence and credentials; no real model calls."""
import json
import io
from email.message import Message
from urllib.error import HTTPError

import pytest
import model_apis
from test_api import _setup_agent_api_fixture


@pytest.fixture
def api_fixture(tmp_path, monkeypatch):
    app, client, *_ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    for name in ("DEEPSEEK_API_KEY", "TYPESAFE_API_KEY", "OPENCODE_API_KEY"):
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
    assert initial['api_models'] == {'jev': {'model': ''}, 'opencode-go': {'model': ''}}
    assert initial['classification'] == {'enabled': True, 'concurrency': 8, 'unread_only': False}
    assert [p['id'] for p in initial['api_providers']] == ['deepseek', 'jev', 'opencode-go']
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
    assert app.load_app_settings()['classification']['concurrency'] == 16
    assert app.CLASSIFICATION_SERVICE.gate.snapshot()['configured_concurrency'] == 16
    assert app.CLASSIFICATION_SERVICE.gate.snapshot()['effective_concurrency'] == 16
    assert app.CLASSIFICATION_SERVICE.threads == []
    # A model-only write from an older client preserves the new setting.
    saved = client.put('/api/settings', json={'llm': llm, 'api_models': {'jev': {'model': 'jev-legacy'}}})
    assert saved.status_code == 200
    assert saved.json['classification']['concurrency'] == 16
    assert client.get('/api/news/classification').json['throttle']['configured_concurrency'] == 16


def test_unread_only_persists_applies_live_and_survives_legacy_writes(api_fixture):
    app, client, _ = api_fixture
    llm = app.load_app_settings()['llm']
    saved = client.put('/api/settings', json={'llm': llm, 'api_models': {'jev': {'unread_only': True}}})
    assert saved.status_code == 200
    assert app.load_app_settings()['classification']['unread_only'] is True
    assert app.CLASSIFICATION_SERVICE.unread_only is True
    assert app.CLASSIFICATION_SERVICE.threads == []
    saved = client.put('/api/settings', json={'llm': llm, 'api_models': {'jev': {'model': 'jev-legacy'}}})
    assert saved.status_code == 200 and saved.json['classification']['unread_only'] is True
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
    ('opencode-go', 'chosen-opencode', {'choices': [{'message': {'content': 'OK'}}]}, 'https://opencode.ai/inference/go/openai/v1/chat/completions'),
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
    for provider in model_apis.API_PROVIDERS:
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


def test_opencode_model_and_key_survive_legacy_client_writes(api_fixture, monkeypatch):
    from settings import resolve_app_settings_path
    app, client, secrets = api_fixture
    settings = client.get('/api/settings').json
    settings['api_models']['opencode-go']['model'] = ' custom-model '
    saved = client.put('/api/settings', json={'llm': settings['llm'], 'api_models': settings['api_models']})
    assert saved.status_code == 200
    assert saved.json['api_models']['opencode-go']['model'] == 'custom-model'
    key = 'open-test-secret-middle-1234'
    result = client.put('/api/settings/secrets/opencode-go', json={'key': key})
    assert result.status_code == 200
    assert secrets == {'OPENCODE_API_KEY': key}
    assert result.json['api_status']['opencode-go']['masked_key'] == 'open••••••••1234'
    assert result.json['api_status']['jev']['configured'] is False
    assert key not in result.text
    legacy = client.put('/api/settings', json={'llm': settings['llm'], 'api_models': {'jev': {'model': 'jev-next'}}})
    assert legacy.json['api_models']['opencode-go']['model'] == 'custom-model'
    assert key not in resolve_app_settings_path().read_text()
    monkeypatch.setenv('OPENCODE_API_KEY', 'environment-key')
    assert app.provider_secret_value('opencode-go') == 'environment-key'
    removed = client.delete('/api/settings/secrets/opencode-go')
    assert removed.status_code == 200 and secrets == {}
    assert removed.json['api_status']['opencode-go']['configured'] is True
    assert removed.json['api_status']['opencode-go']['environment_override'] is True
    assert removed.json['api_models']['opencode-go']['model'] == 'custom-model'


def test_opencode_catalog_and_explicit_model_selection(api_fixture, monkeypatch):
    app, client, secrets = api_fixture
    secrets['OPENCODE_API_KEY'] = 'test-key'
    # No arbitrary paid model is silently selected for a connection test.
    result = client.post('/api/settings/apis/opencode-go/test', json={})
    assert result.status_code == 400 and result.json['error'] == 'invalid_model'
    calls = []
    def models(request, timeout):
        calls.append(request)
        return Reply({'data': [{'id': 'model-a'}, {'id': 'model-b'}, {'id': 'model-a'}, {'id': 2}, None]})
    monkeypatch.setattr(app, 'urlopen', models)
    result = client.post('/api/settings/apis/opencode-go/models', json={})
    assert result.status_code == 200
    assert [r['value'] for r in result.json['catalog']['options']] == ['model-a', 'model-b']
    assert calls[0].full_url == 'https://opencode.ai/inference/go/v1/models'
    assert calls[0].get_header('Authorization') == 'Bearer test-key'


def test_opencode_catalog_hides_free_models_and_preserves_paid_ids():
    options = model_apis.parse_model_options('opencode-go', {'data': [
        {'id': 'big-pickle'}, {'id': 'example-free'},
        {'id': 'zero-priced', 'cost': {'text': {'input': {'price': 0}, 'output': {'price': 0}}}},
        {'id': 'paid-model', 'cost': {'text': {'input': {'price': .1}, 'output': {'price': 1}}}},
        {'id': 'unknown-cost'}, {'id': 'missing-price', 'cost': {'text': {'input': {}, 'output': {}}}},
    ]})
    assert [item['value'] for item in options] == ['paid-model', 'unknown-cost', 'missing-price']
    assert all(item['label'] == item['value'] for item in options)
    assert model_apis.parse_model_options('deepseek', {'data': [{'id': 'example-free'}]})[0]['label'] == 'example-free'
    assert model_apis.parse_model_options('opencode-go', {'data': [{'id': 'jev-free', 'type': 'systemone'}]}) == []


def test_filtered_empty_catalog_succeeds_without_restoring_saved_free_model(api_fixture, monkeypatch):
    app, client, secrets = api_fixture
    secrets['OPENCODE_API_KEY'] = 'isolated-key'
    settings = app.load_app_settings()
    settings['api_models']['opencode-go']['model'] = 'big-pickle'
    app.save_app_settings(settings)
    monkeypatch.setattr(app, 'urlopen', lambda *args, **kwargs: Reply({'data': [{'id': 'big-pickle'}, {'id': 'example-free'}]}))
    response = client.post('/api/settings/apis/opencode-go/models', json={})
    assert response.status_code == 200
    assert response.json['service']['models_endpoint_reachable'] is True
    assert response.json['catalog']['options'] == []
    assert app.managed_api_snapshot('opencode-go', 'big-pickle')['catalog']['options'] == []
    assert app.load_app_settings()['api_models']['opencode-go']['model'] == 'big-pickle'


def test_pi_catalog_hides_only_opencode_free_candidates(api_fixture):
    app, _, _ = api_fixture
    providers, models = app.parse_pi_model_catalog('''provider model
opencode big-pickle
opencode example-free
opencode paid-model
opencode-go another-free
opencode-go go-paid
local another-free
''')
    assert providers == ['opencode', 'opencode-go', 'local']
    assert models == {'opencode': ['paid-model'], 'opencode-go': ['go-paid'], 'local': ['another-free']}


@pytest.mark.parametrize('upstream_type,reason,text', [
    ('FreeUsageLimitError', 'free_usage_limit', '免费模型的使用限制'),
    ('FreeTierError', 'free_model_restricted', '第三方应用不可调用'),
    ('GoUsageLimitError', 'subscription_limit', 'Go 的用量限制'),
    ('InsufficientBalanceError', 'insufficient_balance', '余额不足'),
    ('MonthlyLimitError', 'budget_limit', '月度消费上限'),
    ('insufficient_quota', 'insufficient_quota', '可用额度不足'),
    ('rate_limit_exceeded', 'rate_limit', '请求频率限制'),
    ('UnrecognizedError', 'usage_limited', '未说明具体原因'),
])
def test_probe_429_distinguishes_limit_types_without_leaking_response(api_fixture, monkeypatch, upstream_type, reason, text):
    app, client, secrets = api_fixture
    secrets['OPENCODE_API_KEY'] = 'PRIVATE_KEY'
    headers = Message()
    headers['Retry-After'] = '90'
    calls = []
    def fail(request, **kwargs):
        calls.append(request)
        body = {'error': {'type': upstream_type, 'message': 'PRIVATE_KEY account@example.com usage limit exhausted'}}
        raise HTTPError(request.full_url, 429, 'PRIVATE_KEY', headers, io.BytesIO(json.dumps(body).encode()))
    monkeypatch.setattr(app, 'urlopen', fail)
    response = client.post('/api/settings/apis/opencode-go/test', json={'model': 'selected-model'})
    assert response.status_code == 502
    assert response.json['reason'] == reason
    assert response.json['upstream_status'] == 429
    assert response.json['retry_after_seconds'] == 90
    assert text in response.json['message']
    assert '2 分钟' in response.json['message']
    assert 'PRIVATE_KEY' not in response.text and 'account@example.com' not in response.text
    assert len(calls) == 1  # No automatic retries or silent switch during a connection test.


@pytest.mark.parametrize('value,expected', [('30', 30), ('0.5', 1), ('NaN', None), ('inf', None), ('-1', None), ('PRIVATE_KEY', None)])
def test_http_error_retry_after_is_bounded_and_redacted(value, expected):
    headers = Message()
    headers['Retry-After'] = value
    error = HTTPError('url', 429, 'reason', headers, io.BytesIO(b'<html>PRIVATE_KEY</html>'))
    result = model_apis.http_error_details(error)
    assert result.get('retry_after_seconds') == expected
    assert result['reason'] == 'usage_limited'
    assert 'PRIVATE_KEY' not in json.dumps(result)


def test_http_error_accepts_retry_after_date_and_milliseconds():
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime
    headers = Message()
    headers['Retry-After'] = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=100), usegmt=True)
    result = model_apis.http_error_details(HTTPError('url', 429, 'reason', headers, io.BytesIO(b'{}')))
    assert 98 <= result['retry_after_seconds'] <= 100
    headers = Message()
    headers['retry-after-ms'] = '1500'
    result = model_apis.http_error_details(HTTPError('url', 429, 'reason', headers, io.BytesIO(b'{}')))
    assert result['retry_after_seconds'] == 2


def test_opencode_catalog_and_probe_work_when_default_urllib_signature_is_rejected(api_fixture, monkeypatch):
    """Reproduce the observed Cloudflare 1010 at the HTTP boundary."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.request import Request, build_opener, ProxyHandler

    app, client, secrets = api_fixture
    secrets['OPENCODE_API_KEY'] = 'isolated-key'
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def respond(self):
            requests.append((self.command, self.headers.get('Authorization')))
            if self.headers.get('User-Agent', '').startswith('Python-urllib/'):
                status, payload = 403, {'error_code': 1010, 'error_name': 'browser_signature_banned'}
            else:
                status = 200
                payload = {'data': [{'id': 'test-model'}]} if self.command == 'GET' else {
                    'choices': [{'message': {'content': 'OK'}}]}
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = respond
        do_POST = respond

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        address = f'http://127.0.0.1:{server.server_port}'
        transport = build_opener(ProxyHandler({})).open
        # Establish that this test rejects the previously used request signature.
        with pytest.raises(HTTPError) as caught:
            transport(Request(address + '/models'), timeout=5)
        assert caught.value.code == 403
        caught.value.close()
        monkeypatch.setattr(app, 'urlopen', transport)
        monkeypatch.setitem(model_apis.API_PROVIDERS['opencode-go'], 'models_url', address + '/models')
        monkeypatch.setitem(model_apis.API_PROVIDERS['opencode-go'], 'test_url', address + '/chat/completions')
        catalog = client.post('/api/settings/apis/opencode-go/models', json={})
        assert catalog.status_code == 200
        assert [row['value'] for row in catalog.json['catalog']['options']] == ['test-model']
        probe = client.post('/api/settings/apis/opencode-go/test', json={'model': 'test-model'})
        assert probe.status_code == 200
        assert requests[1:] == [('GET', 'Bearer isolated-key'), ('POST', 'Bearer isolated-key')]
        assert 'isolated-key' not in catalog.text + probe.text
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize('failure', [HTTPError('url', 403, 'PRIVATE_KEY', None, None), HTTPError('url', 429, 'PRIVATE_KEY', None, None), TimeoutError('PRIVATE_KEY')])
def test_opencode_failures_are_redacted_and_leave_settings_intact(api_fixture, monkeypatch, failure):
    app, client, secrets = api_fixture
    secrets['OPENCODE_API_KEY'] = 'PRIVATE_KEY'
    before = app.load_app_settings()
    def fail(*a, **kw): raise failure
    monkeypatch.setattr(app, 'urlopen', fail)
    for action in ('test', 'models'):
        result = client.post(f'/api/settings/apis/opencode-go/{action}', json={'model': 'chosen-model'})
        assert result.status_code in (502, 504)
        assert result.json['ok'] is False
        assert 'PRIVATE_KEY' not in result.text
    assert app.load_app_settings() == before


@pytest.mark.parametrize('value', [None, [], {'model': 7}, {'model': 'bad\nmodel'}, {'model': 'x' * 121}])
def test_invalid_opencode_config_is_atomic(api_fixture, value):
    app, client, _ = api_fixture
    before = app.load_app_settings()
    result = client.put('/api/settings', json={'llm': before['llm'], 'api_models': {'opencode-go': value}})
    assert result.status_code == 400
    assert app.load_app_settings() == before


def test_managed_go_key_does_not_enable_pi_providers_and_preserves_explicit_environment(monkeypatch):
    import os
    import llm_client
    monkeypatch.delenv('OPENCODE_API_KEY', raising=False)
    monkeypatch.setenv('PI_PACKAGE_DIR', '/incompatible/runtime')
    monkeypatch.setattr(llm_client, 'read_secret', lambda name: 'stored-key' if name == 'OPENCODE_API_KEY' else None)
    env = llm_client._pi_subprocess_env()
    assert 'OPENCODE_API_KEY' not in env
    assert 'PI_PACKAGE_DIR' not in env
    assert 'OPENCODE_API_KEY' not in os.environ
    monkeypatch.setenv('OPENCODE_API_KEY', 'environment-key')
    assert llm_client._pi_subprocess_env()['OPENCODE_API_KEY'] == 'environment-key'
    monkeypatch.delenv('OPENCODE_API_KEY')
    def unavailable(_): raise llm_client.SecretStoreError('keychain_unavailable')
    monkeypatch.setattr(llm_client, 'read_secret', unavailable)
    assert 'OPENCODE_API_KEY' not in llm_client._pi_subprocess_env()


def test_pi_report_and_connection_transports_do_not_receive_managed_key(tmp_path, monkeypatch):
    import sys
    import llm_client
    import report_model
    import connection_tests
    monkeypatch.delenv('OPENCODE_API_KEY', raising=False)
    monkeypatch.setattr(llm_client, 'read_secret', lambda _: 'isolated-managed-key')
    script = tmp_path / 'pi_transport.py'
    script.write_text('''import sys, json, os
assert 'OPENCODE_API_KEY' not in os.environ
request = sys.stdin.read()
if request.startswith('{'):
    if json.loads(request).get('action') == 'metadata':
        print(json.dumps({'contextWindow': 10000, 'maxTokens': 1000}))
    else:
        print(json.dumps({'type':'oar_result','outcome':'completed','answer':'transport-ok'}))
else:
    print(json.dumps({'type':'message_end','message':{'role':'assistant','content':[{'type':'text','text':'NEWSREADER_CONNECTION_OK'}]}}))
    print(json.dumps({'type':'agent_end'}))
''')
    monkeypatch.setattr(report_model, 'pi_command', lambda: [sys.executable, str(script)])
    assert report_model.pi_metadata({'action': 'metadata'})['maxTokens'] == 1000
    assert report_model.run({'runtime':'pi','provider':'opencode-go','model':'test-model'}, 'prompt', timeout=5) == 'transport-ok'
    monkeypatch.setattr(connection_tests, 'pi_command', lambda _: [sys.executable, str(script)])
    connection_tests.run_pi({'provider':'opencode-go','model':'test-model'})
