"""Foreground macOS launcher that owns NewsReader's service process."""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.request import urlopen
import uuid

from updater import local_health_url

ROOT = Path(__file__).resolve().parent
ACTIVE_UPDATE_STATES = {
    "validating", "fetching", "stopping_old", "merging", "starting_new",
    "checking_health", "rolling_back",
}


def normalize_host(host: str) -> str:
    value = (host or "127.0.0.1").strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    if not value or any(character in value for character in "/\\?#\r\n\t "):
        raise ValueError("NEWS_READER_HOST must be a host name or IP address")
    return value


def resolve_config(env: dict[str, str] | None = None) -> tuple[str, int, str, str]:
    values = os.environ if env is None else env
    host = normalize_host(values.get("NEWS_READER_HOST", "127.0.0.1"))
    raw_port = (values.get("NEWS_READER_PORT") or "8080").strip()
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError("NEWS_READER_PORT must be an integer from 1 to 65535") from exc
    if not 1 <= port <= 65535:
        raise ValueError("NEWS_READER_PORT must be an integer from 1 to 65535")
    health_url = local_health_url(host, port)
    browser_host = host
    if browser_host in {"0.0.0.0", "::"}:
        browser_host = "127.0.0.1" if browser_host == "0.0.0.0" else "::1"
    if ":" in browser_host:
        browser_host = f"[{browser_host}]"
    return host, port, health_url, f"http://{browser_host}:{port}"


def _git_head(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        text=True, capture_output=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("unable to read current project revision")
    return result.stdout.strip()


def _json_request(path: str, payload: dict, timeout: float = 3.0) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(path)
        client.sendall(json.dumps(payload).encode("utf-8") + b"\n")
        with client.makefile("rb") as reader:
            line = reader.readline(65537)
    if not line or len(line) > 65536:
        raise RuntimeError("invalid launcher response")
    response = json.loads(line.decode("utf-8"))
    if not isinstance(response, dict):
        raise RuntimeError("invalid launcher response")
    return response


def _open_url(url: str) -> None:
    subprocess.run(["open", url], check=True)


class Supervisor:
    """Own one app process and mediate updater restarts over a private socket."""

    def __init__(
        self,
        root: Path = ROOT,
        *,
        host: str | None = None,
        port: int | None = None,
        env: dict[str, str] | None = None,
        opener=_open_url,
        service_command=None,
        health_timeout: float = 30.0,
    ):
        self.root = Path(root).resolve()
        source_env = dict(os.environ if env is None else env)
        configured_host, configured_port, _, _ = resolve_config(source_env)
        self.host = normalize_host(host) if host is not None else configured_host
        self.port = int(port) if port is not None else configured_port
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be from 1 to 65535")
        self.health_url = local_health_url(self.host, self.port)
        _, _, _, default_url = resolve_config({"NEWS_READER_HOST": self.host, "NEWS_READER_PORT": str(self.port)})
        self.browser_url = default_url
        self.opener = opener
        self.service_command = service_command or (lambda env: [sys.executable, str(self.root / "app.py")])
        self.health_timeout = health_timeout
        self.runtime_dir = Path(tempfile.gettempdir()) / f"news-reader-{os.getuid()}-{hashlib.sha256(str(self.root).encode()).hexdigest()[:16]}"
        self.socket_path = str(self.runtime_dir / "control.sock")
        self.lock_path = self.runtime_dir / "supervisor.lock"
        self.token = secrets.token_hex(32)
        self.base_env = source_env
        self.base_env["NEWS_READER_HOST"] = self.host
        self.base_env["NEWS_READER_PORT"] = str(self.port)
        self.base_env["NEWS_READER_MANAGED"] = "1"
        self.base_env["NEWS_READER_UPDATER_DIR"] = source_env.get("NEWS_READER_UPDATER_DIR", str(self.runtime_dir))
        self.base_env["NEWS_READER_SUPERVISOR_SOCKET"] = self.socket_path
        self.base_env["NEWS_READER_SUPERVISOR_TOKEN"] = self.token
        self.child: subprocess.Popen | None = None
        self.owned_processes: list[subprocess.Popen] = []
        self.child_instance_id: str | None = None
        self.child_commit: str | None = None
        self.update_process: subprocess.Popen | None = None
        self.update_run_id: str | None = None
        self.stop_requested = False
        self.listener: socket.socket | None = None
        self.lock_file = None
        self.exit_code = 0
        self._old_handlers: dict[int, object] = {}

    def _acquire(self) -> bool:
        self.runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.runtime_dir, 0o700)
        self.lock_file = self.lock_path.open("a+")
        os.chmod(self.lock_path, 0o600)
        try:
            fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock_file.close()
            self.lock_file = None
            return False
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(self.socket_path)
        os.chmod(self.socket_path, 0o600)
        server.listen(8)
        server.settimeout(0.2)
        self.listener = server
        return True

    def _reuse_existing(self) -> int:
        deadline = time.monotonic() + 15
        last_error = "launcher is already running but not yet ready"
        while time.monotonic() < deadline:
            try:
                response = _json_request(self.socket_path, {"action": "status"}, timeout=1.5)
                if response.get("ok") and response.get("healthy") and response.get("url"):
                    try:
                        self.opener(str(response["url"]))
                    except (OSError, subprocess.CalledProcessError) as exc:
                        print(f"现有服务已就绪，但无法打开浏览器：{exc}", file=sys.stderr)
                        return 1
                    print(f"NewsReader 已由现有启动器运行：{response['url']}")
                    return 0
                last_error = str(response.get("error") or last_error)
            except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
                last_error = str(exc)
            time.sleep(0.25)
        print(f"无法安全复用已有启动器：{last_error}。未启动第二个服务。", file=sys.stderr)
        return 1

    def _install_signals(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            self._old_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, self._handle_signal)

    def _restore_signals(self) -> None:
        for signum, handler in self._old_handlers.items():
            signal.signal(signum, handler)
        self._old_handlers.clear()

    def _handle_signal(self, _signum, _frame) -> None:
        self.stop_requested = True
        if self.update_process is not None and self.update_process.poll() is None:
            print("更新正在进行；将等待更新安全完成后关闭 NewsReader。", flush=True)

    def _spawn_service(self, expected_commit: str) -> tuple[subprocess.Popen, str]:
        instance_id = uuid.uuid4().hex
        child_env = dict(self.base_env, NEWS_READER_INSTANCE_ID=instance_id)
        command = self.service_command(child_env)
        process = subprocess.Popen(
            command, cwd=self.root, env=child_env, start_new_session=True,
        )
        self.child = process
        self.owned_processes.append(process)
        self.child_instance_id = instance_id
        self.child_commit = expected_commit
        if not self._wait_healthy(process, expected_commit, instance_id):
            code = process.poll()
            self._stop_owned_process(process)
            self.child = None
            self.child_instance_id = None
            self.child_commit = None
            detail = f" (exit code {code})" if code is not None else ""
            raise RuntimeError(f"NewsReader health check failed{detail}; see the server output")
        return process, instance_id

    def _wait_healthy(self, process: subprocess.Popen, commit: str, instance_id: str) -> bool:
        deadline = time.monotonic() + self.health_timeout
        base = self.health_url.rsplit("/api/version", 1)[0]
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return False
            try:
                with urlopen(self.health_url, timeout=0.5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if (payload.get("ok") is not True or payload.get("commit") != commit
                        or payload.get("instance_id") != instance_id):
                    time.sleep(0.1)
                    continue
                for path in ("/", "/static/app.js", "/static/style.css"):
                    with urlopen(base + path, timeout=1) as response:
                        if response.status != 200:
                            raise OSError("static health check failed")
                return process.poll() is None
            except (OSError, ValueError, json.JSONDecodeError):
                time.sleep(0.1)
        return False

    @staticmethod
    def _stop_owned_process(process: subprocess.Popen, timeout: float = 5.0) -> None:
        # Each managed server is started in a fresh session, so this group contains
        # only descendants created for that exact Popen-owned server.
        # Re-check ownership immediately before signalling. A process that has
        # already exited must never be addressed by its recycled numeric PID.
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process.poll() is None:
            process.wait(timeout=5)

    def _authorized(self, request: dict) -> bool:
        supplied = request.get("token")
        return isinstance(supplied, str) and hmac.compare_digest(supplied, self.token)

    def _reply(self, client: socket.socket, payload: dict) -> None:
        client.sendall(json.dumps(payload).encode("utf-8") + b"\n")

    def _serve_control(self) -> None:
        assert self.listener is not None
        try:
            client, _ = self.listener.accept()
        except socket.timeout:
            return
        with client:
            client.settimeout(2)
            try:
                reader = client.makefile("rb")
                line = reader.readline(65537)
                if not line or len(line) > 65536:
                    self._reply(client, {"ok": False, "error": "invalid_request"})
                    return
                request = json.loads(line.decode("utf-8"))
                if not isinstance(request, dict):
                    raise ValueError("invalid request")
                action = request.get("action")
                if action == "status":
                    self._status(client)
                elif action == "start_update":
                    self._start_update(client, request)
                elif action == "restart":
                    self._restart(client, request)
                elif action == "stop_service":
                    self._stop_update_service(client, request)
                else:
                    self._reply(client, {"ok": False, "error": "unsupported_action"})
            except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
                try:
                    self._reply(client, {"ok": False, "error": str(exc)[:200]})
                except OSError:
                    pass

    def _status(self, client: socket.socket) -> None:
        if self.child is None or self.child.poll() is not None or not self.child_instance_id:
            self._reply(client, {"ok": False, "healthy": False, "error": "service_restarting"})
            return
        try:
            with urlopen(self.health_url, timeout=1) as response:
                payload = json.loads(response.read().decode("utf-8"))
            healthy = (payload.get("ok") is True and payload.get("commit") == self.child_commit
                       and payload.get("instance_id") == self.child_instance_id)
        except (OSError, ValueError, json.JSONDecodeError):
            healthy = False
        self._reply(client, {"ok": healthy, "healthy": healthy,
                             "url": self.browser_url if healthy else None,
                             "error": None if healthy else "service_not_healthy"})

    def _start_update(self, client: socket.socket, request: dict) -> None:
        if not self._authorized(request):
            self._reply(client, {"ok": False, "error": "unauthorized"})
            return
        if (self.child is None or self.child.poll() is not None
                or request.get("pid") != self.child.pid):
            self._reply(client, {"ok": False, "error": "service_owner_mismatch"})
            return
        if self.update_process is not None and self.update_process.poll() is None:
            self._reply(client, {"ok": False, "error": "update_in_progress"})
            return
        tag = request.get("tag")
        version = request.get("version")
        commit = request.get("commit")
        run_id = request.get("run_id")
        health_url = request.get("health_url")
        instance_id = request.get("instance_id")
        if (not isinstance(tag, str) or version != tag or not isinstance(commit, str)
                or len(commit) != 40 or not isinstance(run_id, str) or not run_id
                or health_url != self.health_url or instance_id != self.child_instance_id):
            self._reply(client, {"ok": False, "error": "invalid_update_request"})
            return
        command = [sys.executable, str(self.root / "updater.py"), "--repo", str(self.root),
                   "--run-id", run_id, "--tag", tag, "--version", version,
                   "--commit", commit, "--pid", str(self.child.pid),
                   "--health-url", self.health_url, "--supervised",
                   "--service-instance-id", self.child_instance_id]
        try:
            self.update_process = subprocess.Popen(
                command, cwd=self.root, env=self.base_env, start_new_session=True,
            )
        except OSError:
            self.update_process = None
            self._reply(client, {"ok": False, "error": "updater_launch_failed"})
            return
        self.update_run_id = run_id
        self._reply(client, {"ok": True, "status": "updating", "run_id": run_id})

    def _restart(self, client: socket.socket, request: dict) -> None:
        if not self._authorized(request):
            self._reply(client, {"ok": False, "error": "unauthorized"})
            return
        if (self.update_process is None or request.get("pid") != self.update_process.pid
                or request.get("run_id") != self.update_run_id):
            self._reply(client, {"ok": False, "error": "update_owner_mismatch"})
            return
        if request.get("health_url") != self.health_url:
            self._reply(client, {"ok": False, "error": "health_url_mismatch"})
            return
        if self.child is not None and self.child.poll() is None:
            self._reply(client, {"ok": False, "error": "service_still_running"})
            return
        expected_commit = request.get("expected_commit")
        try:
            if not isinstance(expected_commit, str) or _git_head(self.root) != expected_commit:
                raise RuntimeError("restart_commit_mismatch")
            self._spawn_service(expected_commit)
        except (OSError, RuntimeError) as exc:
            self._reply(client, {"ok": False, "error": str(exc)[:300]})
            return
        self._reply(client, {"ok": True, "status": "healthy", "commit": expected_commit,
                             "instance_id": self.child_instance_id})

    def _stop_update_service(self, client: socket.socket, request: dict) -> None:
        if not self._authorized(request):
            self._reply(client, {"ok": False, "error": "unauthorized"})
            return
        if (self.update_process is None or request.get("pid") != self.update_process.pid
                or request.get("run_id") != self.update_run_id):
            self._reply(client, {"ok": False, "error": "update_owner_mismatch"})
            return
        expected_commit = request.get("expected_commit")
        expected_instance_id = request.get("expected_instance_id")
        if self.child is None:
            self._reply(client, {"ok": True, "status": "already_stopped"})
            return
        if self.child.poll() is not None:
            # Reap and forget an exited Popen; never signal its old numeric PID.
            self.child = None
            self.child_instance_id = None
            self.child_commit = None
            self._reply(client, {"ok": True, "status": "already_stopped"})
            return
        if self.child_commit != expected_commit:
            self._reply(client, {"ok": False, "error": "service_commit_mismatch"})
            return
        if expected_instance_id != self.child_instance_id:
            self._reply(client, {"ok": False, "error": "service_identity_mismatch"})
            return
        self._stop_owned_process(self.child)
        self.child = None
        self.child_instance_id = None
        self.child_commit = None
        self._reply(client, {"ok": True, "status": "stopped"})

    def _update_state(self) -> dict:
        runtime_dir = Path(self.base_env["NEWS_READER_UPDATER_DIR"])
        try:
            value = json.loads((runtime_dir / "news-reader-updater-state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _observe_update(self) -> None:
        if self.update_process is None or self.update_process.poll() is None:
            return
        code = self.update_process.returncode
        state = self._update_state()
        if self.child is None or self.child.poll() is not None:
            print(f"更新进程已退出（代码 {code}），服务未恢复：{state.get('status', 'unknown')}", file=sys.stderr)
            self.exit_code = 1
            self.stop_requested = True
        elif code != 0:
            print(f"更新未完成，继续运行当前服务：{state.get('error') or state.get('status', 'unknown')}", file=sys.stderr)
        self.update_process = None
        self.update_run_id = None

    def run(self) -> int:
        if not self._acquire():
            return self._reuse_existing()
        self._install_signals()
        try:
            expected_commit = _git_head(self.root)
            print(f"正在启动 NewsReader：{self.browser_url}", flush=True)
            self._spawn_service(expected_commit)
            try:
                self.opener(self.browser_url)
            except (OSError, subprocess.CalledProcessError) as exc:
                print(f"服务已就绪，但无法打开浏览器：{exc}", file=sys.stderr, flush=True)
            while True:
                self._serve_control()
                self._observe_update()
                if self.stop_requested:
                    if self.update_process is not None and self.update_process.poll() is None:
                        continue
                    break
                if self.child is None:
                    if self.update_process is not None and self.update_process.poll() is None:
                        continue
                    self.exit_code = 1
                    break
                if self.child.poll() is not None:
                    if self.update_process is not None and self.update_process.poll() is None:
                        continue
                    print(f"NewsReader 服务退出（代码 {self.child.returncode}）", file=sys.stderr)
                    self.exit_code = self.child.returncode or 1
                    break
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"启动 NewsReader 失败：{exc}", file=sys.stderr, flush=True)
            self.exit_code = 1
        finally:
            if self.update_process is not None and self.update_process.poll() is None:
                # The updater is a detached child. Do not interrupt Git's safe-update transaction.
                print("等待受控更新结束后再清理服务。", flush=True)
                while self.update_process.poll() is None:
                    self._serve_control()
                    time.sleep(0.1)
                self._observe_update()
            if self.child is not None:
                self._stop_owned_process(self.child)
                self.child = None
            if self.listener is not None:
                self.listener.close()
                self.listener = None
            try:
                os.unlink(self.socket_path)
            except FileNotFoundError:
                pass
            if self.lock_file is not None:
                fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
                self.lock_file.close()
                self.lock_file = None
            self._restore_signals()
        return self.exit_code


def main() -> int:
    try:
        host, port, _, _ = resolve_config()
        return Supervisor(host=host, port=port).run()
    except (OSError, ValueError) as exc:
        print(f"启动 NewsReader 失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
