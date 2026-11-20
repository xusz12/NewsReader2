"""Private OAR bridge protocol. No provider credentials or prompts in argv."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import signal
import selectors
import time
import uuid

ROOT = Path(__file__).resolve().parent
ERROR_MESSAGES = {
    "oar_dsh_missing": "未找到本机 DSH。macOS 可安装官方 DeepSeek Harness 桌面版，也可用 npm install -g @deepseek-ai/dsh 安装 CLI。已有安装可用 OAR_DSH_BIN 指定 dsh 可执行文件。",
    "oar_dsh_incompatible": "当前 DSH 版本或配置不支持此操作，请检查本机 DSH。",
    "dsh_model_not_selected": "请先选择 DSH 服务商与模型。",
    "oar_quota": "服务明确报告账户额度不足。",
    "oar_rate_limited": "服务限制请求频率。",
    "oar_input_too_large": "输入超出服务限制。",
    "oar_overloaded": "服务容量暂不可用。",
    "oar_incomplete_answer": "模型输出未完整结束，请重试。",
    "oar_project_untrusted": "Pi 原有规则未信任此项目，OAR 已暂停以免自动扩大权限；请先在 Pi 检查项目信任。",
    "oar_extension_failed": "Pi 扩展或资源加载失败；为避免丢失现有能力，任务已暂停，请检查 Pi 配置。",
    "oar_node_missing": "未发现 Node.js，请安装 Node.js 24 或更高版本。",
    "oar_node_version": "OAR 需要 Node.js 24 或更高版本。",
    "oar_dependency_missing": "OAR 依赖缺失，请在项目目录执行 npm ci --ignore-scripts。",
    "oar_compatibility_mismatch": "OAR 版本与归档适配不匹配，请更新并验证适配后重试；归档尚未写入。",
    "oar_auth_required": "当前运行时未登录或没有可用凭据，请检查服务商登录，或选择已可用的模型。",
    "oar_runtime_failed": "OAR 运行失败，请检查本机运行时与模型配置。",
    "oar_turn_failed": "Agent 未完整完成本轮请求，请检查模型可用性后重试。",
    "oar_legacy_session_missing": "旧 Pi 会话文件已不可用；旧后台聊天仍保留，请在聊天面板接续或明确开启新会话。",
    "oar_empty_answer": "Agent 未返回有效答案，请重试。",
    "oar_model_unavailable": "所选模型不存在，请检查模型配置。",
    "oar_protocol_error": "Agent 返回不完整，未收到有效的完成结果，请重试。",
}


def bridge_command(session_id: str) -> list[str]:
    node = shutil.which(os.environ.get("NEWS_READER_NODE") or "node")
    if not node:
        raise RuntimeError("oar_node_missing")
    return [node, str(ROOT / "scripts/oar_bridge.mjs"), "--session-id", session_id]


def bridge_request(*, prompt: str, resume_prompt: str, state_dir: Path, provider: str, model: str, reasoning_effort: str = "", task: str = "news_chat", runtime: str = "pi") -> dict:
    tools = {}
    if task in {"news_chat", "personal_assistant", "web_research"}:
        args = [str(ROOT / "deepseek_web_tools.py")]
        if task == "web_research":
            args += ["--evidence", str(state_dir / "web-evidence")]
        tools["web_tools"] = {"command": sys.executable, "args": args}
    return {"prompt": prompt, "resume_prompt": resume_prompt, "state_dir": str(state_dir),
            "cwd": str(state_dir if task in {"material_summary", "web_research", "chat_archive"} else ROOT), "runtime": runtime, **tools, "provider": provider, "model": model,
            **({"reasoning_effort": reasoning_effort} if reasoning_effort else {}),
            **({"task": task} if task else {}),
            "config_id": uuid.uuid4().hex}


def parse_event(raw: str) -> dict:
    try:
        event = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("oar_protocol_error") from exc
    if not isinstance(event, dict) or event.get("type") not in {"text_delta", "oar_session", "oar_tool", "oar_progress", "oar_result"}:
        raise RuntimeError("oar_protocol_error")
    if event["type"] == "oar_progress" and event.get("phase") not in {"thinking", "retrying", "summarizing"}:
        raise RuntimeError("oar_protocol_error")
    if event["type"] == "text_delta" and not isinstance(event.get("delta"), str):
        raise RuntimeError("oar_protocol_error")
    if event["type"] == "oar_tool" and (not isinstance(event.get("tool"), str) or
            ("phase" in event and (event["phase"] != "completed" or not isinstance(event.get("success"), bool)))):
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


def _group_finished(pid: int) -> bool:
    try:
        result = subprocess.run(["ps", "-A", "-o", "pgid=,stat="],
                                capture_output=True, text=True, timeout=2, check=True)
        states = []
        for line in result.stdout.splitlines():
            group, state = line.split()
            if int(group) == pid:
                states.append(state)
        return all(state.startswith("Z") for state in states)
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


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
            try:
                os.killpg(pid, sig)
            except PermissionError:
                if not _group_finished(pid):
                    raise

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
    return run_result(command, request, env, timeout)["answer"]


def run_result(command: list[str], request: dict, env: dict, timeout: int, *,
               parser=parse_event, errors=None, timeout_error="pi_timeout", protocol_error="oar_protocol_error",
               failure_error="oar_turn_failed") -> dict:
    """One shared JSONL transport; runtime adapters provide only their contract."""
    errors = ERROR_MESSAGES if errors is None else errors
    proc = None
    # Never share cleanup ownership, even if callers reuse the request object.
    request = {**request, "config_id": uuid.uuid4().hex}
    selector = selectors.DefaultSelector()
    try:
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=str(ROOT), env=env, start_new_session=True, bufsize=0)
        pending_input = json.dumps(request, ensure_ascii=False).encode("utf-8")
        if len(pending_input) > 2 * 1024 * 1024:
            raise RuntimeError(protocol_error)
        os.set_blocking(proc.stdin.fileno(), False)
        selector.register(proc.stdin, selectors.EVENT_WRITE, "stdin")
        selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
        selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
        deadline = time.monotonic() + timeout
        output = bytearray()
        while selector.get_map():
            if time.monotonic() >= deadline:
                raise RuntimeError(timeout_error)
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
                        raise RuntimeError(protocol_error)
        try:
            proc.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(timeout_error) from exc
        try:
            events = [parser(line) for line in output.decode("utf-8").splitlines() if line.strip()]
        except UnicodeError as exc:
            raise RuntimeError(protocol_error) from exc
        results = [event for event in events if event["type"] == "oar_result"]
        if len(results) != 1 or events[-1] is not results[0]:
            raise RuntimeError(protocol_error)
        terminal = results[0]
        if terminal["outcome"] != "completed":
            code = terminal.get("error")
            raise RuntimeError(code if code in errors else failure_error)
        if proc.returncode != 0 or not terminal["answer"].strip():
            raise RuntimeError(protocol_error)
        return terminal
    finally:
        selector.close()
        if proc:
            terminate_group(proc)
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                if pipe and not pipe.closed:
                    pipe.close()
        cleanup_config(Path(request["state_dir"]), request["config_id"])
