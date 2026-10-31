"""Read-only metadata for the shared header indicator. Never starts work."""
import periodic_reports


def snapshot(conn):
    # One snapshot keeps queues consistent without reading news or model inputs.
    conn.execute("BEGIN")
    meta = conn.execute("SELECT database_token FROM periodic_report_meta WHERE singleton=1").fetchone()
    topics = {}
    for row in conn.execute("""SELECT t.id,COALESCE(NULLIF(t.name,''),substr(t.query,1,160)) AS name,j.id AS job_id,j.kind,j.status,j.error,j.next_attempt_at
        FROM event_topics t JOIN topic_jobs j ON j.rowid=(
            SELECT MAX(rowid) FROM topic_jobs WHERE topic_id=t.id)
        WHERE t.archived_at IS NULL AND j.revision=t.revision AND j.status IN ('pending','running','failed')
        ORDER BY CASE j.status WHEN 'running' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,j.created_at,t.id"""):
        topics[row['id']] = {'id':row['id'], 'name':row['name'], 'matching':{},
            'job':{key:row[key] for key in ('kind','status','error','next_attempt_at')}}
    # Unprocessed matches remain visible during the gap before a milestone job is queued.
    for row in conn.execute("""SELECT t.id,t.name,c.status,COUNT(*) AS count
        FROM topic_checks c JOIN event_topics t ON t.id=c.topic_id
        WHERE t.archived_at IS NULL AND t.status='ready' AND c.revision=t.revision
          AND (c.status IN ('pending','running','failed') OR (c.status='done' AND c.matched=1 AND c.processed=0))
        GROUP BY t.id,c.status ORDER BY t.created_at,t.id"""):
        topic = topics.setdefault(row['id'], {'id':row['id'], 'name':row['name'], 'job':None, 'matching':{}})
        topic['matching']['waiting_update' if row['status']=='done' else row['status']] = row['count']
    counts = dict(conn.execute("SELECT status,COUNT(*) FROM periodic_reports WHERE status IN ('pending','running','failed') GROUP BY status"))
    reports = conn.execute(f"""SELECT {periodic_reports.OBSERVATION_COLUMNS} FROM periodic_reports
        WHERE status IN ('pending','running','failed')
        ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,scheduled_at,id LIMIT 10""").fetchall()
    return {'database_token':meta['database_token'], 'topics':{'items':list(topics.values())},
        'reports':{'items':[periodic_reports.execution_record(row) for row in reports],
            'counts':{status:counts.get(status,0) for status in ('running','pending','failed')},
            'total':sum(counts.values())}}
