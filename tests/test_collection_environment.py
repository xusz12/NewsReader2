import json
import subprocess
import sqlite3
from contextlib import closing

import pytest

import collection_environment as environment


@pytest.fixture(autouse=True)
def isolate_cli(monkeypatch):
    pass  # CLI processes are mocked; no host installation is consulted.
    monkeypatch.setattr(environment, "run_command", lambda *a, **kw: pytest.fail("Unexpected CLI execution"))


def cli(monkeypatch, payload, *, returncode=0):
    calls = []

    def run(command, *, timeout):
        calls.append(command)
        if command[1:] == ["--version"]:
            return subprocess.CompletedProcess(command, 0, "2.0.3\n", "")
        if command[1:] == ["--nr-check"]:
            manifest = json.loads((environment.Path(environment.__file__).parent / "collection/opencli-manifest.json").read_text())
            return subprocess.CompletedProcess(command, 0, json.dumps({"ok": True, "data": {"version": manifest["version"], "adapter_commit": manifest["adapters"]["commit"], "isolated": True, "commands": [f"{r['site']} {r['name']}" for r in manifest["commands"]]}}), "")
        if isinstance(payload, Exception):
            raise payload
        return subprocess.CompletedProcess(command, returncode, json.dumps(payload), "private stderr")

    monkeypatch.setattr(environment, "run_command", run)
    return calls


def rows(result):
    return {row["id"]: row for row in result["checks"]}


@pytest.mark.parametrize("target", ["environment", "x"])
def test_missing_runtime_does_not_probe_or_claim_login_failure(monkeypatch, target):
    monkeypatch.setattr(environment, "run_command", lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError()))
    result = rows(environment.check(target))
    assert result["runtime"]["status"] == "failed"
    assert result["x" if target == "x" else "chrome"]["status"] == "unknown"


@pytest.mark.parametrize("wrapped", [True, False])
def test_browser_report_checks_live_connectivity_and_redacts_private_fields(monkeypatch, wrapped):
    report = {"extensionConnected": True, "extensionVersion": "2.0.3", "connectivity": {"ok": True},
              "profiles": [{"name": "private profile", "token": "private-cookie"}], "issues": ["private text"]}
    calls = cli(monkeypatch, {"ok": True, "data": report} if wrapped else report)
    result = environment.check()
    assert all(row["status"] == "success" for row in result["checks"])
    assert "private" not in json.dumps(result)
    assert calls == [["opencli", "--version"], ["opencli", "--nr-check"], ["opencli", "doctor"]]
    assert result["checked_at"].endswith("+00:00")


@pytest.mark.parametrize("overrides, expected", [
    ({"extensionConnected": False, "connectivity": {"ok": False}}, "failed"),
    ({"connectivity": {"ok": False}}, "failed"),
    ({"daemonStale": True}, "unknown"),
    ({"extensionVersion": None}, "unknown"),
    ({"issues": ["CLI version incompatible with extension: private detail"]}, "failed"),
])
def test_browser_does_not_treat_partial_or_incompatible_connection_as_ready(monkeypatch, overrides, expected):
    cli(monkeypatch, {"ok": True, "data": {"extensionConnected": True, "extensionVersion": "2.0.3",
        "connectivity": {"ok": True}, **overrides}})
    result = rows(environment.check())
    assert result["bridge"]["status"] == expected
    if overrides.get("extensionConnected") is False:
        assert result["chrome"]["status"] == "unknown"
        assert "未安装" not in result["chrome"]["message"]


@pytest.mark.parametrize("payload", [{}, {"ok": True}, {"ok": True, "data": {"extensionConnected": "true"}},
    {"ok": False, "error": {"code": "AUTH_REQUIRED", "message": "secret-cookie"}}])
def test_invalid_doctor_response_is_unverified_not_success(monkeypatch, payload):
    cli(monkeypatch, payload)
    result = rows(environment.check())
    assert result["bridge"]["status"] == "unknown"
    assert "secret" not in json.dumps(result)


@pytest.mark.parametrize("payload", [
    {"ok": True, "data": {"username": "private_user", "cookies": "private-cookie"}},
    [{"username": "private_user"}],
])
def test_x_identity_is_verified_but_never_returned(monkeypatch, payload):
    calls = cli(monkeypatch, payload)
    result = environment.check("x")
    assert rows(result)["x"]["status"] == "success"
    assert "private_user" not in json.dumps(result) and "private-cookie" not in json.dumps(result)
    assert calls[-1] == ["opencli", "twitter", "whoami", "-f", "json", "--window", "background"]


@pytest.mark.parametrize("payload, expected", [
    ({"ok": False, "error": {"code": "AUTH_REQUIRED", "message": "private-cookie"}}, "failed"),
    ({"ok": False, "error": {"code": "BROWSER_CONNECT"}}, "unknown"),
    (subprocess.TimeoutExpired("opencli", 45), "unknown"),
    ({"ok": True, "data": {"username": ""}}, "unknown"),
    ({"ok": True, "data": {"username": "some_user", "logged_in": False}}, "failed"),
    ({"ok": True, "data": {"username": "some_user", "logged_in": "true"}}, "unknown"),
    ({"ok": True, "data": {"username": "some_user", "site": "other"}}, "unknown"),
    ({"ok": True, "data": [{"username": "a"}, {"username": "b"}]}, "unknown"),
])
def test_x_missing_login_is_distinct_from_unconfirmed(monkeypatch, payload, expected):
    cli(monkeypatch, payload)
    result = environment.check("x")
    assert rows(result)["x"]["status"] == expected
    assert "private-cookie" not in json.dumps(result)


def test_nonzero_exit_with_success_json_is_not_accepted(monkeypatch):
    cli(monkeypatch, {"ok": True, "data": {"username": "private_user"}}, returncode=1)
    assert rows(environment.check("x"))["x"]["status"] == "unknown"


def test_broken_runtime_does_not_start_browser(monkeypatch):
    calls = []
    def fail(command, **kwargs):
        calls.append(command)
        raise FileNotFoundError("private path")
    monkeypatch.setattr(environment, "run_command", fail)
    result = rows(environment.check())
    assert len(calls) == 1
    assert result["runtime"]["status"] == "failed" and result["bridge"]["status"] == "unknown"


def test_overlapping_checks_are_rejected_and_lock_is_released(monkeypatch):
    cli(monkeypatch, subprocess.TimeoutExpired("opencli", 20))
    with environment._lock:
        with pytest.raises(environment.Busy):
            environment.check()
    assert rows(environment.check())["chrome"]["status"] == "unknown"
    assert not environment._lock.locked()


def test_environment_api_restricts_commands_and_does_not_queue_collection(monkeypatch, tmp_path):
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(tmp_path / "settings.json"))
    import app
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "news.sqlite3")
    with app.db_conn() as conn:
        environment.initialize(conn)
    monkeypatch.setattr(app.COLLECTION_SERVICE, "request", lambda: pytest.fail("must not collect"))
    calls = []
    monkeypatch.setattr(environment, "check", lambda target: calls.append(target) or {
        "checks": [{"id": "x" if target == "x" else "runtime", "status": "success", "message": "检查通过"}],
        "checked_at": "2026-11-12T01:00:00+00:00"})
    client = app.app.test_client()
    endpoint = "/api/collection/environment/check"
    assert client.get('/api/collection/environment').json == {'ok': True, 'checks': []}
    for body in ({"target": "login"}, {"target": ["x"]}, {"target": {"command": "bad"}},
                 {"target": "x", "command": "bad"}, [], None):
        response = client.post(endpoint, data=json.dumps(body), content_type="application/json")
        assert response.status_code == 400
    assert not calls
    assert client.post(endpoint).status_code == 200
    assert client.post(endpoint, json={"target": "x"}).status_code == 200
    assert {row['id'] for row in client.get('/api/collection/environment').json['checks']} == {'runtime', 'x'}
    assert calls == ["environment", "x"]
    def busy(_):
        raise environment.Busy()
    monkeypatch.setattr(environment, "check", busy)
    assert client.post(endpoint).status_code == 409


def test_latest_checks_survive_reopening_and_retain_stale_account_results(tmp_path):
    path = tmp_path / 'checks.sqlite3'
    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn
    account = {'checked_at': '2026-11-12T01:00:00+00:00', 'checks': [
        {'id': 'x', 'status': 'success', 'message': '已验证 X 登录'}]}
    browser = {'checked_at': '2026-11-12T02:00:00+00:00', 'checks': [
        {'id': 'runtime', 'status': 'success', 'message': 'opencli 已就绪'},
        {'id': 'bridge', 'status': 'failed', 'message': '扩展未连接'}]}
    with closing(connect()) as conn:
        environment.initialize(conn)
        environment.remember(conn, 'x', account)
        result = environment.remember(conn, 'environment', browser)
    with closing(connect()) as conn:
        environment.initialize(conn)
        assert environment.latest(conn) == result
        records = {row['id']: row for row in result}
        assert records['x']['stale'] and records['x']['checked_at'] == account['checked_at']
        assert records['bridge']['status'] == 'failed'
        # An older delayed login result cannot undo the newer browser check.
        environment.remember(conn, 'x', account)
        assert next(row for row in environment.latest(conn) if row['id'] == 'x')['stale']
        refreshed = {**account, 'checked_at': '2026-11-12T03:00:00+00:00'}
        environment.remember(conn, 'x', refreshed)
        environment.remember(conn, 'environment', browser)
        assert not next(row for row in environment.latest(conn) if row['id'] == 'x')['stale']
        environment.remember(conn, 'environment', {**browser, 'checked_at': '2026-11-12T04:00:00+00:00',
            'checks': [{'id': 'bridge', 'status': 'success', 'message': '扩展已连接'}]})
        assert next(row for row in environment.latest(conn) if row['id'] == 'bridge')['status'] == 'success'
        assert len(environment.latest(conn)) == 3
