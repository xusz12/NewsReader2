from __future__ import annotations

import json
import os
import shutil
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock

from flask import jsonify, request

import agent_notifications
import codex_client
import report_model
import report_reader
from feed_sources import visible_news_sql
from nr import configuration, database, news_chat


AGENT_DEFAULT_SESSION_TTL_HOURS = 72
AGENT_MAX_QUESTION_LENGTH = 4000
AGENT_MAX_JOBS = 50
AGENT_ERROR_MAX_LENGTH = 500


def agent_session_ttl_hours() -> int:
    raw = configuration.load_app_settings()
    agent = raw.get("agent") if isinstance(raw, dict) else None
    value = agent.get("session_ttl_hours") if isinstance(agent, dict) else None
    return int(value) if value in configuration.AGENT_SESSION_TTL_OPTIONS else AGENT_DEFAULT_SESSION_TTL_HOURS


def agent_expiry_for(timestamp: str) -> str:
    try:
        base = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        base = datetime.now()
    return (base + timedelta(hours=agent_session_ttl_hours())).strftime("%Y-%m-%d %H:%M:%S")


def _agent_session_message_rows(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, job_id, role, content, quote_text, quote_source, status, created_at, updated_at
        FROM agent_messages
        WHERE session_id=?
        ORDER BY created_at ASC, id ASC
        LIMIT ?
        """,
        (session_id, news_chat.AGENT_MAX_MESSAGES),
    ).fetchall()
    return [dict(row) for row in rows]


def _agent_job_rows(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, session_id, item_id, provider, model, question, quote_text, quote_source,
               status, progress, answer_text, error, cancel_requested, created_at, started_at, finished_at, updated_at
        FROM agent_jobs
        WHERE session_id=?
        ORDER BY created_at ASC
        LIMIT ?
        """,
        (session_id, AGENT_MAX_JOBS),
    ).fetchall()
    return [dict(row) for row in rows]


def _full_pi_executor_session_id(session_id: str) -> str:
    return f"newsreader-oar-pi-{session_id}"


def _is_legacy_pi_executor_session(executor_session_id: str) -> bool:
    return bool(executor_session_id) and not executor_session_id.startswith("newsreader-oar-pi-")


def _agent_executor_settings() -> dict:
    """Read one saved settings snapshot for an unused conversation."""
    settings = configuration.current_runtime_settings()
    if settings["agent"].get("runtime") == "codex":
        config = codex_client.normalize_config(settings["agent"]["codex"])
        return dict(provider="codex", model=config["model"], executor_provider="codex", executor_config=config)
    config = settings["llm"].get("pi_chat", {})
    provider = (config.get("provider") or "").strip()
    model = (config.get("model") or "").strip()
    if not provider or not model:
        raise ValueError("pi_model_not_selected")
    return dict(provider="pi", model=model, executor_provider=provider,
                executor_config={"reasoning_effort": config["reasoning_effort"]} if config.get("reasoning_effort") else {})


def _serialize_agent_job(row: sqlite3.Row | dict | None) -> dict | None:
    if not row:
        return None
    payload = dict(row)
    payload["cancel_requested"] = bool(payload.get("cancel_requested"))
    return payload


def _agent_refresh_unused_session(conn: sqlite3.Connection, session: sqlite3.Row, context: dict) -> sqlite3.Row:
    # Unattributed legacy sessions retain their saved executor when explicitly continued.
    if not session["db_scope"]:
        return session
    # Opening a panel may have created an empty session before settings changed.
    # Freeze the executor only once a message/job or native session exists.
    if session["executor_session_id"] or conn.execute(
        "SELECT 1 FROM agent_messages WHERE session_id=? UNION ALL "
        "SELECT 1 FROM agent_jobs WHERE session_id=? LIMIT 1",
        (session["id"], session["id"]),
    ).fetchone():
        return session
    config = ({"provider": session["provider"], "model": session["model"],
               "executor_provider": session["executor_provider"],
               "executor_config": json.loads(session["executor_config"] or "{}")}
              if session["model_override"] else _agent_executor_settings())
    encoded = json.dumps(config["executor_config"])
    if (all(session[key] == config[key] for key in ("provider", "model", "executor_provider"))
            and session["executor_config"] == encoded and session["context_hash"] == context["context_hash"]):
        return session
    conn.execute(
        "UPDATE agent_sessions SET provider=?, model=?, executor_provider=?, executor_config=?, context_hash=?, context_json=?, updated_at=? WHERE id=?",
        (config["provider"], config["model"], config["executor_provider"], encoded, context["context_hash"],
         json.dumps(context, ensure_ascii=False, sort_keys=True), database.now_ts(), session["id"]),
    )
    return conn.execute("SELECT * FROM agent_sessions WHERE id=?", (session["id"],)).fetchone()


def _agent_request_text(body: object) -> tuple[str, str, str]:
    if not isinstance(body, dict):
        raise ValueError("invalid_payload")
    question = str(body.get("question") or "").strip()
    quote_text = str(body.get("quote_text") or "").strip()
    quote_source = str(body.get("quote_source") or "").strip()[:80]
    if not question:
        raise ValueError("empty_question")
    if len(question) > AGENT_MAX_QUESTION_LENGTH:
        raise ValueError("question_too_long")
    if len(quote_text) > news_chat.AGENT_MAX_QUOTE_LENGTH:
        raise ValueError("quote_too_long")
    if quote_source not in {
        "",
        "中文阅读引用",
        "中文正文引用",
        "中文要点引用",
        "中文结论引用",
        "英文原文引用",
        "selection",
    }:
        raise ValueError("invalid_quote_source")
    return question, quote_text, quote_source


def _agent_error_response(error: str) -> tuple[dict, int]:
    status_map = {
        "item_not_found": 404,
        "detail_not_ready": 409,
        "session_not_found": 404,
        "session_expired": 409,
        "job_not_found": 404,
        "empty_question": 400,
        "question_too_long": 400,
        "quote_too_long": 400,
        "invalid_quote_source": 400,
        "invalid_payload": 400,
        "job_in_progress": 409,
        "session_config_changed": 409,
        "session_library_changed": 409,
        "job_not_retryable": 409,
    }
    return {"ok": False, "error": error}, status_map.get(error, 400)


def _agent_model_choices(session=None):
    choices = report_model.selection(configuration.current_runtime_settings(), "chat")
    if session:
        runtime = session["provider"]
        config = json.loads(session["executor_config"] or "{}")
        choices["runtime"] = runtime
        choices[runtime] = (codex_client.normalize_config(config) if runtime == "codex" else
                            {"provider": session["executor_provider"], "model": session["model"],
                             "reasoning_effort": config.get("reasoning_effort", "")})
    return choices


def _agent_choice_settings(choices):
    return {"agent": {"runtime": choices["runtime"], "codex": choices["codex"]},
            "llm": {"pi_chat": choices["pi"]}}


class AgentSessions:
    def __init__(self, db):
        self.db = db
        self._schema_lock = Lock()
        self._schema_identity = None

    def agent_session_runtime_dir(self, session_id: str) -> Path:
        return self.db.agent_dir / session_id

    def _remove_agent_runtime_dir(self, session_id: str) -> None:
        if not session_id:
            return
        path = self.agent_session_runtime_dir(session_id).resolve()
        root = self.db.agent_dir.resolve()
        if path == root or root not in path.parents:
            return
        try:
            if path.exists():
                shutil.rmtree(path)
        except OSError:
            pass

    def ensure_agent_db(self) -> None:
        self.connect().close()

    def connect(self) -> sqlite3.Connection:
        self.db.agent_dir.mkdir(parents=True, exist_ok=True)
        path = self.db.agent_path
        try:
            before = path.stat()
        except FileNotFoundError:
            before = None
        conn = self.db.agent_connect()
        try:
            with self._schema_lock:
                after = path.stat()
                stable = before is not None and os.path.samestat(before, after)
                version = conn.execute("PRAGMA schema_version").fetchone()[0]
                if not stable or self._schema_identity != (after.st_dev, after.st_ino, version):
                    self._migrate_schema(conn)
                    version = conn.execute("PRAGMA schema_version").fetchone()[0]
                    self._schema_identity = (after.st_dev, after.st_ino, version) if stable else None
            return conn
        except BaseException:
            conn.close()
            raise

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
        CREATE TABLE IF NOT EXISTS agent_sessions (
          id TEXT PRIMARY KEY,
          item_id TEXT NOT NULL,
          url TEXT NOT NULL,
          provider TEXT NOT NULL,
          executor_provider TEXT NOT NULL DEFAULT '',
          model TEXT NOT NULL DEFAULT '',
          executor_session_id TEXT NOT NULL DEFAULT '',
          context_hash TEXT NOT NULL,
          context_json TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'active',
          created_at TEXT NOT NULL,
          last_activity_at TEXT NOT NULL,
          expires_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_agent_sessions_item_status
          ON agent_sessions(item_id, status, last_activity_at);
        CREATE TABLE IF NOT EXISTS agent_jobs (
          id TEXT PRIMARY KEY,
          session_id TEXT NOT NULL,
          item_id TEXT NOT NULL,
          provider TEXT NOT NULL,
          model TEXT NOT NULL DEFAULT '',
          question TEXT NOT NULL,
          quote_text TEXT NOT NULL DEFAULT '',
          quote_source TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL,
          answer_text TEXT NOT NULL DEFAULT '',
          error TEXT NOT NULL DEFAULT '',
          cancel_requested INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          started_at TEXT,
          finished_at TEXT,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_agent_jobs_session_created
          ON agent_jobs(session_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_agent_jobs_status_created
          ON agent_jobs(status, created_at);
        CREATE TABLE IF NOT EXISTS agent_messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id TEXT NOT NULL,
          job_id TEXT,
          role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
          content TEXT NOT NULL DEFAULT '',
          quote_text TEXT NOT NULL DEFAULT '',
          quote_source TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'complete',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_agent_messages_session_created
          ON agent_messages(session_id, created_at, id);
        """
        )
        session_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(agent_sessions)").fetchall()
        }
        if "executor_provider" not in session_columns:
            conn.execute("ALTER TABLE agent_sessions ADD COLUMN executor_provider TEXT NOT NULL DEFAULT ''")
        if "executor_config" not in session_columns:
            conn.execute("ALTER TABLE agent_sessions ADD COLUMN executor_config TEXT NOT NULL DEFAULT '{}'")
        if "model_override" not in session_columns:
            conn.execute("ALTER TABLE agent_sessions ADD COLUMN model_override INTEGER NOT NULL DEFAULT 0")
        job_columns = {row["name"] for row in conn.execute("PRAGMA table_info(agent_jobs)")}
        if "progress" not in job_columns:
            conn.execute("ALTER TABLE agent_jobs ADD COLUMN progress TEXT NOT NULL DEFAULT ''")
        agent_notifications.ensure_schema(conn)
        conn.commit()

    def cleanup_agent_sessions(self, conn: sqlite3.Connection | None = None) -> int:
        owns_connection = conn is None
        local_conn = conn or self.connect()
        removed = 0
        try:
            rows = local_conn.execute(
                """
            SELECT id, executor_session_id
            FROM agent_sessions
                WHERE status IN ('active', 'archived') AND expires_at <= ?
              AND NOT EXISTS (
                SELECT 1 FROM agent_jobs aj
                WHERE aj.session_id=agent_sessions.id
                  AND aj.status IN ('queued', 'pending', 'running', 'streaming', 'stopping')
              )
            """,
                (database.now_ts(),),
            ).fetchall()
            if not rows:
                return 0
            with local_conn:
                for row in rows:
                    local_conn.execute("DELETE FROM agent_messages WHERE session_id=?", (row["id"],))
                    local_conn.execute("DELETE FROM agent_jobs WHERE session_id=?", (row["id"],))
                    local_conn.execute("DELETE FROM agent_sessions WHERE id=?", (row["id"],))
                    self._remove_agent_runtime_dir(row["executor_session_id"] or row["id"])
                    removed += 1
            return removed
        finally:
            if owns_connection:
                local_conn.close()

    def mark_agent_jobs_interrupted(self) -> int:
        """把服务重启前未结束的工作标为 interrupted，避免伪装成仍在运行。"""
        conn = self.connect()
        try:
            stamp = database.now_ts()
            with conn:
                rows = conn.execute(
                    """
                SELECT id, session_id
                FROM agent_jobs
                WHERE status IN ('queued', 'pending', 'running', 'streaming', 'stopping')
                """
                ).fetchall()
                if not rows:
                    return 0
                conn.execute(
                    """
                UPDATE agent_jobs
                SET status='interrupted', error='service_restarted', finished_at=?, updated_at=?
                WHERE status IN ('queued', 'pending', 'running', 'streaming', 'stopping')
                """,
                    (stamp, stamp),
                )
                conn.execute(
                    """
                UPDATE agent_messages
                SET status='interrupted', updated_at=?
                WHERE role='assistant' AND status IN ('streaming', 'running')
                  AND job_id IN (SELECT id FROM agent_jobs WHERE status='interrupted')
                """,
                    (stamp,),
                )
                conn.execute(
                    """
                UPDATE agent_sessions
                SET last_activity_at=?, expires_at=?, updated_at=?
                WHERE id IN (SELECT session_id FROM agent_jobs WHERE status='interrupted')
                """,
                    (stamp, agent_expiry_for(stamp), stamp),
                )
                for row in rows:
                    agent_notifications.record(conn, row["id"])
                return len(rows)
        finally:
            conn.close()

    def refresh_agent_session_expiries(self) -> None:
        """让设置页修改的 TTL 立即作用于现有会话，而不是只作用于下一次提问。"""
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT id, last_activity_at FROM agent_sessions WHERE status IN ('active', 'archived')"
            ).fetchall()
            with conn:
                for row in rows:
                    conn.execute(
                        "UPDATE agent_sessions SET expires_at=?, updated_at=? WHERE id=?",
                        (agent_expiry_for(row["last_activity_at"]), database.now_ts(), row["id"]),
                    )
        finally:
            conn.close()

    def _serialize_agent_session(self, row: sqlite3.Row | dict | None) -> dict | None:
        if not row:
            return None
        payload = dict(row)
        context_json = payload.pop("context_json", "{}")
        payload.update(news_chat.agent_context_metadata(json.loads(context_json or "{}")))
        executor_id = payload.pop("executor_session_id", "")
        config_json = payload.pop("executor_config", "{}")
        try:
            payload["reasoning_effort"] = json.loads(config_json or "{}").get("reasoning_effort", "")
        except (ValueError, TypeError, AttributeError):
            payload["reasoning_effort"] = ""
        if payload.get("provider") == "codex":
            compatibility = ""
            try:
                config = codex_client.normalize_config(json.loads(config_json))
            except (ValueError, TypeError) as exc:
                config = {"model": "", "sandbox": "read-only"}
                compatibility = codex_client.ERROR_MESSAGES.get(str(exc), codex_client.ERROR_MESSAGES["codex_session_invalid"])
            payload.update(runtime="codex", runtime_label="Codex", runtime_compatibility=compatibility, history_compatibility="", executor_config=config)
            return payload
        runtime_key = executor_id if executor_id and not _is_legacy_pi_executor_session(executor_id) else _full_pi_executor_session_id(payload["id"])
        payload.update(news_chat.agent_runtime_metadata(runtime_key))
        payload["history_compatibility"] = ""
        if _is_legacy_pi_executor_session(executor_id):
            payload["history_compatibility"] = "旧对话保留；续问时将携带已保存的历史接续会话（不包含旧工具执行状态）。"
        else:
            try:
                mapping = json.loads((self.agent_session_runtime_dir(executor_id) / "session.json").read_text())
                if mapping.get("history_replayed"):
                    payload["history_compatibility"] = "已携带保存的旧对话接续；不包含迁移前的工具执行状态。"
            except (OSError, ValueError):
                pass
        return payload

    def load_agent_payload(self, item_id: str) -> dict:
        conn = self.connect()
        try:
            self.cleanup_agent_sessions(conn)
            session = self._agent_active_session(conn, item_id)
            if not session:
                return {"session": None, "messages": [], "jobs": []}
            return {
                "session": self._serialize_agent_session(session),
                "messages": _agent_session_message_rows(conn, session["id"]),
                "jobs": _agent_job_rows(conn, session["id"]),
            }
        finally:
            conn.close()

    def _load_visible_agent_article(self, item_id: str) -> tuple[dict | None, dict | None]:
        conn = self.db.connect()
        try:
            if item_id.startswith(report_reader.ID_PREFIX):
                item = report_reader.subject(conn, item_id)
                if not item:
                    return None, None
                detail = {"content": report_reader.context_text(item)}
                return {key: value for key, value in item.items() if key not in {"content", "sources"}}, detail
            item = conn.execute(
                f"""
                SELECT id, title, url, source, source_name, source_type, summary, published_at
                FROM items
                WHERE id=? AND {visible_news_sql()}
                """,
                (item_id,),
            ).fetchone()
            if not item:
                return None, None
            detail = conn.execute(
                """
            SELECT title, source, published_at, content
            FROM news_item_details
            WHERE item_id=?
            """,
                (item_id,),
            ).fetchone()
            return dict(item), (dict(detail) if detail else None)
        finally:
            conn.close()

    def _agent_payload_for_session(self, conn: sqlite3.Connection, session: sqlite3.Row | dict) -> dict:
        session_id = session["id"]
        job_rows = _agent_job_rows(conn, session_id)
        messages = _agent_session_message_rows(conn, session_id)
        return {
            "session": self._serialize_agent_session(session),
            "messages": messages,
            "jobs": [_serialize_agent_job(row) for row in job_rows],
        }

    def agent_library_scope(self) -> str:
        return agent_notifications.library_scope(self.db.path)

    def _agent_active_session(self, conn: sqlite3.Connection, item_id: str, session_id: str = "") -> sqlite3.Row | None:
        item, _ = self._load_visible_agent_article(item_id)
        if not item or not item.get("url"):
            return None
        return conn.execute("""
        SELECT * FROM agent_sessions
        WHERE item_id=? AND status='active' AND (?='' OR id=?)
          AND (db_scope=? OR (db_scope='' AND url=?))
        ORDER BY (db_scope<>'') DESC, last_activity_at DESC, rowid DESC LIMIT 1
    """, (item_id, session_id, session_id, self.agent_library_scope(), item["url"])).fetchone()

    def _agent_claim_legacy_session(self, conn, session):
        scope = self.agent_library_scope()
        if not session["db_scope"]:
            changed = conn.execute("UPDATE agent_sessions SET db_scope=? WHERE id=? AND db_scope=''",
                                   (scope, session["id"])).rowcount
            if not changed:
                raise ValueError("session_library_changed")
            session = conn.execute("SELECT * FROM agent_sessions WHERE id=?", (session["id"],)).fetchone()
        if session["db_scope"] != scope:
            raise ValueError("session_library_changed")
        return session

    def _agent_create_session(self,
        conn: sqlite3.Connection,
        item: dict,
        detail: dict,
        config: dict | None = None,
    ) -> sqlite3.Row:
        config = config or _agent_executor_settings()
        context = news_chat.build_agent_context(item, detail)
        if not context:
            raise ValueError("detail_not_ready")
        stamp = database.now_ts()
        session_id = str(uuid.uuid4())
        conn.execute(
            """
        INSERT INTO agent_sessions
          (id, item_id, url, provider, executor_provider, executor_config, model, executor_session_id,
           context_hash, context_json, status, created_at, last_activity_at, expires_at, updated_at, db_scope)
        VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, 'active', ?, ?, ?, ?, ?)
        """,
            (
                session_id,
                item["id"],
                item["url"],
                config["provider"],
                config["executor_provider"],
                json.dumps(config["executor_config"]),
                config["model"],
                context["context_hash"],
                json.dumps(context, ensure_ascii=False, sort_keys=True),
                stamp,
                stamp,
                agent_expiry_for(stamp),
                stamp,
                self.agent_library_scope(),
            ),
        )
        return conn.execute("SELECT * FROM agent_sessions WHERE id=?", (session_id,)).fetchone()

    def _agent_insert_job(self,
        conn: sqlite3.Connection,
        session: sqlite3.Row | dict,
        *,
        question: str,
        quote_text: str = "",
        quote_source: str = "",
    ) -> sqlite3.Row:
        session = self._agent_claim_legacy_session(conn, session)
        active = conn.execute(
            """
        SELECT id FROM agent_jobs
        WHERE session_id=? AND status IN ('queued', 'pending', 'running', 'streaming', 'stopping')
        LIMIT 1
        """,
            (session["id"],),
        ).fetchone()
        if active:
            raise ValueError("job_in_progress")
        stamp = database.now_ts()
        job_id = str(uuid.uuid4())
        conn.execute(
            """
        INSERT INTO agent_jobs
          (id, session_id, item_id, provider, model, question, quote_text, quote_source,
           status, answer_text, error, cancel_requested, created_at, updated_at, db_scope, notify_enabled)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', '', '', 0, ?, ?, ?, 1)
        """,
            (
                job_id,
                session["id"],
                session["item_id"],
                session["provider"],
                session["model"],
                question,
                quote_text,
                quote_source,
                stamp,
                stamp,
                session["db_scope"],
            ),
        )
        conn.execute(
            """
        INSERT INTO agent_messages
          (session_id, job_id, role, content, quote_text, quote_source, status, created_at, updated_at)
        VALUES (?, ?, 'user', ?, ?, ?, 'complete', ?, ?)
        """,
            (session["id"], job_id, question, quote_text, quote_source, stamp, stamp),
        )
        conn.execute(
            """
        INSERT INTO agent_messages
          (session_id, job_id, role, content, quote_text, quote_source, status, created_at, updated_at)
        VALUES (?, ?, 'assistant', '', '', '', 'streaming', ?, ?)
        """,
            (session["id"], job_id, stamp, stamp),
        )
        conn.execute(
            """
        UPDATE agent_sessions
        SET last_activity_at=?, expires_at=?, updated_at=?
        WHERE id=?
        """,
            (stamp, agent_expiry_for(stamp), stamp, session["id"]),
        )
        return conn.execute("SELECT * FROM agent_jobs WHERE id=?", (job_id,)).fetchone()

    def _agent_job_for_item(self, conn: sqlite3.Connection, item_id: str, job_id: str) -> sqlite3.Row | None:
        item, _ = self._load_visible_agent_article(item_id)
        if not item or not item.get("url"):
            return None
        scope = self.agent_library_scope()
        return conn.execute("""
        SELECT aj.* FROM agent_jobs aj JOIN agent_sessions s ON s.id=aj.session_id
        WHERE aj.id=? AND aj.item_id=? AND s.item_id=?
          AND (aj.db_scope='' OR aj.db_scope=?)
          AND (s.db_scope=? OR (s.db_scope='' AND s.url=?))
    """, (job_id, item_id, item_id, scope, scope, item["url"])).fetchone()

    def _agent_current_context_hash(self, item_id: str) -> str:
        item, detail = self._load_visible_agent_article(item_id)
        if not item:
            return ""
        context = news_chat.build_agent_context(item, detail)
        return context["context_hash"] if context else ""

    def _agent_session_response(self, conn: sqlite3.Connection, session: sqlite3.Row | dict, current_hash: str = "") -> dict:
        payload = self._agent_payload_for_session(conn, session)
        payload["context_updated"] = bool(current_hash and current_hash != session["context_hash"])
        payload["current_context_hash"] = current_hash or session["context_hash"]
        return payload

    def _delete_agent_sessions_for_item(self, conn: sqlite3.Connection, item_id: str) -> list[str]:
        # Unattributed legacy history and other libraries are deliberately preserved.
        sessions = conn.execute("SELECT id, executor_session_id FROM agent_sessions WHERE item_id=? AND db_scope=?",
                                (item_id, self.agent_library_scope())).fetchall()
        process_ids = []
        for row in sessions:
            process_ids.extend(job["id"] for job in conn.execute("SELECT id FROM agent_jobs WHERE session_id=? AND status IN ('queued','pending','running','streaming','stopping')", (row["id"],)))
            conn.execute("DELETE FROM agent_messages WHERE session_id=?", (row["id"],))
            conn.execute("DELETE FROM agent_jobs WHERE session_id=?", (row["id"],))
            conn.execute("DELETE FROM agent_sessions WHERE id=?", (row["id"],))
            self._remove_agent_runtime_dir(row["executor_session_id"] or row["id"])
        return process_ids

    def _create_agent_job_response(self, item_id: str, body: object, *, retry_job_id: str = ""):
        item, detail = self._load_visible_agent_article(item_id)
        if not item:
            payload, status = _agent_error_response("item_not_found")
            return jsonify(payload), status
        context = news_chat.build_agent_context(item, detail)
        if not context:
            payload, status = _agent_error_response("detail_not_ready")
            return jsonify(payload), status
        try:
            if retry_job_id:
                question = quote_text = quote_source = ""
            else:
                question, quote_text, quote_source = _agent_request_text(body)
        except ValueError as exc:
            payload, status = _agent_error_response(str(exc))
            return jsonify(payload), status
        conn = self.connect()
        try:
            self.cleanup_agent_sessions(conn)
            conn.execute("BEGIN IMMEDIATE")
            with conn:
                session_id = ""
                if isinstance(body, dict):
                    session_id = str(body.get("session_id") or "").strip()
                session = self._agent_active_session(conn, item_id, session_id)
                if not session:
                    if session_id:
                        other = conn.execute("SELECT db_scope FROM agent_sessions WHERE id=?", (session_id,)).fetchone()
                        error = "session_library_changed" if other and other["db_scope"] and other["db_scope"] != self.agent_library_scope() else "session_not_found"
                        payload, status = _agent_error_response(error)
                        return jsonify(payload), status
                    session = self._agent_create_session(conn, item, detail or {})
                else:
                    session = _agent_refresh_unused_session(conn, session, context)
                if retry_job_id:
                    old_job = self._agent_job_for_item(conn, item_id, retry_job_id)
                    if not old_job or old_job["session_id"] != session["id"]:
                        payload, status = _agent_error_response("job_not_found")
                        return jsonify(payload), status
                    if old_job["status"] not in {"failed", "stopped", "interrupted"}:
                        payload, status = _agent_error_response("job_not_retryable")
                        return jsonify(payload), status
                    question = old_job["question"]
                    quote_text = old_job["quote_text"]
                    quote_source = old_job["quote_source"]
                job = self._agent_insert_job(conn, session, question=question, quote_text=quote_text, quote_source=quote_source)
                session = conn.execute("SELECT * FROM agent_sessions WHERE id=?", (session["id"],)).fetchone()
                response_payload = self._agent_session_response(conn, session, context["context_hash"])
                response_payload["job"] = _serialize_agent_job(job)
            return jsonify({"ok": True, "job_id": job["id"], "context_available": True, **response_payload}), 202
        except ValueError as exc:
            payload, status = _agent_error_response(str(exc))
            return jsonify(payload), status
        finally:
            conn.close()


def register(app, store, worker):
    @app.get("/api/agent/notifications")
    def api_agent_notifications():
        with closing(store.connect()) as conn, conn:
            store.cleanup_agent_sessions(conn)
            scope = store.agent_library_scope()
            return jsonify(ok=True, db_scope=scope, notifications=agent_notifications.unread(conn, scope))

    @app.get("/api/agent/notifications/<notification_id>/target")
    def api_agent_notification_target(notification_id: str):
        scope = store.agent_library_scope()
        if request.args.get("db_scope") != scope:
            return jsonify(ok=False, error="notification_library_changed"), 409
        with closing(store.connect()) as conn, conn:
            store.cleanup_agent_sessions(conn)
            notice = conn.execute("SELECT * FROM agent_notifications WHERE id=? AND db_scope=?", (notification_id, scope)).fetchone()
            if not notice:
                return jsonify(ok=False, error="notification_not_found"), 404
            item, _ = store._load_visible_agent_article(notice["item_id"])
            if not item:
                return jsonify(ok=False, error="notification_article_unavailable"), 404
            session = agent_notifications.target_session(conn, notice, scope)
            job = store._agent_job_for_item(conn, notice["item_id"], notice["job_id"])
            message = conn.execute("SELECT * FROM agent_messages WHERE id=? AND session_id=? AND job_id=? AND role='assistant'",
                                   (notice["message_id"], notice["session_id"], notice["job_id"])).fetchone()
            if (not session or session["url"] != item["url"] or not message or not job
                    or job["db_scope"] != scope or job["session_id"] != session["id"]):
                return jsonify(ok=False, error="notification_session_unavailable"), 410
            payload = store._agent_payload_for_session(conn, session)
            # A pinned old turn must remain reachable even beyond the normal history window.
            if not any(row["id"] == message["id"] for row in payload["messages"]):
                payload["messages"].append(dict(message))
            if not any(row["id"] == job["id"] for row in payload["jobs"]):
                payload["jobs"].append(_serialize_agent_job(job))
            payload["context_available"] = True
            return jsonify(ok=True, db_scope=scope, item=item, agent=payload, notification=dict(notice))

    @app.post("/api/agent/notifications/read")
    def api_agent_notifications_read():
        body = request.get_json(silent=True)
        scope = store.agent_library_scope()
        if not isinstance(body, dict) or body.get("db_scope") != scope:
            return jsonify(ok=False, error="notification_library_changed"), 409
        ids = body.get("ids")
        if not isinstance(ids, list) or not ids or len(ids) > 1000 or any(not isinstance(ident, str) or not ident for ident in ids):
            return jsonify(ok=False, error="invalid_payload"), 400
        dismiss = body.get("dismiss") is True
        try:
            with closing(store.connect()) as conn, conn:
                store.cleanup_agent_sessions(conn)
                conn.execute("BEGIN IMMEDIATE")
                if not dismiss:
                    for ident in ids:
                        notice = conn.execute("SELECT * FROM agent_notifications WHERE id=? AND db_scope=?", (ident, scope)).fetchone()
                        if not notice:
                            raise ValueError("notification_not_found")
                        item, _ = store._load_visible_agent_article(notice["item_id"])
                        if not item:
                            raise ValueError("notification_article_unavailable")
                        session = agent_notifications.target_session(conn, notice, scope)
                        job = store._agent_job_for_item(conn, notice["item_id"], notice["job_id"])
                        if not session or session["url"] != item["url"] or not job or job["db_scope"] != scope:
                            raise ValueError("notification_session_unavailable")
                agent_notifications.acknowledge(conn, scope, ids, database.now_ts(), dismiss=dismiss)
            return jsonify(ok=True, ids=ids, db_scope=scope)
        except ValueError as exc:
            return jsonify(ok=False, error=str(exc)), 409

    @app.get("/api/news/<item_id>/agent")
    @app.get("/api/news/<item_id>/agent/session")
    def api_agent_session(item_id: str):
        item, detail = store._load_visible_agent_article(item_id)
        if not item:
            return jsonify({"ok": False, "error": "item_not_found"}), 404
        conn = store.connect()
        try:
            store.cleanup_agent_sessions(conn)
            requested_session = request.args.get("session_id", "").strip()
            if request.args.get("db_scope", store.agent_library_scope()) != store.agent_library_scope():
                return jsonify(ok=False, error="notification_library_changed"), 409
            if requested_session:
                session = agent_notifications.target_session(conn, {"session_id": requested_session, "item_id": item_id}, store.agent_library_scope())
                if not session or session["url"] != item["url"]:
                    return jsonify(ok=False, error="notification_session_unavailable"), 410
            else:
                session = store._agent_active_session(conn, item_id)
            current_hash = ""
            context = news_chat.build_agent_context(item, detail)
            if context:
                current_hash = context["context_hash"]
            if not session:
                return jsonify({"ok": True, "session": None, "messages": [], "jobs": [], "context_available": bool(context), "context_hash": current_hash, **news_chat.agent_context_metadata(context or {})})
            return jsonify({"ok": True, "context_available": bool(context), **store._agent_session_response(conn, session, current_hash)})
        finally:
            conn.close()

    @app.post("/api/news/<item_id>/agent/session")
    def api_agent_session_create(item_id: str):
        item, detail = store._load_visible_agent_article(item_id)
        if not item:
            return jsonify({"ok": False, "error": "item_not_found"}), 404
        context = news_chat.build_agent_context(item, detail)
        if not context:
            return jsonify({"ok": False, "error": "detail_not_ready"}), 409
        body = request.get_json(silent=True)
        force_new = isinstance(body, dict) and bool(body.get("new_session") or body.get("reset"))
        conn = store.connect()
        process_ids: list[str] = []
        try:
            store.cleanup_agent_sessions(conn)
            conn.execute("BEGIN IMMEDIATE")
            with conn:
                session = store._agent_active_session(conn, item_id)
                if session and not force_new:
                    session = _agent_refresh_unused_session(conn, session, context)
                    return jsonify({"ok": True, "context_available": True, **store._agent_session_response(conn, session, context["context_hash"])})
                if force_new:
                    active_job = conn.execute(
                        """
                    SELECT id FROM agent_jobs
                    WHERE item_id=? AND db_scope=? AND status IN ('queued','pending','running','streaming','stopping')
                    LIMIT 1
                    """,
                        (item_id, store.agent_library_scope()),
                    ).fetchone()
                    if active_job:
                        payload, status = _agent_error_response("job_in_progress")
                        return jsonify(payload), status
                    process_ids = store._delete_agent_sessions_for_item(conn, item_id)
                session = store._agent_create_session(conn, item, detail or {})
            response_payload = store._agent_session_response(conn, session, context["context_hash"])
            for job_id in process_ids:
                worker._terminate_agent_process(job_id)
            return jsonify({"ok": True, "context_available": True, **response_payload})
        except ValueError as exc:
            return jsonify(ok=False, error=str(exc)), 400
        finally:
            conn.close()

    @app.get("/api/news/<item_id>/agent/model-options")
    def api_agent_model_options(item_id):
        item, _ = store._load_visible_agent_article(item_id)
        if not item:
            return jsonify(ok=False, error="item_not_found"), 404
        with closing(store.connect()) as conn:
            session = store._agent_active_session(conn, item_id, request.args.get("session_id", ""))
        try:
            choices = _agent_model_choices(session)
            runtime = request.args.get("runtime", choices["runtime"])
            catalog = report_model.catalog(_agent_choice_settings(choices), runtime, "chat")
            return jsonify(ok=True, choices=choices, runtime=runtime, catalog=catalog)
        except (ValueError, RuntimeError) as exc:
            if str(exc) == "codex_advanced_config_removed":
                return jsonify(ok=False, error=str(exc)), 400
            return jsonify(ok=False, error="report_model_catalog_failed"), 503

    @app.patch("/api/news/<item_id>/agent/session/model")
    def api_agent_model_update(item_id):
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get("selection"), dict):
            return jsonify(ok=False, error="invalid_payload"), 400
        item, detail = store._load_visible_agent_article(item_id)
        if not item:
            return jsonify(ok=False, error="item_not_found"), 404
        with closing(store.connect()) as conn:
            session = store._agent_active_session(conn, item_id)
            expected = body.get("session_id") or ""
            if expected != (session["id"] if session else ""):
                return jsonify(ok=False, error="session_config_changed"), 409
            try:
                choices = _agent_model_choices(session)
                selected = report_model.configuration(_agent_choice_settings(choices), resolve=False,
                                                      override=body["selection"], feature="chat")
            except (ValueError, RuntimeError) as exc:
                return jsonify(ok=False, error=str(exc)), 400
            config = (dict(provider="codex", model=selected["codex"]["model"], executor_provider="codex",
                           executor_config=selected["codex"]) if selected["runtime"] == "codex" else
                      dict(provider="pi", model=selected["model"], executor_provider=selected["provider"],
                           executor_config={"reasoning_effort": selected.get("reasoning_effort", "")}))
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                # Catalog validation can take time. Recheck the target and running jobs under the write lock.
                current = store._agent_active_session(conn, item_id)
                if expected != (current["id"] if current else ""):
                    return jsonify(ok=False, error="session_config_changed"), 409
                if conn.execute("SELECT 1 FROM agent_jobs WHERE item_id=? AND db_scope=? AND status IN "
                                "('queued','pending','running','streaming','stopping') LIMIT 1",
                                (item_id, store.agent_library_scope())).fetchone():
                    return jsonify(ok=False, error="job_in_progress"), 409
                if not current:
                    current = store._agent_create_session(conn, item, detail or {}, config)
                else:
                    # A fresh native turn receives the saved transcript. Never resume a turn with changed settings.
                    for runtime_id in {current["executor_session_id"], _full_pi_executor_session_id(current["id"]),
                                       f"newsreader-codex-agent-{current['id']}"} - {""}:
                        store._remove_agent_runtime_dir(runtime_id)
                conn.execute("UPDATE agent_sessions SET provider=?, model=?, executor_provider=?, executor_config=?, "
                             "executor_session_id='', model_override=1, updated_at=? WHERE id=?",
                             (config["provider"], config["model"], config["executor_provider"],
                              json.dumps(config["executor_config"]), database.now_ts(), current["id"]))
                current = conn.execute("SELECT * FROM agent_sessions WHERE id=?", (current["id"],)).fetchone()
            return jsonify(ok=True, context_available=True, **store._agent_session_response(conn, current))

    @app.delete("/api/news/<item_id>/agent/session")
    def api_agent_session_clear(item_id: str):
        item, _detail = store._load_visible_agent_article(item_id)
        if not item:
            return jsonify({"ok": False, "error": "item_not_found"}), 404
        conn = store.connect()
        process_ids: list[str] = []
        try:
            with conn:
                legacy = store._agent_active_session(conn, item_id)
                if legacy and not legacy["db_scope"]:
                    return jsonify(ok=False, error="legacy_session_unbound"), 409
                process_ids = store._delete_agent_sessions_for_item(conn, item_id)
            for job_id in process_ids:
                worker._terminate_agent_process(job_id)
            return jsonify({"ok": True, "cleared": True})
        finally:
            conn.close()

    @app.delete("/api/agent/sessions")
    def api_agent_sessions_clear_all():
        conn = store.connect()
        process_ids: list[str] = []
        try:
            with conn:
                scope = store.agent_library_scope()
                sessions = conn.execute("SELECT id, executor_session_id FROM agent_sessions WHERE db_scope=?", (scope,)).fetchall()
                for row in sessions:
                    process_ids.extend(job["id"] for job in conn.execute("SELECT id FROM agent_jobs WHERE session_id=? AND status IN ('queued','pending','running','streaming','stopping')", (row["id"],)))
                    conn.execute("DELETE FROM agent_messages WHERE session_id=?", (row["id"],))
                    conn.execute("DELETE FROM agent_jobs WHERE session_id=?", (row["id"],))
                    conn.execute("DELETE FROM agent_sessions WHERE id=?", (row["id"],))
                    store._remove_agent_runtime_dir(row["executor_session_id"] or row["id"])
            for job_id in process_ids:
                worker._terminate_agent_process(job_id)
            legacy_count = conn.execute("SELECT COUNT(*) FROM agent_sessions WHERE db_scope=''").fetchone()[0]
            return jsonify({"ok": True, "cleared": True, "preserved_legacy": legacy_count})
        finally:
            conn.close()

    @app.post("/api/news/<item_id>/agent/jobs")
    def api_agent_job_create(item_id: str):
        return store._create_agent_job_response(item_id, request.get_json(silent=True) or {})

    @app.post("/api/news/<item_id>/agent/jobs/<job_id>/retry")
    def api_agent_job_retry(item_id: str, job_id: str):
        return store._create_agent_job_response(item_id, {"session_id": ""}, retry_job_id=job_id)

    @app.get("/api/news/<item_id>/agent/jobs/<job_id>")
    def api_agent_job_get(item_id: str, job_id: str):
        item, _detail = store._load_visible_agent_article(item_id)
        if not item:
            return jsonify({"ok": False, "error": "item_not_found"}), 404
        conn = store.connect()
        try:
            store.cleanup_agent_sessions(conn)
            job = store._agent_job_for_item(conn, item_id, job_id)
            if not job:
                return jsonify({"ok": False, "error": "job_not_found"}), 404
            session = conn.execute("SELECT * FROM agent_sessions WHERE id=?", (job["session_id"],)).fetchone()
            return jsonify({"ok": True, "job": _serialize_agent_job(job), "session": store._serialize_agent_session(session)})
        finally:
            conn.close()

    @app.post("/api/news/<item_id>/agent/jobs/<job_id>/stop")
    def api_agent_job_stop(item_id: str, job_id: str):
        item, _detail = store._load_visible_agent_article(item_id)
        if not item:
            return jsonify({"ok": False, "error": "item_not_found"}), 404
        conn = store.connect()
        try:
            # Serialize with worker claim and finalization before inspecting status.
            conn.execute("BEGIN IMMEDIATE")
            with conn:
                job = store._agent_job_for_item(conn, item_id, job_id)
                if not job:
                    return jsonify({"ok": False, "error": "job_not_found"}), 404
                if job["status"] in {"queued", "pending", "running", "streaming", "stopping"}:
                    stamp = database.now_ts()
                    queued = job["status"] in {"queued", "pending"}
                    conn.execute(
                        "UPDATE agent_jobs SET cancel_requested=1, status=?, finished_at=?, updated_at=? WHERE id=?",
                        ("stopped" if queued else "stopping", stamp if queued else None, stamp, job_id),
                    )
                    conn.execute(
                        "UPDATE agent_messages SET status=?, updated_at=? WHERE job_id=? AND role='assistant'",
                        ("stopped" if queued else "stopping", stamp, job_id),
                    )
                    conn.execute(
                        "UPDATE agent_sessions SET last_activity_at=?, expires_at=?, updated_at=? WHERE id=?",
                        (stamp, agent_expiry_for(stamp), stamp, job["session_id"]),
                    )
                job = conn.execute("SELECT * FROM agent_jobs WHERE id=?", (job_id,)).fetchone()
            worker._terminate_agent_process(job_id)
            with conn:
                # Running/streaming jobs stay stopping until run_agent_job() has
                # finished process and per-worker config cleanup. This preserves the
                # session execution lock through the launch-registration window.
                job = conn.execute("SELECT * FROM agent_jobs WHERE id=?", (job_id,)).fetchone()
            return jsonify({"ok": True, "job": _serialize_agent_job(job)})
        finally:
            conn.close()
