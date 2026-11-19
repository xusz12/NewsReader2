from __future__ import annotations

import sqlite3
from contextlib import closing

from flask import jsonify, request

import periodic_reports
import reader_sources
import report_reader
from feed_sources import visible_news_sql
from nr import news_chat, highlights, ideas


def report_request_error(exc):
    code = str(exc)
    if code not in {"report_preview_invalid", "report_preview_conflict", "report_database_changed",
                    "report_preview_only", "report_preview_running", "report_not_found", "report_window_invalid",
                    "report_manual_conflict", "report_delete_running", "report_deleted", "report_model_unconfigured",
                    "report_model_selection_invalid", "report_reasoning_unsupported", "report_model_catalog_failed",
                    "report_model_dependencies_missing", "report_window_changed", "report_read_unavailable",
                    "report_sources_unread"}:
        code = "report_preview_invalid"
    return jsonify(ok=False, error=code), 404 if code == "report_not_found" else 409


def register(app, db, jobs, store, worker):
    def clear_report_agent(report_id):
        store.ensure_agent_db()
        with closing(db.agent_connect()) as conn, conn:
            active_jobs = store._delete_agent_sessions_for_item(conn, report_reader.ID_PREFIX + report_id)
        for job_id in active_jobs:
            worker._terminate_agent_process(job_id)

    @app.get("/api/reports")
    def api_reports():
        try:
            offset = int(request.args.get("offset", "0"))
            limit = int(request.args.get("limit", "30"))
            if offset < 0 or not 1 <= limit <= 200:
                raise ValueError("invalid_report_pagination")
            result = jobs.reports.listing(request.args.get("kind", "daily"), offset, limit, request.args.get("group"))
        except ValueError:
            return jsonify(ok=False, error="invalid_report_query"), 400
        return jsonify(ok=True, **result)

    @app.get("/api/reports/preview-options")
    def api_report_preview_options():
        return jsonify(ok=True, **jobs.reports.preview_options())

    @app.get("/api/reports/manual-options")
    def api_report_manual_options():
        return jsonify(ok=True, **jobs.reports.manual_options())

    @app.post("/api/reports/model-options")
    def api_report_model_options():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(ok=False, error="invalid_payload"), 400
        try:
            kind = body.get("kind", "morning")
            if not isinstance(kind, str) or kind not in periodic_reports.KINDS:
                raise ValueError("report_model_selection_invalid")
            catalog = periodic_reports.report_model.catalog(jobs.reports.settings(), body.get("runtime"),
                periodic_reports.report_model_feature(kind))
            return jsonify(ok=True, catalog=catalog)
        except (ValueError, RuntimeError) as exc:
            return report_request_error(exc)

    @app.post("/api/reports/manual")
    def api_report_create_manual():
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return jsonify(ok=False, error="invalid_payload"), 400
        try:
            if "selection" in body:
                report = jobs.reports.create_selected(body.get("kind"), body.get("selection"), body.get("request_id"), body.get("database_token"), body.get("schedule_times"), body.get("model_override"), body.get("window"))
            else:
                report = jobs.reports.create_manual(body.get("kind"), body.get("period_start"), body.get("period_end"), body.get("request_id"), body.get("database_token"), body.get("model_override"))
        except (ValueError, LookupError, RuntimeError) as exc:
            return report_request_error(exc)
        jobs.reports.start()
        return jsonify(ok=True, report=report), 202

    @app.delete("/api/reports/<report_id>")
    def api_report_delete(report_id):
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return jsonify(ok=False, error="invalid_payload"), 400
        try:
            jobs.reports.delete(report_id, body.get("database_token"))
        except (ValueError, LookupError) as exc:
            return report_request_error(exc)
        clear_report_agent(report_id)
        return jsonify(ok=True)

    @app.get("/api/reports/schedule-status")
    def api_report_schedule_status():
        try:
            return jsonify(ok=True, **jobs.reports.observation())
        except (sqlite3.Error, OSError, ValueError):
            return jsonify(ok=False, error="report_schedule_unavailable"), 503

    @app.post("/api/reports/schedule-pause")
    def api_report_schedule_pause():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get("paused"), bool):
            return jsonify(ok=False, error="invalid_report_pause"), 400
        try:
            result = jobs.reports.set_schedule_paused(body["paused"], body.get("database_token"))
            return jsonify(ok=True, **result)
        except ValueError as exc:
            return report_request_error(exc)
        except (sqlite3.Error, OSError):
            return jsonify(ok=False, error="report_pause_unavailable"), 503

    @app.post("/api/reports/snapshots/clear")
    def api_report_snapshots_clear():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(ok=False, error="invalid_payload"), 400
        try:
            return jsonify(ok=True, **jobs.reports.clear_snapshots(body.get("database_token")))
        except ValueError as exc:
            return report_request_error(exc)
        except RuntimeError:
            return jsonify(ok=False, error="report_materials_busy"), 409
        except (sqlite3.Error, OSError):
            return jsonify(ok=False, error="report_snapshot_clear_failed"), 503

    @app.post("/api/reports/previews")
    def api_report_create_preview():
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return jsonify(ok=False, error="invalid_payload"), 400
        try:
            report = jobs.reports.create_preview(body.get("kind"), body.get("scheduled_at"), body.get("request_id"), body.get("database_token"))
        except (ValueError, LookupError, RuntimeError) as exc:
            return report_request_error(exc)
        jobs.reports.start()
        return jsonify(ok=True, report=report), 202

    @app.delete("/api/reports/<report_id>/preview")
    def api_report_delete_preview(report_id):
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return jsonify(ok=False, error="invalid_payload"), 400
        try:
            jobs.reports.delete_preview(report_id, body.get("database_token"))
        except (ValueError, LookupError) as exc:
            return report_request_error(exc)
        clear_report_agent(report_id)
        return jsonify(ok=True)

    @app.get("/api/reports/<report_id>")
    def api_report_detail(report_id):
        with closing(db.connect()) as conn:
            row = conn.execute("SELECT * FROM periodic_reports WHERE id=?", (report_id,)).fetchone()
            if not row:
                return jsonify(ok=False, error="report_not_found"), 404
            token = conn.execute("SELECT database_token FROM periodic_report_meta WHERE singleton=1").fetchone()[0]
            report = periodic_reports.serialize(row, detail=True)
            if report["status"] == "done":
                item = report_reader.subject(conn, report_reader.ID_PREFIX + report_id)
                context = news_chat.build_agent_context(item, {"content": report_reader.context_text(item)})
                report["reader"] = {"item": {k: v for k, v in item.items() if k not in {"content", "sources"}},
                                    "body": item["content"],
                                    **highlights.load_article_highlights_response(conn, item["url"], "original", item["id"]),
                                    **ideas.article_notes_response(conn, item["id"], item["url"]),
                                    "agent": {**store.load_agent_payload(item["id"]), "context_available": True,
                                              **news_chat.agent_context_metadata(context)}}
                report["source_items"] = reader_sources.source_items(conn, report["content"]["sources"], visible_news_sql())
            return jsonify(ok=True, database_token=token, report=report)

    @app.post("/api/reports/<report_id>/read")
    def api_report_read(report_id):
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return jsonify(ok=False, error="invalid_payload"), 400
        try:
            with closing(db.connect()) as conn, conn:
                report = report_reader.complete_reading(conn, report_id, body.get("database_token"), visible_news_sql())
        except (ValueError, LookupError) as exc:
            return report_request_error(exc)
        return jsonify(ok=True, database_token=body["database_token"], report=report)

    @app.post("/api/reports/<report_id>/retry")
    def api_report_retry(report_id):
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return jsonify(ok=False, error="invalid_payload"), 400
        try:
            jobs.reports.retry(report_id, body.get("database_token"))
        except LookupError:
            return jsonify(ok=False, error="report_not_found"), 404
        except (ValueError, RuntimeError) as exc:
            code = str(exc)
            if code not in {"report_database_changed", "report_model_unconfigured", "report_model_capacity_unknown", "report_model_dependencies_missing"}:
                code = "report_retry_unavailable"
            return jsonify(ok=False, error=code), 409
        jobs.reports.start()
        return jsonify(ok=True), 202
