"""Pi model choices, fresh credential-aware catalogs, and thinking capabilities."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

from llm_client import _pi_subprocess_env
from model_apis import is_hidden_model

THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")
ROOT = Path(__file__).resolve().parent


def normalize_config(value: object, defaults: dict | None = None) -> dict:
    if not isinstance(value, dict):
        raise ValueError("invalid_pi_settings")
    defaults = defaults or {}
    result = {}
    for key, limit in (("provider", 40), ("model", 120)):
        text = value.get(key, defaults.get(key, ""))
        if not isinstance(text, str) or len(text.strip()) > limit or any(ord(c) < 32 for c in text):
            raise ValueError(f"invalid_pi_chat_{key}")
        result[key] = text.strip()
    same_model = all(result[key] == defaults.get(key, "") for key in ("provider", "model"))
    effort = value.get("reasoning_effort", defaults.get("reasoning_effort", "") if same_model else "")
    if not isinstance(effort, str) or effort not in ("", *THINKING_LEVELS):
        raise ValueError("invalid_pi_reasoning_effort")
    if effort:
        result["reasoning_effort"] = effort
    return result


def command(*, root: Path = ROOT) -> list[str]:
    node = shutil.which(os.environ.get("NEWS_READER_NODE") or "node")
    if not node:
        raise RuntimeError("report_model_dependencies_missing")
    return [node, str(root / "scripts/report_model.mjs")]


def metadata(request: dict, *, argv: list[str] | None = None) -> dict:
    try:
        result = subprocess.run(argv or command(), input=json.dumps(request), text=True,
                                capture_output=True, timeout=20, check=False, env=_pi_subprocess_env())
        data = json.loads(result.stdout)
        if not isinstance(data, dict):
            raise ValueError()
        if result.returncode:
            if data.get("error") == "report_model_dependencies_missing":
                raise RuntimeError("report_model_dependencies_missing")
            raise ValueError()
        return data
    except (OSError, ValueError, subprocess.SubprocessError):
        raise RuntimeError("report_model_catalog_failed") from None


def filter_catalog(result: dict) -> dict:
    options = [option for option in result.get("options", [])
               if not is_hidden_model(option.get("provider", ""), option.get("value", ""), option)]
    return {**result, "options": options,
            "notice": result.get("notice", "") or ("" if options else "暂无可用模型，请先登录 Pi 或配置 API 密钥。")}


def catalog() -> dict:
    # A new SDK runtime reads current auth/env every time, including after logout.
    return filter_catalog(metadata({"action": "catalog"}))


def validate_effort(config: dict) -> dict:
    config = normalize_config(config)
    if config.get("reasoning_effort"):
        if not config["provider"] or not config["model"]:
            raise ValueError("pi_model_not_selected")
        try:
            supported = metadata({"action": "metadata", "provider": config["provider"], "model": config["model"]})
        except RuntimeError:
            raise ValueError("pi_model_catalog_unavailable") from None
        if config["reasoning_effort"] not in supported.get("reasoning_efforts", []):
            raise ValueError("invalid_pi_reasoning_effort")
    return config
