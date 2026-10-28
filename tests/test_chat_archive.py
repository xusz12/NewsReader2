"""Archives retain full discussion context while saving a concise synthesis."""
from pathlib import Path

import pytest

from test_api import _setup_agent_api_fixture


@pytest.fixture
def archive_api(tmp_path, monkeypatch):
    app, client, item, *_ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    settings = app.current_runtime_settings()
    settings['agent']['runtime'] = 'pi'
    monkeypatch.setattr(app, 'current_runtime_settings', lambda: settings)
    return app, client, f"/api/news/{item['id']}", settings


@pytest.mark.parametrize('runtime', ['pi', 'codex'])
@pytest.mark.parametrize('message_source', ['legacy', 'persisted'])
def test_full_research_and_quote_reach_archive_model(archive_api, monkeypatch, runtime, message_source):
    app, client, path, settings = archive_api
    settings['agent']['runtime'] = runtime
    captured = {}
    summary = '政策利率下调未必降低企业实际融资成本：关键在于银行是否把资金成本下降传导给企业，以及信用风险溢价是否抵消降息效果。现有讨论尚缺企业实际贷款利率的证据。'

    def pi_run(command, request, env, timeout):
        captured['prompt'] = request['prompt']
        return summary

    def codex_run(config, prompt, **kwargs):
        captured.update(prompt=prompt, **kwargs)
        return summary, ''

    monkeypatch.setattr(app, 'run_oar_once', pi_run)
    monkeypatch.setattr(app.codex_client, 'run_once', codex_run)
    messages = []
    for index in range(100):
        messages.extend([
            {'role': 'user', 'content': f'研究问题 {index}：融资成本的影响因素是什么？'},
            {'role': 'assistant', 'content': f'研究发现 {index}：仍需区分政策与市场因素。'},
        ])
    messages[0]['quote_text'] = '  政策利率下调不一定降低企业实际融资成本。  '
    messages[1]['content'] += '关键依据及适用条件。' * 600
    messages[-2]['content'] = '最终澄清：关注企业融资成本，不是资产价格。'
    body = {'messages': messages}
    if message_source == 'persisted':
        body = {'agent_session_id': store_conversation(app, client, path, messages)}
    response = client.post(path + '/chat/archive', json=body)

    assert response.status_code == 200
    assert response.json['provider'] == runtime
    assert response.json['archive_summary'] == summary
    prompt = captured['prompt']
    assert messages[0]['quote_text'].strip() in prompt
    # Both the initial intent and later clarification survive, along with long answers.
    position = 0
    for message in messages:
        position = prompt.index(message['content'], position) + len(message['content'])
    assert prompt.index('对话记录结束。输出前检查：') > position
    if runtime == 'codex':
        assert captured['ephemeral'] is True
        assert 'thread_id' not in captured
    assert client.get(path + '/detail').json['notes'][-1]['archive_summary'] == summary


@pytest.mark.parametrize('source_length', [2, 299, 300, 301, 1000])
def test_archive_summary_boundary_preserves_saved_findings_on_failure(archive_api, monkeypatch, source_length):
    app, client, path, _ = archive_api
    messages = [{'role': 'user', 'content': '问', 'quote_text': '引用资料' * 100},
                {'role': 'assistant', 'content': '研' * (source_length - 1)}]
    summary = '研' * min(300, source_length)
    monkeypatch.setattr(app, 'run_pi_chat_archive', lambda **kwargs: {'summary': summary})
    accepted = client.post(path + '/chat/archive', json={'messages': messages})
    assert accepted.status_code == 200
    saved_notes = client.get(path + '/detail').json['notes']
    assert saved_notes[-1]['archive_summary'] == summary

    summary += '究'
    rejected = client.post(path + '/chat/archive', json={'messages': messages})
    assert rejected.status_code == 502
    assert rejected.json['error'] == 'invalid_archive_summary'
    assert client.get(path + '/detail').json['notes'] == saved_notes


@pytest.mark.parametrize('runtime', ['pi', 'codex'])
@pytest.mark.parametrize('message_source', ['legacy', 'persisted'])
@pytest.mark.parametrize('source_length', [20, 400])
@pytest.mark.parametrize('outcome', ['valid', 'compressed', 'still_long', 'empty', 'empty_after_retry', 'retry_error'])
def test_archive_length_retry_and_atomic_save(archive_api, monkeypatch, runtime, message_source, source_length, outcome):
    app, client, path, settings = archive_api
    settings['agent']['runtime'] = runtime
    messages = [{'role': 'user', 'content': '  研究结论？  ', 'quote_text': '引用资料' * 100},
                {'role': 'assistant', 'content': '研' * (source_length - 5)}]
    limit = min(300, source_length)
    body = {'messages': messages}
    if message_source == 'persisted':
        session_id = store_conversation(app, client, path, messages)
        # Neither a longer client copy nor changed defaults can expand the budget or change runtime.
        body = {'agent_session_id': session_id, 'messages': [{'role': 'assistant', 'content': '副本' * 300}]}
        settings['agent']['runtime'] = 'codex' if runtime == 'pi' else 'pi'
    before_session = client.get(path + '/agent/session').json
    client.post(path + '/notes', json={'note': '已存想法'})
    before_notes = client.get(path + '/detail').json['notes']
    calls, states = [], []
    summary = '结论仍待核实。'
    oversized = '长' * (limit + 1)

    def generate(prompt):
        calls.append(prompt)
        assert len(calls) <= 2
        if outcome == 'valid':
            return summary
        if outcome == 'empty':
            return app.CHAT_ARCHIVE_EMPTY_MARKER
        if len(calls) == 1 or outcome == 'still_long':
            return oversized
        if outcome == 'empty_after_retry':
            return app.CHAT_ARCHIVE_EMPTY_MARKER
        if outcome == 'retry_error':
            raise RuntimeError('oar_turn_failed' if runtime == 'pi' else 'codex_protocol_error')
        return summary

    def pi_run(command, request, env, timeout):
        states.append(Path(request['state_dir']))
        assert 'newsreader-oar-archive-' in str(states[-1])
        assert request['resume_prompt'] == request['prompt']
        return generate(request['prompt'])

    def codex_run(config, prompt, **kwargs):
        assert kwargs == {'ephemeral': True}
        return generate(prompt), ''

    monkeypatch.setattr(app, 'run_oar_once', pi_run)
    monkeypatch.setattr(app.codex_client, 'run_once', codex_run)
    response = client.post(path + '/chat/archive', json=body)
    assert len(calls) == (1 if outcome in {'valid', 'empty'} else 2)
    for prompt in calls:
        assert f'本次上限为{limit}字符' in prompt
        assert '没有最低字数' in prompt
        assert '通常200—600字' not in prompt
        assert messages[0]['quote_text'] in prompt
        assert messages[1]['content'] in prompt
    if len(calls) == 2:
        assert f'上次生成的正文为{limit + 1}字符' in calls[1]
        assert f'输出正文必须不超过{limit}字符' in calls[1]
    assert all(not state.exists() for state in states)
    assert client.get(path + '/agent/session').json == before_session
    notes = client.get(path + '/detail').json['notes']
    if outcome in {'valid', 'compressed'}:
        assert response.status_code == 200
        assert response.json['provider'] == runtime
        assert response.json['archive_summary'] == summary
        assert len(notes) == len(before_notes) + 1
        assert notes[:-1] == before_notes
        assert notes[-1]['archive_summary'] == summary
    else:
        assert response.status_code == 502
        expected_error = {
            'still_long': 'invalid_archive_summary',
            'empty': 'empty_archive_summary',
            'empty_after_retry': 'empty_archive_summary',
            'retry_error': 'oar_turn_failed' if runtime == 'pi' else 'codex_protocol_error',
        }[outcome]
        assert response.json['error'] == expected_error
        assert notes == before_notes


@pytest.mark.parametrize('invalid,error', [
    ('message_count', 'too_many_messages'),
    ('message_length', 'message_too_long'),
    ('quote_length', 'quote_too_long'),
    ('quote_type', 'invalid_message_content'),
])
def test_invalid_archive_sources_fail_before_model_call(archive_api, monkeypatch, invalid, error):
    app, client, path, _ = archive_api
    messages = [{'role': 'user', 'content': '这意味着什么？'},
                {'role': 'assistant', 'content': '有待核实传导效果。'}]
    if invalid == 'message_count':
        messages += [messages[-1]] * 199
    elif invalid == 'message_length':
        messages[-1]['content'] = '研' * 20001
    elif invalid == 'quote_length':
        messages[0]['quote_text'] = '引' * 4001
    else:
        messages[0]['quote_text'] = []

    def unexpected_model(**kwargs):
        pytest.fail('Invalid archive sources must be rejected before a model is called')

    monkeypatch.setattr(app, 'run_pi_chat_archive', unexpected_model)
    response = client.post(path + '/chat/archive', json={'messages': messages})
    assert response.status_code == 400
    assert response.json['error'] == error
    assert client.get(path + '/detail').json['notes'] == []


# The final exchange is deliberately narrower than the earlier findings.
MULTI_TOPIC_CONVERSATION = [
    {'role': 'user', 'content': '这项政策是否必然降低企业融资成本？',
     'quote_text': '政策利率下调，但企业贷款利率仍受信用风险影响。'},
    {'role': 'assistant', 'content': '政策降息不保证企业融资成本下降，还取决于银行传导和信用风险溢价。'},
    {'role': 'user', 'content': '企业之间的影响有什么差异？'},
    {'role': 'assistant', 'content': '大型企业与中小企业融资渠道不同，平均利率不能代表所有企业；尚缺中小企业实际贷款利率数据。'},
    {'role': 'user', 'content': '再看外部环境，汇率有什么影响？'},
    {'role': 'assistant', 'content': '若本币贬值，进口成本和外币债务负担可能上升，可能抵消部分降息收益；幅度尚未确定。'},
    {'role': 'user', 'content': '最后只解释一下，25 个基点是多少个百分点？'},
    {'role': 'assistant', 'content': '25 个基点等于 0.25 个百分点。'},
]


def store_conversation(app, client, path, messages):
    response = client.post(path + '/agent/session', json={})
    assert response.status_code == 200
    session_id = response.json['session']['id']
    with app.agent_db_conn() as conn:
        stamp = app.now_ts()
        conn.executemany("""INSERT INTO agent_messages
            (session_id, role, content, quote_text, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'complete', ?, ?)""", [
                (session_id, message['role'], message['content'], message.get('quote_text', ''), stamp, stamp)
                for message in messages
            ])
    return session_id


@pytest.mark.parametrize('runtime', ['pi', 'codex'])
@pytest.mark.parametrize('client_copy', ['last_reply', 'stale', 'omitted'])
def test_persisted_session_is_archive_source(archive_api, monkeypatch, runtime, client_copy):
    app, client, path, settings = archive_api
    settings['agent']['runtime'] = runtime
    session_id = store_conversation(app, client, path, MULTI_TOPIC_CONVERSATION)
    before = client.get(path + '/agent/session').json
    captured = {}

    def pi_run(command, request, env, timeout):
        captured['prompt'] = request['prompt']
        return '合成归档测试结果。'

    def codex_run(config, prompt, **kwargs):
        captured['prompt'] = prompt
        assert kwargs['ephemeral'] is True
        return '合成归档测试结果。', ''

    monkeypatch.setattr(app, 'run_oar_once', pi_run)
    monkeypatch.setattr(app.codex_client, 'run_once', codex_run)
    # A later default change must not change the saved conversation's runtime.
    settings['agent']['runtime'] = 'codex' if runtime == 'pi' else 'pi'
    body = {'agent_session_id': session_id}
    if client_copy == 'last_reply':
        body['messages'] = MULTI_TOPIC_CONVERSATION[-1:]
    elif client_copy == 'stale':
        body['messages'] = MULTI_TOPIC_CONVERSATION[:2]
    response = client.post(path + '/chat/archive', json=body)
    assert response.status_code == 200
    assert response.json['provider'] == runtime
    position = 0
    for message in MULTI_TOPIC_CONVERSATION:
        position = captured['prompt'].index(message['content'], position) + len(message['content'])
    assert MULTI_TOPIC_CONVERSATION[0]['quote_text'] in captured['prompt']
    assert client.get(path + '/agent/session').json == before
    note = client.get(path + '/detail').json['notes'][-1]
    assert note['source_session_id'] == session_id
    assert note['archive_summary'] == '合成归档测试结果。'


@pytest.mark.parametrize('problem,error,status', [
    ('over_limit', 'too_many_messages', 400),
    ('long_message', 'message_too_long', 400),
    ('long_quote', 'quote_too_long', 400),
    ('no_answer', 'empty_archive_source', 400),
    ('missing_session', 'session_invalid', 409),
    ('busy', 'job_in_progress', 409),
])
def test_incomplete_archive_never_falls_back_to_client_messages(archive_api, monkeypatch, problem, error, status):
    app, client, path, _ = archive_api
    messages = [dict(message) for message in MULTI_TOPIC_CONVERSATION]
    if problem == 'over_limit':
        messages = messages[:2] * 101
    elif problem == 'long_message':
        messages[1]['content'] = '研' * 20001
    elif problem == 'long_quote':
        messages[0]['quote_text'] = '引' * 4001
    elif problem == 'no_answer':
        messages = [messages[0], {'role': 'assistant', 'content': ''}]
    session_id = store_conversation(app, client, path, messages)
    if problem == 'missing_session':
        session_id = 'missing-session'
    elif problem == 'busy':
        assert client.post(path + '/agent/jobs', json={'question': '还在讨论的问题'}).status_code == 202

    def unexpected_model(**kwargs):
        pytest.fail('Incomplete or invalid stored conversations must not reach the model')

    monkeypatch.setattr(app, 'run_pi_chat_archive', unexpected_model)
    assert client.post(path + '/notes', json={'note': '已保存的个人想法'}).status_code == 200
    before = client.get(path + '/detail').json['notes']
    response = client.post(path + '/chat/archive', json={
        'agent_session_id': session_id, 'messages': MULTI_TOPIC_CONVERSATION[-2:],
    })
    assert response.status_code == status
    assert response.json['error'] == error
    assert client.get(path + '/detail').json['notes'] == before
