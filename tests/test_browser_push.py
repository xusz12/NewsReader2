from contextlib import closing
import json
import sqlite3
import time
import uuid

import pytest

from app import create_app
from nr import browser_push
import push_events


ORIGIN = "https://nr.example.test"
DEVICE = "/api/browser-push/device"
HEADERS = {"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"}


@pytest.fixture
def instance(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWS_READER_PUSH_ORIGIN", ORIGIN)
    for variable, name in {
        "DB_PATH":"main.sqlite3", "AGENT_DB_PATH":"agent.sqlite3", "MEDIA_CACHE_DIR":"media",
        "PERSONAL_ASSISTANT_DIR":"assistant", "APP_SETTINGS_PATH":"settings.json", "AGENT_RUNTIME_DIR":"agent-run",
    }.items():
        monkeypatch.setenv("NEWS_READER_" + variable, str(tmp_path / name))
    app = create_app()
    app.extensions["newsreader"].initialize()
    return app, app.extensions["newsreader"].push


def subscription(name, host="fcm.googleapis.com"):
    public, _ = browser_push.generate_keys()
    return {"endpoint": f"https://{host}/fcm/send/{name}", "keys": {"p256dh":public, "auth":browser_push.b64(bytes(16))}}


def request_device(app, method, device, types=None):
    return app.test_client().open(DEVICE, method=method, base_url=ORIGIN, headers=HEADERS,
                                  json={"subscription":device, "types":types})


def test_private_origin_and_subscription_capability(instance):
    app, service = instance
    client = app.test_client()
    device, other = subscription("first"), subscription("second")
    config = client.get("/api/browser-push/config", base_url=ORIGIN).json
    assert config["available"] and set(config) == {"ok", "available", "public_key"}
    restarted = browser_push.Service(service.db)
    assert restarted.public_key() == config["public_key"]
    assert client.get("/api/browser-push/config").json["available"] is False
    forwarded = {"Host":service.host, "X-Forwarded-Host":service.host, "X-Forwarded-Proto":"https"}
    assert client.get("/api/browser-push/config", headers=forwarded).json["available"] is True
    assert client.get("/api/browser-push/config", headers={**forwarded, "Tailscale-Funnel-Request":"?1"}).json["available"] is False
    assert client.get("/api/browser-push/config", headers=forwarded, environ_overrides={"REMOTE_ADDR":"100.64.0.9"}).json["available"] is False
    assert client.put(DEVICE, base_url=ORIGIN, headers={"Origin":"https://untrusted.example"}, json={"subscription":device}).status_code == 403
    assert client.put(DEVICE, base_url=ORIGIN, headers={**HEADERS, "Sec-Fetch-Site":"cross-site"}, json={"subscription":device}).status_code == 403
    assert request_device(app, "PUT", device, ["daily_report"]).status_code == 200
    assert request_device(app, "PUT", other, ["article_reply"]).status_code == 200
    assert request_device(app, "POST", device).json == {"ok":True, "registered":True, "types":["daily_report"]}
    assert request_device(app, "DELETE", device).status_code == 200
    assert request_device(app, "POST", device).json["registered"] is False
    assert request_device(app, "POST", other).json["types"] == ["article_reply"]
    worker = client.get("/push-sw.js", base_url=ORIGIN)
    assert worker.status_code == 200 and worker.headers["Cache-Control"] == "no-cache"


@pytest.mark.parametrize("endpoint", [
    "http://fcm.googleapis.com/fcm/send/test", "https://127.0.0.1:18080/test",
    "https://fcm.googleapis.com.other.test/fcm/send/not-google", "https://fcm.googleapis.com:444/fcm/send/test",
    "https://fake.test@fcm.googleapis.com/fcm/send/test", "https://foo.push.apple.com.other.test/example",
])
def test_rejects_unsupported_endpoint_before_persisting(instance, endpoint):
    app, service = instance
    assert request_device(app, "PUT", subscription("first") | {"endpoint":endpoint}, ["daily_report"]).status_code == 400
    with closing(service.db.connect()) as conn:
        assert not conn.execute("SELECT 1 FROM browser_push_devices").fetchone()


def test_real_encryption_disables_redirects_and_uses_stable_topic(monkeypatch):
    from requests import Response, Session

    calls = []

    def post(self, url, **kwargs):
        calls.append((url, kwargs))
        response = Response()
        response.status_code = 302
        response.headers["Location"] = "https://example.invalid/elsewhere"
        return response

    monkeypatch.setattr(Session, "post", post)
    _, private = browser_push.generate_keys()
    payload = {"tag":"a" * 32, "title":"Synthetic reply"}
    device = subscription("encryption")
    assert browser_push.deliver(device, payload, private, ORIGIN, 50) == 302
    assert len(calls) == 1 and calls[0][0] == device["endpoint"]
    _, sent = calls[0]
    assert sent["allow_redirects"] is False
    assert sent["headers"]["Topic"] == payload["tag"] and sent["headers"]["content-encoding"] == "aes128gcm"
    assert b"Synthetic reply" not in sent["data"]


def test_report_and_actionable_collection_are_atomic_and_once_per_period_or_run(instance):
    _, service = instance
    with closing(service.db.connect()) as conn, conn:
        for ident, kind, edition in (("report-one", "morning", "morning:today"), ("same-period", "morning", "manual:1"),
                                      ("weekly-one", "weekly", "weekly:today"), ("preview-one", "noon", "trial:today")):
            conn.execute("""INSERT INTO periodic_reports(id,edition_key,kind,title,period_start,period_end,scheduled_at,created_at,updated_at)
                VALUES(?,?,?,'report','2026-11-22T07:00:00+08:00','2026-11-22T08:00:00+08:00','now','now','now')""", (ident,edition,kind))
        conn.execute("UPDATE periodic_reports SET status='done' WHERE id='report-one'")
        conn.rollback()
        assert not conn.execute("SELECT 1 FROM browser_push_events").fetchone()
    with closing(service.db.connect()) as conn, conn:
        for ident, kind, edition in (("report-one","morning","morning:today"), ("same-period","morning","manual:1"),
                                      ("weekly-one","weekly","weekly:today"), ("preview-one","noon","trial:today")):
            conn.execute("""INSERT INTO periodic_reports(id,edition_key,kind,title,period_start,period_end,scheduled_at,created_at,updated_at)
                VALUES(?,?,?,'report','2026-11-22T07:00:00+08:00','2026-11-22T08:00:00+08:00','now','now','now')""", (ident,edition,kind))
            conn.execute("UPDATE periodic_reports SET status='done' WHERE id=?", (ident,))
        conn.execute("INSERT INTO collection_runs(id,trigger,status,created_at) VALUES('batch','manual','partial','2026-11-22T12:00:00+08:00')")
        conn.execute("INSERT INTO collection_run_sources(run_id,source_id,config_json,status) VALUES('batch','origin','{}','failed')")
        conn.execute("""INSERT INTO collection_items(item_id,run_id,source_id,raw_json,status,collected_at,published_at)
            VALUES('item','batch','origin','{}','retry_wait','now','now')""")
        assert conn.execute("SELECT COUNT(*) FROM browser_push_events").fetchone()[0] == 1
        conn.execute("UPDATE collection_items SET status='failed' WHERE run_id='batch'")
        conn.execute("UPDATE collection_runs SET publication_status='done' WHERE id='batch'")
        conn.execute("UPDATE collection_runs SET publication_status='pending' WHERE id='batch'")
        conn.execute("UPDATE collection_runs SET publication_status='failed' WHERE id='batch'")
        events = [(row["kind"], json.loads(row["target"])) for row in conn.execute("SELECT kind,target FROM browser_push_events ORDER BY seq")]
        assert events == [("daily_report", {"id":"report-one"}), ("collection_error", {"id":"batch", "day":"2026-11-22"})]


def test_device_types_seen_and_restart(instance, monkeypatch):
    app, service = instance
    foreground, background = subscription("foreground"), subscription("background")
    service.public_key()
    request_device(app, "PUT", foreground, ["article_reply", "daily_report"])
    request_device(app, "PUT", background, ["article_reply"])
    events = (str(uuid.uuid4()), str(uuid.uuid4()))
    with closing(service.db.agent_connect()) as conn, conn:
        push_events.record(conn, "article_reply", events[0], {"id":events[0]}, service.library)
        push_events.record(conn, "article_reply", events[1], {"id":events[1]}, "different-library")
    reply = {"kind":"article_reply", "id":events[0]}
    assert app.test_client().post("/api/browser-push/seen", base_url=ORIGIN, headers=HEADERS,
        json={"subscription":foreground, "replies":[reply]}).status_code == 200
    assert service.queue_deliveries(time.time())
    with closing(service.db.connect()) as conn, conn:
        assert conn.execute("SELECT COUNT(*) FROM browser_push_deliveries").fetchone()[0] == 2
        conn.execute("UPDATE browser_push_deliveries SET next_attempt=0")
    sends = []

    def sender(device, payload, *_):
        sends.append((device["endpoint"], payload))
        return 503 if len(sends) == 1 else 201

    restarted = browser_push.Service(service.db, sender=sender)
    for _ in range(3):
        restarted.step()
    assert len(sends) == 1 and sends[0][0] == background["endpoint"]
    assert sends[0][1]["target"]["id"] == events[0]
    with closing(service.db.connect()) as conn, conn:
        conn.execute("UPDATE browser_push_deliveries SET next_attempt=0")
    restarted.step()
    assert sends[1] == sends[0]
    for _ in range(3):
        assert not restarted.step()
    assert len(sends) == 2

    cloned = type(service.db)()
    cloned.path = service.db.path.with_name("dev-copy.sqlite3")
    with closing(service.db.connect()) as original, closing(sqlite3.connect(cloned.path)) as copy:
        original.backup(copy)
    copied = browser_push.Service(cloned, sender=sender)
    assert copied.scope != service.scope and copied.public_key() != service.public_key()
    assert not copied.step()


def test_toggles_and_expired_subscription_do_not_replay_events(instance):
    app, service = instance
    device = subscription("expires")
    service.public_key()
    request_device(app, "PUT", device, ["daily_report"])
    with closing(service.db.connect()) as conn, conn:
        push_events.record(conn, "daily_report", "period", {"id":"first"}, "")
    assert service.queue_deliveries(time.time())
    request_device(app, "PUT", device, [])
    sent = []
    service.sender = lambda *args: sent.append(args) or 410
    service.step()
    assert sent == []
    request_device(app, "PUT", device, ["daily_report"])
    with closing(service.db.connect()) as conn, conn:
        push_events.record(conn, "daily_report", "next", {"id":"next"}, "")
    service.step()
    assert len(sent) == 1 and sent[0][1]["target"] == {"id":"next"}
    assert request_device(app, "POST", device).json["registered"] is False
