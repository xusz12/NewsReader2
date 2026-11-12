"""Monthly importance and history guarantees, with isolated data and fake models."""
import copy
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest

import periodic_reports as pr
import report_generation as gen
import report_monthly as monthly
import report_workspace as rw
from test_periodic_reports import db, news
from test_report_comparison import service, create, rows
from test_report_generation import source, edition, Checkpoints
from test_report_workspace import Journal, final_actions

START, END = '2026-10-01T00:00:00+08:00', '2026-11-01T00:00:00+08:00'


def mark(conn, *ids):
    for ident in ids:
        conn.execute("INSERT INTO item_state(item_id,important_at,updated_at) VALUES (?,'2026-11-12','2026-11-12') "
                     "ON CONFLICT(item_id) DO UPDATE SET important_at=excluded.important_at", (ident,))
    conn.commit()


def all_refs_model(config, prompt):
    material = json.loads(prompt.rsplit('\n新闻资料：', 1)[1])
    return gen.encoded(edition([ref for item in material for ref in item['refs']]))


def test_capture_uses_publication_window_and_flags_without_mutating_shared_base(db):
    period = {'kind': 'monthly', 'period_start': START, 'period_end': END}
    with closing(db()) as conn:
        for ident, at in [('history', '2026-09-30T15:59:59Z'), ('start', '2026-09-30T16:00:00Z'),
                          ('ordinary', '2026-10-20T12:00:00+08:00'), ('cutoff', '2026-10-31T16:00:00Z'),
                          ('old-unmarked', '2026-08-01T12:00:00+08:00'), ('video', START)]:
            news(conn, ident, at)
        conn.execute("UPDATE items SET url='https://www.bloomberg.com/news/videos/2026-10-01/clip' WHERE id='video'")
        mark(conn, 'history', 'start', 'cutoff', 'video')
        base = pr.capture(conn, period)
        original = copy.deepcopy(base)
        news(conn, 'late-important', '2026-10-28T12:00:00+08:00')
        mark(conn, 'late-important')
        conn.execute("UPDATE items SET title='不应改写旧标题' WHERE id='start'"); conn.commit()
        supplement = pr.capture_monthly(conn, base)
    snapshot = monthly.combine(base, supplement)
    items = {item['id']: item for item in snapshot['sources']}
    assert base == original
    assert set(items) == {'history', 'start', 'ordinary', 'late-important'}
    assert items['start']['monthly_role'] == items['late-important']['monthly_role'] == 'important'
    assert items['history']['monthly_role'] == 'background'
    assert 'monthly_role' not in items['ordinary']
    assert items['start']['title'] == original['sources'][0]['title']
    assert len({s['ref'] for s in items.values()}) == len(items)
    assert items['history']['published_at'] == '2026-09-30T23:59:59+08:00'


def test_empty_month_cannot_be_filled_with_historical_important_news(db):
    with closing(db()) as conn:
        news(conn, 'history', '2026-09-12T12:00:00+08:00'); mark(conn, 'history')
        base = pr.capture(conn, {'kind': 'monthly', 'period_start': START, 'period_end': END})
        snapshot = monthly.combine(base, pr.capture_monthly(conn, base))
    assert snapshot['sources'] == []
    assert gen.generate(snapshot, {}, lambda *_: pytest.fail('No current news'), Checkpoints())['sources'] == []


def test_retries_and_model_comparisons_freeze_monthly_material_and_cleanup_keeps_output(service, db, monkeypatch):
    monkeypatch.setattr(pr, 'now', lambda: pr.local('2026-11-12T12:00:00+08:00'))
    with closing(db()) as conn:
        news(conn, 'history', '2026-09-12T12:00:00+08:00'); mark(conn, 'original', 'history')
    first = create(service, 'monthly', START, END)
    service.runner = lambda *_: (_ for _ in ()).throw(RuntimeError('report_model_timeout'))
    assert service.run_one()
    with closing(db()) as conn:
        initial = dict(conn.execute('SELECT * FROM periodic_report_snapshots').fetchone())
        frozen = pr.stored_snapshot(conn, rows(db)[0])
        conn.execute("UPDATE item_state SET important_at=NULL WHERE item_id='original'"); conn.commit()
        news(conn, 'later', '2026-10-28T12:00:00+08:00'); mark(conn, 'later')
    restarted = pr.Service(db, service.settings, runner=all_refs_model)
    second = create(restarted, 'monthly', START, END)
    assert restarted.run_one()
    with closing(db()) as conn, conn:
        conn.execute("UPDATE periodic_reports SET status='failed' WHERE id=?", (first['id'],))
    restarted.retry(first['id'], restarted.manual_options()['database_token'])
    assert restarted.run_one()
    completed = {row['id']: row for row in rows(db)}
    assert completed[first['id']]['status'] == completed[second['id']]['status'] == 'done'
    assert json.loads(completed[first['id']]['result_json']) == json.loads(completed[second['id']]['result_json'])
    assert {s['id'] for s in json.loads(completed[first['id']]['result_json'])['sources']} == {'original', 'history'}
    with closing(db()) as conn:
        assert pr.stored_snapshot(conn, completed[second['id']]) == frozen
        assert dict(conn.execute('SELECT * FROM periodic_report_snapshots').fetchone()) == initial
        storage = pr.snapshot_storage(conn)
        assert storage['count'] == 1
        assert storage['bytes'] == sum(len(initial[k].encode()) for k in ('snapshot_json', 'monthly_snapshot_json'))
    # Other report kinds continue using the original sources and editorial rules.
    weekly = create(restarted, 'weekly', START, END)
    assert restarted.run_one()
    with closing(db()) as conn:
        saved = conn.execute('SELECT * FROM periodic_reports WHERE id=?', (weekly['id'],)).fetchone()
        assert pr.stored_snapshot(conn, saved) == {**json.loads(initial['snapshot_json']), 'kind': 'weekly'}
    before = {row['id']: pr.serialize(row, detail=True) for row in rows(db)}
    result = restarted.clear_snapshots(restarted.manual_options()['database_token'])
    assert result['snapshot_storage']['bytes'] == 0
    for row in rows(db):
        after = pr.serialize(row, detail=True)
        assert after['content'] == before[row['id']]['content']
        assert after['source_count'] == before[row['id']]['source_count']
        assert after['selected_source_count'] == before[row['id']]['selected_source_count']


def test_concurrent_monthly_jobs_capture_supplement_once(service, db, monkeypatch):
    monkeypatch.setattr(pr, 'now', lambda: pr.local('2026-11-12T12:00:00+08:00'))
    with closing(db()) as conn: mark(conn, 'original')
    for _ in range(2): create(service, 'monthly', START, END)
    capture, calls = pr.capture_monthly, []
    def counted(*args):
        calls.append(True)
        return capture(*args)
    monkeypatch.setattr(pr, 'capture_monthly', counted)
    service.runner = all_refs_model
    other = pr.Service(db, service.settings, runner=all_refs_model)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(worker.run_one) for worker in (service, other)]
        assert all(task.result(timeout=10) for task in tasks)
    assert len(calls) == 1
    assert all(row['status'] == 'done' for row in rows(db))
    assert len({row['snapshot_id'] for row in rows(db)}) == 1


def test_legacy_inline_snapshot_gets_independent_supplement_and_remains_immutable(service, db, monkeypatch):
    monkeypatch.setattr(pr, 'now', lambda: pr.local('2026-11-12T12:00:00+08:00'))
    report = create(service, 'monthly', START, END)
    with closing(db()) as conn, conn:
        mark(conn, 'original')
        base = pr.encoded(pr.capture(conn, rows(db)[0]))
        conn.execute('UPDATE periodic_reports SET snapshot_json=? WHERE id=?', (base, report['id']))
    service.runner = all_refs_model
    assert service.run_one()
    row = rows(db)[0]
    assert row['status'] == 'done'
    assert row['snapshot_json'] == base and row['monthly_snapshot_json']
    with closing(db()) as conn:
        assert pr.stored_snapshot(conn, row)['sources'][0]['monthly_role'] == 'important'
    service.clear_snapshots(service.manual_options()['database_token'])
    row = rows(db)[0]
    assert row['snapshot_json'] is row['monthly_snapshot_json'] is None


def monthly_sources():
    return [source(1, '当月试产计划', monthly_role='important', body='入选重要正文'),
            source(2, '当月公告纠正原定时间', body='入选纠正正文'),
            source(3, '此前试产安排', monthly_role='background', published_at='2026-09-20T08:00:00+08:00', body='相关历史正文'),
            source(4, '另一企业的旧产品', monthly_role='background', published_at='2026-09-21T08:00:00+08:00', body='无关背景秘密正文'),
            source(5, '普通无关新闻', body='未选秘密正文')]


def test_direct_monthly_repairs_missing_important_and_only_enriches_title_selected_sources():
    sources, phases, cache = monthly_sources(), [], Checkpoints()
    cache.saved['title-first-v1:v2:direct:checked'] = {'report': {'sections': [], 'briefs': [], 'sources': []}, 'omitted': []}
    def model(config, prompt):
        assert '无关背景秘密正文' not in prompt and '未选秘密正文' not in prompt
        assert gen.encoded(monthly.RULES) in prompt
        material = json.loads(prompt.rsplit('\n新闻资料：', 1)[1])
        if '标题初稿：' in prompt:
            phases.append('writing')
            assert {item['ref'] for item in material} == {'N00001', 'N00002', 'N00003'}
            assert '相关历史正文' in prompt and '入选纠正正文' in prompt
            return gen.encoded(edition(['N00001', 'N00002', 'N00003']))
        assert '正文' not in gen.encoded(material)
        if '本次仅修复' in prompt:
            phases.append('repair')
            return gen.encoded({'repairs': [{'path': '/briefs', 'value': [
                {'text': '本月计划与此前安排的关系', 'refs': ['N00001', 'N00003']}]}]})
        phases.append('selection')
        return gen.encoded({**edition(['N00002']), 'omitted': ['N00001', 'N00003-N00005']})
    result = gen.generate({'kind': 'monthly', 'sources': sources}, {'limits': {'input_tokens': 100000}}, model, cache)
    assert phases == ['selection', 'repair', 'writing']
    assert {s['ref'] for s in result['sources']} == {'N00001', 'N00002', 'N00003'}
    assert any(monthly.VERSION in key for key in cache.saved)


@pytest.mark.parametrize('kind', ['monthly', 'evening', 'weekly'])
def test_only_monthly_requires_important_sources_and_disallows_standalone_history(kind):
    sources = monthly_sources()
    omitted_important = {**edition(['N00002']), 'omitted': ['N00001', 'N00003-N00005']}
    standalone_history = {**edition(['N00001']), 'briefs': [{'text': '历史安排', 'refs': ['N00003']}],
                          'omitted': ['N00002', 'N00004-N00005']}
    for value in [omitted_important, standalone_history]:
        if kind == 'monthly':
            with pytest.raises(gen.InvalidReport): gen.validate_edition(value, sources, kind)
        else:
            assert gen.validate_edition(value, sources, kind)['report']['sources']


@pytest.mark.parametrize('runtime', ['codex', 'pi'])
def test_workspace_enforces_monthly_review_plan_writing_and_history_boundaries(runtime):
    snapshot = {'kind': 'monthly', 'sources': monthly_sources()}
    config = {'runtime': runtime, 'limits': {'input_tokens': 100000}}
    journal = Journal()
    w = rw.Workspace(snapshot, config, state_store=journal)
    batch = next(iter(w.batches))
    assert '正文' not in gen.encoded(w.invoke('report_sources', {}))
    assert 'error' in w.invoke('report_review', {'batch_id': batch, 'events': [], 'omitted': list(w.sources)})
    events = [{'text': '当月重要计划', 'refs': ['N00001']}, {'text': '当月纠正', 'refs': ['N00002']},
              {'text': '历史背景', 'refs': ['N00003']}]
    assert 'error' not in w.invoke('report_review', {'batch_id': batch, 'events': events, 'omitted': ['N00004', 'N00005']})
    ids = [e['id'] for e in w.events()]
    assert w.events()[0]['important_refs'] == ['N00001'] and w.events()[2]['background_refs'] == ['N00003']
    assert 'error' in w.invoke('report_plan', {'parts': [{'id': 'main', 'title': '主题', 'event_ids': ids[1:]}], 'omitted_events': ids[:1]})
    assert 'error' in w.invoke('report_plan', {'parts': [{'id': 'main', 'title': '主题', 'event_ids': ids[:2]},
        {'id': 'history', 'title': '旧事', 'event_ids': ids[2:]}], 'omitted_events': []})
    assert not w.status()['selection_locked']
    plan = w.invoke('report_plan', {'parts': [{'id': 'main', 'title': '计划与纠正', 'event_ids': ids}], 'omitted_events': []})
    restored = rw.Workspace(snapshot, config, state_store=journal)
    assert restored.selected_refs() == {'N00001', 'N00002', 'N00003'}
    assert '相关历史正文' in restored.invoke('report_source', {'ref': 'N00003'})['json_text']
    assert '无关背景秘密正文' not in restored.invoke('report_source', {'ref': 'N00004'})['json_text']
    args = {'part_id': 'main', 'revision': plan['revision']}
    missing = restored.invoke('report_draft', {**args, 'sections': edition(['N00002', 'N00003'])['sections'], 'briefs': []})
    assert '当月重要新闻' in gen.encoded(missing['issues'])
    assert 'error' in restored.invoke('report_draft', {**args, 'sections': [], 'briefs': [
        {'text': '本月计划和纠正', 'refs': ['N00001', 'N00002']}, {'text': '历史另写', 'refs': ['N00003']}]})
    saved = restored.invoke('report_draft', {**args, 'sections': [], 'briefs': [
        {'text': '相较9月安排，本月计划随后被公告纠正。', 'refs': ['N00001', 'N00002', 'N00003']}]})
    assert saved['saved'] == 'main'
    restored.invoke('report_draft', {'part_id': 'main'})
    assert restored.invoke('report_finish', {'revision': plan['revision'], 'audit': '已复核月内进展、历史日期与纠正'})['finished']
    assert monthly.VERSION in restored.state['final']['generation']['workflow']


def test_pi_orchestration_preserves_corrections_and_related_history_through_final_review():
    snapshot = {'kind': 'monthly', 'sources': monthly_sources()}
    config = {'runtime': 'pi', 'limits': {'input_tokens': 100000}}
    w = rw.Workspace(snapshot, config)
    phases = []
    def model(config, prompt):
        assert '无关背景秘密正文' not in prompt and '未选秘密正文' not in prompt
        if '本批仅建立' in prompt:
            phases.append('titles')
            assert '入选重要正文' not in prompt and '相关历史正文' not in prompt
            return gen.encoded({'events': [{'text': '本月计划及纠正与此前安排', 'refs': ['N00001', 'N00002', 'N00003']}],
                                'omitted': ['N00004', 'N00005']})
        if not w.state['plan']:
            phases.append('plan')
            assert 'important_refs' in prompt and 'background_refs' in prompt
            assert '相关历史正文' not in prompt
            return gen.encoded({'actions': [{'tool': 'report_plan', 'arguments': {'parts': [
                {'id': 'main', 'title': '本月计划与变化', 'event_ids': [w.events()[0]['id']]}], 'omitted_events': []}}]})
        if '本次独立编写' in prompt:
            phases.append('writing')
            assert '相关历史正文' in prompt and '入选纠正正文' in prompt
            return gen.encoded(edition(['N00001', 'N00002', 'N00003']))
        phases.append('audit')
        return gen.encoded(final_actions(w))
    result = rw.program_editor(w, config, model, Checkpoints())
    assert phases == ['titles', 'plan', 'writing', 'audit']
    assert {s['ref'] for s in result['sources']} == {'N00001', 'N00002', 'N00003'}


def test_capacity_batches_keep_importance_signals_during_merge(monkeypatch):
    monkeypatch.setattr(gen.report_model, 'estimate_tokens', len)
    sources = [source(1, '当月重要'+'甲'*14000, monthly_role='important'), source(2, '相关纠正'+'乙'*14000)]
    phases = []
    def model(config, prompt):
        material = json.loads(prompt.rsplit('\n新闻资料：', 1)[1])
        if '本次仅修复' in prompt:
            phases.append('repair')
            return gen.encoded({'repairs': [{'path': '/events', 'value': [{'text': '保留重要进展', 'refs': ['N00001']}]}]})
        if '候选仍超出' in prompt:
            phases.append('merge')
            if material[0]['refs'] == ['N00001']:
                assert material[0]['important_refs'] == ['N00001']
                return gen.encoded({'events': [], 'omitted': ['N00001']})
            return gen.encoded({'events': [{'text': '纠正', 'refs': ['N00002']}], 'omitted': []})
        if '本批仅做选材' in prompt:
            phases.append('extract')
            return gen.encoded({'events': [{'text': '标题事实'+'甲'*14000, 'refs': item['refs']} for item in material], 'omitted': []})
        phases.append('edition')
        assert any(item.get('important_refs') == ['N00001'] for item in material)
        return gen.encoded(edition(['N00001', 'N00002']))
    result = gen.generate({'kind': 'monthly', 'sources': sources}, {'limits': {'input_tokens': 24000}}, model, Checkpoints())
    assert 'merge' in phases and 'repair' in phases
    assert {s['ref'] for s in result['sources']} == {'N00001', 'N00002'}


def test_native_editor_uses_monthly_constraints_without_exposing_unselected_bodies():
    snapshot = {'kind': 'monthly', 'sources': monthly_sources()}
    config = {'runtime': 'codex', 'codex': {'reasoning_effort': 'ultra'}, 'limits': {'input_tokens': 100000}}
    def native(config, prompt):
        w = config['_workspace']
        assert w.status()['context']['monthly_rules'] == monthly.RULES
        assert all('body' not in item and 'summary' not in item for item in w.invoke('report_sources', {})['items'])
        batch = next(iter(w.batches))
        assert 'error' not in w.invoke('report_review', {'batch_id': batch,
            'events': [{'text': '当月变化与此前背景', 'refs': ['N00001', 'N00002', 'N00003']}], 'omitted': ['N00004', 'N00005']})
        plan = w.invoke('report_plan', {'parts': [{'id': 'main', 'title': '月内变化',
            'event_ids': [w.events()[0]['id']]}], 'omitted_events': []})
        assert '无关背景秘密正文' not in w.invoke('report_source', {'ref': 'N00004'})['json_text']
        w.invoke('report_draft', {'part_id': 'main', 'revision': plan['revision'],
            'sections': edition(['N00001', 'N00002', 'N00003'])['sections'], 'briefs': []})
        for action in final_actions(w)['actions']:
            assert 'error' not in w.invoke(action['tool'], action['arguments'])
        return '完成'
    result = gen.generate(snapshot, config, native, Checkpoints(), state_store=Journal())
    assert {s['ref'] for s in result['sources']} == {'N00001', 'N00002', 'N00003'}


def test_monthly_schema_upgrade_is_idempotent_and_preserves_completed_reports(service, db):
    report = create(service)
    assert service.run_one()
    with closing(db()) as conn, conn:
        before = conn.execute('SELECT result_json FROM periodic_reports WHERE id=?', (report['id'],)).fetchone()[0]
        conn.execute('ALTER TABLE periodic_reports DROP COLUMN monthly_snapshot_json')
        conn.execute('ALTER TABLE periodic_report_snapshots DROP COLUMN monthly_snapshot_json')
        pr.initialize(conn); pr.initialize(conn)
        assert conn.execute('SELECT result_json FROM periodic_reports WHERE id=?', (report['id'],)).fetchone()[0] == before
        assert conn.execute('SELECT monthly_snapshot_json FROM periodic_reports').fetchone()[0] is None
        assert conn.execute('SELECT monthly_snapshot_json FROM periodic_report_snapshots').fetchone()[0] is None


def test_monthly_api_returns_current_and_background_citations_and_durable_counts(service, db, monkeypatch, tmp_path):
    import app
    monkeypatch.setattr(pr, 'now', lambda: pr.local('2026-11-12T12:00:00+08:00'))
    monkeypatch.setattr(app, 'REPORT_SERVICE', service)
    def api_db():
        conn = db()
        conn.create_function('is_bloomberg_video_url', 1, lambda url: int(pr.is_bloomberg_video_url(url)))
        conn.create_function('feed_source_subkey', 4, app.feed_source_subkey_sql)
        return conn
    monkeypatch.setattr(app, 'db_conn', api_db)
    monkeypatch.setattr(app, 'AGENT_DB_PATH', tmp_path / 'agent.sqlite3')
    monkeypatch.setattr(app, 'AGENT_RUNTIME_DIR', tmp_path / 'agent-runtime')
    app.ensure_agent_db()
    monkeypatch.setattr(service, 'start', lambda: None)
    with closing(db()) as conn:
        news(conn, 'history', '2026-09-01T12:00:00+08:00'); mark(conn, 'original', 'history')
        states = [tuple(row) for row in conn.execute('SELECT * FROM item_state ORDER BY item_id')]
    service.runner = all_refs_model
    client = app.app.test_client()
    options = client.get('/api/reports/manual-options').json
    created = client.post('/api/reports/manual', json={'kind': 'monthly', 'selection': '2026-10',
        'request_id': uuid.uuid4().hex, 'database_token': options['database_token']})
    assert created.status_code == 202
    assert service.run_one()
    report = client.get('/api/reports/'+created.json['report']['id']).json['report']
    assert report['status'] == 'done'
    assert report['source_count'] == report['selected_source_count'] == 2
    assert {source['monthly_role'] for source in report['content']['sources']} == {'important', 'background'}
    assert {item['id'] for item in report['source_items'].values()} == {'original', 'history'}
    with closing(db()) as conn:
        assert [tuple(row) for row in conn.execute('SELECT * FROM item_state ORDER BY item_id')] == states
