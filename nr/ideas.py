from __future__ import annotations

import re
import sqlite3
import uuid

from flask import jsonify, request

from feed_sources import visible_news_sql
from nr import database, reader_items


def load_idea_rows(conn: sqlite3.Connection, idea_type: str, sort_order: str, selected_ids=None) -> list[dict]:
    ideas: list[dict] = []
    def selected(kind):
        return [key.split(":", 1)[1] for key in selected_ids or [] if key.startswith(kind + ":")]
    def restriction(column, kind):
        return f" AND {column} IN (" + ",".join("?" for _ in selected(kind)) + ")" if selected_ids is not None else ""

    if idea_type in ("all", "article"):
        article_rows = conn.execute(
            f"""
            SELECT items.id, items.source_file, items.item_order, items.published_at,
                   items.date, items.time, items.source, items.source_type,
                   items.source_name, items.title, items.summary, items.url,
                   st.read_at, st.important_at, st.read_later_at, st.read_later_done_at, st.favorite_at,
                   dj.status AS detail_status,
                   dj.last_error AS detail_error,
                   CASE WHEN ad.url IS NULL THEN 0 ELSE 1 END AS detail_ready,
                   an.note AS note,
                   an.note AS note_preview_source,
                   CASE WHEN an.url IS NULL THEN 0 ELSE 1 END AS has_note,
                   aj.status AS ai_status,
                   aj.last_error AS ai_error,
                   CASE WHEN aa.url IS NULL THEN 0 ELSE 1 END AS ai_ready,
                   (
                     SELECT COUNT(*)
                     FROM news_reminders nr
                     WHERE nr.item_id = items.id AND nr.status = 'active'
                   ) AS active_reminder_count,
                   (
                     SELECT COUNT(*)
                     FROM news_reminders nr
                     WHERE nr.item_id = items.id
                       AND nr.status = 'active'
                       AND nr.remind_at <= datetime('now', 'localtime')
                   ) AS due_reminder_count,
                   (
                     SELECT MIN(nr.remind_at)
                     FROM news_reminders nr
                     WHERE nr.item_id = items.id AND nr.status = 'active'
                   ) AS next_remind_at,
                   an.created_at AS idea_created_at,
                   an.updated_at AS idea_updated_at
            FROM article_notes an
            JOIN items ON items.url = an.url
            LEFT JOIN item_state st ON st.item_id = items.id
            LEFT JOIN news_item_detail_jobs dj ON dj.item_id=items.id
            LEFT JOIN news_item_details ad ON ad.item_id=items.id
            LEFT JOIN news_item_ai_jobs aj ON aj.item_id=items.id
            LEFT JOIN news_item_ai aa ON aa.item_id=items.id
            WHERE {visible_news_sql()} {restriction("items.id", "article")}
            """, selected("article"),
        ).fetchall()
        article_items = [dict(row) for row in article_rows]
        article_tags_map = reader_items.load_market_tags_map(conn, [item["url"] for item in article_items if item.get("url")])
        article_items = reader_items.serialize_news_rows(article_items, article_tags_map, conn)
        for item in article_items:
            item["idea_id"] = f"article:{item['id']}"
            item["idea_type"] = "article_note"
            item["idea_context_label"] = "新闻想法"
            item["created_at"] = item.get("idea_created_at") or ""
            item["updated_at"] = item.get("idea_updated_at") or ""
            date_key, date_label = reader_items.derive_ts_date_meta(item.get("updated_at"))
            item["date_key"] = date_key
            item["date_label"] = date_label
            ideas.append(item)

    if idea_type in ("all", "trend"):
        trend_rows = conn.execute(
            f"""
            SELECT mtn.id,
                   mtn.date_key,
                   mtn.tag,
                   mtn.direction,
                   mtn.note,
                   mtn.created_at,
                   mtn.updated_at,
                   mtd.display_name
            FROM market_trend_notes mtn
            LEFT JOIN market_tag_definitions mtd ON mtd.key = mtn.tag
            WHERE 1=1 {restriction("mtn.id", "trend")}
            """, selected("trend"),
        ).fetchall()
        for row in trend_rows:
            direction_label = "看多" if row["direction"] == "bullish" else "看空"
            tag_label = row["display_name"] or row["tag"]
            updated_at = row["updated_at"] or ""
            date_key, date_label = reader_items.derive_ts_date_meta(updated_at)
            ideas.append(
                {
                    "id": None,
                    "idea_id": f"trend:{row['id']}",
                    "idea_type": "trend_note",
                    "idea_context_label": "板块想法",
                    "trend_note_id": row["id"],
                    "trend_date_key": row["date_key"],
                    "tag_key": row["tag"],
                    "tag_label": tag_label,
                    "direction": row["direction"],
                    "direction_label": direction_label,
                    "title": f"{tag_label} · {direction_label}",
                    "summary": f"{row['date_key']} · {tag_label} · {direction_label}",
                    "source": "趋势",
                    "published_at": row["date_key"],
                    "url": "",
                    "note": row["note"],
                    "note_preview": reader_items.build_note_preview(row["note"]),
                    "created_at": row["created_at"] or "",
                    "updated_at": updated_at,
                    "date_key": date_key,
                    "date_label": date_label,
                }
            )

    if idea_type in ("all", "standalone"):
        standalone_rows = conn.execute(
            f"""
            SELECT id, title, note, created_at, updated_at
            FROM standalone_ideas WHERE 1=1 {restriction("id", "standalone")}
            """, selected("standalone"),
        ).fetchall()
        ideas.extend(_serialize_standalone_idea(row) for row in standalone_rows)

    reverse = sort_order != "reverse"
    ideas.sort(
        key=lambda idea: (
            idea.get("updated_at") or "",
            str(idea.get("idea_id") or idea.get("id") or ""),
        ),
        reverse=reverse,
    )
    return ideas


def load_notes_map(conn: sqlite3.Connection, urls: list[str]) -> dict[str, dict]:
    if not urls:
        return {}
    uniq_urls = [u for u in dict.fromkeys(urls) if u]
    if not uniq_urls:
        return {}
    placeholders = ",".join(["?"] * len(uniq_urls))
    rows = conn.execute(
        f"""
        SELECT url, note, created_at, updated_at
        FROM article_notes
        WHERE url IN ({placeholders})
        """,
        uniq_urls,
    ).fetchall()
    return {r["url"]: dict(r) for r in rows}


def load_trend_notes_map(
    conn: sqlite3.Connection,
    keys: list[tuple[str, str, str]],
) -> dict[tuple[str, str, str], list[dict]]:
    if not keys:
        return {}
    uniq_keys = list(dict.fromkeys(keys))
    placeholders = ",".join(["(?, ?, ?)"] * len(uniq_keys))
    args: list[str] = []
    for date_key, tag_key, direction in uniq_keys:
        args.extend([date_key, tag_key, direction])
    rows = conn.execute(
        f"""
        SELECT mtn.id,
               mtn.date_key,
               mtn.tag,
               mtn.direction,
               mtn.note,
               mtn.created_at,
               mtn.updated_at,
               mtd.display_name
        FROM market_trend_notes mtn
        LEFT JOIN market_tag_definitions mtd ON mtd.key = mtn.tag
        WHERE (mtn.date_key, mtn.tag, mtn.direction) IN ({placeholders})
        ORDER BY mtn.updated_at DESC, mtn.id DESC
        """,
        args,
    ).fetchall()
    mapped: dict[tuple[str, str, str], list[dict]] = {}
    for r in rows:
        key = (r["date_key"], r["tag"], r["direction"])
        mapped.setdefault(key, []).append(
            {
                "id": r["id"],
                "date_key": r["date_key"],
                "tag_key": r["tag"],
                "tag": r["display_name"] or r["tag"],
                "direction": r["direction"],
                "note": r["note"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
        )
    return mapped


STANDALONE_IDEA_MAX_LEN = 5000


def _serialize_standalone_idea(row) -> dict:
    updated_at = row["updated_at"] or ""
    date_key, date_label = reader_items.derive_ts_date_meta(updated_at)
    return {
        "id": None,
        "idea_id": f"standalone:{row['id']}",
        "idea_type": "standalone_note",
        "idea_context_label": "独立想法",
        "standalone_id": row["id"],
        "title": row["title"],
        "summary": "",
        "source": "独立",
        "published_at": "",
        "url": "",
        "note": row["note"],
        "note_preview": reader_items.build_note_preview(row["note"]),
        "created_at": row["created_at"] or "",
        "updated_at": updated_at,
        "date_key": date_key,
        "date_label": date_label,
    }


def article_note_entries(conn, url):
    rows = [dict(row) for row in conn.execute("""
        SELECT e.id, e.note, e.created_at, e.updated_at, COALESCE(m.author, 'user') AS author,
               COALESCE(m.quote_text, '') AS quote_text, COALESCE(m.quote_source, '') AS quote_source,
               m.quote_paragraph, COALESCE(m.archive_summary, '') AS archive_summary,
               COALESCE(m.source_session_id, '') AS source_session_id, m.note_id AS metadata_id
        FROM article_note_entries e LEFT JOIN article_note_metadata m ON m.note_id=e.id
        WHERE e.url=? ORDER BY e.created_at, e.rowid
    """, (url,))]
    for row in rows:
        # Standalone archives from the previous version have an unambiguous generated prefix.
        # Never split a legacy aggregate: its individual author boundaries are unknown.
        if row.pop("metadata_id") is None:
            archive = re.fullmatch(r"【Chat 归档｜\d{4}-\d{2}-\d{2} \d{2}:\d{2}】\n(.+)", row["note"], re.S)
            if archive and "\n\n---\n" not in row["note"]:
                row.update(author="agent", archive_summary=archive.group(1))
    legacy = conn.execute("SELECT note, created_at, updated_at FROM article_notes WHERE url=?", (url,)).fetchone()
    # An older app may edit the shared aggregate directly. Respect that edit instead of showing stale comments.
    if rows and legacy and "\n\n---\n".join(row["note"] for row in rows) == legacy["note"]:
        return rows
    return [{"id": "legacy", "author": "user", **dict(legacy)}] if legacy else []


def import_legacy_note(conn, url):
    rows = article_note_entries(conn, url)
    if not rows or rows[0]["id"] == "legacy":
        conn.execute("DELETE FROM article_note_entries WHERE url=?", (url,))
        if rows:
            row = rows[0]
            conn.execute("INSERT INTO article_note_entries VALUES (?, ?, ?, ?, ?)",
                         (uuid.uuid4().hex, url, row["note"], row["created_at"], row["updated_at"]))


def sync_article_notes(conn, url):
    # Read entries directly: an empty entries table after a deletion must not resurrect the aggregate.
    rows = [dict(row) for row in conn.execute("SELECT id, note, created_at, updated_at FROM article_note_entries WHERE url=? ORDER BY created_at, rowid", (url,))]
    if rows:
        conn.execute("""INSERT INTO article_notes(url, note, created_at, updated_at) VALUES (?, ?, ?, ?)
                        ON CONFLICT(url) DO UPDATE SET note=excluded.note, updated_at=excluded.updated_at""",
                     (url, "\n\n---\n".join(row["note"] for row in rows), rows[0]["created_at"], database.now_ts()))
    else:
        conn.execute("DELETE FROM article_notes WHERE url=?", (url,))


def article_notes_response(conn, item_id, url):
    note = conn.execute("SELECT note, created_at, updated_at FROM article_notes WHERE url=?", (url,)).fetchone()
    notes = article_note_entries(conn, url)
    return {"ok": True, "item_id": item_id, "notes": notes, "note_count": len(notes),
            "note": dict(note) if note else None, "has_note": int(bool(note)),
            "note_preview": reader_items.build_note_preview(note["note"] if note else None)}


def register(app, db):
    @app.post("/api/standalone-ideas")
    def api_standalone_idea_create():
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return jsonify(ok=False, error="invalid_payload"), 400
        title = body.get("title", "")
        if not isinstance(title, str) or len(title.strip()) > 200:
            return jsonify(ok=False, error="invalid_idea_title", message="标题最多 200 个字符。"), 400
        raw_note = body.get("note")
        if not isinstance(raw_note, str):
            return jsonify({"ok": False, "error": "invalid_note"}), 400
        note_text = raw_note.strip()
        if not note_text:
            return jsonify({"ok": False, "error": "empty_note"}), 400
        if len(note_text) > STANDALONE_IDEA_MAX_LEN:
            return jsonify({"ok": False, "error": "note_too_long"}), 400
        ts = database.now_ts()
        conn = db.connect()
        try:
            with conn:
                cur = conn.execute(
                    "INSERT INTO standalone_ideas(title, note, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (title.strip(), note_text, ts, ts),
                )
            idea_id = cur.lastrowid
            row = conn.execute(
                "SELECT id, title, note, created_at, updated_at FROM standalone_ideas WHERE id=?",
                (idea_id,),
            ).fetchone()
        finally:
            conn.close()
        return jsonify({"ok": True, "idea": _serialize_standalone_idea(row)})

    @app.patch("/api/standalone-ideas/<int:idea_id>")
    def api_standalone_idea_update(idea_id: int):
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return jsonify(ok=False, error="invalid_payload"), 400
        title = body.get("title")
        if "title" in body and (not isinstance(title, str) or len(title.strip()) > 200):
            return jsonify(ok=False, error="invalid_idea_title", message="标题最多 200 个字符。"), 400
        raw_note = body.get("note")
        if not isinstance(raw_note, str):
            return jsonify({"ok": False, "error": "invalid_note"}), 400
        note_text = raw_note.strip()
        if not note_text:
            return jsonify({"ok": False, "error": "empty_note"}), 400
        if len(note_text) > STANDALONE_IDEA_MAX_LEN:
            return jsonify({"ok": False, "error": "note_too_long"}), 400
        conn = db.connect()
        try:
            existing = conn.execute(
                "SELECT id FROM standalone_ideas WHERE id=?",
                (idea_id,),
            ).fetchone()
            if not existing:
                return jsonify({"ok": False, "error": "idea_not_found"}), 404
            with conn:
                conn.execute(
                    "UPDATE standalone_ideas SET title=COALESCE(?, title), note=?, updated_at=? WHERE id=?",
                    (title.strip() if title is not None else None, note_text, database.now_ts(), idea_id),
                )
            row = conn.execute(
                "SELECT id, title, note, created_at, updated_at FROM standalone_ideas WHERE id=?",
                (idea_id,),
            ).fetchone()
        finally:
            conn.close()
        return jsonify({"ok": True, "idea": _serialize_standalone_idea(row)})

    @app.delete("/api/standalone-ideas/<int:idea_id>")
    def api_standalone_idea_delete(idea_id: int):
        conn = db.connect()
        try:
            existing = conn.execute(
                "SELECT id FROM standalone_ideas WHERE id=?",
                (idea_id,),
            ).fetchone()
            if not existing:
                return jsonify({"ok": False, "error": "idea_not_found"}), 404
            with conn:
                conn.execute("DELETE FROM standalone_ideas WHERE id=?", (idea_id,))
        finally:
            conn.close()
        return jsonify({"ok": True})

    @app.post("/api/news/<item_id>/notes")
    @app.delete("/api/news/<item_id>/notes/<note_id>")
    def api_article_note_entries(item_id, note_id=None):
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return jsonify(ok=False, error="invalid_payload"), 400
        text = body.get("note")
        if request.method == "POST" and (not isinstance(text, str) or not text.strip() or len(text) > 50000):
            return jsonify(ok=False, error="invalid_note"), 400
        quote = body.get("quote")
        if request.method == "POST" and quote is not None:
            if (not isinstance(quote, dict) or not isinstance(quote.get("text"), str)
                    or not quote["text"].strip() or len(quote["text"]) > 6000
                    or quote.get("source") not in {"original", "translated", "conclusion", "points"}
                    or (quote.get("paragraph") is not None and
                        (type(quote["paragraph"]) is not int or not 1 <= quote["paragraph"] <= 100000))):
                return jsonify(ok=False, error="invalid_quote"), 400
        conn = db.connect()
        try:
            item = reader_items.reader_subject(conn, item_id)
            if not item:
                return jsonify(ok=False, error="item_not_found"), 404
            url = item["url"]
            if not url:
                return jsonify(ok=False, error="missing_url"), 400
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                if request.method == "POST":
                    import_legacy_note(conn, url)
                    ts = database.now_ts()
                    note_id = uuid.uuid4().hex
                    conn.execute("INSERT INTO article_note_entries VALUES (?, ?, ?, ?, ?)", (note_id, url, text.strip(), ts, ts))
                    conn.execute("""INSERT INTO article_note_metadata(note_id, author, quote_text, quote_source, quote_paragraph)
                                VALUES (?, 'user', ?, ?, ?)""",
                                 (note_id, quote["text"].strip() if quote else "", quote["source"] if quote else "",
                                  quote.get("paragraph") if quote else None))
                elif note_id == "legacy":
                    current = article_note_entries(conn, url)
                    if current and current[0]["id"] != "legacy":
                        return jsonify(ok=False, error="note_changed"), 409
                    conn.execute("DELETE FROM article_note_entries WHERE url=?", (url,))
                    conn.execute("DELETE FROM article_notes WHERE url=?", (url,))
                else:
                    if not any(row["id"] == note_id for row in article_note_entries(conn, url)):
                        return jsonify(ok=False, error="note_not_found"), 404
                    deleted = conn.execute("DELETE FROM article_note_entries WHERE id=? AND url=?", (note_id, url))
                    if not deleted.rowcount:
                        return jsonify(ok=False, error="note_not_found"), 404
                sync_article_notes(conn, url)
            return jsonify(article_notes_response(conn, item_id, url))
        finally:
            conn.close()

    @app.put("/api/news/<item_id>/note")
    def api_news_note_upsert(item_id: str):
        body = request.get_json(silent=True) or {}
        raw_note = body.get("note")
        if raw_note is None:
            return jsonify({"ok": False, "error": "missing_note"}), 400
        if not isinstance(raw_note, str):
            return jsonify({"ok": False, "error": "invalid_note"}), 400
        note_text = raw_note.strip()

        conn = db.connect()
        try:
            item = conn.execute("SELECT id, url FROM items WHERE id=?", (item_id,)).fetchone()
            if not item:
                return jsonify({"ok": False, "error": "item_not_found"}), 404
            url = (item["url"] or "").strip()
            if not url:
                return jsonify({"ok": False, "error": "missing_url"}), 400

            with conn:
                conn.execute("DELETE FROM article_note_entries WHERE url=?", (url,))
                if note_text:
                    ts = database.now_ts()
                    conn.execute(
                        """
                    INSERT INTO article_notes(url, note, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(url) DO UPDATE SET
                      note=excluded.note,
                      updated_at=excluded.updated_at
                    """,
                        (url, note_text, ts, ts),
                    )
                else:
                    conn.execute("DELETE FROM article_notes WHERE url=?", (url,))

            saved = conn.execute(
                "SELECT note, created_at, updated_at FROM article_notes WHERE url=?",
                (url,),
            ).fetchone()
        finally:
            conn.close()

        return jsonify(
            {
                "ok": True,
                "item_id": item_id,
                "has_note": 1 if saved else 0,
                "note": (dict(saved) if saved else None),
                "note_preview": reader_items.build_note_preview(saved["note"] if saved else None),
            }
        )
