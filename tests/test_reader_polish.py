import pytest
from test_api import _setup_agent_api_fixture


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    return _setup_agent_api_fixture(tmp_path, monkeypatch)


def test_original_highlights_persist_and_old_schema_migrates_once(fixture):
    app, client, item, *_ = fixture
    path = f"/api/news/{item['id']}"
    body = '📌 原文第一段\n\n第二段 **重点** 与后续判断。'
    with app.db_conn() as conn:
        conn.execute('UPDATE article_details SET content=? WHERE url=?', (body, item['url']))
        # Emulate the previous CHECK constraint, without discarding an existing annotation.
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='article_highlights'").fetchone()[0]
        conn.execute('DROP TABLE article_highlights')
        conn.execute(sql.replace(", 'original'", ''))
        conn.execute("""INSERT INTO article_highlights(id,url,body_kind,content_hash,start_offset,end_offset,selected_text,color,annotation_text,created_at,updated_at)
            VALUES(7,?,'ai_zh','old-hash',0,2,'历史','pink','保留批注','2026-10-01','2026-10-02')""", (item['url'],))
    app.ensure_db()
    with app.db_conn() as conn:
        schema_version = conn.execute('PRAGMA schema_version').fetchone()[0]
    app.ensure_db()
    with app.db_conn() as conn:
        assert conn.execute('PRAGMA schema_version').fetchone()[0] == schema_version
        old = conn.execute('SELECT * FROM article_highlights WHERE id=7').fetchone()
        assert old['annotation_text'] == '保留批注' and old['color'] == 'pink'
    start = body.index('重点')
    result = client.post(path + '/highlights', json={'body_kind': 'original', 'selected_text': '重点', 'start_offset': start, 'end_offset': start + 2})
    assert result.status_code == 201
    ident = result.json['highlight']['id']
    assert client.get(path + '/detail').json['highlight_surfaces']['original']['highlights'][0]['id'] == ident
    with app.db_conn() as conn:
        conn.execute('UPDATE article_details SET content=? WHERE url=?', ('新增引言。\n\n' + body, item['url']))
    moved = client.get(path + '/highlights?body_kind=original').json['highlights'][0]
    assert moved['status'] == 'active'
    assert moved['resolved_start_offset'] > start
    assert client.delete(path + f'/highlights/{ident}').status_code == 200


@pytest.mark.parametrize('source', ['points', 'conclusion'])
def test_digest_quote_sources_are_retained(fixture, source):
    _, client, item, *_ = fixture
    path = f"/api/news/{item['id']}"
    response = client.post(path + '/notes', json={'note': '对精读的判断', 'quote': {'text': '精读片段', 'source': source, 'paragraph': 1}})
    assert response.status_code == 200
    assert client.get(path + '/detail').json['notes'][0]['quote_source'] == source


def test_favorite_and_source_counts_follow_independent_states(fixture):
    _, client, item, *_ = fixture
    path = f"/api/news/{item['id']}"
    client.patch(path + '/state', json={'favorite': True, 'read_later': True, 'read': True})
    summary = client.get('/api/nav-summary').json['summary']
    assert summary['favorites'] == 1 and summary['feed_unread'] == 0 and summary['read_later_unread'] == 1
    for collection, read_filter, expected in [('feed', 'unread', 0), ('read_later', 'unread', 1), ('favorites', 'all', 1)]:
        result = client.get(f'/api/sources?collection={collection}&read_filter={read_filter}').json
        assert sum(source['count'] for source in result['sources']) == expected
    client.patch(path + '/state', json={'favorite': False, 'read_later': False})
    assert client.get('/api/nav-summary').json['summary']['favorites'] == 0
