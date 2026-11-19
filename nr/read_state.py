from __future__ import annotations

import json
import time
import uuid
from datetime import datetime

from flask import jsonify, request

import news_classification
import news_collection
import news_versions
from feed_sources import is_bloomberg_video_url, visible_news_sql
from nr import database, reading


def register(app, db, enqueue_detail_job):
    @app.get("/api/reading-checkpoint")
    def api_get_reading_checkpoint():
        scope = (request.args.get("scope") or "feed").strip().lower()
        if scope != "feed":
            return jsonify({"ok": False, "error": "unsupported_scope"}), 400

        conn = db.connect()
        try:
            row = conn.execute(
                f"""
                SELECT rc.scope, rc.item_id, rc.url, rc.title, rc.updated_at
                FROM reading_checkpoints rc
                JOIN items ON items.url = rc.url
                WHERE rc.scope = ?
                  AND {visible_news_sql()}
                """,
                (scope,),
            ).fetchone()
        finally:
            conn.close()

        if not row:
            return jsonify({"ok": True, "checkpoint": None})
        return jsonify({"ok": True, "checkpoint": dict(row)})

    @app.put("/api/reading-checkpoint")
    def api_put_reading_checkpoint():
        body = request.get_json(silent=True) or {}
        scope = (body.get("scope") or "feed").strip().lower()
        if scope != "feed":
            return jsonify({"ok": False, "error": "unsupported_scope"}), 400

        url = (body.get("url") or "").strip()
        if not url:
            return jsonify({"ok": False, "error": "missing_url"}), 400
        if is_bloomberg_video_url(url):
            return jsonify({"ok": False, "error": "item_not_found"}), 404
        item_id = (body.get("item_id") or "").strip() or None
        title = (body.get("title") or "").strip() or None
        ts = database.now_ts()

        conn = db.connect()
        try:
            conn.execute(
                """
            INSERT INTO reading_checkpoints(scope, item_id, url, title, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(scope) DO UPDATE SET
              item_id=excluded.item_id,
              url=excluded.url,
              title=excluded.title,
              updated_at=excluded.updated_at
            """,
                (scope, item_id, url, title, ts),
            )
            conn.commit()
        finally:
            conn.close()
        return jsonify({"ok": True})

    @app.get("/api/reading-checkpoint/locate")
    def api_locate_reading_checkpoint():
        scope = (request.args.get("scope") or "feed").strip().lower()
        if scope != "feed":
            return jsonify({"ok": False, "error": "unsupported_scope"}), 400

        q = (request.args.get("q") or "").strip()
        read_filter = (request.args.get("read_filter") or "all").strip().lower()
        source_filter = (request.args.get("source_filter") or "all").strip().lower()
        per = min(100, max(10, int(request.args.get("per", "30"))))

        conn = db.connect()
        try:
            cp = conn.execute(
                "SELECT item_id, url, title, updated_at FROM reading_checkpoints WHERE scope=?",
                (scope,),
            ).fetchone()
            if not cp or not cp["url"]:
                return jsonify({"ok": True, "found": False, "reason": "no_checkpoint"})
            if is_bloomberg_video_url(cp["url"]):
                return jsonify({"ok": True, "found": False, "reason": "no_checkpoint"})

            where_sql, args = reading._build_news_where_clause(q, read_filter, "feed", source_filter)
            sql = f"""
            WITH filtered AS (
              SELECT items.id, items.url,
                     ROW_NUMBER() OVER (ORDER BY {reading.FEED_NEWS_ORDER_BY_SQL}) AS rn,
                     COUNT(*) OVER () AS total
              FROM items
              LEFT JOIN item_state st ON st.item_id = items.id
              {where_sql}
            )
            SELECT id, url, rn, total
            FROM filtered
            WHERE url = ?
            ORDER BY rn
            LIMIT 1
            """
            hit = conn.execute(sql, [*args, cp["url"]]).fetchone()
        finally:
            conn.close()

        if not hit:
            return jsonify(
                {
                    "ok": True,
                    "found": False,
                    "reason": "not_in_current_scope",
                    "checkpoint": dict(cp),
                }
            )

        row_num = int(hit["rn"])
        total = int(hit["total"])
        page = ((row_num - 1) // per) + 1
        offset = row_num - ((page - 1) * per) - 1
        return jsonify(
            {
                "ok": True,
                "found": True,
                "item_id": hit["id"],
                "url": hit["url"],
                "row_num": row_num,
                "total": total,
                "page": page,
                "offset": offset,
                "per": per,
                "checkpoint": dict(cp),
            }
        )

    @app.patch("/api/news/<item_id>/state")
    def api_update_item_state(item_id: str):
        body = request.get_json(silent=True) or {}
        allowed = ("read", "important", "read_later", "favorite")
        provided = [k for k in allowed if k in body]
        if not provided:
            return jsonify({"ok": False, "error": "missing_state_flags"}), 400
        for k in provided:
            if not isinstance(body[k], bool):
                return jsonify({"ok": False, "error": f"invalid_{k}_flag"}), 400

        if body.get("read") is False:
            return jsonify({"ok": False, "error": "read_state_irreversible"}), 400

        if "read_later_done" in body:
            if not isinstance(body["read_later_done"], bool) or body.get("read_later") is not False:
                return jsonify({"ok": False, "error": "invalid_read_later_done_flag"}), 400

        job_states = {}
        conn = db.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT id, url FROM items WHERE id=?", (item_id,)).fetchone()
            if not row:
                return jsonify({"ok": False, "error": "item_not_found"}), 404
            old_state = conn.execute(
                "SELECT read_at, important_at, read_later_at, read_later_done_at, favorite_at FROM item_state WHERE item_id=?",
                (item_id,),
            ).fetchone()
            if (body.get("read_later") is True and news_versions.archived(conn, item_id)
                    and not conn.execute("SELECT 1 FROM news_item_details WHERE item_id=?", (item_id,)).fetchone()):
                return jsonify({"ok": False, "error": "news_version_archived"}), 409
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            read_at = old_state["read_at"] if old_state else None
            important_at = old_state["important_at"] if old_state else None
            read_later_at = old_state["read_later_at"] if old_state else None
            read_later_done_at = old_state["read_later_done_at"] if old_state else None
            favorite_at = old_state["favorite_at"] if old_state else None
            if "read" in body:
                read_at = read_at or ts
            if any(body.get(flag) is True for flag in ("important", "read_later", "favorite")):
                read_at = read_at or ts
            if "important" in body:
                important_at = ts if body["important"] else None
            if "read_later" in body:
                read_later_at = ts if body["read_later"] else None
                read_later_done_at = ts if not body["read_later"] and body.get("read_later_done") else None
                job = conn.execute("SELECT status FROM news_item_detail_jobs WHERE item_id=?", (item_id,)).fetchone()
                # A cancel racing with the worker's claim cannot remove running work.
                if not body["read_later"] and not body.get("read_later_done") and old_state and old_state["read_later_at"] and job and job["status"] == "running":
                    read_later_at = (old_state["read_later_at"] if old_state else None) or ts
                job_states["detail_ready"] = conn.execute("SELECT 1 FROM news_item_details WHERE item_id=?", (item_id,)).fetchone() is not None
            if "favorite" in body:
                favorite_at = ts if body["favorite"] else None
            with conn:
                conn.execute(
                    """
                INSERT INTO item_state(item_id, read_at, important_at, read_later_at, read_later_done_at, favorite_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                  read_at=COALESCE(item_state.read_at,excluded.read_at),
                  important_at=excluded.important_at,
                  read_later_at=excluded.read_later_at,
                  read_later_done_at=excluded.read_later_done_at,
                  favorite_at=excluded.favorite_at,
                  updated_at=excluded.updated_at
                """,
                    (item_id, read_at, important_at, read_later_at, read_later_done_at, favorite_at, ts),
                )
                if "read_later" in body:
                    item_row = conn.execute(
                        "SELECT id, url, source FROM items WHERE id=?",
                        (item_id,),
                    ).fetchone()
                    if item_row and item_row["url"]:
                        if body["read_later"]:
                            enqueue_detail_job(conn, item_row["id"], item_row["url"], item_row["source"] or "")
                        elif not news_versions.archived(conn, item_id):
                            # Removing a queue entry is separate from reading it. Only
                            # queued work can be canceled; an active CLI call continues.
                            conn.execute(
                                """
                            UPDATE detail_jobs
                            SET status='canceled', updated_at=?
                            WHERE url=? AND status='pending'
                            """,
                                (ts, item_row["url"]),
                            )
                            conn.execute(
                                """
                            UPDATE ai_jobs
                            SET status='canceled', updated_at=?
                            WHERE url=? AND status='pending'
                            """,
                                (ts, item_row["url"]),
                            )
                        for table, field in (("detail_jobs", "detail_status"), ("ai_jobs", "ai_status")):
                            job = conn.execute(f"SELECT status FROM {table} WHERE url=?", (item_row["url"],)).fetchone()
                            job_states[field] = job["status"] if job else "missing"
        finally:
            conn.close()
        return jsonify(
            {
                "ok": True,
                "item_id": item_id,
                "read_at": read_at,
                "important_at": important_at,
                "read_later_at": read_later_at,
                "read_later_done_at": read_later_done_at,
                "favorite_at": favorite_at,
                **job_states,
            }
        )

    @app.post("/api/news/mark-all-read")
    def api_mark_all_read():
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict) or ("preview_id" in body and not isinstance(body["preview_id"], str)):
            return jsonify({"ok": False, "error": "invalid_payload"}), 400
        q = (body.get("q") or "").strip()
        read_filter = (body.get("read_filter") or "all").strip().lower()
        collection = (body.get("collection") or "feed").strip().lower()
        source_filter = (body.get("source_filter") or "all").strip().lower()

        where = [visible_news_sql()]
        args: list = []
        try:
            category_clause, category_args = news_classification.filter_sql(body.get("category", "all"))
        except ValueError:
            return jsonify({"ok": False, "error": "invalid_category"}), 400
        if category_clause:
            where.append(category_clause)
            args.extend(category_args)
        if q:
            where.append("(items.title LIKE ? OR items.summary LIKE ? OR items.source LIKE ?)")
            like = f"%{q}%"
            args.extend([like, like, like])
        join_sql = "LEFT JOIN item_state st ON st.item_id = items.id LEFT JOIN news_item_details ad ON ad.item_id=items.id"
        if read_filter == "unread":
            where.append("st.read_at IS NULL")
        elif read_filter == "read":
            where.append("st.read_at IS NOT NULL")
        if collection == "important":
            where.append("st.important_at IS NOT NULL")
        elif collection == "read_later":
            where.append(reading.READ_LATER_PENDING_SQL)
        elif collection == "favorites":
            where.append("st.favorite_at IS NOT NULL")
        source_clause, source_args = reading.build_source_filter_clause(source_filter)
        if source_clause:
            where.append(source_clause)
            args.extend(source_args)
        hidden_clause, hidden_args = reading.build_feed_source_hidden_clause(collection)
        if hidden_clause:
            where.append(hidden_clause)
            args.extend(hidden_args)
        if str(body.get("has_note")) == "1":
            where.append("EXISTS (SELECT 1 FROM article_notes n WHERE n.url=items.url)")
        if "batch" in body:
            try:
                clause, batch_args = news_collection.batch_clause(body["batch"])
            except ValueError as exc:
                return jsonify({"ok": False, "error": str(exc)}), 400
            where.append(clause.removeprefix(" AND "))
            args.extend(batch_args)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""

        conn = db.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM news_read_previews WHERE created_at<?", (time.time()-1800,))
            if body.get("preview_id"):
                preview = conn.execute("SELECT item_ids FROM news_read_previews WHERE id=?", (body["preview_id"],)).fetchone()
                if not preview:
                    return jsonify({"ok": False, "error": "read_preview_expired"}), 409
                item_ids = json.loads(preview[0])
                # Deleted items must not acquire orphan state if a confirmation stayed open.
                existing = {row[0] for row in conn.execute("SELECT id FROM items")}
                item_ids = [ident for ident in item_ids if ident in existing]
            elif body.get("preview") is True:
                item_ids = [r[0] for r in conn.execute(f"SELECT items.id FROM items {join_sql} {where_sql}", args)]
                preview_id = uuid.uuid4().hex
                conn.execute("INSERT INTO news_read_previews VALUES (?,?,?)", (preview_id,json.dumps(item_ids),time.time()))
                conn.commit()
                return jsonify({"ok": True, "total": len(item_ids), "preview_id": preview_id})
            else:
                return jsonify({"ok": False, "error": "read_preview_required"}), 400
            if not item_ids:
                return jsonify({"ok": True, "marked": 0})

            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with conn:
                conn.executemany(
                    """
                INSERT INTO item_state(item_id, read_at, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                  read_at=COALESCE(item_state.read_at,excluded.read_at),
                  updated_at=excluded.updated_at
                """,
                    [(item_id, ts, ts) for item_id in item_ids],
                )
        finally:
            conn.close()
        return jsonify({"ok": True, "marked": len(item_ids)})

    @app.post("/api/news/clear-favorites")
    def api_clear_favorites():
        conn = db.connect()
        try:
            with conn:
                result = conn.execute(
                    "UPDATE item_state SET favorite_at=NULL, updated_at=? WHERE favorite_at IS NOT NULL",
                    (database.now_ts(),),
                )
            return jsonify({"ok": True, "updated": result.rowcount})
        finally:
            conn.close()

    @app.post("/api/news/mark-read-by-ids")
    def api_mark_read_by_ids():
        body = request.get_json(silent=True) or {}
        raw_item_ids = body.get("item_ids")
        if not isinstance(raw_item_ids, list):
            return jsonify({"ok": False, "error": "invalid_item_ids"}), 400

        item_ids = [str(x).strip() for x in raw_item_ids if str(x).strip()]
        if not item_ids:
            return jsonify({"ok": True, "marked": 0})

        placeholders = ",".join(["?"] * len(item_ids))
        conn = db.connect()
        try:
            existing_ids = [
                r[0]
                for r in conn.execute(
                    f"SELECT id FROM items WHERE id IN ({placeholders})",
                    item_ids,
                ).fetchall()
            ]
            if not existing_ids:
                return jsonify({"ok": True, "marked": 0})

            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with conn:
                conn.executemany(
                    """
                INSERT INTO item_state(item_id, read_at, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                  read_at=COALESCE(item_state.read_at,excluded.read_at),
                  updated_at=excluded.updated_at
                """,
                    [(item_id, ts, ts) for item_id in existing_ids],
                )
        finally:
            conn.close()
        return jsonify({"ok": True, "marked": len(existing_ids)})

    @app.post("/api/news/clear-read-later")
    def api_clear_read_later():
        body = request.get_json(silent=True) or {}
        q = (body.get("q") or "").strip()
        collection = (body.get("collection") or "read_later").strip().lower()
        read_filter = (body.get("read_filter") or "all").strip().lower()
        source_filter = (body.get("source_filter") or "all").strip().lower()

        where = [visible_news_sql()]
        args: list = []
        if q:
            where.append("(items.title LIKE ? OR items.summary LIKE ? OR items.source LIKE ?)")
            like = f"%{q}%"
            args.extend([like, like, like])
        join_sql = "LEFT JOIN item_state st ON st.item_id = items.id LEFT JOIN news_item_details ad ON ad.item_id=items.id"

        # 默认仅清理 read_later 集合；即使传入其它 collection，也不允许扩大到全量。
        if collection == "important":
            where.append("st.important_at IS NOT NULL")
        elif collection == "notes":
            where.append("EXISTS (SELECT 1 FROM article_notes an WHERE an.url = items.url)")
        elif collection == "market_tags":
            where.append("EXISTS (SELECT 1 FROM article_market_tags mt WHERE mt.url = items.url)")
        if read_filter == "read":
            where.append("0 = 1")
        else:
            where.append(reading.READ_LATER_PENDING_SQL)
        source_clause, source_args = reading.build_source_filter_clause(source_filter)
        if source_clause:
            where.append(source_clause)
            args.extend(source_args)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""

        conn = db.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                f"SELECT items.id, items.url FROM items {join_sql} {where_sql}",
                args,
            ).fetchall()
            if not rows:
                return jsonify({"ok": True, "cleared": 0})
            item_ids = [r["id"] for r in rows]
            urls = [r["url"] for r in rows if r["url"]]
            ts = database.now_ts()
            with conn:
                conn.executemany(
                    """
                INSERT INTO item_state(item_id, read_later_at, read_later_done_at, updated_at)
                VALUES (?, NULL, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                  read_later_at=NULL,
                  read_later_done_at=excluded.read_later_done_at,
                  updated_at=excluded.updated_at
                """,
                    [(item_id, ts, ts) for item_id in item_ids],
                )
                for start in range(0, len(urls), 500):
                    batch = urls[start:start + 500]
                    placeholders = ",".join("?" for _ in batch)
                    conn.execute(
                        f"UPDATE detail_jobs SET status='canceled', updated_at=? WHERE status='pending' AND url IN ({placeholders})",
                        [ts, *batch],
                    )
                    conn.execute(
                        f"UPDATE ai_jobs SET status='canceled', updated_at=? WHERE status='pending' AND url IN ({placeholders})",
                        [ts, *batch],
                    )
        finally:
            conn.close()
        return jsonify({"ok": True, "cleared": len(item_ids)})
