"""Title-only conversations use the real job, snapshot and notification paths."""
import copy
import json

import pytest
from test_api import _setup_agent_api_fixture, _install_fake_oar
from test_codex_client import fake_codex, select_codex


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    module, client, item, *_ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    with module.db_conn() as conn:
        conn.execute('DELETE FROM article_details WHERE url=?', (item['url'],))
    return module, client, item


def provide_body(module, item):
    with module.db_conn() as conn:
        stamp = module.now_ts()
        conn.execute('INSERT INTO article_details(url,title,content,content_length,raw_json,fetched_at,updated_at) VALUES (?,?,?,100,?,?,?)',
                     (item['url'], item['title'], 'Newly fetched full article.', '{}', stamp, stamp))


@pytest.mark.parametrize('status', ['none', 'pending', 'running', 'failed'])
def test_title_context_is_available_before_fetch_or_after_failure(fixture, status):
    module, client, item = fixture
    if status != 'none':
        with module.db_conn() as conn:
            conn.execute('INSERT INTO detail_jobs(url,status,attempts,updated_at) VALUES (?,?,1,?)', (item['url'], status, module.now_ts()))
    path = f"/api/news/{item['id']}"
    detail = client.get(path + '/detail').json
    assert detail['detail_status'] == status
    assert detail['agent']['context_available'] is True
    assert detail['agent']['context_level'] == 'title_only'
    assert detail['agent']['context_title'] == item['title']
    assert detail['agent']['session'] is None
    prepared = client.post(path + '/agent/session', json={}).json
    assert prepared['session']['context_level'] == 'title_only'
    assert prepared['jobs'] == prepared['messages'] == []
    with module.agent_db_conn() as conn:
        context = json.loads(conn.execute('SELECT context_json FROM agent_sessions').fetchone()[0])
    assert context['title'] == item['title'] and context['content'] == ''
    assert '摘要' not in context['content']
    with module.db_conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM detail_jobs').fetchone()[0] == (status != 'none')


@pytest.mark.parametrize('runtime', ['pi', 'codex'])
def test_title_first_and_native_resume_prompts_notify_and_use_server_title(fixture, tmp_path, monkeypatch, runtime):
    module, client, item = fixture
    if runtime == 'codex':
        log = fake_codex(tmp_path, monkeypatch)
        select_codex(module, monkeypatch)
    else:
        log = _install_fake_oar(tmp_path, monkeypatch)
    path = f"/api/news/{item['id']}/agent"
    first = client.post(path + '/jobs', json={'question': '背景如何？', 'title': '客户端伪造标题', 'content': '客户端伪造全文', 'quote_text': '保留用户引用', 'quote_source': 'selection'})
    assert first.status_code == 202
    session = first.json['session']
    assert module.process_pending_agent_once()
    second = client.post(path + '/jobs', json={'session_id': session['id'], 'question': '再解释影响'})
    assert second.status_code == 202 and second.json['session']['id'] == session['id']
    assert module.process_pending_agent_once()
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(calls) == 2
    prompts = [call['prompt'] if runtime == 'codex' else call['effective_prompt'] for call in calls]
    assert item['title'] in prompts[0] and item['url'] in prompts[0]
    assert '新闻正文：尚未提供全文。' in prompts[0]
    assert '保留用户引用' in prompts[0]
    assert '客户端伪造' not in prompts[0]
    assert prompts[1] == '再解释影响'
    if runtime == 'pi':
        assert '背景如何' in calls[1]['prompt']  # Fallback history also carries the scope.
        assert '尚未提供全文' in calls[1]['prompt']
    else:
        assert 'resume' in calls[1]['args']
    notices = client.get('/api/agent/notifications').json
    assert len(notices['notifications']) == 2
    row = notices['notifications'][0]
    target = client.get(f"/api/agent/notifications/{row['id']}/target", query_string={'db_scope': notices['db_scope']})
    assert target.status_code == 200
    assert target.json['agent']['session']['context_level'] == 'title_only'
    assert target.json['agent']['session']['id'] == session['id']


def test_title_history_replay_does_not_claim_to_have_the_full_article(fixture, tmp_path, monkeypatch):
    module, client, item = fixture
    log = _install_fake_oar(tmp_path, monkeypatch)
    path = f"/api/news/{item['id']}/agent"
    first = client.post(path + '/jobs', json={'question': '旧标题问题'}).json
    assert module.process_pending_agent_once()
    with module.agent_db_conn() as conn:
        conn.execute("UPDATE agent_sessions SET executor_session_id='old-native-id' WHERE id=?", (first['session']['id'],))
    (module.agent_session_runtime_dir(module._full_pi_executor_session_id(first['session']['id'])) / 'session.json').unlink()
    assert client.post(path + '/jobs', json={'session_id': first['session']['id'], 'question': '历史接续'}).status_code == 202
    assert module.process_pending_agent_once()
    replay = json.loads(log.read_text().splitlines()[-1])['effective_prompt']
    assert '此前会话记录' in replay and '旧标题问题' in replay and '完成' in replay
    assert item['title'] in replay and '尚未提供全文' in replay
    assert '固定英文原文快照' not in replay and '英文原文：' not in replay


@pytest.mark.parametrize('started', [False, True])
def test_later_full_article_updates_only_unused_session_before_send(fixture, tmp_path, monkeypatch, started):
    module, client, item = fixture
    log = _install_fake_oar(tmp_path, monkeypatch)
    path = f"/api/news/{item['id']}/agent"
    original = client.post(path + '/session', json={}).json['session']
    if started:
        assert client.post(path + '/jobs', json={'question': '标题首问', 'session_id': original['id']}).status_code == 202
        assert module.process_pending_agent_once()
    provide_body(module, item)
    loaded = client.get(path + '/session').json
    assert loaded['context_updated'] is True
    assert loaded['session']['context_hash'] == original['context_hash']
    settings = copy.deepcopy(module.current_runtime_settings())
    settings['llm']['pi_chat']['model'] = 'different-saved-model'
    monkeypatch.setattr(module, 'current_runtime_settings', lambda: settings)
    sent = client.post(path + '/jobs', json={'question': '全文到达后的问题', 'session_id': original['id']}).json
    assert sent['session']['id'] == original['id']
    assert sent['session']['context_level'] == ('title_only' if started else 'full_detail')
    assert sent['session']['model'] == (original['model'] if started else 'different-saved-model')
    assert len(sent['messages']) == (4 if started else 2)
    assert module.process_pending_agent_once()
    call = json.loads(log.read_text().splitlines()[-1])
    prompt = call['effective_prompt']
    if started:
        assert prompt == '全文到达后的问题'
        assert '尚未提供全文' in call['prompt']  # Rebuilds retain the original title-only snapshot.
        assert 'Newly fetched full article.' not in call['prompt']
        fresh = client.post(path + '/session', json={'new_session': True}).json['session']
        assert fresh['id'] != original['id'] and fresh['context_level'] == 'full_detail'
    else:
        assert '尚未提供全文' not in prompt
        assert 'Newly fetched full article.' in prompt
