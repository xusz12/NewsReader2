import json
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest

import dependency_update
import updater
from test_updater import remote_repo, git


def candidate(remote_repo):
    target, old, publish = remote_repo
    source = target.parent / "source"
    (source / "package.json").write_text('{"name":"test","version":"1.0.0"}\n')
    (source / "package-lock.json").write_text('{"name":"test","lockfileVersion":3}\n')
    git("-C", source, "add", "package.json", "package-lock.json")
    (target / ".git/info/exclude").write_text("node_modules/\n")
    (target / "node_modules").mkdir()
    (target / "node_modules/version").write_text("old")
    return target, old, publish()


def installer(monkeypatch, fail=False):
    original = subprocess.run
    calls = []
    def run(args, **kwargs):
        if "ci" not in args:
            return original(args, **kwargs)
        calls.append(args)
        assert "--ignore-scripts" in args
        stage = Path(kwargs["cwd"])
        (stage / "node_modules").mkdir()
        (stage / "node_modules/version").write_text("new")
        return subprocess.CompletedProcess(args, 1 if fail else 0, b"", b"")
    monkeypatch.setattr(dependency_update.subprocess, "run", run)
    monkeypatch.setattr(dependency_update.shutil, "which", lambda name: "/test/" + name)
    return calls


def test_install_failure_does_not_stop_service_or_change_checkout(remote_repo, monkeypatch):
    target, old, commit = candidate(remote_repo)
    calls = installer(monkeypatch, fail=True)
    monkeypatch.setattr(updater.os, "kill", lambda *a: pytest.fail("old process must stay running"))
    with pytest.raises(RuntimeError, match="dependency_install_failed"):
        updater.apply_update(target, "v2.1.4.7", commit, "v2.1.4.7", launch=True, old_pid=123)
    assert git("-C", target, "rev-parse", "HEAD") == old
    assert (target / "node_modules/version").read_text() == "old"
    assert len(calls) == 1 and not list(target.parent.glob(".newsreader-deps-*"))


def test_success_replaces_dependencies_only_after_preflight(remote_repo, monkeypatch):
    target, _, commit = candidate(remote_repo)
    installer(monkeypatch)
    assert updater.apply_update(target, "v2.1.4.7", commit, "v2.1.4.7")["ok"]
    assert (target / "node_modules/version").read_text() == "new"
    assert not list(target.parent.glob(".newsreader-deps-*"))


def test_health_failure_restores_code_and_exact_old_dependencies(remote_repo, monkeypatch):
    target, old, commit = candidate(remote_repo)
    installer(monkeypatch)
    monkeypatch.setattr(updater.os, "kill", lambda *a: None)
    monkeypatch.setattr(updater, "_wait_for_pid", lambda *a: None)
    monkeypatch.setattr(updater, "_launch", lambda *a: Mock())
    monkeypatch.setattr(updater, "_stop_child", lambda *a: None)
    seen = []
    def health(url, head, child):
        seen.append((head, (target / "node_modules/version").read_text()))
        return head == old
    monkeypatch.setattr(updater, "_wait_healthy", health)
    with pytest.raises(RuntimeError, match="health_check_failed"):
        updater.apply_update(target, "v2.1.4.7", commit, "v2.1.4.7", launch=True, old_pid=123)
    assert seen == [(commit, "new"), (old, "old")]
    assert git("-C", target, "rev-parse", "HEAD") == old
    assert json.loads(updater.STATE_PATH.read_text())["status"] == "rolled_back"


def test_command_check_failure_rolls_back_even_without_launch(remote_repo, monkeypatch):
    target, old, commit = candidate(remote_repo)
    installer(monkeypatch)
    def validate(repo):
        if repo == target:
            raise RuntimeError("dependency_runtime_check_failed")
    monkeypatch.setattr(dependency_update, "validate_runtime", validate)
    with pytest.raises(RuntimeError, match="dependency_runtime_check_failed"):
        updater.apply_update(target, "v2.1.4.7", commit, "v2.1.4.7")
    assert git("-C", target, "rev-parse", "HEAD") == old
    assert (target / "node_modules/version").read_text() == "old"


def test_unchanged_lock_does_not_reinstall(tmp_path):
    git("init", tmp_path)
    git("-C", tmp_path, "config", "user.email", "test@example.invalid")
    git("-C", tmp_path, "config", "user.name", "Test")
    for name in ("package.json", "package-lock.json"):
        (tmp_path / name).write_text("{}\n")
    git("-C", tmp_path, "add", ".")
    git("-C", tmp_path, "commit", "-m", "test: 固定依赖夹具")
    (tmp_path / "node_modules").mkdir()
    assert dependency_update.prepare(tmp_path, "HEAD") is None


def test_adapter_only_update_is_validated_before_touching_live_files(tmp_path, monkeypatch):
    manifest = json.dumps({"adapters": {"files": {"site/read.js": "hash"}}}).encode()
    files = {"package.json": b"{}", "package-lock.json": b"{}",
             "collection/opencli-manifest.json": manifest, "scripts/opencli.mjs": b"new wrapper",
             "collection/adapters/site/read.js": b"new adapter"}
    monkeypatch.setattr(dependency_update, "_file_at", lambda repo, commit, name: files.get(name))
    for name in ("package.json", "package-lock.json"):
        (tmp_path / name).write_bytes(files[name])
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules/marker").write_text("existing dependencies")
    called = []
    def validate(stage):
        called.append(stage)
        assert stage != tmp_path
        assert (stage / "collection/adapters/site/read.js").read_bytes() == b"new adapter"
        assert (stage / "node_modules/marker").read_text() == "existing dependencies"
        raise RuntimeError("dependency_runtime_check_failed")
    monkeypatch.setattr(dependency_update, "validate_runtime", validate)
    with pytest.raises(RuntimeError, match="dependency_runtime_check_failed"):
        dependency_update.prepare(tmp_path, "candidate")
    assert called and not called[0].exists()
    assert (tmp_path / "node_modules/marker").read_text() == "existing dependencies"
    assert not (tmp_path / "collection").exists()
