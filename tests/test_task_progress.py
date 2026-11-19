"""Header observation uses queue metadata, never news text or model execution."""

from nr import preferences

from nr import configuration
from contextlib import closing
import json
import sqlite3

import task_progress
import task_notices
import news_classification
from test_periodic_reports import db, due, news


def topic(conn, ident, *, archived=None, status="ready"):
    conn.execute("""INSERT INTO event_topics(id,request_id,query,name,status,created_at,updated_at,archived_at)
        VALUES (?,?,?,?,?,'2026-10-31','2026-10-31',?)""",(ident,ident,'查询'+ident,'主题'+ident,status,archived))


def job(conn, ident, topic_id, status, kind="initialize", revision=1):
    conn.execute("""INSERT INTO topic_jobs(id,topic_id,kind,status,revision,config_json,input_json,error,created_at,updated_at)
        VALUES (?,?,?,?,?,'SECRET-CONFIG','SECRET-INPUT',?,'2026-10-31','2026-10-31')""",
        (ident,topic_id,kind,status,revision,'topic_runtime_failed' if status=='failed' else ''))


def check(conn, ident, topic_id, status, *, matched=0, processed=0, revision=1):
    conn.execute("""INSERT INTO topic_checks(topic_id,item_id,revision,fingerprint,item_json,status,matched,processed,updated_at)
        VALUES (?,?,?,'fingerprint','SECRET-NEWS',?,?,?,0)""",(topic_id,ident,revision,status,matched,processed))


def read(db):
    with closing(db()) as conn:
        before=conn.total_changes
        result=task_progress.snapshot(conn)
        assert conn.total_changes==before
        return result


def test_empty_snapshot_is_read_only(db):
    result=read(db)
    assert result['database_token']
    assert result['topics']['items']==[]
    assert result['reports']=={'items':[],'total':0,'counts':{'pending':0,'running':0,'failed':0}}


def test_topics_include_research_matching_and_unscheduled_milestones(db):
    with closing(db()) as conn,conn:
        for ident in ['research','matching','gap','failed','finished','old-error','archived','stale-revision']:
            topic(conn,ident,archived='2026-10-31' if ident=='archived' else None)
        job(conn,'research-job','research','running')
        job(conn,'old','old-error','failed');job(conn,'new','old-error','done')
        job(conn,'failed-job','failed','failed')
        job(conn,'finished-job','finished','done')
        job(conn,'archived-job','archived','running')
        job(conn,'stale-job','stale-revision','running',revision=0)
        for index,state in enumerate(['pending','running','failed']):check(conn,str(index),'matching',state)
        check(conn,'gap','gap','done',matched=1)
        check(conn,'done','finished','done',matched=1,processed=1)
        check(conn,'unmatched','finished','done')
        check(conn,'old','stale-revision','pending',revision=0)
        check(conn,'archived','archived','pending')
    result=read(db);rows={row['id']:row for row in result['topics']['items']}
    assert set(rows)=={'research','matching','gap','failed'}
    assert rows['research']['job']['kind']=='initialize'
    assert rows['matching']['matching']=={'pending':1,'running':1,'failed':1}
    assert rows['gap']['matching']=={'waiting_update':1} and rows['gap']['job'] is None
    assert rows['failed']['job']['error']=='topic_runtime_failed'
    assert 'SECRET' not in json.dumps(result)
    with closing(db()) as conn,conn:
        conn.execute("UPDATE topic_jobs SET status='done' WHERE id IN ('research-job','failed-job')")
        conn.execute("UPDATE topic_checks SET status='done',processed=1 WHERE topic_id IN ('matching','gap')")
    assert read(db)['topics']['items']==[]


def test_reports_keep_real_phases_retries_and_bounded_rows(db):
    report=due(db)
    with closing(db()) as conn,conn:
        conn.execute("""UPDATE periodic_reports SET status='running',snapshot_json='SECRET-INPUT',
            config_json='SECRET-CONFIG',result_json='BROKEN-RESULT',progress_json=? WHERE id=?""",
            (json.dumps({'stage':'selecting','completed':2,'total':5}),report['id']))
        columns=[row[1] for row in conn.execute('PRAGMA table_info(periodic_reports)')]
        for index in range(12):
            copy=dict(conn.execute('SELECT * FROM periodic_reports WHERE id=?',(report['id'],)).fetchone())
            copy.update(id=f'queued-{index}',edition_key=f'manual:{index}',status='pending',kind='weekly' if index%2 else 'monthly',
                error='report_model_timeout',next_attempt=1793388000)
            conn.execute(f"INSERT INTO periodic_reports ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",[copy[key] for key in columns])
    result=read(db)['reports']
    assert result['counts']=={'pending':12,'running':1,'failed':0}
    assert result['total']==13 and len(result['items'])==10
    assert result['items'][0]['id']==report['id']
    assert result['items'][0]['progress']=={'stage':'selecting','completed':2,'total':5}
    assert result['items'][1]['next_retry_at'] and result['items'][1]['error']=='report_model_timeout'
    assert 'SECRET' not in json.dumps(result) and 'BROKEN-RESULT' not in json.dumps(result)
    with closing(db()) as conn,conn:
        conn.execute("UPDATE periodic_reports SET status='done'")
    assert read(db)['reports']['total']==0


def test_progress_api_does_not_start_work_and_hides_internal_errors(db,monkeypatch):
    import app
    monkeypatch.setattr(app.runtime.db,'connect',db)
    monkeypatch.setattr(preferences,'provider_has_configured_key',lambda provider:False)
    def forbidden(*args,**kwargs):raise AssertionError('Observation started work')
    monkeypatch.setattr(app.runtime.jobs.classification,'start',forbidden)
    monkeypatch.setattr(preferences,'provider_secret_value',forbidden)
    monkeypatch.setattr(app.runtime.jobs.reports,'start',forbidden)
    monkeypatch.setattr(app.runtime.jobs.topics,'start',forbidden)
    monkeypatch.setattr(configuration,'current_runtime_settings',forbidden)
    with app.app.test_client() as client:
        result=client.get('/api/tasks/progress')
        assert result.status_code==200 and not result.get_json()['topics']['matching_configured']
        def broken():raise sqlite3.OperationalError('private path /secret/db')
        monkeypatch.setattr(app.runtime.db,'connect',broken)
        failure=client.get('/api/tasks/progress')
        assert failure.status_code==503 and failure.get_json()=={'ok':False,'error':'task_progress_unavailable'}


def test_clear_covers_unlisted_failures_preserves_retries_and_new_errors(db, monkeypatch):
    import app
    monkeypatch.setattr(app.runtime.db, 'connect', db)
    source = due(db)['id']
    with closing(db()) as conn, conn:
        columns = [r[1] for r in conn.execute('PRAGMA table_info(periodic_reports)')]
        row = dict(conn.execute("SELECT * FROM periodic_reports WHERE id=?", (source,)).fetchone())
        for i in range(13):
            row.update(id=f"error-{i}", edition_key=f"error-{i}", status='pending' if i == 12 else 'failed', error='report_model_timeout')
            conn.execute(f"INSERT INTO periodic_reports ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                         [row[key] for key in columns])
    first = read(db)
    payload = {"database_token": first['database_token'], "notices": first['notices']}
    assert len(first['reports']['items']) == 10 and first['reports']['counts']['failed'] == 12
    with closing(db()) as conn, conn:
        conn.execute("UPDATE periodic_reports SET status='running',error='' WHERE id='error-0'")
        conn.execute("UPDATE periodic_reports SET status='failed',error='report_model_timeout' WHERE id='error-0'")
    with app.app.test_client() as client:
        url = '/api/tasks/progress/dismiss'
        assert client.post(url, json=payload | {"database_token": "other"}).status_code == 409
        assert client.post(url, json=payload | {"notices": {"report": True}}).status_code == 400
        assert read(db)['reports']['counts']['failed'] == 12
        assert client.post(url, json=payload).status_code == 200
        assert client.post(url, json=payload).status_code == 200
    remaining = read(db)
    assert remaining['reports']['counts']['failed'] == 1
    rows = {row['id']: row for row in remaining['reports']['items']}
    assert rows['error-0']['error_dismissed'] is False and rows['error-12']['error_dismissed'] is True
    assert remaining['notices']['report'] > first['notices']['report']
    with closing(db()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM periodic_reports WHERE status='failed'").fetchone()[0] == 12


def test_progress_retry_requeues_only_the_selected_topics_visible_failed_matches(db, monkeypatch):
    import app
    monkeypatch.setattr(app.runtime.db, 'connect', db)
    resumes = []
    monkeypatch.setattr(app.runtime.jobs.classification, 'resume', lambda: resumes.append(True))
    with closing(db()) as conn, conn:
        topic(conn, 'first')
        topic(conn, 'second')
        job(conn, 'research', 'first', 'failed')
        job(conn, 'active', 'second', 'running')
        check(conn, 'dismissed', 'first', 'failed')
        for topic_id in ('first', 'second'):
            check(conn, 'shared-news', topic_id, 'failed')
        check(conn, 'old-revision', 'first', 'failed', revision=0)
        check(conn, 'completed', 'first', 'done', matched=1, processed=1)
        check(conn, 'working', 'first', 'running')
        conn.execute("UPDATE topic_checks SET attempts=2,next_attempt_at=10,last_error='decision_refused' WHERE status='failed'")
        conn.execute("UPDATE topic_checks SET lease_token='expired',lease_until=1 WHERE topic_id='first' AND item_id='shared-news'")
        conn.execute("""UPDATE task_progress_notices SET dismissed=1 WHERE source='matching' AND
            item_key=CAST((SELECT seq FROM topic_checks WHERE item_id='dismissed') AS TEXT)""")
    observed = read(db)
    payload = {'topic_id':'first', 'database_token':observed['database_token'], 'notice_id':observed['notices']['matching']}
    with closing(db()) as conn, conn:
        check(conn, 'new-failure', 'first', 'failed')
        keep = """SELECT * FROM topic_checks WHERE topic_id<>'first' OR item_id<>'shared-news' ORDER BY seq"""
        untouched = conn.execute(keep).fetchall()
    url = '/api/tasks/progress/retry-matching'
    with app.app.test_client() as client:
        assert client.post(url, json=payload | {'database_token':'other'}).status_code == 409
        assert client.post(url, json=payload | {'notice_id':True}).status_code == 400
        assert client.post(url, json=[]).status_code == 400
        result = client.post(url, json=payload)
        assert result.status_code == 202 and result.json == {'ok':True, 'retried':1}
        assert client.post(url, json=payload).json['retried'] == 0
        with closing(db()) as conn, conn:
            assert conn.execute(keep).fetchall() == untouched
            assert tuple(conn.execute("""SELECT status,attempts,next_attempt_at,last_error,lease_token,lease_until FROM topic_checks
                WHERE topic_id='first' AND item_id='shared-news'""").fetchone()) == ('pending',0,0,'',None,0)
            assert dict(conn.execute("SELECT id,status FROM topic_jobs")) == {'research':'failed', 'active':'running'}
            conn.execute("UPDATE topic_checks SET status='failed' WHERE topic_id='first' AND item_id='shared-news'")
        assert client.post(url, json=payload).json['retried'] == 0
        assert client.post(url, json=payload | {'topic_id':'second'}).json['retried'] == 1
        fresh = read(db)
        with closing(db()) as conn, conn:
            conn.execute("UPDATE event_topics SET archived_at='now' WHERE id='first'")
        assert client.post(url, json=payload | {'notice_id':fresh['notices']['matching']}).json['retried'] == 0
    assert len(resumes) == 2


def test_clear_classification_and_topic_messages_keeps_underlying_jobs(db):
    with closing(db()) as conn, conn:
        news(conn, 'input')
        news_classification.enqueue(conn)
        conn.execute("UPDATE item_classifications SET status='failed',last_error='http_503'")
        topic(conn, 'research')
        job(conn, 'job', 'research', 'failed')
        check(conn, 'bad', 'research', 'failed')
        check(conn, 'waiting', 'research', 'done', matched=1)
    observed = read(db)
    with closing(db()) as conn:
        classification = news_classification.status(conn)
        assert classification['unacknowledged_failed'] == 1
        with conn:
            task_progress.dismiss(conn, observed['database_token'], classification['notices'], unread_only=False, batch_id='old-batch')
        assert news_classification.status(conn)['unacknowledged_failed'] == 1
        with conn:
            task_progress.dismiss(conn, observed['database_token'], observed['notices'] | classification['notices'],
                                  unread_only=False, batch_id=classification['batch']['id'])
        classified = news_classification.status(conn)
        assert classified['batch']['failed'] == 1 and classified['unacknowledged_failed'] == 0
        assert classified['notices'] == {}
    assert read(db)['topics']['items'] == []
    with closing(db()) as conn, conn:
        assert conn.execute("SELECT status FROM topic_jobs WHERE id='job'").fetchone()[0] == 'failed'
        conn.execute("UPDATE item_classifications SET status='pending'")
        conn.execute("UPDATE item_classifications SET status='failed'")
        conn.execute("UPDATE topic_checks SET status='running' WHERE item_id='bad'")
        conn.execute("UPDATE topic_checks SET status='failed' WHERE item_id='bad'")
    assert read(db)['topics']['items'][0]['matching'] == {'failed': 1}
    with closing(db()) as conn:
        assert news_classification.status(conn)['unacknowledged_failed'] == 1


def test_existing_failures_are_added_once_and_recovery_retires_the_notice():
    with sqlite3.connect(':memory:') as conn:
        conn.execute("CREATE TABLE periodic_reports(id TEXT PRIMARY KEY,status TEXT,error TEXT)")
        conn.execute("INSERT INTO periodic_reports VALUES ('a','failed','timeout')")
        task_notices.install(conn, ('report',))
        conn.execute("UPDATE task_progress_notices SET dismissed=1")
        task_notices.install(conn, ('report',))
        assert conn.execute("SELECT id,dismissed FROM task_progress_notices").fetchall() == [(1, 1)]
        conn.execute("UPDATE periodic_reports SET status='running'")
        assert conn.execute("SELECT COUNT(*) FROM task_progress_notices").fetchone()[0] == 0
        conn.execute("UPDATE periodic_reports SET status='failed'")
        assert conn.execute("SELECT id,dismissed FROM task_progress_notices").fetchall() == [(2, 0)]
        conn.execute("DELETE FROM periodic_reports")
        assert conn.execute("SELECT COUNT(*) FROM task_progress_notices").fetchone()[0] == 0
