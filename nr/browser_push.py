import base64
from contextlib import closing
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
from threading import Lock, Thread
import time
from urllib.parse import urlsplit

from flask import abort, jsonify, make_response, request, send_from_directory

from agent_notifications import library_scope
import push_events


MESSAGES = {
    "daily_report": ("日报已完成", "打开 News Reader 查看当期日报。"),
    "collection_error": ("采集失败待处理", "打开 News Reader 查看本批次错误。"),
    "personal_reply": ("个人助手回复已完成", "打开 News Reader 查看回复。"),
    "article_reply": ("文章问答回复已完成", "打开 News Reader 查看回复。"),
}
DELIVERY_TTL = 86400
REPLY_TYPES = {"personal_reply", "article_reply"}
REPLY_DELAY = 8
ENDPOINT_HOSTS = {"fcm.googleapis.com", "updates.push.services.mozilla.com", "push.services.mozilla.com"}
ENDPOINT_SUFFIXES = (".push.apple.com", ".notify.windows.com")
LOGGER = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS browser_push_keys (
    scope TEXT PRIMARY KEY, public_key TEXT NOT NULL, private_key TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS browser_push_devices (
    scope TEXT NOT NULL, id TEXT NOT NULL, endpoint TEXT NOT NULL, subscription TEXT NOT NULL,
    choices TEXT NOT NULL, PRIMARY KEY(scope,id), UNIQUE(scope,endpoint));
CREATE TABLE IF NOT EXISTS browser_push_cursors (
    scope TEXT NOT NULL, source TEXT NOT NULL, seq INTEGER NOT NULL, PRIMARY KEY(scope,source));
CREATE TABLE IF NOT EXISTS browser_push_deliveries (
    scope TEXT NOT NULL, device_id TEXT NOT NULL, tag TEXT NOT NULL, kind TEXT NOT NULL,
    target TEXT NOT NULL, created_at REAL NOT NULL, next_attempt REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
    lease TEXT NOT NULL DEFAULT '', PRIMARY KEY(scope,device_id,tag));
CREATE INDEX IF NOT EXISTS browser_push_delivery_due ON browser_push_deliveries(scope,next_attempt);
CREATE TABLE IF NOT EXISTS browser_push_seen (
    scope TEXT NOT NULL, device_id TEXT NOT NULL, tag TEXT NOT NULL, seen_at REAL NOT NULL,
    PRIMARY KEY(scope,device_id,tag));
CREATE TRIGGER IF NOT EXISTS browser_push_daily_report AFTER UPDATE OF status ON periodic_reports
WHEN NEW.status='done' AND OLD.status<>'done' AND NEW.kind IN ('morning','noon','evening')
    AND NEW.edition_key NOT LIKE 'trial:%'
BEGIN
    INSERT OR IGNORE INTO browser_push_events(kind,event_key,target,created_at)
    VALUES('daily_report',NEW.kind || ':' || substr(NEW.period_end,1,10),json_object('id',NEW.id),
        round((julianday('now')-2440587.5)*86400,3));
END;
CREATE TRIGGER IF NOT EXISTS browser_push_collection_failure AFTER UPDATE OF publication_status ON collection_runs
WHEN NEW.publication_status IN ('done','failed') AND NEW.publication_status<>OLD.publication_status
    AND (NEW.publication_status='failed'
        OR EXISTS(SELECT 1 FROM collection_run_sources WHERE run_id=NEW.id AND status='failed')
        OR EXISTS(SELECT 1 FROM collection_items WHERE run_id=NEW.id AND status='failed'))
BEGIN
    INSERT OR IGNORE INTO browser_push_events(kind,event_key,target,created_at)
    VALUES('collection_error',NEW.id,json_object('id',NEW.id,'day',substr(COALESCE(NEW.started_at,NEW.created_at),1,10)),
        round((julianday('now')-2440587.5)*86400,3));
END;
"""


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def select_origin(value):
    if not value:
        return ""
    url = urlsplit(value)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.path not in ("", "/") or url.query or url.fragment
            or not re.fullmatch(r"[a-z0-9.-]+(?::[0-9]+)?", url.netloc)):
        raise ValueError("NEWS_READER_PUSH_ORIGIN 需要完整的私有 HTTPS 源地址。")
    return f"https://{url.netloc}"


def subscription_data(value):
    endpoint = value.get("endpoint") if isinstance(value, dict) else None
    if not isinstance(endpoint, str) or not 20 < len(endpoint) <= 2048 or not re.fullmatch(r"[!-~]+", endpoint) or "\\" in endpoint:
        raise ValueError("invalid_subscription")
    url = urlsplit(endpoint)
    host = url.hostname or ""
    if (url.scheme != "https" or url.netloc != host or len(url.path) < 2 or url.fragment
            or not (host in ENDPOINT_HOSTS or host.endswith(ENDPOINT_SUFFIXES))):
        raise ValueError("unsupported_push_endpoint")

    keys = value.get("keys")
    if not isinstance(keys, dict):
        raise ValueError("invalid_subscription")
    decoded = {}
    for name, size in (("auth", 16), ("p256dh", 65)):
        token = keys.get(name)
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,88}={0,2}", token):
            raise ValueError("invalid_subscription")
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        if len(raw) != size:
            raise ValueError("invalid_subscription")
        decoded[name] = raw
    from cryptography.hazmat.primitives.asymmetric import ec

    if decoded["p256dh"][0] != 4:
        raise ValueError("invalid_subscription")
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), decoded["p256dh"])
    clean = {"endpoint": endpoint, "keys": {name: b64(raw) for name, raw in decoded.items()}}
    return digest(json.dumps(clean, sort_keys=True)), clean


def generate_keys():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return b64(public), b64(key.private_numbers().private_value.to_bytes(32, "big"))


def deliver(subscription, payload, private_key, origin, ttl):
    from pywebpush import WebPushException, webpush
    from requests import Session

    class PushSession(Session):
        def post(self, url, **values):
            return super().post(url, allow_redirects=False, **values)

    with PushSession() as session:
        session.trust_env = False
        try:
            return webpush(subscription, json.dumps(payload, ensure_ascii=False), vapid_private_key=private_key,
                vapid_claims={"sub": origin}, ttl=ttl, timeout=8, headers={"Topic": payload["tag"]},
                requests_session=session).status_code
        except WebPushException as error:
            return error.response.status_code if error.response is not None else 400
        except OSError:
            return 503


class Service:
    def __init__(self, db, sender=deliver):
        self.db = db
        self.sender = sender
        self.origin = select_origin(os.environ.get("NEWS_READER_PUSH_ORIGIN", "").strip())
        self.host = urlsplit(self.origin).netloc
        self.library = library_scope(db.path)
        self.scope = digest(self.library + "\n" + self.origin)
        self.assistant_path = None
        self.start_lock = Lock()
        self.worker = None

    def initialize(self, conn):
        push_events.initialize(conn)
        conn.executescript(SCHEMA)

    def public_key(self):
        with closing(self.db.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT public_key FROM browser_push_keys WHERE scope=?", (self.scope,)).fetchone()
            if row:
                return row[0]
            public, private = generate_keys()
            conn.execute("INSERT INTO browser_push_keys VALUES(?,?,?)", (self.scope, public, private))
            return public

    def device(self, verb, value):
        device_id, subscription = subscription_data(value.get("subscription"))
        with closing(self.db.connect()) as conn, conn:
            if verb != "POST":
                conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT choices FROM browser_push_devices WHERE scope=? AND id=?", (self.scope, device_id)).fetchone()
            choices = json.loads(row[0]) if row else {}
            if verb == "POST":
                return {"registered": row is not None, "types": list(choices)}
            if verb == "DELETE":
                self.remove(conn, device_id)
                return {"registered": False, "types": []}

            types = value.get("types")
            if not isinstance(types, list) or len(types) > len(MESSAGES) or any(not isinstance(kind, str) or kind not in MESSAGES for kind in types):
                raise ValueError("invalid_push_types")
            if not row and conn.execute("SELECT COUNT(*) FROM browser_push_devices WHERE scope=?", (self.scope,)).fetchone()[0] >= 32:
                raise ValueError("push_device_limit")
            enabled_at = round(time.time(), 3)
            choices = {kind: choices.get(kind, enabled_at) for kind in MESSAGES if kind in types}
            conn.execute("""INSERT INTO browser_push_devices VALUES(?,?,?,?,?)
                ON CONFLICT(scope,id) DO UPDATE SET choices=excluded.choices""",
                (self.scope, device_id, subscription["endpoint"], json.dumps(subscription), json.dumps(choices)))
            return {"registered": True, "types": list(choices)}

    def remove(self, conn, device_id):
        for table, name in (("browser_push_devices", "id"), ("browser_push_deliveries", "device_id"),
                            ("browser_push_seen", "device_id")):
            conn.execute(f"DELETE FROM {table} WHERE scope=? AND {name}=?", (self.scope, device_id))

    def seen(self, value):
        device_id, _ = subscription_data(value.get("subscription"))
        replies = value.get("replies")
        if not isinstance(replies, list) or not 1 <= len(replies) <= 12:
            raise ValueError("invalid_push_replies")
        tags = []
        for reply in replies:
            if (not isinstance(reply, dict) or not isinstance(reply.get("kind"), str) or reply["kind"] not in REPLY_TYPES
                    or not isinstance(reply.get("id"), str) or not re.fullmatch(r"[a-f0-9-]{36}", reply["id"])):
                raise ValueError("invalid_push_reply")
            tags.append(digest(reply["kind"] + ":" + reply["id"])[:32])
        clock = time.time()
        with closing(self.db.connect()) as conn, conn:
            conn.execute("DELETE FROM browser_push_seen WHERE scope=? AND device_id=? AND seen_at<?",
                         (self.scope, device_id, clock - DELIVERY_TTL))
            conn.executemany("""INSERT OR IGNORE INTO browser_push_seen
                SELECT ?,?,?,? WHERE EXISTS(SELECT 1 FROM browser_push_devices WHERE scope=? AND id=?)""",
                ((self.scope, device_id, tag, clock, self.scope, device_id) for tag in tags))

    def read_events(self, path, after, library):
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        try:
            with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
                if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='browser_push_events'").fetchone():
                    return []
                return conn.execute("""SELECT seq,kind,event_key,target,created_at FROM browser_push_events
                    WHERE library=? AND seq>? ORDER BY seq LIMIT 64""", (library, after)).fetchall()
        except sqlite3.OperationalError:
            if not Path(path).is_file():
                return []
            raise

    def queue_deliveries(self, clock):
        with closing(self.db.connect()) as conn:
            if not conn.execute("SELECT 1 FROM browser_push_devices WHERE scope=? AND choices<>'{}'", (self.scope,)).fetchone():
                return False
            cursors = dict(conn.execute("SELECT source,seq FROM browser_push_cursors WHERE scope=?", (self.scope,)))
            streams = [("main", self.db.path, ""), ("article", self.db.agent_path, self.library)]
            if self.assistant_path:
                streams.append(("assistant", self.assistant_path, self.library))
            batches = {}
            for role, path, library in streams:
                source = role + ":" + digest(str(Path(path).resolve()))
                rows = self.read_events(path, cursors.get(source, 0), library)
                if rows:
                    batches[source] = rows
            if not batches:
                return False
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                devices = [(row["id"], json.loads(row["choices"])) for row in conn.execute(
                    "SELECT id,choices FROM browser_push_devices WHERE scope=?", (self.scope,))]
                for source, rows in batches.items():
                    latest = conn.execute("SELECT seq FROM browser_push_cursors WHERE scope=? AND source=?", (self.scope, source)).fetchone()
                    for seq, kind, key, target, created_at in rows:
                        if (latest and seq <= latest[0]) or created_at < clock - DELIVERY_TTL:
                            continue
                        tag = digest(kind + ":" + key)[:32]
                        next_attempt = max(clock, created_at + REPLY_DELAY) if kind in REPLY_TYPES else clock
                        conn.executemany("INSERT OR IGNORE INTO browser_push_deliveries VALUES(?,?,?,?,?,?,?,0,'')", (
                            (self.scope, device_id, tag, kind, target, created_at, next_attempt)
                            for device_id, choices in devices if kind in choices and choices[kind] <= created_at))
                    conn.execute("""INSERT INTO browser_push_cursors VALUES(?,?,?)
                        ON CONFLICT(scope,source) DO UPDATE SET seq=MAX(seq,excluded.seq)""", (self.scope, source, rows[-1][0]))
            return True

    def claim(self, clock):
        with closing(self.db.connect()) as conn, conn:
            due = conn.execute("SELECT rowid FROM browser_push_deliveries WHERE scope=? AND next_attempt<=? LIMIT 1",
                               (self.scope, clock)).fetchone()
            if not due:
                return None
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("""SELECT d.rowid,d.*,s.subscription,s.choices,k.private_key FROM browser_push_deliveries d
                JOIN browser_push_devices s ON s.scope=d.scope AND s.id=d.device_id
                JOIN browser_push_keys k ON k.scope=d.scope WHERE d.rowid=? AND d.scope=? AND d.next_attempt<=?""",
                (due[0], self.scope, clock)).fetchone()
            if not row:
                return None
            since = json.loads(row["choices"]).get(row["kind"])
            seen = row["kind"] in REPLY_TYPES and conn.execute("""SELECT 1 FROM browser_push_seen
                WHERE scope=? AND device_id=? AND tag=?""", (self.scope, row["device_id"], row["tag"])).fetchone()
            if seen or since is None or since > row["created_at"] or row["created_at"] <= clock - DELIVERY_TTL:
                conn.execute("DELETE FROM browser_push_deliveries WHERE rowid=?", (row["rowid"],))
                return None
            lease = os.urandom(12).hex()
            conn.execute("UPDATE browser_push_deliveries SET lease=?,next_attempt=?,attempts=attempts+1 WHERE rowid=?",
                         (lease, clock + 30, row["rowid"]))
            return dict(row) | {"lease": lease}

    def step(self):
        clock = time.time()
        found = self.queue_deliveries(clock)
        row = self.claim(clock)
        if not row:
            return found
        title, body = MESSAGES[row["kind"]]
        payload = {"kind": row["kind"], "tag": row["tag"], "target": json.loads(row["target"]), "title": title, "body": body}
        subscription = subscription_data(json.loads(row["subscription"]))[1]
        status = self.sender(subscription, payload, row["private_key"], self.origin,
                             max(1, int(row["created_at"] + DELIVERY_TTL - clock)))
        with closing(self.db.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute("SELECT 1 FROM browser_push_deliveries WHERE rowid=? AND lease=?", (row["rowid"], row["lease"])).fetchone():
                return True
            if status in (404, 410):
                self.remove(conn, row["device_id"])
            elif status in (408, 429) or 500 <= status < 600:
                conn.execute("UPDATE browser_push_deliveries SET next_attempt=?,lease='' WHERE rowid=?",
                             (time.time() + min(900, 30 * 2 ** min(row["attempts"], 5)), row["rowid"]))
            else:
                conn.execute("DELETE FROM browser_push_deliveries WHERE rowid=?", (row["rowid"],))
        return True

    def start(self):
        if not self.origin:
            return
        with self.start_lock:
            if self.worker and self.worker.is_alive():
                return
            self.worker = Thread(target=self.run, name="browser-push", daemon=True)
            self.worker.start()

    def run(self):
        while True:
            try:
                if self.step():
                    continue
            except (OSError, sqlite3.Error, ValueError, ImportError) as error:
                LOGGER.warning("Browser push unavailable (%s)", type(error).__name__)
            time.sleep(3)


def register(app, service):
    def private_origin():
        proxied = (request.remote_addr in ("127.0.0.1", "::1")
                   and request.headers.get("X-Forwarded-Proto") == "https"
                   and request.headers.get("X-Forwarded-Host") == service.host)
        return (service.origin and request.host == service.host and (request.is_secure or proxied)
                and not request.headers.get("Tailscale-Funnel-Request"))

    def private_body():
        if (not private_origin() or request.headers.get("Origin") != service.origin
                or request.headers.get("Sec-Fetch-Site", "same-origin") != "same-origin"):
            abort(make_response(jsonify(ok=False, error="push_origin_required"), 403))
        request.max_content_length = 4096
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            abort(make_response(jsonify(ok=False, error="invalid_subscription"), 400))
        return value

    @app.get("/push-sw.js")
    def browser_push_worker():
        response = send_from_directory(service.db.static_dir, "push-sw.js", max_age=0)
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/api/browser-push/config")
    def browser_push_config():
        if not private_origin():
            return jsonify(ok=True, available=False)
        return jsonify(ok=True, available=True, public_key=service.public_key()), 200, {"Cache-Control": "no-store"}

    @app.route("/api/browser-push/device", methods=["POST", "PUT", "DELETE"])
    def browser_push_device():
        try:
            response = service.device(request.method, private_body())
        except ValueError:
            return jsonify(ok=False, error="invalid_subscription"), 400
        except sqlite3.IntegrityError:
            return jsonify(ok=False, error="push_subscription_conflict"), 409
        return jsonify(ok=True, **response), 200, {"Cache-Control": "no-store"}

    @app.post("/api/browser-push/seen")
    def browser_push_seen():
        try:
            service.seen(private_body())
        except ValueError:
            return jsonify(ok=False, error="invalid_push_replies"), 400
        return jsonify(ok=True), 200, {"Cache-Control": "no-store"}
