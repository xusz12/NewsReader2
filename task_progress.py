"""Metadata and notice acknowledgements for the shared header indicator."""
import periodic_reports
import news_classification
import task_notices


ACTIVE_JOBS = """FROM event_topics t JOIN topic_jobs j ON j.rowid=(
    SELECT MAX(rowid) FROM topic_jobs WHERE topic_id=t.id)"""
JOB_SCOPE = "t.archived_at IS NULL AND j.revision=t.revision AND j.status IN ('pending','running','failed')"
ACTIVE_CHECKS = "FROM topic_checks c JOIN event_topics t ON t.id=c.topic_id"
CHECK_SCOPE = "t.archived_at IS NULL AND t.status='ready' AND c.revision=t.revision"
REPORT_SCOPE = """FROM periodic_reports r LEFT JOIN (
    SELECT id AS notice_id,item_key,dismissed FROM task_progress_notices WHERE source='report'
    ) n ON n.item_key=r.id
    WHERE r.status IN ('pending','running','failed') AND (r.status<>'failed' OR COALESCE(n.dismissed,0)=0)"""


def snapshot(conn):
    # One snapshot keeps queues consistent without reading news or model inputs.
    conn.execute("BEGIN")
    meta = conn.execute("SELECT database_token FROM periodic_report_meta WHERE singleton=1").fetchone()
    topics, notices, dismissed_jobs = {}, {}, set()
    for row in conn.execute(f"""SELECT t.id,COALESCE(NULLIF(t.name,''),substr(t.query,1,160)) AS name,j.kind,j.status,j.error,j.next_attempt_at,
        n.id AS notice,n.dismissed {ACTIVE_JOBS}
        LEFT JOIN task_progress_notices n ON n.source='topic' AND n.item_key=j.id WHERE {JOB_SCOPE}
        ORDER BY CASE j.status WHEN 'running' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,j.created_at,t.id"""):
        if row['status'] == 'failed' and row['dismissed']:
            dismissed_jobs.add(row['id'])
            continue
        if row['notice'] and not row['dismissed']:
            notices['topic'] = max(notices.get('topic', 0), row['notice'])
        topics[row['id']] = {'id':row['id'], 'name':row['name'], 'matching':{},
            'job':{**{key:row[key] for key in ('kind','status','error','next_attempt_at')}, 'error_dismissed':bool(row['dismissed'])}}
    # Unprocessed matches remain visible during the gap before a milestone job is queued.
    for row in conn.execute(f"""SELECT t.id,t.name,c.status,COUNT(*) AS count,MAX(n.id) AS notice {ACTIVE_CHECKS}
        LEFT JOIN task_progress_notices n ON c.status='failed' AND n.source='matching' AND n.item_key=CAST(c.seq AS TEXT)
        WHERE {CHECK_SCOPE} AND COALESCE(n.dismissed,0)=0
          AND (c.status IN ('pending','running','failed') OR (c.status='done' AND c.matched=1 AND c.processed=0))
        GROUP BY t.id,c.status ORDER BY t.created_at,t.id"""):
        if row['status'] == 'done' and row['id'] in dismissed_jobs:
            continue
        if row['notice']:
            notices['matching'] = max(notices.get('matching', 0), row['notice'])
        topic = topics.setdefault(row['id'], {'id':row['id'], 'name':row['name'], 'job':None, 'matching':{}})
        topic['matching']['waiting_update' if row['status']=='done' else row['status']] = row['count']
    counts = {}
    for status, count, notice in conn.execute(f"""SELECT r.status,COUNT(*),MAX(CASE WHEN n.dismissed=0 THEN n.notice_id END)
            {REPORT_SCOPE} GROUP BY r.status"""):
        counts[status] = count
        if notice:
            notices['report'] = max(notices.get('report', 0), notice)
    reports = conn.execute(f"""SELECT {periodic_reports.OBSERVATION_COLUMNS},n.dismissed {REPORT_SCOPE}
        ORDER BY CASE r.status WHEN 'running' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,r.scheduled_at,r.id LIMIT 10""")
    return {'database_token':meta['database_token'], 'notices':notices, 'topics':{'items':list(topics.values())},
        'reports':{'items':[{**periodic_reports.execution_record(row), 'error_dismissed':bool(row['dismissed'])} for row in reports],
            'counts':{status:counts.get(status,0) for status in ('running','pending','failed')},
            'total':sum(counts.values())}}


def dismiss(conn, token, limits, *, unread_only, batch_id=None):
    if not isinstance(token, str) or not isinstance(limits, dict) or not limits or any(
            key not in task_notices.SOURCES or type(value) is not int or not 0 < value < 2**63 for key, value in limits.items()):
        raise ValueError("invalid_task_notices")
    if "classification" in limits and not isinstance(batch_id, str):
        raise ValueError("invalid_task_notices")
    conn.execute("BEGIN IMMEDIATE")
    if conn.execute("SELECT database_token FROM periodic_report_meta WHERE singleton=1").fetchone()[0] != token:
        raise ValueError("task_database_changed")
    for source, limit in limits.items():
        scope, params = "", []
        if source == 'classification':
            items, params = news_classification.batch_items(unread_only)
            scope = f""" AND item_key IN (SELECT c.item_id {items} AND c.status='failed')
                AND EXISTS (SELECT 1 FROM classification_progress_batch WHERE id=? AND unread_only=?)"""
            params.extend((batch_id, int(unread_only)))
        elif source == 'topic':
            scope = f" AND item_key IN (SELECT j.id {ACTIVE_JOBS} WHERE {JOB_SCOPE})"
        elif source == 'matching':
            scope = f" AND item_key IN (SELECT CAST(c.seq AS TEXT) {ACTIVE_CHECKS} WHERE {CHECK_SCOPE} AND c.status='failed')"
        conn.execute("UPDATE task_progress_notices SET dismissed=1 WHERE source=? AND id<=? AND dismissed=0" + scope,
                     [source, limit, *params])
