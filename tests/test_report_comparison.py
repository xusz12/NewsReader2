"""Isolated model comparisons: no CLI/model requests or user database writes."""
from contextlib import closing
import json
import threading
import time
import uuid

import pytest
import periodic_reports as pr
from test_periodic_reports import db, news, runner

START, END = '2026-10-29T00:00:00+08:00', '2026-10-30T00:00:00+08:00'


def settings(runtime, model):
    return {'feature_models': {'reports': {'runtime': runtime, 'codex': {'model': model},
            'pi': {'provider': 'test', 'model': model}}}}


@pytest.fixture
def service(db, monkeypatch):
    monkeypatch.setattr(pr, 'now', lambda: pr.local('2026-10-30T12:00:00+08:00'))
    with closing(db()) as conn:
        news(conn, 'original', '2026-10-29T12:00:00+08:00')
    return pr.Service(db, lambda: settings('codex', 'astra'), lambda: None, runner)


def create(service, kind='evening', start=START, end=END, request_id=None):
    return service.create_manual(kind, start, end, request_id or uuid.uuid4().hex, service.manual_options()['database_token'])


def rows(db):
    with closing(db()) as conn:
        return [dict(row) for row in conn.execute('SELECT * FROM periodic_reports ORDER BY created_at,id')]


def until(check):
    deadline = time.monotonic()+5
    while time.monotonic() < deadline:
        if check(): return
        time.sleep(.01)
    assert check()


def test_creation_pins_runtime_model_and_replayed_request_keeps_original(service, db):
    ident = uuid.uuid4().hex
    first = create(service, request_id=ident)
    service.settings = lambda: settings('pi', 'gemini')
    assert create(service, request_id=ident)['id'] == first['id']
    second = create(service)
    assert first['model_label'] == 'Codex · astra' and second['model_label'] == 'Pi · gemini'
    def forbidden(): pytest.fail('A bound report consulted changed settings')
    service.settings = forbidden
    seen = []
    service.runner = lambda config, prompt: (seen.append((config['runtime'], config.get('model') or config['codex']['model'])), runner(config, prompt))[1]
    assert service.run_one() and service.run_one()
    assert set(seen) == {('codex', 'astra'), ('pi', 'gemini')}
    assert all(row['status']=='done' for row in rows(db))


def test_pool_generates_three_in_parallel_with_one_snapshot_and_no_duplicate_claims(service, db):
    expected = {}
    for index in range(5):
        runtime, model = ('codex' if index%2==0 else 'pi'), 'model-'+str(index)
        service.settings = lambda r=runtime, m=model: settings(r, m)
        expected[create(service)['id']] = (runtime, model)
    entered, release, mutex = threading.Event(), threading.Event(), threading.Lock()
    active, peak, calls, refreshes = 0, 0, [], []
    def model(config, prompt):
        nonlocal active, peak
        with mutex:
            active += 1; peak = max(peak, active)
            calls.append((config['runtime'], config.get('model') or config['codex']['model'], prompt))
            if active == 3: entered.set()
        try:
            assert release.wait(5)
            return runner(config, prompt)
        finally:
            with mutex: active -= 1
    service.runner, service.refresh_index = model, lambda: refreshes.append(1)
    try:
        service.start()
        assert entered.wait(5)
        workers = list(service.threads); service.start()
        assert service.threads == workers and len(workers) == 3
        current = rows(db)
        assert sum(row['status']=='running' for row in current) == 3
        assert sum(row['status']=='pending' for row in current) == 2
        observed = service.observation()
        assert observed['concurrency'] == 3 and len(observed['current_reports']) == 3
        assert refreshes == [1]
        release.set()
        until(lambda: all(row['status']=='done' for row in rows(db)))
    finally:
        release.set(); service.stop()
    assert peak == 3 and len(calls) == 5
    assert {(runtime, model) for runtime, model, _ in calls} == set(expected.values())
    assert len({prompt for _, _, prompt in calls}) == 1
    saved = rows(db)
    assert len({row['snapshot_id'] for row in saved}) == 1 and saved[0]['snapshot_id']
    assert all(row['snapshot_json'] is None and row['attempts']==1 for row in saved)
    with closing(db()) as conn:
        assert conn.execute('SELECT COUNT(*) FROM periodic_report_snapshots').fetchone()[0] == 1
    assert not any(thread.is_alive() for thread in workers)


def test_comparison_keeps_sources_when_news_changes_and_reuses_across_report_kinds(service, db):
    first = create(service)
    assert service.run_one()
    with closing(db()) as conn:
        original = pr.stored_snapshot(conn, conn.execute('SELECT * FROM periodic_reports WHERE id=?', (first['id'],)).fetchone())
        news(conn, 'late', '2026-10-29T18:00:00+08:00')
        conn.execute("UPDATE items SET title='后来修改的标题' WHERE id='original'"); conn.commit()
    service.refresh_index = lambda: pytest.fail('Comparison unnecessarily refreshed its frozen material')
    second = create(service, 'custom')
    assert service.run_one()
    saved = {row['id']: row for row in rows(db)}
    assert saved[first['id']]['snapshot_id'] == saved[second['id']]['snapshot_id']
    with closing(db()) as conn:
        compared = pr.stored_snapshot(conn, saved[second['id']])
    assert compared['kind'] == 'custom' and original['kind'] == 'evening'
    assert {k:v for k,v in original.items() if k!='kind'} == {k:v for k,v in compared.items() if k!='kind'}
    token = service.manual_options()['database_token']
    service.delete(first['id'], token)
    with closing(db()) as conn: assert pr.stored_snapshot(conn, saved[second['id']]) == compared
    service.delete(second['id'], token)
    with closing(db()) as conn:
        assert conn.execute('SELECT COUNT(*) FROM periodic_report_snapshots').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM items').fetchone()[0] == 2


def test_failure_does_not_poison_shared_material_and_retries_keep_bound_model(service, db):
    first = create(service)
    def broken(): raise PermissionError('private source path')
    service.refresh_index = broken
    assert service.run_one()
    with closing(db()) as conn:
        assert conn.execute('SELECT COUNT(*) FROM periodic_report_snapshots').fetchone()[0] == 0
        conn.execute("UPDATE periodic_reports SET status='failed' WHERE id=?", (first['id'],)); conn.commit()
    service.settings = lambda: settings('pi', 'gemini')
    second = create(service)
    service.refresh_index = lambda: None
    assert service.run_one()
    assert next(row for row in rows(db) if row['id']==second['id'])['status'] == 'done'
    service.retry(first['id'], service.manual_options()['database_token'])
    service.refresh_index = lambda: pytest.fail('Retry must reuse the comparison material')
    seen = []
    service.runner = lambda config, prompt: (seen.append(config), runner(config, prompt))[1]
    assert service.run_one()
    assert seen[0]['runtime'] == 'codex' and seen[0]['codex']['model'] == 'astra'
    assert len({row['snapshot_id'] for row in rows(db)}) == 1


def test_scheduled_jobs_bind_when_enqueued(service, db):
    service.schedule_once(pr.local('2026-10-30T14:30:00+08:00'))
    job = rows(db)[0]
    assert json.loads(job['config_json'])['codex']['model'] == 'astra'
    service.settings = lambda: settings('pi', 'gemini')
    service.schedule_once(pr.local('2026-10-30T14:30:10+08:00'))
    assert json.loads(rows(db)[0]['config_json'])['runtime'] == 'codex'


def test_legacy_snapshot_is_adopted_without_overwriting_legacy_input(service, db):
    first = create(service)
    with closing(db()) as conn:
        snapshot = pr.capture(conn, rows(db)[0])
        conn.execute("UPDATE periodic_reports SET snapshot_json=?,status='done' WHERE id=?", (pr.encoded(snapshot), first['id'])); conn.commit()
        news(conn, 'late', '2026-10-29T18:00:00+08:00')
    second = create(service)
    service.refresh_index = lambda: pytest.fail('An existing edition supplies the original materials')
    assert service.run_one()
    saved = {row['id']:row for row in rows(db)}
    assert json.loads(saved[first['id']]['snapshot_json']) == snapshot
    assert saved[first['id']]['snapshot_id'] == saved[second['id']]['snapshot_id']
    with closing(db()) as conn: assert pr.stored_snapshot(conn, saved[second['id']]) == snapshot


def test_lease_is_renewed_while_model_is_blocked(service, db, monkeypatch):
    monkeypatch.setattr(pr, 'LEASE_SECONDS', .3)
    monkeypatch.setattr(pr, 'LEASE_RENEW_SECONDS', .03)
    entered, release = threading.Event(), threading.Event()
    def model(config, prompt):
        entered.set(); assert release.wait(5)
        return runner(config, prompt)
    service.runner = model
    create(service)
    thread = threading.Thread(target=service.run_one)
    thread.start()
    try:
        assert entered.wait(3)
        time.sleep(.5)
        assert service.claim() is None
        current = rows(db)[0]
        assert current['status']=='running' and current['attempts']==1 and current['lease_until']>time.time()
    finally:
        release.set(); thread.join(3)
    assert rows(db)[0]['status'] == 'done'


def test_missing_shared_reference_fails_without_changing_comparison_material(service, db):
    create(service)
    with closing(db()) as conn, conn:
        conn.execute("UPDATE periodic_reports SET snapshot_id='missing-reference'")
    service.refresh_index = lambda: pytest.fail('Lost immutable material must not be silently replaced')
    service.runner = lambda *args: pytest.fail('No model call without the original snapshot')
    assert service.run_one()
    assert rows(db)[0]['error'] == 'report_snapshot_missing'


def test_equivalent_timezone_windows_reuse_one_snapshot(service, db):
    create(service)
    create(service, start='2026-10-28T16:00:00Z', end='2026-10-29T16:00:00Z')
    assert service.run_one() and service.run_one()
    assert len({row['snapshot_id'] for row in rows(db)}) == 1


def test_old_schema_migration_preserves_completed_content_and_is_idempotent(db):
    content = pr.encoded({'overview': [], 'sections': [], 'sources': [], 'reading_minutes': 0})
    config = pr.encoded({'runtime':'codex', 'codex':{'model':'astra'}, 'private':'NOT-FOR-UI'})
    with closing(db()) as conn:
        conn.execute('DROP TABLE periodic_reports')
        conn.executescript(pr.SCHEMA)
        conn.execute("""INSERT INTO periodic_reports
            (id,edition_key,kind,title,period_start,period_end,scheduled_at,status,config_json,result_json,created_at,updated_at)
            VALUES ('legacy','legacy','evening','旧日报',?,?,?,'done',?,?,?,?)""", (START, END, END, config, content, END, END))
        conn.commit()
        before = tuple(conn.execute('SELECT * FROM periodic_report_meta').fetchone())
        pr.initialize(conn); pr.initialize(conn)
        row = conn.execute("SELECT * FROM periodic_reports WHERE id='legacy'").fetchone()
        assert row['result_json'] == content and row['config_json'] == config
        assert row['model_label'] == 'Codex · astra'
        assert tuple(conn.execute('SELECT * FROM periodic_report_meta').fetchone()) == before
        assert 'NOT-FOR-UI' not in json.dumps(pr.serialize(row))
