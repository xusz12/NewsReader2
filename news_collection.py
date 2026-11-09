"""Durable native collection: source acquisition and translation are independent.

Only publication writes items. No Markdown, daily sidecar or agent scheduling.
"""
from contextlib import closing, contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
import hashlib
import json
import logging
from pathlib import Path
import re
import threading
import time
import uuid

import collection_sources as adapters
import collection_translation
from feed_sources import is_bloomberg_video_url
import news_classification
import periodic_reports
from report_schedule import TZ
from source_deletion import item_fingerprint
import topic_tracking
import news_versions
from news_identity import canonical_url, identity_key

ROOT = Path(__file__).resolve().parent
from collection_schedule import DEFAULT_SCHEDULE, normalize_schedule
LEASE_SECONDS = 180
SCHEMA = """
CREATE TABLE IF NOT EXISTS collection_meta (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), database_token TEXT NOT NULL,
 activated_at TEXT NOT NULL, checked_at TEXT NOT NULL, schedule_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS collection_runs (
 id TEXT PRIMARY KEY, slot TEXT UNIQUE, trigger TEXT NOT NULL, status TEXT NOT NULL,
 created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, lease TEXT, lease_until REAL DEFAULT 0,
 publication_status TEXT NOT NULL DEFAULT 'pending', publication_error TEXT NOT NULL DEFAULT '', published_at TEXT);
CREATE TABLE IF NOT EXISTS collection_run_sources (
 run_id TEXT NOT NULL, source_id TEXT NOT NULL, config_json TEXT NOT NULL, status TEXT NOT NULL,
 count INTEGER DEFAULT 0, added INTEGER DEFAULT 0, error TEXT DEFAULT '', started_at TEXT, finished_at TEXT,
 duplicate_count INTEGER, filtered_count INTEGER, ingested_count INTEGER NOT NULL DEFAULT 0, ingested_at TEXT,
 PRIMARY KEY(run_id,source_id));
CREATE TABLE IF NOT EXISTS collection_source_cooldowns (source_id TEXT PRIMARY KEY, until REAL NOT NULL);
CREATE TABLE IF NOT EXISTS news_identities (
 identity_key TEXT PRIMARY KEY, item_id TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_news_identity_item ON news_identities(item_id);
CREATE TABLE IF NOT EXISTS collection_items (
 item_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, source_id TEXT NOT NULL, raw_json TEXT NOT NULL,
 translation_json TEXT, status TEXT NOT NULL, error TEXT DEFAULT '', attempts INTEGER DEFAULT 0,
 collected_at TEXT NOT NULL, published_at TEXT NOT NULL, ingested_at TEXT,
 lease TEXT, lease_until REAL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_collection_items_status ON collection_items(status,collected_at);
CREATE INDEX IF NOT EXISTS idx_collection_items_run ON collection_items(run_id,status);
CREATE TRIGGER IF NOT EXISTS items_record_ingestion AFTER INSERT ON items BEGIN
 INSERT OR IGNORE INTO topic_ingestions(item_id) VALUES (NEW.id);
END;
CREATE TABLE IF NOT EXISTS news_read_previews (
 id TEXT PRIMARY KEY, item_ids TEXT NOT NULL, created_at REAL NOT NULL);
"""


def now():
    return datetime.now(TZ)


def stamp(value=None):
    return (value or now()).astimezone(TZ).isoformat(timespec="microseconds")


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def published_time(value):
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (ValueError, TypeError, OverflowError):
            return ""  # Unknown is not the collection date.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ)
    return parsed.astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S")


def initialize(conn):
    conn.executescript(SCHEMA)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(collection_runs)")}
    for name, definition in (("publication_status", "TEXT NOT NULL DEFAULT 'pending'"),
                             ("publication_error", "TEXT NOT NULL DEFAULT ''"), ("published_at", "TEXT")):
        if name not in columns:
            conn.execute(f"ALTER TABLE collection_runs ADD COLUMN {name} {definition}")
    if "publication_status" not in columns:
        conn.execute("""UPDATE collection_runs SET publication_status='done', published_at=COALESCE(
            (SELECT MAX(ingested_at) FROM collection_items WHERE run_id=collection_runs.id), finished_at)
            WHERE status IN ('done','partial','failed') AND NOT EXISTS (
              SELECT 1 FROM collection_items WHERE run_id=collection_runs.id AND status NOT IN ('done','deleted','duplicate'))""")
    source_columns = {row[1] for row in conn.execute("PRAGMA table_info(collection_run_sources)")}
    for name, definition in (("duplicate_count", "INTEGER"), ("filtered_count", "INTEGER"),
                             ("ingested_count", "INTEGER NOT NULL DEFAULT 0"), ("ingested_at", "TEXT")):
        if name not in source_columns:
            conn.execute(f"ALTER TABLE collection_run_sources ADD COLUMN {name} {definition}")
    if "ingested_count" not in source_columns:
        conn.execute("""UPDATE collection_run_sources SET (ingested_count,ingested_at)=(
            SELECT COUNT(*),MAX(i.ingested_at) FROM collection_items i
            WHERE i.run_id=collection_run_sources.run_id AND i.source_id=collection_run_sources.source_id
              AND i.ingested_at IS NOT NULL)""")
    # Old count - added also included filtered and invalid items. Only zero is
    # recoverable with certainty; never invent historical duplicate counts.
    if "duplicate_count" not in source_columns:
        conn.execute("""UPDATE collection_run_sources SET duplicate_count=0,filtered_count=0
            WHERE status IN ('done','failed') AND count=added""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_collection_sources_started ON collection_run_sources(started_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_collection_sources_ingested ON collection_run_sources(ingested_at)")
    topic_tracking.seed_ingestions(conn)
    first = conn.execute("SELECT 1 FROM collection_meta").fetchone() is None
    if first:
        # Preserve all existing IDs and state; one canonical identity may have
        # multiple historical date-based IDs. Do not merge or delete that history.
        for row in conn.execute("SELECT id,url FROM items ORDER BY created_at,id").fetchall():
            try:
                url = canonical_url(row[1])
            except (ValueError, TypeError):
                continue
            conn.execute("INSERT OR IGNORE INTO news_identities VALUES (?,?,0)", (hashlib.sha256(url.encode()).hexdigest(),row[0]))
        ts = stamp()
        conn.execute("INSERT INTO collection_meta VALUES (1,?,?,?,?)", (uuid.uuid4().hex,ts,ts,""))
    news_versions.retire_duplicate_jobs(conn)
    conn.commit()


def batch_boundary(conn):
    return conn.execute("SELECT COALESCE(MAX(seq),0) FROM topic_ingestions").fetchone()[0]


def batch_clause(value, *, newer=False):
    try:
        number = int(value)
        if not 0 <= number <= 9223372036854775807 or str(number) != str(value):
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("invalid_news_batch") from None
    return f" AND items.id IN (SELECT item_id FROM topic_ingestions WHERE seq {'>' if newer else '<='} ?)", [number]


def acquire_source(source):
    def attempt(command):
        return adapters.execute_command_once(section=source["section"], command=command,
            timeout_seconds=source.get("command_timeout_seconds") or 300,
            min_valid_items=source.get("min_valid_items", 1), treat_empty_as_failure=source.get("treat_empty_as_failure", False),
            translation_policy=source["translation_policy"], output_timezone="Asia/Shanghai",
            source_type=source["source_type"], source_handle=source.get("source_handle", ""), source_name=source["source_name"])
    result = attempt(source["command"])
    if not result["ok"] and source.get("retry_once"):
        delay = adapters.retry_delay(result)
        if delay is not None:
            _, ready = adapters.wait_for_retry(delay, result.get("retry_at"))
            if ready:
                result = attempt(source["command"])
    cooldown = adapters.parse_retry_at(result.get("retry_at"))
    if not result["ok"] and source.get("fallback_command") and (cooldown is None or cooldown <= time.time()):
        result = attempt(source["fallback_command"])
    return result


def source_error(result):
    # Never persist raw CLI output, browser cookies, headers or stack traces.
    code = str(result.get("error_code") or "COLLECTION_FAILED")
    return code if re.fullmatch(r"[A-Z0-9_]{1,80}", code) else "COLLECTION_FAILED"


class Service:
    def __init__(self, connect, settings, collector=acquire_source, translator=collection_translation.translate, sources=None):
        self.connect, self.settings = connect, settings
        self.collector, self.translator = collector, translator
        self.sources = sources if sources is not None else adapters.load_config(ROOT / "collection/sources.json")
        self.stopped = threading.Event()
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.threads = []
        self.schedule_error = ""
        self.last_check = None
        self.calendar_wait_since = None

    def start(self):
        with self.lock:
            if any(t.is_alive() for t in self.threads):
                return
            self.stopped.clear()
            self.threads = [threading.Thread(target=self.loop, args=(action,), daemon=True, name=name)
                            for name, action in (("news-collection-schedule", self.schedule_once),
                                                 ("news-collection", self.collect_once))]
            self.threads.append(threading.Thread(target=self.translation_loop, daemon=True,
                                                name="news-collection-translation"))
            for thread in self.threads:
                thread.start()

    def stop(self):
        self.stopped.set()
        self.wake.set()

    def loop(self, action):
        while not self.stopped.is_set():
            try:
                worked = action()
            except Exception:
                logging.getLogger(__name__).exception("Collection worker failed")
                worked = False
            if not worked:
                self.wake.wait(5)
                self.wake.clear()

    def translation_loop(self):
        # Only in-flight calls occupy pool slots; a settings change affects the
        # next claim, while already running requests are allowed to finish.
        active = set()
        with ThreadPoolExecutor(max_workers=32, thread_name_prefix="news-translation") as pool:
            while not self.stopped.is_set():
                try:
                    completed = {future for future in active if future.done()}
                    active -= completed
                    for future in completed:
                        future.result()
                    self.publish_once()
                    limit = normalize_schedule(self.settings().get("collection", {}))["translation_concurrency"]
                    with closing(self.connect()) as conn:
                        available = conn.execute("""SELECT COUNT(*) FROM collection_items
                            WHERE status='pending' OR (status='running' AND lease_until<=?)""", (time.time(),)).fetchone()[0]
                    for _ in range(min(available, max(0, limit-len(active)))):
                        future = pool.submit(self.translate_once)
                        future.add_done_callback(lambda _: self.wake.set())
                        active.add(future)
                except Exception:
                    logging.getLogger(__name__).exception("Collection translation coordinator failed")
                self.wake.wait(1)
                self.wake.clear()

    @contextmanager
    def heartbeat(self, table, key, ident, lease):
        finished = threading.Event()
        def renew():
            while not finished.wait(30):
                try:
                    with closing(self.connect()) as conn, conn:
                        conn.execute(f"UPDATE {table} SET lease_until=? WHERE {key}=? AND lease=? AND status='running'",
                                     (time.time()+LEASE_SECONDS,ident,lease))
                except Exception:
                    logging.getLogger(__name__).warning("Collection lease renewal failed")
        thread = threading.Thread(target=renew, daemon=True)
        thread.start()
        try:
            yield
        finally:
            finished.set()
            thread.join(timeout=1)

    def enqueue(self, conn, *, trigger="manual", slot=None):
        existing = conn.execute("SELECT id FROM collection_runs WHERE status IN ('pending','running') ORDER BY created_at LIMIT 1").fetchone()
        if existing:
            return existing[0]
        ident = uuid.uuid4().hex
        if not conn.execute("INSERT OR IGNORE INTO collection_runs(id,slot,trigger,status,created_at) VALUES (?,?,?,'pending',?)",
                            (ident,slot,trigger,stamp())).rowcount:
            return conn.execute("SELECT id FROM collection_runs WHERE slot=?", (slot,)).fetchone()[0]
        conn.executemany("INSERT INTO collection_run_sources(run_id,source_id,config_json,status,duplicate_count,filtered_count) VALUES (?,?,?,'pending',0,0)",
                         [(ident, s["section"], encoded(s)) for s in self.sources])
        return ident

    def request(self):
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            ident = self.enqueue(conn)
        self.wake.set()
        return ident

    def times(self, day, config, calendar=None):
        values = set(config["daily"])
        try:
            trading = periodic_reports.trading_day(day, periodic_reports.calendar_data() if calendar is None else calendar)
        except (OSError, ValueError, KeyError, TypeError):
            trading = None
        if trading is None:
            self.schedule_error = "collection_calendar_unavailable"
        if trading:
            values.update(config["trading"])
        return [datetime.fromisoformat(f"{day.isoformat()}T{value}").replace(tzinfo=TZ) for value in sorted(values)]

    def schedule_once(self, at=None):
        at = (at or now()).astimezone(TZ)
        config = normalize_schedule(self.settings().get("collection", {}))
        # Changing only translation throughput must not reset schedule catch-up.
        schedule = {key: config[key] for key in ("enabled", "daily", "trading")}
        self.schedule_error = ""
        try:
            calendar = periodic_reports.calendar_data()
        except (OSError, ValueError, KeyError, TypeError):
            calendar = {}
        missing = str(at.year) not in calendar
        if missing:
            self.schedule_error = "collection_calendar_unavailable"
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            meta = conn.execute("SELECT * FROM collection_meta").fetchone()
            previous = datetime.fromisoformat(meta["checked_at"])
            previous_config = json.loads(meta["schedule_json"] or "{}")
            changed = any(previous_config.get(key) != value for key, value in schedule.items())
            if changed or not config["enabled"]:
                self.calendar_wait_since = None
            if config["enabled"] and not changed and at > previous:
                if missing and self.calendar_wait_since is None:
                    self.calendar_wait_since = previous
                if not missing and self.calendar_wait_since is not None:
                    previous = min(previous, self.calendar_wait_since)
                due = [slot for day in (at.date()-timedelta(days=1), at.date()) for slot in self.times(day, config, calendar)
                       if previous < slot <= at]
                if due:
                    self.enqueue(conn, trigger="catchup" if (at-previous).total_seconds()>90 else "scheduled", slot=stamp(max(due)))
            conn.execute("UPDATE collection_meta SET checked_at=?,schedule_json=? WHERE singleton=1",
                         (stamp(max(at,previous)),encoded(schedule)))
        if not missing:
            self.calendar_wait_since = None
        self.last_check = stamp(at)
        return False

    def stage(self, conn, run_id, source, raw):
        """Return the observed disposition without conflating filtering and deduplication."""
        if not str(raw.get("title") or "").strip() or is_bloomberg_video_url(raw.get("url")):
            return "filtered"
        ident = identity_key(raw["url"])
        if conn.execute("SELECT 1 FROM news_identities WHERE identity_key=?", (ident,)).fetchone():
            return "duplicate"
        published = published_time(raw.get("time"))
        # Honour legacy deletion fingerprints without changing any historical ID.
        legacy = hashlib.sha256(f"{published[:10]}|{raw['url']}".encode()).hexdigest()
        deleted = bool(conn.execute("SELECT 1 FROM deleted_news_items WHERE fingerprint IN (?,?)",
                                    (item_fingerprint(legacy), item_fingerprint(ident))).fetchone())
        conn.execute("INSERT INTO news_identities VALUES (?,?,?)", (ident,ident,int(deleted)))
        if deleted:
            return "duplicate"
        raw = {**raw, "source": source["display_name"], "source_type": source["source_type"],
               "source_name": source["source_name"], "translation_policy": source["translation_policy"]}
        conn.execute("""INSERT INTO collection_items(item_id,run_id,source_id,raw_json,status,collected_at,published_at)
                        VALUES (?,?,?,?,'pending',?,?)""", (ident,run_id,source["section"],encoded(raw),stamp(),published))
        return "added"

    def collect_once(self):
        lease = uuid.uuid4().hex
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM collection_runs WHERE status='running' AND lease_until>?", (time.time(),)).fetchone():
                return False
            row = conn.execute("SELECT * FROM collection_runs WHERE status='pending' OR (status='running' AND lease_until<=?) ORDER BY created_at LIMIT 1", (time.time(),)).fetchone()
            if not row:
                return False
            ident = row["id"]
            conn.execute("UPDATE collection_runs SET status='running',started_at=COALESCE(started_at,?),lease=?,lease_until=? WHERE id=?",
                         (stamp(),lease,time.time()+LEASE_SECONDS,ident))
            sources = conn.execute("SELECT * FROM collection_run_sources WHERE run_id=? AND status IN ('pending','running') ORDER BY rowid", (ident,)).fetchall()
        with self.heartbeat("collection_runs", "id", ident, lease):
            for source_row in sources:
                if self.stopped.is_set():
                    break
                source = json.loads(source_row["config_json"])
                with closing(self.connect()) as conn, conn:
                    cooldown = conn.execute("SELECT until FROM collection_source_cooldowns WHERE source_id=?", (source["section"],)).fetchone()
                    conn.execute("UPDATE collection_run_sources SET status='running',started_at=? WHERE run_id=? AND source_id=?", (stamp(),ident,source["section"]))
                try:
                    result = ({"ok":False,"error_code":"RETRY_AFTER","retry_at":datetime.fromtimestamp(cooldown[0],TZ).isoformat(),"items":[]}
                              if cooldown and cooldown[0]>time.time() else self.collector(source))
                except Exception:
                    result = {"ok":False,"error_code":"COLLECTION_FAILED","items":[]}
                with closing(self.connect()) as conn, conn:
                    conn.execute("BEGIN IMMEDIATE")
                    if not conn.execute("SELECT 1 FROM collection_runs WHERE id=? AND lease=? AND status='running'", (ident,lease)).fetchone():
                        return True
                    added = duplicates = filtered = 0
                    if result["ok"]:
                        for raw in result.get("items", []):
                            try:
                                disposition = self.stage(conn,ident,source,raw)
                                added += disposition == "added"
                                duplicates += disposition == "duplicate"
                                filtered += disposition == "filtered"
                            except (ValueError, KeyError, TypeError):
                                result = {**result, "ok":False, "error_code":"INVALID_ITEM"}
                    retry_at = adapters.parse_retry_at(result.get("retry_at"))
                    if retry_at:
                        conn.execute("INSERT OR REPLACE INTO collection_source_cooldowns VALUES (?,?)", (source["section"],retry_at))
                    conn.execute("UPDATE collection_run_sources SET status=?,count=?,added=?,duplicate_count=?,filtered_count=?,error=?,finished_at=? WHERE run_id=? AND source_id=?",
                                 ("done" if result["ok"] else "failed",len(result.get("items",[])),added,duplicates,filtered,
                                  "" if result["ok"] else source_error(result),stamp(),ident,source["section"]))
                self.wake.set()
            with closing(self.connect()) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                states = [r[0] for r in conn.execute("SELECT status FROM collection_run_sources WHERE run_id=?", (ident,))]
                status = "pending" if any(s in {"pending","running"} for s in states) else (
                    "done" if all(s=="done" for s in states) else "failed" if all(s=="failed" for s in states) else "partial")
                conn.execute("UPDATE collection_runs SET status=?,finished_at=?,lease=NULL,lease_until=0 WHERE id=? AND lease=?",
                             (status,None if status=="pending" else stamp(),ident,lease))
        self.wake.set()
        return True

    def publish(self, conn, job, translation, ts):
        raw = json.loads(job["raw_json"])
        ident = job["item_id"]
        if conn.execute("SELECT 1 FROM news_identities WHERE identity_key=? AND deleted=1",
                        (identity_key(raw["url"]),)).fetchone() or conn.execute(
                "SELECT 1 FROM deleted_news_items WHERE fingerprint=?", (item_fingerprint(ident),)).fetchone():
            conn.execute("UPDATE collection_items SET status='deleted',lease=NULL,lease_until=0 WHERE item_id=?", (ident,))
            return 0
        if news_versions.skip_duplicate_job(conn, job):
            return 0
        published = job["published_at"]
        quoted = "引用推文：" + translation["quoted_text"] if translation["quoted_text"] else ""
        summary = "\n\n".join(x for x in (translation["summary"], quoted) if x)
        inserted = conn.execute("""INSERT OR IGNORE INTO items(id,source_file,item_order,published_at,date,time,source,source_type,
                      source_name,title,summary,url,created_at,updated_at) VALUES (?,'',0,?,?,?,?,?,?,?,?,?,?,?)""",
                     (ident,published,published[:10],published[11:16],raw["source"],raw["source_type"],raw["source_name"],
                      translation["title"],summary,raw["url"],ts,ts)).rowcount
        conn.execute("UPDATE collection_items SET status='done',ingested_at=?,error='',lease=NULL,lease_until=0 WHERE item_id=?", (ts,ident))
        if inserted:
            # Keep the historical total even after source cleanup removes items.
            # This rolls back with the rest of the batch on publication failure.
            conn.execute("""UPDATE collection_run_sources SET ingested_count=ingested_count+?,ingested_at=?
                WHERE run_id=? AND source_id=?""", (inserted,ts,job["run_id"],job["source_id"]))
        return inserted

    def publish_once(self):
        ident = None
        try:
            with closing(self.connect()) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                run = conn.execute("""SELECT r.id FROM collection_runs r
                    WHERE r.status IN ('done','partial','failed') AND r.publication_status='pending'
                    AND NOT EXISTS (SELECT 1 FROM collection_items i WHERE i.run_id=r.id
                                    AND i.status NOT IN ('ready','done','deleted','duplicate'))
                    ORDER BY r.created_at,r.id LIMIT 1""").fetchone()
                if not run:
                    return False
                ident, ts = run[0], stamp()
                jobs = conn.execute("SELECT * FROM collection_items WHERE run_id=? AND status='ready' ORDER BY collected_at,rowid", (ident,)).fetchall()
                inserted = 0
                for job in jobs:
                    translation = collection_translation.validate(json.loads(job["translation_json"]), json.loads(job["raw_json"]))
                    inserted += self.publish(conn, job, translation, ts)
                if inserted:
                    news_classification.enqueue(conn)
                    topic_tracking.enqueue_checks(conn)
                conn.execute("UPDATE collection_runs SET publication_status='done',publication_error='',published_at=? WHERE id=?", (ts,ident))
        except Exception:
            if ident is None:
                raise
            # The entire batch, ingestion sequence and downstream queues roll
            # back together. Keep validated translations for a manual retry.
            logging.getLogger(__name__).exception("Collection batch publication failed")
            with closing(self.connect()) as conn, conn:
                conn.execute("""UPDATE collection_runs SET publication_status='failed',publication_error='collection_publish_failed'
                    WHERE id=? AND publication_status='pending'""", (ident,))
        return True

    def translate_once(self):
        lease = uuid.uuid4().hex
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if self.stopped.is_set():
                return False
            limit = normalize_schedule(self.settings().get("collection", {}))["translation_concurrency"]
            running = conn.execute("SELECT COUNT(*) FROM collection_items WHERE status='running' AND lease_until>?", (time.time(),)).fetchone()[0]
            if running >= limit:
                return False
            job = conn.execute("SELECT * FROM collection_items WHERE status='pending' OR (status='running' AND lease_until<=?) ORDER BY collected_at,item_id LIMIT 1", (time.time(),)).fetchone()
            if not job:
                return False
            job = dict(job)
            conn.execute("UPDATE collection_items SET status='running',lease=?,lease_until=?,attempts=attempts+1 WHERE item_id=?",
                         (lease,time.time()+LEASE_SECONDS,job["item_id"]))
        with self.heartbeat("collection_items", "item_id", job["item_id"], lease):
            try:
                raw = json.loads(job["raw_json"])
                translation = (json.loads(job["translation_json"]) if job["translation_json"]
                               else self.translator(raw,self.settings()))
                translation = collection_translation.validate(translation,raw)
                with closing(self.connect()) as conn, conn:
                    conn.execute("UPDATE collection_items SET translation_json=?,status='ready',error='',lease=NULL,lease_until=0 WHERE item_id=? AND lease=? AND status='running'",
                                           (encoded(translation),job["item_id"],lease))
            except Exception as exc:
                error = str(exc) if re.fullmatch(r"[A-Za-z0-9_: |.-]{1,500}", str(exc)) else "translation_or_publish_failed"
                with closing(self.connect()) as conn, conn:
                    conn.execute("UPDATE collection_items SET status='failed',error=?,lease=NULL,lease_until=0 WHERE item_id=? AND lease=?",
                                 (error,job["item_id"],lease))
        self.wake.set()
        return True

    def retry(self, *, run_id=None, source_id=None, stage=None):
        scoped = any(value is not None for value in (run_id, source_id, stage))
        if scoped and (not isinstance(run_id, str) or not run_id or not isinstance(stage, str) or stage not in {"translation", "publication"}
                       or (stage == "translation" and (not isinstance(source_id, str) or not source_id))
                       or (stage == "publication" and source_id is not None)):
            raise ValueError("collection_retry_invalid")
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if scoped:
                target = (conn.execute("SELECT 1 FROM collection_run_sources WHERE run_id=? AND source_id=?", (run_id,source_id)).fetchone()
                          if stage == "translation" else conn.execute("SELECT 1 FROM collection_runs WHERE id=?", (run_id,)).fetchone())
                if not target:
                    raise ValueError("collection_retry_target_missing")
            count = 0
            if stage in (None, "translation"):
                scope, args = (" AND run_id=? AND source_id=?", (run_id,source_id)) if scoped else ("", ())
                count = conn.execute("UPDATE collection_items SET status='pending',error='' WHERE status='failed'" + scope, args).rowcount
            if stage in (None, "publication"):
                scope, args = (" AND id=?", (run_id,)) if scoped else ("", ())
                count += conn.execute("UPDATE collection_runs SET publication_status='pending',publication_error='' WHERE publication_status='failed'" + scope, args).rowcount
        self.wake.set()
        return count

    def status(self):
        config = normalize_schedule(self.settings().get("collection", {}))
        at = now()
        upcoming = [slot for delta in range(8) for slot in self.times(at.date()+timedelta(days=delta),config) if slot>at] if config["enabled"] else []
        with closing(self.connect()) as conn:
            meta = dict(conn.execute("SELECT * FROM collection_meta").fetchone())
            counts = dict(conn.execute("SELECT status,COUNT(*) FROM collection_items GROUP BY status"))
            run = conn.execute("SELECT * FROM collection_runs ORDER BY created_at DESC LIMIT 1").fetchone()
            rows = conn.execute("SELECT * FROM collection_run_sources WHERE run_id=? ORDER BY rowid", (run["id"] if run else "",)).fetchall()
            unresolved = conn.execute("""SELECT s.* FROM collection_run_sources s WHERE s.rowid=(
                SELECT MAX(n.rowid) FROM collection_run_sources n WHERE n.source_id=s.source_id AND n.status IN ('done','failed'))
                AND s.status='failed'""").fetchall()
            boundary = batch_boundary(conn)
            publication_failed = conn.execute("SELECT COUNT(*) FROM collection_runs WHERE publication_status='failed'").fetchone()[0]
            awaiting_batches = conn.execute("""SELECT COUNT(*) FROM collection_runs r WHERE r.publication_status<>'done'
                AND EXISTS (SELECT 1 FROM collection_items i WHERE i.run_id=r.id AND i.status NOT IN ('done','deleted','duplicate'))""").fetchone()[0]
        return {"database_token":meta["database_token"],"schedule":config,
                "next_run_at":stamp(min(upcoming)) if upcoming else None,"schedule_error":self.schedule_error,
                "last_check":self.last_check,"running":bool(run and run["status"] in {"pending","running"}),
                "last_run":{k:run[k] for k in ("id","status","created_at","started_at","finished_at","publication_status","published_at")} if run else None,
                "counts":{s:counts.get(s,0) for s in ("pending","running","ready","failed","done")},
                "publication_failed":publication_failed,"awaiting_batches":awaiting_batches,
                "batch":boundary,"source_errors":len(unresolved),
                "sources":[{"id":s["section"],"label":s["display_name"],
                            **next(({k:r[k] for k in ("status","count","added","error")} for r in rows if r["source_id"]==s["section"]), {"status":"none"})} for s in self.sources]}

    def errors(self, day, *, details=True):
        # Timestamps are persisted in Shanghai ISO format. A lexical day bound
        # also handles 9999-12-31 without overflowing datetime's supported range.
        end = f"{day}T24:00:00"
        with closing(self.connect()) as conn:
            conn.execute("BEGIN")
            unresolved = conn.execute("""SELECT COUNT(*) FROM collection_run_sources s WHERE s.rowid=(
                SELECT MAX(n.rowid) FROM collection_run_sources n WHERE n.source_id=s.source_id AND n.status IN ('done','failed'))
                AND s.status='failed'""").fetchone()[0]
            payload = {"ok":True,"day":day,"today":now().date().isoformat(),"unresolved_count":unresolved}
            if not details:
                return payload
            rows = conn.execute("""SELECT s.*,r.created_at,r.started_at AS run_started_at,
                       r.publication_status,r.publication_error,r.published_at
                FROM collection_run_sources s JOIN collection_runs r ON r.id=s.run_id
                WHERE (s.started_at>=? AND s.started_at<?)
                   OR (s.started_at IS NULL AND r.created_at>=? AND r.created_at<?)
                   OR (s.ingested_at>=? AND s.ingested_at<?)
                ORDER BY COALESCE(s.started_at,r.created_at) DESC,s.rowid DESC""", (day,end,day,end,day,end)).fetchall()
            translation_errors = {}
            run_ids = sorted({r["run_id"] for r in rows})
            if run_ids:
                placeholders = ",".join("?" for _ in run_ids)
                for failure in conn.execute(f"""SELECT run_id,source_id,error,COUNT(*) AS count FROM collection_items
                        WHERE status='failed' AND run_id IN ({placeholders})
                        GROUP BY run_id,source_id,error ORDER BY run_id,source_id,error""", run_ids):
                    translation_errors.setdefault((failure["run_id"],failure["source_id"]), []).append(failure)
        records, failures = [], []
        latest = {}
        for r in rows:
            started = r["started_at"] or r["created_at"]
            collected_today = started[:10] == day
            ingested_today = bool(r["ingested_at"] and r["ingested_at"][:10] == day)
            label = json.loads(r["config_json"]).get("display_name", r["source_id"])
            notices = []
            if collected_today:
                failed = translation_errors.get((r["run_id"],r["source_id"]), [])
                if failed:
                    notices.append({"stage":"translation","count":sum(f["count"] for f in failed),
                                    "error":"\n".join(f"{f['count']} 条：{f['error'] or 'translation_failed'}" for f in failed)})
                if r["publication_status"] == "failed":
                    notices.append({"stage":"publication","error":r["publication_error"] or "collection_publish_failed"})
            record = {"run_id":r["run_id"],"source_id":r["source_id"],"label":label,
                "batch_started_at":r["run_started_at"] or r["created_at"],"notices":notices,
                "date":day,"time":(started if collected_today else r["ingested_at"])[11:19],
                "kind":"collection" if collected_today else "publication",
                "status":r["status"] if collected_today else "done",
                "publication_status":r["publication_status"],"published_at":r["published_at"],
                "fetched_count":r["count"] if collected_today else 0,
                "duplicate_count":r["duplicate_count"] if collected_today else 0,
                "filtered_count":r["filtered_count"] if collected_today else 0,
                "ingested_count":r["ingested_count"] if ingested_today else 0,
                "error_count":int(collected_today and r["status"]=="failed"),
                "error":r["error"] if collected_today else ""}
            records.append(record)
            if record["error_count"]:
                failures.append({"time":record["time"],"entries":[{"label":label,"error":r["error"]}]})
            if collected_today and r["status"] in {"done","failed"}:
                latest.setdefault(r["source_id"],r["status"])
        available = sum(s=="done" for s in latest.values())
        return {**payload,"records":records,
                "days":[{"date":day,"groups":failures}] if failures else [],
                "stats":{"available_sources":available,"total_sources":len(latest),
                         "availability":round(100*available/len(latest),1) if latest else None,
                         **{key:sum(r[key] or 0 for r in records) for key in
                            ("fetched_count","duplicate_count","ingested_count","error_count")},
                         "duplicate_unknown":sum(r["duplicate_count"] is None and r["status"] in {"done","failed"} for r in records)}}
