import sqlite3
import threading

import news_classification as nc
from test_news_classification import api, conn, insert, response


def mark_read(conn, ident, read=True):
    with conn:
        conn.execute("""INSERT INTO item_state(item_id,read_at,updated_at) VALUES (?,?,?)
            ON CONFLICT(item_id) DO UPDATE SET read_at=excluded.read_at""", (ident, 'read' if read else None, 'now'))


def test_library_coverage_includes_read_unqueued_and_stale_titles(conn):
    assert nc.status(conn)['coverage']=={'total':0,'done':0}
    for ident in ['read','unread','changed','old-rule']:
        insert(conn,ident,ident)
    with conn:nc.enqueue(conn)
    while job:=nc.claim(conn):nc.finish(conn,job,nc.parse_response(response()))
    mark_read(conn,'read')
    with conn:
        conn.execute("UPDATE items SET title='changed title' WHERE id='changed'")
        conn.execute("UPDATE item_classifications SET rule_version='old' WHERE item_id='old-rule'")
    insert(conn,'not-queued','not yet queued')
    for unread_only in (False,True):
        result=nc.status(conn,unread_only=unread_only)
        assert result['coverage']=={'total':5,'done':2}
        assert result['done']==(1 if unread_only else 2)
    with conn:conn.execute("DELETE FROM items WHERE id='read'")
    assert nc.status(conn)['coverage']=={'total':4,'done':1}


def test_unread_scope_uses_current_read_state_and_keeps_completed_results(conn):
    for ident in ['read', 'explicit-unread', 'no-state']:
        insert(conn, ident, ident)
    mark_read(conn, 'read')
    mark_read(conn, 'explicit-unread', False)
    with conn:
        nc.enqueue(conn)
    seen = []
    while job := nc.claim(conn, unread_only=True):
        seen.append(job['item_id'])
        nc.finish(conn, job, nc.parse_response(response()))
    assert set(seen) == {'explicit-unread', 'no-state'}
    assert nc.status(conn, unread_only=True)['total'] == 2
    assert nc.status(conn, unread_only=True)['pending'] == 0
    assert nc.status(conn)['pending'] == 1
    assert conn.execute("SELECT attempts FROM item_classifications WHERE item_id='read'").fetchone()[0] == 0
    # Marking an existing item unread makes it eligible without an index refresh.
    mark_read(conn, 'read', False)
    job = nc.claim(conn, unread_only=True)
    assert job['item_id'] == 'read'
    nc.finish(conn, job, nc.parse_response(response()))
    mark_read(conn, 'read')
    assert nc.status(conn)['done'] == 3
    assert nc.status(conn, unread_only=True)['done'] == 2
    assert nc.claim(conn) is None
    # Newly indexed read items are queued, but cannot cause paid calls while enabled.
    insert(conn, 'new-read', 'new read title')
    mark_read(conn, 'new-read')
    with conn:
        assert nc.enqueue(conn) == 1
    assert nc.claim(conn, unread_only=True) is None
    assert nc.claim(conn)['item_id'] == 'new-read'


def test_expired_lease_and_retry_respect_unread_scope(conn):
    for ident in ['read', 'unread']:
        insert(conn, ident, ident)
    mark_read(conn, 'read')
    with conn:
        nc.enqueue(conn)
        conn.execute("UPDATE item_classifications SET status='running',lease_until=0 WHERE item_id='read'")
        conn.execute("UPDATE item_classifications SET status='failed',attempts=4 WHERE item_id='unread'")
    assert nc.claim(conn, unread_only=True) is None
    with conn:
        conn.execute("UPDATE item_classifications SET status='failed',attempts=4 WHERE item_id='read'")
    assert nc.retry_failed(conn, unread_only=True) == 1
    assert nc.claim(conn, unread_only=True)['item_id'] == 'unread'
    assert nc.retry_failed(conn) == 1
    assert nc.claim(conn)['item_id'] == 'read'


def test_live_scope_change_allows_inflight_completion_then_skips_read_items(conn):
    for ident in ['z-inflight', 'y-read', 'x-unread']:
        insert(conn, ident, ident)
    mark_read(conn, 'z-inflight')
    mark_read(conn, 'y-read')
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
        service.configure(8, unread_only=True)
        release.set()
        worker.join(2)
        assert not worker.is_alive()
        assert service.process_one('key', 'model')
        assert not service.process_one('key', 'model')
        assert seen == ['z-inflight', 'x-unread']
        assert nc.status(conn)['done'] == 2
        service.configure(8, unread_only=False)
        assert service.process_one('key', 'model')
        assert seen == ['z-inflight', 'x-unread', 'y-read']
        assert nc.status(conn)['done'] == 3
    finally:
        release.set()
        worker.join(3)


def test_status_and_retry_api_follow_saved_scope_without_hiding_classified_read_news(api):
    module, client = api
    with module.db_conn() as db:
        mark_read(db, 'n00')
        mark_read(db, 'n35')
        db.execute("UPDATE item_classifications SET status='failed',attempts=4 WHERE item_id IN ('n00','n01')")
    saved = client.put('/api/settings', json={'llm': module.load_app_settings()['llm'],
                                            'classification': {'unread_only': True}})
    assert saved.status_code == 200
    progress = client.get('/api/news/classification').json
    assert progress['unread_only'] is True
    assert (progress['total'], progress['done'], progress['failed']) == (34, 33, 1)
    assert client.post('/api/news/classification/retry').json['retried'] == 1
    assert client.get('/api/news/classification').json['pending'] == 1
    # Classification scope never changes the reader's existing category results.
    assert client.get('/api/news?category=economy&read_filter=all&count_only=1').json['total'] == 24
    assert client.post('/api/news/mark-all-read', json={'read_filter': 'unread'}).status_code == 200
    assert client.get('/api/news/classification').json['total'] == 0
    saved = client.put('/api/settings', json={'llm': module.load_app_settings()['llm'],
                                            'classification': {'unread_only': False}})
    assert saved.status_code == 200
    progress = client.get('/api/news/classification').json
    assert (progress['total'], progress['done'], progress['pending'], progress['failed']) == (36, 34, 1, 1)
