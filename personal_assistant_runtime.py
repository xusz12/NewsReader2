"""Persistent native sessions for the personal assistant, separate from News Chat."""
from __future__ import annotations

import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time

import codex_client
import oar_client
from llm_client import _pi_subprocess_env

ROOT = Path(__file__).resolve().parent
MAX_BYTES = 2 * 1024 * 1024


def tool_config(db, workspace, state_db, job_id=None):
    return {"command": str(Path(sys.executable).resolve()), "args": [str(ROOT / "personal_assistant_tools.py"),
            "--db", str(db), "--workspace", str(workspace), "--state-db", str(state_db), *(["--job-id", job_id] if job_id else [])]}


def run(*, profile, prompt, resume_prompt, state_dir, workspace, db, state_db, emit, stopped, job_id=None, timeout=300):
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = tool_config(db, workspace, state_db, job_id)
    runtime = profile["runtime"]
    request = None
    if runtime == "codex":
        codex_client.require_ready()
        native_id = codex_client.read_thread(state_dir, profile["codex"])
        parser = codex_client.EventParser(native_id)
        argv = codex_client.command(profile["codex"], thread_id=native_id, cwd=workspace)
        # MCP tools provide bounded memory writes while the native sandbox stays
        # read-only. These flags are process-local, including resumed sessions.
        argv[2:2] = ["--skip-git-repo-check", "-c", "mcp_servers.nr_assistant.command=" + json.dumps(config["command"]),
                     "-c", "mcp_servers.nr_assistant.args=" + json.dumps(config["args"], ensure_ascii=False),
                     "-c", "mcp_servers.nr_assistant.enabled=true", "-c", "project_doc_max_bytes=0"]
        pending = (resume_prompt if native_id else prompt).encode()
        parse = parser.parse
    else:
        choice = profile["pi"]
        if not choice.get("provider") or not choice.get("model"):
            raise RuntimeError("pi_model_not_selected")
        request = oar_client.bridge_request(prompt=prompt, resume_prompt=resume_prompt, state_dir=state_dir,
            provider=choice["provider"], model=choice["model"], reasoning_effort=choice.get("reasoning_effort", ""))
        request.update(cwd=str(workspace), nr_tools=config)
        argv = oar_client.bridge_command(state_dir.parent.name)
        pending = json.dumps(request, ensure_ascii=False).encode()
        parse = oar_client.parse_event
    if len(pending) > MAX_BYTES:
        raise RuntimeError("assistant_prompt_too_long")
    process = None
    selector = selectors.DefaultSelector()
    terminal, buffer, completion_at = None, b"", None
    try:
        if stopped():
            raise RuntimeError("assistant_stopped")
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   cwd=workspace, env=_pi_subprocess_env() if runtime == "pi" else None,
                                   start_new_session=True, bufsize=0)
        os.set_blocking(process.stdin.fileno(), False)
        selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        deadline = time.monotonic() + timeout

        def event(raw):
            nonlocal terminal, completion_at
            value = parse(raw.decode("utf-8"))
            if value["type"] == "oar_result":
                if terminal is not None:
                    raise RuntimeError("oar_protocol_error")
                terminal, completion_at = value, time.monotonic()
            else:
                emit(value)

        while selector.get_map():
            if stopped():
                raise RuntimeError("assistant_stopped")
            if time.monotonic() >= deadline:
                raise RuntimeError("assistant_timeout")
            if completion_at and time.monotonic() - completion_at > 0.4:
                break
            for key, _ in selector.select(0.1):
                if key.data == "stdin":
                    try:
                        pending = pending[os.write(key.fileobj.fileno(), pending[:4096]):]
                    except BrokenPipeError:
                        pending = b""
                    if not pending:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                    continue
                chunk = os.read(key.fileobj.fileno(), 4096)
                if not chunk:
                    selector.unregister(key.fileobj)
                elif key.data == "stdout":
                    buffer += chunk
                    if len(buffer) > MAX_BYTES:
                        raise RuntimeError("oar_protocol_error")
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        if line.strip():
                            event(line)
                # Drain stderr without ever exposing raw credentials/diagnostics.
        if buffer.strip():
            event(buffer)
        if not terminal or terminal.get("outcome") != "completed" or not terminal.get("answer", "").strip():
            raise RuntimeError((terminal or {}).get("error") or ("codex_runtime_failed" if runtime == "codex" else "oar_protocol_error"))
        if process.poll() not in (None, 0):
            raise RuntimeError("oar_runtime_failed")
        if runtime == "codex":
            codex_client.write_thread(state_dir, profile["codex"], parser.thread_id)
        return terminal["answer"]
    finally:
        if process:
            oar_client.terminate_group(process)
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe:
                    pipe.close()
        selector.close()
        if request:
            oar_client.cleanup_config(state_dir, request["config_id"])
