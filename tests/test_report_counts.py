import json
from contextlib import closing

import periodic_reports as pr
from test_periodic_reports import db, news
from test_report_comparison import service, create, rows


def test_counts_include_briefs_deduplicate_citations_and_survive_cleanup(service, db):
    with closing(db()) as conn:
        news(conn, 'second', '2026-10-29T13:00:00+08:00')
        news(conn, 'unused', '2026-10-29T14:00:00+08:00')
    create(service)
    service.runner = lambda *_: pr.encoded({'sections': [{'title': '重点', 'paragraphs': [
        {'text': '正文一', 'refs': ['N00001']}, {'text': '正文二', 'refs': ['N00001']}]}],
        'briefs': [{'text': '简讯', 'refs': ['N00002']}], 'omitted': ['N00003']})
    assert service.run_one()
    report = pr.serialize(rows(db)[0], detail=True)
    assert report['status'] == 'done'
    assert report['source_count'] == 3 and report['selected_source_count'] == 2
    with closing(db()) as conn, conn:
        conn.execute("DELETE FROM items")
        conn.execute("DELETE FROM periodic_report_snapshots")
        conn.execute("UPDATE periodic_reports SET snapshot_id=NULL,snapshot_json=NULL")
    after = pr.serialize(rows(db)[0], detail=True)
    assert after['source_count'] == 3 and after['selected_source_count'] == 2
    assert after['content'] == report['content']


def test_waiting_counts_are_unknown_and_completed_empty_counts_are_zero(service, db):
    report = create(service, start='2026-10-28T00:00:00+08:00', end='2026-10-29T00:00:00+08:00')
    assert report['source_count'] is None and report['selected_source_count'] is None
    assert service.run_one()
    report = pr.serialize(rows(db)[0])
    assert report['source_count'] == report['selected_source_count'] == 0


def test_migration_uses_saved_counts_or_retained_material_without_guessing(service, db):
    known = create(service)['id']
    assert service.run_one()
    measured = create(service)['id']
    unknown = create(service)['id']
    empty = create(service)['id']
    with closing(db()) as conn, conn:
        conn.execute('ALTER TABLE periodic_reports DROP COLUMN source_count')
        content = {'sections': [], 'briefs': [], 'sources': [], 'generation': {'source_count': 9}}
        conn.execute("UPDATE periodic_reports SET status='done',result_json=? WHERE id=?", (json.dumps(content), measured))
        conn.execute("UPDATE periodic_reports SET snapshot_json=? WHERE id=?", (json.dumps({'sources': []}), empty))
        pr.initialize(conn)
        pr.initialize(conn)
        counts = dict(conn.execute('SELECT id,source_count FROM periodic_reports'))
    assert counts == {known: 1, measured: 9, unknown: None, empty: 0}
