import importlib
import json
import sqlite3
import threading

import pytest
import settings
import news_classification as nc
from test_model_apis import api_fixture
from test_news_classification import api, conn, insert, response


def test_legacy_settings_migrate_without_losing_model_or_scope(tmp_path, monkeypatch):
    path = tmp_path / 'settings.json'
    monkeypatch.setenv('NEWS_READER_APP_SETTINGS_PATH', str(path))
    legacy = {'api_models': {'jev': {'model': 'jev-custom', 'concurrency': 12, 'unread_only': True}}}
    path.write_text(json.dumps(legacy))
    loaded = settings.load_app_settings()
    assert loaded['api_models'] == {'jev': {'model': 'jev-custom'}}
    assert loaded['classification'] == {'enabled': True, 'concurrency': 12, 'unread_only': True}
    legacy['classification'] = {'enabled': False, 'concurrency': 3, 'unread_only': False}
    path.write_text(json.dumps(legacy))
    loaded = settings.load_app_settings()
    assert loaded['classification'] == legacy['classification']
    settings.save_app_settings(loaded)
    assert json.loads(path.read_text())['api_models'] == {'jev': {'model': 'jev-custom'}}
    assert settings.load_app_settings()['classification'] == legacy['classification']


def test_switch_persists_applies_live_and_survives_api_only_writes_and_restart(api_fixture):
    app, client, _ = api_fixture
    config = {'enabled': False, 'unread_only': True, 'concurrency': 3}
    body = {'llm': app.load_app_settings()['llm'], 'classification': config}
    saved = client.put('/api/settings', json=body)
    assert saved.status_code == 200 and saved.json['classification'] == config
    assert app.CLASSIFICATION_SERVICE.enabled is False
    assert app.CLASSIFICATION_SERVICE.unread_only is True
    assert app.CLASSIFICATION_SERVICE.threads == []
    result = client.put('/api/settings', json={
        'llm': body['llm'], 'api_models': {'jev': {'model': 'jev-next'}},
    })
    assert result.status_code == 200 and result.json['classification'] == config
    assert result.json['api_models'] == {'jev': {'model': 'jev-next'}}
    assert client.get('/api/news/classification').json['enabled'] is False
    assert client.post('/api/news/classification/retry').status_code == 409
    app = importlib.reload(app)
    assert app.CLASSIFICATION_SERVICE.enabled is False
    assert app.CLASSIFICATION_SERVICE.unread_only is True
    assert app.CLASSIFICATION_SERVICE.gate.snapshot()['configured_concurrency'] == 3


@pytest.mark.parametrize('value', [None, 0, 1, 'true', 'false', [], {}])
def test_invalid_enabled_does_not_save_or_change_running_service(api_fixture, value):
    app, client, _ = api_fixture
    before = app.load_app_settings()
    result = client.put('/api/settings', json={'llm': before['llm'], 'classification': {'enabled': value}})
    assert result.status_code == 400 and result.json['error'] == 'invalid_classification_enabled'
    assert app.load_app_settings() == before
    assert app.CLASSIFICATION_SERVICE.enabled is True


@pytest.mark.parametrize('value', [None, False, [], 'enabled'])
def test_invalid_classification_module_is_rejected(api_fixture, value):
    app, client, _ = api_fixture
    before = app.load_app_settings()
    result = client.put('/api/settings', json={'llm': before['llm'], 'classification': value})
    assert result.status_code == 400 and result.json['error'] == 'invalid_classification_settings'
    assert app.load_app_settings() == before


def test_disabling_allows_inflight_completion_keeps_queue_and_resumes(conn):
    for ident in ['z-inflight', 'y-waiting']:
        insert(conn, ident, ident)
    with conn:
        nc.enqueue(conn)
    path = conn.execute('PRAGMA database_list').fetchone()[2]
    def connect():
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        return db
    entered, release = threading.Event(), threading.Event()
    seen = []
    def runner(title, key, model):
        seen.append(title)
        if title == 'z-inflight':
            entered.set()
            assert release.wait(3)
        return nc.parse_response(response())
    service = nc.Service(connect, lambda: ('key', 'model'), runner=runner)
    worker = threading.Thread(target=lambda: service.process_one('key', 'model'))
    worker.start()
    try:
        assert entered.wait(2)
        service.configure(8, enabled=False)
        release.set()
        worker.join(2)
        assert not worker.is_alive()
        assert nc.status(conn)['done'] == 1
        insert(conn, 'new-while-disabled', 'new title')
        with conn:
            nc.enqueue(conn)
        assert not service.process_one('key', 'model')
        assert seen == ['z-inflight']
        assert nc.status(conn)['pending'] == 2
        assert conn.execute("SELECT SUM(attempts) FROM item_classifications WHERE status='pending'").fetchone()[0] == 0
        service.configure(8, enabled=True)
        assert service.process_one('key', 'model')
        assert service.process_one('key', 'model')
        assert nc.status(conn)['done'] == 3
        assert len(seen) == len(set(seen)) == 3
    finally:
        release.set()
        worker.join(3)


def test_disabled_workers_do_not_fetch_credentials_or_call_model(conn):
    def unexpected(*args):
        pytest.fail('Disabled classification must not fetch credentials or call a model')
    service = nc.Service(unexpected, unexpected, runner=unexpected)
    service.configure(2, enabled=False)
    service.start()
    try:
        assert not service.process_one('key', 'model')
        assert not service.error
    finally:
        service.stop.set()
        for thread in service.threads:
            thread.join(2)


def test_disabled_feature_retains_categories_and_retry_cannot_modify_the_queue(api):
    app, client = api
    with app.db_conn() as db:
        db.execute("UPDATE item_classifications SET status='failed',attempts=4 WHERE item_id='n00'")
    before = client.get('/api/news?category=economy&read_filter=all').json
    result = client.put('/api/settings', json={
        'llm': app.load_app_settings()['llm'], 'classification': {'enabled': False},
    })
    assert result.status_code == 200
    assert client.get('/api/news?category=economy&read_filter=all').json['items'] == before['items']
    assert client.post('/api/news/classification/retry').json['error'] == 'classification_disabled'
    assert client.get('/api/news/classification').json['failed'] == 1
    client.put('/api/settings', json={
        'llm': app.load_app_settings()['llm'], 'classification': {'enabled': True},
    })
    assert client.post('/api/news/classification/retry').json['retried'] == 1
