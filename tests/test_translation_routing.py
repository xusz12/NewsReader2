"""Translation routing uses isolated settings, deterministic models and local HTTP."""
import json
import threading
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import model_apis
import settings
from test_model_apis import api_fixture


def config(primary='deepseek', fallback=True, pi=True):
    value = settings.default_app_settings()
    value['llm']['translation'].update(provider=primary, model='deepseek-custom', fallback_enabled=fallback, pi_fallback_enabled=pi)
    value['api_models']['opencode']['model'] = 'open-custom'
    value['llm']['pi_chat'] = {'provider': 'pi-provider', 'model': 'pi-model'}
    return value


def translated(model='chosen'):
    return {'model':model, 'body_zh':'这是完整中文译文。', 'key_points_zh':['一','二','三'], 'conclusion_zh':'结论', 'raw_json':'{}'}


def test_legacy_settings_gain_routing_and_old_writes_preserve_switches(api_fixture):
    app, client, _ = api_fixture
    assert client.get('/api/settings').json['llm']['translation']['provider'] == 'deepseek'
    legacy = {'llm': {'translation': {'provider': 'deepseek', 'model': 'deepseek-custom'}}}
    app.save_app_settings(legacy)
    assert app.load_app_settings()['llm']['translation'] == {
        'provider':'deepseek', 'model':'deepseek-custom', 'fallback_enabled':True, 'pi_fallback_enabled':True}
    value = config('opencode', False, False)
    response = client.put('/api/settings', json={'llm':value['llm'], 'api_models':value['api_models']})
    assert response.status_code == 200
    assert response.json['llm']['translation'] == value['llm']['translation']
    assert app.load_app_settings()['api_models']['opencode']['model'] == 'open-custom'
    # A model-only older client may omit both new switches.
    response = client.put('/api/settings', json={'llm':{'translation':{'provider':'opencode','model':'deepseek-next'}}})
    assert response.status_code == 200
    assert response.json['llm']['translation'] == {**value['llm']['translation'], 'model':'deepseek-next'}
    assert response.json['api_models']['opencode']['model'] == 'open-custom'


@pytest.mark.parametrize('update', [{'provider':'jev'}, {'provider':[]}, {'model':12}, {'model':'bad\nmodel'},
    {'fallback_enabled':'true'}, {'fallback_enabled':1}, {'pi_fallback_enabled':None}])
def test_invalid_routing_never_persists(api_fixture, update):
    app, client, _ = api_fixture
    before = app.load_app_settings()
    value = deepcopy(before['llm'])
    value['translation'].update(update)
    response = client.put('/api/settings', json={'llm':value})
    assert response.status_code == 400
    assert app.load_app_settings() == before


@pytest.mark.parametrize('primary', ['deepseek','opencode'])
@pytest.mark.parametrize('failures', [[], ['primary'], ['primary','secondary']])
def test_http_priority_and_pi_is_last(api_fixture, monkeypatch, primary, failures):
    app, _, _ = api_fixture
    secondary = 'opencode' if primary == 'deepseek' else 'deepseek'
    rejected = set([primary, secondary][:len(failures)])
    calls = []
    def api(**kwargs):
        calls.append((kwargs['provider'], kwargs['model']))
        if kwargs['provider'] in rejected:
            raise app.LLMClientError('PROVIDER_TIMEOUT: private-provider-body')
        return translated(kwargs['model'])
    def pi(**kwargs):
        calls.append(('pi', kwargs['pi_model']))
        return translated(kwargs['pi_model'])
    monkeypatch.setattr(app, 'generate_article_ai', api)
    monkeypatch.setattr(app, 'generate_pi_fallback_translation', pi)
    result = app.generate_news_translation(config(primary), title='title', source='Reuters', content='full content')
    expected = [primary,secondary,'pi'][:len(failures)+1]
    assert [p for p, _ in calls] == expected
    assert dict(calls).get('deepseek', 'deepseek-custom') == 'deepseek-custom'
    assert dict(calls).get('opencode', 'open-custom') == 'open-custom'
    route = json.loads(result['raw_json'])['translation_route']
    assert route['provider'] == expected[-1]
    assert route['fallback'] is bool(failures)
    assert 'private-provider-body' not in result['raw_json']


@pytest.mark.parametrize('fallback,pi,expected', [(False,False,['opencode']), (False,True,['opencode','pi']),
    (True,False,['opencode','deepseek']), (True,True,['opencode','deepseek','pi'])])
def test_disabled_fallbacks_never_call_or_report_success(api_fixture, monkeypatch, fallback, pi, expected):
    app, _, _ = api_fixture
    calls = []
    def fail(**kwargs):
        calls.append(kwargs.get('provider','pi'))
        raise app.LLMClientError('REQUEST_FAILED: PRIVATE_KEY')
    monkeypatch.setattr(app, 'generate_article_ai', fail)
    monkeypatch.setattr(app, 'generate_pi_fallback_translation', fail)
    with pytest.raises(app.LLMClientError, match='TRANSLATION_PROVIDERS_FAILED') as caught:
        app.generate_news_translation(config('opencode',fallback,pi), title='t', source='Reuters', content='source')
    assert calls == expected
    assert 'PRIVATE_KEY' not in str(caught.value)


def test_x_body_uses_same_api_order_without_digest(api_fixture, monkeypatch):
    app, _, _ = api_fixture
    calls = []
    def body(**kwargs):
        calls.append(kwargs['provider'])
        if kwargs['provider'] == 'opencode':
            raise app.LLMClientError('INVALID_BODY_ZH')
        return {**translated(kwargs['model']), 'key_points_zh':[], 'conclusion_zh':''}
    monkeypatch.setattr(app, 'generate_body_translation_only', body)
    monkeypatch.setattr(app, 'generate_article_ai', lambda **kw: pytest.fail('X must not generate a digest'))
    result = app.generate_news_translation(config('opencode'), title='Tweet', source='Twitter/X', content='original')
    assert calls == ['opencode','deepseek']
    assert result['key_points_zh'] == [] and result['conclusion_zh'] == ''


@pytest.mark.parametrize('succeeds', [True, False])
def test_worker_persists_only_complete_translation_and_preserves_original(api_fixture, monkeypatch, succeeds):
    app, _, _ = api_fixture
    value = config('opencode', True, False)
    monkeypatch.setattr(app, 'current_runtime_settings', lambda: deepcopy(value))
    url, stamp = 'https://example.com/routing', '2026-11-01 08:00:00'
    with app.db_conn() as conn:
        conn.execute('INSERT INTO article_details(url,title,source,content,content_length,fetched_at,updated_at) VALUES (?,?,?,?,?,?,?)',
                     (url,'Title','Reuters','original full body',18,stamp,stamp))
        conn.execute("INSERT INTO ai_jobs(url,status,queued_at,updated_at) VALUES (?,'pending',?,?)",(url,stamp,stamp))
    calls = []
    def api(**kwargs):
        calls.append(kwargs['provider'])
        if kwargs['provider'] == 'opencode' or not succeeds:
            raise app.LLMClientError('INCOMPLETE_TRANSLATION')
        return translated(kwargs['model'])
    monkeypatch.setattr(app, 'generate_article_ai', api)
    assert app.process_pending_ai_once() is True
    assert calls == ['opencode','deepseek']
    with app.db_conn() as conn:
        result = conn.execute('SELECT * FROM article_ai WHERE url=?',(url,)).fetchone()
        job = conn.execute('SELECT status,last_error FROM ai_jobs WHERE url=?',(url,)).fetchone()
        assert conn.execute('SELECT content FROM article_details WHERE url=?',(url,)).fetchone()[0] == 'original full body'
    assert job['status'] == ('success' if succeeds else 'failed')
    if succeeds:
        assert result['model'] == 'deepseek-custom'
        assert json.loads(result['raw_json'])['translation_route']['provider'] == 'deepseek'
    else:
        assert result is None
        assert 'TRANSLATION_PROVIDERS_FAILED' in job['last_error']


@pytest.mark.parametrize('response_kind', ['success', 'auth', 'timeout', 'rate_limit', 'bad_json', 'truncated', 'non_chinese'])
def test_real_sdk_opencode_translation_and_api_failover(api_fixture, monkeypatch, response_kind):
    import llm_client
    app, _, _ = api_fixture
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append((self.path, body, self.headers.get('Authorization')))
            if self.path.startswith('/opencode'):
                status = {'auth': 401, 'timeout': 408, 'rate_limit': 429}.get(response_kind, 200)
                payload = translated('open-custom')
                if response_kind == 'non_chinese':
                    payload['body_zh'] = 'The original English body.'
                answer = {'error':{'message':'PRIVATE_KEY','type':'authentication_error'}} if status != 200 else {
                    'model':'open-custom','choices':[{'finish_reason':'length' if response_kind == 'truncated' else 'stop',
                        'message':{'role':'assistant','content':'invalid JSON' if response_kind == 'bad_json' else json.dumps(payload,ensure_ascii=False)}}]}
            else:
                status = 200
                answer = {'model':'deepseek-custom','choices':[{'finish_reason':'tool_calls','message':{'role':'assistant',
                    'tool_calls':[{'id':'t1','type':'function','function':{'name':'save_article_translation','arguments':json.dumps(translated(),ensure_ascii=False)}}]}}]}
            raw = json.dumps(answer).encode()
            self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    try:
        for provider in ('deepseek','opencode'):
            monkeypatch.setitem(model_apis.API_PROVIDERS[provider], 'translation_base_url', f'http://127.0.0.1:{server.server_port}/{provider}')
        monkeypatch.setattr(llm_client, '_resolve_api_key', lambda name: 'isolated-'+name)
        monkeypatch.setattr(app, 'generate_pi_fallback_translation', lambda **kw: pytest.fail('API backup must precede Pi'))
        result = app.generate_news_translation(config('opencode'), title='t',source='Reuters',content='English body')
        expected = ['/opencode/chat/completions'] + ([] if response_kind == 'success' else ['/deepseek/chat/completions'])
        assert [path for path, _, _ in calls] == expected
        assert calls[0][2] == 'Bearer isolated-OPENCODE_API_KEY'
        assert not any(key in calls[0][1] for key in ('tools','tool_choice','thinking','temperature'))
        if response_kind != 'success':
            assert calls[1][2] == 'Bearer isolated-DEEPSEEK_API_KEY'
            assert calls[1][1]['tools'][0]['function']['strict'] is True
        route = json.loads(result['raw_json'])['translation_route']
        assert route['provider'] == ('opencode' if response_kind == 'success' else 'deepseek')
        assert result['body_zh'] == '这是完整中文译文。'
        assert 'PRIVATE_KEY' not in result['raw_json']
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)
