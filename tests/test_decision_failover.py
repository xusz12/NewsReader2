import io
import json
import sqlite3
import threading
from contextlib import closing

import pytest

import decision_protocol
import news_classification as nc
import topic_tracking as tt
from test_decision_providers import decisions_response
from test_model_apis import api_fixture
from test_news_classification import conn, insert, response
from test_topic_tracking import article, env, ready


MODELS = {'jev': 'jev-latest', 'openai-decisions': 'gpt-6-luna'}


def config(provider):
    return 'key-' + provider, MODELS[provider], provider


def db_factory(conn):
    path = conn.execute('PRAGMA database_list').fetchone()[2]
    def connect():
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        return db
    return connect


def enqueue(conn, ident):
    insert(conn, ident, ident)
    with conn:
        nc.enqueue(conn)


def run(service, provider):
    key, model, _ = config(provider)
    return service.process_one(key, model, provider=provider)


@pytest.mark.parametrize('primary', decision_protocol.PROVIDERS)
def test_failover_and_single_recovery_probe_with_concurrent_work(conn, primary):
    backup = decision_protocol.alternate(primary)
    now = [1000]
    calls = []
    probing, release = threading.Event(), threading.Event()
    def runner(title, key, model, provider='jev'):
        calls.append((title, provider))
        assert (key, model, provider) == config(provider)
        if provider == primary and now[0] == 1000:
            raise nc.ClassificationError('http_429', retryable=True, retry_after=78)
        if title == 'probe' and provider == primary:
            probing.set()
            assert release.wait(4)
        return {**nc.parse_response(response()), 'provider': provider, 'model': model}

    service = nc.Service(db_factory(conn), lambda: config(primary), runner=runner, backup_config=config, clock=lambda: now[0])
    service.configure(8, provider=primary, fallback_enabled=True)
    enqueue(conn, 'first')
    assert run(service, primary)
    now[0] += 77
    enqueue(conn, 'cooldown')
    assert run(service, primary)
    assert calls == [('first', primary), ('first', backup), ('cooldown', backup)]
    now[0] += 1
    enqueue(conn, 'probe')
    worker = threading.Thread(target=run, args=(service, primary))
    worker.start()
    try:
        assert probing.wait(2)
        enqueue(conn, 'during-probe')
        assert run(service, primary)
    finally:
        release.set()
        worker.join(4)
    enqueue(conn, 'after-recovery')
    assert run(service, primary)
    assert calls[-3:] == [('probe', primary), ('during-probe', backup), ('after-recovery', primary)]
    result = {r['item_id']: (r['provider'], r['attempts'], r['status']) for r in conn.execute('SELECT * FROM item_classifications')}
    assert result == {title: (provider, 1, 'done') for title, provider in calls}
    assert not service.lanes[primary].last_error


def test_both_unavailable_back_off_independently_without_claiming_idle_work(conn):
    now = [1000]
    calls = []
    errors = {'jev': nc.ClassificationError('http_429', retryable=True, retry_after=180),
              'openai-decisions': nc.ClassificationError('http_503', retryable=True)}
    def runner(title, key, model, provider='jev'):
        calls.append(provider)
        if provider in errors:
            raise errors[provider]
        return {**nc.parse_response(response()), 'provider': provider}
    service = nc.Service(db_factory(conn), lambda: config('jev'), runner, backup_config=config, clock=lambda: now[0])
    service.configure(8, fallback_enabled=True)
    enqueue(conn, 'first')
    assert run(service, 'jev')
    enqueue(conn, 'second')
    assert not run(service, 'jev')
    assert not run(service, 'jev')
    assert calls == ['jev', 'openai-decisions']
    assert conn.execute("SELECT attempts FROM item_classifications WHERE item_id='second'").fetchone()[0] == 0
    now[0] += 30
    del errors['openai-decisions']
    assert run(service, 'jev')
    assert calls[-1] == 'openai-decisions'
    now[0] += 150
    del errors['jev']
    enqueue(conn, 'third')
    assert run(service, 'jev')
    assert calls[-1] == 'jev'


@pytest.mark.parametrize('code,should_switch', [('network_error', True), ('http_401', True), ('insufficient_quota', True),
    ('invalid_response', False), ('decision_refused', False), ('http_422', False)])
def test_only_provider_unavailability_switches(conn, code, should_switch):
    calls = []
    def runner(title, key, model, provider='jev'):
        calls.append(provider)
        if provider == 'jev':
            raise nc.ClassificationError(code, retryable=code in {'network_error', 'invalid_response'},
                                         block_config=code in {'http_401', 'http_422', 'insufficient_quota'})
        return {**nc.parse_response(response()), 'provider': provider}
    service = nc.Service(db_factory(conn), lambda: config('jev'), runner, backup_config=config)
    service.configure(8, fallback_enabled=True)
    enqueue(conn, 'test')
    assert run(service, 'jev')
    assert calls == (['jev', 'openai-decisions'] if should_switch else ['jev'])
    row = conn.execute('SELECT category, attempts FROM item_classifications').fetchone()
    assert row[1] == 1 and (row[0] is not None) == should_switch


@pytest.mark.parametrize('mode', ['off', 'disabled-during-call', 'missing-backup'])
def test_no_unauthorized_second_call(conn, mode):
    backup_reads, calls = [], []
    def backup(provider):
        backup_reads.append(provider)
        return '', MODELS[provider], provider
    def runner(title, key, model, provider='jev'):
        calls.append(provider)
        if mode == 'disabled-during-call':
            service.configure(8, fallback_enabled=False)
        raise nc.ClassificationError('network_error', retryable=True)
    service = nc.Service(db_factory(conn), lambda: config('jev'), runner, backup_config=backup)
    service.configure(8, fallback_enabled=mode != 'off')
    enqueue(conn, 'test')
    assert run(service, 'jev')
    assert calls == ['jev']
    assert backup_reads == (['openai-decisions'] if mode == 'missing-backup' else [])
    assert dict(conn.execute('SELECT attempts,status FROM item_classifications').fetchone()) == {'attempts':1, 'status':'pending'}


def test_background_topic_match_and_milestone_use_backup_with_actual_credentials(env, monkeypatch):
    connect, topics, _ = env
    ident = ready(topics)
    calls = []
    def send(req, **kwargs):
        calls.append((req.full_url, req.get_header('Authorization')))
        if 'typesafe.ai' in req.full_url:
            raise TimeoutError()
        body = json.loads(req.data)
        return io.StringIO(json.dumps(decisions_response(body['questions'])))
    monkeypatch.setattr(nc, 'urlopen', send)
    service = nc.Service(connect, lambda: config('jev'), backup_config=config, topic_queue=tt.MatchQueue())
    service.configure(1, enabled=False, fallback_enabled=True)
    with closing(connect()) as db:
        article(db, 'test')
    assert run(service, 'jev')
    with closing(connect()) as db, db:
        db.execute('UPDATE topic_checks SET updated_at=0')
    topics.process_one()
    assert run(service, 'jev')
    with closing(connect()) as db:
        result = json.loads(db.execute('SELECT result_json FROM topic_checks').fetchone()[0])
        assert result['provider'] == result['horizontal']['provider'] == 'openai-decisions'
        assert db.execute('SELECT status FROM item_classifications').fetchone()[0] == 'pending'
    assert topics.detail(ident)['total'] == 1
    assert calls == [('https://api.typesafe.ai/v1/systemone', 'Bearer key-jev'),
                     ('https://api.openai.com/v1/decisions', 'Bearer key-openai-decisions'),
                     ('https://api.openai.com/v1/decisions', 'Bearer key-openai-decisions')]


def test_settings_persistence_and_live_route_when_primary_key_missing(api_fixture):
    app, client, keys = api_fixture
    keys['OPENAI_API_KEY'] = 'backup-key'
    saved = client.get('/api/settings').json
    assert saved['classification']['fallback_enabled'] is False
    assert not client.get('/api/news/classification').json['configured']
    put = lambda classification: client.put('/api/settings', json={'llm': saved['llm'], 'classification': classification})
    assert put({'fallback_enabled': 'false'}).status_code == 400
    assert put({'fallback_enabled': True}).status_code == 200
    assert put({'concurrency': 2}).json['classification']['fallback_enabled']
    assert app.load_app_settings()['classification']['provider'] == 'jev'
    progress = client.get('/api/news/classification').json
    assert progress['configured'] and progress['using_backup'] and progress['active_provider'] == 'openai-decisions'
    assert client.get('/api/topics').json['matching_configured']
    keys.clear()
    assert client.get('/api/news/classification').json['waiting_for_service']
    keys['TYPESAFE_API_KEY'] = 'restored-key'
    assert client.get('/api/news/classification').json['active_provider'] == 'jev'


def test_late_failure_cannot_reopen_recovered_primary(conn):
    began, release = threading.Event(), threading.Event()
    now = [1000]
    calls = []
    def runner(title, key, model, provider='jev'):
        calls.append((title, provider))
        if title == 'slow' and provider == 'jev':
            began.set()
            assert release.wait(4)
            raise nc.ClassificationError('http_503', retryable=True)
        if title == 'outage' and provider == 'jev':
            raise nc.ClassificationError('network_error', retryable=True)
        return {**nc.parse_response(response()), 'provider': provider}
    service = nc.Service(db_factory(conn), lambda: config('jev'), runner, backup_config=config, clock=lambda: now[0])
    service.configure(2, fallback_enabled=True)
    enqueue(conn, 'slow')
    thread = threading.Thread(target=run, args=(service, 'jev'))
    thread.start()
    try:
        assert began.wait(2)
        enqueue(conn, 'outage')
        assert run(service, 'jev')
        now[0] += 30
        enqueue(conn, 'recovered')
        assert run(service, 'jev')
        assert calls[-1] == ('recovered', 'jev')
    finally:
        release.set()
        thread.join(4)
    enqueue(conn, 'next')
    assert run(service, 'jev')
    assert calls[-1] == ('next', 'jev')
    assert nc.status(conn)['done'] == 4


def test_saved_settings_route_a_background_worker_and_report_actual_provider(api_fixture, monkeypatch):
    app, client, keys = api_fixture
    keys.update(TYPESAFE_API_KEY='first-key', OPENAI_API_KEY='second-key')
    body = {'llm': app.load_app_settings()['llm'], 'classification': {'concurrency':1, 'fallback_enabled':True}}
    assert client.put('/api/settings', json=body).status_code == 200
    with closing(app.db_conn()) as db:
        enqueue(db, 'live-routing-fixture')
    calls = []
    worker = app.CLASSIFICATION_SERVICE
    def send(req, **kwargs):
        calls.append((req.full_url, req.get_header('Authorization')))
        if req.full_url.startswith('https://api.typesafe.ai/'):
            raise TimeoutError()
        worker.stop.set()
        questions = json.loads(req.data)['questions']
        return io.StringIO(json.dumps(decisions_response(questions)))
    monkeypatch.setattr(nc, 'urlopen', send)
    worker.start()
    try:
        worker.threads[0].join(4)
        assert not worker.threads[0].is_alive()
        assert calls == [('https://api.typesafe.ai/v1/systemone', 'Bearer first-key'),
                         ('https://api.openai.com/v1/decisions', 'Bearer second-key')]
        with closing(app.db_conn()) as db:
            assert db.execute("SELECT COUNT(*) FROM item_classifications WHERE status='done' AND provider='openai-decisions'").fetchone()[0] == 1
        progress = client.get('/api/news/classification').json
        assert progress['using_backup'] and progress['provider'] == 'jev' and progress['provider_label'] == 'OpenAI Decisions'
        assert client.get('/api/tasks/progress').json['topics']['using_backup']
    finally:
        worker.stop.set()
        worker.threads[0].join(4)
