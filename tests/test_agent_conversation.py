"""News chat supplies material once and leaves native conversations in control."""

from nr import news_chat
import json
import threading
import uuid

import pytest

from api_fixtures import _setup_agent_api_fixture, _install_fake_oar
from test_oar_pi_news import pi_runtime, managed_tools
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
        assert app.runtime.agent.process_pending_agent_once()
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
        assert calls[0]['resume'] == ''
        assert all(call['resume'] == THREAD for call in calls[1:])
        assert all(call['transport'] == 'oar' and call['web_search'] == 'live' for call in calls)
    else:
        assert all(call['task'] == 'news_chat' for call in calls)
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
    prompt = news_chat.build_chat_prompt(title='标题', source='来源', published_at='2026-10-27',
                                  content='测试新闻资料', question='我的问题', context_level=level)
    assert prompt == (
        '新闻标题：标题\n新闻来源：来源\n发布时间：2026-10-27\n原文链接：\n'
        f'{label}\n测试新闻资料\n\n本轮用户问题：我的问题'
    )


def test_two_article_workers_and_web_assistant_can_archive_third_article(tmp_path, monkeypatch, pi_runtime):
    import oar_client
    from settings import resolve_app_settings_path
    from test_oar_assistant import finished
    source, _, _, _, _, responses, _ = pi_runtime
    monkeypatch.setenv("NEWS_READER_PI_AGENT_DIR", str(source))
    monkeypatch.setenv("PI_OFFLINE", "1")
    app, client, third, *_ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    resolve_app_settings_path().write_text(json.dumps({"llm":{"pi_chat":{"provider":"nr-fixture","model":"fixture"}}}))
    with app.runtime.db.connect() as conn:
        for item in ("first", "second"):
            conn.execute("""INSERT INTO items(id,source_file,item_order,published_at,date,title,summary,url,created_at,updated_at)
                VALUES(?,'fixture',1,'2026-11-12','2026-11-12','双轨测试','第一段材料',?,'now','now')""",
                (item, "https://example.test/" + item))
        conn.commit()
    path = "/api/news/" + third["id"]
    archived_session = client.post(path + "/agent/jobs", json={"question":"提炼这篇材料的可比口径"}).json["session"]["id"]
    responses.append("观察样本和分母")
    assert app.runtime.agent.process_pending_agent_once()
    release = threading.Event()
    def hold(entered, answer):
        def reply(body):
            entered.set()
            assert release.wait(30)
            return answer
        return reply
    articles = [threading.Event(), threading.Event()]
    searching = threading.Event()
    original = oar_client.bridge_request
    def local_search(**args):
        payload = original(**args)
        if args.get("task") == "personal_assistant":
            payload["web_tools"] = managed_tools("--evidence", str(tmp_path / "search"))
        return payload
    monkeypatch.setattr(oar_client, "bridge_request", local_search)
    agent = app.runtime.agent
    agent.start_agent_worker()
    try:
        for item, entered in zip(("first","second"), articles):
            responses.append(hold(entered, "文章完成"))
            assert client.post(f"/api/news/{item}/agent/jobs", json={"question":"这篇文章有什么变化？"}).status_code == 202
            assert entered.wait(12)
        responses.extend([("mcp__nr_web__web_search", {"query":"合成报道"}), hold(searching, "个人讨论完成")])
        sent = client.post("/api/personal-assistant/threads/main/messages", json={"text":"查询报道的新进展", "request_id":str(uuid.uuid4())})
        assert sent.status_code == 200
        assert searching.wait(16)
        assert (tmp_path / "search").read_text().strip() == "web_search"
        responses.append("核对样本和分母")
        result = client.post(path + "/chat/archive", json={"agent_session_id":archived_session})
        assert result.status_code == 200, result.json
        assert result.json["notes"][0]["archive_summary"] == "核对样本和分母"
        assert client.get("/api/personal-assistant/threads/main").json["job"]["status"] == "running"
        for item in ("first","second"):
            snapshot = client.get(f"/api/news/{item}/agent/session").json
            assert snapshot["jobs"][0]["status"] in ("running","streaming")
    finally:
        release.set()
        agent.stop_event.set()
        for worker in agent.threads:
            worker.join(10)
    assert finished(app.app.extensions["personal_assistant"], "main")["job"]["status"] == "done"
    for item in ("first","second"):
        assert client.get(f"/api/news/{item}/agent/session").json["jobs"][0]["status"] == "succeeded"
