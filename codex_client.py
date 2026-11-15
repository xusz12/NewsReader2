"""Codex OAR adapter and remaining task-specific native execution.

Normalized events match the existing Agent transport, not Pi session identifiers.
Commands always pin cwd, read-only sandbox and noninteractive approvals, including
resume. Service tier is always Standard, independently of native Fast settings.
No bypass/add-dir flags, credential/config copies or automatic fallback.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import tempfile
import time
import uuid

import oar_client
from oar_client import terminate_group

ROOT = Path(__file__).resolve().parent
REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
MAX_BYTES = 2 * 1024 * 1024
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
    """Public OpenAI login hint only: custom/local providers may not need it.

    Never read native config/auth files and never use this hint as an exec gate.
    Exec inherits the same binary selection, HOME/CODEX_HOME and native config.
    """
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
    # Login status only covers OpenAI auth, not native custom provider credentials.
    # Only absence/incompatible CLI blocks launch; real request errors decide auth.
    try:
        probe = subprocess.run([executable(), "exec", "--help"], stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, timeout=5, text=True)
        if probe.returncode != 0 or not all(flag in probe.stdout for flag in ("--json", "--sandbox", "--cd", "resume")):
            raise RuntimeError("codex_probe_failed")
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        raise RuntimeError("codex_probe_failed") from None


def valid_thread_id(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, AttributeError, TypeError):
        return False


def command(config: dict, *, thread_id: str = "", cwd: Path = ROOT, ephemeral: bool = False, live_search: bool = False) -> list[str]:
    config = normalize_config(config)
    if thread_id and not valid_thread_id(thread_id):
        raise RuntimeError("codex_session_invalid")
    args = [executable(), "exec", "--cd", str(cwd.resolve()), "--sandbox", "read-only",
            "-c", 'approval_policy="never"', "-c", 'sandbox_mode="read-only"',
            "--json", "--color", "never"]
    if config["model"]:
        args += ["--model", config["model"]]
    if config.get("reasoning_effort"):
        args += ["-c", "model_reasoning_effort=" + json.dumps(config["reasoning_effort"])]
    # Apply at execution, including resumed sessions and already queued jobs.
    # Do not change serialized model configs or the user's native CLI settings.
    args += ["-c", 'service_tier="default"', "-c", "features.fast_mode=false"]
    if ephemeral:
        args.append("--ephemeral")
    if live_search:
        args += ["-c", 'web_search="live"']
    if thread_id:
        args += ["resume", thread_id, "-"]
    else:
        args.append("-")
    return args


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


class EventParser:
    """One turn only; completed agent_message is authoritative, never deltas.

    Tool/diagnostic contents are discarded. Unknown events fail closed instead
    of treating partial output as a successful response.
    """
    def __init__(self, expected_thread: str = "", *, allow_delegation=False):
        self.expected_thread = expected_thread
        self.thread_id = ""
        self.started = False
        self.terminal = False
        self.completed_items: set[str] = set()
        self.answer = ""
        self.stream_text = ""
        self.failure_code = "codex_turn_failed"
        self.saw_error = False
        self.allow_delegation = allow_delegation

    def parse(self, raw: str) -> dict:
        try:
            event = json.loads(raw)
            if not isinstance(event, dict) or self.terminal:
                raise ValueError()
            kind = event.get("type")
            # A report's final answer belongs to the coordinator. Native CLI
            # versions that forward child events must not let them complete it.
            if (self.allow_delegation and self.thread_id and event.get("thread_id")
                    and event["thread_id"] != self.thread_id):
                return {"type": "oar_tool"}
            if kind == "thread.started":
                value = event.get("thread_id")
                if self.thread_id or not valid_thread_id(value) or (self.expected_thread and value != self.expected_thread):
                    raise ValueError()
                self.thread_id = value
                return {"type": "oar_session"}
            if kind == "turn.started":
                if not self.thread_id or self.started:
                    raise ValueError()
                self.started = True
                return {"type": "oar_session"}
            if kind == "error":
                # Never forward diagnostics; retain only a whitelisted category.
                self.saw_error = True
                message = str(event.get("message") or "").lower()
                if any(word in message for word in ("not logged in", "unauthorized", "401", "authentication")):
                    self.failure_code = "codex_auth_required"
                return {"type": "oar_tool"}
            if kind == "turn.failed":
                diagnostic = event.get("error")
                message = str(diagnostic.get("message") or "").lower() if isinstance(diagnostic, dict) else ""
                if any(word in message for word in ("not logged in", "unauthorized", "401", "authentication")):
                    self.failure_code = "codex_auth_required"
                self.terminal = True
                return {"type": "oar_result", "outcome": "failed", "error": self.failure_code}
            if kind in {"item.started", "item.updated", "item.completed"}:
                item = event.get("item")
                if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] or item.get("type") not in {
                    "agent_message", "reasoning", "command_execution", "file_change", "mcp_tool_call",
                    "web_search", "todo_list", "error", "tool_call", "image_generation",
                } | ({"collab_tool_call"} if self.allow_delegation else set()):
                    raise ValueError()
                if not self.started:
                    # CLI 0.158.0 can emit completed startup diagnostics between
                    # thread.started and turn.started. These are not top-level
                    # errors or answers; admit only this confirmed item shape.
                    message = item.get("message")
                    if (not self.thread_id or kind != "item.completed" or item["type"] != "error"
                            or not isinstance(message, str) or not message.strip()
                            or len(message.encode()) > MAX_BYTES):
                        raise ValueError()
                item_id = item["id"]
                if item_id in self.completed_items:
                    raise ValueError()
                if kind == "item.completed":
                    self.completed_items.add(item_id)
                if item["type"] == "agent_message" and kind in {"item.updated", "item.completed"}:
                    text = item.get("text")
                    if not isinstance(text, str) or len(text.encode()) > MAX_BYTES:
                        raise ValueError()
                    if kind == "item.completed" and item.get("phase") != "commentary":
                        # Only authoritative completion can fill the final answer.
                        self.answer = text
                    self.stream_text = text
                    return {"type": "text_delta", "delta": text, "replace": True}
                return {"type": "oar_tool"}
            if not self.started:
                raise ValueError()
            if kind == "turn.completed":
                self.terminal = True
                if self.saw_error:
                    return {"type": "oar_result", "outcome": "failed", "error": self.failure_code}
                if not self.answer.strip():
                    return {"type": "oar_result", "outcome": "failed", "error": "codex_empty_answer"}
                return {"type": "oar_result", "outcome": "completed", "answer": self.answer}
            raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise RuntimeError("codex_protocol_error") from None


def run_once(config: dict, prompt: str, *, thread_id: str = "", timeout: int = 90, ephemeral: bool = False, finish_on_completion: bool = False) -> tuple[str, str]:
    require_ready()
    parser = EventParser(thread_id)
    proc = None
    selector = selectors.DefaultSelector()
    terminal = None
    completion_deadline = None
    try:
        proc = subprocess.Popen(command(config, thread_id=thread_id, ephemeral=ephemeral),
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=ROOT, start_new_session=True, bufsize=0)
        pending = prompt.encode("utf-8")
        if len(pending) > MAX_BYTES:
            raise RuntimeError("codex_protocol_error")
        os.set_blocking(proc.stdin.fileno(), False)
        selector.register(proc.stdin, selectors.EVENT_WRITE, "stdin")
        selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
        selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
        buffer = b""
        deadline = time.monotonic() + timeout
        while selector.get_map():
            # Connection probes measure the turn, not native CLI/plugin teardown.
            # Drain immediately queued output briefly to catch failures/duplicates,
            # then finally reaps this invocation's entire process group.
            if finish_on_completion and completion_deadline is not None and time.monotonic() >= completion_deadline:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("codex_timeout")
            for key, _ in selector.select(0.1):
                if key.data == "stdin":
                    try:
                        n = os.write(key.fileobj.fileno(), pending[:4096])
                        pending = pending[n:]
                    except BrokenPipeError:
                        pending = b""
                    if not pending:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                    continue
                chunk = os.read(key.fileobj.fileno(), 4096)
                if not chunk:
                    selector.unregister(key.fileobj)
                    if key.data == "stdout" and buffer.strip():
                        event = parser.parse(buffer.decode())
                        if event["type"] == "oar_result":
                            terminal = event
                            if finish_on_completion:
                                completion_deadline = min(deadline, time.monotonic() + 0.25)
                        buffer = b""
                elif key.data == "stdout":
                    buffer += chunk
                    if len(buffer) > MAX_BYTES:
                        raise RuntimeError("codex_protocol_error")
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        if line.strip():
                            event = parser.parse(line.decode())
                            if event["type"] == "oar_result":
                                terminal = event
                                if finish_on_completion:
                                    completion_deadline = min(deadline, time.monotonic() + 0.25)
        if finish_on_completion and terminal is not None:
            if buffer.strip():
                # No trailing malformed/partial payload may hide behind a completed turn.
                parser.parse(buffer.decode())
            if terminal.get("outcome") != "completed":
                raise RuntimeError(terminal["error"])
            # A known nonzero exit is still a failure. A still-running process is
            # deliberately terminated in finally after the authoritative turn result.
            if not selector.get_map() and proc.poll() is None:
                try:
                    proc.wait(timeout=min(0.25, max(0.01, deadline - time.monotonic())))
                except subprocess.TimeoutExpired:
                    pass
            code = proc.poll()
            if code is not None and code != 0:
                raise RuntimeError("codex_runtime_failed")
            return terminal["answer"], parser.thread_id
        proc.wait(timeout=max(0.01, deadline - time.monotonic()))
        if terminal is None:
            raise RuntimeError("codex_protocol_error")
        if terminal["outcome"] != "completed":
            raise RuntimeError(terminal["error"])
        if proc.returncode != 0:
            raise RuntimeError("codex_runtime_failed")
        return terminal["answer"], parser.thread_id
    except (OSError, UnicodeError):
        raise RuntimeError("codex_runtime_failed") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError("codex_timeout") from None
    finally:
        selector.close()
        if proc:
            terminate_group(proc)
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                if pipe and not pipe.closed:
                    pipe.close()
