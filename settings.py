from __future__ import annotations

import os
import json
from copy import deepcopy
from pathlib import Path
from model_apis import normalize_api_models
import report_schedule


DEFAULT_DAILY_NEWS_DIR = Path(
    "/Users/x/Library/Mobile Documents/iCloud~md~obsidian/Documents/DailyNews"
)
DEFAULT_DAILY_BRIEFING_DIR = DEFAULT_DAILY_NEWS_DIR / "briefings" / "daily"
PROJECT_DB_PATH = Path(__file__).resolve().parent / "news_index.sqlite3"
LEGACY_DB_PATH = Path(__file__).resolve().parent.parent / "news_index.sqlite3"


def resolve_default_db_path() -> Path:
    """Preserve the original parent-directory layout as well as fresh installs."""
    return LEGACY_DB_PATH if LEGACY_DB_PATH.is_file() else PROJECT_DB_PATH


# Kept as a public compatibility constant for callers that import it.
DEFAULT_DB_PATH = resolve_default_db_path()
DEFAULT_APP_SETTINGS_PATH = Path(__file__).resolve().parent / "app_settings.json"

DEFAULT_PI_CHAT_PROVIDER = ""
DEFAULT_PI_CHAT_MODEL = ""

DEFAULT_CLASSIFICATION_CONCURRENCY = 8
MAX_CLASSIFICATION_CONCURRENCY = 32

MODEL_FEATURES = ("topics", "summaries", "reports")


def feature_model_config(settings: dict, feature: str = "chat") -> dict:
    """Old installations inherit the Chat selection once, on their next save."""
    agent = settings.get("agent", {})
    legacy = {"runtime": agent.get("runtime", "pi"), "codex": agent.get("codex", {}),
              "pi": settings.get("llm", {}).get("pi_chat", {})}
    return deepcopy(settings.get("feature_models", {}).get(feature, legacy) if feature != "chat" else legacy)


def normalize_feature_models(value: object, settings: dict) -> dict:
    from codex_client import normalize_config
    if not isinstance(value, dict) or any(key not in MODEL_FEATURES for key in value):
        raise ValueError("invalid_feature_models")
    result = {}
    for feature in MODEL_FEATURES:
        previous = feature_model_config(settings, feature)
        selected = value.get(feature, previous)
        if not isinstance(selected, dict) or selected.get("runtime", previous["runtime"]) not in ("pi", "codex"):
            raise ValueError("invalid_feature_models")
        pi = selected.get("pi", previous["pi"])
        if not isinstance(pi, dict):
            raise ValueError("invalid_feature_models")
        normalized_pi = {}
        for key, limit in (("provider", 40), ("model", 120)):
            text = pi.get(key, previous["pi"].get(key, ""))
            if not isinstance(text, str) or len(text.strip()) > limit or any(ord(char)<32 for char in text):
                raise ValueError("invalid_feature_models")
            normalized_pi[key] = text.strip()
        result[feature] = {"runtime": selected.get("runtime", previous["runtime"]),
                           "codex": normalize_config(selected.get("codex", previous["codex"])), "pi": normalized_pi}
    return result


def classification_concurrency(value: object) -> int:
    if type(value) is not int or not 1 <= value <= MAX_CLASSIFICATION_CONCURRENCY:
        raise ValueError("invalid_classification_concurrency")
    return value


def normalize_classification(value: object, *, defaults: dict | None = None, legacy: object = None) -> dict:
    if not isinstance(value, dict):
        raise ValueError("invalid_classification_settings")
    result = {"enabled": True, "concurrency": DEFAULT_CLASSIFICATION_CONCURRENCY, "unread_only": False}
    result.update(defaults or {})
    # Read old Jev settings without retaining feature controls in API management.
    if isinstance(legacy, dict):
        result.update({key: legacy[key] for key in ("concurrency", "unread_only") if key in legacy})
    result.update({key: value[key] for key in result if key in value})
    result["concurrency"] = classification_concurrency(result["concurrency"])
    for key in ("enabled", "unread_only"):
        if type(result[key]) is not bool:
            raise ValueError(f"invalid_classification_{key}")
    return result


DEFAULT_APP_SETTINGS = {
    "report_schedule": dict(report_schedule.DEFAULT_TIMES),
    "report_schedule_history": [],
    "report_windows": report_schedule.normalize_windows({}),
    "report_window_history": [],
    "api_models": normalize_api_models({}),
    "classification": normalize_classification({}),
    "llm": {
        "translation": {
            "provider": "deepseek",
            "model": "",
        },
        "pi_chat": {
            "provider": DEFAULT_PI_CHAT_PROVIDER,
            "model": DEFAULT_PI_CHAT_MODEL,
        },
    },
    "feed": {
        "hidden_source_subkeys": [],
    },
    "tracked": {
        "default_rule_params": {
            "title_weight": 1,
            "note_weight": 1,
            "summary_weight": 1,
            "content_weight": 1,
            "strong_score": 1,
            "core_score": 1,
            "context_score": 1,
            "exclude_penalty": 1,
            "threshold": 6,
        },
    },
    "agent": {
        "session_ttl_hours": 72,
        "runtime": "pi",
        "codex": {"model": "", "profile": "", "sandbox": "read-only"},
    },
    "database": {
        "path": "",
    },
    "news_summary": {"max_days": 20, "max_items": 6000},
}


def resolve_daily_news_dir() -> Path:
    raw = os.environ.get("NEWS_READER_DAILY_NEWS_DIR", "").strip()
    return Path(raw) if raw else DEFAULT_DAILY_NEWS_DIR


def resolve_daily_briefing_dir() -> Path:
    raw = os.environ.get("NEWS_READER_DAILY_BRIEFING_DIR", "").strip()
    return Path(raw) if raw else DEFAULT_DAILY_BRIEFING_DIR


def resolve_configured_db_path() -> Path | None:
    """Return the database path saved in app settings, if one is configured."""
    configured = load_app_settings().get("database", {}).get("path", "")
    if not isinstance(configured, str) or not configured.strip():
        return None
    return Path(configured.strip()).expanduser()


def resolve_db_path() -> Path:
    raw = os.environ.get("NEWS_READER_DB_PATH", "").strip()
    if raw:
        return Path(raw).expanduser()
    configured = resolve_configured_db_path()
    return configured if configured is not None else resolve_default_db_path()


def resolve_app_settings_path() -> Path:
    raw = os.environ.get("NEWS_READER_APP_SETTINGS_PATH", "").strip()
    return Path(raw) if raw else DEFAULT_APP_SETTINGS_PATH


def default_app_settings() -> dict:
    return json.loads(json.dumps(DEFAULT_APP_SETTINGS))


def _normalize_hidden_source_subkeys(value) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        key = item.strip().lower()
        if not key or "/" not in key:
            continue
        key = {
            "bloomberg/bloomberg_tech": "bloomberg/tech",
            "bloomberg/technology": "bloomberg/tech",
        }.get(key, key)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(key)
    return normalized[:500]


def load_app_settings() -> dict:
    path = resolve_app_settings_path()
    base = default_app_settings()
    if not path.exists():
        return base
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return base

    llm = payload.get("llm") if isinstance(payload, dict) else None
    if not isinstance(llm, dict):
        llm = {}

    translation = llm.get("translation")
    if isinstance(translation, dict):
        provider = translation.get("provider")
        model = translation.get("model")
        if isinstance(provider, str) and provider.strip():
            base["llm"]["translation"]["provider"] = provider.strip()
        if isinstance(model, str):
            base["llm"]["translation"]["model"] = model.strip()

    pi_chat = llm.get("pi_chat")
    if isinstance(pi_chat, dict):
        provider = pi_chat.get("provider")
        provider = provider.strip() if isinstance(provider, str) else ""
        model = pi_chat.get("model")
        if isinstance(provider, str):
            base["llm"]["pi_chat"]["provider"] = provider
        if isinstance(model, str):
            base["llm"]["pi_chat"]["model"] = model.strip()

    try:
        base["api_models"] = normalize_api_models(payload.get("api_models", {}) if isinstance(payload, dict) else {})
    except ValueError:
        pass

    api_models = payload.get("api_models") if isinstance(payload, dict) else None
    legacy_classification = api_models.get("jev") if isinstance(api_models, dict) else None
    try:
        base["classification"] = normalize_classification(
            payload.get("classification", {}) if isinstance(payload, dict) else {}, legacy=legacy_classification)
    except ValueError:
        pass

    feed = payload.get("feed") if isinstance(payload, dict) else None
    if isinstance(feed, dict):
        base["feed"]["hidden_source_subkeys"] = _normalize_hidden_source_subkeys(
            feed.get("hidden_source_subkeys")
        )

    tracked = payload.get("tracked") if isinstance(payload, dict) else None
    if isinstance(tracked, dict):
        default_rule_params = tracked.get("default_rule_params")
        if isinstance(default_rule_params, dict):
            for key in base["tracked"]["default_rule_params"].keys():
                value = default_rule_params.get(key)
                if isinstance(value, (int, float)):
                    base["tracked"]["default_rule_params"][key] = value

    agent = payload.get("agent") if isinstance(payload, dict) else None
    if isinstance(agent, dict):
        from codex_client import normalize_config
        if agent.get("runtime") in ("pi", "codex"):
            base["agent"]["runtime"] = agent["runtime"]
        if isinstance(agent.get("codex"), dict):
            try:
                base["agent"]["codex"] = normalize_config(agent["codex"])
            except ValueError:
                pass
        ttl = agent.get("session_ttl_hours")
        if ttl in (24, 72):
            base["agent"]["session_ttl_hours"] = ttl

    database = payload.get("database") if isinstance(payload, dict) else None
    if isinstance(database, dict):
        configured_path = database.get("path")
        if isinstance(configured_path, str):
            base["database"]["path"] = configured_path.strip()

    from news_summary_selection import limits
    try:
        base["news_summary"] = limits(payload.get("news_summary") if isinstance(payload, dict) else None)
    except ValueError:
        pass
    try:
        base["feature_models"] = normalize_feature_models(payload.get("feature_models", {}) if isinstance(payload, dict) else {}, base)
    except ValueError:
        base["feature_models"] = normalize_feature_models({}, base)
    if isinstance(payload, dict):
        try:
            times = report_schedule.normalize(payload.get("report_schedule", {}))
            revisions = report_schedule.history(payload.get("report_schedule_history"), times)
            base.update(report_schedule=times, report_schedule_history=revisions)
        except ValueError:
            pass
        try:
            windows = report_schedule.normalize_windows(payload.get("report_windows", {}))
            revisions = report_schedule.window_history(payload.get("report_window_history"), windows)
            base.update(report_windows=windows, report_window_history=revisions)
        except ValueError:
            pass
    return base


def save_app_settings(payload: dict) -> Path:
    path = resolve_app_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # The scheduler also reads this file: never expose a partially written JSON.
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return path
