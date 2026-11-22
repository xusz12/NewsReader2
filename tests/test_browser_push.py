import base64
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
LOOPBACK = "http://127.0.0.1:18080"


@pytest.fixture
def instance(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWS_READER_PUSH_ORIGIN", ORIGIN)
    monkeypatch.delenv("NEWS_READER_PUSH_LOOPBACK_PORT", raising=False)
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


def test_loopback_reuses_private_key_and_keeps_existing_https_device(instance, monkeypatch):
    original_app, original_service = instance
    old = subscription("https-saved")
    key = original_service.public_key()
    assert request_device(original_app, "PUT", old, ["daily_report"]).status_code == 200
    assert original_app.test_client().get("/api/browser-push/config", base_url=LOOPBACK).json["available"] is False

    monkeypatch.setenv("NEWS_READER_PUSH_LOOPBACK_PORT", "18080")
    app = create_app()
    service = app.extensions["newsreader"].push
    assert service.origin == ORIGIN and service.scope == original_service.scope
    assert request_device(app, "POST", old).json == {"ok":True, "registered":True, "types":["daily_report"]}
    with app.test_client() as client:
        assert client.get("/api/browser-push/config", base_url=LOOPBACK).json["public_key"] == key
        local = subscription("http-local")
        headers = {"Origin": LOOPBACK, "Sec-Fetch-Site":"same-origin"}
        payload = {"subscription":local, "types":["article_reply", "personal_reply"]}
        assert set(client.put(DEVICE, base_url=LOOPBACK, headers=headers, json=payload).json["types"]) == set(payload["types"])
        match = {"replies":[{"kind":"article_reply", "id":str(uuid.uuid4())}], "subscription":local}
        assert client.post("/api/browser-push/seen", base_url=LOOPBACK, headers=headers, json=match).status_code == 200
        restarted = create_app().test_client()
        restored = restarted.post(DEVICE, base_url=LOOPBACK, headers=headers, json={"subscription":local}).json
        assert restored["ok"] and restored["registered"] and set(restored["types"]) == set(payload["types"])
        assert client.delete(DEVICE, base_url=LOOPBACK, headers=headers, json={"subscription":local}).status_code == 200
        assert client.post(DEVICE, base_url=LOOPBACK, headers=headers, json={"subscription":local}).json["registered"] is False
    assert request_device(app, "POST", old).json["types"] == ["daily_report"]
    assert service.public_key() == key


@pytest.mark.parametrize("url,peer,headers", [
    ("http://localhost:18080", "127.0.0.1", {}),
    ("http://127.0.0.2:18080", "127.0.0.1", {}),
    ("http://127.0.0.1:18081", "127.0.0.1", {}),
    ("http://100.64.1.2:18080", "100.64.1.3", {}),
    (LOOPBACK, "100.64.1.3", {}),
    (LOOPBACK, "::1", {}),
    (LOOPBACK, "127.0.0.1", {"Forwarded":"by=127.0.0.1"}),
    (LOOPBACK, "127.0.0.1", {"X-Forwarded-Proto":"http"}),
    (LOOPBACK, "127.0.0.1", {"Tailscale-Funnel-Request":"?1"}),
    (LOOPBACK, "127.0.0.1", {"Origin":ORIGIN}),
])
def test_only_direct_configured_loopback_origin_can_register(instance, monkeypatch, url, peer, headers):
    monkeypatch.setenv("NEWS_READER_PUSH_LOOPBACK_PORT", "18080")
    client = create_app().test_client()
    request_headers = {"Origin":url, **headers}
    env = {"REMOTE_ADDR":peer}
    assert client.get("/api/browser-push/config", base_url=url, headers=request_headers,
                      environ_overrides=env).json["available"] is False
    assert client.put(DEVICE, base_url=url, headers=request_headers, environ_overrides=env,
                      json={"subscription":subscription("must-fail"), "types":["daily_report"]}).status_code == 403


def test_loopback_mutations_require_exact_origin_and_same_site_fetch(instance, monkeypatch):
    monkeypatch.setenv("NEWS_READER_PUSH_LOOPBACK_PORT", "18080")
    client = create_app().test_client()
    body = {"subscription":subscription("must-fail"), "types":["daily_report"]}
    assert client.put(DEVICE, base_url=LOOPBACK, json=body).status_code == 403
    assert client.put(DEVICE, base_url=LOOPBACK, headers={"Origin":LOOPBACK,"Sec-Fetch-Site":"cross-site"}, json=body).status_code == 403


def test_local_subscription_survives_adding_private_https(instance, monkeypatch):
    original, _ = instance
    previous = subscription("local-from-the-earlier-https-scope")
    assert request_device(original, "PUT", previous, ["daily_report"]).json["registered"]
    monkeypatch.delenv("NEWS_READER_PUSH_ORIGIN")
    app = create_app()
    runtime = app.extensions["newsreader"]
    runtime.push.configure(local_port="18080")
    runtime.initialize()
    local = app.test_client()
    config = "/api/browser-push/config"
    key = local.get(config, base_url=LOOPBACK).json["public_key"]
    device = subscription("local-before-serve")
    body = {"subscription":device, "types":["article_reply"]}
    headers = {"Origin":LOOPBACK}
    assert local.put(DEVICE, base_url=LOOPBACK, headers=headers, json=body).json["registered"]
    assert not local.get(config, base_url="http://100.64.1.2:18080").json["available"]
    assert not local.get(config, base_url=LOOPBACK, headers={"X-Forwarded-For":"100.64.1.2"}).json["available"]

    with_https = create_app()
    runtime = with_https.extensions["newsreader"]
    runtime.push.configure(ORIGIN, "18080")
    runtime.initialize()
    client = with_https.test_client()
    assert client.get(config, base_url=ORIGIN).json["public_key"] != key
    assert client.get(config, base_url=LOOPBACK).json["public_key"] == key
    assert client.post(DEVICE, base_url=LOOPBACK, headers=headers, json=body).json["types"] == ["article_reply"]
    legacy_body = {"subscription":previous, "types":["personal_reply"]}
    assert client.post(DEVICE, base_url=LOOPBACK, headers=headers, json=legacy_body).json["types"] == ["daily_report"]
    assert client.put(DEVICE, base_url=LOOPBACK, headers=headers, json=legacy_body).json["types"] == ["personal_reply"]
    assert request_device(with_https, "POST", previous).json["types"] == ["personal_reply"]
    assert not request_device(with_https, "POST", device).json["registered"]

    monkeypatch.setenv("NEWS_READER_PUSH_ORIGIN", "")
    runtime.push.configure(ORIGIN, "18080")
    assert not client.get(config, base_url=LOOPBACK).json["available"]


def test_missing_push_dependency_explains_unavailable_local_config(instance, monkeypatch):
    app, service = instance
    service.configure(local_port="18080")
    monkeypatch.setattr(service, "installed", False)
    client = app.test_client()
    result = client.get("/api/browser-push/config", base_url=LOOPBACK)
    assert result.json == {"ok":True, "available":False, "reason":"missing_dependency"}
    assert result.headers["Cache-Control"] == "no-store"
    assert request_device(app, "PUT", subscription("disabled"), ["daily_report"]).status_code == 403


def test_upgrade_without_python_push_preserves_keys_and_sends_encrypted_notifications(instance, monkeypatch):
    import http_ece
    import httpx
    from cryptography.hazmat.primitives.asymmetric import ec

    app, service = instance
    old_key = service.public_key()
    public, private = browser_push.generate_keys()
    device = subscription("upgraded")
    device["keys"]["p256dh"] = public
    assert request_device(app, "PUT", device, ["daily_report"]).json["registered"]
    with closing(service.db.connect()) as conn:
        signing_key = conn.execute("SELECT private_key FROM browser_push_keys WHERE scope=?", (service.scope,)).fetchone()[0]

    monkeypatch.setattr(browser_push.push_transport, "has_pywebpush", lambda: False)
    upgraded = create_app()
    config = upgraded.test_client().get("/api/browser-push/config", base_url=ORIGIN).json
    assert config["available"] and config["public_key"] == old_key
    assert request_device(upgraded, "POST", device).json["types"] == ["daily_report"]
    assert request_device(upgraded, "PUT", subscription("new-node-key"), ["article_reply"]).json["registered"]
    invalid = dict(device, keys=dict(device["keys"], p256dh=browser_push.b64(b"\x04" + bytes(64))))
    assert request_device(upgraded, "PUT", invalid, []).status_code == 400

    payload = {"tag": "a" * 32, "body": "测试消息"}
    def receive(url, *, headers, content, **options):
        assert url == device["endpoint"] and options == {"trust_env": False, "follow_redirects": False, "timeout": 8}
        assert headers["TTL"] == "50" and headers["Topic"] == payload["tag"] and headers["Content-Encoding"] == "aes128gcm"
        token, key = headers["Authorization"].removeprefix("vapid t=").split(", k=")
        assert key == old_key
        claims = token.split(".")[1]
        assert json.loads(base64.urlsafe_b64decode(claims + "=" * (-len(claims) % 4)))["sub"] == ORIGIN
        receiver = ec.derive_private_key(int.from_bytes(base64.urlsafe_b64decode(private + "="), "big"), ec.SECP256R1())
        assert json.loads(http_ece.decrypt(content, private_key=receiver, auth_secret=bytes(16), version="aes128gcm")) == payload
        return httpx.Response(410)

    monkeypatch.setattr(httpx, "post", receive)
    assert browser_push.deliver(device, payload, signing_key, ORIGIN + ":8443", 50) == 410
    assert browser_push.deliver(device, payload, "invalid-key", ORIGIN, 50) == 503


def test_missing_both_push_runtimes_leaves_reading_available(instance, monkeypatch):
    monkeypatch.setattr(browser_push.push_transport, "has_pywebpush", lambda: False)
    monkeypatch.setenv("NEWS_READER_NODE", "/missing/newsreader-node")
    client = create_app().test_client()
    assert client.get("/").status_code == 200
    assert client.get("/api/browser-push/config", base_url=ORIGIN).json["reason"] == "missing_dependency"


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


@pytest.mark.parametrize("origin", ["", ORIGIN, f"{ORIGIN}:8443"])
def test_real_encryption_disables_redirects_and_uses_stable_topic(monkeypatch, origin):
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
    assert browser_push.deliver(device, payload, private, origin, 50) == 302
    assert len(calls) == 1 and calls[0][0] == device["endpoint"]
    _, sent = calls[0]
    assert sent["allow_redirects"] is False
    assert sent["headers"]["Topic"] == payload["tag"] and sent["headers"]["content-encoding"] == "aes128gcm"
    assert b"Synthetic reply" not in sent["data"]
    token = sent["headers"]["Authorization"].split("t=", 1)[1].split(",", 1)[0]
    claims = token.split(".")[1]
    assert json.loads(base64.urlsafe_b64decode(claims + "=" * (-len(claims) % 4)))["sub"] == (ORIGIN if origin else "mailto:postmaster@localhost")


def test_vapid_signing_error_is_retryable_without_exposing_credentials(monkeypatch, caplog):
    from py_vapid import VapidException

    def cannot_sign(*args, **kwargs):
        raise VapidException("sensitive-test-value")

    monkeypatch.setattr("pywebpush.webpush", cannot_sign)
    result = browser_push.deliver(subscription("retry"), {"tag":"a" * 32}, "test", ORIGIN, 30)
    assert result == 503 and "sensitive-test-value" not in caplog.text


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
    device = subscription("expires", host="updates.push.services.mozilla.com")
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


@pytest.mark.parametrize("confirmation", [201, 410])
def test_fcm_gone_on_first_delivery_is_confirmed_after_restart(instance, confirmation):
    app, service = instance
    device = subscription("new-fcm")
    service.public_key()
    request_device(app, "PUT", device, ["daily_report"])
    with closing(service.db.connect()) as conn, conn:
        push_events.record(conn, "daily_report", "period", {"id":"first"}, "")
    since = time.time()
    service.sender = lambda *args: 410
    assert service.step()
    assert request_device(app, "POST", device).json["registered"]
    restarted = browser_push.Service(service.db, sender=lambda *args: confirmation)
    assert not restarted.step()
    with closing(service.db.connect()) as conn, conn:
        due, attempts = conn.execute("SELECT next_attempt,attempts FROM browser_push_deliveries").fetchone()
        assert since + 60 <= due <= time.time() + 60 and attempts == 1
        conn.execute("UPDATE browser_push_deliveries SET next_attempt=0")
    assert restarted.step()
    assert request_device(app, "POST", device).json["registered"] is (confirmation == 201)
    assert not restarted.step()
