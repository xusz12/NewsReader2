"""Browsing acknowledgements are permanent; full-text completion is independent."""
import pytest
from test_api import _setup_agent_api_fixture


@pytest.fixture
def env(tmp_path, monkeypatch):
    app, client, item, *_ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    return app, client, item, f'/api/news/{item["id"]}/state'


@pytest.mark.parametrize('already_read', [False, True])
@pytest.mark.parametrize('other', [{}, {'favorite': True}, {'important': True}, {'read_later': True},
                                  {'read_later': False, 'read_later_done': True}])
def test_reverse_requests_fail_atomically(env, already_read, other):
    app, client, item, path = env
    if already_read:
        assert client.patch(path, json={'read': True}).status_code == 200
    def snapshot():
        with app.db_conn() as conn:
            return {table: [tuple(row) for row in conn.execute(f'SELECT * FROM {table}')]
                    for table in ('item_state', 'detail_jobs', 'ai_jobs')}
    before = snapshot()
    result = client.patch(path, json={'read': False, **other})
    assert result.status_code == 400
    assert result.json['error'] == 'read_state_irreversible'
    assert snapshot() == before


@pytest.mark.parametrize('action', ['single', 'all', 'ids', 'later'])
def test_repeated_acknowledgements_preserve_first_timestamp(env, action):
    app, client, item, path = env
    stamp = '2026-01-01 12:00:00'
    client.patch(path, json={'read': True, 'favorite': True})
    with app.db_conn() as conn:
        conn.execute('UPDATE item_state SET read_at=? WHERE item_id=?', (stamp, item['id']))
    if action == 'single':
        result = client.patch(path, json={'read': True})
    elif action == 'later':
        result = client.patch(path, json={'read_later': True})
    else:
        result = client.post('/api/news/mark-all-read' if action == 'all' else '/api/news/mark-read-by-ids',
                             json={} if action == 'all' else {'item_ids': [item['id'], item['id']]})
    assert result.status_code == 200
    detail = client.get(f'/api/news/{item["id"]}/detail').json
    assert detail['read_at'] == stamp
    assert detail['favorite_at']
    assert detail['read_later_done_at'] is None


@pytest.mark.parametrize('flag', ['important', 'favorite', 'read_later'])
@pytest.mark.parametrize('already_read', [False, True])
def test_marking_acknowledges_browsing_and_removing_never_reverses_it(env, flag, already_read):
    app, client, item, path = env
    client.patch(path, json={'read_later': False, 'read_later_done': True})
    if already_read:
        client.patch(path, json={'read': True})
    before = client.get(f'/api/news/{item["id"]}/detail').json
    unread_before = client.get('/api/nav-summary').json['summary']['feed_unread']
    added = client.patch(path, json={flag: True}).json
    assert added['read_at']
    if already_read:
        assert added['read_at'] == before['read_at']
    if flag != 'read_later':
        assert added['read_later_done_at'] == before['read_later_done_at']
        assert added['read_later_at'] == before['read_later_at']
    assert client.get('/api/nav-summary').json['summary']['feed_unread'] == unread_before - int(not already_read)
    removed = client.patch(path, json={flag: False}).json
    assert removed['read_at'] == added['read_at']
    assert client.patch(path, json={flag: True}).json['read_at'] == added['read_at']


def test_legacy_market_tag_important_mark_also_acknowledges_browsing(env):
    app, client, item, path = env
    created = client.post('/api/market-tags', json={'display_name': '浏览状态测试'})
    assert created.status_code == 200
    tag = created.json['tag']['key']
    result = client.put(f'/api/news/{item["id"]}/market-tag', json={'tag': tag, 'direction': 'bullish'})
    assert result.status_code == 200
    assert result.json['read_at'] and result.json['important_at']
    canceled = client.patch(path, json={'important': False}).json
    assert canceled['read_at'] == result.json['read_at']
