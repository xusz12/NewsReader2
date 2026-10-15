from __future__ import annotations
import copy
import json
from pathlib import Path
import sqlite3

import pytest
from scripts import news_digest_samples as tool


def fake_db(tmp_path):
    db = tmp_path / 'news.sqlite3'
    conn = sqlite3.connect(db)
    conn.executescript('''CREATE TABLE items(id TEXT PRIMARY KEY,date TEXT,published_at TEXT,title TEXT,summary TEXT,url TEXT,source TEXT);
        CREATE TABLE item_state(item_id TEXT PRIMARY KEY,read_at TEXT,important_at TEXT);
        CREATE TABLE article_details(url TEXT PRIMARY KEY,content TEXT);''')
    conn.executemany('INSERT INTO items VALUES(?,?,?,?,?,?,?)', [
        ('history','2026-09-30','','AI research','','https://test/h','Source'),
        ('a','2026-10-01','2026-10-02T01:00:00Z','New AI release','','https://test/a','Source'),
        ('b','','2026-10-07T22:00:00-10:00','Economic policy','Policy signed','https://test/b','Source'),
        ('c','2026-10-08','','Outside','','https://test/c','Source')])
    conn.executemany('INSERT INTO item_state VALUES(?,?,?)', [('history','','2026-09-30'),('a','already-read',''),('b','','')])
    conn.commit()
    conn.close()
    return db


def snap(tmp_path):
    return tool.export_snapshot(fake_db(tmp_path), '2026-10-01', '2026-10-08', tmp_path / 'out')


def row(part):
    return dict(ref=part['ref'],keep=True,title='科技消息',fact='原材料明确报道的消息。',topic=tool.TOPICS[0],
                date=part['date'],date_kind='报道日期',quote=part['text'][0:16],date_quote='')


def test_export_range_fallback_includes_read_readonly(tmp_path):
    db = fake_db(tmp_path)
    before = db.read_bytes()
    record = tool.export_snapshot(db,'2026-10-01','2026-10-08',tmp_path / 'out')
    assert [i['id'] for i in record['items']] == ['a','b']
    assert record['items'][0]['read_at'] == 'already-read'
    assert record['daily_counts'] == {'2026-10-01':1,'2026-10-07':1}
    assert record['material_counts'] == {'仅标题':1,'摘要':1}
    assert len(record['preferences']) == record['preference_total'] == 1
    assert db.read_bytes() == before
    assert tool.verify_source(tmp_path / 'out')['state_unchanged']
    conn = tool.readonly(db)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute('UPDATE item_state SET read_at="mutated"')
    conn.close()


def test_fragments_never_truncate_and_batches_cover():
    item = dict(ref='N0001',display_date='2026-10-01',title='x'*200,summary='y'*500,body='z'*1500,material='全文')
    parts = tool.fragments([item],64)
    assert ''.join(p['text'] for p in parts) == 'title: ' + item['title'] + '\nsummary: ' + item['summary'] + '\nbody: ' + item['body']
    assert len(parts) > 1
    assert [p['ref'] for b in tool.batches(parts,1000,3) for p in b] == [p['ref'] for p in parts]


@pytest.mark.parametrize('change', ['missing','duplicate','unknown','unsupported_quote','wrong_report_date','event_no_proof','future','invalid_topic','not_bool','empty_fact'])
def test_reject_incomplete_or_unsupported(tmp_path,change):
    part = tool.fragments(snap(tmp_path)['items'])[0]
    value = {'rows':[row(part)]}
    r = value['rows'][0]
    if change == 'missing': value['rows']=[]
    elif change == 'duplicate': value['rows']*=2
    elif change == 'unknown': r['ref']='BAD'
    elif change == 'unsupported_quote': r['quote']='invented unsupported'
    elif change == 'wrong_report_date': r['date']='2026-10-02'
    elif change == 'event_no_proof': r.update(date_kind='事件日期',date_quote='invented 2026-10-01')
    elif change == 'future':
        part['text'] += ' event 2026-10-02'
        r.update(date_kind='事件日期',date='2026-10-02',date_quote='event 2026-10-02')
    elif change == 'invalid_topic': r['topic']='novel'
    elif change == 'not_bool': r['keep']=1
    elif change == 'empty_fact': r['fact']=''
    with pytest.raises(ValueError): tool.validate_rows(value,[part])


def test_explicit_date_and_omission():
    part = dict(ref='N0001.1',date='2026-10-02',text='title: Company signed on 2026-10-01')
    r = row(part)
    r.update(date='2026-10-01',date_kind='事件日期',date_quote='signed on 2026-10-01')
    assert tool.validate_rows({'rows':[r]},[part]) == [r]
    assert tool.validate_rows({'rows':[dict(ref=part['ref'],keep=False,reason='日常或低价值')]},[part])


def test_event_coverage_and_link_evidence(tmp_path):
    parts = tool.fragments(snap(tmp_path)['items'])
    facts = [row(p) for p in parts]
    event = dict(title='独立新闻',topic=tool.TOPICS[0],refs=[p['ref'] for p in parts],display_refs=[p['ref'] for p in parts],link_reason='具体共同事件',
                 link_quotes=[dict(ref=f['ref'],quote=f['quote']) for f in facts])
    assert tool.validate_events({'events':[event],'excluded':[]},facts)
    for change in ('missing','duplicate','unsupported'):
        changed = copy.deepcopy(event)
        if change=='missing': changed['refs'].pop()
        if change=='duplicate': changed['refs'].append(changed['refs'][0])
        if change=='unsupported': changed['link_quotes'][0]['quote']='invalid evidence'
        with pytest.raises(ValueError): tool.validate_events({'events':[changed],'excluded':[]},facts)


def test_two_drafts_share_facts_and_safe_html(tmp_path):
    record = snap(tmp_path)
    parts = tool.fragments(record['items'])
    rows = [row(p) for p in parts]
    rows[0]['fact'] = '<script>alert(1)</script> [run](javascript:bad)'
    record['items'][0]['title']='<img src=x onerror=bad>'
    record['items'][0]['url']='javascript:bad'
    events = [dict(title='事件',topic=tool.TOPICS[0],refs=[r['ref']],display_refs=[r['ref']],link_reason='独立报道',link_quotes=[]) for r in rows]
    for mode in ('timeline','topics'):
        md = tool.render(record,rows,events,mode)
        output = tool.render_html(md)
        assert '全文缺失 2 条' in md and '仅标题 1 条' in md
        assert '<script>' not in output and '<img' not in output and 'href="javascript:' not in output
        assert all(r['ref'] in md for r in rows)
        assert '<details>' in output


def test_cache_failure_explicit_retry_and_configuration_binding(tmp_path):
    calls=[]
    def fail(config,prompt,**kw):
        calls.append(config)
        raise RuntimeError('codex_turn_failed')
    def ok(config,prompt,**kw):
        calls.append(config)
        assert kw['ephemeral']
        return '{"valid":true}', ''
    config = {'model':'fake'}
    validator = lambda r: r if r.get('valid') else (_ for _ in ()).throw(ValueError('bad'))
    with pytest.raises(RuntimeError): tool.call_cached(tmp_path,'first','prompt',config,validator,fail)
    with pytest.raises(ValueError,match='explicit_retry'): tool.call_cached(tmp_path,'first','prompt',config,validator,ok)
    assert tool.call_cached(tmp_path,'first','prompt',config,validator,ok,retry=True)
    assert tool.call_cached(tmp_path,'first','prompt',config,validator,fail)
    assert len(calls)==2
    with pytest.raises(ValueError,match='cache_mismatch'): tool.call_cached(tmp_path,'first','prompt',{'model':'different'},validator,ok)
    assert len(list((tmp_path/'attempts').glob('*.json')))==2


def test_end_to_end_fake_runtime_no_source_changes(tmp_path):
    record = snap(tmp_path)
    db = Path(record['db_path'])
    before=db.read_bytes()
    def run(config,prompt,**kw):
        assert config['model']=='fake' and config['provider']=='custom' and kw['ephemeral']
        if '此次片段：\n' in prompt:
            parts=json.loads(prompt.split('此次片段：\n')[1])
            return tool.encode({'rows':[row(p) for p in parts]}), ''
        facts=json.loads(prompt.split('事实：\n')[1])
        return tool.encode({'events':[dict(title=f['title'],topic=f['topic'],refs=[f['ref']],display_refs=[f['ref']],link_reason='独立报道',link_quotes=[]) for f in facts], 'excluded':[]}), ''
    result=tool.generate(tmp_path/'out', {'model':'fake','provider':'custom'}, run=run)
    assert result['processed_fragments']==result['fragment_count']==2
    assert len(result['items'])==2
    assert (tmp_path/'out'/'complete.json').exists()
    assert db.read_bytes()==before
    assert tool.verify_source(tmp_path/'out')
    with pytest.raises(ValueError,match='contract_mismatch'):
        tool.generate(tmp_path/'out', {'model':'other'}, run=run)


def test_snapshot_hash_and_source_change(tmp_path):
    record = snap(tmp_path)
    tool.check_snapshot(record)
    record['items'][0]['title']='changed'
    with pytest.raises(ValueError,match='hash_mismatch'): tool.check_snapshot(record)
    db=record['db_path']
    conn=sqlite3.connect(db)
    conn.execute('UPDATE item_state SET read_at="external"');conn.commit();conn.close()
    with pytest.raises(ValueError,match='source_state_changed'): tool.verify_source(tmp_path/'out')


def test_real_data_cannot_be_written_in_repository():
    with pytest.raises(ValueError): tool.private_dir(tool.ROOT/'artifacts')


def test_editorial_omission_is_explicit_and_cannot_lose_input(tmp_path):
    facts = [row(p) for p in tool.fragments(snap(tmp_path)['items'])]
    result = {'events':[dict(title='重大事件',topic=tool.TOPICS[0],refs=[facts[0]['ref']],
                            display_refs=[facts[0]['ref']],link_reason='独立报道',link_quotes=[])],
              'excluded':[{'refs':[facts[1]['ref']],'reason':'次要事件'}]}
    assert tool.validate_events(result,facts)
    omitted = copy.deepcopy(result)
    omitted['excluded'] = []
    with pytest.raises(ValueError,match='lost_or_duplicate'): tool.validate_events(omitted,facts)
    bad = copy.deepcopy(result)
    bad['events'][0]['display_refs']=[facts[1]['ref']]
    with pytest.raises(ValueError,match='display_refs'): tool.validate_events(bad,facts)
    bad = copy.deepcopy(result)
    bad['excluded'][0]['reason']='no logging'
    with pytest.raises(ValueError,match='editorial_reason'): tool.validate_events(bad,facts)


@pytest.mark.parametrize('bad',[None,{},[],True,123])
def test_malformed_date_is_rejected_as_validation_failure(tmp_path,bad):
    part=tool.fragments(snap(tmp_path)['items'])[0]
    value=row(part);value['date']=bad
    with pytest.raises(ValueError): tool.validate_rows({'rows':[value]},[part])


def test_unexpected_validator_error_is_recorded_not_success(tmp_path):
    def run(*a,**kw): return '{}',''
    def invalid(value): raise TypeError('untrusted shape')
    with pytest.raises(ValueError,match='invalid_model_structure'):
        tool.call_cached(tmp_path,'bad','prompt',{},invalid,run)
    log=tool.load(next((tmp_path/'attempts').glob('*.json')))
    assert log['status']=='failed' and log['error']=='invalid_model_structure'
    assert not (tmp_path/'bad.json').exists()


def test_program_retrieves_exact_source_span_and_resume(tmp_path):
    record = snap(tmp_path)
    parts = tool.fragments(record['items'])
    value = row(parts[0])
    value.update(quote_start=0, quote_end=16, quote='model paraphrase is ignored')
    assert tool.validate_rows({'rows':[value]}, parts[:1])[0]['quote'] == parts[0]['text'][:16]
    out = tmp_path / 'out'
    def run(config,prompt,**kw):
        if '此次片段：\n' in prompt:
            batch=json.loads(prompt.split('此次片段：\n')[1])
            rows=[dict(row(p),quote_start=0,quote_end=16) for p in batch]
            return tool.encode({'rows':rows}), ''
        facts=json.loads(prompt.split('事实：\n')[1])
        return tool.encode({'events':[dict(title=f['title'],topic=f['topic'],refs=[f['ref']],display_refs=[f['ref']],link_reason='独立报道',link_quotes=[]) for f in facts], 'excluded':[]}), ''
    tool.generate(out, {'model':'fake','provider':'custom'}, run=run)
    def no_calls(*a, **kw):
        raise AssertionError('validated cache must resume without a model call')
    assert tool.generate(out, {'model':'fake','provider':'custom'}, run=no_calls)['processed_fragments'] == len(parts)
    assert isinstance(tool.load(out/'batch-001.json')['result'], dict)


@pytest.mark.parametrize('start,end', [(True,16),(-1,16),(0,99999),(16,0),(0,5),(0,None)])
def test_source_offsets_rejected_when_invalid(tmp_path,start,end):
    part=tool.fragments(snap(tmp_path)['items'])[0]
    value=dict(row(part),quote_start=start,quote_end=end)
    with pytest.raises(ValueError,match='unsupported_fact'):
        tool.validate_rows({'rows':[value]},[part])


def test_event_date_is_retrieved_from_source_not_model_quote(tmp_path):
    part=tool.fragments(snap(tmp_path)['items'])[0]
    part['text']='示例协议签署于2026-10-01；保持原样。'
    value=dict(row(part),quote_start=0,quote_end=len(part['text']),date_kind='事件日期',date='2026-10-01',
               date_quote_start=0,date_quote_end=len(part['text']),date_quote='invented')
    assert tool.validate_rows({'rows':[value]},[part])[0]['date_quote'] == part['text']
    value['date']='2026-09-30'
    with pytest.raises(ValueError,match='unsupported_event_date'):
        tool.validate_rows({'rows':[value]},[part])


def simple_result(record):
    return {'events': [dict(title='本期重要新闻', topic=tool.TOPICS[0], updates=[
        dict(date=i['display_date'], date_kind='报道日期', fact='仅复述来源标题。', refs=[i['ref']])
        for i in record['items']])]}  # synthetic content only


def test_simple_single_call_same_facts_and_no_source_writes(tmp_path):
    record = snap(tmp_path)
    db = Path(record['db_path'])
    before = db.read_bytes()
    calls = []
    def run(config, prompt, **kw):
        calls.append(prompt)
        assert kw['ephemeral'] and config['model'] == 'fake'
        for i in record['items']:
            assert prompt.count('[' + i['ref'] + ']') == 1
            assert i['title'] in prompt
            if i['summary']:
                assert i['summary'] in prompt
        assert 'quote_start' not in prompt
        return tool.encode(simple_result(record)), ''
    out = tmp_path / 'out'
    result = tool.generate_simple(out, {'model': 'fake', 'provider': 'custom'}, run=run)
    assert result == {'input_count': 2, 'event_count': 1, 'cited_count': 2}
    assert len(calls) == 1
    assert len(tool.load(out / 'input-manifest.json')['items']) == 2
    assert (out / 'complete.json').exists()
    for mode in ('timeline', 'topics'):
        md = (out / f'{mode}.md').read_text()
        assert '不逐条复述全部新闻' in md
        assert md.count('仅复述来源标题。') == 2
        html = (out / f'{mode}.html').read_text()
        assert 'href="#N0001"' in html and '<a id="N0001"></a>' in html
    assert db.read_bytes() == before
    assert tool.verify_source(out)
    tool.generate_simple(out, {'model': 'fake', 'provider': 'custom'}, run=run)
    assert len(calls) == 1


@pytest.mark.parametrize('change', ['unknown', 'duplicate', 'empty', 'bad_kind', 'event_no_evidence', 'future_event'])
def test_simple_refs_and_dates(tmp_path, change):
    record = snap(tmp_path)
    result = simple_result(record)
    update = result['events'][0]['updates'][0]
    if change == 'unknown': update['refs'] = ['N9999']
    if change == 'duplicate': update['refs'] *= 2
    if change == 'empty': update['refs'] = []
    if change == 'bad_kind': update['date_kind'] = '猜测日期'
    if change == 'event_no_evidence': update['date_kind'] = '事件日期'
    if change == 'future_event':
        update['date_kind'] = '事件日期'
        update['date'] = '2026-10-10'
        record['items'][0]['summary'] = 'Planned for 2026-10-10'
    with pytest.raises(ValueError): tool.validate_simple(result, record)


def test_simple_event_date_requires_full_date_and_no_title_injection(tmp_path):
    record = snap(tmp_path)
    record['items'][0]['title'] = 'Occurred 2026-09-30 <script>bad</script>'
    record['items'][0]['url'] = 'javascript:alert(1)'
    result = simple_result(record)
    update = result['events'][0]['updates'][0]
    update.update(date='2026-09-30', date_kind='事件日期', fact='<script>alert(1)</script>')
    assert tool.validate_simple(result, record)
    rendered = tool.render_html(tool.render_simple(record, result, 'timeline'))
    assert '<script>' not in rendered and 'href="javascript:' not in rendered
    assert '仅标题' in rendered


def test_simple_reporting_dates_come_from_sources_not_model(tmp_path):
    record = snap(tmp_path)
    result = simple_result(record)
    update = result['events'][0]['updates'][0]
    update.update(date='2026-09-01', refs=[i['ref'] for i in record['items']])
    tool.validate_simple(result, record)
    assert update['date'] == '2026-10-01'
    assert update['report_dates'] == ['2026-10-01', '2026-10-07']
    assert update['model_report_date'] == '2026-09-01'
    assert '2026-10-01 — 2026-10-07（报道日期）' in tool.render_simple(record, result, 'timeline')
