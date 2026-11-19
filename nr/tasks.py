from __future__ import annotations

import collection_environment
import decision_protocol
import model_apis
import news_classification
import news_collection
import news_versions
import sqlite3
import task_progress
from contextlib import closing
from datetime import datetime
from feed_sources import visible_news_sql
from flask import jsonify, request

from nr import configuration, preferences


def classification_config(provider=None) -> tuple[str, str, str]:
    settings = configuration.current_runtime_settings()
    if provider is None:
        provider = settings["classification"]["provider"]
    model = settings["api_models"][provider]["model"] or model_apis.API_PROVIDERS[provider]["default_model"]
    return preferences.provider_secret_value(provider), model, provider


def decision_service_status(classification):
    service = classification
    with service.lock:
        primary = service.provider
        backup = decision_protocol.alternate(primary)
        primary_key = preferences.provider_has_configured_key(primary)
        route = primary
        waiting = False
        if service.fallback_enabled and (not primary_key or service.gate.last_error or service.gate.cooling()):
            backup_key = preferences.provider_has_configured_key(backup)
            backup_lane = service.lanes[backup]
            waiting = not backup_key or bool(backup_lane.last_error) or backup_lane.cooling()
            if not waiting:
                route = backup
        else:
            backup_key = False
        label = model_apis.API_PROVIDERS[route]["label"]
        return {"provider": primary, "preferred_label": model_apis.API_PROVIDERS[primary]["label"],
                "provider_label": label, "active_provider": None if waiting else route,
                "fallback_enabled": service.fallback_enabled, "using_backup": route != primary,
                "waiting_for_service": waiting, "configured": primary_key or backup_key,
                "error": service.error, "throttle": service.lanes[route].snapshot()}


def register(app, db, jobs):
    @app.get("/api/news/status")
    def api_news_status():
        raw_ids = (request.args.get("ids") or "").strip()
        if not raw_ids:
            return jsonify({"ok": False, "error": "missing_ids"}), 400
        ids = [x.strip() for x in raw_ids.split(",") if x.strip()]
        if not ids:
            return jsonify({"ok": False, "error": "invalid_ids"}), 400
        if len(ids) > 50:
            return jsonify({"ok": False, "error": "too_many_ids"}), 400

        placeholders = ",".join(["?"] * len(ids))
        conn = db.connect()
        try:
            rows = conn.execute(
                f"""
                SELECT items.id,
                       st.favorite_at,
                       st.read_later_at,
                       st.read_later_done_at,
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
                       dj.status AS detail_status,
                       dj.last_error AS detail_error,
                       CASE WHEN ad.url IS NULL THEN 0 ELSE 1 END AS detail_ready,
                       aj.status AS ai_status,
                       aa.conclusion_zh AS ai_conclusion_zh
                FROM items
                LEFT JOIN item_state st ON st.item_id = items.id
                LEFT JOIN news_item_detail_jobs dj ON dj.item_id=items.id
                LEFT JOIN news_item_details ad ON ad.item_id=items.id
                LEFT JOIN news_item_ai_jobs aj ON aj.item_id=items.id
                LEFT JOIN news_item_ai aa ON aa.item_id=items.id
                WHERE items.id IN ({placeholders})
                  AND {visible_news_sql()}
                """,
                ids,
            ).fetchall()
            items = [dict(r) for r in rows]
            news_versions.attach(conn, items)
        finally:
            conn.close()
        return jsonify({"ok": True, "items": items})

    @app.get("/api/error-stats")
    def api_error_stats():
        day = (request.args.get("day") or news_collection.now().date().isoformat()).strip()
        try:
            if datetime.strptime(day, "%Y-%m-%d").strftime("%Y-%m-%d") != day:
                raise ValueError()
        except ValueError:
            return jsonify({"ok": False, "error": "invalid_day"}), 400
        return jsonify(jobs.collection.errors(day, details=request.args.get("details") != "0"))

    @app.get("/api/collection")
    def api_collection_status():
        return jsonify({"ok": True, **jobs.collection.status()})

    @app.post("/api/collection")
    def api_collection_request():
        return jsonify({"ok": True, "run_id": jobs.collection.request()}), 202

    @app.get("/api/collection/environment")
    def api_collection_environment():
        with closing(db.connect()) as conn:
            return jsonify(ok=True, checks=collection_environment.latest(conn))

    @app.post("/api/collection/environment/check")
    def api_collection_environment_check():
        body = request.get_json(silent=True)
        if body is None and not request.content_length:
            body = {}
        if not isinstance(body, dict) or set(body) - {"target"} or body.get("target", "environment") not in ("environment", "x"):
            return jsonify(ok=False, error="invalid_environment_check"), 400
        try:
            target = body.get("target", "environment")
            # Resolve the database before probing, so a later settings change cannot
            # move an in-flight check's result into another library.
            with closing(db.connect()) as conn:
                result = collection_environment.check(target)
                results = collection_environment.remember(conn, target, result)
            return jsonify(ok=True, **result, results=results)
        except collection_environment.Busy:
            return jsonify(ok=False, error="collection_environment_busy"), 409

    @app.post("/api/collection/retry-translation")
    def api_collection_retry():
        body = request.get_json(silent=True)
        if body is None and not request.content_length:
            body = {}
        if not isinstance(body, dict) or set(body) - {"run_id", "source_id", "stage"} or (body and not body.get("run_id")):
            return jsonify({"ok": False, "error": "collection_retry_invalid"}), 400
        try:
            count = jobs.collection.retry(**body)
        except (ValueError, TypeError) as exc:
            code = str(exc) if isinstance(exc, ValueError) else "collection_retry_invalid"
            return jsonify({"ok": False, "error": code}), 404 if code == "collection_retry_target_missing" else 400
        return jsonify({"ok": True, "retried": count}), 202

    @app.get("/api/tasks/progress")
    def api_task_progress():
        try:
            with closing(db.connect()) as conn:
                result = task_progress.snapshot(conn)
            decision = decision_service_status(jobs.classification)
            result['topics'].update(matching_configured=decision['configured'], matching_provider_label=decision['provider_label'],
                                    matching_error=decision["error"], matching_waiting=decision["waiting_for_service"],
                                    using_backup=decision["using_backup"])
            return jsonify(ok=True, **result)
        except (sqlite3.Error, OSError, ValueError):
            return jsonify(ok=False, error="task_progress_unavailable"), 503

    @app.post("/api/tasks/progress/dismiss")
    def api_task_progress_dismiss():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(ok=False, error="invalid_task_notices"), 400
        try:
            with jobs.classification.lock, closing(db.connect()) as conn, conn:
                task_progress.dismiss(conn, body.get("database_token"), body.get("notices"),
                                      unread_only=jobs.classification.unread_only, batch_id=body.get("batch_id"))
            return jsonify(ok=True)
        except ValueError as exc:
            return jsonify(ok=False, error=str(exc)), 409 if str(exc) == "task_database_changed" else 400
        except (sqlite3.Error, OSError):
            return jsonify(ok=False, error="task_notice_clear_failed"), 503

    @app.get("/api/news/classification")
    def api_classification_status():
        with jobs.classification.lock, closing(db.connect()) as conn:
            progress = news_classification.status(conn, unread_only=jobs.classification.unread_only)
            token = conn.execute("SELECT database_token FROM periodic_report_meta WHERE singleton=1").fetchone()[0]
        return jsonify(ok=True, **progress, database_token=token, enabled=jobs.classification.enabled,
                       **decision_service_status(jobs.classification),
                       categories=[{"value": k, "label": v} for k, v in news_classification.CATEGORIES.items()])

    @app.post("/api/news/classification/retry")
    def api_classification_retry():
        with jobs.classification.lock:
            if not jobs.classification.enabled:
                return jsonify(ok=False, error="classification_disabled"), 409
            with closing(db.connect()) as conn:
                retried = news_classification.retry_failed(conn, unread_only=jobs.classification.unread_only)
            jobs.classification.resume()
        return jsonify(ok=True, retried=retried)
