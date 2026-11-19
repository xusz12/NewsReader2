from __future__ import annotations

import news_summaries
import reader_sources
import sqlite3
from feed_sources import feed_source_subkey_sql, is_bloomberg_video_url, visible_news_sql
from flask import jsonify, request

from nr import reading


def news_summary_feed_scope(conn):
    """Hidden sources remain summary candidates; global videos are excluded."""
    conn.create_function("feed_source_subkey", 4, feed_source_subkey_sql)
    conn.create_function("is_bloomberg_video_url", 1, lambda url: int(is_bloomberg_video_url(url)))
    visible_where, visible_args = reading._build_news_where_clause("", "unread", "feed", "all")
    visible_count = conn.execute(
        "SELECT COUNT(DISTINCT items.id) FROM items LEFT JOIN item_state st ON st.item_id=items.id "
        + visible_where, visible_args,
    ).fetchone()[0]
    return "WHERE " + visible_news_sql() + " AND st.read_at IS NULL", [], {"feed_visible_unread_count": visible_count}


def _summary_error(exc):
    code = str(exc) if str(exc) in news_summaries.ERRORS or str(exc) in {"invalid_request_id", "invalid_runtime"} else "summary_save_failed"
    return jsonify({"ok": False, "error": code, "message": news_summaries.ERRORS.get(code, "请求格式不正确。")}), 409 if code in {"summary_database_changed", "summary_empty"} else 400


def register(app, jobs):
    @app.get("/api/news-summaries")
    def api_news_summaries():
        try:
            offset = max(0, min(1000000, int(request.args.get("offset", "0"))))
            return jsonify({"ok": True, **jobs.summaries.listing(offset, request.args.get("q", "").strip()[:500], request.args.get("recent") == "1")})
        except (ValueError, OSError, sqlite3.Error) as exc:
            return _summary_error(exc)

    @app.route("/api/news-summaries/scope", methods=["GET", "POST"])
    def api_news_summary_scope():
        try:
            limits = None
            if request.method == "POST":
                body = request.get_json(silent=True)
                if not isinstance(body, dict) or not isinstance(body.get("news_summary"), dict):
                    raise ValueError("invalid_summary_limits")
                limits = body["news_summary"]
            return jsonify({"ok": True, **jobs.summaries.overview(limits)})
        except (ValueError, OSError, sqlite3.Error) as exc:
            return _summary_error(exc)

    @app.post("/api/news-summaries/prepare")
    def api_news_summary_prepare():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"ok": False, "error": "invalid_request_body"}), 400
        try:
            return jsonify({"ok": True, **jobs.summaries.prepare(body.get("request_id"), body.get("database_token"))})
        except (ValueError, OSError, sqlite3.Error) as exc:
            return _summary_error(exc)

    @app.post("/api/news-summaries/previews/<preview_id>/discard")
    def api_news_summary_discard_preview(preview_id):
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"ok": False, "error": "invalid_request_body"}), 400
        try:
            jobs.summaries.discard_preview(preview_id, body.get("database_token"))
            return jsonify({"ok": True})
        except (ValueError, OSError, sqlite3.Error) as exc:
            return _summary_error(exc)

    @app.post("/api/news-summaries")
    def api_news_summary_create():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"ok": False, "error": "invalid_request_body"}), 400
        try:
            if not isinstance(body.get("preview_id"), str) or not body["preview_id"]:
                raise ValueError("summary_preview_invalid")
            job, created = jobs.summaries.create(body.get("request_id"), body.get("database_token"), body["preview_id"])
            return jsonify({"ok": True, "job": job, "created": created}), 202
        except (ValueError, OSError, sqlite3.Error) as exc:
            return _summary_error(exc)

    @app.get("/api/news-summaries/jobs/<job_id>")
    def api_news_summary_job(job_id):
        try:
            job = jobs.summaries.job(job_id)
            return (jsonify({"ok": True, "job": job}) if job else (jsonify({"ok": False, "error": "summary_not_found"}), 404))
        except (ValueError, OSError, sqlite3.Error) as exc:
            return _summary_error(exc)

    @app.post("/api/news-summaries/jobs/<job_id>/cancel")
    def api_news_summary_cancel(job_id):
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"ok": False, "error": "invalid_request_body"}), 400
        try:
            job = jobs.summaries.cancel(job_id, body.get("database_token"))
            return (jsonify({"ok": True, "job": job}) if job else (jsonify({"ok": False, "error": "summary_not_found"}), 404))
        except (ValueError, OSError, sqlite3.Error) as exc:
            return _summary_error(exc)

    @app.get("/api/news-summaries/<summary_id>")
    def api_news_summary_detail(summary_id):
        try:
            detail = jobs.summaries.detail(summary_id, lambda conn, sources: reader_sources.source_items(
                conn, [source for source in sources if not is_bloomberg_video_url(source.get("url"))]))
            return (jsonify({"ok": True, "summary": detail}) if detail else (jsonify({"ok": False, "error": "summary_not_found"}), 404))
        except (ValueError, OSError, sqlite3.Error) as exc:
            return _summary_error(exc)
