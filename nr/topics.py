from __future__ import annotations

import news_summaries
import sqlite3
import time
import topic_tracking
from contextlib import closing
from flask import jsonify, request

from nr import tasks


def _topic_error(exc):
    code = str(exc)
    messages = {**news_summaries.ERRORS, **topic_tracking.ERRORS}
    if code not in messages:
        code = "topic_invalid_input" if isinstance(exc, (ValueError, TypeError)) else "topic_runtime_failed"
    return jsonify(ok=False, error=code, message=messages.get(code, "主题处理失败。")), (
        404 if code == "topic_not_found" else 409 if code in {"topic_busy", "topic_not_draft", "topic_not_ready", "topic_archived", "topic_retry_required", "topic_refine_cooldown", "topic_definition_changed", "topic_draft_changed", "topic_preview_pending", "topic_preview_changed", "topic_refinement_not_found"} else 400)


def register(app, db, jobs):
    @app.route("/api/topics", methods=["GET", "POST"])
    def api_event_topics():
        try:
            if request.method == "GET":
                decision = tasks.decision_service_status(jobs.classification)
                return jsonify(ok=True, topics=jobs.topics.listing(), matching_configured=decision['configured'],
                               matching_provider_label=decision['provider_label'],
                               matching_error=decision["error"], matching_waiting=decision["waiting_for_service"])
            body = request.get_json(silent=True)
            if not isinstance(body, dict):
                raise ValueError("topic_invalid_input")
            return jsonify(ok=True, topic=jobs.topics.create(body)), 202
        except (ValueError, TypeError, OSError, sqlite3.Error) as exc:
            return _topic_error(exc)

    @app.get("/api/topics/<topic_id>")
    def api_event_topic(topic_id):
        try:
            offset = int(request.args.get("offset", 0))
            limit = int(request.args.get("limit", 40))
            if not 0 <= offset <= 1000000 or not 1 <= limit <= 400:
                raise ValueError("topic_invalid_input")
            return jsonify(ok=True, **jobs.topics.detail(topic_id, offset=offset, limit=limit, node_id=request.args.get("node") or None))
        except (ValueError, TypeError, OSError, sqlite3.Error) as exc:
            return _topic_error(exc)

    @app.post("/api/topics/<topic_id>/initialize")
    def api_event_topic_initialize(topic_id):
        try:
            return jsonify(ok=True, topic=jobs.topics.initialize(topic_id, request.get_json(silent=True))), 202
        except (ValueError, TypeError, OSError, sqlite3.Error) as exc:
            return _topic_error(exc)

    @app.patch("/api/topics/<topic_id>/definition")
    def api_event_topic_definition(topic_id):
        try:
            return jsonify(ok=True, topic=jobs.topics.revise_definition(topic_id, request.get_json(silent=True)))
        except (ValueError, TypeError, OSError, sqlite3.Error) as exc:
            return _topic_error(exc)

    @app.post("/api/topics/<topic_id>/messages")
    def api_event_topic_message(topic_id):
        try:
            return jsonify(ok=True, topic=jobs.topics.discuss(topic_id, request.get_json(silent=True))), 202
        except (ValueError, TypeError, OSError, sqlite3.Error) as exc:
            return _topic_error(exc)

    @app.post("/api/topics/<topic_id>/retry")
    def api_event_topic_retry(topic_id):
        try:
            jobs.topics.retry(topic_id)
            jobs.classification.resume()
            return jsonify(ok=True), 202
        except (ValueError, TypeError, OSError, sqlite3.Error) as exc:
            return _topic_error(exc)

    @app.post("/api/topics/<topic_id>/seen")
    def api_event_topic_seen(topic_id):
        body = request.get_json(silent=True) or {}
        observed = body.get("observed_at") if isinstance(body, dict) else None
        if not isinstance(observed, (int, float)) or isinstance(observed, bool) or not 0 <= observed <= time.time():
            return _topic_error(ValueError("topic_invalid_input"))
        with closing(db.connect()) as conn, conn:
            conn.execute("UPDATE event_topics SET seen_at=MAX(seen_at,?) WHERE id=?", (observed, topic_id))
        return jsonify(ok=True)

    @app.post("/api/topics/<topic_id>/archive")
    def api_event_topic_archive(topic_id):
        try:
            body = request.get_json(silent=True)
            if not isinstance(body, dict):
                raise ValueError("topic_invalid_input")
            jobs.topics.set_archived(topic_id, body.get("archived"))
            return jsonify(ok=True)
        except (ValueError, TypeError, OSError, sqlite3.Error) as exc:
            return _topic_error(exc)

    @app.post("/api/topics/<topic_id>/refine")
    def api_event_topic_refine(topic_id):
        try:
            jobs.topics.refine(topic_id)
            return jsonify(ok=True), 202
        except (ValueError, TypeError, OSError, sqlite3.Error) as exc:
            return _topic_error(exc)

    @app.post("/api/topics/<topic_id>/refinements/<version_id>/<action>")
    def api_event_topic_refinement(topic_id, version_id, action):
        try:
            jobs.topics.change_refinement(topic_id, version_id, action)
            return jsonify(ok=True)
        except (ValueError, TypeError, OSError, sqlite3.Error) as exc:
            return _topic_error(exc)

    @app.delete("/api/topics/<topic_id>")
    def api_event_topic_delete(topic_id):
        with closing(db.connect()) as conn, conn:
            conn.execute("DELETE FROM topic_node_items WHERE node_id IN (SELECT id FROM topic_nodes WHERE topic_id=?)", (topic_id,))
            for table in ("topic_nodes", "topic_checks", "topic_jobs", "topic_definition_messages", "topic_refinements"):
                conn.execute(f"DELETE FROM {table} WHERE topic_id=?", (topic_id,))
            conn.execute("DELETE FROM event_topics WHERE id=?", (topic_id,))
        return jsonify(ok=True)
