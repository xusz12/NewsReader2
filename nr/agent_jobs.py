from __future__ import annotations

import json
import os
import re
import selectors
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from flask import Response, jsonify, stream_with_context

import agent_notifications
import codex_client
from llm_client import _pi_subprocess_env
from nr import agent_sessions, database, news_chat
from oar_client import (
    ERROR_MESSAGES as OAR_ERROR_MESSAGES,
    bridge_command,
    bridge_request,
    cleanup_config as cleanup_oar_config,
    parse_event as parse_oar_event,
    terminate_group,
)
from settings import DEFAULT_PI_CHAT_MODEL, DEFAULT_PI_CHAT_PROVIDER


AGENT_JOB_TIMEOUT_SECONDS = 300
AGENT_STREAM_POLL_SECONDS = 0.5
AGENT_WORKER_COUNT = 2


def _agent_error_text(value: object) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    text = re.sub(r"(?i)(api[_ -]?key|token|authorization)\s*[:=]\s*\S+", r"\1=[已隐藏]", text)
    return text[:agent_sessions.AGENT_ERROR_MAX_LENGTH] or "agent_failed"


def _agent_stream_text_delta(payload: dict) -> str:
    event_type = str(payload.get("type") or "")
    if event_type == "message_update":
        event = payload.get("assistantMessageEvent") or {}
        if event.get("type") == "text_delta" and isinstance(event.get("delta"), str):
            return event["delta"]
        if event.get("type") == "text_end" and isinstance(event.get("content"), str):
            return event["content"]
    if event_type in {"response.output_text.delta", "output_text.delta", "text_delta"}:
        return payload.get("delta") if isinstance(payload.get("delta"), str) else ""
    if event_type in {"item.completed", "message.completed", "assistant_message"}:
        item = payload.get("item") or payload.get("message") or payload
        if isinstance(item, dict):
            content = item.get("text") or item.get("output_text")
            if isinstance(content, str):
                return content
            chunks = item.get("content")
            if isinstance(chunks, list):
                return "".join(
                    str(chunk.get("text") or chunk.get("value") or "")
                    for chunk in chunks
                    if isinstance(chunk, dict)
                )
    return ""


def _agent_stream_session_id(payload: dict) -> str:
    if payload.get("type") == "session":
        return str(payload.get("id") or "").strip()
    return ""


def _agent_oar_request(job: dict, session: dict, context: dict, history: list[dict], runtime_dir: Path) -> dict:
    if session.get("provider") == "codex":
        config = codex_client.normalize_config(json.loads(session.get("executor_config") or "{}"))
        return codex_client.oar_request(config,
            news_chat.build_agent_prompt(context, job["question"], job.get("quote_text") or "", history=history),
            resume_prompt=news_chat.build_agent_resume_prompt(job["question"], job.get("quote_text") or ""),
            state_dir=runtime_dir, thread_id=codex_client.read_thread(runtime_dir, config), live_search=True)
    payload = bridge_request(
        prompt=news_chat.build_agent_prompt(context, job["question"], job.get("quote_text") or "", history=history),
        resume_prompt=news_chat.build_agent_resume_prompt(job["question"], job.get("quote_text") or ""),
        state_dir=runtime_dir, provider=session.get("executor_provider") or DEFAULT_PI_CHAT_PROVIDER,
        model=job.get("model") or DEFAULT_PI_CHAT_MODEL,
        reasoning_effort=json.loads(session.get("executor_config") or "{}").get("reasoning_effort", ""),
        task="news_chat", runtime=session.get("provider") or "pi",
    )
    payload["history_replayed"] = bool(history)
    # Resource limits are enforced by the worker timeout/cancellation path;
    # do not inject soft-budget steering messages into the conversation.
    return payload


class AgentWorker:
    def __init__(self, db, store, start_lock):
        self.db = db
        self.store = store
        self.start_lock = start_lock
        self.threads = []
        self.stop_event = threading.Event()
        self.processes = {}
        self.process_lock = threading.Lock()

    def _agent_process_command(self, job: dict, session: dict, context: dict, history: list[dict]) -> tuple[list[str], dict[str, str], Path | None]:
        previous = str(session.get("executor_session_id") or "").strip()
        if session.get("provider") == "codex":
            if previous and previous != f"newsreader-codex-agent-{session['id']}":
                raise RuntimeError("codex_session_invalid")
            executor_id = previous or f"newsreader-codex-agent-{session['id']}"
            runtime_dir = self.store.agent_session_runtime_dir(executor_id)
            config = codex_client.normalize_config(json.loads(session.get("executor_config") or "{}"))
            thread_id = codex_client.read_thread(runtime_dir, config)
            # Missing or orphaned mapping must not silently start/resume a different
            # native turn after a failed filesystem/SQLite commit.
            if bool(previous) != bool(thread_id):
                raise RuntimeError("codex_session_invalid")
            return bridge_command(executor_id), codex_client.oar_environment(), runtime_dir
        executor_id = agent_sessions._full_pi_executor_session_id(session["id"], session.get("provider") or "pi") if not previous or agent_sessions._is_legacy_pi_executor_session(previous) else previous
        runtime_dir = self.store.agent_session_runtime_dir(executor_id)
        runtime_dir.mkdir(parents=True, exist_ok=True)
        return bridge_command(executor_id), _pi_subprocess_env(), runtime_dir

    def _update_agent_progress(self, job_id: str, progress: str) -> None:
        conn = self.db.agent_connect()
        try:
            with conn:
                conn.execute("UPDATE agent_jobs SET progress=?, updated_at=? WHERE id=? AND cancel_requested=0 AND status IN ('running', 'streaming') AND progress<>?",
                             (progress, database.now_ts(), job_id, progress))
        finally:
            conn.close()

    def _update_agent_job_output(self, job_id: str, session_id: str, output: str, *, status: str = "streaming") -> None:
        if not output:
            return
        conn = self.db.agent_connect()
        try:
            now = database.now_ts()
            with conn:
                conn.execute(
                    "UPDATE agent_jobs SET answer_text=?, status=?, updated_at=? WHERE id=? AND cancel_requested=0 AND status IN ('running', 'streaming')",
                    (output, status, now, job_id),
                )
                conn.execute(
                    """
                UPDATE agent_messages
                SET content=?, status=?, updated_at=?
                WHERE session_id=? AND job_id=? AND role='assistant'
                  AND status IN ('streaming', 'running')
                """,
                    (output, status, now, session_id, job_id),
                )
        finally:
            conn.close()

    def _agent_cancel_requested(self, job_id: str) -> bool:
        conn = self.db.agent_connect()
        try:
            row = conn.execute("SELECT cancel_requested FROM agent_jobs WHERE id=?", (job_id,)).fetchone()
            return not row or bool(row["cancel_requested"])
        finally:
            conn.close()

    def _terminate_agent_process(self, job_id: str) -> None:
        with self.process_lock:
            proc = self.processes.get(job_id)
        if proc:
            terminate_group(proc)

    def run_agent_job(self, job: dict, session: dict, context: dict, history: list[dict]) -> None:
        selected = session
        binding = json.loads(job.get("model_binding") or "{}")
        isolated_retry = bool(binding and binding != agent_sessions._agent_job_model(selected))
        if isolated_retry:
            session = {**selected, **binding, "executor_config": json.dumps(binding["executor_config"]), "executor_session_id": ""}
        retry_dir = None
        is_codex = session.get("provider") == "codex"
        runtime_errors = codex_client.ERROR_MESSAGES if is_codex else OAR_ERROR_MESSAGES
        protocol_code = "codex_protocol_error" if is_codex else "oar_protocol_error"
        runtime_code = "codex_runtime_failed" if is_codex else "oar_runtime_failed"
        codex_config = None
        codex_parser = None
        output = ""
        stderr = ""
        proc = None
        selector = None
        cancel_sent = False
        timed_out = False
        deadline = time.monotonic() + AGENT_JOB_TIMEOUT_SECONDS
        terminal = None
        terminal_answer: str | None = None
        protocol_error = False
        _runtime_dir = None
        executor_session_id = ""
        config_id = ""
        try:
            if isolated_retry:
                retry_dir = tempfile.TemporaryDirectory(prefix="newsreader-article-retry-")
                _runtime_dir = Path(retry_dir.name)
                command = bridge_command(job["id"])
                env = codex_client.oar_environment() if is_codex else _pi_subprocess_env()
            else:
                command, env, _runtime_dir = self._agent_process_command(job, session, context, history)
            # Cancellation during command preparation must not start a new runtime.
            if self._agent_cancel_requested(job["id"]):
                raise RuntimeError("oar_stopped")
            if is_codex:
                codex_config = codex_client.normalize_config(json.loads(session.get("executor_config") or "{}"))
                codex_parser = codex_client.OarEventParser(codex_client.read_thread(_runtime_dir, codex_config))
            # Store a deterministic private runtime directory, never a foreign SDK id.
            try:
                session_id_index = command.index("--session-id") + 1
                executor_session_id = command[session_id_index]
            except (ValueError, IndexError):
                executor_session_id = f"newsreader-codex-agent-{session['id']}" if is_codex else ""
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
                cwd=str(self.db.root),
                env=env,
                bufsize=0,
                start_new_session=True,
            )
            with self.process_lock:
                self.processes[job["id"]] = proc
            request_payload = _agent_oar_request(job, session, context, history, _runtime_dir)
            config_id = request_payload["config_id"]
            pending_input = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
            if len(pending_input) > 2 * 1024 * 1024:
                raise RuntimeError("oar_protocol_error")
            selector = selectors.DefaultSelector()
            os.set_blocking(proc.stdin.fileno(), False)
            selector.register(proc.stdin, selectors.EVENT_WRITE, "stdin")
            if proc.stdout:
                selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
            if proc.stderr:
                selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
            buffers: dict[str, bytes] = {"stdout": b"", "stderr": b""}

            def handle_line(kind: str, raw_line: bytes) -> None:
                nonlocal output, stderr, terminal, terminal_answer, protocol_error
                if kind == "stderr":
                    # Runtime/extension diagnostics can contain credentials. Discard.
                    return
                if not raw_line.strip():
                    return
                try:
                    event = codex_parser.parse(raw_line.decode("utf-8")) if is_codex else parse_oar_event(raw_line.decode("utf-8"))
                    if terminal is not None:
                        raise RuntimeError("oar_protocol_error")
                    if event["type"] == "oar_result":
                        terminal = event
                        if event["outcome"] == "completed":
                            terminal_answer = event["answer"]
                            if len(terminal_answer) > 2 * 1024 * 1024:
                                raise RuntimeError("oar_protocol_error")
                            # A terminal answer is authoritative for success. Keep any
                            # earlier stream text visible when the terminal answer is
                            # empty, but never use that text to promote the job.
                            if terminal_answer.strip():
                                output = terminal_answer
                                self._update_agent_job_output(job["id"], session["id"], output)
                    elif event["type"] == "oar_session":
                        self._update_agent_progress(job["id"], "thinking")
                    elif event["type"] == "oar_progress":
                        self._update_agent_progress(job["id"], event["phase"])
                    elif event["type"] == "oar_tool":
                        tool = str(event.get("tool") or "").lower()
                        phase = "searching" if "search" in tool else "fetching" if "fetch" in tool or "browse" in tool else "tool"
                        self._update_agent_progress(job["id"], phase)
                    elif event["type"] == "text_delta":
                        self._update_agent_progress(job["id"], "writing")
                        output = event["delta"] if is_codex and event.get("replace") else output + event["delta"]
                        if len(output) > 2 * 1024 * 1024:
                            raise RuntimeError("oar_protocol_error")
                        self._update_agent_job_output(job["id"], session["id"], output)
                except (RuntimeError, UnicodeError):
                    protocol_error = True
                    self._terminate_agent_process(job["id"])

            while selector.get_map():
                if time.monotonic() >= deadline:
                    self._terminate_agent_process(job["id"])
                    timed_out = True
                    cancel_sent = True
                if self._agent_cancel_requested(job["id"]) and not cancel_sent:
                    self._terminate_agent_process(job["id"])
                    cancel_sent = True
                events = selector.select(0.25)
                if not events:
                    if proc.poll() is not None:
                        break
                    continue
                for key, _mask in events:
                    stream = key.fileobj
                    kind = key.data
                    if kind == "stdin":
                        try:
                            written = os.write(stream.fileno(), pending_input[:4096])
                            pending_input = pending_input[written:]
                        except BrokenPipeError:
                            pending_input = b""
                        if not pending_input:
                            selector.unregister(stream)
                            stream.close()
                        continue
                    try:
                        chunk = stream.read1(4096) if hasattr(stream, "read1") else stream.read(4096)
                    except OSError:
                        chunk = b""
                    if not chunk:
                        pending = buffers[kind]
                        if pending:
                            handle_line(kind, pending)
                        buffers[kind] = b""
                        try:
                            selector.unregister(stream)
                        except Exception:
                            pass
                        continue
                    buffers[kind] += chunk
                    if len(buffers[kind]) > 2 * 1024 * 1024:
                        protocol_error = True
                        self._terminate_agent_process(job["id"])
                        buffers[kind] = b""
                    while b"\n" in buffers[kind]:
                        line, buffers[kind] = buffers[kind].split(b"\n", 1)
                        handle_line(kind, line)
            return_code = proc.wait(timeout=5)
            if selector is not None:
                for key in list(selector.get_map().values()):
                    stream = key.fileobj
                    pending = buffers.get(key.data, b"")
                    if pending:
                        handle_line(key.data, pending)
        except subprocess.TimeoutExpired:
            self._terminate_agent_process(job["id"])
            return_code = -1
            stderr = "agent process did not exit"
        except Exception as exc:
            return_code = -1
            stderr = str(exc) if str(exc) in runtime_errors else runtime_code
            if proc is not None:
                self._terminate_agent_process(job["id"])
        finally:
            if proc:
                terminate_group(proc)
                for stream in (proc.stdin, proc.stdout, proc.stderr):
                    if stream and not stream.closed:
                        stream.close()
            if selector is not None:
                selector.close()
            with self.process_lock:
                self.processes.pop(job["id"], None)
            # Bridge finally cleans its unique directory. After SIGKILL, reap only
            # copies carrying this invocation's token, never another worker's config.
            if _runtime_dir and config_id:
                cleanup_oar_config(_runtime_dir, config_id)
            if retry_dir:
                retry_dir.cleanup()

        conn = self.db.agent_connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT cancel_requested, answer_text FROM agent_jobs WHERE id=?",
                (job["id"],),
            ).fetchone()
            if not row:
                # Deletion can race process preparation; reap any directory recreated
                # after it was staged, and do not persist a new Codex thread mapping.
                if _runtime_dir and not isolated_retry:
                    self.store._remove_agent_runtime_dir(_runtime_dir.name)
                return
            output = output or (row["answer_text"] if row else "") or ""
            finished = database.now_ts()
            canceled = bool(row and row["cancel_requested"])
            if canceled:
                status, error = "stopped", ""
            elif timed_out:
                status, error = "failed", "agent_timeout"
            elif protocol_error:
                status, error = "failed", protocol_code
            elif terminal is not None and terminal["outcome"] != "completed":
                code = terminal.get("error") or ""
                status, error = "failed", code if code in runtime_errors else ("codex_turn_failed" if is_codex else "oar_turn_failed")
            elif return_code != 0:
                status, error = "failed", stderr if stderr in runtime_errors else runtime_code
            elif terminal is None:
                status, error = "failed", protocol_code
            elif terminal_answer is None or not terminal_answer.strip():
                status, error = "failed", "empty_answer"
            else:
                status, error = "succeeded", ""
            with conn:
                if status == "succeeded" and is_codex and not isolated_retry:
                    try:
                        codex_client.write_thread(_runtime_dir, codex_config, codex_parser.thread_id)
                    except (OSError, RuntimeError):
                        status, error = "failed", "codex_session_invalid"
                if status == "succeeded" and isolated_retry:
                    self.store._remove_agent_runtime_dir(selected["executor_session_id"])
                    conn.execute("UPDATE agent_sessions SET executor_session_id='' WHERE id=?", (session["id"],))
                elif status == "succeeded" and executor_session_id:
                    conn.execute("UPDATE agent_sessions SET executor_session_id=? WHERE id=?", (executor_session_id, session["id"]))
                conn.execute(
                    """
                UPDATE agent_jobs
                SET status=?, answer_text=?, error=?, finished_at=?, updated_at=?
                WHERE id=?
                """,
                    (status, output, error, finished, finished, job["id"]),
                )
                conn.execute(
                    """
                UPDATE agent_messages
                SET content=?, status=?, updated_at=?
                WHERE session_id=? AND job_id=? AND role='assistant'
                """,
                    (output, status, finished, session["id"], job["id"]),
                )
                conn.execute(
                    """
                UPDATE agent_sessions
                SET last_activity_at=?, expires_at=?, updated_at=?
                WHERE id=?
                """,
                    (finished, agent_sessions.agent_expiry_for(finished), finished, session["id"]),
                )
                agent_notifications.record(conn, job["id"])
        finally:
            conn.close()

    def process_pending_agent_once(self) -> bool:
        conn = self.store.connect()
        try:
            self.store.cleanup_agent_sessions(conn)
            # 多 worker 时必须在写事务中领取任务，避免两个 worker 同时执行同一 job。
            conn.execute("BEGIN IMMEDIATE")
            job = conn.execute(
                """
            SELECT * FROM agent_jobs
            WHERE status IN ('queued', 'pending')
            ORDER BY created_at ASC
            LIMIT 1
            """
            ).fetchone()
            if not job:
                conn.rollback()
                return False
            session = conn.execute("SELECT * FROM agent_sessions WHERE id=? AND status='active'", (job["session_id"],)).fetchone()
            if not session:
                now = database.now_ts()
                conn.execute(
                    "UPDATE agent_jobs SET status='failed', error=?, finished_at=?, updated_at=? WHERE id=?",
                    ("session_expired", now, now, job["id"]),
                )
                conn.commit()
                return True
            context = json.loads(session["context_json"] or "{}")
            message_rows = agent_sessions._agent_session_message_rows(conn, session["id"])
            history = [
                {"role": row["role"], "content": row["content"], "quote_text": row["quote_text"]}
                for row in message_rows
                if row["job_id"] != job["id"] and row["content"]
            ]
            started = database.now_ts()
            conn.execute("UPDATE agent_jobs SET status='running', started_at=?, updated_at=? WHERE id=?", (started, started, job["id"]))
            conn.execute(
                "UPDATE agent_messages SET status='streaming', updated_at=? WHERE session_id=? AND job_id=? AND role='assistant'",
                (started, session["id"], job["id"]),
            )
            conn.commit()
            job_payload = dict(job)
            job_payload["status"] = "running"
            session_payload = dict(session)
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()
        self.run_agent_job(job_payload, session_payload, context, history)
        return True

    def agent_worker_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                processed = self.process_pending_agent_once()
            except Exception:
                processed = False
            if processed:
                time.sleep(0.2)
            else:
                self.stop_event.wait(0.5)

    def start_agent_worker(self) -> None:
        with self.start_lock:
            if any(thread.is_alive() for thread in self.threads):
                return
            self.store.ensure_agent_db()
            self.store.mark_agent_jobs_interrupted()
            self.stop_event.clear()
            self.threads = [
                threading.Thread(target=self.agent_worker_loop, name=f"agent-worker-{index + 1}", daemon=True)
                for index in range(AGENT_WORKER_COUNT)
            ]
            for thread in self.threads:
                thread.start()

    def _agent_stream_generator(self, item_id: str, job_id: str):
        last_signature = None
        last_keepalive = time.monotonic()
        while True:
            conn = self.db.agent_connect()
            try:
                job = self.store._agent_job_for_item(conn, item_id, job_id)
                if not job:
                    payload = {"ok": False, "error": "job_not_found"}
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    return
                serialized = agent_sessions._serialize_agent_job(job)
            finally:
                conn.close()
            signature = (serialized.get("status"), serialized.get("progress"), serialized.get("updated_at"), len(serialized.get("answer_text") or ""), serialized.get("error"))
            if signature != last_signature:
                yield f"data: {json.dumps({'ok': True, 'job': serialized}, ensure_ascii=False)}\n\n"
                last_signature = signature
                last_keepalive = time.monotonic()
            elif time.monotonic() - last_keepalive >= 15:
                yield ": keep-alive\n\n"
                last_keepalive = time.monotonic()
            if serialized.get("status") in {"succeeded", "failed", "stopped", "interrupted"}:
                return
            time.sleep(AGENT_STREAM_POLL_SECONDS)


def register(app, store, worker):
    @app.get("/api/news/<item_id>/agent/jobs/<job_id>/stream")
    def api_agent_job_stream(item_id: str, job_id: str):
        item, _detail = store._load_visible_agent_article(item_id)
        if not item:
            return jsonify({"ok": False, "error": "item_not_found"}), 404
        return Response(
            stream_with_context(worker._agent_stream_generator(item_id, job_id)),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
