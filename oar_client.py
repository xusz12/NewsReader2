"""Private OAR bridge protocol. No provider credentials or prompts in argv."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import signal
import selectors
import time
import uuid

ROOT = Path(__file__).resolve().parent
ERROR_MESSAGES = {
    "oar_resources_changed": "此 OAR 会话需要新的 Pi 原生资源，请明确开启新会话使用兼容模式；不会把旧会话 ID 交给另一运行方式。",
    "oar_trust_changed": "此 OAR 会话的项目信任已改变，请检查 Pi 信任设置或明确开启新会话；不会自动扩大权限或换成另一执行方式。",
    "oar_project_untrusted": "Pi 原有规则未信任此项目，OAR 已暂停以免自动扩大权限；请先在 Pi 检查项目信任。",
    "oar_extension_failed": "Pi 扩展或资源加载失败；为避免丢失现有能力，任务已暂停，请检查 Pi 配置。",
    "oar_node_missing": "未发现 Node.js，请安装 Node.js 24 或更高版本。",
    "oar_node_version": "OAR 需要 Node.js 24 或更高版本。",
    "oar_dependency_missing": "OAR 依赖缺失，请在项目目录执行 npm ci --ignore-scripts。",
    "oar_auth_required": "当前 Pi 模型未登录或没有可用凭据，请在 Pi 中检查登录，或选择已可用的模型。",
    "oar_runtime_failed": "OAR 运行失败，请检查本机 Pi 模型与扩展配置。",
    "oar_turn_failed": "Agent 未完整完成本轮请求，请检查模型可用性后重试。",
    "oar_legacy_session_missing": "旧 Pi 会话文件已不可用；旧后台聊天仍保留，请在聊天面板接续或明确开启新会话。",
    "oar_empty_answer": "Agent 未返回有效答案，请重试。",
    "oar_model_unavailable": "所选 Pi 模型不存在，请检查模型配置。",
    "oar_protocol_error": "Agent 返回不完整，未收到有效的完成结果，请重试。",
}


def bridge_command(session_id: str) -> list[str]:
    node = shutil.which(os.environ.get("NEWS_READER_NODE") or "node")
    if not node:
        raise RuntimeError("oar_node_missing")
    return [node, str(ROOT / "scripts/oar_bridge.mjs"), "--session-id", session_id]


def bridge_request(*, prompt: str, resume_prompt: str, state_dir: Path, provider: str, model: str) -> dict:
    return {"prompt": prompt, "resume_prompt": resume_prompt, "state_dir": str(state_dir),
            "cwd": str(ROOT), "provider": provider, "model": model,
            "config_id": uuid.uuid4().hex}


def parse_event(raw: str) -> dict:
    try:
        event = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("oar_protocol_error") from exc
    if not isinstance(event, dict) or event.get("type") not in {"text_delta", "oar_session", "oar_tool", "oar_result"}:
        raise RuntimeError("oar_protocol_error")
    if event["type"] == "text_delta" and not isinstance(event.get("delta"), str):
        raise RuntimeError("oar_protocol_error")
    if event["type"] == "oar_result" and (event.get("outcome") not in {"completed", "failed"} or
       (event["outcome"] == "completed" and not isinstance(event.get("answer"), str))):
        raise RuntimeError("oar_protocol_error")
    return event


def cleanup_config(state_dir: Path, config_id: str) -> None:
    """Remove only this invocation's copies, including after a Node SIGKILL."""
    if len(config_id) != 32 or any(ch not in "0123456789abcdef" for ch in config_id):
        return
    for candidate in state_dir.glob(f"config-{config_id}-*"):
        if candidate.is_dir() and not candidate.is_symlink():
            shutil.rmtree(candidate, ignore_errors=True)


def terminate_group(proc) -> None:
    """The session leader may exit before its tools; always reap the group."""
    pid = int(getattr(proc, "pid", 0) or 0)
    if not pid:
        return
    def signal_group(sig: int) -> None:
        try:
            os.killpg(pid, sig)
        except PermissionError as error:
            # macOS can return EPERM while the leader is becoming a zombie.
            # Reap only our child, then retry so surviving tools are killed.
            # Never ignore a permission error for a still-live process.
            try:
                proc.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                raise error
            os.killpg(pid, sig)

    try:
        if os.name == "posix":
            signal_group(signal.SIGTERM)
        elif proc.poll() is None:
            proc.terminate()
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=1.5)
    except subprocess.TimeoutExpired:
        pass
    # killpg(pid) still addresses descendants after the leader has exited.
    try:
        if os.name == "posix":
            signal_group(signal.SIGKILL)
        elif proc.poll() is None:
            proc.kill()
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def run_once(command: list[str], request: dict, env: dict, timeout: int) -> str:
    proc = None
    # Never share cleanup ownership, even if callers reuse the request object.
    request = {**request, "config_id": uuid.uuid4().hex}
    selector = selectors.DefaultSelector()
    try:
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=str(ROOT), env=env, start_new_session=True, bufsize=0)
        pending_input = json.dumps(request, ensure_ascii=False).encode("utf-8")
        if len(pending_input) > 2 * 1024 * 1024:
            raise RuntimeError("oar_protocol_error")
        os.set_blocking(proc.stdin.fileno(), False)
        selector.register(proc.stdin, selectors.EVENT_WRITE, "stdin")
        selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
        selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
        deadline = time.monotonic() + timeout
        output = bytearray()
        while selector.get_map():
            if time.monotonic() >= deadline:
                raise RuntimeError("pi_timeout")
            for key, _ in selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                if key.data == "stdin":
                    try:
                        written = os.write(key.fileobj.fileno(), pending_input[:4096])
                        pending_input = pending_input[written:]
                    except BrokenPipeError:
                        pending_input = b""
                    if not pending_input:
                        selector.unregister(key.fileobj)
                        proc.stdin.close()
                    continue
                chunk = os.read(key.fileobj.fileno(), 4096)
                if not chunk:
                    selector.unregister(key.fileobj)
                elif key.data == "stdout":
                    output.extend(chunk)
                    if len(output) > 2 * 1024 * 1024:
                        raise RuntimeError("oar_protocol_error")
        try:
            proc.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("pi_timeout") from exc
        try:
            events = [parse_event(line) for line in output.decode("utf-8").splitlines() if line.strip()]
        except UnicodeError as exc:
            raise RuntimeError("oar_protocol_error") from exc
        results = [event for event in events if event["type"] == "oar_result"]
        if len(results) != 1 or events[-1] is not results[0]:
            raise RuntimeError("oar_protocol_error")
        terminal = results[0]
        if terminal["outcome"] != "completed":
            code = terminal.get("error")
            raise RuntimeError(code if code in ERROR_MESSAGES else "oar_turn_failed")
        if proc.returncode != 0 or not terminal["answer"].strip():
            raise RuntimeError("oar_protocol_error")
        return terminal["answer"]
    finally:
        selector.close()
        if proc:
            terminate_group(proc)
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                if pipe and not pipe.closed:
                    pipe.close()
        cleanup_config(Path(request["state_dir"]), request["config_id"])
