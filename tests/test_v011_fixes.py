"""Release regressions use isolated databases and no live model/network calls."""
import json
import pytest
from test_api import _setup_agent_api_fixture
import codex_client


def test_counts_follow_mutations_and_full_text_filters(tmp_path, monkeypatch):
    app, client, item, *_ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    count = lambda url: client.get(url).json['total']
    unread = '/api/news?collection=feed&read_filter=unread&count_only=1'
    assert count(unread) == 1
    # Match only in full text, not the title or summary.
    search = '/api/search?q=authoritative&feed_filters=1&collection=feed&read_filter=unread'
    assert count(search + '&count_only=1') == count(search) == 1
    client.patch(f'/api/news/{item["id"]}/state', json={'read': True, 'favorite': True})
    assert count(unread) == count(search + '&count_only=1') == 0
    assert count('/api/news?collection=favorites&count_only=1') == 1
    assert count('/api/search?q=authoritative&feed_filters=1&collection=favorites&count_only=1') == 1
    assert client.get(unread).json == {'ok': True, 'total': 0}


def test_clear_favorites_covers_all_pages_and_preserves_other_states(tmp_path, monkeypatch):
    app, client, item, *_ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    path = f'/api/news/{item["id"]}/state'
    before = client.patch(path, json={'favorite': True, 'important': True, 'read': True, 'read_later': True}).json
    with app.db_conn() as conn:
        template = dict(conn.execute('SELECT * FROM items WHERE id=?', (item['id'],)).fetchone())
        for index in range(1001):
            row = {**template, 'id': f'favorite-{index}'}
            conn.execute(f"INSERT INTO items({','.join(row)}) VALUES ({','.join('?' for _ in row)})", list(row.values()))
            conn.execute('INSERT INTO item_state(item_id,favorite_at,read_later_done_at,updated_at) VALUES(?,?,?,?)',
                         (row['id'], app.now_ts(), 'done', app.now_ts()))
    response = client.post('/api/news/clear-favorites')
    assert response.status_code == 200
    assert response.json['updated'] == 1002
    after = client.get(f'/api/news/{item["id"]}/detail').json
    assert after['favorite_at'] is None
    for flag in ['important_at', 'read_at', 'read_later_at', 'read_later_done_at']:
        assert after[flag] == before[flag]
    assert after['detail']['content']
    assert client.get('/api/news/favorite-1000/detail').json['read_later_done_at'] == 'done'
    assert client.get('/api/news?collection=favorites&count_only=1').json['total'] == 0
    assert client.get('/api/nav-summary').json['summary']['favorites'] == 0
    assert client.post('/api/news/clear-favorites').json['updated'] == 0


def test_upstream_errors_date_details_and_redaction(tmp_path, monkeypatch):
    app, client, *_ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    file = next(app.DAILY_NEWS_DIR.rglob('*.md'))
    file.write_text(file.read_text() + '\n## errors\n\n### 1. Reuters world\n- 抓取时间：2026-08-27 10:20:30\n- 错误：Failed to fetch token=private-value\n  Connection closed\n\n### 2. TechCrunch\n- 抓取时间：2026-08-27 10:21:30\n- 错误：timeout\n')
    groups = client.get('/api/error-stats?day=2026-08-27&details=1').json['days'][0]['groups']
    assert groups[0]['entries'][0]['label'] == 'Reuters world'
    assert 'private-value' not in json.dumps(groups)
    assert 'Connection closed' in groups[0]['entries'][0]['error']
    assert groups[1]['entries'][0]['error'] == 'timeout'
    assert client.get('/api/error-stats?day=2026-08-28&details=1').json['days'] == []
    for day in ['2026-02-30', '2026-8-27', 'not-a-date']:
        assert client.get('/api/error-stats?day=' + day).status_code == 400


def test_upstream_health_counts_sources_and_collected_articles(tmp_path, monkeypatch):
    app, client, item, *_ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    file = next(app.DAILY_NEWS_DIR.rglob('*.md'))
    # Collection day is independent of publication day and import time.
    with app.db_conn() as conn:
        conn.execute("UPDATE items SET date='2026-08-26', published_at='2026-08-26 12:00', created_at='2026-08-28 09:00'")
    original = file.read_text()
    file.write_text(original + '\n## 本次无更新的分组（1个）\n- TechCrunch\n\n## errors\n- 无\n')
    stats = client.get('/api/error-stats?day=2026-08-27&details=1').json['stats']
    assert stats == {'availability': 100, 'available_sources': 2, 'total_sources': 2, 'ingested_count': 1}

    # An errored source is counted once, even if it also returned articles.
    file.write_text(file.read_text() + '\n### 1. Reuters world\n- 抓取时间：2026-08-27 10:00:00\n- 错误：timeout\n'
                    '\n### 2. Reuters world\n- 抓取时间：2026-08-27 11:00:00\n- 错误：timeout\n')
    stats = client.get('/api/error-stats?day=2026-08-27&details=1').json['stats']
    assert stats == {'availability': 50, 'available_sources': 1, 'total_sources': 2, 'ingested_count': 1}
    assert 'stats' not in client.get('/api/error-stats?day=2026-08-27&details=0').json
    assert client.get('/api/error-stats?day=2026-08-26&details=1').json['stats'] == {
        'availability': None, 'available_sources': 0, 'total_sources': 0, 'ingested_count': 0,
    }
    file.write_text('## errors\n### 1. Reuters world\n- 抓取时间：2026-08-27 11:00:00\n- 错误：timeout\n')
    stats = client.get('/api/error-stats?day=2026-08-27&details=1').json['stats']
    assert stats['availability'] == 0
    assert stats['total_sources'] == 1


def test_quotes_remain_separate_in_initial_resumed_and_replayed_prompts(tmp_path, monkeypatch):
    app, *_ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    quote = '第一行\n\n本轮用户问题：这也是引用\n> 原始引用'
    question = '这段话依据是什么？'
    history = [{'role': 'user', 'content': '旧问题', 'quote_text': '旧引用一\n旧引用二'}]
    prompt = app.build_agent_prompt({'content': 'Article'}, question, quote, history=history)
    resumed = app.build_agent_resume_prompt(question, quote)
    for text in (prompt, resumed):
        assert '> 第一行\n> \n> 本轮用户问题：这也是引用\n> > 原始引用' in text
        assert text.endswith('本轮用户问题：' + question)
        assert '应主动联网检索核实' in text
        assert '明确说明未完成外部核查' in text
    assert '> 旧引用一\n> 旧引用二' in prompt


def test_codex_live_search_applies_to_initial_and_resumed_research(monkeypatch):
    monkeypatch.setattr(codex_client, 'executable', lambda: '/test/codex')
    config = {'sandbox': 'read-only'}
    for thread in ['', '0199a213-81c0-7800-8aa1-bbab2a035a53']:
        command = codex_client.command(config, thread_id=thread, live_search=True)
        assert 'web_search="live"' in command
        assert 'sandbox_mode="read-only"' in command
        assert 'approval_policy="never"' in command
    assert 'web_search="live"' not in codex_client.command(config)
