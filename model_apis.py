"""Managed HTTP model providers and their native connection probes.

TypeSafe contracts: https://docs.typesafe.ai/api/system-one and /api/list-models.
Credentials stay in the existing environment/Keychain store, never in settings.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def request_headers(api_key: str) -> dict[str, str]:
    # OpenCode rejects urllib's default client signature with Cloudflare 1010.
    # Identify this application, consistently with the existing DeepSeek client.
    return {"Authorization": f"Bearer {api_key}", "Accept": "application/json",
            "User-Agent": "news-reader-settings/1.0"}


def http_error_details(error) -> dict:
    """Translate known upstream errors without returning arbitrary response text."""
    try:
        payload = json.loads(error.read(8192).decode("utf-8"))
    except (OSError, ValueError, TypeError, AttributeError):
        payload = {}
    finally:
        error.close()
    payload = payload if isinstance(payload, dict) else {}
    nested = payload.get("error")
    nested = nested if isinstance(nested, dict) else {}
    codes = [nested.get("type"), nested.get("code"), payload.get("error_name"), payload.get("error_code")]
    known = {
        "FreeUsageLimitError": ("free_usage_limit", "免费模型的使用限制已触发。请在 OpenCode 控制台检查免费额度与访问限制，或选择支持外部应用调用的模型。"),
        "FreeTierError": ("free_model_restricted", "所选免费模型仅允许在 OpenCode 内使用，第三方应用不可调用。请选择支持外部调用的文本模型。"),
        "GoUsageLimitError": ("subscription_limit", "OpenCode Go 的用量限制已触发。请在控制台检查用量与恢复时间。"),
        "MonthlyLimitError": ("budget_limit", "账户的月度消费上限已达到。请在服务商控制台检查消费限制。"),
        "InsufficientBalanceError": ("insufficient_balance", "账户可用余额不足。请在服务商控制台检查余额。"),
        "insufficient_quota": ("insufficient_quota", "账户可用额度不足。请在服务商控制台检查额度与用量限制。"),
        "quota_exceeded": ("insufficient_quota", "账户可用额度不足。请在服务商控制台检查额度与用量限制。"),
        "RateLimitError": ("rate_limit", "服务端请求频率限制已触发。请稍后重试。"),
        "rate_limit_error": ("rate_limit", "服务端请求频率限制已触发。请稍后重试。"),
        "rate_limit_exceeded": ("rate_limit", "服务端请求频率限制已触发。请稍后重试。"),
        "browser_signature_banned": ("client_blocked", "服务端拒绝了客户端标识，请检查应用版本与请求配置。"),
    }
    reason, message = "http_error", ""
    for code in codes:
        if isinstance(code, str) and code in known:
            reason, message = known[code]
            break
    else:
        reason, message = {
            401: ("authentication_failed", "密钥鉴权失败，请检查密钥是否有效。"),
            402: ("billing_required", "服务端要求检查计费配置，请在控制台确认余额与可用额度。"),
            403: ("access_denied", "服务端拒绝访问，请检查所选模型、账户权限及客户端限制。"),
            429: ("usage_limited", "服务端限制了请求，可能涉及请求频率或使用额度。请查看服务商控制台；当前响应未说明具体原因。"),
        }.get(error.code, (reason, message))

    result = {"upstream_status": error.code, "reason": reason, "message": message}
    headers = error.headers or {}
    value = headers.get("Retry-After")
    seconds = None
    if value:
        try:
            seconds = float(value)
        except (ValueError, TypeError):
            try:
                deadline = parsedate_to_datetime(value)
                seconds = (deadline - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                pass
    elif headers.get("retry-after-ms"):
        try:
            seconds = float(headers["retry-after-ms"]) / 1000
        except (ValueError, TypeError):
            pass
    if seconds is not None and math.isfinite(seconds) and 0 <= seconds <= 365 * 86400:
        seconds = max(1, math.ceil(seconds))
        result["retry_after_seconds"] = seconds
        wait = f"{seconds} 秒" if seconds < 60 else f"{math.ceil(seconds / 60)} 分钟"
        result["message"] += f"服务端建议等待 {wait} 后再试。"
    return result


API_PROVIDERS = {
    "deepseek": {
        "label": "DeepSeek", "secret_name": "DEEPSEEK_API_KEY",
        "models_url": "https://api.deepseek.com/models",
        "test_url": "https://api.deepseek.com/beta/chat/completions",
        "translation_base_url": "https://api.deepseek.com/beta",
        "model_path": "llm.translation.model",
        "description": "用于新闻正文翻译、中文精读及 X 评论总结；旧版主题与市场标签接口也使用此服务。",
        "fallback": "正文翻译与精读按上方顺序切换 API，全部失败后可尝试 Pi。原文保留，可重新翻译。X 评论总结及旧版功能仍单独使用 DeepSeek。",
    },
    "jev": {
        "label": "Jev", "secret_name": "TYPESAFE_API_KEY",
        "models_url": "https://api.typesafe.ai/v1/models",
        "test_url": "https://api.typesafe.ai/v1/systemone",
        "model_path": "api_models.jev.model", "default_model": "jev-latest",
        "description": "TypeSafe 的判断模型，用于新闻分类、主题新闻归属和重要进展筛选。",
        "fallback": "临时错误自动退避重试，限流时降低并发；密钥失效时暂停等待修复。已有分类与主题内容保留，未分类新闻仍可在「全部」阅读；不会自动改用其他服务。",
    },
    "opencode": {
        "label": "OpenCode", "secret_name": "OPENCODE_API_KEY",
        "models_url": "https://opencode.ai/inference/v1/models",
        "test_url": "https://opencode.ai/inference/openai/v1/chat/completions",
        "translation_base_url": "https://opencode.ai/inference/openai/v1",
        "model_path": "api_models.opencode.model", "default_model": "",
        "description": "直接用于新闻正文翻译与精读，使用本页密钥和模型。也可供 Pi 调用；Pi 功能的模型在「模型与 Agent」选择，Pi 自有凭据优先的规则仅适用于 Pi。",
        "fallback": "正文翻译按上方顺序切换 API。模型目录已隐藏免费模型；选择后可测试当前账户是否能够调用。",
        "docs_url": "https://opencode.ai/v2/docs/console/api/inference",
    },
}

TRANSLATION_PROVIDERS = tuple(provider for provider, config in API_PROVIDERS.items() if config.get("translation_base_url"))


def normalize_translation(value: object, *, defaults: dict | None = None) -> dict:
    if not isinstance(value, dict):
        raise ValueError("invalid_llm_settings")
    result = {"provider": "deepseek", "model": "", "fallback_enabled": True, "pi_fallback_enabled": True}
    result.update({key: val for key, val in (defaults or {}).items() if key in result})
    result.update({key: val for key, val in value.items() if key in result})
    provider, model = result["provider"], result["model"]
    if not isinstance(provider, str) or provider.strip().lower() not in TRANSLATION_PROVIDERS:
        raise ValueError("unsupported_translation_provider")
    if not isinstance(model, str) or len(model.strip()) > 120 or any(ord(c) < 32 for c in model):
        raise ValueError("invalid_translation_model")
    for key in ("fallback_enabled", "pi_fallback_enabled"):
        if type(result[key]) is not bool:
            raise ValueError("invalid_translation_fallback")
    result.update(provider=provider.strip().lower(), model=model.strip())
    return result


def normalize_api_models(value: object, *, defaults: dict | None = None) -> dict:
    if not isinstance(value, dict):
        raise ValueError("invalid_api_models")
    normalized = {}
    for provider in API_PROVIDERS:
        if provider == "deepseek":
            continue  # Keep the existing translation setting as the single source.
        config = value.get(provider, {})
        if not isinstance(config, dict):
            raise ValueError("invalid_api_models")
        model = config.get("model", (defaults or {}).get(provider, {}).get("model", ""))
        if not isinstance(model, str) or len(model.strip()) > 120 or any(ord(c) < 32 for c in model):
            raise ValueError("invalid_model")
        normalized[provider] = {"model": model.strip()}
    return normalized


def is_hidden_model(provider: str, model: str, metadata: dict | None = None) -> bool:
    """Hide OpenCode free models from catalogs; preserve explicit saved settings."""
    if provider not in {"opencode", "opencode-go"}:
        return False
    name = (model or "").strip().lower().rsplit("/", 1)[-1]
    if name.endswith("-free") or name == "big-pickle":
        return True
    cost = (metadata or {}).get("cost")
    if not isinstance(cost, dict):
        return False
    # Console and Pi expose prices in different public metadata shapes.
    text_cost = cost.get("text")
    prices = [text_cost.get(direction) for direction in ("input", "output")] if isinstance(text_cost, dict) else []
    prices = [item.get("price") if isinstance(item, dict) else None for item in prices] if prices else [cost.get("input"), cost.get("output")]
    return all(type(price) in (int, float) and price == 0 for price in prices)


def parse_model_options(provider: str, payload: object) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("models" if provider == "jev" else "data")
    if not isinstance(rows, list):
        return []
    result, seen = [], set()
    for row in rows:
        name = row.get("name" if provider == "jev" else "id") if isinstance(row, dict) else None
        if not isinstance(name, str) or not name.strip() or name.strip() in seen:
            continue
        name = name.strip()
        seen.add(name)
        if is_hidden_model(provider, name, row):
            continue
        result.append({"value": name, "label": name, "source": "official"})
    return result


def probe_payload(provider: str, model: str) -> dict:
    if provider == "jev":
        return {"model": model, "state": "The sky is blue.", "questions": {
            "connection_check": {"type": "noul", "instructions": "Does the text mention the sky?"},
        }}
    return {"model": model, "messages": [{"role": "user", "content": "Reply OK."}],
            "max_tokens": 128 if provider == "opencode" else 16}


def valid_probe_response(provider: str, payload: object) -> bool:
    if not isinstance(payload, dict) or payload.get("error"):
        return False
    if provider == "jev":
        answers = payload.get("answers")
        answer = answers.get("connection_check") if isinstance(answers, dict) else None
        if not isinstance(answer, dict) or answer.get("type") != "noul":
            return False
        probability = answer.get("noul")
        return type(probability) in (int, float) and 0 <= probability <= 1
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return False
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return False
    # Reasoning models may exhaust this small probe's budget before final text.
    return any(isinstance(message.get(key), str) and message[key].strip()
               for key in ("content", "reasoning_content"))
