"""Catalog timeout and error regressions using isolated credentials/transports."""
import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import BaseRequestHandler, ThreadingTCPServer
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener

import pytest
import model_apis
from test_model_apis import api_fixture


def test_opencode_catalog_accepts_response_slower_than_five_seconds(api_fixture, monkeypatch):
    app, client, secrets = api_fixture
    secrets['OPENCODE_API_KEY'] = 'isolated-key'
    before = app.load_app_settings()
    requests = []
    models = [{'id': f'paid-{i}'} for i in range(70)] + [{'id': f'example-{i}-free'} for i in range(8)]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def do_GET(self):
            requests.append((self.path, self.headers.get('Authorization'), self.headers.get('User-Agent')))
            time.sleep(6)
            body = json.dumps({'data': models}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # The old five-second timeout closes the socket early.

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        address = f'http://127.0.0.1:{server.server_port}/models'
        monkeypatch.setitem(model_apis.API_PROVIDERS['opencode-go'], 'models_url', address)
        monkeypatch.setattr(app, 'urlopen', build_opener(ProxyHandler({})).open)
        started = time.monotonic()
        result = client.post('/api/settings/apis/opencode/models', json={})
        assert result.status_code == 200, result.json
        assert time.monotonic() - started >= 6
        assert result.json['service']['models_endpoint_reachable'] is True
        assert result.json['catalog']['source'] == 'official'
        parsed = model_apis.parse_model_options('opencode', {'data': models})
        assert [row['value'] for row in parsed] == [f'paid-{i}' for i in range(70)]
        # Catalog merging retains its existing display limit after parsing.
        assert [row['value'] for row in result.json['catalog']['options']] == [
            row['value'] for row in parsed[:app.SETTINGS_MODEL_OPTION_LIMIT]]
        assert requests == [('/models', 'Bearer isolated-key', 'news-reader-settings/1.0')]
        assert 'isolated-key' not in result.text
        assert app.load_app_settings() == before
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_opencode_stalled_tls_handshake_is_reported_as_timeout(api_fixture, monkeypatch):
    app, client, secrets = api_fixture
    secrets['OPENCODE_API_KEY'] = 'isolated-key'
    release = threading.Event()
    connections, failures = [], []

    class Handler(BaseRequestHandler):
        def handle(self):
            connections.append(self.request.recv(1))
            release.wait(2)  # Accept ClientHello, but never send ServerHello.

    server = ThreadingTCPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    transport = build_opener(ProxyHandler({})).open

    def open_request(request, timeout):
        assert 20 <= timeout <= 30
        try:
            # Exercise real TLS/urllib wrapping without waiting 25 seconds in CI.
            return transport(request, timeout=.15)
        except URLError as exc:
            failures.append(exc.reason)
            raise

    try:
        address = f'https://127.0.0.1:{server.server_address[1]}/models'
        monkeypatch.setitem(model_apis.API_PROVIDERS['opencode-go'], 'models_url', address)
        monkeypatch.setattr(app, 'urlopen', open_request)
        result = client.post('/api/settings/apis/opencode/models', json={})
        assert len(failures) == 1 and isinstance(failures[0], TimeoutError)
        assert 'handshake' in str(failures[0]).lower()
        assert connections == [b'\x16']  # TLS handshake record, before HTTP/authentication.
        assert result.status_code == 504
        assert result.json['error'] == 'models_unavailable'
        assert result.json['reason'] == 'connection_timeout'
        assert 'HTTPS 握手' in result.json['message']
        assert '25 秒' in result.json['message']
        assert '鉴权' not in result.json['message']
        assert 'isolated-key' not in result.text
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize('wrapped', [False, True])
def test_catalog_response_read_timeout_is_not_a_parse_error(api_fixture, monkeypatch, wrapped):
    app, client, secrets = api_fixture
    secrets['OPENCODE_API_KEY'] = 'PRIVATE_KEY'

    class Reply(io.BytesIO):
        def read(self):
            failure = TimeoutError('PRIVATE_KEY')
            raise URLError(failure) if wrapped else failure

    monkeypatch.setattr(app, 'urlopen', lambda *a, **kw: Reply())
    result = client.post('/api/settings/apis/opencode/models', json={})
    assert result.status_code == 504
    assert result.json['reason'] == 'connection_timeout'
    assert '超时' in result.json['message']
    assert 'PRIVATE_KEY' not in result.text


@pytest.mark.parametrize('body', [
    b'not-json PRIVATE_KEY', b'\xffPRIVATE_KEY', b'[]', b'{}',
    b'{"data": {}}', b'{"data": [null, {"id": 42}]}',
])
def test_catalog_invalid_response_is_reported_as_parse_failure(api_fixture, monkeypatch, body):
    app, client, secrets = api_fixture
    secrets['OPENCODE_API_KEY'] = 'PRIVATE_KEY'
    before = app.load_app_settings()
    monkeypatch.setattr(app, 'urlopen', lambda *a, **kw: io.BytesIO(body))
    result = client.post('/api/settings/apis/opencode/models', json={})
    assert result.status_code == 502
    assert result.json['error'] == 'models_unavailable'
    assert result.json['reason'] == 'response_parse_failed'
    assert '解析失败' in result.json['message']
    assert 'PRIVATE_KEY' not in result.text
    assert app.load_app_settings() == before


@pytest.mark.parametrize('status,payload,reason,message', [
    (401, {}, 'authentication_failed', '密钥鉴权失败'),
    (403, {}, 'access_denied', '拒绝访问'),
    (403, {'error_name': 'browser_signature_banned'}, 'client_blocked', '客户端标识'),
    (429, {}, 'usage_limited', '限制了请求'),
])
def test_catalog_http_errors_keep_upstream_reason(api_fixture, monkeypatch, status, payload, reason, message):
    app, client, secrets = api_fixture
    secrets['OPENCODE_API_KEY'] = 'PRIVATE_KEY'
    before = app.load_app_settings()
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        body = json.dumps({**payload, 'message': 'PRIVATE_KEY account@example.com'}).encode()
        raise HTTPError('url', status, 'PRIVATE_KEY', {'Retry-After': '90'}, io.BytesIO(body))

    monkeypatch.setattr(app, 'urlopen', fail)
    result = client.post('/api/settings/apis/opencode/models', json={})
    assert result.status_code == 502
    assert result.json['error'] == 'models_unavailable'
    assert result.json['reason'] == reason
    assert result.json['upstream_status'] == status
    assert result.json['retry_after_seconds'] == 90
    assert message in result.json['message']
    assert f'HTTP {status}' in result.json['message']
    assert 'PRIVATE_KEY' not in result.text and 'account@example.com' not in result.text
    assert calls == [1]
    assert app.load_app_settings() == before


@pytest.mark.parametrize('failure', [URLError('DNS PRIVATE_KEY'), ConnectionRefusedError('PRIVATE_KEY')])
def test_catalog_network_errors_are_distinct_from_timeout_and_auth(api_fixture, monkeypatch, failure):
    app, client, secrets = api_fixture
    secrets['OPENCODE_API_KEY'] = 'PRIVATE_KEY'

    def fail(*args, **kwargs): raise failure

    monkeypatch.setattr(app, 'urlopen', fail)
    result = client.post('/api/settings/apis/opencode/models', json={})
    assert result.status_code == 502
    assert result.json['reason'] == 'network_error'
    assert '无法连接' in result.json['message']
    assert 'PRIVATE_KEY' not in result.text


def test_catalog_unreadable_key_is_not_a_response_parse_failure(api_fixture):
    _, client, secrets = api_fixture
    secrets['OPENCODE_API_KEY'] = ''
    result = client.post('/api/settings/apis/opencode/models', json={})
    assert result.status_code == 502
    assert result.json['reason'] == 'key_read_failed'
    assert '读取已保存的 API 密钥失败' in result.json['message']
