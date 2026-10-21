"""Real result transactions and two-library notification routing, using a fake CLI."""
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_api import _setup_agent_api_fixture, _install_fake_oar


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    module, client, item, news_path, agent_path = _setup_agent_api_fixture(tmp_path, monkeypatch)
    _install_fake_oar(tmp_path, monkeypatch)
    return module, client, item, news_path, agent_path


def submit(fixture, **extra):
    module, client, item, *_ = fixture
    response = client.post(f"/api/news/{item['id']}/agent/jobs", json={"question": "研究原文", **extra})
    assert response.status_code == 202, response.json
    return response.json


def listed(client):
    response = client.get('/api/agent/notifications')
    assert response.status_code == 200
    return response.json


def confirm(client, snapshot, ids=None, dismiss=False):
    return client.post('/api/agent/notifications/read', json={
        'db_scope': snapshot['db_scope'], 'ids': ids or [row['id'] for row in snapshot['notifications']], 'dismiss': dismiss,
    })


@pytest.mark.parametrize('mode,kind', [('success', 'reply'), ('auth', 'failed'), ('malformed', 'failed'), ('empty_terminal', 'failed')])
def test_result_notification_is_persistent_atomic_and_deduplicated(fixture, monkeypatch, mode, kind):
    module, client, item, *_ = fixture
    monkeypatch.setenv('OAR_FAKE_MODE', mode)
    submitted = submit(fixture)
    assert listed(client)['notifications'] == []
    assert module.process_pending_agent_once()
    first = listed(client)
    row, = first['notifications']
    assert row['kind'] == kind
    assert row['session_id'] == submitted['session']['id']
    with module.agent_db_conn() as conn:
        message = conn.execute('SELECT * FROM agent_messages WHERE id=?', (row['message_id'],)).fetchone()
        assert message['job_id'] == submitted['job_id']
        assert message['status'] == ('succeeded' if kind == 'reply' else 'failed')
        module.agent_notifications.record(conn, submitted['job_id'])
    assert listed(module.app.test_client()) == first
    target = client.get(f"/api/agent/notifications/{row['id']}/target", query_string={'db_scope': first['db_scope']})
    assert target.status_code == 200
    assert target.json['agent']['session']['id'] == row['session_id']
    assert len(target.json['agent']['jobs']) == 1  # Reading never creates a session or job.
    assert confirm(client, first).status_code == 200
    assert listed(client)['notifications'] == []
    with module.agent_db_conn() as conn:
        module.agent_notifications.record(conn, submitted['job_id'])
    assert listed(client)['notifications'] == []


def test_bulk_snapshot_leaves_new_reply_and_news_flags_unchanged(fixture):
    module, client, item, *_ = fixture
    before = client.get(f"/api/news/{item['id']}/detail").json
    first = submit(fixture)
    module.process_pending_agent_once()
    snapshot = listed(client)
    submit(fixture, session_id=first['session']['id'])
    module.process_pending_agent_once()
    assert len(listed(client)['notifications']) == 2
    assert confirm(client, snapshot, dismiss=True).status_code == 200
    remaining, = listed(client)['notifications']
    assert remaining['id'] != first['job_id']
    after = client.get(f"/api/news/{item['id']}/detail").json
    for key in ['read_at', 'important_at', 'favorite_at', 'read_later_at']:
        assert after[key] == before[key]


def test_stop_and_historical_jobs_do_not_notify_but_restart_does(fixture):
    module, client, item, *_ = fixture
    stopped = submit(fixture)
    assert client.post(f"/api/news/{item['id']}/agent/jobs/{stopped['job_id']}/stop").status_code == 200
    assert module.mark_agent_jobs_interrupted() == 0
    assert listed(client)['notifications'] == []
    historical = submit(fixture)
    with module.agent_db_conn() as conn:
        conn.execute("UPDATE agent_jobs SET notify_enabled=0, db_scope='' WHERE id=?", (historical['job_id'],))
    assert module.mark_agent_jobs_interrupted() == 1
    assert listed(client)['notifications'] == []
    current = submit(fixture)
    assert module.mark_agent_jobs_interrupted() == 1
    row, = listed(client)['notifications']
    assert row['id'] == current['job_id'] and row['kind'] == 'failed'
    assert module.mark_agent_jobs_interrupted() == 0
    assert len(listed(client)['notifications']) == 1


def test_finishing_job_uses_original_library_and_same_id_other_library_is_isolated(fixture, tmp_path, monkeypatch):
    module, client, item, original, *_ = fixture
    submitted = submit(fixture)
    scope_a = module.agent_library_scope()
    other = tmp_path / 'another-news.sqlite3'
    shutil.copy2(original, other)
    monkeypatch.setattr(module, 'DB_PATH', other)
    assert module.process_pending_agent_once()
    scope_b = module.agent_library_scope()
    assert scope_a != scope_b
    assert listed(client)['notifications'] == []
    path = f"/api/news/{item['id']}/agent"
    assert client.get(path + '/session').json['session'] is None
    assert client.get(path + f"/jobs/{submitted['job_id']}").status_code == 404
    assert client.get(path + '/session', query_string={'session_id': submitted['session']['id']}).status_code == 410
    assert client.get(f"/api/agent/notifications/{submitted['job_id']}/target", query_string={'db_scope': scope_a}).status_code == 409
    assert client.post('/api/agent/notifications/read', json={'db_scope': scope_b, 'ids': [submitted['job_id']], 'dismiss': True}).status_code == 409
    other_session = client.post(path + '/session', json={}).json['session']
    assert other_session['id'] != submitted['session']['id']
    assert client.delete(path + '/session').status_code == 200
    assert client.delete('/api/agent/sessions').status_code == 200
    monkeypatch.setattr(module, 'DB_PATH', original)
    assert client.get(path + '/session').json['session']['id'] == submitted['session']['id']
    assert len(listed(client)['notifications']) == 1


def test_expired_or_missing_target_preserves_notice_until_explicit_dismiss(fixture):
    module, client, item, *_ = fixture
    submitted = submit(fixture)
    module.process_pending_agent_once()
    snapshot = listed(client)
    with module.agent_db_conn() as conn:
        conn.execute("UPDATE agent_sessions SET expires_at='2000-01-01 00:00:00' WHERE id=?", (submitted['session']['id'],))
    url = f"/api/agent/notifications/{submitted['job_id']}/target"
    assert client.get(url, query_string={'db_scope': snapshot['db_scope']}).status_code == 410
    assert confirm(client, snapshot).status_code == 409
    assert len(listed(client)['notifications']) == 1
    assert confirm(client, snapshot, dismiss=True).status_code == 200
    submit(fixture)
    module.process_pending_agent_once()
    snapshot = listed(client)
    with module.db_conn() as conn:
        conn.execute('DELETE FROM items WHERE id=?', (item['id'],))
    row, = snapshot['notifications']
    assert client.get(f"/api/agent/notifications/{row['id']}/target", query_string={'db_scope': snapshot['db_scope']}).status_code == 404
    assert confirm(client, snapshot).status_code == 409
    assert len(listed(client)['notifications']) == 1


def test_failed_notice_insert_rolls_back_saved_result(fixture, monkeypatch):
    module, client, *_ = fixture
    submitted = submit(fixture)
    def unavailable(*_):
        raise RuntimeError('fixture notification write failed')
    monkeypatch.setattr(module.agent_notifications, 'record', unavailable)
    with pytest.raises(RuntimeError, match='fixture notification write failed'):
        module.process_pending_agent_once()
    with module.agent_db_conn() as conn:
        job = conn.execute('SELECT * FROM agent_jobs WHERE id=?', (submitted['job_id'],)).fetchone()
        message = conn.execute("SELECT * FROM agent_messages WHERE job_id=? AND role='assistant'", (submitted['job_id'],)).fetchone()
        assert job['status'] != 'succeeded' and message['status'] != 'succeeded'
        assert conn.execute('SELECT COUNT(*) FROM agent_notifications').fetchone()[0] == 0


def test_legacy_read_does_not_bind_and_first_followup_claims_only_session(fixture):
    module, client, item, *_ = fixture
    first = submit(fixture)
    module.process_pending_agent_once()
    with module.agent_db_conn() as conn:
        conn.execute('DELETE FROM agent_notifications')
        conn.execute("UPDATE agent_sessions SET db_scope='' WHERE id=?", (first['session']['id'],))
        conn.execute("UPDATE agent_jobs SET db_scope='', notify_enabled=0")
    path = f"/api/news/{item['id']}/agent/session"
    assert client.get(path).json['session']['db_scope'] == ''
    assert client.get(f"/api/news/{item['id']}/detail").json['agent']['session']['db_scope'] == ''
    assert client.delete(path).status_code == 409
    continued = submit(fixture, session_id=first['session']['id'])
    assert continued['session']['id'] == first['session']['id']
    assert continued['session']['db_scope'] == module.agent_library_scope()
    with module.agent_db_conn() as conn:
        assert conn.execute('SELECT db_scope FROM agent_jobs WHERE id=?', (first['job_id'],)).fetchone()[0] == ''
    assert listed(client)['notifications'] == []
    module.process_pending_agent_once()
    assert len(listed(client)['notifications']) == 1


def test_two_libraries_cannot_claim_same_legacy_session(fixture, monkeypatch):
    module, client, item, *_ = fixture
    path = f"/api/news/{item['id']}/agent"
    session = client.post(path + '/session', json={}).json['session']
    with module.agent_db_conn() as conn:
        conn.execute("UPDATE agent_sessions SET db_scope='' WHERE id=?", (session['id'],))
    local = threading.local()
    monkeypatch.setattr(module, 'agent_library_scope', lambda: local.scope)
    def follow(scope):
        local.scope = scope
        with module.app.test_client() as peer:
            return peer.post(path + '/jobs', json={'session_id': session['id'], 'question': '实际续问'}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(follow, ['library-a', 'library-b']))
    assert sorted(codes) == [202, 409]
    with module.agent_db_conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM agent_jobs').fetchone()[0] == 1
        assert conn.execute('SELECT db_scope FROM agent_sessions').fetchone()[0] in ['library-a', 'library-b']


def test_legacy_url_mismatch_is_not_read_or_claimed(fixture):
    module, client, item, *_ = fixture
    first = client.post(f"/api/news/{item['id']}/agent/session", json={}).json
    with module.agent_db_conn() as conn:
        conn.execute("UPDATE agent_sessions SET db_scope='', url='https://example.com/other' WHERE id=?", (first['session']['id'],))
    assert client.get(f"/api/news/{item['id']}/agent/session").json['session'] is None
    result = client.post(f"/api/news/{item['id']}/agent/jobs", json={'session_id': first['session']['id'], 'question': '不能接错历史'})
    assert result.status_code == 404


def test_legacy_empty_session_retains_executor_on_first_claim(fixture, monkeypatch):
    import copy
    module, client, item, *_ = fixture
    original = client.post(f"/api/news/{item['id']}/agent/session", json={}).json['session']
    with module.agent_db_conn() as conn:
        conn.execute("UPDATE agent_sessions SET db_scope='' WHERE id=?", (original['id'],))
    settings = copy.deepcopy(module.current_runtime_settings())
    settings['llm']['pi_chat']['model'] = 'changed-default'
    monkeypatch.setattr(module, 'current_runtime_settings', lambda: settings)
    response = submit(fixture, session_id=original['id'])
    assert response['session']['model'] == original['model']
    assert response['session']['db_scope'] == module.agent_library_scope()


def test_codex_completion_uses_same_notification_transaction(fixture, tmp_path, monkeypatch):
    from test_codex_client import fake_codex, select_codex
    module, client, *_ = fixture
    fake_codex(tmp_path, monkeypatch)
    select_codex(module, monkeypatch)
    submitted = submit(fixture)
    assert module.process_pending_agent_once()
    row, = listed(client)['notifications']
    assert row['id'] == submitted['job_id'] and row['kind'] == 'reply'
    assert row['preview'] == '有效最终结论'


def test_specified_read_only_session_does_not_substitute_current_session(fixture):
    module, client, item, *_ = fixture
    first = submit(fixture)
    module.process_pending_agent_once()
    with module.agent_db_conn() as conn:
        conn.execute("UPDATE agent_sessions SET status='archived' WHERE id=?", (first['session']['id'],))
    path = f"/api/news/{item['id']}/agent/session"
    current = client.post(path, json={}).json['session']
    assert current['id'] != first['session']['id']
    exact = client.get(path, query_string={'session_id': first['session']['id'], 'db_scope': module.agent_library_scope()})
    assert exact.status_code == 200
    assert exact.json['session']['id'] == first['session']['id']
    assert exact.json['messages'][-1]['content'] == '完成'
    with module.agent_db_conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM agent_sessions').fetchone()[0] == 2
        assert conn.execute('SELECT COUNT(*) FROM agent_jobs').fetchone()[0] == 1
