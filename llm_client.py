from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from secret_store import SecretStoreError, read_secret


class LLMClientError(RuntimeError):
    pass


def _validate_daily_summary_payload(parsed: object) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        raise LLMClientError("INVALID_TOOL_ARGUMENTS_JSON: payload_not_object")

    summary_text = parsed.get("summary_text")
    if not isinstance(summary_text, str):
        raise LLMClientError("INVALID_SUMMARY_TEXT")
    normalized = summary_text.strip()
    if not normalized or not _contains_cjk(normalized):
        raise LLMClientError("INVALID_SUMMARY_TEXT")
    return {"summary_text": normalized}


def _validate_market_tag_summary_payload(parsed: object) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        raise LLMClientError("INVALID_TOOL_ARGUMENTS_JSON: payload_not_object")

    summary_text = parsed.get("summary_text")
    if not isinstance(summary_text, str):
        raise LLMClientError("INVALID_MARKET_TAG_SUMMARY_TEXT")
    normalized = summary_text.strip()
    if not normalized or not _contains_cjk(normalized):
        raise LLMClientError("INVALID_MARKET_TAG_SUMMARY_TEXT")
    return {"summary_text": normalized}


def _validate_twitter_comments_summary_payload(parsed: object) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        raise LLMClientError("INVALID_TOOL_ARGUMENTS_JSON: payload_not_object")

    summary_text = parsed.get("summary_text")
    if not isinstance(summary_text, str):
        raise LLMClientError("INVALID_TWITTER_COMMENTS_SUMMARY_TEXT")
    normalized = summary_text.strip()
    if not normalized or not _contains_cjk(normalized):
        raise LLMClientError("INVALID_TWITTER_COMMENTS_SUMMARY_TEXT")
    return {"summary_text": normalized}


def _validate_body_translation_only_payload(parsed: object) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        raise LLMClientError("INVALID_TOOL_ARGUMENTS_JSON: payload_not_object")
    body_zh = parsed.get("body_zh")
    if not isinstance(body_zh, str):
        raise LLMClientError("INVALID_BODY_ZH")
    normalized = body_zh.strip()
    if not normalized or not _contains_cjk(normalized):
        raise LLMClientError("INVALID_BODY_ZH")
    return {"body_zh": normalized}


def _validate_structured_translation_payload(parsed: object) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        raise LLMClientError("INVALID_TOOL_ARGUMENTS_JSON: payload_not_object")

    key_points = parsed.get("key_points_zh")
    conclusion = parsed.get("conclusion_zh")
    body_zh = parsed.get("body_zh")

    if not isinstance(key_points, list) or not (3 <= len(key_points) <= 5):
        raise LLMClientError("INVALID_KEY_POINTS")
    if not all(isinstance(x, str) and x.strip() for x in key_points):
        raise LLMClientError("INVALID_KEY_POINT_ITEM")
    if not isinstance(conclusion, str) or not conclusion.strip():
        raise LLMClientError("INVALID_CONCLUSION")
    if not isinstance(body_zh, str):
        raise LLMClientError("INVALID_BODY_ZH")
    body_text = body_zh.strip()
    if not body_text or not _contains_cjk(body_text):
        raise LLMClientError("INVALID_BODY_ZH")

    return {
        "key_points_zh": [x.strip() for x in key_points],
        "conclusion_zh": conclusion.strip(),
        "body_zh": body_text,
    }


def _validate_tracked_rule_draft_payload(parsed: object) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        raise LLMClientError("INVALID_TOOL_ARGUMENTS_JSON: payload_not_object")

    title = parsed.get("title")
    if not isinstance(title, str) or not title.strip():
        raise LLMClientError("INVALID_RULE_DRAFT_TITLE")

    normalized: dict[str, Any] = {"title": title.strip()}
    for key in ("strong_phrases", "core_terms", "context_terms", "exclude_terms"):
        value = parsed.get(key)
        if not isinstance(value, list):
            raise LLMClientError(f"INVALID_RULE_DRAFT_{key.upper()}")
        cleaned = []
        for item in value:
            if not isinstance(item, str):
                raise LLMClientError(f"INVALID_RULE_DRAFT_{key.upper()}_ITEM")
            text = item.strip()
            if text:
                cleaned.append(text)
        normalized[key] = cleaned

    threshold = parsed.get("threshold")
    if isinstance(threshold, bool):
        raise LLMClientError("INVALID_RULE_DRAFT_THRESHOLD")
    if isinstance(threshold, (int, float)):
        normalized["threshold"] = int(threshold)
    elif isinstance(threshold, str) and threshold.strip():
        try:
            normalized["threshold"] = int(float(threshold.strip()))
        except Exception as exc:
            raise LLMClientError("INVALID_RULE_DRAFT_THRESHOLD") from exc
    else:
        raise LLMClientError("INVALID_RULE_DRAFT_THRESHOLD")
    return normalized


def _contains_cjk(text: str) -> bool:
    for char in text:
        code = ord(char)
        if 0x3400 <= code <= 0x4DBF or 0x4E00 <= code <= 0x9FFF:
            return True
    return False


_DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"


def _configured_model() -> str:
    value = (os.getenv("NEWS_READER_LLM_MODEL") or "").strip()
    return value or _DEFAULT_DEEPSEEK_MODEL


def resolve_translation_default_model() -> str:
    return _configured_model()


def _resolve_api_key(env_name: str) -> str:
    value = (os.getenv(env_name) or "").strip()
    if value:
        return value
    try:
        secret = read_secret(env_name)
    except SecretStoreError:
        return ""
    return (secret or "").strip()


def _build_messages(*, title: str, source: str, content: str) -> list[dict[str, str]]:
    system = (
        "你是专业新闻编辑与翻译。"
        "你必须调用给定函数并严格提供结构化参数。"
        "要求：\n"
        "1) key_points_zh: 3-5 条中文要点，每条一句话。\n"
        "2) conclusion_zh: 一句话中文结论。\n"
        "3) body_zh: 对全文做完整中文翻译，保持段落结构，不遗漏关键事实。"
    )
    user = (
        f"来源: {source or '未知'}\n"
        f"标题: {title or '无标题'}\n\n"
        "请基于以下英文正文生成结构化中文结果：\n\n"
        f"{content}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _build_tracked_daily_summary_messages(
    *,
    topic_title: str,
    summary_date: str,
    materials: str,
    news_count: int,
    max_summary_chars: int,
) -> list[dict[str, str]]:
    system = (
        "你是专业新闻编辑。"
        "你必须调用给定函数并严格提供结构化参数。"
        "任务：基于同一跟踪主题、同一天的本地新闻材料，按新闻发生/发布时间顺序，融合总结当天发生的事实与事态发展。"
        "材料里如果出现“独立想法 / 用户判断”，那属于用户判断，不属于新闻事实。"
        "只允许使用材料内已经出现的事实信息。"
        "不要把用户判断伪装成事实。"
        "不要联网，不要补充材料外信息，不要评价影响，不要预测后续，不要自行赋予意义。"
        "如果材料冲突或不确定，按材料中的来源陈述，不自行裁决。"
        "输出必须是连续叙述式总结，像把当天发生的事吃透后重新讲给读者听。"
        "不要按 1/2/3/4 或首先/其次/再次 逐条列点，不要把原始新闻一条条改写后顺序拼接。"
        "要把重复信息合并，把前后进展串起来，让读者读完像听完一段当天事件经过。"
        f"本日共有 {max(0, int(news_count or 0))} 条新闻，最终摘要不得超过 {max(1, int(max_summary_chars or 1))} 个中文字符或等价可见字符。"
    )
    user = (
        f"跟踪主题：{topic_title or '未命名主题'}\n"
        f"日期：{summary_date}\n\n"
        "请基于以下材料生成当天的中文融合摘要：\n\n"
        f"{materials}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _build_tracked_rule_draft_messages(*, topic_title: str, description: str = "") -> list[dict[str, str]]:
    system = (
        "你是专业新闻编辑，正在为本地新闻阅读器生成跟踪主题规则草稿。"
        "你必须调用给定函数并严格提供结构化参数。"
        "目标是为当前 v1.9.8.x 跟踪规则结构生成可人工审核的第一版草稿，而不是自动保存主题。"
        "字段含义："
        "强匹配短语=几乎单独就能证明主题相关的完整表达、常见别名或固定短语；"
        "核心对象词=主题核心国家、组织、公司、人物、地点、资产或产品；"
        "相关场景词=动作、事件、军事/外交/产业/市场语境，用来和核心对象词组合；"
        "排除词=最常见误伤场景，例如历史回顾、电影、游戏、旅游、文学、普通市场评论。"
        "宁可少给高信息量词，也不要给过宽泛的万能词，如国际形势、热点新闻、市场。"
        "threshold 默认保守给 6，除非主题明显需要不同阈值。"
        "输出必须是 JSON / function 参数，不要输出自然语言说明。"
    )
    user = (
        f"主题名称：{topic_title.strip()}\n"
        f"补充描述：{(description or '').strip() or '无'}\n\n"
        "请生成跟踪主题规则草稿。可以参考“中东局势 / 美伊战争”这类模板风格，但必须按当前主题定制，不要把示例词原样套用到无关主题。"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _build_market_tag_summary_messages(
    *,
    tag_label: str,
    range_days: int,
    news_count: int,
    note_count: int,
    materials: str,
) -> list[dict[str, str]]:
    system = (
        "你是专业市场新闻编辑。"
        "你必须调用给定函数并严格提供结构化参数。"
        "任务：基于单一板块最近一段时间的本地新闻与用户想法，总结“近期趋势”。"
        "必须明确区分两类来源："
        "1) 新闻事实：来自新闻标题、摘要、AI 摘要、AI 正文摘录、正文摘录；"
        "2) 用户判断：来自新闻想法与独立趋势想法。"
        "输出要求："
        "先概括近期事实主线，再单独概括用户判断/分歧/关注点。"
        "不得把用户判断伪装成新闻事实，不得补充材料外信息，不得联网，不得重打标签。"
        "如果材料不足或存在冲突，只能按材料原样表述。"
        "输出为自然中文段落，可用“新闻事实 / 用户想法”这样的短标题，但不要列表化。"
    )
    user = (
        f"板块：{tag_label or '未命名板块'}\n"
        f"范围：最近 {max(1, int(range_days or 1))} 天\n"
        f"新闻条数上限：{max(0, int(news_count or 0))}\n"
        f"独立趋势想法条数：{max(0, int(note_count or 0))}\n\n"
        "请基于以下本地材料生成板块近期趋势总结：\n\n"
        f"{materials}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _build_twitter_comments_summary_messages(
    *,
    title: str,
    source: str,
    comments_text: str,
    comment_count: int,
) -> list[dict[str, str]]:
    system = (
        "你是专业社交媒体编辑。"
        "你必须调用给定函数并严格提供结构化参数。"
        "任务：基于已抓取的 Twitter/X 评论样本，总结评论区的主要观点。"
        "不要假装看过未抓取的评论，不要补充材料外信息，不要联网。"
        "summary_text 只写观点总结本体，用自然中文 1-3 句话概括评论区主流观点、分歧点或情绪。"
        "不要在 summary_text 里重复“基于已抓取的 N 条评论总结”这类前缀。"
    )
    user = (
        f"标题：{title or '无标题'}\n"
        f"来源：{source or 'Twitter/X'}\n"
        f"已抓取评论数：{max(0, int(comment_count or 0))}\n\n"
        "请基于以下评论样本生成总结：\n\n"
        f"{comments_text}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _build_body_translation_only_messages(*, title: str, source: str, content: str) -> list[dict[str, str]]:
    system = (
        "你是专业翻译。"
        "你必须调用给定函数并严格提供结构化参数。"
        "任务：把正文完整翻译成中文。"
        "不要生成要点，不要生成结论，只返回完整中文正文。"
    )
    user = (
        f"来源：{source or '未知'}\n"
        f"标题：{title or '无标题'}\n\n"
        "请翻译以下正文：\n\n"
        f"{content}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def generate_article_ai(*, title: str, source: str, content: str, model: str | None = None) -> dict[str, Any]:
    api_key = _resolve_api_key("DEEPSEEK_API_KEY")
    if not api_key:
        raise LLMClientError("MISSING_DEEPSEEK_API_KEY")

    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover - env-specific
        raise LLMClientError(f"OPENAI_SDK_IMPORT_FAILED: {exc}") from exc

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com/beta")
    model_name = (model or "").strip() or _configured_model()
    tools = [
        {
            "type": "function",
            "function": {
                "name": "save_article_translation",
                "description": "保存文章翻译与摘要结果",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key_points_zh": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 3,
                            "maxItems": 5,
                        },
                        "conclusion_zh": {"type": "string"},
                        "body_zh": {"type": "string"},
                    },
                    "required": ["key_points_zh", "conclusion_zh", "body_zh"],
                    "additionalProperties": False,
                },
            },
        }
    ]

    try:
        resp = client.chat.completions.create(
            model=model_name,
            messages=_build_messages(title=title, source=source, content=content),
            tools=tools,
            tool_choice={"type": "function", "function": {"name": "save_article_translation"}},
            temperature=0.2,
            timeout=180,
            extra_body={"thinking": {"type": "disabled"}},
        )
    except Exception as exc:
        raise LLMClientError(f"DEEPSEEK_CALL_FAILED: {exc}") from exc

    choices = getattr(resp, "choices", None) or []
    if not choices:
        raise LLMClientError("EMPTY_CHOICES")
    msg = choices[0].message
    tool_calls = getattr(msg, "tool_calls", None) or []
    if not tool_calls:
        raise LLMClientError("NO_TOOL_CALL")

    args_text = getattr(tool_calls[0].function, "arguments", "") or ""
    try:
        parsed = json.loads(args_text)
    except Exception as exc:
        raise LLMClientError(f"INVALID_TOOL_ARGUMENTS_JSON: {exc}") from exc

    normalized = _validate_structured_translation_payload(parsed)

    return {
        "model": getattr(resp, "model", None) or model_name,
        **normalized,
        "raw_json": json.dumps(parsed, ensure_ascii=False),
    }


def generate_twitter_comments_summary(
    *,
    title: str,
    source: str,
    comments_text: str,
    comment_count: int,
    model: str | None = None,
) -> dict[str, Any]:
    api_key = _resolve_api_key("DEEPSEEK_API_KEY")
    if not api_key:
        raise LLMClientError("MISSING_DEEPSEEK_API_KEY")

    body = (comments_text or "").strip()
    if not body:
        raise LLMClientError("EMPTY_TWITTER_COMMENTS")

    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover - env-specific
        raise LLMClientError(f"OPENAI_SDK_IMPORT_FAILED: {exc}") from exc

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com/beta")
    model_name = (model or "").strip() or _configured_model()
    tools = [
        {
            "type": "function",
            "function": {
                "name": "save_twitter_comments_summary",
                "description": "保存 Twitter 评论区观点总结",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary_text": {"type": "string"},
                    },
                    "required": ["summary_text"],
                    "additionalProperties": False,
                },
            },
        }
    ]

    try:
        resp = client.chat.completions.create(
            model=model_name,
            messages=_build_twitter_comments_summary_messages(
                title=title,
                source=source,
                comments_text=body,
                comment_count=comment_count,
            ),
            tools=tools,
            tool_choice={"type": "function", "function": {"name": "save_twitter_comments_summary"}},
            temperature=0.2,
            timeout=120,
            extra_body={"thinking": {"type": "disabled"}},
        )
    except Exception as exc:
        raise LLMClientError(f"DEEPSEEK_CALL_FAILED: {exc}") from exc

    choices = getattr(resp, "choices", None) or []
    if not choices:
        raise LLMClientError("EMPTY_CHOICES")
    msg = choices[0].message
    tool_calls = getattr(msg, "tool_calls", None) or []
    if not tool_calls:
        raise LLMClientError("NO_TOOL_CALL")

    args_text = getattr(tool_calls[0].function, "arguments", "") or ""
    try:
        parsed = json.loads(args_text)
    except Exception as exc:
        raise LLMClientError(f"INVALID_TOOL_ARGUMENTS_JSON: {exc}") from exc

    normalized = _validate_twitter_comments_summary_payload(parsed)
    return {
        "model": getattr(resp, "model", None) or model_name,
        **normalized,
        "raw_json": json.dumps(parsed, ensure_ascii=False),
    }


def generate_body_translation_only(*, title: str, source: str, content: str, model: str | None = None) -> dict[str, Any]:
    api_key = _resolve_api_key("DEEPSEEK_API_KEY")
    if not api_key:
        raise LLMClientError("MISSING_DEEPSEEK_API_KEY")

    body = (content or "").strip()
    if not body:
        raise LLMClientError("EMPTY_TRANSLATION_CONTENT")

    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover - env-specific
        raise LLMClientError(f"OPENAI_SDK_IMPORT_FAILED: {exc}") from exc

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com/beta")
    model_name = (model or "").strip() or _configured_model()
    tools = [
        {
            "type": "function",
            "function": {
                "name": "save_body_translation_only",
                "description": "保存仅正文中文翻译",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "body_zh": {"type": "string"},
                    },
                    "required": ["body_zh"],
                    "additionalProperties": False,
                },
            },
        }
    ]

    try:
        resp = client.chat.completions.create(
            model=model_name,
            messages=_build_body_translation_only_messages(title=title, source=source, content=body),
            tools=tools,
            tool_choice={"type": "function", "function": {"name": "save_body_translation_only"}},
            temperature=0.2,
            timeout=120,
            extra_body={"thinking": {"type": "disabled"}},
        )
    except Exception as exc:
        raise LLMClientError(f"DEEPSEEK_CALL_FAILED: {exc}") from exc

    choices = getattr(resp, "choices", None) or []
    if not choices:
        raise LLMClientError("EMPTY_CHOICES")
    msg = choices[0].message
    tool_calls = getattr(msg, "tool_calls", None) or []
    if not tool_calls:
        raise LLMClientError("NO_TOOL_CALL")

    args_text = getattr(tool_calls[0].function, "arguments", "") or ""
    try:
        parsed = json.loads(args_text)
    except Exception as exc:
        raise LLMClientError(f"INVALID_TOOL_ARGUMENTS_JSON: {exc}") from exc

    normalized = _validate_body_translation_only_payload(parsed)
    return {
        "model": getattr(resp, "model", None) or model_name,
        "key_points_zh": [],
        "conclusion_zh": "",
        **normalized,
        "raw_json": json.dumps(parsed, ensure_ascii=False),
    }


def generate_tracked_topic_rule_draft(
    *,
    title: str,
    description: str = "",
    model: str | None = None,
) -> dict[str, Any]:
    api_key = _resolve_api_key("DEEPSEEK_API_KEY")
    if not api_key:
        raise LLMClientError("MISSING_DEEPSEEK_API_KEY")

    normalized_title = (title or "").strip()
    if not normalized_title:
        raise LLMClientError("EMPTY_RULE_DRAFT_TITLE")

    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover - env-specific
        raise LLMClientError(f"OPENAI_SDK_IMPORT_FAILED: {exc}") from exc

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com/beta")
    model_name = (model or "").strip() or _configured_model()
    tools = [
        {
            "type": "function",
            "function": {
                "name": "save_tracked_rule_draft",
                "description": "保存跟踪主题规则草稿",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "strong_phrases": {"type": "array", "items": {"type": "string"}},
                        "core_terms": {"type": "array", "items": {"type": "string"}},
                        "context_terms": {"type": "array", "items": {"type": "string"}},
                        "exclude_terms": {"type": "array", "items": {"type": "string"}},
                        "threshold": {"type": "integer"},
                    },
                    "required": [
                        "title",
                        "strong_phrases",
                        "core_terms",
                        "context_terms",
                        "exclude_terms",
                        "threshold",
                    ],
                    "additionalProperties": False,
                },
            },
        }
    ]

    try:
        resp = client.chat.completions.create(
            model=model_name,
            messages=_build_tracked_rule_draft_messages(
                topic_title=normalized_title,
                description=description,
            ),
            tools=tools,
            tool_choice={"type": "function", "function": {"name": "save_tracked_rule_draft"}},
            temperature=0.2,
            timeout=180,
            extra_body={"thinking": {"type": "disabled"}},
        )
    except Exception as exc:
        raise LLMClientError(f"DEEPSEEK_CALL_FAILED: {exc}") from exc

    choices = getattr(resp, "choices", None) or []
    if not choices:
        raise LLMClientError("EMPTY_CHOICES")
    msg = choices[0].message
    tool_calls = getattr(msg, "tool_calls", None) or []
    if not tool_calls:
        raise LLMClientError("NO_TOOL_CALL")

    args_text = getattr(tool_calls[0].function, "arguments", "") or ""
    try:
        parsed = json.loads(args_text)
    except Exception as exc:
        raise LLMClientError(f"INVALID_TOOL_ARGUMENTS_JSON: {exc}") from exc

    normalized = _validate_tracked_rule_draft_payload(parsed)
    return {
        "model": getattr(resp, "model", None) or model_name,
        **normalized,
        "raw_json": json.dumps(parsed, ensure_ascii=False),
    }


def generate_tracked_topic_daily_summary(
    *,
    topic_title: str,
    summary_date: str,
    materials: str,
    news_count: int,
    max_summary_chars: int,
    model: str | None = None,
) -> dict[str, Any]:
    api_key = _resolve_api_key("DEEPSEEK_API_KEY")
    if not api_key:
        raise LLMClientError("MISSING_DEEPSEEK_API_KEY")

    compiled_materials = (materials or "").strip()
    if not compiled_materials:
        raise LLMClientError("EMPTY_DAILY_SUMMARY_MATERIALS")

    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover - env-specific
        raise LLMClientError(f"OPENAI_SDK_IMPORT_FAILED: {exc}") from exc

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com/beta")
    model_name = (model or "").strip() or _configured_model()
    tools = [
        {
            "type": "function",
            "function": {
                "name": "save_daily_summary",
                "description": "保存跟踪主题单日时间流摘要",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary_text": {"type": "string"},
                    },
                    "required": ["summary_text"],
                    "additionalProperties": False,
                },
            },
        }
    ]

    try:
        resp = client.chat.completions.create(
            model=model_name,
            messages=_build_tracked_daily_summary_messages(
                topic_title=topic_title,
                summary_date=summary_date,
                materials=compiled_materials,
                news_count=news_count,
                max_summary_chars=max_summary_chars,
            ),
            tools=tools,
            tool_choice={"type": "function", "function": {"name": "save_daily_summary"}},
            extra_body={"thinking": {"type": "disabled"}},
            temperature=0.2,
            timeout=180,
        )
    except Exception as exc:
        raise LLMClientError(f"DEEPSEEK_CALL_FAILED: {exc}") from exc

    choices = getattr(resp, "choices", None) or []
    if not choices:
        raise LLMClientError("EMPTY_CHOICES")
    msg = choices[0].message
    tool_calls = getattr(msg, "tool_calls", None) or []
    if not tool_calls:
        raise LLMClientError("NO_TOOL_CALL")

    args_text = getattr(tool_calls[0].function, "arguments", "") or ""
    try:
        parsed = json.loads(args_text)
    except Exception as exc:
        raise LLMClientError(f"INVALID_TOOL_ARGUMENTS_JSON: {exc}") from exc

    normalized = _validate_daily_summary_payload(parsed)
    return {
        "model": getattr(resp, "model", None) or model_name,
        **normalized,
        "raw_json": json.dumps(parsed, ensure_ascii=False),
    }


def generate_market_tag_summary(
    *,
    tag_label: str,
    range_days: int,
    news_count: int,
    note_count: int,
    materials: str,
    model: str | None = None,
) -> dict[str, Any]:
    api_key = _resolve_api_key("DEEPSEEK_API_KEY")
    if not api_key:
        raise LLMClientError("MISSING_DEEPSEEK_API_KEY")

    compiled_materials = (materials or "").strip()
    if not compiled_materials:
        raise LLMClientError("EMPTY_MARKET_TAG_SUMMARY_MATERIALS")

    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover - env-specific
        raise LLMClientError(f"OPENAI_SDK_IMPORT_FAILED: {exc}") from exc

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com/beta")
    model_name = (model or "").strip() or _configured_model()
    tools = [
        {
            "type": "function",
            "function": {
                "name": "save_market_tag_summary",
                "description": "保存单一板块近期趋势总结",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary_text": {"type": "string"},
                    },
                    "required": ["summary_text"],
                    "additionalProperties": False,
                },
            },
        }
    ]

    try:
        resp = client.chat.completions.create(
            model=model_name,
            messages=_build_market_tag_summary_messages(
                tag_label=tag_label,
                range_days=range_days,
                news_count=news_count,
                note_count=note_count,
                materials=compiled_materials,
            ),
            tools=tools,
            tool_choice={"type": "function", "function": {"name": "save_market_tag_summary"}},
            extra_body={"thinking": {"type": "disabled"}},
            temperature=0.2,
            timeout=180,
        )
    except Exception as exc:
        raise LLMClientError(f"DEEPSEEK_CALL_FAILED: {exc}") from exc

    choices = getattr(resp, "choices", None) or []
    if not choices:
        raise LLMClientError("EMPTY_CHOICES")
    msg = choices[0].message
    tool_calls = getattr(msg, "tool_calls", None) or []
    if not tool_calls:
        raise LLMClientError("NO_TOOL_CALL")

    args_text = getattr(tool_calls[0].function, "arguments", "") or ""
    try:
        parsed = json.loads(args_text)
    except Exception as exc:
        raise LLMClientError(f"INVALID_TOOL_ARGUMENTS_JSON: {exc}") from exc

    normalized = _validate_market_tag_summary_payload(parsed)
    return {
        "model": getattr(resp, "model", None) or model_name,
        **normalized,
        "raw_json": json.dumps(parsed, ensure_ascii=False),
    }


def _pi_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    # Slock 会注入指向其 runtime-pkg 的 PI_PACKAGE_DIR，该目录缺少 pi 主题文件，
    # 导致 pi 启动崩溃。清掉它，让 pi 使用自身真实安装目录。
    env.pop("PI_PACKAGE_DIR", None)
    return env


def _parse_pi_stdout(stdout: str) -> tuple[str, str, bool, str]:
    session_id = ""
    answer_parts: list[str] = []
    fallback_answer = ""
    has_error = False
    error_message = ""
    stop_reason = ""

    for line in stdout.splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue

        obj_type = obj.get("type")
        if obj_type == "session":
            session_id = (obj.get("id") or "").strip()
            continue

        if obj_type == "auto_retry_end":
            if not obj.get("success"):
                has_error = True
                error_message = (obj.get("finalError") or "pi_auto_retry_failed")[:500]
            continue

        if obj_type == "message_update":
            ev = obj.get("assistantMessageEvent") or {}
            ev_type = ev.get("type")
            if ev_type == "text_delta":
                delta = ev.get("delta")
                if isinstance(delta, str):
                    answer_parts.append(delta)
            elif ev_type == "text_end":
                content = ev.get("content")
                if isinstance(content, str):
                    fallback_answer = content
                partial = ev.get("partial") or {}
                if not fallback_answer and isinstance(partial.get("content"), list):
                    for chunk in partial["content"]:
                        if isinstance(chunk, dict) and chunk.get("type") == "text":
                            text = chunk.get("text")
                            if isinstance(text, str):
                                fallback_answer = text
                                break
                if partial.get("stopReason"):
                    stop_reason = partial["stopReason"]
            continue

        if obj_type == "message_end":
            message = obj.get("message") or {}
            if message.get("stopReason"):
                stop_reason = message["stopReason"]
            if not fallback_answer and isinstance(message.get("content"), list):
                for chunk in message["content"]:
                    if isinstance(chunk, dict) and chunk.get("type") == "text":
                        text = chunk.get("text")
                        if isinstance(text, str):
                            fallback_answer = text
                            break
            continue

        if obj_type == "agent_end" and obj.get("willRetry"):
            has_error = True
            if not error_message:
                error_message = "pi_will_retry"
            continue

    answer = ("".join(answer_parts) or fallback_answer).strip()
    if stop_reason == "error" and not has_error:
        has_error = True
        if not error_message:
            error_message = "pi_stop_reason_error"

    return session_id, answer, has_error, error_message


def generate_pi_fallback_translation(
    *,
    title: str,
    source: str,
    content: str,
    pi_provider: str,
    pi_model: str,
    timeout: int = 90,
    max_chars: int = 12000,
) -> dict[str, Any]:
    """DeepSeek 翻译失败后的 Pi 兜底：复用结构化翻译 schema/prompt，调 pi 单次无会话。

    复用现有"结构化成功 / body-only 降级 / 完全失败"分层。
    """
    body = (content or "").strip()
    if not body:
        raise LLMClientError("PI_FALLBACK_EMPTY_CONTENT")

    truncated = False
    if len(body) > max_chars:
        body = body[:max_chars]
        truncated = True

    prompt = (
        "你是专业新闻编辑与翻译。请基于英文新闻正文返回一个 JSON 对象，不要输出 JSON 以外的任何内容。\n"
        "JSON 字段要求：\n"
        '- "key_points_zh": 3 到 5 条中文要点数组，每条一句话\n'
        '- "conclusion_zh": 一句中文结论\n'
        '- "body_zh": 完整中文翻译，保持段落结构，不遗漏关键信息\n'
        f"来源：{source or '未知'}\n"
        f"标题：{title or '无标题'}\n\n"
        f"{body}"
    )

    command = [
        "pi",
        "-p",
        "--mode",
        "json",
        "--no-session",
        "--provider",
        pi_provider,
        "--model",
        pi_model,
        prompt,
    ]

    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout + 15,
            check=False,
            env=_pi_subprocess_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise LLMClientError("PI_FALLBACK_TIMEOUT") from exc
    except Exception as exc:
        raise LLMClientError(f"PI_FALLBACK_FAILED: {exc}") from exc

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    _session_id, raw_output, has_error, error_message = _parse_pi_stdout(stdout)

    if proc.returncode != 0:
        detail = stderr or stdout or f"exit={proc.returncode}"
        raise LLMClientError(f"PI_FALLBACK_FAILED: {detail[:300]}")
    if has_error:
        raise LLMClientError(f"PI_FALLBACK_FAILED: {error_message or 'pi_failed'}")

    raw_output = (raw_output or "").strip()
    raw_payload = {
        "provider": "pi-fallback-structured",
        "truncated": truncated,
        "pi_provider": pi_provider,
        "pi_model": pi_model,
        "stdout": stdout[:4000],
        "stderr": stderr[:1000],
    }

    try:
        parsed = json.loads(raw_output)
        normalized = _validate_structured_translation_payload(parsed)
        return {
            "model": pi_model or "pi-fallback",
            **normalized,
            "raw_json": json.dumps(
                {
                    **raw_payload,
                    "structured_success": True,
                    "parsed": parsed,
                },
                ensure_ascii=False,
            ),
        }
    except Exception as exc:
        body_zh = raw_output.strip()
        if not body_zh or not _contains_cjk(body_zh):
            detail = str(exc) if isinstance(exc, LLMClientError) else "PI_FALLBACK_INVALID_OUTPUT"
            raise LLMClientError(detail)
        return {
            "model": pi_model or "pi-fallback",
            "key_points_zh": [],
            "conclusion_zh": "",
            "body_zh": body_zh,
            "raw_json": json.dumps(
                {
                    **raw_payload,
                    "provider": "pi-fallback-body-only",
                    "structured_success": False,
                    "structured_error": str(exc),
                },
                ensure_ascii=False,
            ),
        }
