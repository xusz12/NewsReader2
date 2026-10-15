from __future__ import annotations

import importlib
import json
import sys
import types

import pytest


def _install_fake_openai(monkeypatch, create_impl):
    class FakeOpenAI:
        def __init__(self, api_key=None, base_url=None):
            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(create=create_impl)
            )
            self.responses = types.SimpleNamespace(create=create_impl)

    fake_module = types.SimpleNamespace(OpenAI=FakeOpenAI)
    monkeypatch.setitem(sys.modules, "openai", fake_module)


def test_generate_article_ai_success(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")

    arguments = json.dumps(
        {
            "key_points_zh": ["要点一", "要点二", "要点三"],
            "conclusion_zh": "一句话结论",
            "body_zh": "中" * 260,
        },
        ensure_ascii=False,
    )

    called = {}

    def create_impl(**kwargs):
        called.update(kwargs)
        tool_call = types.SimpleNamespace(
            function=types.SimpleNamespace(arguments=arguments)
        )
        msg = types.SimpleNamespace(tool_calls=[tool_call])
        choice = types.SimpleNamespace(message=msg)
        return types.SimpleNamespace(model="deepseek-v4-flash", choices=[choice])

    _install_fake_openai(monkeypatch, create_impl)
    import llm_client

    importlib.reload(llm_client)
    out = llm_client.generate_article_ai(
        title="Test Title",
        source="Reuters",
        content="English body " * 50,
    )
    assert out["model"] == "deepseek-v4-flash"
    assert out["conclusion_zh"] == "一句话结论"
    assert out["key_points_zh"] == ["要点一", "要点二", "要点三"]
    assert len(out["body_zh"]) >= 200
    assert called["model"] == "deepseek-v4-flash"
    assert called["extra_body"] == {"thinking": {"type": "disabled"}}


def test_generate_article_ai_invalid_tool_payload(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")

    def create_impl(**kwargs):
        tool_call = types.SimpleNamespace(
            function=types.SimpleNamespace(arguments='{"oops": 1}')
        )
        msg = types.SimpleNamespace(tool_calls=[tool_call])
        choice = types.SimpleNamespace(message=msg)
        return types.SimpleNamespace(model="deepseek-v4-flash", choices=[choice])

    _install_fake_openai(monkeypatch, create_impl)
    import llm_client

    importlib.reload(llm_client)
    with pytest.raises(llm_client.LLMClientError) as exc:
        llm_client.generate_article_ai(
            title="Test Title",
            source="Reuters",
            content="English body " * 50,
        )
    assert "INVALID_KEY_POINTS" in str(exc.value)


def test_generate_article_ai_reads_key_from_keychain(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    arguments = json.dumps(
        {
            "key_points_zh": ["要点一", "要点二", "要点三"],
            "conclusion_zh": "一句话结论",
            "body_zh": "中" * 260,
        },
        ensure_ascii=False,
    )

    def create_impl(**kwargs):
        tool_call = types.SimpleNamespace(
            function=types.SimpleNamespace(arguments=arguments)
        )
        msg = types.SimpleNamespace(tool_calls=[tool_call])
        choice = types.SimpleNamespace(message=msg)
        return types.SimpleNamespace(model="deepseek-v4-flash", choices=[choice])

    _install_fake_openai(monkeypatch, create_impl)
    import llm_client

    importlib.reload(llm_client)
    monkeypatch.setattr(llm_client, "read_secret", lambda name: "keychain-secret")
    out = llm_client.generate_article_ai(
        title="Test Title",
        source="Reuters",
        content="English body " * 50,
    )
    assert out["model"] == "deepseek-v4-flash"


def test_generate_article_ai_uses_configured_model_and_fallback(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
    monkeypatch.setenv("NEWS_READER_LLM_MODEL", "deepseek-v4-pro")

    arguments = json.dumps(
        {
            "key_points_zh": ["要点一", "要点二", "要点三"],
            "conclusion_zh": "一句话结论",
            "body_zh": "中" * 260,
        },
        ensure_ascii=False,
    )

    called = {}

    def create_impl(**kwargs):
        called.update(kwargs)
        tool_call = types.SimpleNamespace(
            function=types.SimpleNamespace(arguments=arguments)
        )
        msg = types.SimpleNamespace(tool_calls=[tool_call])
        choice = types.SimpleNamespace(message=msg)
        return types.SimpleNamespace(model=None, choices=[choice])

    _install_fake_openai(monkeypatch, create_impl)
    import llm_client

    importlib.reload(llm_client)
    out = llm_client.generate_article_ai(
        title="Test Title",
        source="Reuters",
        content="English body " * 50,
    )
    assert called["model"] == "deepseek-v4-pro"
    assert called["extra_body"] == {"thinking": {"type": "disabled"}}
    assert out["model"] == "deepseek-v4-pro"


def test_generate_article_ai_accepts_short_chinese_body(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
    arguments = json.dumps(
        {
            "key_points_zh": ["要点一", "要点二", "要点三"],
            "conclusion_zh": "一句话结论",
            "body_zh": "该快讯较短，但中文翻译完整。",
        },
        ensure_ascii=False,
    )

    def create_impl(**kwargs):
        tool_call = types.SimpleNamespace(
            function=types.SimpleNamespace(arguments=arguments)
        )
        msg = types.SimpleNamespace(tool_calls=[tool_call])
        choice = types.SimpleNamespace(message=msg)
        return types.SimpleNamespace(model="deepseek-v4-flash", choices=[choice])

    _install_fake_openai(monkeypatch, create_impl)
    import llm_client

    importlib.reload(llm_client)
    out = llm_client.generate_article_ai(
        title="Short Brief",
        source="Reuters",
        content="Short english brief.",
    )
    assert out["body_zh"] == "该快讯较短，但中文翻译完整。"


def test_generate_body_translation_only_disables_deepseek_thinking(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
    arguments = json.dumps({"body_zh": "这是完整的推文正文翻译。"}, ensure_ascii=False)
    called = {}

    def create_impl(**kwargs):
        called.update(kwargs)
        tool_call = types.SimpleNamespace(
            function=types.SimpleNamespace(arguments=arguments)
        )
        msg = types.SimpleNamespace(tool_calls=[tool_call])
        choice = types.SimpleNamespace(message=msg)
        return types.SimpleNamespace(model="deepseek-v4-flash", choices=[choice])

    _install_fake_openai(monkeypatch, create_impl)
    import llm_client

    importlib.reload(llm_client)
    out = llm_client.generate_body_translation_only(
        title="Tweet",
        source="Twitter/X",
        content="English tweet body.",
    )
    assert out["body_zh"] == "这是完整的推文正文翻译。"
    assert out["key_points_zh"] == []
    assert out["conclusion_zh"] == ""
    assert called["model"] == "deepseek-v4-flash"
    assert called["extra_body"] == {"thinking": {"type": "disabled"}}


def test_generate_article_ai_rejects_non_chinese_body(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
    arguments = json.dumps(
        {
            "key_points_zh": ["要点一", "要点二", "要点三"],
            "conclusion_zh": "一句话结论",
            "body_zh": "This is still English.",
        },
        ensure_ascii=False,
    )

    def create_impl(**kwargs):
        tool_call = types.SimpleNamespace(
            function=types.SimpleNamespace(arguments=arguments)
        )
        msg = types.SimpleNamespace(tool_calls=[tool_call])
        choice = types.SimpleNamespace(message=msg)
        return types.SimpleNamespace(model="deepseek-v4-flash", choices=[choice])

    _install_fake_openai(monkeypatch, create_impl)
    import llm_client

    importlib.reload(llm_client)
    with pytest.raises(llm_client.LLMClientError) as exc:
        llm_client.generate_article_ai(
            title="Short Brief",
            source="Reuters",
            content="Short english brief.",
        )
    assert str(exc.value) == "INVALID_BODY_ZH"


def test_build_tracked_daily_summary_messages_require_story_style(monkeypatch):
    import llm_client

    importlib.reload(llm_client)
    messages = llm_client._build_tracked_daily_summary_messages(
        topic_title="俄乌战争",
        summary_date="2026-06-20",
        materials="新闻 1\n发布时间：2026-06-20 09:00:00\n标题：A",
        news_count=3,
        max_summary_chars=150,
    )
    system = messages[0]["content"]
    assert "连续叙述式总结" in system
    assert "不要按 1/2/3/4" in system
    assert "不要把原始新闻一条条改写后顺序拼接" in system
    assert "本日共有 3 条新闻" in system
    assert "不得超过 150 个中文字符" in system


def test_generate_tracked_topic_rule_draft_success(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
    arguments = json.dumps(
        {
            "title": "美伊战争",
            "strong_phrases": ["美伊战争", "美国伊朗战争"],
            "core_terms": ["美国", "伊朗", "特朗普"],
            "context_terms": ["空袭", "导弹", "报复"],
            "exclude_terms": ["电影", "游戏", "旅游"],
            "threshold": 6,
        },
        ensure_ascii=False,
    )

    called = {}

    def create_impl(**kwargs):
        called.update(kwargs)
        tool_call = types.SimpleNamespace(
            function=types.SimpleNamespace(arguments=arguments)
        )
        msg = types.SimpleNamespace(tool_calls=[tool_call])
        choice = types.SimpleNamespace(message=msg)
        return types.SimpleNamespace(model="deepseek-v4-flash", choices=[choice])

    _install_fake_openai(monkeypatch, create_impl)
    import llm_client

    importlib.reload(llm_client)
    out = llm_client.generate_tracked_topic_rule_draft(title="美伊战争")
    assert out["model"] == "deepseek-v4-flash"
    assert out["title"] == "美伊战争"
    assert out["strong_phrases"] == ["美伊战争", "美国伊朗战争"]
    assert out["threshold"] == 6
    assert called["tool_choice"]["function"]["name"] == "save_tracked_rule_draft"
    assert called["extra_body"] == {"thinking": {"type": "disabled"}}


def test_generate_tracked_topic_rule_draft_invalid_payload(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")

    def create_impl(**kwargs):
        tool_call = types.SimpleNamespace(
            function=types.SimpleNamespace(arguments='{"title":"AI","threshold":"x"}')
        )
        msg = types.SimpleNamespace(tool_calls=[tool_call])
        choice = types.SimpleNamespace(message=msg)
        return types.SimpleNamespace(model="deepseek-v4-flash", choices=[choice])

    _install_fake_openai(monkeypatch, create_impl)
    import llm_client

    importlib.reload(llm_client)
    with pytest.raises(llm_client.LLMClientError) as exc:
        llm_client.generate_tracked_topic_rule_draft(title="AI 发展")
    assert "INVALID_RULE_DRAFT_STRONG_PHRASES" in str(exc.value)
