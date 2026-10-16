"""Regressions for the v0.0.2 contracts, always against an isolated database."""
import json
from urllib.error import HTTPError
import pytest
from test_api import _setup_agent_api_fixture

@pytest.fixture
def fixture(tmp_path, monkeypatch):
    return _setup_agent_api_fixture(tmp_path, monkeypatch)


def test_comments_preserve_legacy_and_delete_one_at_a_time(fixture):
    app, client, item, *_ = fixture
    path = f"/api/news/{item['id']}"
    client.put(path + '/note', json={'note': '原来的想法'})
    assert client.get(path + '/detail').json['notes'][0]['id'] == 'legacy'
    first = client.post(path + '/notes', json={'note': '第二条 <script>'}).json
    assert [row['note'] for row in first['notes']] == ['原来的想法', '第二条 <script>']
    second = client.post(path + '/notes', json={'note': '第三条'}).json
    assert len(second['notes']) == 3
    assert second['note_count'] == 3
    assert client.get('/api/news?collection=notes').json['items'][0]['note_count'] == 3
    assert client.delete(path + '/notes/not-found').status_code == 404
    deleted = client.delete(path + '/notes/' + first['notes'][1]['id']).json
    assert [row['note'] for row in deleted['notes']] == ['原来的想法', '第三条']
    assert '第二条' not in deleted['note']['note']
    assert client.get('/api/news?collection=notes').json['total'] == 1
    for row in deleted['notes']:
        assert client.delete(path + '/notes/' + row['id']).status_code == 200
    assert client.get(path + '/detail').json['has_note'] == 0
    assert client.get('/api/news?collection=notes').json['total'] == 0
    # Old PUT callers can still replace the aggregate coherently.
    client.post(path + '/notes', json={'note': '被替换'})
    client.put(path + '/note', json={'note': '旧客户端编辑'})
    assert [row['note'] for row in client.get(path + '/detail').json['notes']] == ['旧客户端编辑']
    assert client.delete(path + '/notes/legacy').json['has_note'] == 0


def test_note_filter_intersects_important_collection(fixture):
    app, client, item, *_ = fixture
    path = f"/api/news/{item['id']}"
    client.post(path + '/notes', json={'note': '含想法但不重要'})
    assert client.get('/api/news?collection=important&has_note=1').json['total'] == 0
    client.patch(path + '/state', json={'important': True})
    assert client.get('/api/news?collection=important&has_note=1').json['total'] == 1


@pytest.mark.parametrize('quote,source', [('', ''), ('original quote', 'selection')])
def test_frontend_agent_request_is_accepted(fixture, quote, source):
    app, client, item, *_ = fixture
    path = f"/api/news/{item['id']}/agent"
    session = client.post(path + '/session', json={}).json['session']
    response = client.post(path + '/jobs', json={'session_id': session['id'], 'question': '解释背景', 'quote_text': quote, 'quote_source': source})
    assert response.status_code == 202
    assert response.json['job']['status'] == 'queued'
    assert response.json['messages'][0]['quote_text'] == quote


def test_translation_catalog_masks_key_and_probe_uses_selected_model(fixture, monkeypatch):
    app, client, *_ = fixture
    secret = 'sk-fixture-secret-middle-9876'
    monkeypatch.setenv('DEEPSEEK_API_KEY', secret)
    calls = []
    class Reply:
        def __init__(self, data): self.data = data
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self): return json.dumps(self.data).encode()
    def urlopen(request, timeout):
        calls.append(request)
        return Reply({'data': [{'id': 'model-one'}]} if request.full_url.endswith('/models') else {'choices': [{'message': {'content': 'OK'}}]})
    monkeypatch.setattr(app, 'urlopen', urlopen)
    catalog = client.post('/api/settings/translation/models', json={}).json
    assert catalog['service']['masked_key'] == 'sk-f••••••••9876'
    assert secret not in json.dumps(catalog)
    assert catalog['catalog']['options'][0]['value'] == 'model-one'
    result = client.post('/api/settings/translation/test', json={'model': 'custom-model'}).json
    assert result['ok'] and result['model'] == 'custom-model'
    assert json.loads(calls[-1].data)['model'] == 'custom-model'
    def fail(*args, **kwargs): raise HTTPError('url', 401, secret, None, None)
    monkeypatch.setattr(app, 'urlopen', fail)
    result = client.post('/api/settings/translation/test', json={'model': 'custom-model'})
    assert result.status_code == 502
    assert secret not in result.text
    assert '401' in result.json['message']


def test_shared_database_respects_old_app_note_edits(fixture):
    app, client, item, *_ = fixture
    path = f"/api/news/{item['id']}"
    original = client.post(path + '/notes', json={'note': '第一条'}).json['notes'][0]['id']
    with app.db_conn() as conn:
        conn.execute('UPDATE article_notes SET note=? WHERE url=?', ('旧应用直接修改', item['url']))
    assert client.get(path + '/detail').json['notes'][0]['note'] == '旧应用直接修改'
    assert client.get('/api/news?collection=notes').json['items'][0]['note_count'] == 1
    assert client.delete(path + '/notes/' + original).status_code == 404
    added = client.post(path + '/notes', json={'note': '新追加'}).json
    assert [row['note'] for row in added['notes']] == ['旧应用直接修改', '新追加']
    with app.db_conn() as conn:
        conn.execute('DELETE FROM article_notes WHERE url=?', (item['url'],))
    assert client.get(path + '/detail').json['notes'] == []
    added = client.post(path + '/notes', json={'note': '删除后重新添加'}).json
    assert [row['note'] for row in added['notes']] == ['删除后重新添加']


def test_agent_worker_reports_safe_progress_and_requires_completion(fixture, tmp_path, monkeypatch):
    import sys
    app, client, item, *_ = fixture
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    script = tmp_path / 'runtime.py'
    script.write_text("""import json,sys
request=json.load(sys.stdin)
assert request['research_budget_ms']==90000
assert request['max_tool_calls']==6
for event in [
 {'type':'oar_session','id':'test'},
 {'type':'oar_tool','tool':'web_search'},
 {'type':'oar_progress','phase':'summarizing'},
 {'type':'text_delta','delta':'核实后的回答'},
 {'type':'oar_result','outcome':'completed','answer':'核实后的回答'}
]: print(json.dumps(event),flush=True)
""")
    monkeypatch.setattr(app, '_agent_process_command', lambda *args: ([sys.executable, str(script)], None, runtime))
    phases = []
    update = app._update_agent_progress
    def record(job_id, phase):
        update(job_id, phase)
        phases.append(client.get(f"/api/news/{item['id']}/agent/jobs/{job_id}").json['job']['progress'])
    monkeypatch.setattr(app, '_update_agent_progress', record)
    response = client.post(f"/api/news/{item['id']}/agent/jobs", json={'question':'解释背景'})
    assert response.status_code == 202
    assert app.process_pending_agent_once()
    job = app.load_agent_payload(item['id'])['jobs'][-1]
    assert job['status'] == 'succeeded'
    assert job['answer_text'] == '核实后的回答'
    assert phases == ['thinking', 'searching', 'summarizing', 'writing']


def test_agent_sse_reports_progress_changes_in_same_second(fixture, monkeypatch):
    app, client, item, *_ = fixture
    created = client.post(f"/api/news/{item['id']}/agent/jobs", json={'question':'研究背景'}).json
    job_id = created['job']['id']
    monkeypatch.setattr(app, 'AGENT_STREAM_POLL_SECONDS', 0)
    monkeypatch.setattr(app, 'now_ts', lambda: '2026-10-15 12:00:00')
    with app.agent_db_conn() as conn:
        conn.execute("UPDATE agent_jobs SET status='running' WHERE id=?", (job_id,))
    app._update_agent_progress(job_id, 'thinking')
    stream = app._agent_stream_generator(item['id'], job_id)
    assert json.loads(next(stream).removeprefix('data: '))['job']['progress'] == 'thinking'
    app._update_agent_progress(job_id, 'searching')
    assert json.loads(next(stream).removeprefix('data: '))['job']['progress'] == 'searching'
    stream.close()
