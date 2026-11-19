from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import jsonify, request

import codex_client
import connection_tests
import model_apis
import model_catalogs
import news_collection
import news_summaries
import periodic_reports
import pi_models
import source_deletion
import x_accounts
from feed_sources import (
    derive_source_subkey,
    normalize_hidden_source_subkeys,
    normalize_source_subkey_key,
    visible_news_sql,
)
from llm_client import resolve_translation_default_model
from nr import configuration, tracked_rules
from secret_store import SecretStoreError, delete_secret, has_secret, read_secret, write_secret
from settings import (
    DEFAULT_PI_CHAT_MODEL,
    DEFAULT_PI_CHAT_PROVIDER,
    normalize_classification,
    normalize_feature_models,
    save_app_settings,
)


SECRET_PROVIDER_MAP = {
    **{provider: config["secret_name"] for provider, config in model_apis.API_PROVIDERS.items()},
    "openai": "OPENAI_API_KEY",
}
DEEPSEEK_MODEL_FALLBACKS = [
    "deepseek-v4-flash",
    "deepseek-v4-pro",
]
SETTINGS_MODEL_OPTION_LIMIT = 40
DEEPSEEK_MODELS_URL = "https://api.deepseek.com/models"


def provider_secret_name(provider: str) -> str:
    secret_name = SECRET_PROVIDER_MAP.get(model_apis.canonical_provider(provider))
    if not secret_name:
        raise ValueError("unsupported_provider")
    return secret_name


def provider_has_configured_key(provider: str) -> bool:
    secret_name = provider_secret_name(provider)
    if (os.getenv(secret_name) or "").strip():
        return True
    try:
        return has_secret(secret_name)
    except SecretStoreError:
        return False


def provider_secret_value(provider: str) -> str:
    secret_name = provider_secret_name(provider)
    value = (os.getenv(secret_name) or "").strip()
    if value:
        return value
    try:
        return (read_secret(secret_name) or "").strip()
    except SecretStoreError:
        return ""


def save_provider_secret(provider: str, value: object) -> None:
    secret_name = provider_secret_name(provider)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("empty_key")
    write_secret(secret_name, value.strip())


def remove_provider_secret(provider: str) -> None:
    secret_name = provider_secret_name(provider)
    delete_secret(secret_name)


def build_model_option(value: str, *, label: str = "", description: str = "", source: str = "") -> dict:
    normalized = (value or "").strip()
    if not normalized:
        raise ValueError("empty_model_value")
    return {
        "value": normalized,
        "label": (label or normalized).strip(),
        "description": (description or "").strip(),
        "source": (source or "").strip(),
    }


def merge_model_options(options: list[dict], saved_model: str) -> list[dict]:
    merged: list[dict] = []
    seen: set[str] = set()
    for item in options:
        if not isinstance(item, dict):
            continue
        value = (item.get("value") or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        merged.append(
            {
                "value": value,
                "label": (item.get("label") or value).strip(),
                "description": (item.get("description") or "").strip(),
                "source": (item.get("source") or "").strip(),
            }
        )
    saved = (saved_model or "").strip()
    if saved and saved not in seen:
        merged.append(build_model_option(saved, description="当前已保存模型", source="saved"))
    return merged[:SETTINGS_MODEL_OPTION_LIMIT]


def trim_settings_error(value: object, limit: int = 160) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit - 1]}…"


def fallback_model_options(values: list[str], *, source: str) -> list[dict]:
    return [build_model_option(value, source=source) for value in values]


def sort_deepseek_model_ids(model_ids: list[str]) -> list[str]:
    priority = {name: index for index, name in enumerate(DEEPSEEK_MODEL_FALLBACKS)}
    return sorted(
        {model_id.strip() for model_id in model_ids if model_id and model_id.strip()},
        key=lambda model_id: (priority.get(model_id, len(priority)), model_id),
    )


def parse_deepseek_model_options(payload: object) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("data")
    if not isinstance(rows, list):
        return []
    ids: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        model_id = configuration.normalize_deepseek_model((row.get("id") or "").strip())
        if model_id and model_id not in seen:
            seen.add(model_id)
            ids.append(model_id)
    return [build_model_option(model_id, source="official") for model_id in sort_deepseek_model_ids(ids)]


def deepseek_settings_snapshot(saved_model: str) -> dict:
    resolved_default_model = resolve_translation_default_model()
    configured = provider_has_configured_key("deepseek")
    options = fallback_model_options(DEEPSEEK_MODEL_FALLBACKS, source="fallback") if configured else []
    status_text = "尚未配置 API 密钥，保存密钥后可获取模型。"
    last_error = ""
    models_endpoint_reachable = None
    used_fallback = configured
    source = "fallback" if configured else "unavailable"
    api_key = ""

    if configured:
        api_key = provider_secret_value("deepseek")
        if not api_key:
            status_text = "检测到已配置 key，但当前读取失败，使用默认候选。"
            last_error = "read_failed"
        else:
            request = Request(
                DEEPSEEK_MODELS_URL,
                headers=model_apis.request_headers(api_key),
            )
            try:
                with urlopen(request, timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                parsed_options = parse_deepseek_model_options(payload)
                if parsed_options:
                    options = parsed_options
                    models_endpoint_reachable = True
                    used_fallback = False
                    source = "official"
                    status_text = "官方 /models 可访问。"
                else:
                    models_endpoint_reachable = False
                    last_error = "empty_models"
                    status_text = "官方 /models 返回空列表，使用默认候选。"
            except HTTPError as exc:
                models_endpoint_reachable = False
                last_error = f"http_{exc.code}"
                status_text = f"官方 /models 访问失败（HTTP {exc.code}），使用默认候选。"
            except URLError as exc:
                models_endpoint_reachable = False
                last_error = trim_settings_error(getattr(exc, "reason", exc))
                status_text = "官方 /models 暂不可达，使用默认候选。"
            except Exception as exc:  # pragma: no cover - env-specific
                models_endpoint_reachable = False
                last_error = trim_settings_error(exc)
                status_text = "官方 /models 检查失败，使用默认候选。"

    return {
        "service": {
            "configured": configured,
            "masked_key": (api_key[:4] + "••••••••" + api_key[-4:]) if len(api_key) > 12 else ("••••••••" if configured else ""),
            "environment_override": bool(os.getenv("DEEPSEEK_API_KEY", "").strip()),
            "models_endpoint_reachable": models_endpoint_reachable,
            "used_fallback": used_fallback,
            "last_error": last_error,
            "status_text": status_text,
        },
        "catalog": {
            "source": source,
            "saved_model": (saved_model or "").strip(),
            "resolved_default_model": resolved_default_model,
            "custom_allowed": True,
            "default_label": resolved_default_model,
            "options": merge_model_options(options, saved_model),
        },
    }


PI_CHAT_MODEL_FALLBACKS = [DEFAULT_PI_CHAT_MODEL]
PI_CHAT_PROVIDER_FALLBACKS = [DEFAULT_PI_CHAT_PROVIDER]


def parse_pi_model_catalog(stdout: str) -> tuple[list[str], dict[str, list[str]]]:
    """解析 `pi --list-models` 表格 provider/model 两列。

    跳过表头、空行与不足两列的畸形行；provider 与 provider 内 model 均去重保序。
    """
    providers: list[str] = []
    models_by_provider: dict[str, list[str]] = {}
    seen_providers: set[str] = set()
    seen_models: dict[str, set[str]] = {}
    for line in (stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        provider = parts[0].strip()
        model = parts[1].strip()
        if not provider or not model or provider.lower() == "provider":
            continue
        if model_apis.is_hidden_model(provider, model):
            continue
        if provider not in seen_providers:
            seen_providers.add(provider)
            providers.append(provider)
            models_by_provider[provider] = []
            seen_models[provider] = set()
        if model not in seen_models[provider]:
            seen_models[provider].add(model)
            models_by_provider[provider].append(model)
    return providers, models_by_provider


def parse_pi_providers(stdout: str) -> list[str]:
    """解析 `pi --list-models` 表格第一列 provider，去重保序；跳过表头与畸形行。"""
    providers, _ = parse_pi_model_catalog(stdout)
    return providers


def pi_chat_settings_snapshot(saved_model: str, saved_provider: str = "") -> dict:
    options = {}
    error = notice = ""
    try:
        result = pi_models.catalog()
        error, notice = result.get("error", ""), result.get("notice", "")
        for option in result["options"]:
            options.setdefault(option["provider"], []).append({**option, "source": "pi-model-registry"})
    except RuntimeError:
        error = "Pi 模型目录读取失败。"
    # Saved absent values are preserved by the form, not presented as discovered models.
    return {"service": {"last_error": error, "status_text": error or "目录仅表示候选，不证明调用成功。"},
            "catalog": {"source": "unavailable" if error else "pi-model-registry", "error": error, "notice": notice,
                        "saved_model": saved_model, "saved_provider": saved_provider,
                        "resolved_default_model": "", "resolved_default_provider": "",
                        "default_label": "请选择模型", "custom_allowed": False,
                        "options": options.get(saved_provider, []),
                        "provider_options": [build_model_option(p, source="pi-model-registry") for p in options],
                        "model_options_by_provider": options}}


def managed_api_snapshot(provider: str, saved_model: str = "", *, refresh: bool = False) -> dict:
    provider = model_apis.canonical_provider(provider)
    if provider == "deepseek":
        return deepseek_settings_snapshot(saved_model)
    config = model_apis.API_PROVIDERS[provider]
    key = provider_secret_value(provider)
    configured = bool(key) or provider_has_configured_key(provider)
    default_model = config.get("default_model", "")
    options = fallback_model_options([default_model], source="fallback") if configured and default_model else []
    service = {
        "configured": configured,
        "masked_key": (key[:4] + "••••••••" + key[-4:]) if len(key) > 12 else ("••••••••" if configured else ""),
        "environment_override": bool(os.getenv(config["secret_name"], "").strip()),
        "models_endpoint_reachable": None,
        "status_text": "点击获取模型以读取可用目录。" if configured else "尚未配置 API 密钥。",
    }
    source = "fallback" if options else "unavailable"
    if refresh and configured and not key:
        service.update(models_endpoint_reachable=False, reason="key_read_failed",
                       status_text="读取已保存的 API 密钥失败，请重新保存密钥后重试。")
    elif refresh and configured:
        service["models_endpoint_reachable"] = False
        try:
            probe = Request(config["models_url"], headers=model_apis.request_headers(key, provider))
            with urlopen(probe, timeout=model_apis.MODEL_CATALOG_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8"))
                parsed = model_apis.parse_model_options(provider, payload)
            # A valid directory may contain only free or incompatible models.
            empty_filtered = provider in {"opencode", "opencode-go", "openai-decisions"} and isinstance(payload, dict) and isinstance(payload.get("data"), list) and all(
                isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"].strip() for row in payload["data"])
            if not parsed and not empty_filtered:
                raise ValueError("empty_models")
            options, source = parsed, "official"
            service.update(models_endpoint_reachable=True, status_text="模型目录已获取。")
        except HTTPError as exc:
            details = model_apis.http_error_details(exc)
            service.update(status_text=f"获取模型失败（HTTP {exc.code}）。{details['message']}",
                           **{name: value for name, value in details.items() if name != "message"})
        except (URLError, OSError) as exc:
            # urllib wraps connect/TLS timeouts in URLError.reason; reads may
            # raise TimeoutError directly. Neither indicates invalid credentials.
            cause = exc.reason if isinstance(exc, URLError) else exc
            if isinstance(cause, TimeoutError):
                service.update(reason="connection_timeout", status_text=(
                    f"模型目录请求超时（{model_apis.MODEL_CATALOG_TIMEOUT_SECONDS} 秒，"
                    "含连接、HTTPS 握手或响应读取），请检查网络后重试。"))
            else:
                service.update(reason="network_error", status_text="无法连接模型目录服务，请检查网络或代理设置后重试。")
        except ValueError:
            service.update(reason="response_parse_failed", status_text="模型目录响应解析失败：返回内容不是有效的 JSON 或模型目录格式，请稍后重试。")
        except Exception:
            service.update(reason="catalog_error", status_text="暂时无法获取模型目录，请稍后重试。")
    return {"service": service, "catalog": {
        "source": source, "saved_model": saved_model, "custom_allowed": True,
        "resolved_default_model": config["default_model"],
        "options": merge_model_options(options, "" if model_apis.is_hidden_model(provider, saved_model) else saved_model),
    }}


def current_pi_chat_config() -> dict[str, str]:
    llm = configuration.current_runtime_settings()["llm"]
    pi_chat = llm.get("pi_chat", {})
    return {
        "provider": (pi_chat.get("provider") or "").strip(),
        "model": (pi_chat.get("model") or "").strip(),
    }


class Preferences:
    def __init__(self, db):
        self.db = db

    def build_feed_source_settings_snapshot(self, hidden_keys: list[str], conn=None) -> dict:
        hidden = set(normalize_hidden_source_subkeys(hidden_keys or []))
        groups: dict[str, dict] = {}
        owns_connection = conn is None
        conn = conn or self.db.connect()
        try:
            rows = conn.execute(
                f"""
                SELECT items.url, items.source_type, items.source, items.source_name, st.read_at
                FROM items
                LEFT JOIN item_state st ON st.item_id = items.id
                WHERE {visible_news_sql()}
                """
            ).fetchall()
        finally:
            if owns_connection:
                conn.close()

        for row in rows:
            item = derive_source_subkey(row["url"], row["source_type"], row["source"], row["source_name"])
            if not item:
                continue
            source_key = item["source_key"]
            group = groups.setdefault(
                source_key,
                {"source_key": source_key, "label": item["source_label"], "items": {}},
            )
            bucket = group["items"].setdefault(
                item["key"],
                {
                    "key": item["key"],
                    "label": item["label"],
                    "count": 0,
                    "unread_count": 0,
                    "hidden": item["key"] in hidden,
                },
            )
            bucket["count"] += 1
            if row["read_at"] is None:
                bucket["unread_count"] += 1

        known_order = ["reuters", "bloomberg", "techcrunch", "ars", "x"]
        ordered_groups = []
        known_item_keys: set[str] = set()
        for source_key in sorted(groups.keys(), key=lambda key: (known_order.index(key) if key in known_order else len(known_order), groups[key]["label"].lower())):
            group = groups[source_key]
            items = list(group["items"].values())
            items.sort(key=lambda item: (item["label"].lower(), item["key"]))
            known_item_keys.update(item["key"] for item in items)
            ordered_groups.append({"source_key": source_key, "label": group["label"], "items": items})
        visible_hidden_keys = [key for key in normalize_hidden_source_subkeys(hidden_keys or []) if key in known_item_keys]
        return {"hidden_source_subkeys": visible_hidden_keys, "groups": ordered_groups}

    def _database_path_from_payload(self, payload: object, current_settings: dict) -> str:
        database = payload.get("database") if isinstance(payload, dict) else None
        if not isinstance(database, dict) or "path" not in database:
            return str(current_settings.get("database", {}).get("path") or "").strip()
        value = database.get("path")
        if not isinstance(value, str):
            raise ValueError("invalid_database_path")
        return value.strip()

    def choose_existing_database_path(self) -> str:
        """Open the host-native file chooser and return a validated SQLite path."""
        if sys.platform != "darwin" or shutil.which("osascript") is None:
            raise ValueError("database_picker_unavailable")

        script = """
try
    set selectedFile to choose file with prompt "选择已有 NewsReader SQLite 数据库"
    return POSIX path of selectedFile
on error number -128
    return "__CANCELLED__"
end try
"""
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError("database_picker_failed") from None
        if result.returncode != 0:
            raise ValueError("database_picker_failed")
        selected = result.stdout.strip()
        if not selected or selected == "__CANCELLED__":
            raise ValueError("database_picker_cancelled")
        return self.validate_existing_database_path(selected)

    def validate_existing_database_path(self, value: str) -> str:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = (self.db.root / candidate).resolve()
        else:
            candidate = candidate.resolve()
        if not candidate.is_file():
            raise ValueError("database_path_must_exist")
        if not os.access(candidate, os.R_OK):
            raise ValueError("database_path_not_readable")
        if not os.access(candidate, os.W_OK):
            raise ValueError("database_path_not_writable")
        try:
            if candidate.read_bytes()[:16] != b"SQLite format 3\x00":
                raise ValueError("database_path_not_sqlite")
            uri = f"file:{candidate.as_posix()}?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=5) as conn:
                conn.execute("PRAGMA schema_version").fetchone()
        except (OSError, sqlite3.Error):
            raise ValueError("database_path_not_sqlite") from None
        return str(candidate)

    def database_runtime_snapshot(self, settings: dict | None = None) -> dict:
        settings = settings or configuration.current_runtime_settings()
        env_value = os.environ.get("NEWS_READER_DB_PATH", "").strip()
        configured = str(settings.get("database", {}).get("path") or "").strip()
        return {
            "path": str(self.db.path.expanduser().resolve()),
            "configured_path": configured,
            "environment_override": bool(env_value),
            "environment_variable": "NEWS_READER_DB_PATH",
            "editable": not bool(env_value),
            "native_picker_available": bool(sys.platform == "darwin" and shutil.which("osascript")),
            "restart_required": True,
            "notice": (
                "当前由环境变量 NEWS_READER_DB_PATH 控制，设置页不能覆盖。"
                if env_value
                else "选择已有 SQLite 文件后，重启 NewsReader 才会切换；不会迁移或复制数据。"
            ),
        }

    def serialize_runtime_settings(self) -> dict:
        settings = configuration.current_runtime_settings()
        with closing(self.db.connect()) as conn:
            accounts = x_accounts.snapshot(conn)
        translation_model = (settings["llm"]["translation"].get("model") or "").strip()
        pi_chat_model = (settings["llm"]["pi_chat"].get("model") or "").strip()
        pi_chat_provider = (settings["llm"]["pi_chat"].get("provider") or "").strip()
        deepseek_snapshot = deepseek_settings_snapshot(translation_model)
        api_snapshots = {
            provider: managed_api_snapshot(provider, settings["api_models"][provider]["model"])
            for provider in model_apis.API_PROVIDERS if provider != "deepseek"
        }
        pi_snapshot = pi_chat_settings_snapshot(pi_chat_model, pi_chat_provider)
        feed_source_snapshot = self.build_feed_source_settings_snapshot(settings["feed"].get("hidden_source_subkeys") or [])
        return {
            **accounts,
            "api_status": {
                "deepseek": deepseek_snapshot["service"],
                **{provider: snapshot["service"] for provider, snapshot in api_snapshots.items()},
                "pi": pi_snapshot["service"],
                "codex": {},
            },
            "model_catalogs": {
                "translation": deepseek_snapshot["catalog"],
                **{provider: snapshot["catalog"] for provider, snapshot in api_snapshots.items()},
                "pi_chat": pi_snapshot["catalog"],
                "codex": model_catalogs.cached_catalog(settings["agent"]["codex"]),
            },
            "llm": settings["llm"],
            "api_models": settings["api_models"],
            "classification": settings["classification"],
            "api_providers": [{"id": provider, **config} for provider, config in model_apis.API_PROVIDERS.items()],
            "feed": settings["feed"],
            "feed_source_subkeys": feed_source_snapshot,
            "tracked": settings["tracked"],
            "agent": settings["agent"],
            "feature_models": settings["feature_models"],
            "collection": settings["collection"],
            "report_schedule": settings["report_schedule"],
            "report_windows": settings["report_windows"],
            "report_window_times": dict(periodic_reports.report_schedule.WINDOW_TIMES),
            "feature_model_catalogs": {feature: model_catalogs.cached_catalog(config["codex"])
                                       for feature, config in settings["feature_models"].items()},
            "news_summary": settings["news_summary"],
            "database": self.database_runtime_snapshot(settings),
            "restart_notice": "翻译 / 总结与 chat 的新请求通常立即生效；涉及 app.py 本版改动，终验前请重启 Flask。",
        }

    def validate_runtime_settings(self, payload: object) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("invalid_payload")
        current_settings = configuration.current_runtime_settings()
        llm = payload.get("llm")
        if not isinstance(llm, dict):
            raise ValueError("invalid_llm_settings")

        translation = llm.get("translation")
        if not isinstance(translation, dict):
            raise ValueError("invalid_llm_settings")

        translation = model_apis.normalize_translation(translation, defaults=current_settings["llm"]["translation"])
        translation["model"] = configuration.normalize_deepseek_model(translation["model"])
        pi_chat = pi_models.normalize_config(llm.get("pi_chat", {}), current_settings["llm"]["pi_chat"])
        if pi_chat != current_settings["llm"]["pi_chat"] and pi_chat.get("reasoning_effort"):
            pi_chat = pi_models.validate_effort(pi_chat)

        feed_payload = payload.get("feed") if isinstance(payload.get("feed"), dict) else None
        hidden_source_subkeys = normalize_hidden_source_subkeys(
            feed_payload.get("hidden_source_subkeys") if feed_payload is not None else current_settings["feed"].get("hidden_source_subkeys")
        )
        agent_payload = payload.get("agent") if isinstance(payload.get("agent"), dict) else {}
        ttl = agent_payload.get("session_ttl_hours", current_settings["agent"]["session_ttl_hours"])
        if ttl not in configuration.AGENT_SESSION_TTL_OPTIONS:
            raise ValueError("invalid_agent_session_ttl")

        runtime = agent_payload.get("runtime", current_settings["agent"].get("runtime", "pi"))
        if not isinstance(runtime, str) or runtime not in {"pi", "codex"}:
            raise ValueError("invalid_agent_runtime")
        codex_config = codex_client.normalize_config(agent_payload.get("codex", current_settings["agent"]["codex"]))
        if codex_config != current_settings["agent"]["codex"]:
            codex_config = model_catalogs.validate_effort(codex_config)
        feature_models = normalize_feature_models(payload.get("feature_models", {}), current_settings)
        for feature, config in feature_models.items():
            if config["codex"] != current_settings["feature_models"][feature]["codex"]:
                config["codex"] = model_catalogs.validate_effort(config["codex"])
            if config["pi"] != current_settings["feature_models"][feature]["pi"] and config["pi"].get("reasoning_effort"):
                config["pi"] = pi_models.validate_effort(config["pi"])

        configured_db_path = self._database_path_from_payload(payload, current_settings)
        if os.environ.get("NEWS_READER_DB_PATH", "").strip():
            # The environment override is authoritative; do not let an explicit
            # settings request pretend it will change the active database.
            current_configured_db_path = str(current_settings.get("database", {}).get("path") or "").strip()
            if configured_db_path != current_configured_db_path:
                raise ValueError("database_path_controlled_by_environment")
            configured_db_path = current_configured_db_path
        elif configured_db_path:
            configured_db_path = self.validate_existing_database_path(configured_db_path)

        if "news_summary" in payload and not isinstance(payload["news_summary"], dict):
            raise ValueError("invalid_summary_limits")
        summary_limits = news_summaries.selection.limits(payload.get("news_summary", current_settings["news_summary"]))
        api_models = payload.get("api_models")
        legacy_classification = api_models.get("jev") if isinstance(api_models, dict) else None
        normalized = {
            "collection": news_collection.normalize_schedule(payload.get("collection", {}), current_settings["collection"]),
            "report_schedule": periodic_reports.report_schedule.normalize(payload.get("report_schedule", {}), current_settings["report_schedule"]),
            "report_windows": periodic_reports.report_schedule.normalize_windows(payload.get("report_windows", {}), current_settings["report_windows"]),
            "feature_models": feature_models,
            "api_models": model_apis.normalize_api_models(payload.get("api_models", current_settings["api_models"]), defaults=current_settings["api_models"]),
            "classification": normalize_classification(payload.get("classification", {}),
                defaults=current_settings["classification"], legacy=legacy_classification),
            "news_summary": summary_limits,
            "llm": {
                "translation": translation,
                "pi_chat": pi_chat,
            },
            "feed": {
                "hidden_source_subkeys": hidden_source_subkeys,
            },
            "tracked": current_settings["tracked"],
            "agent": {
                "session_ttl_hours": int(ttl),
                "runtime": runtime,
                "codex": codex_config,
            },
            "database": {
                "path": configured_db_path,
            },
        }
        if any(normalized[key] != current_settings[key] for key in ("report_schedule", "report_windows")):
            periodic_reports.report_schedule.validate_windows(normalized["report_schedule"], normalized["report_windows"])
        report_changed_at = periodic_reports.now()
        normalized["report_schedule_history"] = periodic_reports.report_schedule.changed_history(
            current_settings, normalized["report_schedule"], report_changed_at)
        normalized["report_window_history"] = periodic_reports.report_schedule.changed_window_history(
            current_settings, normalized["report_windows"], report_changed_at)
        return normalized


def register(app, db, jobs, preferences, store, worker):
    @app.post("/api/settings/x-accounts/test")
    def api_settings_x_account_test():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(ok=False, error="invalid_x_handle"), 400
        try:
            handle = x_accounts.normalize_handle(body.get("handle"))
            revision = body.get("x_accounts_revision")
            def check_context():
                with closing(db.connect()) as conn:
                    x_accounts.check_revision(conn, revision)
                    if conn.execute("SELECT 1 FROM x_accounts WHERE handle=?", (handle,)).fetchone():
                        raise ValueError("duplicate_x_account")
            check_context()
            # Do not hold a database transaction while waiting on the browser.
            result = x_accounts.test_account(handle, news_collection.acquire_source)
            check_context()
            return jsonify(ok=True, **result)
        except ValueError as exc:
            code = str(exc)
            status = 409 if code == "x_accounts_changed" else 400 if code in {"invalid_x_handle", "duplicate_x_account"} else 422
            return jsonify(ok=False, error=code), status
        except Exception:
            app.logger.exception("X account content test failed")
            return jsonify(ok=False, error="x_account_test_failed"), 500

    @app.route("/api/settings/sources/<path:source_key>", methods=["GET", "DELETE"])
    @app.route("/api/settings/x-accounts/<x_handle>", methods=["GET", "DELETE"])
    def api_settings_source(source_key=None, x_handle=None):
        if x_handle is not None:
            try:
                x_handle = x_accounts.normalize_handle(x_handle)
            except ValueError:
                return jsonify(ok=False, error="invalid_x_handle"), 400
            source_key = "x/" + x_handle
        key = normalize_source_subkey_key(source_key)
        if not key or key != source_key:
            return jsonify(ok=False, error="invalid_source_key"), 400
        deleting = request.method == "DELETE"
        body = request.get_json(silent=True) if deleting else None
        if deleting and x_handle and (not isinstance(body, dict) or type(body.get("delete_history")) is not bool):
            return jsonify(ok=False, error="invalid_x_removal"), 400
        if deleting and (not isinstance(body, dict) or not isinstance(body.get("confirmation_token"), str)
                         or not re.fullmatch(r"[0-9a-f]{64}", body["confirmation_token"])):
            return jsonify(ok=False, error="source_confirmation_required"), 400
        staged, committed = None, False
        try:
            store.ensure_agent_db()
            with jobs.report_lock, closing(db.connect()) as conn:
                agent_schema = source_deletion.attach_agent(conn, db.agent_path, db.path)
                conn.execute("BEGIN IMMEDIATE" if deleting else "BEGIN")
                try:
                    empty_source = None
                    if x_handle:
                        account = conn.execute("SELECT * FROM x_accounts WHERE handle=?", (x_handle,)).fetchone()
                        if not account:
                            return jsonify(ok=False, error="x_account_not_found"), 404
                        empty_source = {"label": account["name"] + " (@" + x_handle + ")", "source_label": "X"}
                        if deleting:
                            x_accounts.check_revision(conn, body.get("x_accounts_revision"))
                    plan = source_deletion.Plan(conn, key, database_path=db.path, agent_schema=agent_schema,
                        scope=store.agent_library_scope(), media_root=db.media_dir, runtime_root=db.agent_dir, empty_source=empty_source)
                    if not deleting:
                        return jsonify(ok=True, source=plan.preview, **(x_accounts.snapshot(conn) if x_handle else {}))
                    if body["confirmation_token"] != plan.preview["confirmation_token"]:
                        raise source_deletion.SourceDeletionError("source_data_changed")
                    delete_history = not x_handle or body["delete_history"]
                    if delete_history:
                        staged = source_deletion.StagedFiles(plan)
                        staged.stage()
                        plan.delete_rows()
                    if x_handle:
                        x_accounts.remove(conn, x_handle, delete_history=delete_history)
                    accounts = x_accounts.snapshot(conn) if x_handle else {}
                    remaining_sources = preferences.build_feed_source_settings_snapshot(
                        configuration.current_runtime_settings()["feed"].get("hidden_source_subkeys") or [], conn=conn)
                    conn.commit()
                    committed = True
                except Exception:
                    conn.rollback()
                    raise
            for job_id in (plan.process_ids if delete_history else []):
                worker._terminate_agent_process(job_id)
            warning = ""
            try:
                if staged:
                    staged.cleanup()
            except OSError:
                app.logger.exception("Source data deleted but staged cache cleanup failed")
                warning = "新闻数据已删除，部分隔离缓存未能清理，请检查缓存目录的写入权限。"
            jobs.collection.wake.set()
            return jsonify(ok=True, deleted=plan.preview if delete_history else None,
                           deleted_item_ids=plan.item_ids if delete_history else [], warning=warning,
                           feed_source_subkeys=remaining_sources, **accounts)
        except ValueError as exc:
            return jsonify(ok=False, error=str(exc)), 409
        except source_deletion.SourceDeletionError as exc:
            return jsonify(ok=False, error=exc.code), exc.status
        except (OSError, sqlite3.Error):
            app.logger.exception("Source deletion failed")
            return jsonify(ok=False, error="source_delete_failed"), 500
        finally:
            if staged and not committed:
                staged.restore()

    @app.post("/api/settings/apis/<provider>/models")
    @app.post("/api/settings/translation/models")
    def api_translation_models(provider: str = "deepseek"):
        provider = model_apis.canonical_provider(provider)
        if provider not in model_apis.API_PROVIDERS:
            return jsonify(ok=False, error="unsupported_provider"), 400
        snapshot = managed_api_snapshot(provider, refresh=True)
        if not snapshot["service"]["configured"]:
            return jsonify(ok=False, error="missing_api_key"), 400
        service = snapshot["service"]
        if service.get("models_endpoint_reachable") is not True:
            details = {name: service[name] for name in ("reason", "upstream_status", "retry_after_seconds") if name in service}
            status = 504 if details.get("reason") == "connection_timeout" else 502
            return jsonify(ok=False, error="models_unavailable", message=service["status_text"], **details), status
        return jsonify(ok=True, **snapshot)

    @app.post("/api/settings/apis/<provider>/test")
    @app.post("/api/settings/translation/test")
    def api_translation_test(provider: str = "deepseek"):
        provider = model_apis.canonical_provider(provider)
        if provider not in model_apis.API_PROVIDERS:
            return jsonify(ok=False, error="unsupported_provider"), 400
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(ok=False, error="invalid_payload"), 400
        config = model_apis.API_PROVIDERS[provider]
        model = body.get("model", "")
        if not isinstance(model, str) or len(model.strip()) > 120 or any(ord(c) < 32 for c in model):
            return jsonify(ok=False, error="invalid_model"), 400
        model = model.strip() or (resolve_translation_default_model() if provider == "deepseek" else config["default_model"])
        key = provider_secret_value(provider)
        if not key:
            return jsonify(ok=False, error="missing_api_key"), 400
        if not model:
            return jsonify(ok=False, error="invalid_model", message="请先选择或填写测试模型。"), 400
        try:
            probe_body = model_apis.probe_payload(provider, model)
        except ValueError as exc:
            return jsonify(ok=False, error=str(exc)), 400
        probe = Request(config["test_url"],
                        data=json.dumps(probe_body).encode(),
                        headers={**model_apis.request_headers(key, provider), "Content-Type": "application/json"}, method="POST")
        started = time.monotonic()
        try:
            with urlopen(probe, timeout=30) as response:
                result = json.loads(response.read().decode())
            if not model_apis.valid_probe_response(provider, result):
                return jsonify(ok=False, error="empty_model_response"), 502
            return jsonify(ok=True, message="模型连接成功", provider=provider, model=model, elapsed_ms=round((time.monotonic()-started)*1000))
        except HTTPError as exc:
            details = model_apis.http_error_details(exc)
            details["message"] = f"模型测试失败（HTTP {exc.code}）。{details['message']}"
            return jsonify(ok=False, error="api_test_failed", **details), 502
        except TimeoutError:
            return jsonify(ok=False, error="api_test_timeout", message="模型连接测试超时。"), 504
        except Exception:
            return jsonify(ok=False, error="api_test_failed", message="模型连接测试失败。"), 502

    @app.post("/api/settings/models")
    def api_settings_models():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(ok=False, error="invalid_payload"), 400
        if body.get("runtime") == "codex":
            try:
                config = codex_client.normalize_config(body.get("codex", {}))
            except ValueError as exc:
                return jsonify(ok=False, error=str(exc)), 400
            return jsonify(ok=True, catalog=model_catalogs.safe_catalog(config))
        if body.get("runtime") == "pi":
            return jsonify(ok=True, catalog=pi_chat_settings_snapshot("", "")["catalog"])
        return jsonify(ok=False, error="invalid_agent_runtime"), 400

    @app.post("/api/settings/connection-test")
    def api_settings_connection_test():
        body = request.get_json(silent=True)
        try:
            if isinstance(body, dict) and body.get("runtime") == "codex":
                body = {**body, "codex": model_catalogs.validate_effort(body.get("codex", {}))}
            result = connection_tests.test_connection(body)
            return jsonify(ok=True, **result)
        except ValueError as exc:
            return jsonify(ok=False, error=str(exc)), 400
        except connection_tests.Busy:
            return jsonify(ok=False, error="connection_test_busy"), 409

    @app.get("/api/settings")
    def api_settings():
        return jsonify({"ok": True, **preferences.serialize_runtime_settings()})

    @app.post("/api/settings/database/pick")
    def api_settings_database_pick():
        if os.environ.get("NEWS_READER_DB_PATH", "").strip():
            return jsonify({"ok": False, "error": "database_path_controlled_by_environment"}), 400
        try:
            path = preferences.choose_existing_database_path()
        except ValueError as exc:
            error = str(exc)
            status = 409 if error == "database_picker_cancelled" else 501 if error == "database_picker_unavailable" else 400
            return jsonify({"ok": False, "error": error}), status
        except Exception:
            app.logger.exception("database picker failed unexpectedly")
            return jsonify({"ok": False, "error": "database_picker_failed"}), 500
        return jsonify({"ok": True, "path": path})

    @app.put("/api/settings")
    def api_settings_update():
        body = request.get_json(silent=True) or {}
        try:
            normalized = preferences.validate_runtime_settings(body)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        # Account revisions protect drafts from concurrent edits/removals and bind
        # them to the library. The legacy settings file never owns subscriptions.
        try:
            with jobs.summaries.lock, jobs.report_lock, jobs.classification.lock, closing(db.connect()) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                if "x_accounts" in body:
                    x_accounts.save(conn, body["x_accounts"], body.get("x_accounts_revision"))
                save_app_settings(normalized)
                jobs.classification.configure(**normalized["classification"])
        except ValueError as exc:
            return jsonify(ok=False, error=str(exc)), 409 if str(exc) == "x_accounts_changed" else 400
        jobs.reports.schedule_wake.set()
        jobs.collection.wake.set()
        store.refresh_agent_session_expiries()
        return jsonify({"ok": True, **preferences.serialize_runtime_settings()})

    @app.put("/api/settings/tracked-default-rule-params")
    def api_settings_tracked_default_rule_params_update():
        body = request.get_json(silent=True) or {}
        params = body.get("default_rule_params")
        if not isinstance(params, dict):
            return jsonify({"ok": False, "error": "invalid_default_rule_params"}), 400
        settings = configuration.current_runtime_settings()
        settings["tracked"]["default_rule_params"] = tracked_rules.tracked_default_rule_params(params)
        save_app_settings(settings)
        return jsonify({"ok": True, **preferences.serialize_runtime_settings()})

    @app.put("/api/settings/secrets/<provider>")
    def api_settings_secret_save(provider: str):
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(ok=False, error="invalid_payload"), 400
        try:
            save_provider_secret(provider, body.get("key"))
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except SecretStoreError as exc:
            status = 503 if exc.code == "keychain_unavailable" else 500
            return jsonify({"ok": False, "error": exc.code}), status
        return jsonify({"ok": True, **preferences.serialize_runtime_settings()})

    @app.delete("/api/settings/secrets/<provider>")
    def api_settings_secret_delete(provider: str):
        try:
            remove_provider_secret(provider)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except SecretStoreError as exc:
            status = 503 if exc.code == "keychain_unavailable" else 500
            return jsonify({"ok": False, "error": exc.code}), status
        return jsonify({"ok": True, **preferences.serialize_runtime_settings()})
