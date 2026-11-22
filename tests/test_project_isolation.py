import os
from pathlib import Path
import shlex
import subprocess
import sys

import app
import pytest
import secret_store
import updater


def test_keychain_operations_use_independent_account(monkeypatch):
    from types import SimpleNamespace
    calls = []

    def security(args):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="test-secret")

    monkeypatch.setattr(secret_store, "_run_security", security)
    assert secret_store.has_secret("DEEPSEEK_API_KEY")
    assert secret_store.read_secret("DEEPSEEK_API_KEY") == "test-secret"
    secret_store.write_secret("DEEPSEEK_API_KEY", "new-test-secret")
    secret_store.delete_secret("DEEPSEEK_API_KEY")
    assert len(calls) == 4
    assert all(args[args.index("-a") + 1] == "newsreader-workbench" for args in calls)


def test_child_process_uses_configured_keychain_account():
    script = """
from types import SimpleNamespace
from llm_client import _resolve_api_key
import secret_store

def security(args):
    assert args == ["find-generic-password", "-a", "newsreader-workbench-dev", "-s", "DEEPSEEK_API_KEY", "-w"]
    return SimpleNamespace(returncode=0, stdout="test-secret")

secret_store._run_security = security
assert _resolve_api_key("DEEPSEEK_API_KEY") == "test-secret"
"""
    env = {**os.environ, "NEWS_READER_KEYCHAIN_ACCOUNT": "newsreader-workbench-dev"}
    env.pop("DEEPSEEK_API_KEY", None)
    subprocess.run([sys.executable, "-B", "-c", script], cwd=Path(__file__).resolve().parents[1],
                   env=env, check=True)


@pytest.mark.parametrize(("account", "expected", "provided", "missing"), [
    (None, "newsreader-workbench", None, False),
    ("  \t", "newsreader-workbench", None, False),
    ("  nr test dev  ", "nr test dev", None, False),
    ("nr-test-dev", "nr-test-dev", "fixture-environment", False),
    ("nr-test-dev", "nr-test-dev", None, True),
])
def test_tailscale_launcher_respects_the_keychain_account(tmp_path, account, expected, provided, missing):
    keychain_log = tmp_path / "keychain.log"
    launch_log = tmp_path / "launch.log"
    commands = {
        "security": """printf '%s\\n' "$@" > "$NR_TEST_KEYCHAIN_LOG"
if [ "$NR_TEST_KEYCHAIN_MISSING" = '1' ]; then exit 44; fi
printf '%s\\n' fixture-keychain
""",
        "tailscale": "printf '%s\\n' 100.64.1.2\n",
        "python3": f"""if [ "${{1##*/}}" = launcher.py ]; then
    [ "$2" = --replace-existing ] && [ "$3" = --detect-network ] || exit 2
    printf '%s\\n' "$DEEPSEEK_API_KEY" > "$NR_TEST_LAUNCH_LOG"
else
    exec {shlex.quote(sys.executable)} "$@"
fi
""",
    }
    for command, source in commands.items():
        path = tmp_path / command
        path.write_text("#!/bin/sh\n" + source)
        path.chmod(0o700)
    env = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
           "NEWS_READER_PYTHON": str(tmp_path / "python3"),
           "NR_TEST_KEYCHAIN_LOG": str(keychain_log), "NR_TEST_LAUNCH_LOG": str(launch_log),
           "NR_TEST_KEYCHAIN_MISSING": str(int(missing))}
    for key, value in (("NEWS_READER_KEYCHAIN_ACCOUNT", account), ("DEEPSEEK_API_KEY", provided)):
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["bash", "scripts/start-tailscale.sh"], cwd=root, env=env,
                            text=True, capture_output=True, timeout=10)
    if provided is None:
        assert keychain_log.read_text().splitlines() == [
            "find-generic-password", "-a", expected, "-s", "DEEPSEEK_API_KEY", "-w"]
    else:
        assert not keychain_log.exists()
    if missing:
        assert result.returncode == 1 and f"-a {expected} -s DEEPSEEK_API_KEY" in result.stderr
        assert not launch_log.exists()
    else:
        assert result.returncode == 0, result.stderr
        assert launch_log.read_text().strip() == (provided or "fixture-keychain")


def test_agent_runtime_is_scoped_to_project(monkeypatch, tmp_path):
    monkeypatch.delenv("NEWS_READER_AGENT_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(app.runtime.db, 'root', tmp_path / "first")
    first = app.runtime.db.resolve_agent_runtime_dir()
    monkeypatch.setattr(app.runtime.db, 'root', tmp_path / "second")
    assert app.runtime.db.resolve_agent_runtime_dir() != first
    assert first.name == "agent"
    assert first.parent.name.startswith("newsreader-workbench-")
    assert updater.PROJECT_RUNTIME_DIR.name.startswith("newsreader-workbench-")
