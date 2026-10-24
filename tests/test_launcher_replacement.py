import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
from urllib.request import urlopen

import pytest

import launcher
import updater

COMMIT = "a" * 40


def wait_for(predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.05)
    raise AssertionError("isolated launcher did not reach the expected state")


@pytest.fixture
def project(tmp_path, monkeypatch):
    tmp_path = tmp_path / "project with spaces"
    tmp_path.mkdir()
    monkeypatch.setattr(launcher, "_git_head", lambda root: COMMIT)
    script = tmp_path / "app.py"
    script.write_text('''import json, os, signal
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
if os.environ.get('TEST_IGNORE_TERM'): signal.signal(signal.SIGTERM, signal.SIG_IGN)
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        broken = os.environ.get('TEST_BROKEN_FILE')
        data = {'ok': not (broken and Path(broken).exists()), 'commit': 'a' * 40,
                'instance_id': os.environ['NEWS_READER_INSTANCE_ID'], 'pid': os.getpid()}
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args): pass
HTTPServer((os.environ['NEWS_READER_HOST'], int(os.environ['NEWS_READER_PORT'])), Handler).serve_forever()
''')
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    running = []

    def start(*, replace=True, env=None, timeout=8, host="127.0.0.1"):
        opened, result = [], []
        supervisor = launcher.Supervisor(
            root=tmp_path, host=host, port=port,
            env={"NEWS_READER_UPDATER_DIR": str(tmp_path / "update"), **(env or {})},
            opener=opened.append, health_timeout=3,
            replace_existing=replace, replacement_timeout=timeout,
        )
        thread = threading.Thread(target=lambda: result.append(supervisor.run()), daemon=True)
        running.append((supervisor, thread))
        thread.start()
        return supervisor, thread, opened, result

    yield tmp_path, port, start
    for supervisor, thread in running:
        supervisor.stop_requested = True
        if supervisor.update_process and supervisor.update_process.poll() is None:
            supervisor.update_process.terminate()
            supervisor.update_process.wait(timeout=3)
        thread.join(timeout=12)
        assert not thread.is_alive()
        assert all(child.poll() is not None for child in supervisor.owned_processes)


@pytest.mark.parametrize("unhealthy", [False, True])
def test_replacement_stops_the_old_service_and_owns_a_fresh_instance(project, unhealthy):
    root, _, start = project
    broken = root / "broken"
    old, old_thread, old_opened, old_result = start(env={"TEST_BROKEN_FILE": str(broken)})
    wait_for(lambda: bool(old_opened))
    old_child, old_instance = old.child, old.child_instance_id
    rejected = launcher._json_request(old.socket_path, {"action": "shutdown", "token": "wrong"})
    assert rejected == {"ok": False, "error": "unauthorized"}
    assert not old.stop_requested
    if unhealthy:
        broken.touch()
    new, _, new_opened, _ = start()
    wait_for(lambda: bool(new_opened))
    old_thread.join(timeout=3)
    assert old_result == [0]
    assert old_child.poll() is not None
    assert new.child.poll() is None
    assert new.child_instance_id != old_instance
    assert json.loads(new.lock_path.read_text())["token"] == new.token
    assert launcher._json_request(new.socket_path, {"action": "status"})["healthy"]


def test_replacement_waits_for_an_active_update(project):
    root, _, start = project
    old, _, opened, _ = start()
    wait_for(lambda: bool(opened))
    old_child = old.child
    release = root / "release-update"
    old.update_process = subprocess.Popen([sys.executable, "-c",
        "from pathlib import Path; import sys,time\nwhile not Path(sys.argv[1]).exists(): time.sleep(.05)", str(release)])
    old.update_run_id = "isolated-update"
    state = root / "update" / "news-reader-updater-state.json"
    state.parent.mkdir()
    state.write_text(json.dumps({"run_id": old.update_run_id, "status": "healthy"}))
    new, _, new_opened, _ = start()
    wait_for(lambda: old.stop_requested)
    assert old_child.poll() is None
    assert not new.owned_processes
    denied = launcher._json_request(old.socket_path, {"action": "start_update", "token": old.token})
    assert denied["error"] == "launcher_stopping"
    release.touch()
    wait_for(lambda: bool(new_opened))
    assert old_child.poll() is not None


def test_simultaneous_replacements_are_serialized(project):
    _, _, start = project
    old, _, opened, _ = start()
    wait_for(lambda: bool(opened))
    replacements = [start(), start()]
    wait_for(lambda: all(opened for _, _, opened, _ in replacements))
    wait_for(lambda: sum(supervisor.child is not None and supervisor.child.poll() is None
                        for supervisor, _, _, _ in replacements) == 1)
    assert old.stop_requested
    stopped = [result for supervisor, _, _, result in replacements if supervisor.stop_requested]
    wait_for(lambda: stopped == [[0]])


def test_failed_takeover_preserves_the_old_socket_and_service(project):
    _, _, start = project
    old, _, opened, _ = start()
    wait_for(lambda: bool(opened))
    owner = json.loads(old.lock_path.read_text())
    old.lock_path.write_text(json.dumps({**owner, "token": "stale-token"}))
    new, thread, _, result = start(timeout=.3)
    thread.join(timeout=5)
    assert result == [1]
    assert new.owned_processes == []
    assert not old.stop_requested
    assert old.child.poll() is None
    assert launcher._json_request(old.socket_path, {"action": "status"})["healthy"]


@pytest.mark.skipif(not shutil.which("lsof"), reason="legacy ownership detection requires lsof")
def test_replacement_of_a_legacy_launcher_uses_its_project_lock(project):
    root, port, start = project
    probe = launcher.Supervisor(root=root, host="127.0.0.1", port=port, env={})
    script = root / "launcher.py"
    script.write_text('''import fcntl, os, signal, subprocess, sys, time
from pathlib import Path
runtime = Path(os.environ['TEST_RUNTIME'])
runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
lock = (runtime / 'supervisor.lock').open('a+')
fcntl.flock(lock, fcntl.LOCK_EX)
child = subprocess.Popen([sys.executable, str(Path(__file__).with_name('app.py'))], env=os.environ)
def stop(*args):
    child.terminate()
    child.wait(timeout=5)
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
while True: time.sleep(.05)
''')
    process = subprocess.Popen([sys.executable, str(script)], cwd=root, env={**os.environ,
        "TEST_RUNTIME": str(probe.runtime_dir), "NEWS_READER_HOST": "127.0.0.1",
        "NEWS_READER_PORT": str(port), "NEWS_READER_INSTANCE_ID": "legacy-instance"})
    try:
        wait_for(lambda: probe.lock_path.exists() and probe._legacy_owner() is not None)
        new, _, opened, _ = start()
        wait_for(lambda: bool(opened))
        assert process.wait(timeout=3) == 0
        assert new.child.poll() is None
        assert new.child_instance_id != "legacy-instance"
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def test_unrelated_process_holding_a_lock_is_not_signalled(project, monkeypatch):
    _, _, start = project
    old, _, opened, _ = start()
    wait_for(lambda: bool(opened))
    old.lock_path.write_text("")
    # A PID belonging to this test runner is not a separately owned launcher.
    monkeypatch.setattr(launcher.os, "kill", lambda *args: pytest.fail("must not signal an unrelated process"))
    new, thread, _, result = start(timeout=.3)
    thread.join(timeout=5)
    assert result == [1]
    assert not new.owned_processes
    assert old.child.poll() is None


@pytest.mark.skipif(not shutil.which("lsof"), reason="leftover ownership detection requires lsof")
@pytest.mark.parametrize("ignore_term", [False, True])
def test_replaces_a_verified_leftover_service(project, ignore_term):
    root, port, start = project
    process = subprocess.Popen([sys.executable, str(root / "app.py")], cwd=root, env={**os.environ,
        "NEWS_READER_HOST": "127.0.0.1", "NEWS_READER_PORT": str(port),
        "NEWS_READER_INSTANCE_ID": "leftover", "TEST_IGNORE_TERM": "1" if ignore_term else ""})
    try:
        def ready():
            try:
                with urlopen(f"http://127.0.0.1:{port}/api/version", timeout=.2) as response:
                    return json.load(response)["instance_id"] == "leftover"
            except OSError:
                return False
        wait_for(ready)
        new, _, opened, _ = start()
        wait_for(lambda: bool(opened))
        assert process.wait(timeout=3) == (-9 if ignore_term else -15)
        assert new.child.poll() is None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)


def test_does_not_stop_another_projects_port_occupant(project):
    root, port, start = project
    other = root / "other-project"
    other.mkdir()
    shutil.copyfile(root / "app.py", other / "app.py")
    process = subprocess.Popen([sys.executable, str(other / "app.py")], cwd=other, env={**os.environ,
        "NEWS_READER_HOST": "127.0.0.1", "NEWS_READER_PORT": str(port), "NEWS_READER_INSTANCE_ID": "unrelated"})
    try:
        def ready():
            try:
                with urlopen(f"http://127.0.0.1:{port}/api/version", timeout=.2) as response:
                    return json.load(response)["instance_id"] == "unrelated"
            except OSError:
                return False
        wait_for(ready)
        _, thread, opened, result = start()
        thread.join(timeout=8)
        assert result == [1]
        assert opened == []
        assert process.poll() is None
        assert ready()
    finally:
        process.terminate()
        process.wait(timeout=3)


def test_leftover_identity_is_rechecked_before_signalling(tmp_path, monkeypatch):
    supervisor = launcher.Supervisor(root=tmp_path, env={})
    monkeypatch.setattr(launcher.subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, stdout="12345\n"))
    snapshots = iter(["original process", "replacement process"])
    monkeypatch.setattr(supervisor, "_project_process", lambda *args: next(snapshots))
    monkeypatch.setattr(launcher.os, "kill", lambda *args: pytest.fail("must not signal a reused PID"))
    supervisor._stop_leftover_service()


def test_local_health_checks_bypass_proxy_settings(project, monkeypatch):
    _, _, start = project
    for key in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
        monkeypatch.setenv(key, "http://127.0.0.1:1")
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.setenv("NO_PROXY", "")
    supervisor, _, opened, _ = start(host="localhost")
    wait_for(lambda: bool(opened))
    assert launcher._json_request(supervisor.socket_path, {"action": "status"})["healthy"]
    assert updater._wait_healthy(supervisor.health_url, COMMIT, supervisor.child, timeout=1)
