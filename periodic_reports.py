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
from report_generation import generate, parse_json, partition, validate_events, validate_report

ROOT = Path(__file__).resolve().parent
TZ = ZoneInfo("Asia/Shanghai")
KINDS = {"morning": "早报", "noon": "午报", "evening": "晚报", "weekly": "周报", "monthly": "月报"}
REPORT_KINDS = {**KINDS, "custom": "自定义"}
LEASE_SECONDS = 1200
PREVIEW_PREFIX = "trial:"
MANUAL_PREFIX = "manual:"
SCHEDULE_LABELS = {"morning": "交易日 08:30", "noon": "交易日 14:30", "evening": "每天 21:30",
                   "weekly": "每周一 08:30", "monthly": "每月 1 日 08:30"}
OBSERVATION_COLUMNS = "id,edition_key,kind,title,period_start,period_end,scheduled_at,status,error,attempts,next_attempt,updated_at,created_at,progress_json,started_at,(snapshot_json IS NOT NULL) AS has_snapshot"
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
    for name, declaration in (("progress_json", "TEXT NOT NULL DEFAULT '{}'"), ("started_at", "TEXT")):
        if name not in columns:
            conn.execute(f"ALTER TABLE periodic_reports ADD COLUMN {name} {declaration}")
    ts = stamp(at or now())
    conn.execute("INSERT OR IGNORE INTO periodic_report_meta VALUES (1, ?, ?, ?)", (uuid.uuid4().hex, ts, ts))
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


def slots(day: date, calendar: dict):
    midnight = datetime.combine(day, daytime(), TZ)
    evening = midnight + timedelta(hours=21, minutes=30)
    result = [("evening", evening, evening - timedelta(days=1), evening)]
    if trading_day(day, calendar):
        morning = midnight + timedelta(hours=8, minutes=30)
        noon = midnight + timedelta(hours=14, minutes=30)
        result += [("morning", morning, evening - timedelta(days=1), morning), ("noon", noon, morning, noon)]
    if day.weekday() == 0:
        result.append(("weekly", midnight + timedelta(hours=8, minutes=30), midnight - timedelta(days=7), midnight))
    if day.day == 1:
        previous = (day - timedelta(days=1)).replace(day=1)
        result.append(("monthly", midnight + timedelta(hours=8, minutes=30), datetime.combine(previous, daytime(), TZ), midnight))
    return sorted(result, key=lambda slot: (slot[1], slot[0]))


def edition_title(kind, scheduled, start, end):
    if kind == "monthly":
        return f"{(end - timedelta(seconds=1)):%Y年%m月}月报"
    if kind == "weekly":
        return f"{(end-timedelta(days=7)):%m月%d日}—{(end-timedelta(days=1)):%m月%d日}周报"
    return f"{scheduled:%m月%d日}{KINDS[kind]}"

def selected_period(kind, selection):
    """Resolve a selected day/month in Beijing time, independently of the browser."""
    try:
        if kind not in KINDS or not isinstance(selection, str):
            raise ValueError()
        selected = date.fromisoformat(selection + "-01" if kind == "monthly" else selection)
        if selection != selected.isoformat()[:7 if kind == "monthly" else 10]:
            raise ValueError()
        midnight = datetime.combine(selected, daytime(), TZ)
        if kind == "weekly":
            return midnight, midnight + timedelta(days=7)
        if kind == "monthly":
            return midnight, (midnight.replace(day=28)+timedelta(days=4)).replace(day=1)
        hour, hours = {"morning": (8, 11), "noon": (14, 6), "evening": (21, 24)}[kind]
        end = midnight + timedelta(hours=hour, minutes=30)
        return end-timedelta(hours=hours), end
    except (ValueError, TypeError, KeyError, OverflowError):
        raise ValueError("report_window_invalid") from None


def preview_periods(at=None):
    """Offer the latest complete edition of each kind, without moving the schedule."""
    at = local(at or now())
    calendar = calendar_data()
    latest = {}
    for offset in range(63):
        for kind, scheduled, start, end in slots(at.date()-timedelta(days=offset), calendar):
            if end <= at and kind not in latest:
                latest[kind] = {"kind": kind, "kind_label": KINDS[kind],
                                "title": edition_title(kind, scheduled, start, end),
                                "scheduled_at": stamp(scheduled), "period_start": stamp(start), "period_end": stamp(end)}
        if len(latest) == len(KINDS):
            break
    return [latest[kind] for kind in KINDS if kind in latest]


def enqueue_due(conn, at=None, calendar=None, *, intraday_since=None):
    at = local(at or now())
    # Morning/noon editions are useful when their slot arrives while this
    # service is running. A restart only catches up durable evening/week/month editions.
    intraday_since = local(intraday_since or at)
    calendar = calendar if calendar is not None else calendar_data()
    conn.execute("BEGIN IMMEDIATE")
    try:
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
        count = 0
        day = cursor.date()
        while day <= until.date():
            for kind, scheduled, start, end in slots(day, calendar):
                if not cursor < scheduled <= until or end <= active:
                    continue
                if kind in {"morning", "noon"} and scheduled < intraday_since:
                    continue
                if conn.execute("SELECT 1 FROM periodic_report_deletions WHERE edition_key=?", (f"{kind}:{scheduled.date()}",)).fetchone():
                    continue
                start = max(start, active)
                ts = stamp(at)
                count += conn.execute("""INSERT OR IGNORE INTO periodic_reports
                    (id,edition_key,kind,title,period_start,period_end,scheduled_at,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""", (uuid.uuid4().hex, f"{kind}:{scheduled.date()}", kind,
                    edition_title(kind, scheduled, start, end), stamp(start), stamp(end), stamp(scheduled), ts, ts)).rowcount
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


def serialize(row, detail=False):
    result = json.loads(row["result_json"]) if row["result_json"] else None
    value = {key:row[key] for key in ("id", "kind", "title", "period_start", "period_end", "scheduled_at", "status", "error", "updated_at")}
    value["kind_label"] = REPORT_KINDS[row["kind"]]
    value["is_preview"] = row["edition_key"].startswith(PREVIEW_PREFIX)
    value["is_manual"] = row["edition_key"].startswith(MANUAL_PREFIX)
    value["progress"] = progress_record(row)
    value["started_at"] = row["started_at"]
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
    result["phase"] = ("editing" if row["has_snapshot"] else "indexing") if row["status"] == "running" else None
    result["next_retry_at"] = stamp(datetime.fromtimestamp(row["next_attempt"], TZ)) if row["status"] == "pending" and row["error"] and row["next_attempt"] else None
    return result


class Service:
    def __init__(self, connect, settings, refresh_index, runner=report_model.run, *, index_lock=None):
        self.connect, self.settings, self.refresh_index, self.runner = connect, settings, refresh_index, runner
        self.index_lock = index_lock if index_lock is not None else threading.RLock()
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.schedule_wake = threading.Event()
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self.thread = None
        self.scheduler_thread = None
        self.started_at = None
        self.last_tick = None
        self.last_schedule_success = None
        self.last_schedule_error = ""
        self.last_database_token = None

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive() and self.scheduler_thread and self.scheduler_thread.is_alive():
                self.wake.set()
                self.schedule_wake.set()
                return
            if not self.thread or not self.thread.is_alive():
                self.started_at = now()
                self.last_tick = None
                self.last_schedule_success = None
                self.last_schedule_error = ""
                self.last_database_token = None
                self.stopped.clear()
                self.ready.clear()
                self.thread = threading.Thread(target=self.loop, name="periodic-report-worker", daemon=True)
                self.thread.start()
            if not self.scheduler_thread or not self.scheduler_thread.is_alive():
                self.scheduler_thread = threading.Thread(target=self.schedule_loop, name="periodic-report-scheduler", daemon=True)
                self.scheduler_thread.start()

    def stop(self):
        self.stopped.set()
        self.wake.set()
        self.schedule_wake.set()
        self.ready.set()
        for thread in (self.thread, self.scheduler_thread):
            if thread and thread is not threading.current_thread():
                thread.join(timeout=1)

    def schedule_once(self, at=None):
        at = local(at or now())
        if self.started_at is None or (self.last_tick and at - self.last_tick > timedelta(seconds=90)):
            # A machine waking after sleep also misses intraday slots. Model
            # latency cannot trigger this branch: scheduling has its own thread.
            self.started_at = at
        self.last_tick = at
        try:
            calendar = calendar_data()
        except (OSError, ValueError, TypeError):
            raise RuntimeError("report_calendar_failed") from None
        with closing(self.connect()) as conn:
            enqueue_due(conn, at, calendar, intraday_since=self.started_at)
            meta = conn.execute("SELECT scheduled_through,database_token FROM periodic_report_meta WHERE singleton=1").fetchone()
            cursor = local(meta["scheduled_through"])
        self.last_schedule_success = at
        self.last_schedule_error = ""
        self.last_database_token = meta["database_token"]
        self.ready.set()
        self.wake.set()
        if cursor < at:
            return 0  # Continue a >90-day durable catch-up without delaying live slots.
        upcoming = [scheduled for delta in range(2)
                    for _, scheduled, _, _ in slots(at.date() + timedelta(days=delta), calendar) if scheduled > at]
        # Wake at the next exact slot, with a cheap watchdog for clock changes,
        # resume from sleep, calendar edits, or a changed database connection.
        return min(30, max(0, (min(upcoming) - at).total_seconds()))

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

    def run_one(self):
        job = self.claim()
        if not job:
            return False
        try:
            if job["snapshot_json"]:
                snapshot = json.loads(job["snapshot_json"])
                config = json.loads(job["config_json"]) if job["config_json"] else report_model.configuration(self.settings(), resolve=False)
            else:
                # No model request or material capture until a successful fresh index.
                # A manual full refresh must not clear the index between our
                # refresh and capture. Release the guard before any model work.
                self.update_progress(job, "indexing")
                with self.index_lock:
                    try:
                        self.refresh_index()
                    except Exception as exc:
                        logging.getLogger(__name__).warning("Report index refresh failed (%s)", type(exc).__name__)
                        raise RuntimeError(index_error(exc)) from None
                    self.update_progress(job, "capturing")
                    with closing(self.connect()) as conn:
                        conn.execute("BEGIN")
                        snapshot = capture(conn, job)
                        conn.commit()
                config = report_model.configuration(self.settings(), resolve=False) if snapshot["sources"] else {}
                with closing(self.connect()) as conn, conn:
                    changed = conn.execute("UPDATE periodic_reports SET snapshot_json=?,config_json=?,lease_until=? WHERE id=? AND lease=? AND status='running'",
                        (encoded(snapshot), encoded(config), time.time()+LEASE_SECONDS, job["id"], job["lease"])).rowcount
                    if changed != 1:
                        raise RuntimeError("report_interrupted")
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
                conn.execute("""UPDATE periodic_reports SET status='done',result_json=?,lease=NULL,lease_until=NULL,error='',progress_json='{"stage":"done"}',updated_at=?
                    WHERE id=? AND lease=? AND status='running'""", (encoded(result), stamp(now()), job["id"], job["lease"]))
        except Exception as exc:
            error = str(exc)
            if error not in INDEX_ERRORS | {"report_model_unconfigured", "report_model_capacity_unknown", "report_model_timeout", "report_invalid_result", "report_input_too_large", "report_interrupted", "report_tools_not_allowed"}:
                error = "report_model_failed"
            with closing(self.connect()) as conn, conn:
                conn.execute("""UPDATE periodic_reports SET status=?,error=?,lease=NULL,lease_until=NULL,next_attempt=?,updated_at=?
                    WHERE id=? AND lease=? AND status='running'""", ("failed" if job["attempts"] >= 3 else "pending", error,
                    time.time() + (60 if job["attempts"] == 1 else 300), stamp(now()), job["id"], job["lease"]))
        return True

    def listing(self, kind, offset=0, limit=30):
        kinds = ("morning", "noon", "evening", "custom") if kind == "daily" else (kind,)
        if kind not in {"daily", "weekly", "monthly"}:
            raise ValueError("invalid_report_kind")
        clause = "kind IN (" + ",".join("?" for _ in kinds) + ")"
        with closing(self.connect()) as conn:
            meta = conn.execute("SELECT * FROM periodic_report_meta WHERE singleton=1").fetchone()
            total = conn.execute(f"SELECT COUNT(*) FROM periodic_reports WHERE {clause}", kinds).fetchone()[0]
            rows = conn.execute(f"SELECT * FROM periodic_reports WHERE {clause} ORDER BY CASE WHEN edition_key LIKE 'trial:%' OR edition_key LIKE 'manual:%' THEN created_at ELSE scheduled_at END DESC,id DESC LIMIT ? OFFSET ?", (*kinds, limit, offset)).fetchall()
        at = now()
        calendar = calendar_data()
        upcoming = []
        for delta in range(35):
            upcoming += [(scheduled, slotkind) for slotkind, scheduled, start, end in slots(at.date()+timedelta(days=delta), calendar)
                         if slotkind in kinds and scheduled > at and end > local(meta["activated_at"])]
        upcoming.sort()
        return {"items": [serialize(row) for row in rows], "total": total, "has_more": offset+len(rows)<total,
                "database_token": meta["database_token"], "activated_at": meta["activated_at"],
                "next_edition_at": stamp(upcoming[0][0]) if upcoming else None,
                "calendar_warning": str(at.year) not in calendar}

    def observation(self):
        at = now()
        with closing(self.connect()) as conn:
            meta = conn.execute("SELECT * FROM periodic_report_meta WHERE singleton=1").fetchone()
            counts = dict(conn.execute("SELECT status,COUNT(*) FROM periodic_reports GROUP BY status"))
            retrying = conn.execute("SELECT COUNT(*) FROM periodic_reports WHERE status='pending' AND error<>''").fetchone()[0]
            recent = conn.execute(f"SELECT {OBSERVATION_COLUMNS} FROM periodic_reports ORDER BY updated_at DESC,id DESC LIMIT 20").fetchall()
            running = conn.execute(f"SELECT {OBSERVATION_COLUMNS} FROM periodic_reports WHERE status='running' ORDER BY updated_at DESC LIMIT 1").fetchone()
            latest = {kind: execution_record(conn.execute(f"SELECT {OBSERVATION_COLUMNS} FROM periodic_reports WHERE kind=? AND edition_key=kind || ':' || substr(scheduled_at,1,10) ORDER BY scheduled_at DESC LIMIT 1", (kind,)).fetchone()) for kind in KINDS}
        calendar_error = False
        try:
            calendar = calendar_data()
            future = [slot for day in range(35) for slot in slots(at.date()+timedelta(days=day), calendar)]
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            calendar, calendar_error = {}, True
            future = [slot for day in range(35) for slot in slots(at.date()+timedelta(days=day), calendar)]
        schedules = []
        for kind in KINDS:
            times = [scheduled for slot_kind, scheduled, _, end in future if slot_kind == kind and scheduled > at and end > local(meta["activated_at"])]
            schedules.append({"kind": kind, "kind_label": KINDS[kind], "rule": SCHEDULE_LABELS[kind],
                              "next_run_at": stamp(min(times)) if times else None, "latest": latest[kind]})
        checked = self.last_schedule_success if self.last_database_token == meta["database_token"] else None
        alive = bool(self.scheduler_thread and self.scheduler_thread.is_alive())
        error = "report_calendar_failed" if calendar_error else self.last_schedule_error
        status = ("stopped" if self.stopped.is_set() else "not_running" if not alive else "error" if error else
                  "starting" if checked is None else "delayed" if at-checked > timedelta(seconds=90) else "running")
        return {"observed_at": stamp(at), "database_token": meta["database_token"], "activated_at": meta["activated_at"],
                "timezone": "Asia/Shanghai", "scheduler": {"status": status, "error": error,
                    "last_checked_at": stamp(checked) if checked else None, "scheduled_through": meta["scheduled_through"]},
                "worker_running": bool(self.thread and self.thread.is_alive() and not self.stopped.is_set()),
                "queue": {key: counts.get(key, 0) for key in ("running", "pending", "failed", "skipped")},
                "retrying": retrying, "current": execution_record(running), "schedules": schedules,
                "recent": [execution_record(row) for row in recent], "calendar_warning": calendar_error or str(at.year) not in calendar}

    def preview_options(self):
        with closing(self.connect()) as conn:
            token = conn.execute("SELECT database_token FROM periodic_report_meta WHERE singleton=1").fetchone()[0]
        return {"database_token": token, "periods": preview_periods()}

    def manual_options(self):
        at = now()
        try:
            periods = {row["kind"]: row for row in preview_periods(at)}
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            periods = {}
        # Manual ranges are user-selected, independent of trading-day coverage.
        # Normally prefer the most recent complete scheduled window; if a year's
        # calendar is unavailable, still offer all five dated report types.
        midnight = datetime.combine(at.date(), daytime(), TZ)
        for kind in KINDS:
            if kind in periods:
                continue
            if kind in {"morning", "noon", "evening"}:
                hour, hours = {"morning": (8, 11), "noon": (14, 6), "evening": (21, 24)}[kind]
                end = midnight + timedelta(hours=hour, minutes=30)
                if end > at:
                    end -= timedelta(days=1)
                start = end - timedelta(hours=hours)
            elif kind == "weekly":
                end = midnight - timedelta(days=at.weekday())
                start = end - timedelta(days=7)
            else:
                end = midnight.replace(day=1)
                start = (end-timedelta(days=1)).replace(day=1)
            periods[kind] = {"kind": kind, "kind_label": KINDS[kind], "period_start": stamp(start), "period_end": stamp(end)}
        with closing(self.connect()) as conn:
            token = conn.execute("SELECT database_token FROM periodic_report_meta WHERE singleton=1").fetchone()[0]
        for kind, period in periods.items():
            period["selection"] = period["period_start"][:7] if kind == "monthly" else period["period_start"][:10] if kind == "weekly" else period["period_end"][:10]
        periods["custom"] = {"kind": "custom", "kind_label": REPORT_KINDS["custom"],
                             "period_start": stamp(at-timedelta(days=1)), "period_end": stamp(at)}
        return {"database_token": token, "periods": [periods[kind] for kind in REPORT_KINDS], "now": stamp(at)}

    def create_selected(self, kind, selection, request_id, token):
        start, end = selected_period(kind, selection)
        return self.create_manual(kind, stamp(start), stamp(end), request_id, token)

    def create_manual(self, kind, period_start, period_end, request_id, token):
        try:
            if not isinstance(kind, str) or kind not in REPORT_KINDS or not all(isinstance(value, str) for value in (period_start, period_end, request_id)):
                raise ValueError()
            request_id = uuid.UUID(request_id).hex
            start, end, at = local(period_start), local(period_end), now()
            if not start < end <= at:
                raise ValueError()
        except (ValueError, TypeError, AttributeError, OverflowError):
            raise ValueError("report_window_invalid") from None
        if kind == "monthly" and start.day == end.day == 1 and start.time() == end.time() == daytime() and (end-timedelta(days=1)).replace(day=1) == start:
            title = f"{start:%Y年%m月}月报"
        else:
            last = end-timedelta(seconds=1)
            title = (f"{last:%m月%d日}" if start.date() == last.date() or kind in {"morning", "noon", "evening"} and end-start <= timedelta(days=1)
                     else f"{start:%m月%d日}—{last:%m月%d日}") + ("自定义报告" if kind == "custom" else KINDS[kind])
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            if token != conn.execute("SELECT database_token FROM periodic_report_meta WHERE singleton=1").fetchone()[0]:
                raise ValueError("report_database_changed")
            key = MANUAL_PREFIX + request_id
            if conn.execute("SELECT 1 FROM periodic_report_deletions WHERE edition_key=?", (key,)).fetchone():
                raise ValueError("report_deleted")
            row = conn.execute("SELECT * FROM periodic_reports WHERE edition_key=?", (key,)).fetchone()
            if row:
                if (row["kind"], row["period_start"], row["period_end"]) != (kind, stamp(start), stamp(end)):
                    raise ValueError("report_manual_conflict")
            else:
                ts = stamp(at)
                conn.execute("""INSERT INTO periodic_reports
                    (id,edition_key,kind,title,period_start,period_end,scheduled_at,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""", (uuid.uuid4().hex, key, kind, title, stamp(start), stamp(end), stamp(end), ts, ts))
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
            slot = next((slot for slot in slots(scheduled.date(), calendar_data())
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
                conn.execute("""INSERT INTO periodic_reports
                    (id,edition_key,kind,title,period_start,period_end,scheduled_at,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""", (uuid.uuid4().hex, key, kind,
                    edition_title(kind, scheduled, start, end), stamp(start), stamp(end), stamp(scheduled), ts, ts))
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
            row = conn.execute("SELECT edition_key,status FROM periodic_reports WHERE id=?", (report_id,)).fetchone()
            if not row:
                raise LookupError("report_not_found")
            if preview_only and not row["edition_key"].startswith(PREVIEW_PREFIX):
                raise ValueError("report_preview_only")
            if row["status"] == "running":
                raise ValueError("report_preview_running" if preview_only else "report_delete_running")
            conn.execute("INSERT OR IGNORE INTO periodic_report_deletions VALUES (?,?)", (row["edition_key"], stamp(now())))
            conn.execute("DELETE FROM periodic_report_steps WHERE report_id=?", (report_id,))
            conn.execute("DELETE FROM periodic_reports WHERE id=?", (report_id,))
            conn.commit()

    def retry(self, report_id, token):
        with closing(self.connect()) as conn:
            if token != conn.execute("SELECT database_token FROM periodic_report_meta WHERE singleton=1").fetchone()[0]:
                raise ValueError("report_database_changed")
            row = conn.execute("SELECT status FROM periodic_reports WHERE id=?", (report_id,)).fetchone()
            if not row:
                raise LookupError("report_not_found")
            if row["status"] != "failed":
                return
        # Resolve metadata outside the SQLite write transaction.
        config = report_model.configuration(self.settings())
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            if token != conn.execute("SELECT database_token FROM periodic_report_meta WHERE singleton=1").fetchone()[0]:
                raise ValueError("report_database_changed")
            row = conn.execute("SELECT status,config_json FROM periodic_reports WHERE id=?", (report_id,)).fetchone()
            if not row:
                raise LookupError("report_not_found")
            if row["status"] == "failed":
                conn.execute("UPDATE periodic_reports SET status='pending',attempts=0,next_attempt=0,error='',progress_json='{}',started_at=NULL,config_json=?,updated_at=? WHERE id=?", (encoded(config), stamp(now()), report_id))
                # Keep successful work and the repairable draft when the model
                # has not changed. Changed models must not inherit old answers.
                if not row["config_json"] or json.loads(row["config_json"]) != config:
                    conn.execute("DELETE FROM periodic_report_steps WHERE report_id=?", (report_id,))
            conn.commit()
        self.wake.set()
