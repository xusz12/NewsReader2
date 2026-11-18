import io
import json
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError

import pytest

import decision_protocol as protocol
import news_classification as nc
import topic_tracking as tt
from database_schema import apply_schema
from test_model_apis import api_fixture, Reply
from test_news_classification import conn, insert, response
from test_topic_tracking import env, ready, article


def decisions_response(questions, choices=None):
    answers = []
    for question in questions:
        name = question['name']
        if question['type'] == 'predicate':
            answers.append({'name': name, 'type': 'predicate', 'probability': .1})
        else:
            selected = (choices or {}).get(name, 'AI' if name == 'topic' else '新增节点' if name == 'milestone' else '属于')
            answers.append({'name': name, 'type': 'choice', 'choice': selected, 'confidence': 1,
                            'probabilities': [{'value': c['value'], 'probability': float(c['value'] == selected)}
                                              for c in question['choices']]})
    return {'model': 'gpt-6-luna', 'answers': answers, 'usage': {'input_tokens': 100, 'output_tokens': 0, 'total_tokens': 100}}


def fake_decisions(monkeypatch, choices=None):
    calls = []
    def send(request, **kwargs):
        payload = json.loads(request.data)
        calls.append((request, payload))
        return io.StringIO(json.dumps(decisions_response(payload['questions'], choices)))
    monkeypatch.setattr(nc, 'urlopen', send)
    return calls


def test_native_contract_uses_same_rules_and_reorders_answers_safely(monkeypatch):
    calls = fake_decisions(monkeypatch)
    result = nc.classify('标题', 'openai-key', 'gpt-6-luna', provider='openai-decisions')
    request, body = calls[0]
    assert request.full_url == 'https://api.openai.com/v1/decisions'
    assert request.get_header('Authorization') == 'Bearer openai-key'
    assert body['input'] == '标题' and 'state' not in body and 'messages' not in body
    assert body['questions'][0]['choices'] == [{'value': k, 'description': v} for k, v in nc.RULES['questions']['topic']['criteria'].items()]
    assert nc.RULES['questions']['china_focus']['criteria']['false'] in body['questions'][1]['instructions']
    assert result['category'] == 'ai' and result['provider'] == 'openai-decisions'
    payload = decisions_response(body['questions'])
    payload['answers'].reverse()
    assert protocol.normalize_response('openai-decisions', payload, nc.RULES['questions'])['answers']['topic']['choice'] == 'AI'


@pytest.mark.parametrize('mutation', [
    lambda p: p['answers'].pop(),
    lambda p: p['answers'].append(p['answers'][0]),
    lambda p: p['answers'][1].update(name='topic'),
    lambda p: p['answers'][0].update(name='unknown'),
    lambda p: p['answers'][0].update(choice='unexpected'),
    lambda p: p['answers'][0].update(choice=True),
    lambda p: p['answers'][0].update(confidence=float('nan')),
    lambda p: p['answers'][0]['probabilities'].pop(),
    lambda p: p['answers'][0]['probabilities'].append(p['answers'][0]['probabilities'][0]),
    lambda p: p['answers'][0]['probabilities'][0].update(probability=-.1),
    lambda p: p['answers'][0]['probabilities'][0].update(probability=.5),
    lambda p: p['answers'][1].update(probability=True),
    lambda p: p['answers'][1].update(probability=float('inf')),
    lambda p: p['answers'][0].update(type='refusal'),
    lambda p: p.update(model=''),
])
def test_invalid_or_refused_answer_never_becomes_a_business_result(monkeypatch, mutation):
    body = protocol.request_payload('openai-decisions', 'gpt-6-luna', 'title', nc.RULES['questions'])
    payload = decisions_response(body['questions'])
    mutation(payload)
    monkeypatch.setattr(nc, 'urlopen', lambda *a, **kw: io.StringIO(json.dumps(payload)))
    with pytest.raises(nc.ClassificationError) as error:
        nc.classify('title', 'key', 'gpt-6-luna', provider='openai-decisions')
    assert error.value.code in {'invalid_response', 'decision_refused'}


@pytest.mark.parametrize('choice', ['新增节点', '不收录', '信息不足', 'existing'])
def test_decisions_drives_membership_and_milestones_with_classification_disabled(env, monkeypatch, choice):
    connect, service, agent_calls = env
    ident = ready(service)
    old = service.detail(ident)['nodes'][0]
    calls = fake_decisions(monkeypatch, {'milestone': 'existing:' + old['id'] if choice == 'existing' else choice})
    worker = nc.Service(connect, lambda: ('key', 'gpt-6-luna', 'openai-decisions'), topic_queue=tt.MatchQueue())
    worker.configure(8, enabled=False, unread_only=True, provider='openai-decisions')
    with closing(connect()) as db:
        article(db, 'news')
    assert worker.process_one('key', 'gpt-6-luna', provider='openai-decisions')
    with closing(connect()) as db, db:
        db.execute('UPDATE topic_checks SET updated_at=0')
        assert db.execute('SELECT status FROM item_classifications').fetchone()[0] == 'pending'
    service.process_one()
    assert worker.process_one('key', 'gpt-6-luna', provider='openai-decisions')
    detail = service.detail(ident)
    assert detail['total'] == 1
    assert len(detail['nodes']) == 1 + int(choice == '新增节点')
    with closing(connect()) as db:
        saved = json.loads(db.execute('SELECT result_json FROM topic_checks').fetchone()[0])
    assert saved['provider'] == saved['horizontal']['provider'] == 'openai-decisions'
    assert calls[-1][1]['input'][0]['content'][0]['data']['summary'] == '监管机构已批准本次交易'
    assert len(agent_calls) == 2


@pytest.mark.parametrize('code,reason,blocked,limited', [(429, 'insufficient_quota', True, False), (429, 'rate_limit_exceeded', False, True), (401, 'invalid_api_key', True, False)])
def test_errors_are_redacted_and_quota_does_not_loop_as_rate_limit(monkeypatch, code, reason, blocked, limited):
    def fail(*args, **kwargs):
        raise HTTPError('url', code, 'secret', {'Retry-After': '31'}, io.BytesIO(json.dumps({'error': {'code': reason, 'message': 'private-secret'}}).encode()))
    monkeypatch.setattr(nc, 'urlopen', fail)
    with pytest.raises(nc.ClassificationError) as error:
        nc.classify('title', 'private-secret', 'gpt-6-luna', provider='openai-decisions')
    assert error.value.block_config is blocked and error.value.rate_limited is limited
    assert 'secret' not in str(error.value)
    if limited:
        assert error.value.retry_after == 31


@pytest.mark.parametrize('old_provider,new_provider', [('jev', 'openai-decisions'), ('openai-decisions', 'jev')])
def test_switch_preserves_inflight_result_and_does_not_repeat_completed_rows(conn, monkeypatch, old_provider, new_provider):
    insert(conn, 'z', 'old')
    insert(conn, 'a', 'next')
    nc.enqueue(conn)
    conn.commit()
    path = conn.execute('PRAGMA database_list').fetchone()[2]
    def connect():
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        return db
    entered, release = threading.Event(), threading.Event()
    seen = []
    def send(request, **kwargs):
        body = json.loads(request.data)
        seen.append((request.full_url, request.get_header('Authorization')))
        if len(seen) == 1:
            entered.set()
            assert release.wait(3)
        if 'state' in body:
            return io.StringIO(json.dumps(response()))
        return io.StringIO(json.dumps(decisions_response(body['questions'])))
    monkeypatch.setattr(nc, 'urlopen', send)
    models = {'jev': 'jev-latest', 'openai-decisions': 'gpt-6-luna'}
    service = nc.Service(connect, lambda: ('new-key', models[new_provider], new_provider))
    worker = threading.Thread(target=lambda: service.process_one('old-key', models[old_provider], provider=old_provider))
    worker.start()
    try:
        assert entered.wait(2)
        service.configure(8, provider=new_provider)
        release.set()
        worker.join(3)
        assert not worker.is_alive()
        assert service.process_one('new-key', models[new_provider], provider=new_provider)
        assert not service.process_one('new-key', models[new_provider], provider=new_provider)
        assert nc.status(conn)['done'] == 2
        assert {r[0] for r in conn.execute('SELECT provider FROM item_classifications')} == {'jev', 'openai-decisions'}
        urls = {'jev': 'https://api.typesafe.ai/v1/systemone', 'openai-decisions': 'https://api.openai.com/v1/decisions'}
        assert seen == [(urls[old_provider], 'Bearer old-key'), (urls[new_provider], 'Bearer new-key')]
    finally:
        release.set()
        worker.join(3)


def test_late_error_from_previous_provider_cannot_pause_new_provider(conn):
    insert(conn)
    nc.enqueue(conn)
    conn.commit()
    def runner(*args):
        service.configure(8, provider='openai-decisions')
        raise nc.ClassificationError('http_401', block_config=True)
    service = nc.Service(lambda: sqlite3.connect(conn.execute('PRAGMA database_list').fetchone()[2]), lambda: None, runner=runner)
    def connect():
        db = sqlite3.connect(conn.execute('PRAGMA database_list').fetchone()[2])
        db.row_factory = sqlite3.Row
        return db
    service.connect = connect
    assert service.process_one('old-key', 'old-model')
    assert service.blocked_config is None and service.error == ''
    assert conn.execute('SELECT attempts FROM item_classifications').fetchone()[0] == 0


def test_settings_credentials_catalog_probe_and_status_follow_selected_provider(api_fixture, monkeypatch):
    app, client, secrets = api_fixture
    secrets.update(TYPESAFE_API_KEY='typesafe-key', OPENAI_API_KEY='openai-key')
    initial = client.get('/api/settings').json
    assert initial['classification']['provider'] == 'jev'
    result = client.put('/api/settings', json={'llm': initial['llm'], 'classification': {'provider': 'openai-decisions'}})
    assert result.status_code == 200
    assert app.classification_config() == ('openai-key', 'gpt-6-luna', 'openai-decisions')
    assert client.get('/api/news/classification').json['provider_label'] == 'OpenAI Decisions'
    secrets.pop('OPENAI_API_KEY')
    assert app.classification_config() == ('', 'gpt-6-luna', 'openai-decisions')
    assert not client.get('/api/topics').json['matching_configured']
    assert not client.get('/api/tasks/progress').json['topics']['matching_configured']
    secrets['OPENAI_API_KEY'] = 'openai-key'
    monkeypatch.setenv('OPENAI_API_KEY', 'environment-key')
    assert app.classification_config()[0] == 'environment-key'
    monkeypatch.delenv('OPENAI_API_KEY')
    calls = []
    def send(request, **kwargs):
        calls.append(request)
        if request.data:
            return Reply(decisions_response(json.loads(request.data)['questions']))
        return Reply({'data': [{'id': 'gpt-6-luna'}, {'id': 'unrelated-model'}]})
    monkeypatch.setattr(app, 'urlopen', send)
    catalog = client.post('/api/settings/apis/openai-decisions/models').json
    assert [m['value'] for m in catalog['catalog']['options']] == ['gpt-6-luna']
    assert client.post('/api/settings/apis/openai-decisions/test', json={}).status_code == 200
    assert calls[-1].full_url == 'https://api.openai.com/v1/decisions'
    assert calls[-1].get_header('Authorization') == 'Bearer openai-key'
    assert client.post('/api/settings/apis/openai-decisions/test', json={'model': 'unrelated-model'}).status_code == 400
    assert client.put('/api/settings', json={'llm': initial['llm'], 'classification': {'concurrency': 2}}).json['classification']['provider'] == 'openai-decisions'
    assert 'openai-key' not in json.dumps(app.load_app_settings())


def test_combined_classification_and_multiple_independent_topic_answers(env, monkeypatch):
    connect, service, _ = env
    topics = [ready(service, f'create-{i}') for i in range(4)]
    choices = ['属于', '属于', '不属于', '信息不足']
    calls = fake_decisions(monkeypatch, {'tracked_' + ident: choice for ident, choice in zip(topics, choices)})
    with closing(connect()) as db:
        article(db, 'news')
    worker = nc.Service(connect, lambda: None, topic_queue=tt.MatchQueue())
    assert worker.process_one('key', 'gpt-6-luna', provider='openai-decisions')
    assert len(calls) == 1 and len(calls[0][1]['questions']) == 6
    with closing(connect()) as db:
        classification = dict(db.execute('SELECT * FROM item_classifications').fetchone())
        matches = {r['topic_id']: r['matched'] for r in db.execute('SELECT * FROM topic_checks')}
    assert classification['status'] == 'done' and classification['provider'] == 'openai-decisions'
    assert [matches[ident] for ident in topics] == [1, 1, 0, 0]


def test_old_database_migration_preserves_classification_and_reading_state(conn):
    insert(conn)
    nc.enqueue(conn)
    conn.commit()
    job = nc.claim(conn)
    assert nc.finish(conn, job, nc.parse_response(response()))
    with conn:
        conn.execute("INSERT INTO item_state(item_id,read_at,updated_at) VALUES ('a','read-before','before')")
        conn.execute('ALTER TABLE item_classifications DROP COLUMN provider')
    before = dict(conn.execute('SELECT * FROM item_classifications').fetchone())
    schema = Path(__file__).resolve().parents[1] / 'schema.sql'
    apply_schema(conn, schema)
    apply_schema(conn, schema)
    after = dict(conn.execute('SELECT * FROM item_classifications').fetchone())
    assert after.pop('provider') == 'jev'
    assert after == before
    assert conn.execute("SELECT read_at FROM item_state WHERE item_id='a'").fetchone()[0] == 'read-before'
    assert nc.enqueue(conn) == 0


def test_stale_worker_snapshot_cannot_claim_after_switch(conn):
    insert(conn)
    nc.enqueue(conn)
    conn.commit()
    service = nc.Service(lambda: pytest.fail('Stale configuration must not claim work'), lambda: None)
    old_revision = service.config_revision
    service.configure(8, provider='openai-decisions')
    assert not service.process_one('old-key', 'old-model', config_revision=old_revision)
    assert conn.execute('SELECT attempts FROM item_classifications').fetchone()[0] == 0


def test_background_loop_dispatches_selected_provider(conn, monkeypatch):
    insert(conn)
    nc.enqueue(conn)
    conn.commit()
    path = conn.execute('PRAGMA database_list').fetchone()[2]
    def connect():
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        return db
    calls = []
    def send(request, **kwargs):
        calls.append(request.full_url)
        service.stop.set()
        return io.StringIO(json.dumps(decisions_response(json.loads(request.data)['questions'])))
    monkeypatch.setattr(nc, 'urlopen', send)
    service = nc.Service(connect, lambda: ('key', 'gpt-6-luna', 'openai-decisions'))
    service.configure(1, provider='openai-decisions')
    service.start()
    try:
        for worker in service.threads:
            worker.join(3)
            assert not worker.is_alive()
        assert calls == ['https://api.openai.com/v1/decisions']
        assert nc.status(conn)['done'] == 1
    finally:
        service.stop.set()


@pytest.mark.parametrize('provider', [None, [], {}, 'deepseek', 'unknown'])
def test_invalid_provider_cannot_mutate_settings(api_fixture, provider):
    app, client, _ = api_fixture
    before = app.load_app_settings()
    result = client.put('/api/settings', json={'llm': before['llm'], 'classification': {'provider': provider}})
    assert result.status_code == 400
    assert app.load_app_settings() == before
