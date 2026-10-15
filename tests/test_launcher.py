from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
from urllib.request import Request, urlopen

import pytest

import launcher

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, text=True, capture_output=True, check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def temp_update_repo(tmp_path: Path) -> tuple[Path, str, str]:
    """Build a complete throwaway project and a local tagged update remote."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True, check=True)
    source = tmp_path / "source"
    shutil.copytree(
        PROJECT_ROOT, source,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".pytest_cache"),
    )
    manifest_path = source / "version.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["repository"] = str(remote)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    git("init", "-b", "main", cwd=source)
    git("config", "user.name", "Isolated Test", cwd=source)
    git("config", "user.email", "test@example.invalid", cwd=source)
    git("add", "-A", cwd=source)
    git("commit", "-m", "test: 创建隔离基线", cwd=source)
    old_commit = git("rev-parse", "HEAD", cwd=source)
    git("remote", "add", "origin", str(remote), cwd=source)
    git("push", "-u", "origin", "main", cwd=source)
    target = tmp_path / "target"
    git("clone", "-b", "main", str(remote), str(target))

    manifest["version"] = "v2.1.4.7"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    git("add", "version.json", cwd=source)
    git("commit", "-m", "test: 发布隔离更新", cwd=source)
    new_commit = git("rev-parse", "HEAD", cwd=source)
    git("tag", "v2.1.4.7", cwd=source)
    git("push", "origin", "main", "v2.1.4.7", cwd=source)
    return target, old_commit, new_commit


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def make_app_wrapper(path: Path) -> None:
    path.write_text(
        """import json, os, sys
sys.path.insert(0, os.environ['TEST_PROJECT_ROOT'])
import app

token = os.environ['TEST_UPDATE_TOKEN']
target = json.loads(os.environ['TEST_UPDATE_TARGET'])
app.UPDATE_TOKENS[token] = {
    'expires_at': app.time.time() + 3600,
    'payload': {
        'available': True,
        'current_commit': app._current_commit(),
        'latest': target,
    },
}
fail_commit = os.environ.get('TEST_FAIL_HEALTH_COMMIT')
if fail_commit and app._current_commit() == fail_commit:
    original = app.api_version
    def wrong_commit_health():
        response = original().get_json()
        response['commit'] = '0' * 40
        return app.jsonify(response)
    app.app.view_functions['api_version'] = wrong_commit_health

app.app.run(host=os.environ['NEWS_READER_HOST'], port=int(os.environ['NEWS_READER_PORT']),
            debug=False, use_reloader=False, threaded=True)
""",
        encoding="utf-8",
    )


def post_json(url: str, payload: dict) -> tuple[int, dict]:
    request = Request(url, data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
    try:
        response = urlopen(request, timeout=3)
    except Exception as exc:
        response = exc
    return response.code, json.loads(response.read().decode())


def get_json(url: str) -> dict:
    with urlopen(url, timeout=2) as response:
        return json.loads(response.read().decode())


def wait_until(predicate, timeout: float = 30.0):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except Exception as exc:  # transient connection failures during restart
            last_error = exc
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for condition: {last_error}")


def test_launcher_config_handles_loopback_and_wildcard_hosts():
    assert launcher.resolve_config({}) == (
        "127.0.0.1", 8080, "http://127.0.0.1:8080/api/version", "http://127.0.0.1:8080",
    )
    assert launcher.resolve_config({"NEWS_READER_HOST": "localhost", "NEWS_READER_PORT": "8765"}) == (
        "localhost", 8765, "http://localhost:8765/api/version", "http://localhost:8765",
    )
    assert launcher.resolve_config({"NEWS_READER_HOST": "0.0.0.0", "NEWS_READER_PORT": "8765"})[3] == "http://127.0.0.1:8765"
    assert launcher.resolve_config({"NEWS_READER_HOST": "::1", "NEWS_READER_PORT": "8765"})[2:] == (
        "http://[::1]:8765/api/version", "http://[::1]:8765",
    )
    assert launcher.resolve_config({"NEWS_READER_HOST": "::", "NEWS_READER_PORT": "8765"})[3] == "http://[::1]:8765"
    with pytest.raises(ValueError, match="NEWS_READER_PORT"):
        launcher.resolve_config({"NEWS_READER_PORT": "70000"})
    with pytest.raises(ValueError, match="NEWS_READER_HOST"):
        launcher.resolve_config({"NEWS_READER_HOST": "http://example.invalid"})


@pytest.mark.parametrize(
    ("fail_new_health", "host"),
    [(False, "localhost"), (True, "127.0.0.1")],
    ids=["update-success-custom-loopback", "rollback-old-service"],
)
def test_supervisor_update_handoff_and_exit_cleanup(tmp_path: Path, fail_new_health: bool, host: str):
    target, old_commit, new_commit = temp_update_repo(tmp_path)
    token = "confirmation-token-for-isolated-test"
    latest = {"tag": "v2.1.4.7", "version": "v2.1.4.7", "commit": new_commit}
    port = free_port()
    runtime_dir = tmp_path / "runtime"
    env = {
        "NEWS_READER_HOST": host,
        "NEWS_READER_PORT": str(port),
        "NEWS_READER_DB_PATH": str(tmp_path / "data" / "news.sqlite3"),
        "NEWS_READER_AGENT_DB_PATH": str(tmp_path / "data" / "agent.sqlite3"),
        "NEWS_READER_APP_SETTINGS_PATH": str(tmp_path / "data" / "settings.json"),
        "NEWS_READER_DAILY_NEWS_DIR": str(tmp_path / "data" / "daily"),
        "NEWS_READER_DAILY_BRIEFING_DIR": str(tmp_path / "data" / "briefings"),
        "NEWS_READER_MEDIA_CACHE_DIR": str(tmp_path / "data" / "media"),
        "NEWS_READER_AGENT_RUNTIME_DIR": str(tmp_path / "runtime" / "agent"),
        "NEWS_READER_UPDATER_DIR": str(runtime_dir),
        "TEST_PROJECT_ROOT": str(target),
        "TEST_UPDATE_TOKEN": token,
        "TEST_UPDATE_TARGET": json.dumps(latest),
    }
    if fail_new_health:
        env["TEST_FAIL_HEALTH_COMMIT"] = new_commit
    wrapper = tmp_path / "run_test_app.py"
    make_app_wrapper(wrapper)
    opened: list[str] = []
    supervisor = launcher.Supervisor(
        root=target, host=host, port=port, env=env,
        opener=opened.append,
        service_command=lambda child_env: [sys.executable, str(wrapper)],
        health_timeout=3.0,
    )
    result: list[int] = []
    thread = threading.Thread(target=lambda: result.append(supervisor.run()), daemon=True)
    thread.start()
    wait_until(lambda: bool(opened))
    browser_host = host
    assert opened == [f"http://{browser_host}:{port}"]
    base_url = opened[0]
    assert get_json(base_url + "/api/version")["commit"] == old_commit

    # A second launch talks to the lock owner's private socket and reuses that service.
    second_opened: list[str] = []
    second = launcher.Supervisor(
        root=target, host="127.0.0.1", port=port, env=env,
        opener=second_opened.append,
        service_command=lambda child_env: pytest.fail("duplicate launch must not spawn a service"),
    )
    assert second.run() == 0
    assert second_opened == [base_url]
    assert second.owned_processes == []

    status, payload = post_json(base_url + "/api/update/apply", {
        "check_token": token, **latest,
    })
    assert status == 202, payload
    assert payload["status"] == "updating"

    def completed_state():
        state_path = runtime_dir / "news-reader-updater-state.json"
        if not state_path.exists():
            return None
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("status") in {"healthy", "rolled_back", "rollback_failed", "failed"}:
            return state
        return None

    state = wait_until(completed_state, timeout=45)
    active = get_json(base_url + "/api/version")
    if fail_new_health:
        assert state["status"] == "rolled_back", state
        assert active["commit"] == old_commit
        assert git("-C", str(target), "rev-parse", "HEAD") == old_commit
    else:
        assert state["status"] == "healthy", state
        assert active["commit"] == new_commit
        assert git("-C", str(target), "rev-parse", "HEAD") == new_commit
    assert active["instance_id"]

    supervisor.stop_requested = True
    thread.join(timeout=12)
    assert not thread.is_alive(), "supervisor did not stop after cleanup request"
    assert result == [0]
    assert all(process.poll() is not None for process in supervisor.owned_processes)
    with pytest.raises(Exception):
        urlopen(base_url + "/api/version", timeout=0.3)


def test_existing_matching_service_is_not_mistaken_for_launcher_child(tmp_path: Path):
    """A stale listener cannot pass health without the per-process launch marker."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    port = free_port()
    fake_commit = "a" * 40

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            payload = {"ok": True, "commit": fake_commit, "instance_id": "another-process"}
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    stale = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    stale_thread = threading.Thread(target=stale.serve_forever, daemon=True)
    stale_thread.start()
    sleeper = tmp_path / "sleeper.py"
    sleeper.write_text("import time; time.sleep(30)\n", encoding="utf-8")
    git("init", "-b", "main", cwd=tmp_path)
    git("config", "user.name", "Isolated Test", cwd=tmp_path)
    git("config", "user.email", "test@example.invalid", cwd=tmp_path)
    git("add", "sleeper.py", cwd=tmp_path)
    git("commit", "-m", "test: 创建隔离服务仓库", cwd=tmp_path)
    fake_commit = git("rev-parse", "HEAD", cwd=tmp_path)
    opened: list[str] = []
    supervisor = launcher.Supervisor(
        root=tmp_path, host="127.0.0.1", port=port, env={"NEWS_READER_PORT": str(port)},
        opener=opened.append,
        service_command=lambda _env: [sys.executable, str(sleeper)],
        health_timeout=0.35,
    )
    try:
        assert supervisor.run() == 1
        assert opened == []
        assert all(process.poll() is not None for process in supervisor.owned_processes)
    finally:
        stale.shutdown()
        stale.server_close()
        stale_thread.join(timeout=2)


def test_supervised_stop_uses_owned_process_after_fetch_race(tmp_path: Path):
    """A stale update stop request cannot signal a different process after fetch."""
    target, old_commit, new_commit = temp_update_repo(tmp_path)
    token = "confirmation-token-for-race-test"
    latest = {"tag": "v2.1.4.7", "version": "v2.1.4.7", "commit": new_commit}
    port = free_port()
    runtime_dir = tmp_path / "runtime"
    fetch_entered = tmp_path / "fetch-entered"
    fetch_release = tmp_path / "fetch-release"
    real_git = shutil.which("git")
    assert real_git
    git_bin = tmp_path / "git-bin"
    git_bin.mkdir()
    git_wrapper = git_bin / "git"
    git_wrapper.write_text(
        """#!/usr/bin/env python3
import os
from pathlib import Path
import subprocess
import sys
import time

if len(sys.argv) > 3 and sys.argv[3] == "fetch":
    Path(os.environ["TEST_FETCH_ENTERED"]).write_text("ready")
    while not Path(os.environ["TEST_FETCH_RELEASE"]).exists():
        time.sleep(0.02)
os.execv(os.environ["TEST_REAL_GIT"], [os.environ["TEST_REAL_GIT"], *sys.argv[1:]])
""",
        encoding="utf-8",
    )
    git_wrapper.chmod(0o755)
    env = {
        "NEWS_READER_HOST": "127.0.0.1",
        "NEWS_READER_PORT": str(port),
        "NEWS_READER_DB_PATH": str(tmp_path / "data" / "news.sqlite3"),
        "NEWS_READER_AGENT_DB_PATH": str(tmp_path / "data" / "agent.sqlite3"),
        "NEWS_READER_APP_SETTINGS_PATH": str(tmp_path / "data" / "settings.json"),
        "NEWS_READER_DAILY_NEWS_DIR": str(tmp_path / "data" / "daily"),
        "NEWS_READER_DAILY_BRIEFING_DIR": str(tmp_path / "data" / "briefings"),
        "NEWS_READER_MEDIA_CACHE_DIR": str(tmp_path / "data" / "media"),
        "NEWS_READER_AGENT_RUNTIME_DIR": str(tmp_path / "runtime" / "agent"),
        "NEWS_READER_UPDATER_DIR": str(runtime_dir),
        "TEST_PROJECT_ROOT": str(target),
        "TEST_UPDATE_TOKEN": token,
        "TEST_UPDATE_TARGET": json.dumps(latest),
        "TEST_FETCH_ENTERED": str(fetch_entered),
        "TEST_FETCH_RELEASE": str(fetch_release),
        "TEST_REAL_GIT": real_git,
    }
    env["PATH"] = f"{git_bin}{os.pathsep}{os.environ['PATH']}"
    wrapper = tmp_path / "run_test_app.py"
    make_app_wrapper(wrapper)
    opened: list[str] = []
    supervisor = launcher.Supervisor(
        root=target, host="127.0.0.1", port=port, env=env,
        opener=opened.append,
        service_command=lambda child_env: [sys.executable, str(wrapper)],
        health_timeout=10.0,
    )
    result: list[int] = []
    sentinel = None
    thread = threading.Thread(target=lambda: result.append(supervisor.run()), daemon=True)
    thread.start()
    try:
        wait_until(lambda: bool(opened))
        base_url = opened[0]
        old_instance_id = get_json(base_url + "/api/version")["instance_id"]
        status, payload = post_json(base_url + "/api/update/apply", {
            "check_token": token, **latest,
        })
        assert status == 202, payload
        assert payload["status"] == "updating"
        wait_until(fetch_entered.exists, timeout=10)
        wait_until(lambda: supervisor.update_process is not None)
        assert supervisor.child is not None

        sentinel = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        bad_stop = launcher._json_request(supervisor.socket_path, {
            "action": "stop_service",
            "token": supervisor.token,
            "pid": supervisor.update_process.pid,
            "run_id": supervisor.update_run_id,
            "expected_commit": old_commit,
            "expected_instance_id": "stale-" + old_instance_id,
        })
        assert bad_stop == {"ok": False, "error": "service_identity_mismatch"}
        assert supervisor.child.poll() is None
        assert sentinel.poll() is None

        # The owned Popen exits while fetch is blocked. The later valid request
        # must observe that state and avoid signalling any recycled PID.
        supervisor._stop_owned_process(supervisor.child)
        assert supervisor.child.poll() is not None
        fetch_release.write_text("release")

        def completed_state():
            state_path = runtime_dir / "news-reader-updater-state.json"
            if not state_path.exists():
                return None
            state = json.loads(state_path.read_text(encoding="utf-8"))
            return state if state.get("status") in {"healthy", "failed", "rollback_failed"} else None

        state = wait_until(completed_state, timeout=45)
        assert state["status"] == "healthy", state
        assert get_json(base_url + "/api/version")["commit"] == new_commit
        assert sentinel.poll() is None
    finally:
        fetch_release.touch()
        if thread.is_alive():
            supervisor.stop_requested = True
            thread.join(timeout=15)
        if sentinel is not None and sentinel.poll() is None:
            sentinel.terminate()
            sentinel.wait(timeout=5)
    assert not thread.is_alive()
    assert result == [0]


def _run_startup_script_with_fake_python(
    tmp_path: Path,
    *,
    preflight_status: int,
    launcher_status: int,
    tailscale_output: str | None = None,
    tailscale_status: int = 0,
    explicit_host: str | None = None,
):
    fake_python = tmp_path / "fake-python"
    captured_host = tmp_path / "captured-host"
    captured_port = tmp_path / "captured-port"
    fake_python.write_text(
        """#!/bin/sh
if [ \"$1\" = \"-\" ]; then
    cat >/dev/null
    exit %d
fi
printf '%%s' \"${NEWS_READER_HOST-}\" > \"$TEST_CAPTURE_HOST\"
printf '%%s' \"${NEWS_READER_PORT-}\" > \"$TEST_CAPTURE_PORT\"
exit %d
""" % (preflight_status, launcher_status),
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = os.environ.copy()
    env.update({
        "NEWS_READER_PYTHON": str(fake_python),
        "NEWS_READER_NO_ALERT": "1",
        "TEST_CAPTURE_HOST": str(captured_host),
        "TEST_CAPTURE_PORT": str(captured_port),
    })
    if explicit_host is not None:
        env["NEWS_READER_HOST"] = explicit_host
    else:
        env.pop("NEWS_READER_HOST", None)
    if tailscale_output is not None or tailscale_status != 0:
        fake_tailscale = tmp_path / "tailscale"
        fake_tailscale.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"${TEST_TAILSCALE_OUTPUT-}\"\n"
            f"exit {tailscale_status}\n",
            encoding="utf-8",
        )
        fake_tailscale.chmod(0o755)
        env["TEST_TAILSCALE_OUTPUT"] = tailscale_output or ""
        env["PATH"] = f"{tmp_path}{os.pathsep}{env['PATH']}"
    else:
        env["PATH"] = os.pathsep.join(
            part for part in env["PATH"].split(os.pathsep) if Path(part) != tmp_path
        )
    result = subprocess.run(
        ["zsh", str(PROJECT_ROOT / "启动NewsReader.command")],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    result.captured_host = captured_host.read_text() if captured_host.exists() else None
    result.captured_port = captured_port.read_text() if captured_port.exists() else None
    return result


def test_startup_script_reports_missing_dependencies_before_health_check(tmp_path: Path):
    result = _run_startup_script_with_fake_python(tmp_path, preflight_status=1, launcher_status=99)
    output = result.stdout + result.stderr
    assert result.returncode == 1
    assert "pip install -r" in output
    assert "health check" not in output
    assert "read-only variable: status" not in output


def test_startup_script_uses_non_reserved_exit_code_variable(tmp_path: Path):
    result = _run_startup_script_with_fake_python(tmp_path, preflight_status=0, launcher_status=7)
    output = result.stdout + result.stderr
    assert result.returncode == 7
    assert "退出码 7" in output
    assert "read-only variable: status" not in output


def test_startup_script_uses_tailscale_ipv4_when_host_is_not_explicit(tmp_path: Path):
    result = _run_startup_script_with_fake_python(
        tmp_path, preflight_status=0, launcher_status=0, tailscale_output="100.64.12.34",
    )
    assert result.returncode == 0
    assert result.captured_host == "100.64.12.34"
    assert result.captured_port == ""


def test_startup_script_falls_back_to_loopback_when_tailscale_is_unavailable(tmp_path: Path):
    result = _run_startup_script_with_fake_python(
        tmp_path, preflight_status=0, launcher_status=0, tailscale_output="not-an-ip",
    )
    assert result.returncode == 0
    assert result.captured_host is None or result.captured_host == ""


def test_startup_script_ignores_tailscale_output_when_command_fails(tmp_path: Path):
    result = _run_startup_script_with_fake_python(
        tmp_path,
        preflight_status=0,
        launcher_status=0,
        tailscale_output="100.64.12.34",
        tailscale_status=7,
    )
    assert result.returncode == 0
    assert result.captured_host is None or result.captured_host == ""


def test_startup_script_preserves_explicit_host_over_tailscale(tmp_path: Path):
    result = _run_startup_script_with_fake_python(
        tmp_path,
        preflight_status=0,
        launcher_status=0,
        tailscale_output="100.64.12.34",
        explicit_host="127.0.0.1",
    )
    assert result.returncode == 0
    assert result.captured_host == "127.0.0.1"


def _make_fake_python(path: Path, *, preflight_status: int, marker: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-\" ]; then\n"
        "    cat >/dev/null\n"
        f"    exit {preflight_status}\n"
        "fi\n"
        f"printf '%s' '{path}' > \"{marker}\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _run_startup_script_with_python_candidates(
    tmp_path: Path,
    *,
    local_preflight_status: int | None = None,
    home_preflight_status: int | None = None,
    system_preflight_status: int | None = 0,
    local_executable: bool = True,
    explicit_python: Path | None = None,
    explicit_preflight_status: int | None = None,
):
    project = tmp_path / "project"
    project.mkdir()
    script = project / "启动NewsReader.command"
    shutil.copy2(PROJECT_ROOT / "启动NewsReader.command", script)
    script.chmod(0o755)
    (project / "launcher.py").write_text("# fake launcher\n", encoding="utf-8")

    home = tmp_path / "home"
    selected = tmp_path / "selected-python"
    if local_preflight_status is not None:
        local_candidate = project / ".venv" / "bin" / "python"
        _make_fake_python(
            local_candidate,
            preflight_status=local_preflight_status,
            marker=selected,
        )
        if not local_executable:
            local_candidate.chmod(0o644)
    if home_preflight_status is not None:
        _make_fake_python(
            home / ".venvs" / "newsreader-workbench" / "bin" / "python",
            preflight_status=home_preflight_status,
            marker=selected,
        )

    fake_bin = tmp_path / "bin"
    if system_preflight_status is not None:
        _make_fake_python(
            fake_bin / "python3",
            preflight_status=system_preflight_status,
            marker=selected,
        )
    if explicit_python is not None:
        _make_fake_python(
            explicit_python,
            preflight_status=explicit_preflight_status or 0,
            marker=selected,
        )

    env = os.environ.copy()
    env.update({
        "HOME": str(home),
        "NEWS_READER_NO_ALERT": "1",
        "PATH": f"{fake_bin}{os.pathsep}/usr/bin:/bin",
    })
    env.pop("NEWS_READER_HOST", None)
    if explicit_python is not None:
        env["NEWS_READER_PYTHON"] = str(explicit_python)
    else:
        env.pop("NEWS_READER_PYTHON", None)

    result = subprocess.run(
        ["zsh", str(script)],
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    result.selected_python = selected.read_text() if selected.exists() else None
    return result


def test_startup_script_prefers_project_venv_python(tmp_path: Path):
    result = _run_startup_script_with_python_candidates(
        tmp_path,
        local_preflight_status=0,
        home_preflight_status=0,
        system_preflight_status=0,
    )
    assert result.returncode == 0
    assert result.selected_python == str(tmp_path / "project" / ".venv" / "bin" / "python")


def test_startup_script_falls_back_to_home_venv_after_local_dependency_failure(tmp_path: Path):
    result = _run_startup_script_with_python_candidates(
        tmp_path,
        local_preflight_status=1,
        home_preflight_status=0,
        system_preflight_status=0,
    )
    assert result.returncode == 0
    assert result.selected_python == str(tmp_path / "home" / ".venvs" / "newsreader-workbench" / "bin" / "python")


def test_startup_script_falls_back_to_system_python_after_both_venvs_fail(tmp_path: Path):
    result = _run_startup_script_with_python_candidates(
        tmp_path,
        local_preflight_status=1,
        home_preflight_status=1,
        system_preflight_status=0,
    )
    assert result.returncode == 0
    assert result.selected_python == str(tmp_path / "bin" / "python3")


def test_startup_script_skips_non_executable_local_python(tmp_path: Path):
    result = _run_startup_script_with_python_candidates(
        tmp_path,
        local_preflight_status=0,
        system_preflight_status=0,
        local_executable=False,
    )
    assert result.returncode == 0
    assert result.selected_python == str(tmp_path / "bin" / "python3")


def test_startup_script_rejects_import_broken_dependencies(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    script = project / "启动NewsReader.command"
    shutil.copy2(PROJECT_ROOT / "启动NewsReader.command", script)
    script.chmod(0o755)
    launcher_marker = tmp_path / "launcher-called"
    (project / "launcher.py").write_text(
        f"from pathlib import Path\nPath({str(launcher_marker)!r}).touch()\n",
        encoding="utf-8",
    )
    (tmp_path / "flask.py").write_text("raise ImportError('broken Flask')\n", encoding="utf-8")
    (tmp_path / "openai.py").write_text("raise RuntimeError('broken openai')\n", encoding="utf-8")

    env = os.environ.copy()
    env.update({
        "NEWS_READER_PYTHON": sys.executable,
        "NEWS_READER_NO_ALERT": "1",
        "PYTHONPATH": str(tmp_path),
    })
    result = subprocess.run(
        ["zsh", str(script)],
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 1
    assert "Flask" in output
    assert "openai" in output
    assert "pip install -r" in output
    assert not launcher_marker.exists()


def test_startup_script_does_not_fallback_after_explicit_python_dependency_failure(tmp_path: Path):
    explicit = tmp_path / "explicit-python"
    result = _run_startup_script_with_python_candidates(
        tmp_path,
        local_preflight_status=0,
        home_preflight_status=0,
        system_preflight_status=0,
        explicit_python=explicit,
        explicit_preflight_status=1,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 1
    assert result.selected_python is None
    assert "NEWS_READER_PYTHON" in output
    assert "pip install -r" in output


def test_startup_script_reports_install_hint_when_all_python_candidates_fail(tmp_path: Path):
    result = _run_startup_script_with_python_candidates(
        tmp_path,
        local_preflight_status=1,
        home_preflight_status=1,
        system_preflight_status=1,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 1
    assert result.selected_python is None
    assert "未找到可用的 Python 环境" in output
    assert "pip install -r" in output
