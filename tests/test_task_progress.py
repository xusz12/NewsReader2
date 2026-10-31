"""Header observation uses queue metadata, never news text or model execution."""
from contextlib import closing
import json
import sqlite3
from types import SimpleNamespace

import task_progress
from test_periodic_reports import db, due


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
    monkeypatch.setattr(app,'db_conn',db)
    monkeypatch.setattr(app,'CLASSIFICATION_SERVICE',SimpleNamespace(error=''))
    monkeypatch.setattr(app,'provider_has_configured_key',lambda provider:False)
    def forbidden(*args,**kwargs):raise AssertionError('Observation started work')
    monkeypatch.setattr(app.REPORT_SERVICE,'start',forbidden)
    monkeypatch.setattr(app.TOPIC_SERVICE,'start',forbidden)
    monkeypatch.setattr(app,'current_runtime_settings',forbidden)
    with app.app.test_client() as client:
        result=client.get('/api/tasks/progress')
        assert result.status_code==200 and not result.get_json()['topics']['matching_configured']
        def broken():raise sqlite3.OperationalError('private path /secret/db')
        monkeypatch.setattr(app,'db_conn',broken)
        failure=client.get('/api/tasks/progress')
        assert failure.status_code==503 and failure.get_json()=={'ok':False,'error':'task_progress_unavailable'}
