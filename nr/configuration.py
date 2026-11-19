from __future__ import annotations

import codex_client
import model_apis
import news_summaries
import pi_models
from feed_sources import normalize_hidden_source_subkeys
from nr import tracked_rules
from settings import (
    default_app_settings,
    load_app_settings,
    normalize_classification,
    normalize_feature_models,
)


AGENT_SESSION_TTL_OPTIONS = (24, 72)
_DEPRECATED_DEEPSEEK_MODELS = {
    "deepseek-chat": "deepseek-v4-flash",
    "deepseek-reasoner": "deepseek-v4-pro",
}


def normalize_deepseek_model(model: str) -> str:
    return _DEPRECATED_DEEPSEEK_MODELS.get((model or "").strip().lower(), (model or "").strip())


def current_runtime_settings() -> dict:
    raw = load_app_settings()
    merged = default_app_settings()
    llm = raw.get("llm") if isinstance(raw, dict) else {}
    if isinstance(llm, dict):
        translation = llm.get("translation")
        if isinstance(translation, dict):
            merged["llm"]["translation"] = model_apis.normalize_translation(translation)
            merged["llm"]["translation"]["model"] = normalize_deepseek_model(merged["llm"]["translation"]["model"])
        pi_chat = llm.get("pi_chat")
        if isinstance(pi_chat, dict):
            merged["llm"]["pi_chat"] = pi_models.normalize_config(pi_chat)
    feed = raw.get("feed") if isinstance(raw, dict) else {}
    if isinstance(feed, dict):
        merged["feed"]["hidden_source_subkeys"] = normalize_hidden_source_subkeys(
            feed.get("hidden_source_subkeys")
        )
    tracked = raw.get("tracked") if isinstance(raw, dict) else {}
    if isinstance(tracked, dict):
        merged["tracked"]["default_rule_params"] = tracked_rules.tracked_default_rule_params(
            tracked.get("default_rule_params")
        )
    agent = raw.get("agent") if isinstance(raw, dict) else {}
    if isinstance(agent, dict) and agent.get("session_ttl_hours") in AGENT_SESSION_TTL_OPTIONS:
        merged["agent"]["session_ttl_hours"] = int(agent["session_ttl_hours"])
    if isinstance(agent, dict):
        if isinstance(agent.get("runtime"), str) and agent["runtime"] in {"pi", "codex"}:
            merged["agent"]["runtime"] = agent["runtime"]
        try:
            merged["agent"]["codex"] = codex_client.saved_preferences(agent.get("codex", {}))
        except ValueError:
            pass
    database = raw.get("database") if isinstance(raw, dict) else {}
    if isinstance(database, dict) and isinstance(database.get("path"), str):
        merged["database"]["path"] = database["path"].strip()
    merged["news_summary"] = news_summaries.selection.limits(raw.get("news_summary"))
    merged["api_models"] = model_apis.normalize_api_models(raw.get("api_models", {}))
    merged["classification"] = normalize_classification(raw.get("classification", {}))
    merged["feature_models"] = normalize_feature_models(raw.get("feature_models", {}), merged)
    merged["collection"] = raw["collection"]
    merged["report_schedule"] = raw["report_schedule"]
    merged["report_schedule_history"] = raw["report_schedule_history"]
    merged["report_windows"] = raw["report_windows"]
    merged["report_window_history"] = raw["report_window_history"]
    return merged
