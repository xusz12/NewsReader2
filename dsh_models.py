import json
import os
import re
import subprocess
import tempfile
import threading
import time

import pi_models
import oar_client
from llm_client import _pi_subprocess_env

_lock = threading.Lock()
_cache = {}


def normalize_config(value, defaults=None):
    if not isinstance(value, dict):
        raise ValueError("invalid_dsh_settings")
    defaults = defaults or {}
    result = pi_models.normalize_config({**value, "reasoning_effort": ""}, defaults)
    same = all(result[k] == defaults.get(k, "") for k in ("provider", "model"))
    effort = value.get("reasoning_effort", defaults.get("reasoning_effort", "") if same else "")
    if not isinstance(effort, str) or (effort and not re.fullmatch(r"[a-zA-Z0-9_-]{1,32}", effort)):
        raise ValueError("invalid_dsh_reasoning_effort")
    if effort:
        result["reasoning_effort"] = effort
    return result


def _query(request):
    try:
        command = pi_models.command()[:1] + [str(pi_models.ROOT / "scripts/dsh_runtime.mjs")]
        with tempfile.TemporaryDirectory(prefix="nr-dsh-catalog-") as directory:
            with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  text=True, start_new_session=True, env=_pi_subprocess_env()) as proc:
                try:
                    output, _ = proc.communicate(json.dumps({**request, "temp_dir": directory}), timeout=45)
                finally:
                    if proc.poll() is None:
                        oar_client.terminate_group(proc)
                result = json.loads(output)
                if not isinstance(result, dict) or proc.returncode:
                    raise ValueError()
                return result
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError):
        return {"error": "oar_dsh_incompatible", "notice": "读取 DSH 模型目录失败，请检查本机 DSH 的版本与服务商配置。", "options": []}


def catalog(*, refresh=False):
    identity = tuple(os.environ.get(k, "") for k in ("DSH_HOME", "OAR_DSH_BIN", "PATH"))
    with _lock:
        if refresh or _cache.get("identity") != identity or time.monotonic() >= _cache.get("expires", 0):
            data = _query({"action": "catalog"})
            _cache.update(identity=identity, expires=time.monotonic() + (8 if data.get("error") else 120), data=data)
        return _cache["data"]


def metadata(request):
    result = _query({"action": "metadata", "provider": request["provider"], "model": request["model"]})
    if result.get("error"):
        raise RuntimeError(result["error"])
    return result


def validate_effort(config):
    config = normalize_config(config)
    if config.get("reasoning_effort"):
        row = next((r for r in catalog()["options"] if (r["provider"], r["value"]) == (config["provider"], config["model"])), {})
        if config["reasoning_effort"] not in row.get("reasoning_efforts", []):
            raise ValueError("invalid_dsh_reasoning_effort")
    return config
