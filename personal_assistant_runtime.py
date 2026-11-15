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
        native_id = codex_client.read_thread(state_dir, profile["codex"])
        parser = codex_client.OarEventParser(native_id)
        request = codex_client.oar_request(profile["codex"], prompt, state_dir=state_dir,
            thread_id=native_id, cwd=workspace, resume_prompt=resume_prompt)
        parse = parser.parse
    else:
        choice = profile["pi"]
        if not choice.get("provider") or not choice.get("model"):
            raise RuntimeError("pi_model_not_selected")
        request = oar_client.bridge_request(prompt=prompt, resume_prompt=resume_prompt, state_dir=state_dir,
            provider=choice["provider"], model=choice["model"], reasoning_effort=choice.get("reasoning_effort", ""))
        parse = oar_client.parse_event
    request.update(task="personal_assistant", cwd=str(workspace), nr_tools=config)
    argv = oar_client.bridge_command(state_dir.parent.name)
    pending = json.dumps(request, ensure_ascii=False).encode()
    if len(pending) > MAX_BYTES:
        raise RuntimeError("assistant_prompt_too_long")
    process = None
    selector = selectors.DefaultSelector()
    terminal, buffer = None, b""
    try:
        if stopped():
            raise RuntimeError("assistant_stopped")
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   cwd=workspace, env=_pi_subprocess_env() if runtime == "pi" else codex_client.oar_environment(),
                                   start_new_session=True, bufsize=0)
        os.set_blocking(process.stdin.fileno(), False)
        selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        deadline = time.monotonic() + timeout

        def event(raw):
            nonlocal terminal
            if terminal is not None:
                raise RuntimeError("oar_protocol_error")
            value = parse(raw.decode("utf-8"))
            if value["type"] == "oar_result":
                terminal = value
            else:
                emit(value)

        while selector.get_map():
            if stopped():
                raise RuntimeError("assistant_stopped")
            if time.monotonic() >= deadline:
                raise RuntimeError("assistant_timeout")
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
        if stopped():
            raise RuntimeError("assistant_stopped")
        if not terminal or terminal.get("outcome") != "completed" or not terminal.get("answer", "").strip():
            raise RuntimeError((terminal or {}).get("error") or ("codex_runtime_failed" if runtime == "codex" else "oar_protocol_error"))
        try:
            process.wait(timeout=max(.01, deadline-time.monotonic()))
        except subprocess.TimeoutExpired:
            raise RuntimeError("assistant_timeout") from None
        if stopped():
            raise RuntimeError("assistant_stopped")
        if process.returncode:
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
