"""Archives retain full discussion context while saving a concise synthesis."""
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
def test_full_research_and_quote_reach_archive_model(archive_api, monkeypatch, runtime):
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
    response = client.post(path + '/chat/archive', json={'messages': messages})

    assert response.status_code == 200
    assert response.json['provider'] == runtime
    assert response.json['archive_summary'] == summary
    prompt = captured['prompt']
    assert messages[0]['quote_text'].strip() in prompt
    # Both the initial intent and later clarification survive, along with long answers.
    position = 0
    for message in messages:
        position = prompt.index(message['content'], position) + len(message['content'])
    assert prompt.endswith(messages[-1]['content'])
    if runtime == 'codex':
        assert captured['ephemeral'] is True
        assert 'thread_id' not in captured
    assert client.get(path + '/detail').json['notes'][-1]['archive_summary'] == summary


def test_archive_summary_boundary_preserves_saved_findings_on_failure(archive_api, monkeypatch):
    app, client, path, _ = archive_api
    messages = [{'role': 'user', 'content': '研究重点是什么？'},
                {'role': 'assistant', 'content': '重点是融资成本的传导。'}]
    summary = '研' * 500
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
