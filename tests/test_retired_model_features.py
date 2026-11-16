from test_api import _setup_agent_api_fixture


def test_retired_model_routes_are_absent_and_historical_rows_survive_initialization(tmp_path, monkeypatch):
    app, client, item, _, _ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    with app.db_conn() as conn:
        conn.execute("INSERT INTO tracked_topic_daily_summaries(topic_id,date,summary_text,created_at,updated_at) VALUES(91,'2026-10-01','保留旧主题摘要','old','old')")
        conn.execute("INSERT INTO market_tag_summaries(tag_key,range_days,summary_text,created_at,updated_at) VALUES('archived',30,'保留旧板块摘要','old','old')")
        before = {name: [tuple(row) for row in conn.execute('SELECT * FROM ' + name)]
                  for name in ('tracked_topic_daily_summaries', 'market_tag_summaries')}
    for method, url in [('post', '/api/tracked-topics/rule-draft'), ('get', '/api/tracked-topics/91/daily-summaries'),
        ('post', '/api/tracked-topics/91/daily-summaries/2026-10-01/generate'),
        ('get', '/api/market-tags/archived/summary'), ('post', '/api/market-tags/archived/summary/generate')]:
        assert getattr(client, method)(url).status_code == 404
    app.ensure_db()
    with app.db_conn() as conn:
        assert before == {name: [tuple(row) for row in conn.execute('SELECT * FROM ' + name)] for name in before}
    assert client.get('/api/topics').status_code == 200
    assert client.get('/api/market-workbench').status_code == 200
