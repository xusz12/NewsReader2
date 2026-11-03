"""Reports reuse persisted reading tools without becoming news feed entries."""
import json
import pytest
from test_api import _setup_agent_api_fixture


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    app, client, item, *_ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    content = {"sections": [{"title": "重复判断", "paragraphs": [
        {"text": "📌 规模交付仍待验证。重复判断。", "refs": ["N1"]},
        {"text": "重复判断。", "refs": ["N1"]}]}], "briefs": [{"text": "简讯中的判断", "refs": ["N1"]}],
        "sources": [{"ref": "N1", "id": "stale-id", "url": item["url"], "title": "存档标题", "source_name": "媒体", "published_at": "2026-10-30T12:00:00+08:00"}], "reading_minutes": 1}
    with app.db_conn() as conn:
        for ident in ("one", "two"):
            conn.execute("""INSERT INTO periodic_reports(id,edition_key,kind,title,period_start,period_end,scheduled_at,status,result_json,created_at,updated_at)
                VALUES (?,?, 'evening', '日报', '2026-10-29T00:00:00+08:00','2026-10-30T00:00:00+08:00','2026-10-30T00:00:00+08:00','done',?,'2026-10-30','2026-10-30')""",
                (ident, ident, json.dumps(content, ensure_ascii=False)))
    return app, client, item


def test_highlights_quotes_notes_and_agent_are_report_scoped(fixture):
    app, client, item = fixture
    report = client.get('/api/reports/one').json['report']
    reader = report['reader']
    body = reader['body']
    assert report['source_items']['N1']['id'] == item['id']
    assert reader['agent']['context_level'] == 'report'
    assert '简讯中的判断' in body
    start = body.rindex('重复判断。')
    base = '/api/news/report:one'
    created = client.post(base + '/highlights', json={'body_kind':'original','start_offset':start,'end_offset':start+5,'selected_text':'重复判断。'})
    assert created.status_code == 201
    mark = created.json['highlight']
    assert mark['resolved_start_offset'] == start
    note = client.post(base + '/notes', json={'note':'等待后续数据', 'quote':{'text':'重复判断。','source':'original','paragraph':2}})
    assert note.status_code == 200
    restored = client.get('/api/reports/one').json['report']['reader']
    assert restored['notes'][0]['quote_text'] == '重复判断。'
    assert restored['highlight_surfaces']['original']['highlights'][0]['id'] == mark['id']
    assert client.get('/api/reports/two').json['report']['reader']['notes'] == []
    assert client.delete('/api/news/report:two/highlights/' + str(mark['id'])).status_code == 404
    started = client.post(base + '/agent/jobs', json={'question':'报告依据是什么？','quote_text':'重复判断。','quote_source':'selection'})
    assert started.status_code == 202
    with app.agent_db_conn() as conn:
        saved = conn.execute('SELECT context_json FROM agent_sessions WHERE id=?', (started.json['session']['id'],)).fetchone()
    context = json.loads(saved[0])
    assert context['context_level'] == 'report'
    assert '存档标题' in context['content'] and item['url'] in context['content']
    assert client.get('/api/news/report:two/agent/session').json['session'] is None
    assert client.get('/api/news?per=20').json['total'] == 1
    assert client.delete(base + '/highlights/' + str(mark['id'])).status_code == 200
    assert client.get(base + '/highlights?body_kind=original').json['highlights'] == []


def test_unavailable_sources_and_reports_never_target_unrelated_news(fixture):
    app, client, item = fixture
    with app.db_conn() as conn:
        conn.execute("UPDATE items SET url='https://example.com/replaced' WHERE id=?", (item['id'],))
        conn.execute("UPDATE periodic_reports SET status='pending' WHERE id='two'")
    assert client.get('/api/reports/one').json['report']['source_items'] == {}
    for ident in ('two', 'missing'):
        base = '/api/news/report:' + ident
        assert client.post(base + '/agent/session', json={}).status_code == 404
        assert client.post(base + '/notes', json={'note':'no'}).status_code == 404
    assert 'reader' not in client.get('/api/reports/two').json['report']


def test_deleting_report_cleans_its_reading_state_only(fixture):
    app, client, item = fixture
    base = '/api/news/report:one'
    client.post(base+'/notes', json={'note':'日报想法'})
    client.post('/api/news/'+item['id']+'/notes', json={'note':'新闻想法'})
    client.post(base+'/highlights', json={'body_kind':'original','start_offset':0,'end_offset':4,'selected_text':'重复判断'})
    session = client.post(base+'/agent/session', json={}).json['session']
    token = client.get('/api/reports/one').json['database_token']
    deleted = client.delete('/api/reports/one', json={'database_token':token})
    assert deleted.status_code == 200
    assert client.get('/api/reports/one').status_code == 404
    assert client.get('/api/news/'+item['id']+'/detail').json['notes'][0]['note'] == '新闻想法'
    with app.db_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM article_highlights WHERE url='newsreader-report:one'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM article_notes WHERE url='newsreader-report:one'").fetchone()[0] == 0
    with app.agent_db_conn() as conn:
        assert not conn.execute('SELECT 1 FROM agent_sessions WHERE id=?',(session['id'],)).fetchone()


def test_reading_completion_is_explicit_persistent_and_report_scoped(fixture):
    app, client, item = fixture
    original = client.get('/api/reports/one').json
    token = original['database_token']
    assert original['report']['read_completed_at'] is None
    rejected = client.post('/api/reports/one/read', json={'database_token': token})
    assert rejected.status_code == 409 and rejected.json['error'] == 'report_sources_unread'
    client.patch('/api/news/' + item['id'] + '/state', json={'read': True})
    before = client.get('/api/news/' + item['id'] + '/detail').json
    saved = client.post('/api/reports/one/read', json={'database_token': token})
    assert saved.status_code == 200
    completed_at = saved.json['report']['read_completed_at']
    assert completed_at
    assert client.post('/api/reports/one/read', json={'database_token': token}).json == saved.json
    with app.db_conn() as conn:
        app.periodic_reports.initialize(conn)
    restored = client.get('/api/reports/one').json['report']
    assert restored['read_completed_at'] == completed_at
    assert restored['content'] == original['report']['content']
    assert restored['updated_at'] == original['report']['updated_at']
    assert client.get('/api/reports/two').json['report']['read_completed_at'] is None
    rows = {row['id']: row for row in client.get('/api/reports?kind=daily').json['items']}
    assert rows['one']['read_completed_at'] == completed_at
    assert rows['two']['read_completed_at'] is None
    after = client.get('/api/news/' + item['id'] + '/detail').json
    for key in ('read_at', 'read_later_at', 'read_later_done_at', 'important_at', 'favorite_at'):
        assert after[key] == before[key]


@pytest.mark.parametrize('target,token_kind,status,error', [
    ('one', 'wrong', 409, 'report_database_changed'),
    ('one', 'missing', 409, 'report_database_changed'),
    ('missing', 'valid', 404, 'report_not_found'),
    ('two', 'valid', 409, 'report_read_unavailable'),
])
def test_reading_completion_rejects_invalid_requests_without_mutation(fixture, target, token_kind, status, error):
    app, client, item = fixture
    token = client.get('/api/reports/one').json['database_token']
    with app.db_conn() as conn:
        conn.execute("UPDATE periodic_reports SET status='failed' WHERE id='two'")
    body = {} if token_kind == 'missing' else {'database_token': token if token_kind == 'valid' else 'wrong'}
    response = client.post('/api/reports/' + target + '/read', json=body)
    assert response.status_code == status and response.json['error'] == error
    assert client.post('/api/reports/one/read', json=['invalid']).status_code == 400
    with app.db_conn() as conn:
        assert not conn.execute('SELECT 1 FROM periodic_reports WHERE read_completed_at IS NOT NULL').fetchone()
    assert client.get('/api/news/' + item['id'] + '/detail').json['read_at'] is None


def test_reading_completion_checks_briefs_but_not_unused_material(fixture):
    app, client, item = fixture
    token = client.get('/api/reports/one').json['database_token']
    with app.db_conn() as conn:
        base = dict(conn.execute('SELECT * FROM items WHERE id=?', (item['id'],)).fetchone())
        content = json.loads(conn.execute("SELECT result_json FROM periodic_reports WHERE id='one'").fetchone()[0])
        for ref, ident in [('N2', 'brief'), ('N3', 'unused')]:
            news = {**base, 'id': ident, 'url': 'https://example.com/' + ident}
            conn.execute(f"INSERT INTO items ({','.join(news)}) VALUES ({','.join('?' for _ in news)})", tuple(news.values()))
            content['sources'].append({'ref': ref, 'id': ident, 'url': news['url'], 'title': ident})
        content['briefs'][0]['refs'] = ['N2']
        conn.execute("UPDATE periodic_reports SET result_json=? WHERE id='one'", (json.dumps(content),))
    client.post('/api/news/mark-read-by-ids', json={'item_ids': [item['id']]})
    assert client.post('/api/reports/one/read', json={'database_token': token}).json['error'] == 'report_sources_unread'
    client.post('/api/news/mark-read-by-ids', json={'item_ids': ['brief']})
    assert client.post('/api/reports/one/read', json={'database_token': token}).status_code == 200
    assert client.get('/api/news/unused/detail').json['read_at'] is None


def test_reading_completion_with_unavailable_sources_does_not_target_stale_ids(fixture):
    app, client, item = fixture
    token = client.get('/api/reports/one').json['database_token']
    with app.db_conn() as conn:
        conn.execute("UPDATE items SET url='https://example.com/replaced' WHERE id=?", (item['id'],))
    assert client.post('/api/reports/one/read', json={'database_token': token}).status_code == 200
    assert client.get('/api/news/' + item['id'] + '/detail').json['read_at'] is None


def test_existing_reports_migrate_without_inventing_reading_completion(fixture):
    app, client, item = fixture
    client.patch('/api/news/' + item['id'] + '/state', json={'read': True})
    with app.db_conn() as conn:
        conn.execute('ALTER TABLE periodic_reports DROP COLUMN read_completed_at')
        app.periodic_reports.initialize(conn)
        rows = conn.execute('SELECT read_completed_at FROM periodic_reports').fetchall()
    assert len(rows) == 2 and all(row[0] is None for row in rows)
