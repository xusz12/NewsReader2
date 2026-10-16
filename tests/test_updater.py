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


@pytest.fixture
def update_api(monkeypatch, tmp_path):
    import app
    monkeypatch.setattr(app, "UPDATE_REPOSITORY_URL", "https://github.com/example/workbench.git")
    monkeypatch.setattr(app, "UPDATE_CHECK_URL", "https://api.github.com/repos/example/workbench/tags?per_page=100")
    monkeypatch.setattr(app, "UPDATE_CHECK_CACHE", None)
    monkeypatch.setattr(app, "UPDATE_TOKENS", {})
    monkeypatch.setattr(app, "UPDATE_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(app, "_version_manifest", lambda: {"version": "v0.0.6"})
    monkeypatch.setattr(app, "_current_commit", lambda: "a" * 40)
    state = {"clean": True, "detached": False, "head": "a" * 40, "branch": "main"}
    monkeypatch.setattr(app, "update_git_state", lambda repo: dict(state))
    monkeypatch.setenv("NEWS_READER_MANAGED", "1")
    return app, app.app.test_client(), state


def tag_response(items):
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = json.dumps(items).encode()
    return response


def tag(version):
    return {"name": version, "commit": {"sha": "b" * 40}}


def test_check_pagination_numeric_sort_and_force_refresh(update_api, monkeypatch):
    app, client, state = update_api
    responses = [tag_response([tag("v0.0.7")] * 100), tag_response([
        tag("v0.0.10"), tag("v0.0.99-rc1"), {"name": "v0.1.0", "commit": None},
    ]), tag_response([tag("v0.0.11")])]
    fetch = Mock(side_effect=responses)
    monkeypatch.setattr(app, "urlopen", fetch)
    first = client.get("/api/update/check").json
    assert first["latest"]["version"] == "v0.0.10"
    assert first["can_update"] and first["check_token"]
    assert fetch.call_args_list[1].args[0].full_url.endswith("&page=2")
    state["clean"] = False
    cached = client.get("/api/update/check").json
    assert fetch.call_count == 2
    assert cached["blockers"] == ["dirty_worktree"]
    assert not cached["can_update"] and "check_token" not in cached
    fresh = client.get("/api/update/check?force=1").json
    assert fresh["latest"]["version"] == "v0.0.11"
    assert fetch.call_count == 3


@pytest.mark.parametrize(("tags", "status"), [
    ([], "no_releases"), ([tag("v0.0.6.0")], "up_to_date"), ([tag("v0.0.5")], "ahead"),
])
def test_no_update_states_do_not_issue_tokens(update_api, monkeypatch, tags, status):
    app, client, _ = update_api
    monkeypatch.setattr(app, "urlopen", lambda *args, **kwargs: tag_response(tags))
    result = client.get("/api/update/check").json
    assert result["status"] == status
    assert not result["available"]
    assert "check_token" not in result


def test_check_reports_detached_and_unmanaged(update_api, monkeypatch):
    app, client, state = update_api
    state["detached"] = True
    monkeypatch.delenv("NEWS_READER_MANAGED")
    monkeypatch.setattr(app, "urlopen", lambda *args, **kwargs: tag_response([tag("v0.0.7")]))
    result = client.get("/api/update/check").json
    assert result["available"]
    assert result["blockers"] == ["detached_head", "managed_launcher_required"]
    assert "check_token" not in result


def test_apply_rechecks_worktree_before_launch_and_consumes_token(update_api, monkeypatch):
    app, client, state = update_api
    monkeypatch.setattr(app, "urlopen", lambda *args, **kwargs: tag_response([tag("v0.0.7")]))
    checked = client.get("/api/update/check").json
    state["clean"] = False
    launch = Mock(side_effect=AssertionError("dirty worktree must not launch"))
    monkeypatch.setattr(app.subprocess, "Popen", launch)
    body = {"check_token": checked["check_token"], **checked["latest"]}
    result = client.post("/api/update/apply", json=body)
    assert result.status_code == 409
    assert result.json["error"] == "dirty_worktree"
    assert not launch.called
    assert client.post("/api/update/apply", json=body).json["error"] == "expired_check_token"
    assert client.post("/api/update/apply", json=["invalid"]).status_code == 400


def test_check_cleans_expired_tokens(update_api, monkeypatch):
    app, client, _ = update_api
    app.UPDATE_TOKENS["expired"] = {"expires_at": 0, "payload": {}}
    monkeypatch.setattr(app, "urlopen", lambda *args, **kwargs: tag_response([tag("v0.0.7")]))
    checked = client.get("/api/update/check").json
    assert list(app.UPDATE_TOKENS) == [checked["check_token"]]


def test_update_check_loads_trusted_roots_without_disabling_tls(update_api, monkeypatch):
    import ssl
    app, client, _ = update_api
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    assert context.cert_store_stats()["x509_ca"] == 0
    monkeypatch.setattr(app.ssl, "create_default_context", lambda: context)
    fetch = Mock(return_value=tag_response([]))
    monkeypatch.setattr(app, "urlopen", fetch)
    assert client.get("/api/update/check").status_code == 200
    used_context = fetch.call_args.kwargs["context"]
    assert used_context.cert_store_stats()["x509_ca"] > 0
    assert used_context.check_hostname is True
    assert used_context.verify_mode == ssl.CERT_REQUIRED


def test_ignored_local_files_are_preserved(remote_repo):
    target, _, publish = remote_repo
    (target / ".git/info/exclude").write_text("app_settings.json\noutput/\n")
    (target / "app_settings.json").write_text("local settings")
    (target / "output").mkdir()
    (target / "output/draft.svg").write_text("local draft")
    commit = publish()
    updater.apply_update(target, "v2.1.4.7", commit, "v2.1.4.7")
    assert (target / "app_settings.json").read_text() == "local settings"
    assert (target / "output/draft.svg").read_text() == "local draft"


def test_update_never_overwrites_ignored_file(remote_repo):
    target, old, publish = remote_repo
    source = target.parent / "source"
    (source / "private.json").write_text("remote contents")
    git("-C", source, "add", "private.json")
    commit = publish()
    (target / ".git/info/exclude").write_text("private.json\n")
    (target / "private.json").write_text("local contents")
    with pytest.raises(RuntimeError):
        updater.apply_update(target, "v2.1.4.7", commit, "v2.1.4.7")
    assert git("-C", target, "rev-parse", "HEAD") == old
    assert (target / "private.json").read_text() == "local contents"


def test_initial_git_error_records_failed_run(remote_repo, monkeypatch):
    target, _, publish = remote_repo
    commit = publish()
    monkeypatch.setattr(updater, "git_state", Mock(side_effect=RuntimeError("git_timeout")))
    with pytest.raises(RuntimeError, match="git_timeout"):
        updater.apply_update(target, "v2.1.4.7", commit, "v2.1.4.7", run_id="failed-git")
    state = json.loads(updater.STATE_PATH.read_text())
    assert state["run_id"] == "failed-git" and state["status"] == "failed"


def test_supervisor_records_unexpected_updater_exit(tmp_path):
    import launcher
    supervisor = launcher.Supervisor(env={"NEWS_READER_UPDATER_DIR": str(tmp_path)})
    supervisor.update_process = Mock(returncode=1)
    supervisor.update_process.poll.return_value = 1
    supervisor.child = Mock()
    supervisor.child.poll.return_value = None
    supervisor.update_run_id = "interrupted-run"
    supervisor._observe_update()
    state = json.loads((tmp_path / "news-reader-updater-state.json").read_text())
    assert state["run_id"] == "interrupted-run"
    assert state["error"] == "update_interrupted" and state["status"] == "failed"
