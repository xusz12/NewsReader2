"""Persistent, library-scoped notices; callers own the result transaction."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path


def library_scope(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode()).hexdigest()


def ensure_schema(conn) -> None:
    for table, columns in {
        "agent_sessions": {"db_scope": "TEXT NOT NULL DEFAULT ''"},
        "agent_jobs": {"db_scope": "TEXT NOT NULL DEFAULT ''", "notify_enabled": "INTEGER NOT NULL DEFAULT 0"},
    }.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, declaration in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_agent_sessions_scope ON agent_sessions(db_scope, item_id, status);
        CREATE TABLE IF NOT EXISTS agent_notifications (
            id TEXT PRIMARY KEY, db_scope TEXT NOT NULL, item_id TEXT NOT NULL,
            session_id TEXT NOT NULL, job_id TEXT NOT NULL UNIQUE, message_id INTEGER NOT NULL,
            title TEXT NOT NULL, preview TEXT NOT NULL, kind TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, read_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_agent_notifications_unread
            ON agent_notifications(db_scope, read_at, created_at);
    """)


def record(conn, job_id: str) -> None:
    """Never use the process's current library to attribute a finishing job."""
    row = conn.execute("""
        SELECT j.*, s.url, s.context_json, m.id AS message_id, m.content, m.status AS message_status
        FROM agent_jobs j JOIN agent_sessions s ON s.id=j.session_id AND s.db_scope=j.db_scope
        JOIN agent_messages m ON m.session_id=j.session_id AND m.job_id=j.id AND m.role='assistant'
        WHERE j.id=? AND j.notify_enabled=1 AND j.db_scope<>'' AND j.cancel_requested=0
          AND j.status IN ('succeeded', 'failed', 'interrupted')
    """, (job_id,)).fetchone()
    if not row or (row["status"] == "succeeded" and (not row["content"].strip() or row["message_status"] != "succeeded")):
        return
    import json
    context = json.loads(row["context_json"] or "{}")
    succeeded = row["status"] == "succeeded"
    preview = re.sub(r"\s+", " ", row["content"]).strip()[:160] if succeeded else "本轮未完成"
    conn.execute("""
        INSERT OR IGNORE INTO agent_notifications
          (id, db_scope, item_id, session_id, job_id, message_id, title, preview, kind, error, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (job_id, row["db_scope"], row["item_id"], row["session_id"], job_id, row["message_id"],
          str(context.get("title") or "新闻研究"), preview, "reply" if succeeded else "failed",
          "" if succeeded else row["error"], row["finished_at"] or row["updated_at"]))


def unread(conn, scope: str) -> list[dict]:
    return [dict(row) for row in conn.execute("""
        SELECT id, db_scope, item_id, session_id, job_id, message_id, title, preview, kind, error, created_at
        FROM agent_notifications WHERE db_scope=? AND read_at IS NULL
        ORDER BY created_at DESC, rowid DESC
    """, (scope,))]


def target_session(conn, notice, scope: str):
    return conn.execute("""
        SELECT * FROM agent_sessions WHERE id=? AND item_id=? AND db_scope=?
          AND status IN ('active', 'archived')
    """, (notice["session_id"], notice["item_id"], scope)).fetchone()


def acknowledge(conn, scope: str, ids: list[str], stamp: str, *, dismiss: bool = False) -> None:
    # Only explicit snapshot IDs are updated; a later reply has a different ID.
    for ident in ids:
        row = conn.execute("SELECT * FROM agent_notifications WHERE id=? AND db_scope=?", (ident, scope)).fetchone()
        if not row:
            raise ValueError("notification_not_found")
        if not dismiss:
            session = target_session(conn, row, scope)
            message = conn.execute("""SELECT id FROM agent_messages
                WHERE id=? AND session_id=? AND job_id=? AND role='assistant'""",
                (row["message_id"], row["session_id"], row["job_id"])).fetchone()
            if not session or not message:
                raise ValueError("notification_session_unavailable")
        conn.execute("UPDATE agent_notifications SET read_at=? WHERE id=? AND db_scope=? AND read_at IS NULL",
                     (stamp, ident, scope))
