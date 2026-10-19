"""Queue removal, cached bodies and explicit reading completion are independent."""
import threading
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_api import _setup_agent_api_fixture


@pytest.fixture
def setup(tmp_path, monkeypatch):
    app, client, item, *_ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    return app, client, item, f'/api/news/{item["id"]}/state'


def test_pending_cancel_does_not_complete_and_can_requeue(setup, monkeypatch):
    app, client, item, path = setup
    with app.db_conn() as conn:
        conn.execute('DELETE FROM article_details')
    monkeypatch.setattr(app, 'run_opencli_detail', lambda url: pytest.fail('Canceled job must not run'))
    added = client.patch(path, json={'read_later': True}).json
    assert added['detail_status'] == 'pending'
    removed = client.patch(path, json={'read_later': False}).json
    assert removed['detail_status'] == 'canceled'
    assert removed['read_later_at'] is removed['read_later_done_at'] is None
    assert app.process_pending_jobs_once() is False
    assert client.get('/api/news?collection=read_later&read_filter=read').json['total'] == 0
    added = client.patch(path, json={'read_later': True}).json
    assert added['read_later_at'] and added['read_later_done_at'] is None
    assert added['detail_status'] == 'pending'


def test_cancel_racing_with_running_fetch_keeps_it_pending_until_explicit_completion(setup, monkeypatch):
    app, client, item, path = setup
    with app.db_conn() as conn:
        original = dict(conn.execute('SELECT * FROM article_details WHERE url=?', (item['url'],)).fetchone())
        conn.execute('DELETE FROM article_details')
    entered, release = threading.Event(), threading.Event()
    def fetch(url):
        entered.set()
        assert release.wait(10)
        return True, original, ''
    monkeypatch.setattr(app, 'run_opencli_detail', fetch)
    client.patch(path, json={'read_later': True})
    with ThreadPoolExecutor(max_workers=1) as pool:
        work = pool.submit(app.process_pending_jobs_once)
        try:
            assert entered.wait(10)
            removed = client.patch(path, json={'read_later': False}).json
            assert removed['detail_status'] == 'running'
            assert removed['read_later_at']
            assert removed['read_later_done_at'] is None
        finally:
            release.set()
        assert work.result(timeout=10)
    detail = client.get(f'/api/news/{item["id"]}/detail').json
    assert detail['detail_status'] == 'success' and detail['detail']['content']
    assert detail['read_later_at']
    assert detail['read_later_done_at'] is detail['read_at'] is None
    assert client.get('/api/news?collection=read_later&read_filter=unread').json['total'] == 1
    assert client.get('/api/news?collection=read_later&read_filter=read').json['total'] == 0


def test_all_cached_unfinished_articles_are_pending_in_lists_search_counts_and_sources(setup):
    app, client, item, path = setup
    read = '/api/news?collection=read_later&read_filter=read'
    search = '/api/search?q=authoritative&feed_filters=1&collection=read_later&read_filter=read'
    assert client.get(read).json['total'] == 0
    unread = read.replace('read_filter=read', 'read_filter=unread')
    unread_search = search.replace('read_filter=read', 'read_filter=unread')
    for url in (unread, unread + '&count_only=1', unread_search, unread_search + '&count_only=1'):
        assert client.get(url).json['total'] == 1
    assert client.get('/api/nav-summary').json['summary']['read_later_unread'] == 1
    assert sum(source['count'] for source in client.get('/api/sources?collection=read_later&read_filter=unread').json['sources']) == 1
    client.patch(path, json={'read_later': True})
    removed = client.patch(path, json={'read_later': False}).json
    assert removed['detail_status'] == 'success' and removed['read_later_done_at'] is None
    assert client.get(unread).json['total'] == 1
    for url in (read, read + '&count_only=1', search, search + '&count_only=1'):
        assert client.get(url).json['total'] == 0
    assert client.get('/api/sources?collection=read_later&read_filter=read').json['sources'] == []
    completed = client.patch(path, json={'read_later': False, 'read_later_done': True}).json
    assert completed['read_later_done_at'] and completed['read_at'] is None
    assert client.get(unread).json['total'] == 0
    assert client.get('/api/nav-summary').json['summary']['read_later_unread'] == 0
    for url in (read, read + '&count_only=1', search, search + '&count_only=1'):
        assert client.get(url).json['total'] == 1
    readded = client.patch(path, json={'read_later': True}).json
    assert readded['read_later_at'] and readded['read_later_done_at'] is None
    assert client.get(read).json['total'] == 0


def test_bulk_completion_includes_cached_articles_without_explicit_queue_flag(setup):
    app, client, item, path = setup
    result = client.post('/api/news/clear-read-later', json={'read_filter': 'unread'}).json
    assert result['cleared'] == 1
    detail = client.get(f'/api/news/{item["id"]}/detail').json
    assert detail['read_later_done_at'] and detail['read_at'] is None
    assert client.get('/api/nav-summary').json['summary']['read_later_unread'] == 0
    assert client.get('/api/news?collection=read_later&read_filter=read').json['total'] == 1


def test_late_cache_arrival_inherits_explicit_completion_instead_of_reopening(setup):
    app, client, item, path = setup
    client.patch(path, json={'read_later': False, 'read_later_done': True})
    with app.db_conn() as conn:
        conn.execute('UPDATE article_details SET content=content || ? WHERE url=?', (' refreshed', item['url']))
    assert client.get('/api/news?collection=read_later&read_filter=unread').json['total'] == 0


@pytest.mark.parametrize('status,expected', [('pending', 'canceled'), ('running', 'running')])
def test_removal_preserves_body_and_only_cancels_queued_translation(setup, status, expected):
    app, client, item, path = setup
    client.patch(path, json={'read_later': True})
    with app.db_conn() as conn:
        conn.execute('INSERT INTO ai_jobs(url,status,updated_at) VALUES(?,?,?)', (item['url'], status, app.now_ts()))
    result = client.patch(path, json={'read_later': False}).json
    assert result['ai_status'] == expected
    assert result['read_later_done_at'] is None
    assert client.get(f'/api/news/{item["id"]}/detail').json['detail']['content']


@pytest.mark.parametrize('body', [
    {'read_later': True, 'read_later_done': True},
    {'read_later': False, 'read_later_done': 'true'},
    {'favorite': True, 'read_later_done': True},
])
def test_completion_requires_explicit_valid_removal(setup, body):
    _, client, _, path = setup
    assert client.patch(path, json=body).status_code == 400


@pytest.mark.parametrize('status', ['success', 'skipped'])
def test_retry_recovers_legacy_terminal_jobs_without_cached_body(setup, status):
    app, client, item, path = setup
    with app.db_conn() as conn:
        conn.execute('DELETE FROM article_details')
        conn.execute('INSERT INTO detail_jobs(url,item_id,status,updated_at) VALUES(?,?,?,?)',
                     (item['url'], item['id'], status, app.now_ts()))
    result = client.patch(path, json={'read_later': True}).json
    assert result['detail_status'] == 'pending'
    assert result['read_later_at'] and result['read_later_done_at'] is None


@pytest.mark.parametrize('read_filter', ['unread', 'read', 'all'])
@pytest.mark.parametrize('sort_order', ['default', 'reverse'])
def test_read_later_paging_does_not_skip_after_rows_leave_current_filter(setup, read_filter, sort_order):
    app, client, item, _ = setup
    with app.db_conn() as conn:
        template = dict(conn.execute('SELECT * FROM items WHERE id=?', (item['id'],)).fetchone())
        if read_filter == 'all':
            conn.execute('DELETE FROM article_details')
        for index in range(35):
            row = {**template, 'id': f'paging-{index:02d}'}
            conn.execute(f"INSERT INTO items({','.join(row)}) VALUES ({','.join('?' for _ in row)})", list(row.values()))
            conn.execute('INSERT INTO item_state(item_id,read_later_at,read_later_done_at,updated_at) VALUES(?,?,?,?)',
                         (row['id'], None if read_filter == 'read' else app.now_ts(),
                          app.now_ts() if read_filter == 'read' else None, app.now_ts()))
    params = {'collection': 'read_later', 'read_filter': read_filter, 'sort_order': sort_order}
    expected = [row['id'] for row in client.get('/api/news', query_string={**params, 'per': 100}).json['items']]
    legacy_page = client.get('/api/news', query_string={**params, 'per': 10, 'page': 2}).json
    assert [row['id'] for row in legacy_page['items']] == expected[10:20]
    first = client.get('/api/news', query_string={**params, 'per': 10}).json
    assert first['next_cursor']
    for row in first['items'][:5]:
        body = {'read_later': read_filter == 'read'}
        if read_filter == 'unread':
            body['read_later_done'] = True
        assert client.patch(f'/api/news/{row["id"]}/state', json=body).status_code == 200
    cursor = first['next_cursor']
    second = client.get('/api/news', query_string={**params, 'per': 10, 'page': 2,
        'cursor_date': cursor['date_key'], 'cursor_published_at': cursor['published_at'], 'cursor_id': cursor['id']}).json
    assert [row['id'] for row in second['items']] == expected[10:20]


def test_large_bulk_completion_stays_atomic_with_legacy_sqlite_variable_limit(setup, monkeypatch):
    app, client, item, _ = setup
    with app.db_conn() as conn:
        template = dict(conn.execute('SELECT * FROM items WHERE id=?', (item['id'],)).fetchone())
        ts = app.now_ts()
        for index in range(1001):
            row = {**template, 'id': f'bulk-{index}', 'url': f'https://example.com/bulk-{index}'}
            conn.execute(f"INSERT INTO items({','.join(row)}) VALUES ({','.join('?' for _ in row)})", list(row.values()))
            conn.execute('INSERT INTO item_state(item_id,read_later_at,read_at,favorite_at,updated_at) VALUES(?,?,?,?,?)',
                         (row['id'], ts, ts, ts, ts))
            conn.execute('INSERT INTO detail_jobs(url,item_id,status,updated_at) VALUES(?,?,?,?)',
                         (row['url'], row['id'], 'pending', ts))
    connect = app.db_conn
    def limited_connection():
        conn = connect()
        conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
        return conn
    monkeypatch.setattr(app, 'db_conn', limited_connection)
    response = client.post('/api/news/clear-read-later', json={})
    assert response.status_code == 200
    assert response.json['cleared'] == 1002
    with app.db_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM detail_jobs WHERE status='pending'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM item_state WHERE read_later_done_at IS NOT NULL").fetchone()[0] == 1002
        assert conn.execute("SELECT COUNT(*) FROM item_state WHERE read_at IS NOT NULL AND favorite_at IS NOT NULL").fetchone()[0] == 1001
