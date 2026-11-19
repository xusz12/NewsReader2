from __future__ import annotations

import sqlite3
from datetime import datetime

from flask import jsonify, request

from nr import database, reader_items


REMINDER_STATUSES = {"active", "done", "dismissed"}


def parse_reminder_event_date(value: str) -> str:
    normalized = (value or "").strip()
    try:
        return datetime.strptime(normalized, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("invalid_event_date") from exc


def parse_reminder_remind_at(value: str) -> str:
    normalized = (value or "").strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            parsed = datetime.strptime(normalized, fmt)
            return parsed.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    raise ValueError("invalid_remind_at")


def parse_review_reminder_remind_at(value: str) -> str:
    """Strict parser for review reminder remind_at.

    Accepts only the exact frontend format YYYY-MM-DDTHH:MM.
    Rejects trailing characters, seconds, timezone suffixes, non-existent dates,
    24:00, and any format other than the exact 16-character string.
    Returns canonical storage format YYYY-MM-DD HH:MM:SS.
    """
    normalized = (value or "").strip()
    if len(normalized) != 16 or normalized[10] != "T":
        raise ValueError("invalid_remind_at")
    try:
        parsed = datetime.strptime(normalized, "%Y-%m-%dT%H:%M")
    except ValueError as exc:
        raise ValueError("invalid_remind_at") from exc
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def reminder_is_due(remind_at: str | None, status: str | None) -> bool:
    return bool(status == "active" and remind_at and remind_at <= database.now_ts())


def serialize_reminder_rows(rows: list[sqlite3.Row | dict]) -> list[dict]:
    reminders: list[dict] = []
    for row in rows:
        reminder = dict(row)
        reminder["note"] = reminder.get("note") or ""
        reminder["is_due"] = reminder_is_due(reminder.get("remind_at"), reminder.get("status"))
        reminder["item_exists"] = 1 if reminder.get("item_exists") else 0
        reminders.append(reminder)
    return reminders


def load_reminder_summary(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        """
        SELECT
          COUNT(*) AS total,
          SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) AS active_total,
          SUM(CASE WHEN status='active' AND remind_at <= datetime('now', 'localtime') THEN 1 ELSE 0 END) AS due_total,
          SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done_total,
          SUM(CASE WHEN status='dismissed' THEN 1 ELSE 0 END) AS dismissed_total
        FROM news_reminders
        """
    ).fetchone()
    return {
        "total": int((row["total"] if row else 0) or 0),
        "active_total": int((row["active_total"] if row else 0) or 0),
        "due_total": int((row["due_total"] if row else 0) or 0),
        "done_total": int((row["done_total"] if row else 0) or 0),
        "dismissed_total": int((row["dismissed_total"] if row else 0) or 0),
    }


def load_item_reminders(conn: sqlite3.Connection, item_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT nr.*,
               CASE WHEN items.id IS NULL THEN 0 ELSE 1 END AS item_exists
        FROM news_reminders nr
        LEFT JOIN items ON items.id = nr.item_id
        WHERE nr.item_id = ?
        ORDER BY
          CASE
            WHEN nr.status = 'active' AND nr.remind_at <= datetime('now', 'localtime') THEN 0
            WHEN nr.status = 'active' THEN 1
            WHEN nr.status = 'done' THEN 2
            ELSE 3
          END ASC,
          CASE WHEN nr.status = 'active' THEN nr.remind_at ELSE nr.updated_at END ASC,
          nr.id DESC
        """,
        (item_id,),
    ).fetchall()
    return serialize_reminder_rows(rows)


def migrate_news_reminders_review_chain_id(conn: sqlite3.Connection) -> None:
    """Add nullable review_chain_id column to news_reminders for review reminder support."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(news_reminders)").fetchall()}
    if "review_chain_id" not in cols:
        conn.execute("ALTER TABLE news_reminders ADD COLUMN review_chain_id INTEGER DEFAULT NULL")
    idx_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_news_reminders_review_chain_id'"
    ).fetchone()
    if not idx_exists:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_news_reminders_review_chain_id ON news_reminders(review_chain_id)"
        )


def register(app, db):
    @app.get("/api/reminders")
    def api_reminders():
        status = (request.args.get("status") or "active").strip().lower()
        if status not in {"active", "done", "dismissed", "all"}:
            return jsonify({"ok": False, "error": "invalid_status"}), 400

        conn = db.connect()
        try:
            summary = load_reminder_summary(conn)
            where_sql = ""
            args: list[str] = []
            order_by_sql = "nr.updated_at DESC, nr.id DESC"
            if status != "all":
                where_sql = "WHERE nr.status = ?"
                args.append(status)
            if status in {"active", "all"}:
                order_by_sql = """
            CASE
              WHEN nr.status = 'active' AND nr.remind_at <= datetime('now', 'localtime') THEN 0
              WHEN nr.status = 'active' THEN 1
              WHEN nr.status = 'done' THEN 2
              ELSE 3
            END ASC,
            CASE WHEN nr.status = 'active' THEN nr.remind_at ELSE nr.updated_at END ASC,
            nr.id DESC
            """
            rows = conn.execute(
                f"""
                SELECT nr.*,
                       CASE WHEN items.id IS NULL THEN 0 ELSE 1 END AS item_exists
                FROM news_reminders nr
                LEFT JOIN items ON items.id = nr.item_id
                {where_sql}
                ORDER BY {order_by_sql}
                """,
                args,
            ).fetchall()
            reminders = serialize_reminder_rows(rows)
            item_map = reader_items.load_news_item_map(
                conn,
                [reminder["item_id"] for reminder in reminders if reminder.get("item_exists")],
            )
        finally:
            conn.close()

        for reminder in reminders:
            reminder["item"] = item_map.get(reminder.get("item_id"))
        return jsonify({"ok": True, "items": reminders, "summary": summary})

    @app.get("/api/reminders/summary")
    def api_reminders_summary():
        conn = db.connect()
        try:
            summary = load_reminder_summary(conn)
        finally:
            conn.close()
        return jsonify({"ok": True, "summary": summary})

    @app.post("/api/news/<item_id>/reminders")
    def api_news_reminder_create(item_id: str):
        body = request.get_json(silent=True) or {}
        event_title = (body.get("event_title") or "").strip()
        if not event_title:
            return jsonify({"ok": False, "error": "empty_event_title"}), 400
        if len(event_title) > 200:
            return jsonify({"ok": False, "error": "event_title_too_long"}), 400
        try:
            event_date = parse_reminder_event_date(body.get("event_date") or "")
            remind_at = parse_reminder_remind_at(body.get("remind_at") or "")
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        note = body.get("note")
        if note is None:
            note_text = ""
        elif isinstance(note, str):
            note_text = note.strip()
        else:
            return jsonify({"ok": False, "error": "invalid_note"}), 400
        if len(note_text) > 5000:
            return jsonify({"ok": False, "error": "note_too_long"}), 400

        conn = db.connect()
        try:
            item = conn.execute(
                "SELECT id, title, url FROM items WHERE id=?",
                (item_id,),
            ).fetchone()
            if not item:
                return jsonify({"ok": False, "error": "item_not_found"}), 404
            ts = database.now_ts()
            with conn:
                cur = conn.execute(
                    """
                INSERT INTO news_reminders(
                  item_id, item_title_snapshot, item_url_snapshot,
                  event_title, event_date, remind_at, note,
                  status, created_at, updated_at, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, NULL)
                """,
                    (
                        item_id,
                        item["title"] or "",
                        item["url"] or "",
                        event_title,
                        event_date,
                        remind_at,
                        note_text,
                        ts,
                        ts,
                    ),
                )
            reminder = next((row for row in load_item_reminders(conn, item_id) if row["id"] == cur.lastrowid), None)
            summary = load_reminder_summary(conn)
        finally:
            conn.close()
        return jsonify({"ok": True, "reminder": reminder, "summary": summary})

    @app.patch("/api/reminders/<int:reminder_id>")
    def api_reminder_update(reminder_id: int):
        body = request.get_json(silent=True) or {}
        allowed = {"event_title", "event_date", "remind_at", "note", "status"}
        provided = [key for key in allowed if key in body]
        if not provided:
            return jsonify({"ok": False, "error": "missing_fields"}), 400

        conn = db.connect()
        try:
            existing = conn.execute(
                "SELECT * FROM news_reminders WHERE id=?",
                (reminder_id,),
            ).fetchone()
            if not existing:
                return jsonify({"ok": False, "error": "reminder_not_found"}), 404

            event_title = existing["event_title"]
            event_date = existing["event_date"]
            remind_at = existing["remind_at"]
            note_text = existing["note"] or ""
            status = existing["status"]
            completed_at = existing["completed_at"]

            if "event_title" in body:
                event_title = (body.get("event_title") or "").strip()
                if not event_title:
                    return jsonify({"ok": False, "error": "empty_event_title"}), 400
                if len(event_title) > 200:
                    return jsonify({"ok": False, "error": "event_title_too_long"}), 400
            if "event_date" in body:
                try:
                    event_date = parse_reminder_event_date(body.get("event_date") or "")
                except ValueError as exc:
                    return jsonify({"ok": False, "error": str(exc)}), 400
            if "remind_at" in body:
                try:
                    remind_at = parse_reminder_remind_at(body.get("remind_at") or "")
                except ValueError as exc:
                    return jsonify({"ok": False, "error": str(exc)}), 400
            if "note" in body:
                note = body.get("note")
                if note is None:
                    note_text = ""
                elif isinstance(note, str):
                    note_text = note.strip()
                else:
                    return jsonify({"ok": False, "error": "invalid_note"}), 400
                if len(note_text) > 5000:
                    return jsonify({"ok": False, "error": "note_too_long"}), 400
            if "status" in body:
                status = (body.get("status") or "").strip().lower()
                if status not in REMINDER_STATUSES:
                    return jsonify({"ok": False, "error": "invalid_status"}), 400
                if status == "done":
                    completed_at = database.now_ts()
                elif status == "active":
                    completed_at = None

            updated_at = database.now_ts()
            with conn:
                conn.execute(
                    """
                UPDATE news_reminders
                SET event_title=?,
                    event_date=?,
                    remind_at=?,
                    note=?,
                    status=?,
                    updated_at=?,
                    completed_at=?
                WHERE id=?
                """,
                    (
                        event_title,
                        event_date,
                        remind_at,
                        note_text,
                        status,
                        updated_at,
                        completed_at,
                        reminder_id,
                    ),
                )
            updated = conn.execute(
                """
            SELECT nr.*,
                   CASE WHEN items.id IS NULL THEN 0 ELSE 1 END AS item_exists
            FROM news_reminders nr
            LEFT JOIN items ON items.id = nr.item_id
            WHERE nr.id=?
            """,
                (reminder_id,),
            ).fetchone()
            summary = load_reminder_summary(conn)
        finally:
            conn.close()
        return jsonify({"ok": True, "reminder": serialize_reminder_rows([updated])[0], "summary": summary})

    @app.delete("/api/reminders/<int:reminder_id>")
    def api_reminder_delete(reminder_id: int):
        conn = db.connect()
        try:
            existing = conn.execute(
                "SELECT id FROM news_reminders WHERE id=?",
                (reminder_id,),
            ).fetchone()
            if not existing:
                return jsonify({"ok": False, "error": "reminder_not_found"}), 404
            with conn:
                conn.execute("DELETE FROM news_reminders WHERE id=?", (reminder_id,))
            summary = load_reminder_summary(conn)
        finally:
            conn.close()
        return jsonify({"ok": True, "deleted_id": reminder_id, "summary": summary})
