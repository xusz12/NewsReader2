"""Thought cards retain authorship, quotations and old-client compatibility."""
import pytest
from test_api import _setup_agent_api_fixture


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    return _setup_agent_api_fixture(tmp_path, monkeypatch)


def test_quote_metadata_round_trip_and_delete_cleanup(fixture):
    app, client, item, *_ = fixture
    path = f"/api/news/{item['id']}"
    client.put(path + '/note', json={'note': '历史想法'})
    response = client.post(path + '/notes', json={
        'note': '我的判断', 'author': 'agent', 'archive_summary': '冒充归档',
        'quote': {'text': '正文 <script>不执行</script>', 'source': 'original', 'paragraph': 2},
    })
    assert response.status_code == 200
    entry = response.json['notes'][-1]
    assert entry['author'] == 'user'
    assert entry['archive_summary'] == ''
    assert entry['quote_text'] == '正文 <script>不执行</script>'
    assert entry['quote_source'] == 'original'
    assert entry['quote_paragraph'] == 2
    assert client.get(path + '/detail').json['notes'][-1] == entry
    assert '我的判断' in response.json['note']['note']
    assert client.delete(path + '/notes/' + entry['id']).json['note_count'] == 1
    with app.db_conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM article_note_metadata').fetchone()[0] == 0
    client.post(path + '/notes', json={'note': '带译文', 'quote': {'text': '译文片段', 'source': 'translated'}})
    client.put(path + '/note', json={'note': '旧客户端整体替换'})
    assert client.get(path + '/detail').json['notes'][0]['author'] == 'user'
    with app.db_conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM article_note_metadata').fetchone()[0] == 0


@pytest.mark.parametrize('quote', [[], {}, {'text': 'x', 'source': 'unknown'},
    {'text': 'x' * 6001, 'source': 'original'}, {'text': ' ', 'source': 'original'},
    {'text': 'x', 'source': 'original', 'paragraph': True},
    {'text': 'x', 'source': 'original', 'paragraph': -1}])
def test_invalid_quotes_are_rejected_without_saving(fixture, quote):
    _, client, item, *_ = fixture
    path = f"/api/news/{item['id']}"
    assert client.post(path + '/notes', json={'note': '想法', 'quote': quote}).status_code == 400
    assert client.get(path + '/detail').json['notes'] == []


def test_archive_appends_agent_card_and_preserves_multiline_summary(fixture, monkeypatch):
    app, client, item, *_ = fixture
    path = f"/api/news/{item['id']}"
    # An article can accumulate thoughts over time; the old aggregate's 5k cap must not block a new card.
    client.post(path + '/notes', json={'note': '历史研究' * 1500})
    summary = '**研读结论**\n\n- 已知：风险有所缓解。\n- 待核实：传导是否持续。'
    calls = []
    def archive(**kwargs):
        calls.append(kwargs)
        return {'provider': 'pi', 'model': 'fixture', 'summary': summary}
    monkeypatch.setattr(app, 'run_pi_chat_archive', archive)
    settings = app.current_runtime_settings()
    settings['agent']['runtime'] = 'pi'
    monkeypatch.setattr(app, 'current_runtime_settings', lambda: settings)
    payload = {'messages': [{'role': 'user', 'content': '风险如何？'}, {'role': 'assistant', 'content': '有所缓解，传导待核实。'}]}
    response = client.post(path + '/chat/archive', json=payload)
    assert response.status_code == 200
    entry = response.json['notes'][-1]
    assert entry['author'] == 'agent'
    assert entry['archive_summary'] == summary
    assert entry['quote_text'] == ''
    assert response.json['note_count'] == 2
    assert '【Chat 归档｜' in response.json['note']['note']
    assert calls[0]['messages'] == payload['messages']
    assert client.get(path + '/detail').json['notes'][-1] == entry
    monkeypatch.setattr(app, 'run_pi_chat_archive', lambda **kwargs: {'summary': ''})
    assert client.post(path + '/chat/archive', json=payload).status_code == 502
    assert len(client.get(path + '/detail').json['notes']) == 2
    client.delete(path + '/notes/' + entry['id'])
    assert len(client.get(path + '/detail').json['notes']) == 1


def test_old_entries_upgrade_without_changing_text_or_times(fixture):
    app, client, item, *_ = fixture
    with app.db_conn() as conn:
        conn.execute('DROP TRIGGER article_note_metadata_cleanup')
        conn.execute('DROP TABLE article_note_metadata')
        # Existing clients can still write all five positional values.
        conn.execute('INSERT INTO article_note_entries VALUES (?, ?, ?, ?, ?)',
            ('old-archive', item['url'], '【Chat 归档｜2026-10-14 16:30】\n历史归档', '2026-10-14 16:30:00', '2026-10-14 16:30:00'))
        app.sync_article_notes(conn, item['url'])
    app.ensure_db()
    app.ensure_db()
    entry = client.get(f"/api/news/{item['id']}/detail").json['notes'][0]
    assert entry['author'] == 'agent'
    assert entry['archive_summary'] == '历史归档'
    assert entry['created_at'] == '2026-10-14 16:30:00'
    assert entry['note'] == '【Chat 归档｜2026-10-14 16:30】\n历史归档'
