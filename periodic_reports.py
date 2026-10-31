"""Scheduled, immutable editions generated from full time-window news snapshots."""
from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, time as daytime, timedelta
import json
import errno
import logging
import os
import sqlite3
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit
import uuid
from zoneinfo import ZoneInfo

import report_model
import report_schedule
from report_generation import generate, parse_json, partition, validate_events, validate_report

ROOT = Path(__file__).resolve().parent
TZ = ZoneInfo("Asia/Shanghai")
KINDS = {"morning": "早报", "noon": "午报", "evening": "晚报", "weekly": "周报", "monthly": "月报"}
REPORT_KINDS = {**KINDS, "custom": "自定义"}
LEASE_SECONDS = 1200
MAX_CONCURRENT_REPORTS = 3
LEASE_RENEW_SECONDS = 60
SNAPSHOT_RETENTION = timedelta(days=7)
PREVIEW_PREFIX = "trial:"
MANUAL_PREFIX = "manual:"
OBSERVATION_COLUMNS = "id,edition_key,kind,title,period_start,period_end,scheduled_at,status,error,attempts,next_attempt,updated_at,created_at,progress_json,started_at,generation_seconds,model_label,snapshot_id,snapshot_captured_at,(snapshot_json IS NOT NULL OR snapshot_id IS NOT NULL) AS has_snapshot"
INDEX_ERRORS = {
    "report_index_failed", "report_index_source_missing", "report_index_not_directory",
    "report_index_permission_denied", "report_index_database_locked",
    "report_index_disk_full", "report_index_invalid_file", "report_index_database_error",
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS periodic_report_meta (
  singleton INTEGER PRIMARY KEY CHECK(singleton=1), database_token TEXT NOT NULL,
  activated_at TEXT NOT NULL, scheduled_through TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS periodic_reports (
  id TEXT PRIMARY KEY, edition_key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
  title TEXT NOT NULL, period_start TEXT NOT NULL, period_end TEXT NOT NULL,
  scheduled_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
  lease TEXT, lease_until REAL, error TEXT NOT NULL DEFAULT '',
  snapshot_json TEXT, config_json TEXT, result_json TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_periodic_reports_schedule ON periodic_reports(scheduled_at DESC, id);
CREATE TABLE IF NOT EXISTS periodic_report_steps (
  report_id TEXT NOT NULL, step_key TEXT NOT NULL, result_json TEXT NOT NULL,
  PRIMARY KEY(report_id, step_key)
);
CREATE TABLE IF NOT EXISTS periodic_report_deletions (
  edition_key TEXT PRIMARY KEY, deleted_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS periodic_report_snapshots (
  id TEXT PRIMARY KEY, period_start TEXT NOT NULL, period_end TEXT NOT NULL,
  captured_at TEXT NOT NULL, snapshot_json TEXT NOT NULL,
  UNIQUE(period_start, period_end)
);
CREATE TABLE IF NOT EXISTS periodic_report_snapshot_cleanup (
  singleton INTEGER PRIMARY KEY CHECK(singleton=1), next_run_at TEXT NOT NULL,
  last_run_at TEXT, last_attempt_at TEXT, error TEXT NOT NULL DEFAULT '',
  retry_at TEXT, removed_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS periodic_report_schedule_pauses (
  id INTEGER PRIMARY KEY, paused_at TEXT NOT NULL, resumed_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_periodic_report_active_pause
  ON periodic_report_schedule_pauses((1)) WHERE resumed_at IS NULL;
"""


def now() -> datetime:
    return datetime.now(TZ)


def local(value) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    return parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ)


def stamp(value: datetime) -> str:
    return local(value).isoformat(timespec="seconds")


def encoded(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def initialize(conn, at=None):
    conn.executescript(SCHEMA)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(periodic_reports)")}
    for name, declaration in (("progress_json", "TEXT NOT NULL DEFAULT '{}'"), ("started_at", "TEXT"),
                              ("model_label", "TEXT NOT NULL DEFAULT ''"), ("snapshot_id", "TEXT"),
                              ("snapshot_captured_at", "TEXT"), ("generation_seconds", "REAL")):
        if name not in columns:
            conn.execute(f"ALTER TABLE periodic_reports ADD COLUMN {name} {declaration}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_periodic_reports_window ON periodic_reports(period_start,period_end)")
    if "model_label" not in columns:
        for ident, raw in conn.execute("SELECT id,config_json FROM periodic_reports WHERE config_json IS NOT NULL").fetchall():
            try:
                label = model_label(json.loads(raw))
            except (ValueError, TypeError, AttributeError):
                continue
            conn.execute("UPDATE periodic_reports SET model_label=? WHERE id=?", (label, ident))
    if "generation_seconds" not in columns:
        # Older completed jobs have a persisted execution start and finish.
        # Imported samples without a start remain unknown, never zero-filled.
        conn.execute("""UPDATE periodic_reports SET generation_seconds=
            ROUND((julianday(updated_at)-julianday(started_at))*86400,3)
            WHERE status='done' AND julianday(updated_at)>=julianday(started_at)""")
    ts = stamp(at or now())
    conn.execute("INSERT OR IGNORE INTO periodic_report_meta VALUES (1, ?, ?, ?)", (uuid.uuid4().hex, ts, ts))
    conn.execute("INSERT OR IGNORE INTO periodic_report_snapshot_cleanup(singleton,next_run_at) VALUES (1,?)",
                 (stamp(local(ts) + SNAPSHOT_RETENTION),))
    conn.commit()


def calendar_data():
    path = Path(os.environ.get("NEWS_READER_TRADING_CALENDAR_PATH") or ROOT / "reports/trading-calendar.json")
    return json.loads(path.read_text(encoding="utf-8"))


def trading_day(day: date, calendar: dict) -> bool | None:
    if day.weekday() >= 5:
        return False  # A make-up working weekend is still a market holiday.
    year = calendar.get(str(day.year))
    if not year:
        return None
    return not any(start <= day.isoformat() <= end for start, end in year["closed_ranges"])


def slots(day: date, calendar: dict, schedule=None):
    schedule = schedule or report_schedule.Schedule()
    result = []
    for kind in KINDS:
        if kind in {"morning", "noon"} and not trading_day(day, calendar):
            continue
        if (kind == "weekly" and day.weekday() != 0) or (kind == "monthly" and day.day != 1):
            continue
        occurrence = schedule.occurrence(day, kind)
        if occurrence is None:
            continue
        scheduled, _ = occurrence
        start, end = schedule.window(day, kind, scheduled)
        # Old saved schedules may predate the coverage cutoff. Preserve the saved
        # settings, but never capture a window before its news can have arrived.
        result.append((kind, max(scheduled, end), start, end))
    return sorted(result, key=lambda slot: (slot[1], slot[0]))


def edition_title(kind, scheduled, start, end):
    if kind == "monthly":
        return f"{start:%Y年%m月}月报"
    if kind == "weekly":
        return f"{(end-timedelta(days=7)):%m月%d日}—{(end-timedelta(days=1)):%m月%d日}周报"
    return f"{scheduled:%m月%d日}{KINDS[kind]}"

def selected_period(kind, selection, windows=None):
    """Resolve a selected day/month in Beijing time, independently of the browser."""
    try:
        if kind not in KINDS or not isinstance(selection, str):
            raise ValueError()
        selected = date.fromisoformat(selection + "-01" if kind == "monthly" else selection)
        if selection != selected.isoformat()[:7 if kind == "monthly" else 10]:
            raise ValueError()
        if kind == "weekly":
            selected += timedelta(days=7)
        if kind == "monthly":
            selected = (selected.replace(day=28)+timedelta(days=4)).replace(day=1)
        return report_schedule.news_window(selected, kind, windows)
    except (ValueError, TypeError, KeyError, OverflowError):
        raise ValueError("report_window_invalid") from None


def preview_periods(at=None, schedule=None):
    """Offer the latest complete edition of each kind, without moving the schedule."""
    at = local(at or now())
    calendar = calendar_data()
    latest = {}
    for offset in range(63):
        for kind, scheduled, start, end in slots(at.date()-timedelta(days=offset), calendar, schedule):
            if end <= at and kind not in latest:
                latest[kind] = {"kind": kind, "kind_label": KINDS[kind],
                                "title": edition_title(kind, scheduled, start, end),
                                "scheduled_at": stamp(scheduled), "period_start": stamp(start), "period_end": stamp(end)}
        if len(latest) == len(KINDS):
            break
    return [latest[kind] for kind in KINDS if kind in latest]


def schedule_control(conn):
    row = conn.execute("SELECT paused_at FROM periodic_report_schedule_pauses WHERE resumed_at IS NULL").fetchone()
    return {"paused": row is not None, "paused_at": row[0] if row else None}


SHARED_SNAPSHOT_IN_USE = """EXISTS (SELECT 1 FROM periodic_reports dependent
    WHERE (dependent.snapshot_id=s.id OR (dependent.period_start=s.period_start AND dependent.period_end=s.period_end))
    AND dependent.status NOT IN ('done','skipped'))"""
LEGACY_SNAPSHOT_CLEARABLE = """r.status IN ('done','skipped') AND NOT EXISTS (
    SELECT 1 FROM periodic_reports active WHERE active.period_start=r.period_start AND active.period_end=r.period_end
    AND active.status NOT IN ('done','skipped'))"""


def snapshot_storage(conn):
    # Count stored UTF-8 bytes, not characters or the size of the entire DB.
    # Shared material is counted once regardless of how many reports reference it.
    row = conn.execute(f"""WITH materials AS (
        SELECT LENGTH(CAST(s.snapshot_json AS BLOB)) AS bytes, NOT ({SHARED_SNAPSHOT_IN_USE}) AS clearable
            FROM periodic_report_snapshots s
        UNION ALL
        SELECT LENGTH(CAST(r.snapshot_json AS BLOB)), {LEGACY_SNAPSHOT_CLEARABLE}
            FROM periodic_reports r WHERE r.snapshot_json IS NOT NULL)
        SELECT COALESCE(SUM(bytes),0),COALESCE(SUM(CASE WHEN clearable THEN bytes ELSE 0 END),0),
            COUNT(*),COALESCE(SUM(clearable),0) FROM materials""").fetchone()
    return {"bytes": row[0], "clearable_bytes": row[1], "protected_bytes": row[0]-row[1],
            "count": row[2], "clearable_count": row[3]}


def prune_snapshots(conn, cutoff=None):
    """Caller holds the index guard and a write transaction; None clears all idle ages."""
    unused = f"""SELECT s.id FROM periodic_report_snapshots s WHERE (? IS NULL OR s.captured_at<=?)
        AND NOT ({SHARED_SNAPSHOT_IN_USE})"""
    conn.execute(f"UPDATE periodic_reports SET snapshot_id=NULL WHERE snapshot_id IN ({unused})", (cutoff, cutoff))
    removed = conn.execute(f"DELETE FROM periodic_report_snapshots WHERE id IN ({unused})", (cutoff, cutoff)).rowcount
    removed += conn.execute(f"""UPDATE periodic_reports AS r SET snapshot_json=NULL
        WHERE r.snapshot_json IS NOT NULL AND (? IS NULL OR COALESCE(r.snapshot_captured_at,r.updated_at)<=?)
        AND {LEGACY_SNAPSHOT_CLEARABLE}""", (cutoff, cutoff)).rowcount
    return removed


def enqueue_due(conn, at=None, calendar=None, *, intraday_since=None, config=None, schedule=None):
    at = local(at or now())
    # Morning/noon editions are useful when their slot arrives while this
    # service is running. A restart only catches up durable evening/week/month editions.
    intraday_since = local(intraday_since or at)
    conn.execute("BEGIN IMMEDIATE")
    try:
        # Share the write lock with pause/resume: no automatic job can be
        # inserted after a pause has committed. Keep pre-pause backlog intact.
        if schedule_control(conn)["paused"]:
            conn.commit()
            return 0
        calendar = calendar if calendar is not None else calendar_data()
        conn.execute("""UPDATE periodic_reports SET status='skipped',error='report_intraday_expired',
            lease=NULL,lease_until=NULL,updated_at=? WHERE kind IN ('morning','noon')
            AND edition_key NOT LIKE 'trial:%' AND edition_key NOT LIKE 'manual:%' AND scheduled_at<? AND (status IN ('pending','failed')
                OR (status='running' AND lease_until<?))""",
            (stamp(at), stamp(intraday_since), time.time()))
        meta = conn.execute("SELECT * FROM periodic_report_meta WHERE singleton=1").fetchone()
        active = local(meta["activated_at"])
        cursor = local(meta["scheduled_through"])
        until = min(at, cursor + timedelta(days=90))
        if until <= cursor:
            conn.commit()
            return 0
        pauses = [(local(row[0]), local(row[1])) for row in conn.execute(
            "SELECT paused_at,resumed_at FROM periodic_report_schedule_pauses WHERE paused_at<=? AND resumed_at>=?",
            (stamp(until), stamp(cursor)))]
        count = 0
        day = cursor.date()
        while day <= until.date():
            for kind, scheduled, start, end in slots(day, calendar, schedule):
                if not cursor < scheduled <= until or end <= active:
                    continue
                if any(started <= scheduled <= resumed for started, resumed in pauses):
                    continue
                if kind in {"morning", "noon"} and scheduled < intraday_since:
                    continue
                if conn.execute("SELECT 1 FROM periodic_report_deletions WHERE edition_key=?", (f"{kind}:{scheduled.date()}",)).fetchone():
                    continue
                start = max(start, active)
                ts = stamp(at)
                count += conn.execute("""INSERT OR IGNORE INTO periodic_reports
                    (id,edition_key,kind,title,period_start,period_end,scheduled_at,created_at,updated_at,config_json,model_label)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (uuid.uuid4().hex, f"{kind}:{scheduled.date()}", kind,
                    edition_title(kind, scheduled, start, end), stamp(start), stamp(end), stamp(scheduled), ts, ts,
                    encoded(config) if config is not None else None, model_label(config))).rowcount
            day += timedelta(days=1)
        conn.execute("UPDATE periodic_report_meta SET scheduled_through=? WHERE singleton=1", (stamp(until),))
        conn.commit()
        return count
    except Exception:
        conn.rollback()
        raise


def safe_url(value):
    try:
        parsed = urlsplit(value or "")
        return value if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password else ""
    except ValueError:
        return ""


def index_error(exc):
    """Expose an actionable local-index cause without returning raw exception text."""
    if str(exc) in INDEX_ERRORS:
        return str(exc)
    if isinstance(exc, FileNotFoundError):
        return "report_index_source_missing"
    if isinstance(exc, NotADirectoryError):
        return "report_index_not_directory"
    if isinstance(exc, PermissionError):
        return "report_index_permission_denied"
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return "report_index_disk_full"
    if isinstance(exc, sqlite3.Error):
        code = getattr(exc, "sqlite_errorcode", 0) & 0xff
        if code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            return "report_index_database_locked"
        if code == sqlite3.SQLITE_FULL:
            return "report_index_disk_full"
        return "report_index_database_error"
    if isinstance(exc, (UnicodeError, ValueError)):
        return "report_index_invalid_file"
    return "report_index_failed"


def capture(conn, report):
    """Capture after indexing. Newly indexed in-window reports must be included.

    There is deliberately no read-state filter and no cutoff on created_at: the
    pre-generation reindex may discover an article published before the deadline.
    """
    start, end = local(report["period_start"]), local(report["period_end"])
    rows = conn.execute("""SELECT i.*, d.content, d.fetched_at FROM items i
        LEFT JOIN article_details d ON d.url=i.url WHERE i.date>=? AND i.date<=?
        ORDER BY i.published_at, i.id""", ((start-timedelta(days=1)).date().isoformat(), (end+timedelta(days=1)).date().isoformat())).fetchall()
    sources = []
    for row in rows:
        try:
            published = local(row["published_at"])
        except (ValueError, TypeError):
            continue
        if not start <= published < end:
            continue
        body = ""
        try:
            if row["content"] and local(row["fetched_at"]) <= end:
                body = row["content"]
        except (ValueError, TypeError):
            pass
        sources.append({"ref": f"N{len(sources)+1:05}", "id": row["id"], "title": row["title"],
                        "summary": row["summary"] or "", "published_at": stamp(published),
                        "source_name": row["source_name"] or row["source"] or "来源未注明", "url": safe_url(row["url"]),
                        "body": body[:12000], "body_excerpt": len(body) > 12000})
    policy = json.loads((ROOT / "reports/selection-rules.json").read_text(encoding="utf-8"))
    return {"sources": sources, "policy": policy, "captured_at": stamp(now()),
            "period_start": report["period_start"], "period_end": report["period_end"], "kind": report["kind"]}


def model_label(config):
    if not config:
        return ""
    if config.get("runtime") == "codex":
        selected = config.get("codex", {})
        return " · ".join(filter(None, ("Codex", selected.get("model") or selected.get("profile") or "默认模型", selected.get("reasoning_effort"))))
    return " · ".join(filter(None, ("Pi", config.get("model") or "未配置模型", config.get("reasoning_effort"))))


def stored_snapshot(conn, report):
    # Preserve legacy inputs. New reports reference shared materials; editorial
    # kind remains specific to the individual report.
    if report["snapshot_json"]:
        return json.loads(report["snapshot_json"])
    if report["snapshot_id"]:
        row = conn.execute("SELECT snapshot_json FROM periodic_report_snapshots WHERE id=?", (report["snapshot_id"],)).fetchone()
        if row is None:
            raise RuntimeError("report_snapshot_missing")
        return {**json.loads(row[0]), "kind": report["kind"]}
    return None


def serialize(row, detail=False):
    result = json.loads(row["result_json"]) if row["result_json"] else None
    value = {key:row[key] for key in ("id", "kind", "title", "period_start", "period_end", "scheduled_at", "status", "error", "created_at", "updated_at")}
    value["kind_label"] = REPORT_KINDS[row["kind"]]
    value["is_preview"] = row["edition_key"].startswith(PREVIEW_PREFIX)
    value["is_manual"] = row["edition_key"].startswith(MANUAL_PREFIX)
    value["progress"] = progress_record(row)
    value["started_at"] = row["started_at"]
    value.update({key:row[key] for key in ("model_label", "snapshot_id", "snapshot_captured_at", "generation_seconds")})
    value["attempts"] = row["attempts"]
    value["next_retry_at"] = (stamp(datetime.fromtimestamp(row["next_attempt"], TZ))
                              if row["status"] == "pending" and row["error"] and row["next_attempt"] else None)
    value["excerpt"] = " ".join(p["text"] for p in result["overview"]) if result else ""
    if detail:
        value["content"] = result
    return value


def progress_record(row):
    try:
        value = json.loads(row["progress_json"] or "{}")
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def execution_record(row):
    """Small status-only record; never parse or expose news/model snapshots."""
    if row is None:
        return None
    result = {key: row[key] for key in ("id", "kind", "title", "period_start", "period_end", "scheduled_at", "status", "error", "attempts", "updated_at")}
    result["kind_label"] = REPORT_KINDS[row["kind"]]
    result["trigger"] = "manual" if row["edition_key"].startswith(MANUAL_PREFIX) else "preview" if row["edition_key"].startswith(PREVIEW_PREFIX) else (
        "scheduled" if row["edition_key"] == row["kind"] + ":" + row["scheduled_at"][:10] else "imported")
    result["is_preview"] = result["trigger"] == "preview"
    result["progress"] = progress_record(row)
    result["started_at"] = row["started_at"]
    result.update({key:row[key] for key in ("model_label", "snapshot_id", "snapshot_captured_at", "generation_seconds")})
    result["phase"] = ("editing" if row["has_snapshot"] else "indexing") if row["status"] == "running" else None
    result["next_retry_at"] = stamp(datetime.fromtimestamp(row["next_attempt"], TZ)) if row["status"] == "pending" and row["error"] and row["next_attempt"] else None
    return result


class Service:
    def __init__(self, connect, settings, refresh_index, runner=report_model.run, *, index_lock=None, concurrency=MAX_CONCURRENT_REPORTS):
        self.connect, self.settings, self.refresh_index, self.runner = connect, settings, refresh_index, runner
        self.index_lock = index_lock if index_lock is not None else threading.RLock()
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.schedule_wake = threading.Event()
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self.concurrency = max(1, min(MAX_CONCURRENT_REPORTS, int(concurrency)))
        self.threads = []
        self.scheduler_thread = None
        self.started_at = None
        self.last_tick = None
        self.last_schedule_success = None
        self.last_schedule_error = ""
        self.last_database_token = None

    def start(self):
        with self.lock:
            self.threads = [thread for thread in self.threads if thread.is_alive()]
            # Don't revive a stopped pool while its old model calls are exiting.
            if self.stopped.is_set() and self.threads:
                return
            if not self.threads:
                self.started_at = now()
                self.last_tick = None
                self.last_schedule_success = None
                self.last_schedule_error = ""
                self.last_database_token = None
                self.stopped.clear()
                self.ready.clear()
            while len(self.threads) < self.concurrency:
                thread = threading.Thread(target=self.loop, name=f"periodic-report-worker-{len(self.threads)+1}", daemon=True)
                self.threads.append(thread)
                thread.start()
            if not self.scheduler_thread or not self.scheduler_thread.is_alive():
                self.scheduler_thread = threading.Thread(target=self.schedule_loop, name="periodic-report-scheduler", daemon=True)
                self.scheduler_thread.start()
            self.wake.set()
            self.schedule_wake.set()

    def stop(self):
        self.stopped.set()
        self.wake.set()
        self.schedule_wake.set()
        self.ready.set()
        for thread in (*self.threads, self.scheduler_thread):
            if thread and thread is not threading.current_thread():
                thread.join(timeout=1)

    def schedule_once(self, at=None):
        at = local(at or now())
        self.cleanup_snapshots(at)
        if self.started_at is None or (self.last_tick and at - self.last_tick > timedelta(seconds=90)):
            # A machine waking after sleep also misses intraday slots. Model
            # latency cannot trigger this branch: scheduling has its own thread.
            self.started_at = at
        self.last_tick = at
        with closing(self.connect()) as conn:
            paused = schedule_control(conn)["paused"]
        if not paused:
            try:
                calendar = calendar_data()
            except (OSError, ValueError, TypeError):
                raise RuntimeError("report_calendar_failed") from None
            settings = self.settings()
            config = report_model.configuration(settings, resolve=False)
            schedule = report_schedule.Schedule(settings)
        with closing(self.connect()) as conn:
            if not paused:
                enqueue_due(conn, at, calendar, intraday_since=self.started_at, config=config, schedule=schedule)
            meta = conn.execute("SELECT scheduled_through,database_token FROM periodic_report_meta WHERE singleton=1").fetchone()
            cursor = local(meta["scheduled_through"])
            paused = paused or schedule_control(conn)["paused"]
        self.last_schedule_success = at
        self.last_schedule_error = ""
        self.last_database_token = meta["database_token"]
        self.ready.set()
        self.wake.set()
        if paused:
            return 30
        if cursor < at:
            return 0  # Continue a >90-day durable catch-up without delaying live slots.
        upcoming = [scheduled for delta in range(2)
                    for _, scheduled, _, _ in slots(at.date() + timedelta(days=delta), calendar, schedule) if scheduled > at]
        # Wake at the next exact slot, with a cheap watchdog for clock changes,
        # resume from sleep, calendar edits, or a changed database connection.
        return min(30, max(0, (min(upcoming) - at).total_seconds())) if upcoming else 30

    def schedule_loop(self):
        while not self.stopped.is_set():
            self.schedule_wake.clear()
            delay = 30
            try:
                delay = self.schedule_once()
            except Exception as exc:
                self.last_schedule_error = "report_calendar_failed" if str(exc) == "report_calendar_failed" else "report_schedule_failed"
                logging.getLogger(__name__).warning("Report scheduler failed (%s)", type(exc).__name__)
            self.schedule_wake.wait(delay)

    def set_schedule_paused(self, paused, token):
        if not isinstance(paused, bool):
            raise ValueError("invalid_report_pause")
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            if token != conn.execute("SELECT database_token FROM periodic_report_meta WHERE singleton=1").fetchone()[0]:
                raise ValueError("report_database_changed")
            current = schedule_control(conn)
            if paused != current["paused"]:
                if paused:
                    conn.execute("INSERT INTO periodic_report_schedule_pauses(paused_at) VALUES (?)", (stamp(now()),))
                else:
                    # Include the resume instant: an already-due paused edition
                    # must not appear during the first check after resuming.
                    conn.execute("UPDATE periodic_report_schedule_pauses SET resumed_at=MAX(paused_at,?) WHERE resumed_at IS NULL", (stamp(now()),))
            control = schedule_control(conn)
            conn.commit()
        self.schedule_wake.set()
        return {"database_token": token, "schedule_control": control}

    def cleanup_snapshots(self, at=None):
        """Weekly retention; never delay scheduling behind an index refresh."""
        at = local(at or now())
        if not self.index_lock.acquire(blocking=False):
            return
        try:
            with closing(self.connect()) as conn:
                state = conn.execute("SELECT * FROM periodic_report_snapshot_cleanup WHERE singleton=1").fetchone()
                if local(state["retry_at"] or state["next_run_at"]) > at:
                    return
                conn.execute("BEGIN IMMEDIATE")
                # Recheck under the write lock, including across app instances.
                state = conn.execute("SELECT * FROM periodic_report_snapshot_cleanup WHERE singleton=1").fetchone()
                if local(state["retry_at"] or state["next_run_at"]) > at:
                    return
                cutoff = stamp(at - SNAPSHOT_RETENTION)
                # Keep capture timestamps and final sources; only release materials.
                removed = prune_snapshots(conn, cutoff)
                # A missed maintenance slot runs once on resume, then waits 7 days.
                conn.execute("""UPDATE periodic_report_snapshot_cleanup SET next_run_at=?,last_run_at=?,
                    last_attempt_at=?,error='',retry_at=NULL,removed_count=? WHERE singleton=1""",
                    (stamp(at + SNAPSHOT_RETENTION), stamp(at), stamp(at), removed))
                conn.commit()
        except sqlite3.Error as exc:
            logging.getLogger(__name__).warning("Report snapshot cleanup failed (%s)", type(exc).__name__)
            try:
                with closing(self.connect()) as conn, conn:
                    conn.execute("""UPDATE periodic_report_snapshot_cleanup SET error='report_snapshot_cleanup_failed',
                        last_attempt_at=?,retry_at=? WHERE singleton=1""", (stamp(at), stamp(at+timedelta(minutes=5))))
            except sqlite3.Error:
                logging.getLogger(__name__).warning("Cannot persist report snapshot cleanup failure")
        finally:
            self.index_lock.release()

    def clear_snapshots(self, token):
        if not self.index_lock.acquire(blocking=False):
            raise RuntimeError("report_materials_busy")
        try:
            with closing(self.connect()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                if token != conn.execute("SELECT database_token FROM periodic_report_meta WHERE singleton=1").fetchone()[0]:
                    raise ValueError("report_database_changed")
                before = snapshot_storage(conn)
                removed = prune_snapshots(conn)
                after = snapshot_storage(conn)
                conn.commit()
            # A manual cleanup does not postpone the weekly maintenance slot.
            return {"database_token": token, "removed_count": removed,
                    "freed_bytes": before["bytes"]-after["bytes"], "snapshot_storage": after}
        finally:
            self.index_lock.release()

    def loop(self):
        while not self.stopped.is_set():
            if not self.ready.wait(1):
                continue
            if self.stopped.is_set():
                break
            self.wake.clear()
            try:
                if self.run_one():
                    continue
            except Exception as exc:
                logging.getLogger(__name__).warning("Report worker failed (%s)", type(exc).__name__)
            self.wake.wait(30)

    def claim(self):
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""UPDATE periodic_reports SET status=CASE WHEN attempts>=3 THEN 'failed' ELSE 'pending' END,
                lease=NULL,lease_until=NULL,error='report_interrupted' WHERE status='running' AND lease_until<?""", (time.time(),))
            if conn.execute("SELECT COUNT(*) FROM periodic_reports WHERE status='running'").fetchone()[0] >= self.concurrency:
                conn.commit()
                return None
            row = conn.execute("""SELECT * FROM periodic_reports WHERE status='pending' AND next_attempt<=?
                ORDER BY scheduled_at, CASE WHEN kind IN ('morning','noon','evening') THEN 0 WHEN kind='weekly' THEN 1 ELSE 2 END, id LIMIT 1""", (time.time(),)).fetchone()
            if not row:
                conn.commit()
                return None
            lease = uuid.uuid4().hex
            conn.execute("UPDATE periodic_reports SET status='running',attempts=attempts+1,lease=?,lease_until=?,error='',progress_json='{}',started_at=?,updated_at=? WHERE id=?", (lease, time.time()+LEASE_SECONDS, stamp(now()), stamp(now()), row["id"]))
            conn.commit()
            return {**dict(row), "lease": lease, "attempts": row["attempts"]+1}

    def checkpoint(self, job, key, produce):
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT result_json FROM periodic_report_steps WHERE report_id=? AND step_key=?", (job["id"], key)).fetchone()
            if row:
                return json.loads(row["result_json"])
        result = produce()
        with closing(self.connect()) as conn, conn:
            changed = conn.execute("UPDATE periodic_reports SET lease_until=? WHERE id=? AND lease=? AND status='running'", (time.time()+LEASE_SECONDS, job["id"], job["lease"])).rowcount
            if changed != 1:
                raise RuntimeError("report_interrupted")
            conn.execute("INSERT OR REPLACE INTO periodic_report_steps VALUES (?,?,?)", (job["id"], key, encoded(result)))
        return result

    def update_progress(self, job, stage, **details):
        with closing(self.connect()) as conn, conn:
            changed = conn.execute("UPDATE periodic_reports SET progress_json=?,updated_at=?,lease_until=? WHERE id=? AND lease=? AND status='running'",
                (encoded({"stage": stage, **details}), stamp(now()), time.time()+LEASE_SECONDS, job["id"], job["lease"])).rowcount
            if changed != 1:
                raise RuntimeError("report_interrupted")

    def renew_lease(self, job, finished):
        while not finished.wait(LEASE_RENEW_SECONDS) and not self.stopped.is_set():
            try:
                with closing(self.connect()) as conn, conn:
                    changed = conn.execute("UPDATE periodic_reports SET lease_until=? WHERE id=? AND lease=? AND status='running'",
                        (time.time()+LEASE_SECONDS, job["id"], job["lease"])).rowcount
                    if not changed:
                        return
            except sqlite3.Error:
                logging.getLogger(__name__).warning("Report lease renewal temporarily unavailable")

    def shared_snapshot(self, job):
        with closing(self.connect()) as conn:
            existing = stored_snapshot(conn, job)
        if existing is not None:
            return existing
        self.update_progress(job, "waiting_material")
        # Serialise only index/capture, never model execution. Recheck inside
        # the shared index guard so competing workers refresh this window once.
        with self.index_lock:
            with closing(self.connect()) as conn:
                shared = conn.execute("SELECT * FROM periodic_report_snapshots WHERE period_start=? AND period_end=?",
                    (job["period_start"], job["period_end"])).fetchone()
                legacy = None if shared else conn.execute("""SELECT id,snapshot_json FROM periodic_reports
                    WHERE period_start=? AND period_end=? AND snapshot_json IS NOT NULL
                    ORDER BY created_at,id LIMIT 1""", (job["period_start"], job["period_end"])).fetchone()
            material = None
            if not shared:
                if legacy:
                    material = json.loads(legacy["snapshot_json"])
                else:
                    self.update_progress(job, "indexing")
                    try:
                        self.refresh_index()
                    except Exception as exc:
                        logging.getLogger(__name__).warning("Report index refresh failed (%s)", type(exc).__name__)
                        raise RuntimeError(index_error(exc)) from None
                    self.update_progress(job, "capturing")
                    with closing(self.connect()) as conn:
                        conn.execute("BEGIN")
                        material = capture(conn, job)
                material = {key:value for key,value in material.items() if key != "kind"}
            else:
                self.update_progress(job, "reusing_material")
            with closing(self.connect()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                if material is not None:
                    conn.execute("""INSERT OR IGNORE INTO periodic_report_snapshots
                        (id,period_start,period_end,captured_at,snapshot_json) VALUES (?,?,?,?,?)""",
                        (uuid.uuid4().hex, job["period_start"], job["period_end"], material["captured_at"], encoded(material)))
                shared = conn.execute("SELECT * FROM periodic_report_snapshots WHERE period_start=? AND period_end=?",
                    (job["period_start"], job["period_end"])).fetchone()
                changed = conn.execute("""UPDATE periodic_reports SET snapshot_id=?,snapshot_captured_at=?,lease_until=?
                    WHERE id=? AND lease=? AND status='running'""",
                    (shared["id"], shared["captured_at"], time.time()+LEASE_SECONDS, job["id"], job["lease"])).rowcount
                if changed != 1:
                    raise RuntimeError("report_interrupted")
                if legacy and shared["snapshot_json"] == encoded(material):
                    conn.execute("UPDATE periodic_reports SET snapshot_id=?,snapshot_captured_at=? WHERE id=?",
                        (shared["id"], shared["captured_at"], legacy["id"]))
                conn.commit()
            return {**json.loads(shared["snapshot_json"]), "kind": job["kind"]}

    def run_one(self):
        job = self.claim()
        if not job:
            return False
        generation_started = time.monotonic()
        finished = threading.Event()
        keeper = threading.Thread(target=self.renew_lease, args=(job, finished), name="report-lease", daemon=True)
        keeper.start()
        try:
            config = json.loads(job["config_json"]) if job["config_json"] else report_model.configuration(self.settings(), resolve=False)
            if not job["config_json"]:  # Upgrade jobs queued before model binding existed.
                with closing(self.connect()) as conn, conn:
                    changed = conn.execute("UPDATE periodic_reports SET config_json=?,model_label=? WHERE id=? AND lease=? AND status='running'",
                        (encoded(config), model_label(config), job["id"], job["lease"])).rowcount
                    if changed != 1:
                        raise RuntimeError("report_interrupted")
            snapshot = self.shared_snapshot(job)
            progress = lambda stage, **details: self.update_progress(job, stage, source_count=len(snapshot["sources"]), **details)
            progress("preparing")
            if snapshot["sources"] and "limits" not in config:
                config["limits"] = report_model.capacity(config)
                with closing(self.connect()) as conn, conn:
                    changed = conn.execute("UPDATE periodic_reports SET config_json=?,lease_until=? WHERE id=? AND lease=? AND status='running'",
                        (encoded(config), time.time()+LEASE_SECONDS, job["id"], job["lease"])).rowcount
                    if changed != 1:
                        raise RuntimeError("report_interrupted")
            result = generate(snapshot, config, self.runner, lambda key, fn: self.checkpoint(job, key, fn), progress=progress)
            progress("saving")
            with closing(self.connect()) as conn, conn:
                conn.execute("""UPDATE periodic_reports SET status='done',result_json=?,lease=NULL,lease_until=NULL,error='',progress_json='{"stage":"done"}',updated_at=?,generation_seconds=?
                    WHERE id=? AND lease=? AND status='running'""", (encoded(result), stamp(now()),
                    max(0, round(time.monotonic()-generation_started, 3)), job["id"], job["lease"]))
        except Exception as exc:
            error = str(exc)
            if error not in INDEX_ERRORS | {"report_model_unconfigured", "report_model_capacity_unknown", "report_model_timeout", "report_invalid_result", "report_input_too_large", "report_interrupted", "report_tools_not_allowed", "report_snapshot_missing"}:
                error = "report_model_failed"
            with closing(self.connect()) as conn, conn:
                conn.execute("""UPDATE periodic_reports SET status=?,error=?,lease=NULL,lease_until=NULL,next_attempt=?,updated_at=?
                    WHERE id=? AND lease=? AND status='running'""", ("failed" if job["attempts"] >= 3 else "pending", error,
                    time.time() + (60 if job["attempts"] == 1 else 300), stamp(now()), job["id"], job["lease"]))
        finally:
            finished.set()
            keeper.join(timeout=1)
        return True

    def listing(self, kind, offset=0, limit=30, group=None):
        kinds = ("morning", "noon", "evening", "custom") if kind == "daily" else (kind,)
        if kind not in {"daily", "weekly", "monthly"}:
            raise ValueError("invalid_report_kind")
        at = now()
        group_length = 4 if kind == "monthly" else 7
        if group == "current":
            group = stamp(at)[:group_length]
        if group is not None:
            try:
                date.fromisoformat(group + ("-01-01" if group_length == 4 else "-01"))
            except (ValueError, TypeError):
                raise ValueError("invalid_report_group") from None
            if len(group) != group_length:
                raise ValueError("invalid_report_group")
        clause = "kind IN (" + ",".join("?" for _ in kinds) + ")"
        # Archive by the period covered, even when an old report is created today.
        # Weeks, months and custom ranges belong to their starting date; daily
        # editions belong to the date on which their window ends.
        period = "CASE WHEN kind IN ('weekly','monthly','custom') THEN period_start ELSE period_end END"
        group_key = f"substr({period},1,{group_length})"
        with closing(self.connect()) as conn:
            meta = conn.execute("SELECT * FROM periodic_report_meta WHERE singleton=1").fetchone()
            control = schedule_control(conn)
            groups = [dict(row) for row in conn.execute(f"""SELECT {group_key} AS key, COUNT(*) AS count,
                SUM(status IN ('pending','running')) AS active_count FROM periodic_reports
                WHERE {clause} GROUP BY {group_key} ORDER BY key DESC""", kinds)]
            params = list(kinds)
            if group is not None:
                clause += f" AND {group_key}=?"
                params.append(group)
            total = conn.execute(f"SELECT COUNT(*) FROM periodic_reports WHERE {clause}", params).fetchone()[0]
            rows = conn.execute(f"SELECT * FROM periodic_reports WHERE {clause} ORDER BY {period} DESC,created_at DESC,id DESC LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
        calendar = calendar_data()
        schedule = report_schedule.Schedule(self.settings())
        upcoming = []
        for delta in range(35):
            upcoming += [(scheduled, slotkind) for slotkind, scheduled, start, end in slots(at.date()+timedelta(days=delta), calendar, schedule)
                         if not control["paused"] and slotkind in kinds and scheduled > at and end > local(meta["activated_at"])]
        upcoming.sort()
        return {"items": [serialize(row) for row in rows], "total": total, "has_more": offset+len(rows)<total,
                "groups": groups, "group": group, "now": stamp(at),
                "database_token": meta["database_token"], "activated_at": meta["activated_at"],
                "schedule_control": control,
                "next_edition_at": stamp(upcoming[0][0]) if upcoming else None,
                "calendar_warning": str(at.year) not in calendar}

    def observation(self):
        at = now()
        schedule = report_schedule.Schedule(self.settings())
        with closing(self.connect()) as conn:
            conn.execute("BEGIN")
            meta = conn.execute("SELECT * FROM periodic_report_meta WHERE singleton=1").fetchone()
            control = schedule_control(conn)
            cleanup = conn.execute("SELECT * FROM periodic_report_snapshot_cleanup WHERE singleton=1").fetchone()
            storage = snapshot_storage(conn)
            counts = dict(conn.execute("SELECT status,COUNT(*) FROM periodic_reports GROUP BY status"))
            retrying = conn.execute("SELECT COUNT(*) FROM periodic_reports WHERE status='pending' AND error<>''").fetchone()[0]
            recent = conn.execute(f"SELECT {OBSERVATION_COLUMNS} FROM periodic_reports ORDER BY updated_at DESC,id DESC LIMIT 20").fetchall()
            running = conn.execute(f"SELECT {OBSERVATION_COLUMNS} FROM periodic_reports WHERE status='running' ORDER BY started_at,id").fetchall()
            latest = {kind: execution_record(conn.execute(f"SELECT {OBSERVATION_COLUMNS} FROM periodic_reports WHERE kind=? AND edition_key=kind || ':' || substr(scheduled_at,1,10) ORDER BY scheduled_at DESC LIMIT 1", (kind,)).fetchone()) for kind in KINDS}
        calendar_error = False
        try:
            calendar = calendar_data()
            future = [slot for day in range(35) for slot in slots(at.date()+timedelta(days=day), calendar, schedule)]
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            calendar, calendar_error = {}, True
            future = [slot for day in range(35) for slot in slots(at.date()+timedelta(days=day), calendar, schedule)]
        schedules = []
        for kind in KINDS:
            times = [scheduled for slot_kind, scheduled, _, end in future if slot_kind == kind and scheduled > at and end > local(meta["activated_at"])]
            schedules.append({"kind": kind, "kind_label": KINDS[kind],
                              "time": schedule.times[kind], "frequency": report_schedule.FREQUENCIES[kind],
                              "window": schedule.windows[kind],
                              "window_label": report_schedule.window_label(kind, schedule.windows[kind]),
                              "window_end_time": schedule.windows[kind]["end"],
                              "rule": f"{report_schedule.FREQUENCIES[kind]} {schedule.times[kind]}",
                              "next_run_at": stamp(min(times)) if times and not control["paused"] else None,
                              "paused": control["paused"], "latest": latest[kind]})
        schedules.append({"kind": "snapshot_cleanup", "kind_label": "材料快照清理", "editable": False,
                          "frequency": "每 7 天", "rule": "每 7 天", "next_run_at": cleanup["retry_at"] or cleanup["next_run_at"],
                          "latest": None, "last_run_at": cleanup["last_run_at"], "error": cleanup["error"],
                          "removed_count": cleanup["removed_count"]})
        schedules.sort(key=lambda row: row["next_run_at"] or "9999")
        checked = self.last_schedule_success if self.last_database_token == meta["database_token"] else None
        alive = bool(self.scheduler_thread and self.scheduler_thread.is_alive())
        error = "report_calendar_failed" if calendar_error else self.last_schedule_error
        status = ("stopped" if self.stopped.is_set() else "not_running" if not alive else "error" if error else
                  "starting" if checked is None else "delayed" if at-checked > timedelta(seconds=90) else "running")
        return {"observed_at": stamp(at), "database_token": meta["database_token"], "activated_at": meta["activated_at"],
                "schedule_control": control,
                "snapshot_storage": storage,
                "timezone": "Asia/Shanghai", "scheduler": {"status": status, "error": error,
                    "last_checked_at": stamp(checked) if checked else None, "scheduled_through": meta["scheduled_through"]},
                "worker_running": bool(any(thread.is_alive() for thread in self.threads) and not self.stopped.is_set()),
                "concurrency": self.concurrency,
                "queue": {key: counts.get(key, 0) for key in ("running", "pending", "failed", "skipped")},
                "retrying": retrying, "current": execution_record(running[0]) if running else None,
                "current_reports": [execution_record(row) for row in running], "schedules": schedules,
                "recent": [execution_record(row) for row in recent], "calendar_warning": calendar_error or str(at.year) not in calendar}

    def preview_options(self):
        with closing(self.connect()) as conn:
            token = conn.execute("SELECT database_token FROM periodic_report_meta WHERE singleton=1").fetchone()[0]
        return {"database_token": token, "periods": preview_periods(schedule=report_schedule.Schedule(self.settings()))}

    def manual_options(self):
        at = now()
        settings = self.settings()
        times = report_schedule.normalize(settings.get("report_schedule", {}))
        windows = report_schedule.normalize_windows(settings.get("report_windows", {}))
        selected_model = report_model.feature_model_config(settings, "reports")
        label = model_label({"runtime": selected_model.get("runtime"), "codex": selected_model.get("codex", {}),
                             "model": selected_model.get("pi", {}).get("model")})
        try:
            periods = {row["kind"]: row for row in preview_periods(at, report_schedule.Schedule({"report_schedule": times, "report_windows": windows}))}
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            periods = {}
        # Manual ranges are user-selected, independent of trading-day coverage.
        # Normally prefer the most recent complete scheduled window; if a year's
        # calendar is unavailable, still offer all five dated report types.
        for kind in KINDS:
            if kind in periods:
                continue
            if kind in {"morning", "noon", "evening"}:
                start, end = selected_period(kind, at.date().isoformat(), windows)
                if end > at:
                    start, end = start-timedelta(days=1), end-timedelta(days=1)
            elif kind == "weekly":
                start, end = report_schedule.news_window(at.date()-timedelta(days=at.weekday()), kind, windows)
                if end > at:
                    start, end = start-timedelta(days=7), end-timedelta(days=7)
            else:
                start, end = report_schedule.news_window(at.date().replace(day=1), kind, windows)
                if end > at:
                    start, end = report_schedule.news_window(start.date(), kind, windows)
            periods[kind] = {"kind": kind, "kind_label": KINDS[kind], "period_start": stamp(start), "period_end": stamp(end)}
        with closing(self.connect()) as conn:
            token = conn.execute("SELECT database_token FROM periodic_report_meta WHERE singleton=1").fetchone()[0]
        for kind, period in periods.items():
            period["selection"] = period["period_start"][:7] if kind == "monthly" else period["period_start"][:10] if kind == "weekly" else period["period_end"][:10]
        periods["custom"] = {"kind": "custom", "kind_label": REPORT_KINDS["custom"],
                             "period_start": stamp(at-timedelta(days=1)), "period_end": stamp(at)}
        return {"database_token": token, "periods": [periods[kind] for kind in REPORT_KINDS], "now": stamp(at),
                "schedule_times": times, "window_times": dict(report_schedule.WINDOW_TIMES), "windows": windows,
                "model_label": label, "model_settings": report_model.selection(settings)}

    def create_selected(self, kind, selection, request_id, token, schedule_times=None, model_override=None, window=None):
        if not isinstance(kind, str) or kind not in KINDS:
            raise ValueError("report_window_invalid")
        if window is not None:
            try:
                if not isinstance(window, dict) or set(window) != set(report_schedule.DEFAULT_WINDOWS[kind]):
                    raise ValueError()
                windows = report_schedule.normalize_windows({kind: window})
            except ValueError:
                raise ValueError("report_window_invalid") from None
        else:
            windows = report_schedule.normalize_windows(self.settings().get("report_windows", {}))
            # Forms predating editable windows showed the fixed defaults.
            if windows[kind] != report_schedule.DEFAULT_WINDOWS[kind]:
                raise ValueError("report_window_changed")
        start, end = selected_period(kind, selection, windows)
        # Older open forms submit the former coupled boundaries. Do not silently
        # generate a different window than the one those forms showed.
        if schedule_times is not None and kind in report_schedule.WINDOW_TIMES:
            times = report_schedule.normalize(schedule_times)
            used = {"morning": ("evening", "morning"), "noon": ("morning", "noon"), "evening": ("evening",)}[kind]
            if any(times[key] != report_schedule.WINDOW_TIMES[key] for key in used):
                raise ValueError("report_window_changed")
        return self.create_manual(kind, stamp(start), stamp(end), request_id, token, model_override)

    def create_manual(self, kind, period_start, period_end, request_id, token, model_override=None):
        try:
            if not isinstance(kind, str) or kind not in REPORT_KINDS or not all(isinstance(value, str) for value in (period_start, period_end, request_id)):
                raise ValueError()
            request_id = uuid.UUID(request_id).hex
            start, end, at = local(period_start), local(period_end), now()
            if not start < end <= at:
                raise ValueError()
        except (ValueError, TypeError, AttributeError, OverflowError):
            raise ValueError("report_window_invalid") from None
        if kind == "monthly" and start.day == end.day == 1 and (end.date()-timedelta(days=1)).replace(day=1) == start.date():
            title = f"{start:%Y年%m月}月报"
        else:
            last = end-timedelta(seconds=1)
            title = (f"{last:%m月%d日}" if start.date() == last.date() or kind in {"morning", "noon", "evening"} and end-start <= timedelta(days=1)
                     else f"{start:%m月%d日}—{last:%m月%d日}") + ("自定义报告" if kind == "custom" else KINDS[kind])
        key = MANUAL_PREFIX + request_id
        with closing(self.connect()) as conn:
            if token != conn.execute("SELECT database_token FROM periodic_report_meta WHERE singleton=1").fetchone()[0]:
                raise ValueError("report_database_changed")
            if conn.execute("SELECT 1 FROM periodic_report_deletions WHERE edition_key=?", (key,)).fetchone():
                raise ValueError("report_deleted")
            existing = conn.execute("SELECT 1 FROM periodic_reports WHERE edition_key=?", (key,)).fetchone()
        # Metadata validation may start a local CLI. Keep it outside the write
        # transaction, and never re-resolve configuration for a replayed request.
        config = None if existing else report_model.configuration(self.settings(), resolve=False, override=model_override)
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            if token != conn.execute("SELECT database_token FROM periodic_report_meta WHERE singleton=1").fetchone()[0]:
                raise ValueError("report_database_changed")
            if conn.execute("SELECT 1 FROM periodic_report_deletions WHERE edition_key=?", (key,)).fetchone():
                raise ValueError("report_deleted")
            row = conn.execute("SELECT * FROM periodic_reports WHERE edition_key=?", (key,)).fetchone()
            if row:
                if (row["kind"], row["period_start"], row["period_end"]) != (kind, stamp(start), stamp(end)):
                    raise ValueError("report_manual_conflict")
            else:
                ts = stamp(at)
                if config is None:
                    raise ValueError("report_deleted")
                conn.execute("""INSERT INTO periodic_reports
                    (id,edition_key,kind,title,period_start,period_end,scheduled_at,created_at,updated_at,config_json,model_label)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (uuid.uuid4().hex, key, kind, title, stamp(start), stamp(end), stamp(end), ts, ts,
                    encoded(config), model_label(config)))
                row = conn.execute("SELECT * FROM periodic_reports WHERE edition_key=?", (key,)).fetchone()
            conn.commit()
        self.wake.set()
        return serialize(row)

    def create_preview(self, kind, scheduled_at, request_id, token):
        try:
            if kind not in KINDS or not isinstance(scheduled_at, str) or not isinstance(request_id, str):
                raise ValueError()
            request_id = uuid.UUID(request_id).hex
            scheduled = local(scheduled_at)
            at = now()
            slot = next((slot for slot in slots(scheduled.date(), calendar_data(), report_schedule.Schedule(self.settings()))
                         if slot[0] == kind and slot[1] == scheduled and slot[3] <= at), None)
            if not slot:
                raise ValueError()
        except (ValueError, TypeError, AttributeError, OverflowError):
            raise ValueError("report_preview_invalid") from None
        _, scheduled, start, end = slot
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            if token != conn.execute("SELECT database_token FROM periodic_report_meta WHERE singleton=1").fetchone()[0]:
                raise ValueError("report_database_changed")
            key = PREVIEW_PREFIX + request_id
            if conn.execute("SELECT 1 FROM periodic_report_deletions WHERE edition_key=?", (key,)).fetchone():
                raise ValueError("report_deleted")
            row = conn.execute("SELECT * FROM periodic_reports WHERE edition_key=?", (key,)).fetchone()
            if row:
                if row["kind"] != kind or row["scheduled_at"] != stamp(scheduled):
                    raise ValueError("report_preview_conflict")
            else:
                ts = stamp(at)
                config = report_model.configuration(self.settings(), resolve=False)
                conn.execute("""INSERT INTO periodic_reports
                    (id,edition_key,kind,title,period_start,period_end,scheduled_at,created_at,updated_at,config_json,model_label)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (uuid.uuid4().hex, key, kind,
                    edition_title(kind, scheduled, start, end), stamp(start), stamp(end), stamp(scheduled), ts, ts,
                    encoded(config), model_label(config)))
                row = conn.execute("SELECT * FROM periodic_reports WHERE edition_key=?", (key,)).fetchone()
            conn.commit()
        self.wake.set()
        return serialize(row)

    def delete_preview(self, report_id, token):
        return self.delete(report_id, token, preview_only=True)

    def delete(self, report_id, token, *, preview_only=False):
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            if token != conn.execute("SELECT database_token FROM periodic_report_meta WHERE singleton=1").fetchone()[0]:
                raise ValueError("report_database_changed")
            row = conn.execute("SELECT edition_key,status,period_start,period_end FROM periodic_reports WHERE id=?", (report_id,)).fetchone()
            if not row:
                raise LookupError("report_not_found")
            if preview_only and not row["edition_key"].startswith(PREVIEW_PREFIX):
                raise ValueError("report_preview_only")
            if row["status"] == "running":
                raise ValueError("report_preview_running" if preview_only else "report_delete_running")
            conn.execute("INSERT OR IGNORE INTO periodic_report_deletions VALUES (?,?)", (row["edition_key"], stamp(now())))
            conn.execute("DELETE FROM periodic_report_steps WHERE report_id=?", (report_id,))
            conn.execute("DELETE FROM periodic_reports WHERE id=?", (report_id,))
            conn.execute("""DELETE FROM periodic_report_snapshots WHERE period_start=? AND period_end=?
                AND NOT EXISTS (SELECT 1 FROM periodic_reports r WHERE
                    r.period_start=periodic_report_snapshots.period_start AND r.period_end=periodic_report_snapshots.period_end)""",
                (row["period_start"], row["period_end"]))
            conn.commit()

    def retry(self, report_id, token):
        with closing(self.connect()) as conn:
            if token != conn.execute("SELECT database_token FROM periodic_report_meta WHERE singleton=1").fetchone()[0]:
                raise ValueError("report_database_changed")
            row = conn.execute("SELECT status,config_json FROM periodic_reports WHERE id=?", (report_id,)).fetchone()
            if not row:
                raise LookupError("report_not_found")
            if row["status"] != "failed":
                return
        # A retry keeps the report's identity, even after global settings change.
        # Only legacy unbound jobs acquire their first configuration here.
        config = json.loads(row["config_json"]) if row["config_json"] else report_model.configuration(self.settings(), resolve=False)
        if "limits" not in config:
            config["limits"] = report_model.capacity(config)
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            if token != conn.execute("SELECT database_token FROM periodic_report_meta WHERE singleton=1").fetchone()[0]:
                raise ValueError("report_database_changed")
            row = conn.execute("SELECT status,config_json FROM periodic_reports WHERE id=?", (report_id,)).fetchone()
            if not row:
                raise LookupError("report_not_found")
            if row["status"] == "failed":
                conn.execute("UPDATE periodic_reports SET status='pending',attempts=0,next_attempt=0,error='',progress_json='{}',started_at=NULL,config_json=?,model_label=?,updated_at=? WHERE id=?",
                    (encoded(config), model_label(config), stamp(now()), report_id))
                if not row["config_json"]:
                    conn.execute("DELETE FROM periodic_report_steps WHERE report_id=?", (report_id,))
            conn.commit()
        self.wake.set()
