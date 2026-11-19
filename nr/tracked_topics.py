from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from feed_sources import visible_news_sql
from flask import jsonify, request

from nr import configuration, database, reader_items, tracked_rules


def serialize_tracked_topic_row(row: sqlite3.Row | dict) -> dict:
    topic = dict(row)
    topic["description"] = topic.get("description") or ""
    topic["scope"] = topic.get("scope") or "important"
    topic["active"] = 1 if topic.get("active") else 0
    topic["keywords"] = tracked_rules.normalize_keyword_list(topic.get("keywords_json") or "[]")
    topic["exclude_keywords"] = tracked_rules.normalize_keyword_list(topic.get("exclude_keywords_json") or "[]")
    topic["rules"] = tracked_rules.tracked_rules_from_topic(topic)
    topic["rules_json"] = json.dumps(topic["rules"], ensure_ascii=False)
    topic["visible_item_count"] = int(topic.get("visible_item_count") or 0)
    topic["hidden_item_count"] = int(topic.get("hidden_item_count") or 0)
    topic["latest_published_at"] = topic.get("latest_published_at") or ""
    topic["latest_matched_at"] = topic.get("latest_matched_at") or ""
    return topic


def load_tracked_topic_choices(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, title
        FROM tracked_topics
        WHERE active = 1
        ORDER BY updated_at DESC, id DESC
        """
    ).fetchall()
    return [{"id": row["id"], "title": row["title"]} for row in rows]


def load_tracked_topics(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT tt.*,
               (
                 SELECT COUNT(*)
                 FROM tracked_topic_items tti
                 WHERE tti.topic_id = tt.id
                   AND tti.hidden_at IS NULL
                   AND tti.item_id NOT LIKE 'trend_note:%'
                   AND EXISTS (
                     SELECT 1 FROM items visible_item
                     WHERE visible_item.id = tti.item_id
                       AND is_bloomberg_video_url(visible_item.url) = 0
                   )
               ) AS visible_item_count,
               (
                 SELECT COUNT(*)
                 FROM tracked_topic_items tti
                 WHERE tti.topic_id = tt.id
                   AND tti.hidden_at IS NOT NULL
                   AND tti.item_id NOT LIKE 'trend_note:%'
                   AND EXISTS (
                     SELECT 1 FROM items visible_item
                     WHERE visible_item.id = tti.item_id
                       AND is_bloomberg_video_url(visible_item.url) = 0
                   )
               ) AS hidden_item_count,
               (
                 SELECT MAX(items.published_at)
                 FROM tracked_topic_items tti
                 JOIN items ON items.id = tti.item_id
                 WHERE tti.topic_id = tt.id AND tti.hidden_at IS NULL
                   AND is_bloomberg_video_url(items.url) = 0
               ) AS latest_published_at,
               (
                 SELECT MAX(tti.updated_at)
                 FROM tracked_topic_items tti
                 WHERE tti.topic_id = tt.id
                   AND tti.item_id NOT LIKE 'trend_note:%'
                   AND EXISTS (
                     SELECT 1 FROM items visible_item
                     WHERE visible_item.id = tti.item_id
                       AND is_bloomberg_video_url(visible_item.url) = 0
                   )
               ) AS latest_matched_at
        FROM tracked_topics tt
        ORDER BY tt.updated_at DESC, tt.id DESC
        """
    ).fetchall()
    return [serialize_tracked_topic_row(row) for row in rows]


def load_tracked_topic(conn: sqlite3.Connection, topic_id: int) -> dict | None:
    row = conn.execute(
        """
        SELECT tt.*,
               (
                 SELECT COUNT(*)
                 FROM tracked_topic_items tti
                 WHERE tti.topic_id = tt.id
                   AND tti.hidden_at IS NULL
                   AND tti.item_id NOT LIKE 'trend_note:%'
                   AND EXISTS (
                     SELECT 1 FROM items visible_item
                     WHERE visible_item.id = tti.item_id
                       AND is_bloomberg_video_url(visible_item.url) = 0
                   )
               ) AS visible_item_count,
               (
                 SELECT COUNT(*)
                 FROM tracked_topic_items tti
                 WHERE tti.topic_id = tt.id
                   AND tti.hidden_at IS NOT NULL
                   AND tti.item_id NOT LIKE 'trend_note:%'
                   AND EXISTS (
                     SELECT 1 FROM items visible_item
                     WHERE visible_item.id = tti.item_id
                       AND is_bloomberg_video_url(visible_item.url) = 0
                   )
               ) AS hidden_item_count,
               (
                 SELECT MAX(items.published_at)
                 FROM tracked_topic_items tti
                 JOIN items ON items.id = tti.item_id
                 WHERE tti.topic_id = tt.id AND tti.hidden_at IS NULL
                   AND is_bloomberg_video_url(items.url) = 0
               ) AS latest_published_at,
               (
                 SELECT MAX(tti.updated_at)
                 FROM tracked_topic_items tti
                 WHERE tti.topic_id = tt.id
                   AND tti.item_id NOT LIKE 'trend_note:%'
                   AND EXISTS (
                     SELECT 1 FROM items visible_item
                     WHERE visible_item.id = tti.item_id
                       AND is_bloomberg_video_url(visible_item.url) = 0
                   )
               ) AS latest_matched_at
        FROM tracked_topics tt
        WHERE tt.id = ?
        """,
        (topic_id,),
    ).fetchone()
    if not row:
        return None
    return serialize_tracked_topic_row(row)


def tracked_topic_item_status(conn: sqlite3.Connection, topic_id: int, item_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id, hidden_at, manual_added_at, match_method, score, reason
        FROM tracked_topic_items
        WHERE topic_id = ? AND item_id = ?
        """,
        (topic_id, item_id),
    ).fetchone()


def tracked_topic_candidate_rows(
    conn: sqlite3.Connection,
    *,
    scope: str,
    created_after: str | None = None,
    date_after: str | None = None,
) -> list[dict]:
    where = [visible_news_sql()]
    args: list[object] = []
    if scope == "important":
        where.append("st.important_at IS NOT NULL")
    if created_after:
        where.append("items.created_at >= ?")
        args.append(created_after)
    if date_after:
        where.append(f"{reader_items.ITEM_DATE_SQL} >= ?")
        args.append(date_after)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    news_rows = conn.execute(
        f"""
        SELECT items.id,
               items.url,
               items.title,
               items.summary,
               items.published_at,
               items.date,
               aa.key_points_zh,
               aa.conclusion_zh,
               aa.body_zh,
               an.note
        FROM items
        LEFT JOIN item_state st ON st.item_id = items.id
        LEFT JOIN news_item_ai aa ON aa.item_id=items.id
        LEFT JOIN article_notes an ON an.url = items.url
        {where_sql}
        ORDER BY items.published_at DESC, items.id DESC
        """,
        args,
    ).fetchall()
    return [dict(row) for row in news_rows]


def upsert_tracked_topic_match(
    conn: sqlite3.Connection,
    *,
    topic_id: int,
    item_row: sqlite3.Row | dict,
    match_method: str,
    score: float,
    reason: str,
    manual_added_at: str | None = None,
    clear_hidden: bool = False,
) -> bool:
    ts = database.now_ts()
    item = dict(item_row)
    existing = tracked_topic_item_status(conn, topic_id, str(item["id"]))
    created_or_restored = not existing
    if existing and existing["hidden_at"] and not manual_added_at and not clear_hidden:
        return False
    if existing and existing["manual_added_at"] and not manual_added_at:
        return False
    if existing and existing["hidden_at"] and (manual_added_at or clear_hidden):
        created_or_restored = True

    hidden_value = None if (manual_added_at or clear_hidden) else (existing["hidden_at"] if existing else None)
    conn.execute(
        """
        INSERT INTO tracked_topic_items(
          topic_id, item_id, item_url, match_method, score, reason,
          hidden_at, manual_added_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(topic_id, item_id) DO UPDATE SET
          item_url=excluded.item_url,
          match_method=excluded.match_method,
          score=excluded.score,
          reason=excluded.reason,
          hidden_at=excluded.hidden_at,
          manual_added_at=COALESCE(excluded.manual_added_at, tracked_topic_items.manual_added_at),
          updated_at=excluded.updated_at
        """,
        (
            topic_id,
            str(item["id"]),
            item.get("url") or "",
            match_method,
            score,
            reason[:200],
            hidden_value,
            manual_added_at,
            ts,
            ts,
        ),
    )
    return created_or_restored or bool(manual_added_at)


def run_tracked_topic_match(
    conn: sqlite3.Connection,
    *,
    topic: dict,
    candidate_rows: list[sqlite3.Row],
    preserve_last_incremental: bool = False,
    replace_existing_auto_matches: bool = False,
) -> int:
    topic_id = int(topic["id"])
    rules = topic.get("rules") if isinstance(topic.get("rules"), dict) else tracked_rules.tracked_rules_from_topic(topic)
    matched = 0
    matched_ids: set[str] = set()
    candidate_ids = {str(dict(row)["id"]) for row in candidate_rows}
    for row in candidate_rows:
        ok, score, reason = tracked_rules.match_tracked_topic_row(row, rules)
        if not ok:
            continue
        matched_ids.add(str(dict(row)["id"]))
        if upsert_tracked_topic_match(
            conn,
            topic_id=topic_id,
            item_row=row,
            match_method="keyword",
            score=score,
            reason=reason,
        ):
            matched += 1
    if replace_existing_auto_matches and candidate_ids:
        placeholders = ",".join("?" for _ in candidate_ids)
        args: list[object] = [topic_id, *candidate_ids]
        sql = f"""
            DELETE FROM tracked_topic_items
            WHERE topic_id = ?
              AND match_method = 'keyword'
              AND manual_added_at IS NULL
              AND hidden_at IS NULL
              AND item_id IN ({placeholders})
        """
        if matched_ids:
            matched_placeholders = ",".join("?" for _ in matched_ids)
            sql += f" AND item_id NOT IN ({matched_placeholders})"
            args.extend(matched_ids)
        conn.execute(sql, args)
    if not preserve_last_incremental:
        conn.execute(
            "UPDATE tracked_topics SET last_incremental_at=?, updated_at=? WHERE id=?",
            (database.now_ts(), database.now_ts(), topic_id),
        )
    return len(matched_ids) if replace_existing_auto_matches else matched


def run_tracked_topics_incremental(conn: sqlite3.Connection) -> int:
    topics = [topic for topic in load_tracked_topics(conn) if topic["active"]]
    total_matched = 0
    for topic in topics:
        candidate_rows = tracked_topic_candidate_rows(
            conn,
            scope=topic["scope"],
            created_after=topic.get("last_incremental_at") or topic.get("created_at"),
        )
        total_matched += run_tracked_topic_match(conn, topic=topic, candidate_rows=candidate_rows)
    return total_matched


def tracked_topic_timeline_items(conn: sqlite3.Connection, topic_id: int) -> list[dict]:
    tracked_rows = conn.execute(
        """
        SELECT item_id, item_url, match_method, score, reason, manual_added_at, updated_at AS tracked_updated_at
        FROM tracked_topic_items
        WHERE topic_id = ?
          AND hidden_at IS NULL
          AND item_id NOT LIKE 'trend_note:%'
        """,
        (topic_id,),
    ).fetchall()
    news_ids = [row["item_id"] for row in tracked_rows]

    news_map: dict[str, dict] = {}
    if news_ids:
        placeholders = ",".join(["?"] * len(news_ids))
        rows = conn.execute(
            f"""
            SELECT items.id,
                   items.source_file,
                   items.item_order,
                   items.published_at,
                   items.date,
                   items.time,
                   items.source,
                   items.source_type,
                   items.source_name,
                   items.title,
                   items.summary,
                   items.url,
                   st.read_at,
                   st.important_at,
                   st.read_later_at,
                   st.read_later_done_at,
                   st.favorite_at,
                   dj.status AS detail_status,
                   dj.last_error AS detail_error,
                   CASE WHEN ad.url IS NULL THEN 0 ELSE 1 END AS detail_ready,
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
                   ) AS next_remind_at
            FROM items
            LEFT JOIN item_state st ON st.item_id = items.id
            LEFT JOIN news_item_detail_jobs dj ON dj.item_id=items.id
            LEFT JOIN news_item_details ad ON ad.item_id=items.id
            LEFT JOIN article_notes an ON an.url = items.url
            LEFT JOIN news_item_ai_jobs aj ON aj.item_id=items.id
            LEFT JOIN news_item_ai aa ON aa.item_id=items.id
            WHERE items.id IN ({placeholders})
              AND {visible_news_sql()}
            """,
            news_ids,
        ).fetchall()
        urls = [row["url"] for row in rows if row["url"]]
        market_tags_map = reader_items.load_market_tags_map(conn, urls)
        serialized = reader_items.serialize_news_rows([dict(row) for row in rows], market_tags_map, conn)
        for item in serialized:
            item["entry_type"] = "news"
            item["tracked_item_id"] = str(item["id"])
            item["tracked_sort_ts"] = item.get("published_at") or ""
            news_map[str(item["id"])] = item

    items: list[dict] = []
    for tracked_row in tracked_rows:
        tracked_item_id = str(tracked_row["item_id"])
        item = news_map.get(tracked_item_id)
        if not item:
            continue
        item = dict(item)
        item["tracked_match_method"] = tracked_row["match_method"] or "keyword"
        item["tracked_score"] = float(tracked_row["score"] or 0)
        item["tracked_reason"] = tracked_row["reason"] or ""
        item["tracked_manual_added_at"] = tracked_row["manual_added_at"] or ""
        item["tracked_updated_at"] = tracked_row["tracked_updated_at"] or ""
        items.append(item)
    items.sort(
        key=lambda item: (
            item.get("tracked_sort_ts") or item.get("published_at") or "",
            str(item.get("tracked_item_id") or item.get("id") or ""),
        ),
        reverse=True,
    )
    return items


def register(app, db_conn):
    def parse_tracked_topic_body(body: dict, *, partial: bool = False) -> dict:
        payload: dict[str, object] = {}
        if not partial or "title" in body:
            title = " ".join(str(body.get("title") or "").split()).strip()
            if not title:
                raise ValueError("empty_title")
            if len(title) > 120:
                raise ValueError("title_too_long")
            payload["title"] = title
        if not partial or "description" in body:
            description = str(body.get("description") or "").strip()
            if len(description) > 1000:
                raise ValueError("description_too_long")
            payload["description"] = description
        rules_touched = any(
            key in body
            for key in (
                "strong_phrases",
                "core_terms",
                "context_terms",
                "exclude_terms",
                "required_terms",
                "threshold",
                "candidate_threshold",
                "title_weight",
                "note_weight",
                "summary_weight",
                "content_weight",
                "strong_score",
                "core_score",
                "context_score",
                "exclude_penalty",
                "keywords",
                "exclude_keywords",
            )
        )
        if not partial or rules_touched:
            default_rule_params = None if partial else configuration.current_runtime_settings()["tracked"]["default_rule_params"]
            incoming_rules: dict[str, object] = {**(default_rule_params or {})}
            incoming_rules["strong_phrases"] = body.get("strong_phrases")
            incoming_rules["core_terms"] = body.get("core_terms", body.get("keywords"))
            incoming_rules["context_terms"] = body.get("context_terms")
            incoming_rules["exclude_terms"] = body.get("exclude_terms", body.get("exclude_keywords"))
            incoming_rules["required_terms"] = body.get("required_terms")
            for key in (
                "threshold",
                "candidate_threshold",
                "title_weight",
                "note_weight",
                "summary_weight",
                "content_weight",
                "strong_score",
                "core_score",
                "context_score",
                "exclude_penalty",
            ):
                if key in body:
                    incoming_rules[key] = body.get(key)
            rules = tracked_rules.normalize_tracked_rules(incoming_rules)
            if not rules["strong_phrases"] and not rules["core_terms"]:
                raise ValueError("empty_rules")
            payload["rules"] = rules
            payload["keywords"] = list(rules["core_terms"])
            payload["exclude_keywords"] = list(rules["exclude_terms"])
        if not partial or "scope" in body:
            payload["scope"] = tracked_rules.tracked_scope_value(body.get("scope"))
        if not partial or "active" in body:
            active = body.get("active", True)
            if not isinstance(active, bool):
                raise ValueError("invalid_active")
            payload["active"] = active
        return payload

    @app.get("/api/tracked-topics")
    def api_tracked_topics():
        conn = db_conn()
        try:
            topics = load_tracked_topics(conn)
        finally:
            conn.close()
        return jsonify({"ok": True, "items": topics})

    @app.post("/api/tracked-topics")
    def api_tracked_topics_create():
        body = request.get_json(silent=True) or {}
        try:
            payload = parse_tracked_topic_body(body, partial=False)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        ts = database.now_ts()
        conn = db_conn()
        try:
            with conn:
                cur = conn.execute(
                    """
                INSERT INTO tracked_topics(
                  title, description, keywords_json, exclude_keywords_json, rules_json,
                  scope, active, last_incremental_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        payload["title"],
                        payload.get("description") or "",
                        json.dumps(payload["keywords"], ensure_ascii=False),
                        json.dumps(payload["exclude_keywords"], ensure_ascii=False),
                        json.dumps(payload["rules"], ensure_ascii=False),
                        payload["scope"],
                        1 if payload["active"] else 0,
                        ts,
                        ts,
                        ts,
                    ),
                )
                topic = load_tracked_topic(conn, int(cur.lastrowid))
        finally:
            conn.close()
        return jsonify({"ok": True, "topic": topic})

    @app.patch("/api/tracked-topics/<int:topic_id>")
    def api_tracked_topics_update(topic_id: int):
        body = request.get_json(silent=True) or {}
        try:
            payload = parse_tracked_topic_body(body, partial=True)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        if not payload:
            return jsonify({"ok": False, "error": "empty_patch"}), 400

        conn = db_conn()
        try:
            existing = load_tracked_topic(conn, topic_id)
            if not existing:
                return jsonify({"ok": False, "error": "topic_not_found"}), 404
            title = payload.get("title", existing["title"])
            description = payload.get("description", existing["description"])
            rules = payload.get("rules", existing["rules"])
            keywords = payload.get("keywords", existing["keywords"])
            exclude_keywords = payload.get("exclude_keywords", existing["exclude_keywords"])
            scope = payload.get("scope", existing["scope"])
            active = payload.get("active", bool(existing["active"]))
            ts = database.now_ts()
            with conn:
                conn.execute(
                    """
                UPDATE tracked_topics
                SET title=?,
                    description=?,
                    keywords_json=?,
                    exclude_keywords_json=?,
                    rules_json=?,
                    scope=?,
                    active=?,
                    updated_at=?
                WHERE id=?
                """,
                    (
                        title,
                        description,
                        json.dumps(keywords, ensure_ascii=False),
                        json.dumps(exclude_keywords, ensure_ascii=False),
                        json.dumps(rules, ensure_ascii=False),
                        scope,
                        1 if active else 0,
                        ts,
                        topic_id,
                    ),
                )
                topic = load_tracked_topic(conn, topic_id)
        finally:
            conn.close()
        return jsonify({"ok": True, "topic": topic})

    @app.delete("/api/tracked-topics/<int:topic_id>")
    def api_tracked_topics_delete(topic_id: int):
        conn = db_conn()
        try:
            existing = load_tracked_topic(conn, topic_id)
            if not existing:
                return jsonify({"ok": False, "error": "topic_not_found"}), 404
            with conn:
                conn.execute("DELETE FROM tracked_topic_items WHERE topic_id=?", (topic_id,))
                conn.execute("DELETE FROM tracked_topics WHERE id=?", (topic_id,))
        finally:
            conn.close()
        return jsonify({"ok": True, "deleted_id": topic_id})

    @app.get("/api/tracked-topics/<int:topic_id>/items")
    def api_tracked_topic_items(topic_id: int):
        conn = db_conn()
        try:
            topic = load_tracked_topic(conn, topic_id)
            if not topic:
                return jsonify({"ok": False, "error": "topic_not_found"}), 404
            items = tracked_topic_timeline_items(conn, topic_id)
        finally:
            conn.close()
        return jsonify({"ok": True, "topic": topic, "items": items, "total": len(items)})

    @app.post("/api/tracked-topics/<int:topic_id>/backfill")
    def api_tracked_topic_backfill(topic_id: int):
        body = request.get_json(silent=True) or {}
        try:
            mode = tracked_rules.tracked_backfill_mode_value(body.get("mode"))
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        conn = db_conn()
        try:
            topic = load_tracked_topic(conn, topic_id)
            if not topic:
                return jsonify({"ok": False, "error": "topic_not_found"}), 404
            date_after = None if mode in {"all_important", "all_news"} else datetime.now().strftime("%Y-%m-%d")
            if mode == "recent_important":
                date_after = (datetime.now().date().fromordinal(datetime.now().date().toordinal() - 179)).strftime("%Y-%m-%d")
            candidate_rows = tracked_topic_candidate_rows(
                conn,
                scope="all" if mode == "all_news" else "important",
                date_after=date_after,
            )
            with conn:
                matched = run_tracked_topic_match(
                    conn,
                    topic=topic,
                    candidate_rows=candidate_rows,
                    replace_existing_auto_matches=True,
                )
                topic = load_tracked_topic(conn, topic_id)
                items = tracked_topic_timeline_items(conn, topic_id)
        finally:
            conn.close()
        return jsonify({"ok": True, "topic": topic, "items": items, "matched_count": matched, "mode": mode})

    @app.post("/api/tracked-topics/<int:topic_id>/items")
    def api_tracked_topic_item_create(topic_id: int):
        body = request.get_json(silent=True) or {}
        item_id = str(body.get("item_id") or "").strip()
        if not item_id:
            return jsonify({"ok": False, "error": "missing_item_id"}), 400
        if item_id.startswith("trend_note:"):
            return jsonify({"ok": False, "error": "invalid_item_id"}), 400

        conn = db_conn()
        try:
            topic = load_tracked_topic(conn, topic_id)
            if not topic:
                return jsonify({"ok": False, "error": "topic_not_found"}), 404
            item = conn.execute("SELECT id, url FROM items WHERE id=?", (item_id,)).fetchone()
            if not item:
                return jsonify({"ok": False, "error": "item_not_found"}), 404
            with conn:
                upsert_tracked_topic_match(
                    conn,
                    topic_id=topic_id,
                    item_row=item,
                    match_method="manual",
                    score=0,
                    reason="手动加入",
                    manual_added_at=database.now_ts(),
                    clear_hidden=True,
                )
                conn.execute(
                    "UPDATE tracked_topics SET updated_at=? WHERE id=?",
                    (database.now_ts(), topic_id),
                )
                topic = load_tracked_topic(conn, topic_id)
            item_row = tracked_topic_item_status(conn, topic_id, item_id)
        finally:
            conn.close()
        return jsonify({"ok": True, "topic": topic, "item_id": item_id, "tracked_item": dict(item_row) if item_row else None})

    @app.patch("/api/tracked-topics/<int:topic_id>/items/<item_id>")
    def api_tracked_topic_item_update(topic_id: int, item_id: str):
        body = request.get_json(silent=True) or {}
        hidden = body.get("hidden")
        if not isinstance(hidden, bool):
            return jsonify({"ok": False, "error": "invalid_hidden"}), 400

        conn = db_conn()
        try:
            topic = load_tracked_topic(conn, topic_id)
            if not topic:
                return jsonify({"ok": False, "error": "topic_not_found"}), 404
            existing = tracked_topic_item_status(conn, topic_id, item_id)
            if not existing:
                return jsonify({"ok": False, "error": "tracked_item_not_found"}), 404
            ts = database.now_ts()
            with conn:
                conn.execute(
                    """
                UPDATE tracked_topic_items
                SET hidden_at=?,
                    updated_at=?
                WHERE topic_id=? AND item_id=?
                """,
                    (ts if hidden else None, ts, topic_id, item_id),
                )
                conn.execute("UPDATE tracked_topics SET updated_at=? WHERE id=?", (ts, topic_id))
            tracked_item = tracked_topic_item_status(conn, topic_id, item_id)
        finally:
            conn.close()
        return jsonify({"ok": True, "tracked_item": dict(tracked_item) if tracked_item else None})
