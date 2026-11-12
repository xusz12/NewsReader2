"""Real library/settings/queue integration, with no browser or model calls."""
import json
import sqlite3
import subprocess
from contextlib import closing

import pytest

import collection_translation
import x_accounts
from test_news_collection import api


def snapshot(module):
    with closing(module.db_conn()) as conn:
        return x_accounts.snapshot(conn)


def save(module, client, accounts, previous=None):
    return client.put('/api/settings', json={
        'llm': module.current_runtime_settings()['llm'], **(previous or snapshot(module)), 'x_accounts': accounts,
    })


def remove(client, handle='ilyasut', delete=False, preview=None):
    data = preview or client.get('/api/settings/x-accounts/' + handle).json
    return client.delete('/api/settings/x-accounts/' + handle, json={
        'delete_history': delete, 'x_accounts_revision': data['x_accounts_revision'],
        'confirmation_token': data['source']['confirmation_token'],
    })


@pytest.mark.parametrize('value', ['Name_123', '@NAME_123', 'https://x.com/Name_123/', 'twitter.com/name_123?lang=en'])
def test_handle_normalization(value):
    assert x_accounts.normalize_handle(value) == 'name_123'


@pytest.mark.parametrize('value', ['', '@@foo', 'long_account_name_123', 'bad name', 'https://x.com/a/status/1',
                                  'https://example.com/a', 'https://x.com.evil/a', '$(pwd)', 'home', None])
def test_invalid_handle_rejected(value):
    with pytest.raises(ValueError):
        x_accounts.normalize_handle(value)


def test_account_probe_uses_real_collection_normalizer_without_saving(api, monkeypatch):
    module, client = api
    previous = snapshot(module)
    calls = []
    def command(args, *, timeout):
        calls.append((args, timeout))
        return subprocess.CompletedProcess(args, 0, json.dumps([
            {'id':'12345', 'author':'exampleuser', 'text':'Actual content', 'created_at':'2026-11-12T01:00:00Z'},
            {'id':'12346', 'author':'anotheruser', 'text':'Retweeted content'},
            {'id':'12347', 'author':'exampleuser', 'text':''},
        ]), '')
    monkeypatch.setattr(module.news_collection.adapters, 'run_command', command)
    response = client.post('/api/settings/x-accounts/test', json={
        'handle':'https://x.com/ExampleUser', 'x_accounts_revision':previous['x_accounts_revision']})
    assert response.status_code == 200, response.json
    assert response.json == {'ok':True, 'handle':'exampleuser', 'count':2}
    assert calls == [(['opencli','twitter','tweets','exampleuser','--limit','3','-f','json',
                      '--window','background','--site-session','persistent'], 45)]
    assert snapshot(module) == previous
    with closing(module.db_conn()) as conn:
        for table in ('items','item_state','collection_runs','collection_items','detail_jobs','ai_jobs'):
            assert conn.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] == 0


@pytest.mark.parametrize('stdout,returncode,error', [
    ('[]', 0, 'x_account_empty'),
    ('[{"id":"123","text":"","author":"exampleuser"}]', 0, 'x_account_empty'),
    ('[{"title":"not a tweet","url":"https://example.com/article"}]', 0, 'x_account_empty'),
    ('not json', 0, 'INVALID_JSON'),
    ('{"ok":false,"error":{"code":"AUTH_REQUIRED","message":"private details"}}', 1, 'AUTH_REQUIRED'),
    ('{"ok":false,"error":{"code":"NOT_FOUND","message":"private details"}}', 1, 'x_account_unavailable'),
    ('{"ok":false,"error":{"code":"OTHER_ERROR","message":"private details"}}', 1, 'x_account_test_failed'),
])
def test_account_probe_requires_content_and_never_returns_raw_cli_errors(api, monkeypatch, stdout, returncode, error):
    module, client = api
    previous = snapshot(module)
    monkeypatch.setattr(module.news_collection.adapters, 'run_command',
        lambda args, **kwargs: subprocess.CompletedProcess(args, returncode, stdout, 'private details'))
    response = client.post('/api/settings/x-accounts/test', json={
        'handle':'exampleuser', 'x_accounts_revision':previous['x_accounts_revision']})
    assert response.status_code == 422
    assert response.json == {'ok':False, 'error':error}
    assert 'private details' not in response.get_data(as_text=True)
    assert snapshot(module) == previous


@pytest.mark.parametrize('failure,error', [(subprocess.TimeoutExpired('opencli',45),'TIMEOUT'),
                                         (ValueError('private details'),'x_account_test_failed')])
def test_account_probe_handles_timeout_and_unexpected_failure(api, monkeypatch, failure, error):
    module, client = api
    def command(*args, **kwargs):
        raise failure
    monkeypatch.setattr(module.news_collection.adapters, 'run_command', command)
    response = client.post('/api/settings/x-accounts/test', json={
        'handle':'exampleuser', 'x_accounts_revision':snapshot(module)['x_accounts_revision']})
    assert response.json == {'ok':False, 'error':error}


def test_account_probe_rejects_invalid_duplicate_and_stale_context_without_fetching(api, monkeypatch):
    module, client = api
    monkeypatch.setattr(module.news_collection, 'acquire_source', lambda _: pytest.fail('must not fetch'))
    revision = snapshot(module)['x_accounts_revision']
    for body, error in [([], 'invalid_x_handle'), ({'handle':'bad name', 'x_accounts_revision':revision},'invalid_x_handle'),
                        ({'handle':'@ILYASUT','x_accounts_revision':revision},'duplicate_x_account'),
                        ({'handle':'exampleuser','x_accounts_revision':'stale'},'x_accounts_changed')]:
        response = client.post('/api/settings/x-accounts/test', json=body)
        assert response.json == {'ok':False, 'error':error}


def test_account_probe_rechecks_context_after_browser_returns(api, monkeypatch):
    module, client = api
    revision = snapshot(module)['x_accounts_revision']
    def acquire(source):
        with closing(module.db_conn()) as conn, conn:
            x_accounts.changed(conn)
        return {'ok':True, 'items':[{'title':'Content','url':'https://x.com/exampleuser/status/123'}]}
    monkeypatch.setattr(module.news_collection, 'acquire_source', acquire)
    response = client.post('/api/settings/x-accounts/test', json={'handle':'exampleuser','x_accounts_revision':revision})
    assert response.status_code == 409
    assert response.json['error'] == 'x_accounts_changed'
    assert all(a['handle'] != 'exampleuser' for a in snapshot(module)['x_accounts'])


def test_defaults_save_restart_duplicates_stale_and_empty_list(api):
    module, client = api
    initial = snapshot(module)
    assert len(initial['x_accounts']) == 4
    assert all(not a['auto_translate'] and not a['auto_fulltext'] for a in initial['x_accounts'])
    accounts = initial['x_accounts'] + [{'handle': 'https://x.com/ExampleUser', 'auto_translate': True}]
    response = save(module, client, accounts)
    assert response.status_code == 200, response.json
    assert response.json['x_accounts'][-1] == {'handle': 'exampleuser', 'name': 'exampleuser', 'auto_translate': True, 'auto_fulltext': False}
    assert save(module, client, accounts, initial).status_code == 409
    current = snapshot(module)
    assert save(module, client, current['x_accounts'] + [{'handle': '@EXAMPLEUSER'}]).json['error'] == 'duplicate_x_account'
    assert save(module, client, []).json['error'] == 'x_account_removal_required'
    module.ensure_db()
    assert snapshot(module) == current
    for account in current['x_accounts']:
        assert remove(client, account['handle']).status_code == 200
    module.ensure_db()
    assert snapshot(module)['x_accounts'] == []
    assert len(module.COLLECTION_SERVICE.sources) == 8
    assert save(module, client, current['x_accounts'], current).status_code == 409


def prepare_collection(module, *, translate=False, fulltext=False):
    with closing(module.db_conn()) as conn, conn:
        conn.execute("DELETE FROM x_accounts WHERE handle<>'ilyasut'")
        conn.execute('UPDATE x_accounts SET auto_translate=?,auto_fulltext=?', (translate, fulltext))
    service = module.COLLECTION_SERVICE
    service.fixed_sources = []
    service.collector = lambda source: {'ok': True, 'items': [{
        'url': 'https://x.com/ilyasut/status/10001', 'title': 'New research 中文',
        'time': '2026-11-12 12:00:00', 'quoted_text_raw': 'English quotation',
    }]}
    return service


def collect(service):
    service.request()
    assert service.collect_once()
    while service.translate_once():
        pass
    assert service.publish_once()


def detail_payload(url):
    return True, {'source': 'Twitter/X', 'title': 'Title', 'author': 'Ilya', 'published_at': '',
                  'content': 'English text 中文', 'content_length': 15, 'raw_json': '{}'}, ''


@pytest.mark.parametrize('translate,fulltext', [(False, False), (False, True), (True, False), (True, True)])
def test_independent_switches_and_prefetch_never_changes_reading_state(api, monkeypatch, translate, fulltext):
    module, client = api
    service = prepare_collection(module, translate=translate, fulltext=fulltext)
    calls = []
    def model(raw, settings, provider):
        calls.append(raw)
        return {'title': '新研究 中文', 'summary': '', 'quoted_text': '英文引用'}
    monkeypatch.setattr(collection_translation, 'http_translate', model)
    collect(service)
    with closing(module.db_conn()) as conn:
        item = dict(conn.execute('SELECT * FROM items').fetchone())
        jobs = conn.execute('SELECT * FROM detail_jobs').fetchall()
        assert len(jobs) == int(fulltext)
        assert not conn.execute('SELECT * FROM item_state').fetchall()
    assert len(calls) == int(translate)  # Mixed Chinese/English is never skipped.
    assert item['title'] == ('新研究 中文' if translate else 'New research 中文')
    monkeypatch.setattr(module, 'run_opencli_detail', detail_payload)
    if fulltext:
        assert jobs[0]['auto_translate'] == int(translate)
        assert module.process_pending_jobs_once()
        with closing(module.db_conn()) as conn:
            assert conn.execute('SELECT COUNT(*) FROM ai_jobs').fetchone()[0] == int(translate)
            assert not conn.execute('SELECT * FROM item_state').fetchall()
        for query in ('all', 'unread', 'read'):
            assert client.get('/api/news?collection=read_later&read_filter=' + query).json['total'] == 0
        assert client.get('/api/nav-summary').json['summary']['read_later_unread'] == 0
        assert client.get('/api/sources?collection=read_later&read_filter=unread&include_catalog=1').json['sources'] == []
    else:
        # Later manual full-text fetching reads this account's saved switch.
        assert client.post('/api/news/' + item['id'] + '/detail/retry').status_code == 200
        assert module.process_pending_jobs_once()
        with closing(module.db_conn()) as conn:
            assert conn.execute('SELECT COUNT(*) FROM ai_jobs').fetchone()[0] == int(translate)
    result = client.patch('/api/news/' + item['id'] + '/state', json={'read_later': True}).json
    assert result['read_later_at'] and result['read_at']
    assert client.get('/api/news?collection=read_later&read_filter=unread').json['total'] == 1


def test_batch_options_are_snapshotted_and_failures_do_not_remove_published_news(api, monkeypatch):
    module, client = api
    service = prepare_collection(module, fulltext=True)
    service.request()
    with closing(module.db_conn()) as conn, conn:
        conn.execute('UPDATE x_accounts SET auto_translate=1,auto_fulltext=0')
    service.collect_once(); service.translate_once(); service.publish_once()
    monkeypatch.setattr(module, 'run_opencli_detail', lambda _: (False, {}, 'AUTH_REQUIRED'))
    module.process_pending_jobs_once()
    with closing(module.db_conn()) as conn:
        assert conn.execute('SELECT COUNT(*) FROM items').fetchone()[0] == 1
        assert conn.execute('SELECT auto_translate FROM detail_jobs').fetchone()[0] == 0
        assert not conn.execute('SELECT * FROM item_state').fetchall()


def test_enabled_translation_calls_model_for_chinese_and_preserves_original(api, monkeypatch):
    module, _ = api
    service = prepare_collection(module, translate=True)
    service.collector = lambda _: {'ok': True, 'items': [{
        'url': 'https://x.com/ilyasut/status/10002', 'title': '這是中文 OpenAI', 'time': '2026-11-12 12:00:00',
    }]}
    calls = []
    def model(raw, settings, provider):
        calls.append(raw)
        return collection_translation.original_fields(raw)
    monkeypatch.setattr(collection_translation, 'http_translate', model)
    collect(service)
    assert len(calls) == 1
    with closing(module.db_conn()) as conn:
        assert conn.execute('SELECT title FROM items').fetchone()[0] == '這是中文 OpenAI'


@pytest.mark.parametrize('delete', [False, True])
def test_remove_with_history_preservation_or_cleanup(api, monkeypatch, delete):
    module, client = api
    service = prepare_collection(module, fulltext=True)
    collect(service)
    monkeypatch.setattr(module, 'run_opencli_detail', detail_payload)
    module.process_pending_jobs_once()
    with closing(module.db_conn()) as conn, conn:
        ident, url = conn.execute('SELECT id,url FROM items').fetchone()
        conn.execute("INSERT INTO item_state(item_id,favorite_at,updated_at) VALUES (?,'saved','now')", (ident,))
        conn.execute("INSERT INTO article_notes VALUES (?,'note','now','now')", (url,))
    response = remove(client, delete=delete)
    assert response.status_code == 200, response.json
    assert response.json['x_accounts'] == []
    with closing(module.db_conn()) as conn:
        for table in ('items', 'article_details', 'item_state', 'article_notes'):
            assert conn.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] == int(not delete)
        if delete:
            assert conn.execute('SELECT deleted FROM news_identities').fetchone()[0] == 1
    assert not service.sources


@pytest.mark.parametrize('delete', [False, True])
def test_remove_account_without_history(api, delete):
    _, client = api
    assert remove(client, delete=delete).status_code == 200


def test_delete_during_collection_revokes_late_result_even_after_readding(api):
    module, client = api
    service = prepare_collection(module)
    original = service.collector
    def acquire(source):
        assert remove(client, delete=True).status_code == 200
        assert save(module, client, [{'handle': 'ilyasut'}]).status_code == 200
        return original(source)
    service.collector = acquire
    collect(service)
    with closing(module.db_conn()) as conn:
        assert conn.execute('SELECT COUNT(*) FROM items').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM collection_items').fetchone()[0] == 0


def test_delete_during_translation_revokes_late_result(api):
    module, client = api
    service = prepare_collection(module)
    def translate(raw, settings):
        assert remove(client, delete=True).status_code == 200
        return collection_translation.original_fields(raw)
    service.translator = translate
    collect(service)
    with closing(module.db_conn()) as conn:
        assert not conn.execute('SELECT * FROM items').fetchall()


def test_removal_rollback_and_stale_data_confirmation(api, monkeypatch):
    module, client = api
    collect(prepare_collection(module))
    preview = client.get('/api/settings/x-accounts/ilyasut').json
    with closing(module.db_conn()) as conn, conn:
        conn.execute("UPDATE items SET summary='changed'")
    assert remove(client, delete=True, preview=preview).status_code == 409
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('simulated transaction failure')
    monkeypatch.setattr(x_accounts, 'remove', fail)
    assert remove(client, delete=True).status_code == 500
    with closing(module.db_conn()) as conn:
        assert conn.execute('SELECT COUNT(*) FROM items').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM x_accounts').fetchone()[0] == 1


def test_migration_preserves_legacy_reading_queue_only_once(api):
    module, _ = api
    collect(prepare_collection(module))
    with closing(module.db_conn()) as conn, conn:
        ident, url = conn.execute('SELECT id,url FROM items').fetchone()
        conn.execute("INSERT INTO article_details(url,content,fetched_at,updated_at) VALUES (?,'body','old','old')", (url,))
        conn.execute("DELETE FROM news_version_migrations WHERE name='explicit_read_later'")
    module.ensure_db()
    with closing(module.db_conn()) as conn, conn:
        state = conn.execute('SELECT * FROM item_state WHERE item_id=?', (ident,)).fetchone()
        assert state['read_later_at'] == 'old' and state['read_at'] is None
        conn.execute('UPDATE item_state SET read_later_at=NULL')
    module.ensure_db()
    with closing(module.db_conn()) as conn:
        assert conn.execute('SELECT read_later_at FROM item_state').fetchone()[0] is None
