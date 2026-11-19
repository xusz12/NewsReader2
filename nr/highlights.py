from __future__ import annotations

import hashlib
import json
import sqlite3

from flask import jsonify, request

import report_reader
from feed_sources import derive_source_key
from nr import database, reader_items


ARTICLE_HIGHLIGHT_BODY_KIND = "ai_zh"
ARTICLE_HIGHLIGHT_POINTS_BODY_KIND = "ai_points_zh"
ARTICLE_HIGHLIGHT_CONCLUSION_BODY_KIND = "ai_conclusion_zh"
ARTICLE_HIGHLIGHT_TWITTER_DETAIL_BODY_KIND = "twitter_detail"
ARTICLE_HIGHLIGHT_ORIGINAL_BODY_KIND = "original"
ARTICLE_HIGHLIGHT_BODY_KINDS = {
    ARTICLE_HIGHLIGHT_BODY_KIND,
    ARTICLE_HIGHLIGHT_POINTS_BODY_KIND,
    ARTICLE_HIGHLIGHT_CONCLUSION_BODY_KIND,
    ARTICLE_HIGHLIGHT_TWITTER_DETAIL_BODY_KIND,
    ARTICLE_HIGHLIGHT_ORIGINAL_BODY_KIND,
}
ARTICLE_HIGHLIGHT_COLORS = {"yellow", "red", "green", "blue", "pink"}
ARTICLE_HIGHLIGHT_CONTEXT_CHARS = 48
ARTICLE_HIGHLIGHT_ANNOTATION_MAX_LENGTH = 2000
ARTICLE_HIGHLIGHT_UNAVAILABLE_AI_STATUSES = {"pending", "running", "failed"}


def migrate_article_highlights_schema(conn: sqlite3.Connection) -> None:
    table = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='article_highlights'"
    ).fetchone()
    if not table:
        return

    columns = {row[1] for row in conn.execute("PRAGMA table_info(article_highlights)").fetchall()}
    table_sql = (table[0] or "").lower()
    needs_rebuild = (
        any(kind not in table_sql for kind in ARTICLE_HIGHLIGHT_BODY_KINDS)
        or any(f"'{color}'" not in table_sql for color in ARTICLE_HIGHLIGHT_COLORS)
    )
    if needs_rebuild:
        sequence = conn.execute("SELECT seq FROM sqlite_sequence WHERE name='article_highlights'").fetchone()
        conn.execute("DROP INDEX IF EXISTS idx_article_highlights_url")
        conn.execute("DROP INDEX IF EXISTS idx_article_highlights_content_hash")
        conn.execute("ALTER TABLE article_highlights RENAME TO article_highlights_legacy")
        conn.execute(
            """
            CREATE TABLE article_highlights (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              url TEXT NOT NULL,
              body_kind TEXT NOT NULL DEFAULT 'ai_zh'
                CHECK (body_kind IN ('ai_zh', 'ai_points_zh', 'ai_conclusion_zh', 'twitter_detail', 'original')),
              content_hash TEXT NOT NULL,
              start_offset INTEGER NOT NULL CHECK (start_offset >= 0),
              end_offset INTEGER NOT NULL CHECK (end_offset > start_offset),
              selected_text TEXT NOT NULL CHECK (length(selected_text) > 0),
              color TEXT NOT NULL DEFAULT 'yellow'
                CHECK (color IN ('yellow', 'red', 'green', 'blue', 'pink')),
              annotation_text TEXT NOT NULL DEFAULT ''
                CHECK (length(annotation_text) <= 2000),
              prefix TEXT NOT NULL DEFAULT '',
              suffix TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE (url, body_kind, content_hash, start_offset, end_offset)
            )
            """
        )
        legacy_columns = {row[1] for row in conn.execute("PRAGMA table_info(article_highlights_legacy)").fetchall()}
        color_sql = "color" if "color" in legacy_columns else "'yellow'"
        annotation_sql = "COALESCE(annotation_text, '')" if "annotation_text" in legacy_columns else "''"
        conn.execute(
            f"""
            INSERT INTO article_highlights(
              id, url, body_kind, content_hash, start_offset, end_offset,
              selected_text, color, annotation_text, prefix, suffix, created_at, updated_at
            )
            SELECT id, url, body_kind, content_hash, start_offset, end_offset,
                   selected_text, {color_sql}, {annotation_sql}, prefix, suffix, created_at, updated_at
            FROM article_highlights_legacy
            """
        )
        conn.execute("DROP TABLE article_highlights_legacy")
        # Keep deleted IDs retired as well as preserving every surviving record.
        if sequence and not conn.execute(
            "UPDATE sqlite_sequence SET seq=MAX(seq,?) WHERE name='article_highlights'", (sequence[0],)
        ).rowcount:
            conn.execute("INSERT INTO sqlite_sequence(name,seq) VALUES ('article_highlights',?)", (sequence[0],))
        columns = {row[1] for row in conn.execute("PRAGMA table_info(article_highlights)").fetchall()}

    if "color" not in columns:
        conn.execute(
            "ALTER TABLE article_highlights ADD COLUMN color TEXT NOT NULL DEFAULT 'yellow'"
        )
    if "annotation_text" not in columns:
        conn.execute(
            """
            ALTER TABLE article_highlights
            ADD COLUMN annotation_text TEXT NOT NULL DEFAULT ''
              CHECK (length(annotation_text) <= 2000)
            """
        )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_article_highlights_url
        ON article_highlights(url, body_kind, created_at, id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_article_highlights_content_hash
        ON article_highlights(url, body_kind, content_hash)
        """
    )


def article_highlight_content_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def article_highlight_context(body: str, start_offset: int, end_offset: int) -> tuple[str, str]:
    prefix = body[max(0, start_offset - ARTICLE_HIGHLIGHT_CONTEXT_CHARS) : start_offset]
    suffix = body[end_offset : end_offset + ARTICLE_HIGHLIGHT_CONTEXT_CHARS]
    return prefix, suffix


def parse_article_highlight_points(value: object) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [point for point in parsed if isinstance(point, str) and point.strip()]


def load_article_highlight_body(
    conn: sqlite3.Connection,
    url: str,
    body_kind: str = ARTICLE_HIGHLIGHT_BODY_KIND,
    item_id: str | None = None,
) -> tuple[str | None, str | None]:
    if not isinstance(body_kind, str) or body_kind not in ARTICLE_HIGHLIGHT_BODY_KINDS:
        return None, "invalid_body_kind"
    if url.startswith(report_reader.URL_PREFIX):
        if body_kind != ARTICLE_HIGHLIGHT_ORIGINAL_BODY_KIND:
            return None, "body_not_ready"
        item = report_reader.subject(conn, report_reader.ID_PREFIX + url[len(report_reader.URL_PREFIX):])
        if not item or not item["content"]:
            return None, "body_not_ready"
        return item["content"], None
    if body_kind in {ARTICLE_HIGHLIGHT_TWITTER_DETAIL_BODY_KIND, ARTICLE_HIGHLIGHT_ORIGINAL_BODY_KIND}:
        if body_kind == ARTICLE_HIGHLIGHT_TWITTER_DETAIL_BODY_KIND and derive_source_key(url, None, None) != "x":
            item = conn.execute(
                "SELECT source_type FROM items WHERE url=? ORDER BY id LIMIT 1", (url,),
            ).fetchone()
            if not item or item["source_type"] != "twitter":
                return None, "body_not_ready"
        detail_row = conn.execute(
            "SELECT content FROM news_item_details WHERE item_id=?" if item_id else "SELECT content FROM article_details WHERE url=?",
            (item_id or url,),
        ).fetchone()
        detail_body = detail_row["content"] if detail_row else None
        if not isinstance(detail_body, str) or not detail_body.strip():
            return None, "body_not_ready"
        return detail_body, None
    row = conn.execute(
        ("""
        SELECT aa.body_zh, aa.key_points_zh, aa.conclusion_zh, aj.status
        FROM news_item_ai aa LEFT JOIN news_item_ai_jobs aj ON aj.item_id=aa.item_id
        WHERE aa.item_id=?
        """ if item_id else """
        SELECT aa.body_zh, aa.key_points_zh, aa.conclusion_zh, aj.status
        FROM article_ai aa
        LEFT JOIN ai_jobs aj ON aj.url = aa.url
        WHERE aa.url=?
        """),
        (item_id or url,),
    ).fetchone()
    if not row or not isinstance(row["body_zh"], str) or not row["body_zh"].strip():
        return None, "body_not_ready"
    if row["status"] in ARTICLE_HIGHLIGHT_UNAVAILABLE_AI_STATUSES:
        return None, "body_not_ready"
    if body_kind == ARTICLE_HIGHLIGHT_POINTS_BODY_KIND:
        points = parse_article_highlight_points(row["key_points_zh"])
        body = "".join(points)
        return (body, None) if body else (None, "body_not_ready")
    if body_kind == ARTICLE_HIGHLIGHT_CONCLUSION_BODY_KIND:
        body = row["conclusion_zh"] if isinstance(row["conclusion_zh"], str) else ""
        return (body, None) if body.strip() else (None, "body_not_ready")
    return row["body_zh"], None


def find_article_highlight_reanchor_candidates(
    body: str,
    selected_text: str,
    prefix: str,
    suffix: str,
) -> list[tuple[int, int]]:
    candidates: list[tuple[int, int]] = []
    search_from = 0
    while True:
        start_offset = body.find(selected_text, search_from)
        if start_offset < 0:
            break
        end_offset = start_offset + len(selected_text)
        prefix_matches = not prefix or body[max(0, start_offset - len(prefix)) : start_offset] == prefix
        suffix_matches = not suffix or body[end_offset : end_offset + len(suffix)] == suffix
        if prefix_matches and suffix_matches:
            candidates.append((start_offset, end_offset))
        search_from = start_offset + 1
    return candidates


def resolve_article_highlight_row(row: sqlite3.Row, body: str, current_hash: str) -> dict:
    payload = dict(row)
    start_offset = int(row["start_offset"])
    end_offset = int(row["end_offset"])
    selected_text = row["selected_text"]
    resolved: tuple[int, int] | None = None
    if (
        row["content_hash"] == current_hash
        and 0 <= start_offset < end_offset <= len(body)
        and body[start_offset:end_offset] == selected_text
    ):
        resolved = (start_offset, end_offset)
    elif row["content_hash"] != current_hash:
        candidates = find_article_highlight_reanchor_candidates(
            body,
            selected_text,
            row["prefix"] or "",
            row["suffix"] or "",
        )
        if len(candidates) == 1:
            resolved = candidates[0]

    if resolved:
        payload["status"] = "active"
        payload["resolved_start_offset"] = resolved[0]
        payload["resolved_end_offset"] = resolved[1]
    else:
        payload["status"] = "orphan"
        payload["resolved_start_offset"] = None
        payload["resolved_end_offset"] = None
    return payload


def load_article_highlights_payload(
    conn: sqlite3.Connection,
    url: str,
    body_kind: str = ARTICLE_HIGHLIGHT_BODY_KIND,
    item_id: str | None = None,
) -> dict:
    rows = conn.execute(
        """
        SELECT id, url, body_kind, content_hash, start_offset, end_offset,
               selected_text, color, annotation_text, prefix, suffix, created_at, updated_at
        FROM article_highlights
        WHERE url=? AND body_kind=?
        ORDER BY start_offset ASC, id ASC
        """,
        (url, body_kind),
    ).fetchall()
    body, _ = load_article_highlight_body(conn, url, body_kind, item_id)
    if body is None:
        return {
            "body_ready": False,
            "body_kind": body_kind,
            "content_hash": None,
            "highlights": [],
            "highlight_summary": {
                "total": len(rows),
                "active": 0,
                "orphan": 0,
            },
        }

    current_hash = article_highlight_content_hash(body)
    highlights = [resolve_article_highlight_row(row, body, current_hash) for row in rows]
    active_count = sum(1 for highlight in highlights if highlight["status"] == "active")
    orphan_count = len(highlights) - active_count
    return {
        "body_ready": True,
        "body_kind": body_kind,
        "content_hash": current_hash,
        "highlights": highlights,
        "highlight_summary": {
            "total": len(highlights),
            "active": active_count,
            "orphan": orphan_count,
        },
    }


def load_article_highlights_response(
    conn: sqlite3.Connection,
    url: str,
    primary_body_kind: str = ARTICLE_HIGHLIGHT_BODY_KIND,
    item_id: str | None = None,
) -> dict:
    surfaces = {
        body_kind: load_article_highlights_payload(conn, url, body_kind, item_id)
        for body_kind in sorted(ARTICLE_HIGHLIGHT_BODY_KINDS)
    }
    response = dict(surfaces[primary_body_kind])
    response["highlight_surfaces"] = surfaces
    return response


def normalize_article_highlight_annotation(value: object) -> tuple[str | None, str | None]:
    if not isinstance(value, str):
        return None, "invalid_annotation_type"
    annotation_text = value.strip()
    if not annotation_text:
        return None, "empty_annotation"
    if len(annotation_text) > ARTICLE_HIGHLIGHT_ANNOTATION_MAX_LENGTH:
        return None, "annotation_too_long"
    return annotation_text, None


def register(app, db):
    @app.get("/api/news/<item_id>/highlights")
    def api_news_highlights_list(item_id: str):
        body_kind = request.args.get("body_kind", ARTICLE_HIGHLIGHT_BODY_KIND)
        if not isinstance(body_kind, str) or body_kind not in ARTICLE_HIGHLIGHT_BODY_KINDS:
            return jsonify({"ok": False, "error": "invalid_body_kind"}), 400
        conn = db.connect()
        try:
            item = reader_items.reader_subject(conn, item_id)
            if not item:
                return jsonify({"ok": False, "error": "item_not_found"}), 404
            url = (item["url"] or "").strip()
            if not url:
                return jsonify({"ok": False, "error": "missing_url"}), 400
            payload = load_article_highlights_response(conn, url, body_kind, item_id)
        finally:
            conn.close()
        return jsonify({"ok": True, "item_id": item_id, **payload})

    @app.post("/api/news/<item_id>/highlights")
    def api_news_highlight_create(item_id: str):
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"ok": False, "error": "invalid_payload"}), 400
        body_kind = body.get("body_kind", ARTICLE_HIGHLIGHT_BODY_KIND)
        if not isinstance(body_kind, str) or body_kind not in ARTICLE_HIGHLIGHT_BODY_KINDS:
            return jsonify({"ok": False, "error": "invalid_body_kind"}), 400
        color = body.get("color", "yellow")
        if not isinstance(color, str) or color not in ARTICLE_HIGHLIGHT_COLORS:
            return jsonify({"ok": False, "error": "invalid_color"}), 400
        selected_text = body.get("selected_text")
        if not isinstance(selected_text, str) or not selected_text.strip():
            return jsonify({"ok": False, "error": "empty_selected_text"}), 400
        start_offset = body.get("start_offset")
        end_offset = body.get("end_offset")
        if (
            isinstance(start_offset, bool)
            or isinstance(end_offset, bool)
            or not isinstance(start_offset, int)
            or not isinstance(end_offset, int)
            or start_offset < 0
            or end_offset <= start_offset
        ):
            return jsonify({"ok": False, "error": "invalid_offset"}), 400

        conn = db.connect()
        try:
            item = reader_items.reader_subject(conn, item_id)
            if not item:
                return jsonify({"ok": False, "error": "item_not_found"}), 404
            url = (item["url"] or "").strip()
            if not url:
                return jsonify({"ok": False, "error": "missing_url"}), 400
            article_body, body_error = load_article_highlight_body(conn, url, body_kind, item_id)
            if article_body is None:
                return jsonify({"ok": False, "error": body_error or "body_not_ready"}), 409
            if end_offset > len(article_body):
                return jsonify({"ok": False, "error": "invalid_offset"}), 400
            if article_body[start_offset:end_offset] != selected_text:
                return jsonify({"ok": False, "error": "selected_text_mismatch"}), 400

            current_payload = load_article_highlights_payload(conn, url, body_kind, item_id)
            for existing in current_payload["highlights"]:
                if existing["status"] != "active":
                    continue
                existing_start = existing["resolved_start_offset"]
                existing_end = existing["resolved_end_offset"]
                if start_offset < existing_end and end_offset > existing_start:
                    return jsonify({"ok": False, "error": "highlight_overlap"}), 409

            content_hash = article_highlight_content_hash(article_body)
            prefix, suffix = article_highlight_context(article_body, start_offset, end_offset)
            ts = database.now_ts()
            try:
                with conn:
                    cursor = conn.execute(
                        """
                    INSERT INTO article_highlights(
                      url, body_kind, content_hash, start_offset, end_offset,
                      selected_text, color, prefix, suffix, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            url,
                            body_kind,
                            content_hash,
                            start_offset,
                            end_offset,
                            selected_text,
                            color,
                            prefix,
                            suffix,
                            ts,
                            ts,
                        ),
                    )
                    highlight_id = cursor.lastrowid
            except sqlite3.IntegrityError:
                return jsonify({"ok": False, "error": "highlight_exists"}), 409

            payload = load_article_highlights_response(conn, url, body_kind, item_id)
            saved = next(
                (highlight for highlight in payload["highlights"] if highlight["id"] == highlight_id),
                None,
            )
        finally:
            conn.close()

        return (
            jsonify(
                {
                    "ok": True,
                    "item_id": item_id,
                    "highlight": saved,
                    **payload,
                }
            ),
            201,
        )

    @app.patch("/api/news/<item_id>/highlights/<int:highlight_id>")
    def api_news_highlight_update(item_id: str, highlight_id: int):
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"ok": False, "error": "invalid_payload"}), 400
        color = body.get("color")
        if not isinstance(color, str) or color not in ARTICLE_HIGHLIGHT_COLORS:
            return jsonify({"ok": False, "error": "invalid_color"}), 400

        conn = db.connect()
        try:
            item = reader_items.reader_subject(conn, item_id)
            if not item:
                return jsonify({"ok": False, "error": "item_not_found"}), 404
            url = (item["url"] or "").strip()
            if not url:
                return jsonify({"ok": False, "error": "missing_url"}), 400
            existing = conn.execute(
                "SELECT id, body_kind FROM article_highlights WHERE id=? AND url=?",
                (highlight_id, url),
            ).fetchone()
            if not existing:
                return jsonify({"ok": False, "error": "highlight_not_found"}), 404
            with conn:
                conn.execute(
                    "UPDATE article_highlights SET color=?, updated_at=? WHERE id=? AND url=?",
                    (color, database.now_ts(), highlight_id, url),
                )
            payload = load_article_highlights_response(conn, url, existing["body_kind"], item_id)
            updated = next(
                (highlight for highlight in payload["highlights"] if highlight["id"] == highlight_id),
                None,
            )
        finally:
            conn.close()
        return jsonify(
            {
                "ok": True,
                "item_id": item_id,
                "highlight": updated,
                **payload,
            }
        )

    @app.get("/api/news/<item_id>/highlights/<int:highlight_id>/annotation")
    def api_news_highlight_annotation_get(item_id: str, highlight_id: int):
        conn = db.connect()
        try:
            item = reader_items.reader_subject(conn, item_id)
            if not item:
                return jsonify({"ok": False, "error": "item_not_found"}), 404
            url = (item["url"] or "").strip()
            if not url:
                return jsonify({"ok": False, "error": "missing_url"}), 400
            highlight = conn.execute(
                """
            SELECT id, url, body_kind, annotation_text, updated_at
            FROM article_highlights
            WHERE id=? AND url=?
            """,
                (highlight_id, url),
            ).fetchone()
            if not highlight:
                return jsonify({"ok": False, "error": "highlight_not_found"}), 404
        finally:
            conn.close()
        return jsonify(
            {
                "ok": True,
                "item_id": item_id,
                "highlight_id": highlight_id,
                "annotation_text": highlight["annotation_text"] or "",
                "updated_at": highlight["updated_at"],
            }
        )

    @app.put("/api/news/<item_id>/highlights/<int:highlight_id>/annotation")
    @app.patch("/api/news/<item_id>/highlights/<int:highlight_id>/annotation")
    def api_news_highlight_annotation_save(item_id: str, highlight_id: int):
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"ok": False, "error": "invalid_payload"}), 400
        annotation_text, annotation_error = normalize_article_highlight_annotation(
            body.get("annotation_text")
        )
        if annotation_error:
            return jsonify({"ok": False, "error": annotation_error}), 400

        conn = db.connect()
        try:
            item = reader_items.reader_subject(conn, item_id)
            if not item:
                return jsonify({"ok": False, "error": "item_not_found"}), 404
            url = (item["url"] or "").strip()
            if not url:
                return jsonify({"ok": False, "error": "missing_url"}), 400
            existing = conn.execute(
                "SELECT id, body_kind FROM article_highlights WHERE id=? AND url=?",
                (highlight_id, url),
            ).fetchone()
            if not existing:
                return jsonify({"ok": False, "error": "highlight_not_found"}), 404
            with conn:
                conn.execute(
                    """
                UPDATE article_highlights
                SET annotation_text=?, updated_at=?
                WHERE id=? AND url=?
                """,
                    (annotation_text, database.now_ts(), highlight_id, url),
                )
            payload = load_article_highlights_response(conn, url, existing["body_kind"], item_id)
            updated = next(
                (highlight for highlight in payload["highlights"] if highlight["id"] == highlight_id),
                None,
            )
        finally:
            conn.close()
        return jsonify(
            {
                "ok": True,
                "item_id": item_id,
                "highlight": updated,
                **payload,
            }
        )

    @app.delete("/api/news/<item_id>/highlights/<int:highlight_id>/annotation")
    def api_news_highlight_annotation_clear(item_id: str, highlight_id: int):
        conn = db.connect()
        try:
            item = reader_items.reader_subject(conn, item_id)
            if not item:
                return jsonify({"ok": False, "error": "item_not_found"}), 404
            url = (item["url"] or "").strip()
            if not url:
                return jsonify({"ok": False, "error": "missing_url"}), 400
            existing = conn.execute(
                "SELECT id, body_kind FROM article_highlights WHERE id=? AND url=?",
                (highlight_id, url),
            ).fetchone()
            if not existing:
                return jsonify({"ok": False, "error": "highlight_not_found"}), 404
            with conn:
                conn.execute(
                    """
                UPDATE article_highlights
                SET annotation_text='', updated_at=?
                WHERE id=? AND url=?
                """,
                    (database.now_ts(), highlight_id, url),
                )
            payload = load_article_highlights_response(conn, url, existing["body_kind"], item_id)
            updated = next(
                (highlight for highlight in payload["highlights"] if highlight["id"] == highlight_id),
                None,
            )
        finally:
            conn.close()
        return jsonify(
            {
                "ok": True,
                "item_id": item_id,
                "highlight": updated,
                **payload,
            }
        )

    @app.delete("/api/news/<item_id>/highlights/<int:highlight_id>")
    def api_news_highlight_delete(item_id: str, highlight_id: int):
        conn = db.connect()
        try:
            item = reader_items.reader_subject(conn, item_id)
            if not item:
                return jsonify({"ok": False, "error": "item_not_found"}), 404
            url = (item["url"] or "").strip()
            if not url:
                return jsonify({"ok": False, "error": "missing_url"}), 400
            existing = conn.execute(
                "SELECT id, body_kind FROM article_highlights WHERE id=? AND url=?",
                (highlight_id, url),
            ).fetchone()
            if not existing:
                return jsonify({"ok": False, "error": "highlight_not_found"}), 404
            with conn:
                conn.execute("DELETE FROM article_highlights WHERE id=? AND url=?", (highlight_id, url))
            payload = load_article_highlights_response(conn, url, existing["body_kind"], item_id)
        finally:
            conn.close()
        return jsonify(
            {
                "ok": True,
                "item_id": item_id,
                "deleted_id": highlight_id,
                **payload,
            }
        )
