"""News chat supplies material once and leaves native conversations in control."""
import json

import pytest

from test_api import _setup_agent_api_fixture, _install_fake_oar
from test_codex_client import THREAD, fake_codex, select_codex


@pytest.mark.parametrize('runtime', ['pi', 'codex'])
def test_native_followups_keep_session_and_only_add_new_user_input(tmp_path, monkeypatch, runtime):
    app, client, item, *_ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    if runtime == 'codex':
        log = fake_codex(tmp_path, monkeypatch)
        select_codex(app, monkeypatch)
    else:
        log = _install_fake_oar(tmp_path, monkeypatch)
    path = f"/api/news/{item['id']}/agent"
    turns = [
        {'question': '解释这篇新闻中的主要变化。'},
        {'question': '继续讨论上一轮的第二点。'},
        {'question': '这段话与刚才的讨论有什么联系？', 'quote_text': '新引用第一行\n新引用第二行'},
    ]
    session_id = None
    for turn in turns:
        response = client.post(path + '/jobs', json={**turn, 'session_id': session_id})
        assert response.status_code == 202
        session_id = session_id or response.json['session']['id']
        assert response.json['session']['id'] == session_id
        assert app.process_pending_agent_once()
        assert client.get(path + '/session').json['jobs'][-1]['status'] == 'succeeded'

    calls = [json.loads(line) for line in log.read_text().splitlines()]
    prompts = [call['prompt'] if runtime == 'codex' else call['effective_prompt'] for call in calls]
    assert len(prompts) == 3
    # The first turn contains only labelled article material and the user's question.
    assert prompts[0] == (
        f"新闻标题：{item['title']}\n新闻来源：Reuters\n发布时间：2026-08-27 09:00:00\n"
        f"原文链接：{item['url']}\n新闻正文：\n"
        "The complete original article used as the authoritative context.\n\n"
        "本轮用户问题：解释这篇新闻中的主要变化。"
    )
    assert prompts[1] == turns[1]['question']
    assert prompts[2] == (
        '用户划线引用：\n> 新引用第一行\n> 新引用第二行\n\n'
        '本轮用户问题：这段话与刚才的讨论有什么联系？'
    )
    if runtime == 'codex':
        assert 'resume' not in calls[0]['args']
        assert all(call['args'][-3:] == ['resume', THREAD, '-'] for call in calls[1:])
        assert all('web_search="live"' in call['args'] for call in calls)
    else:
        assert len({call['state_dir'] for call in calls}) == 1
        assert all('research_budget_ms' not in call and 'max_tool_calls' not in call for call in calls)
        # The bridge still receives the full fallback in case a native session must be rebuilt.
        assert turns[0]['question'] in calls[-1]['prompt']
        assert turns[1]['question'] in calls[-1]['prompt']
        assert '完成' in calls[-1]['prompt']
    saved = client.get(path + '/session').json
    assert len(saved['messages']) == 6
    assert [row['content'] for row in saved['messages'] if row['role'] == 'user'] == [turn['question'] for turn in turns]
    assert saved['messages'][-2]['quote_text'] == turns[-1]['quote_text']


@pytest.mark.parametrize('level,label', [
    ('full_detail', '新闻正文：'),
    ('summary_context', '新闻资料（摘要与元数据，尚未提供全文）：'),
])
def test_legacy_chat_prompt_uses_material_without_answering_rules(tmp_path, monkeypatch, level, label):
    app, *_ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    prompt = app.build_chat_prompt(title='标题', source='来源', published_at='2026-10-27',
                                  content='测试新闻资料', question='我的问题', context_level=level)
    assert prompt == (
        '新闻标题：标题\n新闻来源：来源\n发布时间：2026-10-27\n原文链接：\n'
        f'{label}\n测试新闻资料\n\n本轮用户问题：我的问题'
    )
