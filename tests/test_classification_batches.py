import sqlite3
from contextlib import closing

import news_classification as nc
from test_news_classification import conn, insert, response, ROOT
from test_classification_unread import mark_read
from scanner import apply_schema


def complete(conn, *, unread_only=False):
    job = nc.claim(conn, unread_only=unread_only)
    assert job
    nc.finish(conn, job, nc.parse_response(response()))
    return job


def queue(conn, *ids):
    for ident in ids:
        insert(conn, ident, ident)
    with conn:
        nc.enqueue(conn)


def test_batch_excludes_history_and_resets_for_next_round(conn):
    queue(conn, 'history')
    complete(conn)
    queue(conn, 'first', 'second')
    initial = nc.status(conn)
    batch = initial['batch']
    assert (initial['done'], initial['total']) == (1, 3)
    assert (batch['done'], batch['total']) == (0, 2)
    complete(conn)
    assert nc.status(conn)['batch'] == {**batch, 'done':1, 'pending':1}
    complete(conn)
    assert nc.status(conn)['batch'] == {**batch, 'done':2, 'pending':0}
    queue(conn, 'next')
    next_batch = nc.status(conn)['batch']
    assert next_batch['id'] != batch['id']
    assert (next_batch['done'], next_batch['total']) == (0, 1)
    assert nc.status(conn)['coverage'] == {'done':3, 'total':4}


def test_workers_capture_batch_before_first_poll_and_restart_preserves_it(conn):
    queue(conn, 'one', 'two', 'three')
    complete(conn)
    first = nc.status(conn)['batch']
    assert (first['done'], first['total']) == (1, 3)
    path = conn.execute('PRAGMA database_list').fetchone()[2]
    with closing(sqlite3.connect(path)) as restored:
        restored.row_factory = sqlite3.Row
        apply_schema(restored, ROOT/'schema.sql')
        assert nc.status(restored)['batch'] == first
        complete(restored)
    assert nc.status(conn)['batch'] == {**first, 'done':2, 'pending':1}


def test_added_work_retries_and_parallel_claims_stay_in_current_batch(conn):
    queue(conn, 'one', 'two')
    first, second = nc.claim(conn), nc.claim(conn)
    batch = nc.status(conn)['batch']
    assert batch['running'] == 2
    nc.finish(conn, first, nc.parse_response(response()))
    nc.finish(conn, second, None, nc.ClassificationError('invalid_response'))
    queue(conn, 'three')
    current = nc.status(conn)['batch']
    assert current['id'] == batch['id']
    assert (current['done'], current['failed'], current['pending'], current['total']) == (1,1,1,3)
    nc.retry_failed(conn)
    complete(conn)
    complete(conn)
    final = nc.status(conn)['batch']
    assert final['id'] == batch['id'] and (final['done'], final['total']) == (3,3)


def test_unread_batch_keeps_finished_and_inflight_work_after_reading(conn):
    queue(conn, 'one', 'two', 'read')
    mark_read(conn, 'read')
    first = complete(conn, unread_only=True)
    mark_read(conn, first['item_id'])
    batch = nc.status(conn, unread_only=True)['batch']
    assert (batch['done'], batch['total']) == (1,2)
    job = nc.claim(conn, unread_only=True)
    mark_read(conn, job['item_id'])
    assert nc.status(conn, unread_only=True)['batch']['running'] == 1
    nc.finish(conn, job, nc.parse_response(response()))
    final = nc.status(conn, unread_only=True)
    assert final['done'] == 0  # Library scope is still current unread news.
    assert (final['batch']['done'], final['batch']['total']) == (2,2)
    assert final['batch']['id'] == batch['id']
    expanded = nc.status(conn)['batch']
    assert expanded['id'] != batch['id'] and (expanded['done'], expanded['total']) == (0,1)


def test_changed_titles_deleted_items_and_skipped_read_work_do_not_inflate_batch(conn):
    queue(conn, 'one', 'two', 'three')
    first = complete(conn)
    old = nc.status(conn)['batch']
    with conn:
        conn.execute('UPDATE items SET title=? WHERE id=?', ('changed', first['item_id']))
        nc.enqueue(conn)
        conn.execute("DELETE FROM items WHERE id='one'")
    batch = nc.status(conn)['batch']
    assert batch['id'] == old['id'] and (batch['done'], batch['total']) == (0,2)
    mark_read(conn, 'two')
    batch = nc.status(conn, unread_only=True)['batch']
    assert batch['total'] == 1
    mark_read(conn, 'three')
    assert nc.status(conn, unread_only=True)['batch']['total'] == 0


def test_existing_completed_library_does_not_invent_a_new_batch(conn):
    queue(conn, 'old')
    with conn:
        conn.execute("UPDATE item_classifications SET status='done',category='ai'")
    status = nc.status(conn)
    assert status['total'] == 1 and status['done'] == 1
    assert status['batch']['total'] == 0 and status['batch']['id'] is None


def test_skipped_news_made_unread_after_completion_starts_a_new_batch(conn):
    queue(conn, 'one', 'two')
    complete(conn, unread_only=True)
    mark_read(conn, 'one')
    previous = nc.status(conn, unread_only=True)['batch']
    assert (previous['done'], previous['total']) == (1,1)
    mark_read(conn, 'one', False)
    job = nc.claim(conn, unread_only=True)
    current = nc.status(conn, unread_only=True)['batch']
    assert current['id'] != previous['id']
    assert (current['done'], current['running'], current['total']) == (0,1,1)
    nc.finish(conn, job, nc.parse_response(response()))
    assert nc.status(conn, unread_only=True)['batch']['done'] == 1
