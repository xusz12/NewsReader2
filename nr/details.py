from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta

from flask import jsonify, request

import model_apis
import news_versions
import x_accounts
from feed_sources import visible_news_sql
from llm_client import (
    LLMClientError,
    generate_article_ai,
    generate_body_translation_only,
    generate_pi_fallback_translation,
)
from nr import (
    article_fetch,
    configuration,
    database,
    highlights,
    ideas,
    market,
    news_chat,
    reader_items,
    tracked_topics,
)
from nr.reminders import load_item_reminders


def enqueue_detail_job(conn: sqlite3.Connection, item_id: str, url: str, source: str, *, refresh: bool = False, auto_translate: bool | None = None) -> bool:
    ts = database.now_ts()
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    if news_versions.archived(conn, item_id):
        return False
    if auto_translate is None:
        auto_translate = x_accounts.item_auto_translate(conn, item_id) if article_fetch._is_twitter_url(url) else False
    row = conn.execute("SELECT status FROM detail_jobs WHERE url=?", (url,)).fetchone()
    # Duplicate requests must not reset a running attempt or its retry budget.
    if row and row["status"] in ("pending", "running"):
        conn.execute(
            "UPDATE detail_jobs SET item_id=?, source=? WHERE url=?",
            (item_id, source, url),
        )
        return False
    existing_detail = conn.execute(
        "SELECT url FROM article_details WHERE url=?",
        (url,),
    ).fetchone()
    if existing_detail and not refresh:
        if auto_translate:
            enqueue_ai_job(conn, url)
        conn.execute(
            """
            INSERT INTO detail_jobs(url, item_id, source, status, attempts, queued_at, updated_at)
            VALUES (?, ?, ?, 'success', 0, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
              item_id=excluded.item_id,
              source=excluded.source,
              status='success',
              last_error=NULL,
              next_attempt_at=NULL,
              worker_pid=NULL,
              updated_at=excluded.updated_at
            """,
            (url, item_id, source, ts, ts),
        )
        return False

    conn.execute(
        """
        INSERT INTO detail_jobs(url, item_id, source, status, attempts, queued_at, updated_at, auto_translate)
        VALUES (?, ?, ?, 'pending', 0, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
          item_id=excluded.item_id,
          source=excluded.source,
          auto_translate=excluded.auto_translate,
          status='pending',
          attempts=0,
          last_error=NULL,
          started_at=NULL,
          finished_at=NULL,
          next_attempt_at=NULL,
          worker_pid=NULL,
          queued_at=excluded.queued_at,
          updated_at=excluded.updated_at
        """,
        (url, item_id, source, ts, ts, int(auto_translate)),
    )
    return True


def _detail_retry_at(attempts: int, timestamp: str) -> str:
    delay = article_fetch.DETAIL_RETRY_DELAYS[min(max(attempts - 1, 0), len(article_fetch.DETAIL_RETRY_DELAYS) - 1)]
    return (datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S") + timedelta(seconds=delay)).strftime("%Y-%m-%d %H:%M:%S")


def _owns_detail_job(conn: sqlite3.Connection, job: dict) -> bool:
    if news_versions.cache_token(conn, job["url"]) != job.get("cache_token", ""):
        return False
    return conn.execute(
        "SELECT 1 FROM detail_jobs WHERE url=? AND status='running' AND worker_pid=? AND attempts=?",
        (job["url"], os.getpid(), job["attempts"]),
    ).fetchone() is not None


def _finish_detail_failure(conn: sqlite3.Connection, job: dict, error: str) -> None:
    finished = database.now_ts()
    retry = job["attempts"] < article_fetch.DETAIL_MAX_ATTEMPTS and article_fetch._detail_error_is_retryable(error)
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        if not _owns_detail_job(conn, job):
            return
        conn.execute(
            """UPDATE detail_jobs SET status=?, last_error=?, finished_at=?, updated_at=?,
               next_attempt_at=?, worker_pid=NULL WHERE url=?""",
            ("pending" if retry else "failed", error[:500], None if retry else finished,
             finished, _detail_retry_at(job["attempts"], finished) if retry else None, job["url"]),
        )


def _detail_worker_process_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def enqueue_ai_job(conn: sqlite3.Connection, url: str) -> bool:
    ts = database.now_ts()
    existing_ai = conn.execute("SELECT url FROM article_ai WHERE url=?", (url,)).fetchone()
    if existing_ai:
        conn.execute(
            """
            INSERT INTO ai_jobs(url, status, attempts, queued_at, updated_at)
            VALUES (?, 'success', 0, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
              status='success',
              last_error=NULL,
              updated_at=excluded.updated_at
            """,
            (url, ts, ts),
        )
        return False

    row = conn.execute("SELECT status FROM ai_jobs WHERE url=?", (url,)).fetchone()
    if row and row["status"] in ("pending", "running", "success"):
        conn.execute("UPDATE ai_jobs SET updated_at=? WHERE url=?", (ts, url))
        return False

    conn.execute(
        """
        INSERT INTO ai_jobs(url, status, attempts, queued_at, updated_at)
        VALUES (?, 'pending', 0, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
          status='pending',
          queued_at=excluded.queued_at,
          updated_at=excluded.updated_at
        """,
        (url, ts, ts),
    )
    return True


def generate_news_translation(settings: dict, *, title: str, source: str, content: str) -> dict:
    """Use one settings snapshot: preferred HTTP API, other APIs, then optional Pi."""
    config = settings["llm"]["translation"]
    preferred = model_apis.canonical_provider(config["provider"])
    providers = [preferred]
    if config.get("fallback_enabled", True):
        providers += [provider for provider in model_apis.TRANSLATION_PROVIDERS if provider != preferred]
    translate = generate_body_translation_only if source == "Twitter/X" else generate_article_ai
    attempts = []

    def failed(provider, exc):
        # Never persist upstream response bodies, credentials or prompts.
        code = str(exc).split(":", 1)[0]
        if not re.fullmatch(r"[A-Z0-9_]{1,80}", code):
            code = "TRANSLATION_FAILED"
        attempts.append({"provider": provider, "status": "failed", "error": code})

    def completed(provider, result):
        attempts.append({"provider": provider, "status": "success"})
        raw = json.loads(result.get("raw_json") or "{}")
        if not isinstance(raw, dict):
            raw = {}
        raw["translation_route"] = {"preferred_provider": preferred, "provider": provider,
                                    "fallback": provider != preferred, "attempts": attempts}
        return {**result, "raw_json": json.dumps(raw, ensure_ascii=False)}

    for provider in providers:
        model = config["model"] if provider == "deepseek" else settings["api_models"][provider]["model"]
        try:
            result = translate(title=title, source=source, content=content, model=model, provider=provider)
            return completed(provider, result)
        except LLMClientError as exc:
            failed(provider, exc)
    if config.get("pi_fallback_enabled", True):
        pi = settings["llm"]["pi_chat"]
        try:
            result = generate_pi_fallback_translation(title=title, source=source, content=content,
                                                      pi_provider=pi["provider"], pi_model=pi["model"],
                                                      **({"reasoning_effort": pi["reasoning_effort"]} if pi.get("reasoning_effort") else {}))
            if source == "Twitter/X":
                result.update(key_points_zh=[], conclusion_zh="")
            return completed("pi", result)
        except LLMClientError as exc:
            failed("pi", exc)
    raise LLMClientError("TRANSLATION_PROVIDERS_FAILED: " + " | ".join(
        f"{attempt['provider']}:{attempt['error']}" for attempt in attempts))


class DetailWorker:
    def __init__(self, db, fetcher, start_lock):
        self.db = db
        self.fetcher = fetcher
        self.start_lock = start_lock
        self.thread = None
        self.stop_event = threading.Event()

    def recover_interrupted_detail_jobs(self) -> int:
        """Called before starting this process's worker, never during a live attempt.

    Preserve attempts across restarts, including legacy running rows without an
    owner. A second app sharing the DB must leave another live worker alone.
    """
        conn = self.db.connect()
        recovered = 0
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                for row in conn.execute("SELECT url, attempts, worker_pid FROM detail_jobs WHERE status='running'").fetchall():
                    owner = row["worker_pid"]
                    if owner != os.getpid() and _detail_worker_process_alive(owner):
                        continue
                    attempts = max(1, int(row["attempts"] or 0))
                    ts = database.now_ts()
                    retry = attempts < article_fetch.DETAIL_MAX_ATTEMPTS
                    conn.execute(
                        """UPDATE detail_jobs SET status=?, attempts=?, last_error=?,
                       finished_at=?, updated_at=?, next_attempt_at=?, worker_pid=NULL WHERE url=?""",
                        ("pending" if retry else "failed", attempts,
                         "INTERRUPTED: previous detail worker stopped before completion",
                         None if retry else ts, ts, _detail_retry_at(attempts, ts) if retry else None, row["url"]),
                    )
                    recovered += 1
            return recovered
        finally:
            conn.close()

    def process_pending_jobs_once(self) -> bool:
        conn = self.db.connect()
        try:
            # Claim atomically, and count the attempt before invoking the subprocess
            # so a crash cannot reset the retry budget.
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                started = database.now_ts()
                row = conn.execute(
                    """SELECT url, item_id, source, attempts, auto_translate FROM detail_jobs
                   WHERE status='pending' AND (next_attempt_at IS NULL OR next_attempt_at<=?)
                   ORDER BY queued_at ASC LIMIT 1""", (started,),
                ).fetchone()
                if not row:
                    return False
                job = dict(row)
                job["cache_token"] = news_versions.cache_token(conn, job["url"])
                if int(job["attempts"] or 0) >= article_fetch.DETAIL_MAX_ATTEMPTS:
                    conn.execute(
                        """UPDATE detail_jobs SET status='failed', last_error='RETRY_EXHAUSTED',
                       finished_at=?, updated_at=?, next_attempt_at=NULL, worker_pid=NULL WHERE url=?""",
                        (started, started, job["url"]),
                    )
                    return True
                job["attempts"] = int(job["attempts"] or 0) + 1
                conn.execute(
                    """UPDATE detail_jobs SET status='running', attempts=?, started_at=?, updated_at=?,
                   finished_at=NULL, next_attempt_at=NULL, worker_pid=? WHERE url=?""",
                    (job["attempts"], started, started, os.getpid(), job["url"]),
                )

            try:
                ok, detail, error = self.fetcher.run_opencli_detail(job["url"])
                if not ok:
                    _finish_detail_failure(conn, job, error)
                    return True
                finished = database.now_ts()
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    if not _owns_detail_job(conn, job):
                        return True
                    conn.execute(
                        """
                    INSERT INTO article_details(
                      url, source, title, author, published_at, content,
                      content_length, raw_json, fetched_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(url) DO UPDATE SET
                      source=excluded.source,
                      title=excluded.title,
                      author=excluded.author,
                      published_at=excluded.published_at,
                      content=excluded.content,
                      content_length=excluded.content_length,
                      raw_json=excluded.raw_json,
                      fetched_at=excluded.fetched_at,
                      updated_at=excluded.updated_at
                    """,
                        (
                            job["url"],
                            detail["source"],
                            detail["title"],
                            detail["author"],
                            detail["published_at"],
                            detail["content"],
                            detail["content_length"],
                            detail["raw_json"],
                            finished,
                            finished,
                        ),
                    )
                    conn.execute(
                        """
                    UPDATE detail_jobs
                    SET status='success',
                        last_error=NULL,
                        next_attempt_at=NULL,
                        worker_pid=NULL,
                        finished_at=?,
                        updated_at=?
                    WHERE url=?
                    """,
                        (finished, finished, job["url"]),
                    )
                    if detail["source"] != "Twitter/X" or job["auto_translate"]:
                        enqueue_ai_job(conn, job["url"])
            except Exception as exc:
                logging.getLogger(__name__).exception("Detail job failed for %s", job["url"])
                _finish_detail_failure(conn, job, f"DETAIL_JOB_ERROR: {exc}")
            return True
        finally:
            conn.close()

    def process_pending_ai_once(self) -> bool:
        conn = self.db.connect()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                job = conn.execute("""SELECT url, attempts FROM ai_jobs WHERE status='pending'
                ORDER BY queued_at ASC LIMIT 1""").fetchone()
                if not job:
                    return False
                cache_token = news_versions.cache_token(conn, job["url"])
                detail = conn.execute("SELECT title, source, content FROM article_details WHERE url=?",
                                      (job["url"],)).fetchone()
                started = database.now_ts()
                if not detail or not (detail["content"] or "").strip():
                    conn.execute("""UPDATE ai_jobs SET status='failed', attempts=?, last_error=?,
                    finished_at=?, updated_at=? WHERE url=?""",
                        (int(job["attempts"] or 0) + 1, "MISSING_ARTICLE_DETAILS", started, started, job["url"]))
                    return True
                conn.execute("UPDATE ai_jobs SET status='running', started_at=?, updated_at=? WHERE url=?",
                             (started, started, job["url"]))

            ok = False
            payload: dict = {}
            error = ""
            try:
                payload = generate_news_translation(
                    configuration.current_runtime_settings(), title=(detail["title"] or "").strip(),
                    source=(detail["source"] or "").strip(), content=(detail["content"] or "").strip())
                ok = True
            except LLMClientError as exc:
                error = str(exc)
            except Exception:
                error = "AI_UNKNOWN_ERROR"

            finished = database.now_ts()
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                # A source deletion or retry can revoke an in-flight translation.
                if news_versions.cache_token(conn, job["url"]) != cache_token:
                    return True
                if not conn.execute("SELECT 1 FROM ai_jobs WHERE url=? AND status='running' AND started_at=?",
                                    (job["url"], started)).fetchone():
                    return True
                if ok:
                    conn.execute(
                        """
                    INSERT INTO article_ai(
                      url, model, key_points_zh, conclusion_zh, body_zh, raw_json, generated_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(url) DO UPDATE SET
                      model=excluded.model,
                      key_points_zh=excluded.key_points_zh,
                      conclusion_zh=excluded.conclusion_zh,
                      body_zh=excluded.body_zh,
                      raw_json=excluded.raw_json,
                      generated_at=excluded.generated_at,
                      updated_at=excluded.updated_at
                    """,
                        (
                            job["url"],
                            payload.get("model", "deepseek-v4-flash"),
                            json.dumps(payload.get("key_points_zh", []), ensure_ascii=False),
                            payload.get("conclusion_zh", ""),
                            payload.get("body_zh", ""),
                            payload.get("raw_json", "{}"),
                            finished,
                            finished,
                        ),
                    )
                    conn.execute(
                        """
                    UPDATE ai_jobs
                    SET status='success', last_error=NULL, finished_at=?, updated_at=?
                    WHERE url=?
                    """,
                        (finished, finished, job["url"]),
                    )
                else:
                    conn.execute(
                        """
                    UPDATE ai_jobs
                    SET status='failed', attempts=?, last_error=?, finished_at=?, updated_at=?
                    WHERE url=?
                    """,
                        (int(job["attempts"] or 0) + 1, error[:500], finished, finished, job["url"]),
                    )
            return True
        finally:
            conn.close()

    def detail_worker_loop(self) -> None:
        while not self.stop_event.is_set():
            processed = False
            try:
                processed = self.process_pending_jobs_once()
            except Exception:
                logging.getLogger(__name__).exception("Detail worker could not process its queue")
            if not processed:
                try:
                    processed = self.process_pending_ai_once()
                except Exception:
                    pass
            if processed:
                time.sleep(2)
            else:
                self.stop_event.wait(2)

    def start_detail_worker(self) -> None:
        with self.start_lock:
            if self.thread and self.thread.is_alive():
                return
            self.recover_interrupted_detail_jobs()
            self.stop_event.clear()
            self.thread = threading.Thread(target=self.detail_worker_loop, name="detail-worker", daemon=True)
            self.thread.start()


def register(app, db, store):
    @app.get("/api/news/<item_id>/detail")
    def api_news_detail(item_id: str):
        conn = db.connect()
        try:
            item = conn.execute(
                f"""
                SELECT
                  items.id,
                  items.title,
                  items.url,
                  items.source,
                  items.source_name,
                  items.source_type,
                  items.summary,
                  items.published_at
                FROM items
                WHERE items.id=?
                  AND {visible_news_sql()}
                """,
                (item_id,),
            ).fetchone()
            if not item:
                return jsonify({"ok": False, "error": "item_not_found"}), 404
            url = item["url"] or ""
            version_meta = {"id": item_id}
            news_versions.attach(conn, [version_meta])
            related_ids = [version_meta.get(key) for key in ("previous_item_id", "latest_item_id")
                           if version_meta.get(key) and version_meta[key] != item_id]
            related = reader_items.load_news_item_map(conn, related_ids) if related_ids else {}
            version_meta["previous_version"] = related.get(version_meta.get("previous_item_id"))
            version_meta["latest_version"] = related.get(version_meta.get("latest_item_id"))
            job = None
            notes = ideas.article_note_entries(conn, url) if url else []
            detail = None
            ai_job = None
            ai = None
            note_row = None
            market_tags: list[dict] = []
            tracked_topic_choices: list[dict] = []
            reminders: list[dict] = []
            highlight_payload = {
                "body_ready": False,
                "body_kind": highlights.ARTICLE_HIGHLIGHT_BODY_KIND,
                "content_hash": None,
                "highlights": [],
                "highlight_summary": {"total": 0, "active": 0, "orphan": 0},
                "highlight_surfaces": {
                    body_kind: {
                        "body_ready": False,
                        "body_kind": body_kind,
                        "content_hash": None,
                        "highlights": [],
                        "highlight_summary": {"total": 0, "active": 0, "orphan": 0},
                    }
                    for body_kind in sorted(highlights.ARTICLE_HIGHLIGHT_BODY_KINDS)
                },
            }
            reminder_summary = {
                "active_total": 0,
                "due_total": 0,
                "done_total": 0,
                "dismissed_total": 0,
                "total": 0,
            }
            state_row = conn.execute(
                "SELECT read_at, important_at, read_later_at, read_later_done_at, favorite_at FROM item_state WHERE item_id=?",
                (item_id,),
            ).fetchone()
            market_tag_choices = market.load_market_tag_definitions(conn, active_only=True)
            tracked_topic_choices = tracked_topics.load_tracked_topic_choices(conn)
            reminders = load_item_reminders(conn, item_id)
            reminder_summary = {
                "total": len(reminders),
                "active_total": sum(1 for reminder in reminders if reminder["status"] == "active"),
                "due_total": sum(1 for reminder in reminders if reminder["is_due"]),
                "done_total": sum(1 for reminder in reminders if reminder["status"] == "done"),
                "dismissed_total": sum(1 for reminder in reminders if reminder["status"] == "dismissed"),
            }
            detail = None
            detail_out = None
            if url:
                job = conn.execute(
                    "SELECT status, attempts, last_error, queued_at, started_at, finished_at, next_attempt_at FROM news_item_detail_jobs WHERE item_id=?",
                    (item_id,),
                ).fetchone()
                ai_job = conn.execute(
                    "SELECT status, attempts, last_error, queued_at, started_at, finished_at FROM news_item_ai_jobs WHERE item_id=?",
                    (item_id,),
                ).fetchone()
                detail = conn.execute(
                    "SELECT title, author, published_at, content, content_length, fetched_at, raw_json FROM news_item_details WHERE item_id=?",
                    (item_id,),
                ).fetchone()
                ai = conn.execute(
                    "SELECT model, key_points_zh, conclusion_zh, body_zh, raw_json, generated_at FROM news_item_ai WHERE item_id=?",
                    (item_id,),
                ).fetchone()
                note_row = conn.execute(
                    "SELECT note, created_at, updated_at FROM article_notes WHERE url=?",
                    (url,),
                ).fetchone()
                market_tags = reader_items.load_market_tags_map(conn, [url]).get(url, [])
                highlight_payload = highlights.load_article_highlights_response(conn, url, item_id=item_id)

                detail_out = dict(detail) if detail else None
                if detail_out and (article_fetch._is_twitter_url(url) or item["source_type"] == "twitter"):
                    try:
                        raw_payload = json.loads(detail_out.get("raw_json") or "{}")
                        detail_out["media_images"] = article_fetch._sanitize_twitter_media_images(raw_payload.get("media_images"))
                        for image in detail_out["media_images"]:
                            cached = conn.execute(
                                "SELECT cache_key FROM media_cache WHERE url=? AND status='success'",
                                (image["url"],),
                            ).fetchone()
                            if cached:
                                image["cached_url"] = f"/api/media-cache/{cached['cache_key']}"
                    except Exception:
                        detail_out["media_images"] = []
                if detail_out and "raw_json" in detail_out:
                    del detail_out["raw_json"]
        finally:
            conn.close()

        agent_context = news_chat.build_agent_context(item, detail)
        agent_payload = store.load_agent_payload(item_id)
        agent_payload["context_available"] = bool(agent_context)
        agent_payload.update(news_chat.agent_context_metadata(agent_context or {}))
        agent_payload["context_hash"] = agent_context["context_hash"] if agent_context else ""
        if agent_payload.get("session"):
            agent_payload["context_updated"] = bool(
                agent_context and agent_payload["session"].get("context_hash") != agent_context["context_hash"]
            )

        return jsonify(
            {
                "ok": True,
                "item_id": item_id,
                **version_meta,
                "url": item["url"],
                "read_at": (state_row["read_at"] if state_row else None),
                "important_at": (state_row["important_at"] if state_row else None),
                "read_later_at": (state_row["read_later_at"] if state_row else None),
                "read_later_done_at": (state_row["read_later_done_at"] if state_row else None),
                "favorite_at": (state_row["favorite_at"] if state_row else None),
                "detail_status": (job["status"] if job else "none"),
                "job": dict(job) if job else None,
                "detail": detail_out,
                "ai_status": (ai_job["status"] if ai_job else "none"),
                "ai_job": dict(ai_job) if ai_job else None,
                "ai": dict(ai) if ai else None,
                "has_note": 1 if note_row else 0,
                "note": (dict(note_row) if note_row else None),
                "notes": notes,
                "highlights": highlight_payload["highlights"],
                "highlight_summary": highlight_payload["highlight_summary"],
                "highlight_body_ready": highlight_payload["body_ready"],
                "highlight_body_kind": highlight_payload["body_kind"],
                "highlight_content_hash": highlight_payload["content_hash"],
                "highlight_surfaces": highlight_payload["highlight_surfaces"],
                "market_tags": market_tags,
                "has_market_tags": 1 if market_tags else 0,
                "market_tag_choices": market_tag_choices,
                "tracked_topic_choices": tracked_topic_choices,
                "reminders": reminders,
                "reminder_summary": reminder_summary,
                "chat_context": news_chat.build_news_chat_context(
                    item=item,
                    detail=detail,
                    ai=ai,
                    note_row=note_row,
                    market_tags=market_tags,
                ),
                "agent": agent_payload,
            }
        )

    @app.post("/api/news/<item_id>/detail/retry")
    def api_news_detail_retry(item_id: str):
        conn = db.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT id, url, source, source_type FROM items WHERE id=?", (item_id,)).fetchone()
            if not row:
                return jsonify({"ok": False, "error": "item_not_found"}), 404
            if not row["url"]:
                return jsonify({"ok": False, "error": "missing_url"}), 400
            if news_versions.archived(conn, item_id):
                return jsonify({"ok": False, "error": "news_version_archived"}), 409
            body = request.get_json(silent=True) or {}
            mode = (request.args.get("mode") or body.get("mode") or "").strip().lower()
            url = row["url"]
            is_twitter = article_fetch._is_twitter_url(url) or row["source_type"] == "twitter"
            has_detail = conn.execute("SELECT 1 FROM article_details WHERE url=?", (url,)).fetchone()
            with conn:
                ts = database.now_ts()
                if has_detail and (not is_twitter or mode == "ai"):
                    enqueue_ai_job(conn, url)
                    conn.execute(
                        """
                    UPDATE ai_jobs
                    SET status='pending', last_error=NULL, queued_at=?, updated_at=?
                    WHERE url=?
                    """,
                        (ts, ts, url),
                    )
                else:
                    # A completed/failed job gets a fresh budget; an active one is
                    # left alone. Existing X content stays readable while refreshing.
                    enqueue_detail_job(conn, row["id"], url, row["source"] or "", refresh=True)
        finally:
            conn.close()
        return jsonify({"ok": True})
