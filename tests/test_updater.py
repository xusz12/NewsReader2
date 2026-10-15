import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

import updater


def git(*args):
    return subprocess.run(["git", *map(str, args)], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def remote_repo(tmp_path, monkeypatch):
    remote, source, target = (tmp_path / name for name in ("remote.git", "source", "target"))
    git("init", "--bare", remote)
    git("init", source)
    git("-C", source, "config", "user.email", "test@example.invalid")
    git("-C", source, "config", "user.name", "Test")
    (source / "version.json").write_text(json.dumps({"version": "v2.1.4.6", "repository": updater.REPOSITORY_URL}))
    git("-C", source, "add", "version.json")
    git("-C", source, "commit", "-m", "feat: 初始版本")
    git("-C", source, "branch", "-M", "main")
    git("-C", source, "remote", "add", "origin", remote)
    git("-C", source, "push", "-u", "origin", "main")
    git("clone", "-b", "main", remote, target)
    old = git("-C", target, "rev-parse", "HEAD")
    monkeypatch.setattr(updater, "REPOSITORY_URL", str(remote))
    monkeypatch.setattr(updater, "LOCK_PATH", tmp_path / "lock")
    monkeypatch.setattr(updater, "STATE_PATH", tmp_path / "state.json")

    def publish(version="v2.1.4.7", tag="v2.1.4.7"):
        (source / "version.json").write_text(json.dumps({"version": version, "repository": str(remote)}))
        git("-C", source, "add", "version.json")
        git("-C", source, "commit", "-m", "feat: 更新版本")
        commit = git("-C", source, "rev-parse", "HEAD")
        git("-C", source, "tag", tag)
        git("-C", source, "push", "origin", "main", tag)
        return commit

    return target, old, publish


def test_stable_versions_support_three_and_four_parts():
    assert updater.parse_version("v2.1.4") == (2, 1, 4)
    assert updater.parse_version("v2.1.4.6") == (2, 1, 4, 6)
    assert updater.parse_version("v2.1.4-rc1") is None
    assert updater.parse_version("2.1.4") is None


def test_fast_forward(remote_repo):
    target, _, publish = remote_repo
    commit = publish()
    assert updater.apply_update(target, "v2.1.4.7", commit, "v2.1.4.7")["status"] == "updated"
    assert git("-C", target, "rev-parse", "HEAD") == commit


def test_target_manifest_mismatch_never_updates(remote_repo):
    target, old, publish = remote_repo
    commit = publish(version="v9.9.9")
    with pytest.raises(RuntimeError, match="target_version_mismatch"):
        updater.apply_update(target, "v2.1.4.7", commit, "v2.1.4.7")
    assert git("-C", target, "rev-parse", "HEAD") == old


def test_dirty_and_detached_rejected(remote_repo):
    target, old, publish = remote_repo
    commit = publish()
    (target / "untracked").write_text("user data")
    with pytest.raises(RuntimeError, match="dirty_worktree"):
        updater.apply_update(target, "v2.1.4.7", commit, "v2.1.4.7", run_id="dirty-run")
    assert json.loads(updater.STATE_PATH.read_text())["status"] == "failed"
    assert json.loads(updater.STATE_PATH.read_text())["run_id"] == "dirty-run"
    (target / "untracked").unlink()
    git("-C", target, "checkout", "--detach", old)
    with pytest.raises(RuntimeError, match="detached_head"):
        updater.apply_update(target, "v2.1.4.7", commit, "v2.1.4.7")


def test_commit_mismatch_rejected(remote_repo):
    target, old, publish = remote_repo
    publish()
    with pytest.raises(RuntimeError, match="tag_commit_mismatch"):
        updater.apply_update(target, "v2.1.4.7", old, "v2.1.4.7")


def test_health_failure_rolls_back_and_restarts_old(remote_repo, monkeypatch):
    target, old, publish = remote_repo
    commit = publish()
    monkeypatch.setattr(updater.os, "kill", lambda *args: None)
    monkeypatch.setattr(updater, "_wait_for_pid", lambda *args: None)
    children = [Mock(), Mock()]
    monkeypatch.setattr(updater, "_launch", lambda repo: children.pop(0))
    health = iter([False, True])
    monkeypatch.setattr(updater, "_wait_healthy", lambda *args: next(health))
    monkeypatch.setattr(updater, "_stop_child", lambda child: None)
    with pytest.raises(RuntimeError, match="health_check_failed"):
        updater.apply_update(target, "v2.1.4.7", commit, "v2.1.4.7", old_pid=123, launch=True)
    assert git("-C", target, "rev-parse", "HEAD") == old
    assert json.loads(updater.STATE_PATH.read_text())["status"] == "rolled_back"


def test_duplicate_lock_rejected(remote_repo):
    import fcntl
    target, _, publish = remote_repo
    commit = publish()
    with updater.LOCK_PATH.open("w") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="update_in_progress"):
            updater.apply_update(target, "v2.1.4.7", commit, "v2.1.4.7")


def test_non_descendant_rejected(remote_repo):
    target, old, publish = remote_repo
    commit = publish()
    git("-C", target, "config", "user.email", "test@example.invalid")
    git("-C", target, "config", "user.name", "Test")
    (target / "local.txt").write_text("local branch")
    git("-C", target, "add", "local.txt")
    git("-C", target, "commit", "-m", "feat: 本地分叉")
    local = git("-C", target, "rev-parse", "HEAD")
    with pytest.raises(RuntimeError, match="target_not_descendant"):
        updater.apply_update(target, "v2.1.4.7", commit, "v2.1.4.7")
    assert git("-C", target, "rev-parse", "HEAD") == local


def test_network_error_preserves_head(remote_repo, monkeypatch):
    target, old, publish = remote_repo
    commit = publish()
    original = updater._run
    def fail_fetch(repo, *args, **kwargs):
        if args[0] == "fetch":
            raise RuntimeError("network_timeout")
        return original(repo, *args, **kwargs)
    monkeypatch.setattr(updater, "_run", fail_fetch)
    with pytest.raises(RuntimeError, match="network_timeout"):
        updater.apply_update(target, "v2.1.4.7", commit, "v2.1.4.7")
    assert git("-C", target, "rev-parse", "HEAD") == old
    assert json.loads(updater.STATE_PATH.read_text())["status"] == "failed"


def test_status_api_and_unmanaged_apply(tmp_path, monkeypatch):
    import app
    monkeypatch.setattr(app, "UPDATE_REPOSITORY_URL", "https://github.com/example/workbench.git")
    monkeypatch.setattr(app, "UPDATE_STATE_PATH", tmp_path / "status.json")
    client = app.app.test_client()
    assert client.get("/api/update/status").json["status"] == "idle"
    app.UPDATE_STATE_PATH.write_text(json.dumps({"status": "rolled_back", "error": "health_check_failed"}))
    assert client.get("/api/update/status").json["error"] == "health_check_failed"
    monkeypatch.delenv("NEWS_READER_MANAGED", raising=False)
    token = "test-unmanaged-token"
    app.UPDATE_TOKENS[token] = {"expires_at": app.time.time() + 100, "payload": {
        "available": True, "current_commit": app._current_commit(),
        "latest": {"tag": "v2.1.4.7", "version": "v2.1.4.7", "commit": "0" * 40},
    }}
    response = client.post("/api/update/apply", json={"check_token": token, "tag": "v2.1.4.7", "version": "v2.1.4.7", "commit": "0" * 40})
    assert response.status_code == 409
    assert response.json["error"] == "managed_launcher_required"


def test_check_network_timeout_returns_explicit_error(monkeypatch):
    import app
    monkeypatch.setattr(app, "UPDATE_CHECK_URL", "https://api.github.com/repos/example/workbench/tags?per_page=100")
    monkeypatch.setattr(app, "UPDATE_CHECK_CACHE", None)
    monkeypatch.setattr(app, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("timeout")))
    response = app.app.test_client().get("/api/update/check")
    assert response.status_code == 502
    assert response.json["error"] == "update_check_failed"


def test_unconfigured_project_never_checks_or_starts_updates(monkeypatch):
    import app
    monkeypatch.setattr(app, "UPDATE_REPOSITORY_URL", "")
    monkeypatch.setattr(app, "UPDATE_CHECK_URL", "")
    monkeypatch.setattr(app, "UPDATE_CHECK_CACHE", {"checked_at": app.time.time(), "payload": {"available": True}})
    def unexpected(*args, **kwargs):
        pytest.fail("An unconfigured project must not contact a remote or start an updater")
    monkeypatch.setattr(app, "urlopen", unexpected)
    monkeypatch.setattr(app, "_current_commit", lambda: "a" * 40)
    monkeypatch.setattr(app.subprocess, "Popen", unexpected)
    client = app.app.test_client()
    payload = client.get("/api/update/check").get_json()
    assert payload["ok"] is True
    assert payload["enabled"] is False
    assert payload["available"] is False
    assert payload["status"] == "not_configured"
    assert "check_token" not in payload
    response = client.post("/api/update/apply", json={})
    assert response.status_code == 409
    assert response.json["error"] == "update_repository_not_configured"
    assert client.get("/api/version").json["repository"] == ""


def test_unconfigured_cli_rejects_update_before_git(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "REPOSITORY_URL", "")
    def unexpected(*args, **kwargs):
        pytest.fail("No Git operation is allowed without an update repository")
    monkeypatch.setattr(updater, "_run", unexpected)
    with pytest.raises(ValueError, match="update_repository_not_configured"):
        updater.apply_update(tmp_path, "v0.0.2", "a" * 40, "v0.0.2")


def test_repository_configuration_has_no_fallback(tmp_path):
    assert updater.configured_repository(tmp_path) == ""
    path = tmp_path / "version.json"
    for content in ["not json", "[]", '{"repository":null}', '{"repository":""}']:
        path.write_text(content)
        assert updater.configured_repository(tmp_path) == ""
    path.write_text(json.dumps({"repository": "https://github.com/example/workbench.git"}))
    assert updater.configured_repository(tmp_path) == "https://github.com/example/workbench.git"
    assert updater.github_tags_url(updater.configured_repository(tmp_path)) == "https://api.github.com/repos/example/workbench/tags?per_page=100"
    assert updater.github_tags_url("") == ""
