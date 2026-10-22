"""Managed HTTP model providers and their native connection probes.

TypeSafe contracts: https://docs.typesafe.ai/api/system-one and /api/list-models.
Credentials stay in the existing environment/Keychain store, never in settings.
"""
from __future__ import annotations

API_PROVIDERS = {
    "deepseek": {
        "label": "DeepSeek", "secret_name": "DEEPSEEK_API_KEY",
        "models_url": "https://api.deepseek.com/models",
        "test_url": "https://api.deepseek.com/beta/chat/completions",
        "model_path": "llm.translation.model",
        "description": "为单篇新闻提供中文正文与结构化精读。",
    },
    "jev": {
        "label": "Jev", "secret_name": "TYPESAFE_API_KEY",
        "models_url": "https://api.typesafe.ai/v1/models",
        "test_url": "https://api.typesafe.ai/v1/systemone",
        "model_path": "api_models.jev.model", "default_model": "jev-latest",
        "description": "TypeSafe 的分类、评分与真假判断模型。",
    },
}


def normalize_api_models(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("invalid_api_models")
    normalized = {}
    for provider in API_PROVIDERS:
        if provider == "deepseek":
            continue  # Keep the existing translation setting as the single source.
        config = value.get(provider, {})
        if not isinstance(config, dict):
            raise ValueError("invalid_api_models")
        model = config.get("model", "")
        if not isinstance(model, str) or len(model.strip()) > 120 or any(ord(c) < 32 for c in model):
            raise ValueError("invalid_model")
        normalized[provider] = {"model": model.strip()}
    return normalized


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
        result.append({"value": name, "label": name, "source": "official"})
    return result


def probe_payload(provider: str, model: str) -> dict:
    if provider == "jev":
        return {"model": model, "state": "The sky is blue.", "questions": {
            "connection_check": {"type": "noul", "instructions": "Does the text mention the sky?"},
        }}
    return {"model": model, "messages": [{"role": "user", "content": "Reply OK."}], "max_tokens": 16}


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
