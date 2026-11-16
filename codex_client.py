from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid

import oar_client

ROOT = Path(__file__).resolve().parent
REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
ERROR_MESSAGES = {
    "codex_missing": "未找到 Codex CLI，请先安装 Codex。",
    "codex_auth_required": "Codex 请求认证失败，请检查本机 Codex 登录和配置；应用不收集凭据。",
    "codex_advanced_config_removed": "Codex 自定义服务商和 Profile 已停用，请从模型列表重新选择；旧会话请开启新对话。",
    "codex_probe_failed": "Codex 状态探测或命令能力检查失败；这不代表未登录，请检查可执行文件和配置。",
    "codex_protocol_error": "Codex 返回不完整或格式异常，本轮未成功，请重试。",
    "codex_empty_answer": "Codex 未返回有效最终答案，请重试。",
    "codex_turn_failed": "Codex 未完成本轮请求，请检查模型、网络和登录后重试。",
    "codex_runtime_failed": "Codex 运行失败，请在终端检查所选模型或配置。",
    "codex_session_invalid": "Codex 会话不可用或与当前运行方式不匹配，请明确开启新会话。",
    "codex_timeout": "Codex 请求超时，请重试。",
}
ERROR_MESSAGES.update({key: value for key, value in oar_client.ERROR_MESSAGES.items()
                       if key in {"oar_node_missing", "oar_node_version", "oar_dependency_missing", "oar_compatibility_mismatch"}})


def oar_environment() -> dict:
    # Keep Codex's own HOME/config/auth. Only the selected executable is pinned.
    return {**os.environ, "NEWS_READER_CODEX": executable()}


def oar_request(config: dict, prompt: str, *, state_dir: Path, thread_id="", resume_prompt=None,
                cwd: Path = ROOT, live_search=False) -> dict:
    config = normalize_config(config)
    if thread_id and not valid_thread_id(thread_id):
        raise RuntimeError("codex_session_invalid")
    return {"runtime": "codex", "codex": config, "cwd": str(cwd.resolve()), "state_dir": str(state_dir),
            "prompt": prompt, "resume_prompt": prompt if resume_prompt is None else resume_prompt,
            "resume": thread_id, "config_id": uuid.uuid4().hex,
            **({"web_search": "live"} if live_search else {})}


class OarEventParser:
    """Validate native identity without parsing Codex's vendor-specific stream."""
    def __init__(self, expected_thread=""):
        self.expected_thread = expected_thread
        self.thread_id = ""
        self.terminal = False

    def parse(self, raw):
        try:
            event = oar_client.parse_event(raw)
        except RuntimeError:
            raise RuntimeError("codex_protocol_error") from None
        if self.terminal:
            raise RuntimeError("codex_protocol_error")
        if event["type"] == "oar_session":
            native = event.get("id", "")
            if (self.thread_id or event.get("runtime") != "oar-codex" or not valid_thread_id(native)
                    or (self.expected_thread and native != self.expected_thread)):
                raise RuntimeError("codex_session_invalid")
            self.thread_id = native
        if event["type"] == "oar_result":
            self.terminal = True
            if event["outcome"] == "completed" and (not self.thread_id or event.get("native_id") != self.thread_id):
                raise RuntimeError("codex_protocol_error")
        return event


def run_chat(config: dict, prompt: str, *, state_dir: Path, thread_id="", timeout=90) -> tuple[str, str]:
    request = oar_request(config, prompt, state_dir=state_dir, thread_id=thread_id, live_search=True)
    return _run_oar(request, state_dir, timeout, thread_id)


def run_archive(config: dict, prompt: str, *, timeout=300, max_length=300) -> tuple[str, str]:
    """Independent OAR turn; never resumes or persists a native chat thread."""
    with tempfile.TemporaryDirectory(prefix="newsreader-codex-archive-") as directory:
        state_dir = Path(directory)
        request = oar_request(config, prompt, state_dir=state_dir)
        request.update(task="chat_archive", web_search="disabled", archive_max_length=max_length)
        return _run_oar(request, state_dir, timeout)


def _run_oar(request: dict, state_dir: Path, timeout: int, thread_id="") -> tuple[str, str]:
    parser = OarEventParser(thread_id)
    result = oar_client.run_result(oar_client.bridge_command(state_dir.name), request, oar_environment(), timeout,
        parser=parser.parse, errors=ERROR_MESSAGES, timeout_error="codex_timeout", protocol_error="codex_protocol_error",
        failure_error="codex_turn_failed")
    return result["answer"], parser.thread_id


def normalize_config(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("invalid_codex_settings")
    # Accept old empty fields when reading saved sessions; never execute a
    # formerly selected provider/profile under a different native configuration.
    if any(value.get(key, "") != "" for key in ("provider", "profile")):
        raise ValueError("codex_advanced_config_removed")
    result = {}
    for key, limit in (("model", 120),):
        raw = value.get(key, "")
        if not isinstance(raw, str) or len(raw) > limit or (raw and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", raw)):
            raise ValueError(f"invalid_codex_{key}")
        result[key] = raw
    effort = value.get("reasoning_effort", "")
    if not isinstance(effort, str) or (effort and effort not in REASONING_EFFORTS):
        raise ValueError("invalid_codex_reasoning_effort")
    # Do not add empty fields to old session mappings: their byte compatibility matters.
    if effort:
        result["reasoning_effort"] = effort
    # News Chat does not need write access.
    if value.get("sandbox", "read-only") != "read-only":
        raise ValueError("invalid_codex_sandbox")
    result["sandbox"] = "read-only"
    return result


def saved_preferences(value: object) -> dict:
    """Remove retired options from editable defaults, never from bound tasks."""
    if not isinstance(value, dict):
        raise ValueError("invalid_codex_settings")
    return normalize_config({key: item for key, item in value.items() if key not in ("provider", "profile")})


def executable() -> str:
    found = shutil.which(os.environ.get("NEWS_READER_CODEX") or "codex")
    if not found:
        raise RuntimeError("codex_missing")
    return found


def status_snapshot() -> dict:
    try:
        command = executable()
    except RuntimeError:
        return {"available": False, "authenticated": False, "login_status": "unknown", "error": "codex_missing"}
    try:
        result = subprocess.run([command, "login", "status"], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=5, text=True)
        text = (result.stdout + result.stderr).lower()
        if result.returncode == 0 and "logged in" in text and "not logged in" not in text:
            return {"available": True, "authenticated": True, "login_status": "logged_in", "error": ""}
        if "not logged in" in text:
            return {"available": True, "authenticated": False, "login_status": "not_logged_in", "error": ""}
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        pass
    return {"available": True, "authenticated": None, "login_status": "unknown", "error": "codex_probe_failed"}


def require_ready() -> None:
    try:
        probe = subprocess.run([executable(), "app-server", "--help"], stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, timeout=5, text=True)
        if probe.returncode != 0 or not all(flag in probe.stdout for flag in ("--listen",)):
            raise RuntimeError("codex_probe_failed")
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        raise RuntimeError("codex_probe_failed") from None


def valid_thread_id(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, AttributeError, TypeError):
        return False


def toml_value(value):
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(k) + "=" + toml_value(v) for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ",".join(toml_value(v) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False)


def isolated_launch_args(config: dict, bridge=None) -> list[str]:
    config = normalize_config(config)
    command = [executable(), "mcp", "list", "--json"]
    try:
        response = subprocess.run(command, capture_output=True, timeout=20, check=True)
        servers = json.loads(response.stdout)
        if not isinstance(servers, list) or not all(isinstance(s, dict) and isinstance(s.get("name"), str) for s in servers):
            raise ValueError()
    except (ValueError, OSError, subprocess.SubprocessError):
        raise RuntimeError("codex_probe_failed") from None
    restrictions = ["-c", 'web_search="disabled"', "-c", "skills.include_instructions=false",
                    "-c", "skills.bundled.enabled=false"]
    entries = {server["name"]: {"enabled": False, "command": "false"} for server in servers}
    if bridge:
        entries["newsreader_report"] = bridge.config()
        restrictions += ["--enable", "multi_agent", "-c", "agents.enabled=true",
                         "-c", "agents.max_concurrent_threads_per_session=3",
                         "-c", "skills.include_instructions=false", "-c", "skills.bundled.enabled=false"]
        for source, target in (("model", "default_subagent_model"), ("reasoning_effort", "default_subagent_reasoning_effort")):
            if config.get(source):
                restrictions += ["-c", "agents." + target + "=" + toml_value(config[source])]
        if not bridge.workspace.delegation:
            restrictions += ["--disable", "multi_agent", "-c", "agents.enabled=false"]
    else:
        restrictions += ["--disable", "multi_agent"]
    restrictions += ["-c", "mcp_servers=" + toml_value(entries)]
    for feature in ("shell_tool", "apps", "plugins", "browser_use", "computer_use", "image_generation", "view_image", "hooks", "memories", "goals"):
        restrictions += ["--disable", feature]
    return restrictions


def saved_config(state_dir: Path) -> dict:
    """Read only our safe session mapping, never the native credential/config files."""
    try:
        record = json.loads((state_dir / "codex-session.json").read_text())
        if record["runtime"] != "codex" or not valid_thread_id(record["thread_id"]):
            raise ValueError()
        return normalize_config(record["config"])
    except (OSError, ValueError, TypeError, KeyError):
        raise RuntimeError("codex_session_invalid") from None


def read_thread(state_dir: Path, config: dict) -> str:
    path = state_dir / "codex-session.json"
    if not path.exists():
        return ""
    try:
        record = json.loads(path.read_text())
        if record["runtime"] != "codex" or normalize_config(record["config"]) != normalize_config(config) or not valid_thread_id(record["thread_id"]):
            raise ValueError()
        return record["thread_id"]
    except (ValueError, KeyError, TypeError, OSError):
        raise RuntimeError("codex_session_invalid") from None


def write_thread(state_dir: Path, config: dict, thread_id: str) -> None:
    if not valid_thread_id(thread_id):
        raise RuntimeError("codex_session_invalid")
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = state_dir / f".codex-session-{uuid.uuid4().hex}.tmp"
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as out:
            json.dump({"runtime": "codex", "config": normalize_config(config), "thread_id": thread_id}, out)
        os.replace(temp, state_dir / "codex-session.json")
    finally:
        temp.unlink(missing_ok=True)


def run_once(config, prompt, *, thread_id="", timeout=90, ephemeral=False):
    require_ready()
    if ephemeral and thread_id:
        raise RuntimeError("codex_session_invalid")
    with tempfile.TemporaryDirectory(prefix="newsreader-codex-once-") as directory:
        state = Path(directory)
        request = oar_request(config, prompt, state_dir=state, thread_id=thread_id,
                              cwd=state if ephemeral else ROOT)
        if ephemeral:
            request.update(task="material_summary", web_search="disabled")
        return _run_oar(request, state, timeout, thread_id)
