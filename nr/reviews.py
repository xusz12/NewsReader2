from __future__ import annotations

import json
import math
import sqlite3
from datetime import date as date_cls, datetime, timedelta

from flask import jsonify, request

from nr import database, reader_items, reminders


REVIEW_SOURCE_TYPES = ("article_note", "market_trend_note", "standalone_idea")
REVIEW_RESULTS = ("confirmed", "refuted", "inconclusive")
REVIEW_TEXT_MAX_LEN = 10000
REVIEW_EVIDENCE_MAX_LEN = 5000


def _today_str() -> str:
    return date_cls.today().isoformat()


def _build_source_snapshot(source_data: dict) -> str:
    return json.dumps({
        "source_note": source_data["source_note"],
        "source_created_at": source_data["source_created_at"],
        "source_tag_key": source_data.get("source_tag_key", ""),
        "source_tag_label": source_data.get("source_tag_label", ""),
        "news_list": source_data.get("source_news_list", []),
    }, ensure_ascii=False)


def _effective_status(status: str, plan_review_date: str) -> str:
    if status == "done":
        return "done"
    today = _today_str()
    if plan_review_date <= today:
        return "pending_review"
    return "in_progress"


def _serialize_review_chain(row: sqlite3.Row, conn: sqlite3.Connection) -> dict:
    chain = dict(row)
    chain["effective_status"] = _effective_status(
        chain.get("status", "active"), chain.get("plan_review_date", "")
    )
    # Date grouping for list view (plan_review_date)
    plan_date = (chain.get("plan_review_date") or "").strip()
    chain["date_key"], chain["date_label"] = reader_items.derive_date_meta(None, plan_date)
    # Get current version info
    v_row = conn.execute(
        "SELECT version_no, judgment, criteria, revision_reason, created_at "
        "FROM review_versions WHERE chain_id=? AND version_no=?",
        (chain["id"], chain.get("current_version", 1)),
    ).fetchone()
    chain["current_judgment"] = v_row["judgment"] if v_row else ""
    chain["current_criteria"] = v_row["criteria"] if v_row else ""
    # Get version count
    v_count = conn.execute(
        "SELECT COUNT(*) AS c FROM review_versions WHERE chain_id=?",
        (chain["id"],),
    ).fetchone()
    chain["version_count"] = v_count["c"] if v_count else 1
    # Get latest event time
    e_row = conn.execute(
        "SELECT created_at FROM review_events WHERE chain_id=? ORDER BY created_at DESC LIMIT 1",
        (chain["id"],),
    ).fetchone()
    chain["latest_event_at"] = e_row["created_at"] if e_row else ""
    # Parse snapshot
    try:
        chain["source_snapshot"] = json.loads(chain.get("source_snapshot_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        chain["source_snapshot"] = {}
    return chain


def _serialize_review_chain_detail(row: sqlite3.Row, conn: sqlite3.Connection) -> dict:
    chain = _serialize_review_chain(row, conn)
    # All versions
    versions = conn.execute(
        "SELECT id, version_no, judgment, criteria, revision_reason, created_at "
        "FROM review_versions WHERE chain_id=? ORDER BY version_no ASC",
        (chain["id"],),
    ).fetchall()
    chain["versions"] = [dict(v) for v in versions]
    # All events
    events = conn.execute(
        "SELECT id, event_type, event_text, event_date, version_id, metadata_json, created_at "
        "FROM review_events WHERE chain_id=? ORDER BY created_at ASC",
        (chain["id"],),
    ).fetchall()
    chain["events"] = []
    chain_created_at = chain.get("created_at", "")
    for e in events:
        ev = dict(e)
        try:
            ev["metadata"] = json.loads(e["metadata_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            ev["metadata"] = {}
        # Old records stored plan_review_date as the initial event_date; display actual creation date
        if ev["event_type"] == "revision" and ev["version_id"] is None and chain_created_at:
            ev["event_date"] = chain_created_at[:10]
        chain["events"].append(ev)
    # All evidence
    evidence = conn.execute(
        "SELECT id, event_id, news_title, news_summary, news_url, added_at "
        "FROM review_evidence WHERE chain_id=? ORDER BY added_at ASC",
        (chain["id"],),
    ).fetchall()
    chain["evidence"] = [dict(ev) for ev in evidence]
    return chain


def register(app, db):
    def _validate_review_source(source_type: str, source_key: str) -> dict | None:
        """Validate that the source idea exists and return its snapshot data."""
        source_type = (source_type or "").strip()
        source_key = (source_key or "").strip()
        if source_type not in REVIEW_SOURCE_TYPES:
            return None
        if not source_key:
            return None

        conn = db.connect()
        try:
            if source_type == "article_note":
                row = conn.execute(
                    "SELECT note, created_at, updated_at FROM article_notes WHERE url=?",
                    (source_key,),
                ).fetchone()
                if not row:
                    return None
                # Get associated news item
                news_rows = conn.execute(
                    """SELECT i.title, i.summary, i.url, i.source, i.published_at
                   FROM items i WHERE i.url=?""",
                    (source_key,),
                ).fetchall()
                news_list = [
                    {
                        "title": nr["title"] or "",
                        "summary": nr["summary"] or "",
                        "url": nr["url"] or "",
                        "source": nr["source"] or "",
                        "published_at": nr["published_at"] or "",
                    }
                    for nr in news_rows
                ]
                return {
                    "source_note": row["note"] or "",
                    "source_created_at": row["created_at"] or "",
                    "source_tag_key": "",
                    "source_tag_label": "",
                    "source_news_list": news_list,
                }

            elif source_type == "market_trend_note":
                row = conn.execute(
                    """SELECT mtn.id, mtn.note, mtn.created_at, mtn.updated_at, mtn.tag, mtn.direction,
                          mtd.display_name
                   FROM market_trend_notes mtn
                   LEFT JOIN market_tag_definitions mtd ON mtd.key = mtn.tag
                   WHERE mtn.id=?""",
                    (int(source_key),),
                ).fetchone()
                if not row:
                    return None
                # Get associated news for this tag
                news_rows = conn.execute(
                    """SELECT i.title, i.summary, i.url, i.source, i.published_at
                   FROM article_market_tags amt
                   JOIN items i ON i.url = amt.url
                   WHERE amt.tag=?
                   ORDER BY i.published_at DESC LIMIT 20""",
                    (row["tag"],),
                ).fetchall()
                news_list = [
                    {
                        "title": nr["title"] or "",
                        "summary": nr["summary"] or "",
                        "url": nr["url"] or "",
                        "source": nr["source"] or "",
                        "published_at": nr["published_at"] or "",
                    }
                    for nr in news_rows
                ]
                return {
                    "source_note": row["note"] or "",
                    "source_created_at": row["created_at"] or "",
                    "source_tag_key": row["tag"] or "",
                    "source_tag_label": row["display_name"] or row["tag"] or "",
                    "source_news_list": news_list,
                }

            elif source_type == "standalone_idea":
                row = conn.execute(
                    "SELECT id, note, created_at, updated_at FROM standalone_ideas WHERE id=?",
                    (int(source_key),),
                ).fetchone()
                if not row:
                    return None
                return {
                    "source_note": row["note"] or "",
                    "source_created_at": row["created_at"] or "",
                    "source_tag_key": "",
                    "source_tag_label": "",
                    "source_news_list": [],
                }
        except (ValueError, TypeError):
            return None
        finally:
            conn.close()

    @app.get("/api/reviews")
    def api_reviews_list():
        status_filter = (request.args.get("status") or "all").strip().lower()
        result_filter = (request.args.get("result") or "all").strip().lower()
        q = (request.args.get("q") or "").strip()
        page = max(1, int(request.args.get("page", "1")))
        per = min(100, max(10, int(request.args.get("per", "30"))))

        valid_filters = ("all", "in_progress", "pending_review", "done")
        if status_filter not in valid_filters:
            return jsonify({"ok": False, "error": "invalid_status_filter"}), 400
        valid_result_filters = ("all",) + REVIEW_RESULTS
        if result_filter not in valid_result_filters:
            return jsonify({"ok": False, "error": "invalid_result_filter"}), 400

        conn = db.connect()
        try:
            base_sql = "FROM review_chains c"
            conditions = []
            params = []

            if status_filter == "done":
                conditions.append("c.status = 'done'")
            elif status_filter == "in_progress":
                conditions.append("c.status = 'active'")
                conditions.append("c.plan_review_date > ?")
                params.append(_today_str())
            elif status_filter == "pending_review":
                conditions.append("c.status = 'active'")
                conditions.append("c.plan_review_date <= ?")
                params.append(_today_str())

            if result_filter != "all":
                # Result is a completed-review semantic. Keep the query authoritative so
                # pagination never loses matching rows that are not loaded yet.
                conditions.append("c.status = 'done'")
                conditions.append("c.result = ?")
                params.append(result_filter)

            if q:
                like = f"%{q}%"
                conditions.append("""(
                c.source_note LIKE ?
                OR c.source_tag_label LIKE ?
                OR c.source_snapshot_json LIKE ?
                OR c.experience LIKE ?
                OR c.actual_text LIKE ?
                OR EXISTS (SELECT 1 FROM review_versions v WHERE v.chain_id = c.id AND (v.judgment LIKE ? OR v.criteria LIKE ?))
                OR EXISTS (SELECT 1 FROM review_evidence ev WHERE ev.chain_id = c.id AND (ev.news_title LIKE ? OR ev.news_summary LIKE ? OR ev.news_url LIKE ?))
            )""")
                params.extend([like, like, like, like, like, like, like, like, like, like])

            where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
            total = conn.execute(f"SELECT COUNT(*) AS c {base_sql}{where_clause}", params).fetchone()["c"]

            offset = (page - 1) * per
            rows = conn.execute(
                f"SELECT c.* {base_sql}{where_clause} ORDER BY c.plan_review_date ASC, c.updated_at DESC, c.id DESC LIMIT ? OFFSET ?",
                params + [per, offset],
            ).fetchall()

            items = [_serialize_review_chain(r, conn) for r in rows]
            pages = max(1, math.ceil(total / per)) if total else 1
            return jsonify({
                "items": items,
                "total": total,
                "page": page,
                "pages": pages,
                "has_more": page < pages,
            })
        finally:
            conn.close()

    @app.get("/api/reviews/<int:chain_id>")
    def api_review_detail(chain_id: int):
        conn = db.connect()
        try:
            row = conn.execute("SELECT * FROM review_chains WHERE id=?", (chain_id,)).fetchone()
            if not row:
                return jsonify({"ok": False, "error": "review_not_found"}), 404
            detail = _serialize_review_chain_detail(row, conn)
            return jsonify({"ok": True, "review": detail})
        finally:
            conn.close()

    @app.post("/api/reviews")
    def api_review_create():
        body = request.get_json(silent=True) or {}
        source_type = (body.get("source_type") or "").strip()
        source_key = (body.get("source_key") or "").strip()
        judgment = (body.get("judgment") or "").strip()
        criteria = (body.get("criteria") or "").strip()
        plan_review_date = (body.get("plan_review_date") or "").strip()
        add_reminder = bool(body.get("add_reminder", False))
        remind_at = (body.get("remind_at") or "").strip()

        if not judgment:
            return jsonify({"ok": False, "error": "missing_judgment"}), 400
        if not plan_review_date:
            return jsonify({"ok": False, "error": "missing_plan_review_date"}), 400
        if len(judgment) > REVIEW_TEXT_MAX_LEN or len(criteria) > REVIEW_TEXT_MAX_LEN:
            return jsonify({"ok": False, "error": "text_too_long"}), 400

        try:
            datetime.strptime(plan_review_date, "%Y-%m-%d")
        except ValueError:
            return jsonify({"ok": False, "error": "invalid_date"}), 400

        # If add_reminder is True, validate remind_at; default to plan_review_date 09:00
        if add_reminder:
            if not remind_at:
                remind_at = f"{plan_review_date}T09:00"
            try:
                remind_at = reminders.parse_review_reminder_remind_at(remind_at)
            except ValueError:
                return jsonify({"ok": False, "error": "invalid_remind_at"}), 400

        source_data = _validate_review_source(source_type, source_key)
        if source_data is None:
            return jsonify({"ok": False, "error": "source_not_found"}), 404

        snapshot_json = _build_source_snapshot(source_data)
        ts = database.now_ts()
        conn = db.connect()
        try:
            with conn:
                cur = conn.execute(
                    """INSERT INTO review_chains
                   (parent_chain_id, source_type, source_key, source_note, source_created_at,
                    source_tag_key, source_tag_label, source_snapshot_json,
                    status, current_version, plan_review_date,
                    result, actual_text, bias_text, experience, completed_at,
                    created_at, updated_at)
                   VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, 'active', 1, ?, '', '', '', '', '', ?, ?)""",
                    (source_type, source_key, source_data["source_note"],
                     source_data["source_created_at"],
                     source_data.get("source_tag_key", ""),
                     source_data.get("source_tag_label", ""),
                     snapshot_json, plan_review_date, ts, ts),
                )
                chain_id = cur.lastrowid
                conn.execute(
                    """INSERT INTO review_versions
                   (chain_id, version_no, judgment, criteria, revision_reason, created_at)
                   VALUES (?, 1, ?, ?, '', ?)""",
                    (chain_id, judgment, criteria, ts),
                )
                conn.execute(
                    """INSERT INTO review_events
                   (chain_id, event_type, event_text, event_date, version_id, metadata_json, created_at)
                   VALUES (?, 'revision', '', ?, NULL, '{}', ?)""",
                    (chain_id, _today_str(), ts),
                )

            row = conn.execute("SELECT * FROM review_chains WHERE id=?", (chain_id,)).fetchone()
            detail = _serialize_review_chain_detail(row, conn)

            if add_reminder:
                # Create a review-specific reminder
                event_title = f"复盘到期: {judgment[:50]}"
                conn.execute(
                    """INSERT INTO news_reminders
                   (item_id, item_title_snapshot, item_url_snapshot,
                    event_title, event_date, remind_at, note, status,
                    review_chain_id, created_at, updated_at)
                   VALUES (NULL, ?, '', ?, ?, ?, '', 'active', ?, ?, ?)""",
                    (source_data["source_note"][:200], event_title, plan_review_date,
                     remind_at, chain_id, ts, ts),
                )
                conn.commit()

            return jsonify({"ok": True, "review": detail})
        finally:
            conn.close()

    @app.post("/api/reviews/<int:chain_id>/progress")
    def api_review_progress(chain_id: int):
        body = request.get_json(silent=True) or {}
        event_text = (body.get("event_text") or "").strip()
        event_date = (body.get("event_date") or "").strip()
        evidence_list = body.get("evidence") or []

        if not event_text:
            return jsonify({"ok": False, "error": "missing_event_text"}), 400
        if not event_date:
            return jsonify({"ok": False, "error": "missing_event_date"}), 400
        try:
            datetime.strptime(event_date, "%Y-%m-%d")
        except ValueError:
            return jsonify({"ok": False, "error": "invalid_date"}), 400
        if len(event_text) > REVIEW_TEXT_MAX_LEN:
            return jsonify({"ok": False, "error": "text_too_long"}), 400

        conn = db.connect()
        try:
            auto_postponed = False
            old_plan_review_date = ""
            new_plan_review_date = ""
            moved_reminder_ids: list[int] = []
            try:
                # Serialize the read/decision/write sequence so two simultaneous progress
                # submissions cannot both observe the same overdue plan and extend it twice.
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT status, plan_review_date FROM review_chains WHERE id=?",
                    (chain_id,),
                ).fetchone()
                if not row:
                    conn.rollback()
                    return jsonify({"ok": False, "error": "review_not_found"}), 404
                if row["status"] == "done":
                    conn.rollback()
                    return jsonify({"ok": False, "error": "review_already_done"}), 409

                ts = database.now_ts()
                today = _today_str()
                old_plan_review_date = (row["plan_review_date"] or "").strip()
                auto_postponed = row["status"] == "active" and old_plan_review_date <= today
                new_plan_review_date = old_plan_review_date
                if auto_postponed:
                    new_plan_review_date = (
                        datetime.strptime(today, "%Y-%m-%d") + timedelta(days=15)
                    ).strftime("%Y-%m-%d")

                cur = conn.execute(
                    """INSERT INTO review_events
                   (chain_id, event_type, event_text, event_date, version_id, metadata_json, created_at)
                   VALUES (?, 'progress', ?, ?, NULL, '{}', ?)""",
                    (chain_id, event_text, event_date, ts),
                )
                event_id = cur.lastrowid
                for ev in evidence_list:
                    if not isinstance(ev, dict):
                        continue
                    title = (ev.get("news_title") or "").strip()
                    url = (ev.get("news_url") or "").strip()
                    if not title or not url:
                        continue
                    conn.execute(
                        """INSERT INTO review_evidence
                       (chain_id, event_id, news_title, news_summary, news_url, added_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                        (chain_id, event_id, title[:REVIEW_EVIDENCE_MAX_LEN],
                         (ev.get("news_summary") or "").strip()[:REVIEW_EVIDENCE_MAX_LEN], url, ts),
                    )

                if auto_postponed:
                    matching_reminders = conn.execute(
                        """SELECT id, remind_at
                       FROM news_reminders
                       WHERE review_chain_id=? AND status='active' AND event_date=?
                       ORDER BY id ASC""",
                        (chain_id, old_plan_review_date),
                    ).fetchall()
                    for reminder in matching_reminders:
                        try:
                            canonical_remind_at = reminders.parse_reminder_remind_at(reminder["remind_at"])
                        except ValueError as exc:
                            raise sqlite3.IntegrityError("invalid_remind_at") from exc
                        moved_reminder_ids.append(int(reminder["id"]))
                        conn.execute(
                            """UPDATE news_reminders
                           SET event_date=?, remind_at=?, updated_at=?
                           WHERE id=?""",
                            (
                                new_plan_review_date,
                                f"{new_plan_review_date}{canonical_remind_at[10:]}",
                                ts,
                                reminder["id"],
                            ),
                        )

                    conn.execute(
                        "UPDATE review_chains SET plan_review_date=?, updated_at=? WHERE id=?",
                        (new_plan_review_date, ts, chain_id),
                    )
                else:
                    conn.execute("UPDATE review_chains SET updated_at=? WHERE id=?", (ts, chain_id))

                conn.commit()
            except sqlite3.Error:
                conn.rollback()
                return jsonify({"ok": False, "error": "review_progress_failed"}), 500
            except Exception:
                conn.rollback()
                raise

            row = conn.execute("SELECT * FROM review_chains WHERE id=?", (chain_id,)).fetchone()
            detail = _serialize_review_chain_detail(row, conn)
            return jsonify(
                {
                    "ok": True,
                    "review": detail,
                    "auto_postponed": auto_postponed,
                    "old_plan_review_date": old_plan_review_date,
                    "new_plan_review_date": new_plan_review_date,
                    "moved_reminder_count": len(moved_reminder_ids),
                    "moved_reminder_ids": moved_reminder_ids,
                }
            )
        finally:
            conn.close()

    @app.post("/api/reviews/<int:chain_id>/revise")
    def api_review_revise(chain_id: int):
        body = request.get_json(silent=True) or {}
        judgment = (body.get("judgment") or "").strip()
        criteria = (body.get("criteria") or "").strip()
        revision_reason = (body.get("revision_reason") or "").strip()
        event_date = (body.get("event_date") or "").strip()

        if not judgment:
            return jsonify({"ok": False, "error": "missing_judgment"}), 400
        if not revision_reason:
            return jsonify({"ok": False, "error": "missing_revision_reason"}), 400
        if not event_date:
            return jsonify({"ok": False, "error": "missing_event_date"}), 400
        try:
            datetime.strptime(event_date, "%Y-%m-%d")
        except ValueError:
            return jsonify({"ok": False, "error": "invalid_date"}), 400
        if len(judgment) > REVIEW_TEXT_MAX_LEN or len(criteria) > REVIEW_TEXT_MAX_LEN:
            return jsonify({"ok": False, "error": "text_too_long"}), 400

        conn = db.connect()
        try:
            row = conn.execute("SELECT status, current_version FROM review_chains WHERE id=?", (chain_id,)).fetchone()
            if not row:
                return jsonify({"ok": False, "error": "review_not_found"}), 404
            if row["status"] == "done":
                return jsonify({"ok": False, "error": "review_already_done"}), 409

            new_version = row["current_version"] + 1
            ts = database.now_ts()
            try:
                with conn:
                    cur = conn.execute(
                        """INSERT INTO review_versions
                       (chain_id, version_no, judgment, criteria, revision_reason, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                        (chain_id, new_version, judgment, criteria, revision_reason, ts),
                    )
                    version_id = cur.lastrowid
                    conn.execute(
                        """INSERT INTO review_events
                       (chain_id, event_type, event_text, event_date, version_id, metadata_json, created_at)
                       VALUES (?, 'revision', ?, ?, ?, '{"action": "revise"}', ?)""",
                        (chain_id, revision_reason, event_date, version_id, ts),
                    )
                    conn.execute(
                        "UPDATE review_chains SET current_version=?, updated_at=? WHERE id=?",
                        (new_version, ts, chain_id),
                    )
            except sqlite3.IntegrityError:
                return jsonify({"ok": False, "error": "version_conflict"}), 409

            row = conn.execute("SELECT * FROM review_chains WHERE id=?", (chain_id,)).fetchone()
            detail = _serialize_review_chain_detail(row, conn)
            return jsonify({"ok": True, "review": detail})
        finally:
            conn.close()

    @app.post("/api/reviews/<int:chain_id>/complete")
    def api_review_complete(chain_id: int):
        body = request.get_json(silent=True) or {}
        result = (body.get("result") or "").strip()
        actual_text = (body.get("actual_text") or "").strip()
        bias_text = (body.get("bias_text") or "").strip()
        experience = (body.get("experience") or "").strip()
        evidence_list = body.get("evidence") or []

        if result not in REVIEW_RESULTS:
            return jsonify({"ok": False, "error": "invalid_result"}), 400
        if not experience:
            return jsonify({"ok": False, "error": "missing_experience"}), 400
        if len(actual_text) > REVIEW_TEXT_MAX_LEN or len(bias_text) > REVIEW_TEXT_MAX_LEN or len(experience) > REVIEW_TEXT_MAX_LEN:
            return jsonify({"ok": False, "error": "text_too_long"}), 400

        conn = db.connect()
        try:
            row = conn.execute("SELECT status FROM review_chains WHERE id=?", (chain_id,)).fetchone()
            if not row:
                return jsonify({"ok": False, "error": "review_not_found"}), 404
            if row["status"] == "done":
                return jsonify({"ok": False, "error": "review_already_done"}), 409

            ts = database.now_ts()
            metadata = json.dumps({"result": result}, ensure_ascii=False)
            with conn:
                cur = conn.execute(
                    """INSERT INTO review_events
                   (chain_id, event_type, event_text, event_date, version_id, metadata_json, created_at)
                   VALUES (?, 'review_completed', ?, ?, NULL, ?, ?)""",
                    (chain_id, bias_text, _today_str(), metadata, ts),
                )
                event_id = cur.lastrowid
                for ev in evidence_list:
                    if not isinstance(ev, dict):
                        continue
                    title = (ev.get("news_title") or "").strip()
                    url = (ev.get("news_url") or "").strip()
                    if not title or not url:
                        continue
                    conn.execute(
                        """INSERT INTO review_evidence
                       (chain_id, event_id, news_title, news_summary, news_url, added_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                        (chain_id, event_id, title[:REVIEW_EVIDENCE_MAX_LEN],
                         (ev.get("news_summary") or "").strip()[:REVIEW_EVIDENCE_MAX_LEN], url, ts),
                    )
                conn.execute(
                    """UPDATE review_chains
                   SET status='done', result=?, actual_text=?, bias_text=?, experience=?,
                       completed_at=?, updated_at=?
                   WHERE id=?""",
                    (result, actual_text, bias_text, experience, ts, ts, chain_id),
                )

            row = conn.execute("SELECT * FROM review_chains WHERE id=?", (chain_id,)).fetchone()
            detail = _serialize_review_chain_detail(row, conn)
            return jsonify({"ok": True, "review": detail})
        finally:
            conn.close()

    @app.post("/api/reviews/<int:chain_id>/continue-observing")
    def api_review_continue_observing(chain_id: int):
        body = request.get_json(silent=True) or {}
        event_text = (body.get("event_text") or "").strip()
        new_review_date = (body.get("new_review_date") or "").strip()

        if not new_review_date:
            return jsonify({"ok": False, "error": "missing_new_review_date"}), 400
        try:
            datetime.strptime(new_review_date, "%Y-%m-%d")
        except ValueError:
            return jsonify({"ok": False, "error": "invalid_date"}), 400
        if len(event_text) > REVIEW_TEXT_MAX_LEN:
            return jsonify({"ok": False, "error": "text_too_long"}), 400

        conn = db.connect()
        try:
            row = conn.execute("SELECT status, plan_review_date FROM review_chains WHERE id=?", (chain_id,)).fetchone()
            if not row:
                return jsonify({"ok": False, "error": "review_not_found"}), 404
            if row["status"] == "done":
                return jsonify({"ok": False, "error": "review_already_done"}), 409

            old_date = row["plan_review_date"]
            ts = database.now_ts()
            metadata = json.dumps({"old_date": old_date, "new_date": new_review_date}, ensure_ascii=False)
            with conn:
                conn.execute(
                    """INSERT INTO review_events
                   (chain_id, event_type, event_text, event_date, version_id, metadata_json, created_at)
                   VALUES (?, 'continue_observing', ?, ?, NULL, ?, ?)""",
                    (chain_id, event_text, _today_str(), metadata, ts),
                )
                conn.execute(
                    "UPDATE review_chains SET plan_review_date=?, updated_at=? WHERE id=?",
                    (new_review_date, ts, chain_id),
                )

            row = conn.execute("SELECT * FROM review_chains WHERE id=?", (chain_id,)).fetchone()
            detail = _serialize_review_chain_detail(row, conn)
            return jsonify({"ok": True, "review": detail})
        finally:
            conn.close()

    @app.post("/api/reviews/<int:chain_id>/retrack")
    def api_review_retrack(chain_id: int):
        body = request.get_json(silent=True) or {}
        judgment = (body.get("judgment") or "").strip()
        criteria = (body.get("criteria") or "").strip()
        plan_review_date = (body.get("plan_review_date") or "").strip()

        if not judgment:
            return jsonify({"ok": False, "error": "missing_judgment"}), 400
        if not plan_review_date:
            return jsonify({"ok": False, "error": "missing_plan_review_date"}), 400
        try:
            datetime.strptime(plan_review_date, "%Y-%m-%d")
        except ValueError:
            return jsonify({"ok": False, "error": "invalid_date"}), 400
        if len(judgment) > REVIEW_TEXT_MAX_LEN or len(criteria) > REVIEW_TEXT_MAX_LEN:
            return jsonify({"ok": False, "error": "text_too_long"}), 400

        conn = db.connect()
        try:
            parent = conn.execute("SELECT * FROM review_chains WHERE id=?", (chain_id,)).fetchone()
            if not parent:
                return jsonify({"ok": False, "error": "review_not_found"}), 404
            if parent["status"] != "done":
                return jsonify({"ok": False, "error": "review_not_done"}), 409

            ts = database.now_ts()
            with conn:
                cur = conn.execute(
                    """INSERT INTO review_chains
                   (parent_chain_id, source_type, source_key, source_note, source_created_at,
                    source_tag_key, source_tag_label, source_snapshot_json,
                    status, current_version, plan_review_date,
                    result, actual_text, bias_text, experience, completed_at,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', 1, ?, '', '', '', '', '', ?, ?)""",
                    (chain_id, parent["source_type"], parent["source_key"], parent["source_note"],
                     parent["source_created_at"], parent["source_tag_key"], parent["source_tag_label"],
                     parent["source_snapshot_json"], plan_review_date, ts, ts),
                )
                new_chain_id = cur.lastrowid
                conn.execute(
                    """INSERT INTO review_versions
                   (chain_id, version_no, judgment, criteria, revision_reason, created_at)
                   VALUES (?, 1, ?, ?, '', ?)""",
                    (new_chain_id, judgment, criteria, ts),
                )
                conn.execute(
                    """INSERT INTO review_events
                   (chain_id, event_type, event_text, event_date, version_id, metadata_json, created_at)
                   VALUES (?, 'retracked', ?, ?, NULL, '{"parent_chain_id": ' + ? + '}', ?)""",
                    (new_chain_id, f"从复盘 #{chain_id} 派生", _today_str(), str(chain_id), ts),
                )

            row = conn.execute("SELECT * FROM review_chains WHERE id=?", (new_chain_id,)).fetchone()
            detail = _serialize_review_chain_detail(row, conn)
            return jsonify({"ok": True, "review": detail})
        finally:
            conn.close()

    @app.post("/api/reviews/<int:chain_id>/evidence")
    def api_review_add_evidence(chain_id: int):
        body = request.get_json(silent=True) or {}
        news_title = (body.get("news_title") or "").strip()
        news_summary = (body.get("news_summary") or "").strip()
        news_url = (body.get("news_url") or "").strip()

        if not news_title or not news_url:
            return jsonify({"ok": False, "error": "missing_evidence_fields"}), 400
        if len(news_title) > REVIEW_EVIDENCE_MAX_LEN:
            return jsonify({"ok": False, "error": "text_too_long"}), 400

        conn = db.connect()
        try:
            row = conn.execute("SELECT status FROM review_chains WHERE id=?", (chain_id,)).fetchone()
            if not row:
                return jsonify({"ok": False, "error": "review_not_found"}), 404
            if row["status"] == "done":
                return jsonify({"ok": False, "error": "review_already_done"}), 409

            ts = database.now_ts()
            with conn:
                cur = conn.execute(
                    """INSERT INTO review_evidence
                   (chain_id, event_id, news_title, news_summary, news_url, added_at)
                   VALUES (?, NULL, ?, ?, ?, ?)""",
                    (chain_id, news_title, news_summary, news_url, ts),
                )
                evidence_id = cur.lastrowid
                conn.execute("UPDATE review_chains SET updated_at=? WHERE id=?", (ts, chain_id))

            return jsonify({"ok": True, "evidence_id": evidence_id})
        finally:
            conn.close()

    @app.delete("/api/reviews/<int:chain_id>/evidence/<int:evidence_id>")
    def api_review_delete_evidence(chain_id: int, evidence_id: int):
        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT chain_id FROM review_evidence WHERE id=?", (evidence_id,),
            ).fetchone()
            if not row:
                return jsonify({"ok": False, "error": "evidence_not_found"}), 404
            if row["chain_id"] != chain_id:
                return jsonify({"ok": False, "error": "evidence_not_belong"}), 404

            chain = conn.execute("SELECT status FROM review_chains WHERE id=?", (chain_id,)).fetchone()
            if not chain:
                return jsonify({"ok": False, "error": "review_not_found"}), 404
            if chain["status"] == "done":
                return jsonify({"ok": False, "error": "review_already_done"}), 409

            with conn:
                conn.execute("DELETE FROM review_evidence WHERE id=?", (evidence_id,))
                conn.execute("UPDATE review_chains SET updated_at=? WHERE id=?", (database.now_ts(), chain_id))

            return jsonify({"ok": True})
        finally:
            conn.close()

    @app.post("/api/reviews/<int:chain_id>/reminders")
    def api_review_create_reminder(chain_id: int):
        body = request.get_json(silent=True) or {}
        event_date = (body.get("event_date") or "").strip()
        remind_at = (body.get("remind_at") or "").strip()
        note = (body.get("note") or "").strip()

        if not event_date:
            return jsonify({"ok": False, "error": "missing_event_date"}), 400
        try:
            datetime.strptime(event_date, "%Y-%m-%d")
        except ValueError:
            return jsonify({"ok": False, "error": "invalid_date"}), 400

        if not remind_at:
            remind_at = f"{event_date}T09:00"
        try:
            remind_at = reminders.parse_review_reminder_remind_at(remind_at)
        except ValueError:
            return jsonify({"ok": False, "error": "invalid_remind_at"}), 400

        conn = db.connect()
        try:
            chain = conn.execute(
                "SELECT id, source_note, current_version FROM review_chains WHERE id=?",
                (chain_id,),
            ).fetchone()
            if not chain:
                return jsonify({"ok": False, "error": "review_not_found"}), 404

            v_row = conn.execute(
                "SELECT judgment FROM review_versions WHERE chain_id=? AND version_no=?",
                (chain_id, chain["current_version"]),
            ).fetchone()
            judgment_text = v_row["judgment"] if v_row else ""
            event_title = f"复盘提醒: {judgment_text[:50]}"
            ts = database.now_ts()
            with conn:
                cur = conn.execute(
                    """INSERT INTO news_reminders
                   (item_id, item_title_snapshot, item_url_snapshot,
                    event_title, event_date, remind_at, note, status,
                    review_chain_id, created_at, updated_at)
                   VALUES (NULL, ?, '', ?, ?, ?, ?, 'active', ?, ?, ?)""",
                    (chain["source_note"][:200], event_title, event_date, remind_at,
                     note[:REVIEW_TEXT_MAX_LEN], chain_id, ts, ts),
                )
                reminder_id = cur.lastrowid

            return jsonify({"ok": True, "reminder_id": reminder_id})
        finally:
            conn.close()
