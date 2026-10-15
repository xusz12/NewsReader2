import app
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


def test_agent_runtime_is_scoped_to_project(monkeypatch, tmp_path):
    monkeypatch.delenv("NEWS_READER_AGENT_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(app, "BASE_DIR", tmp_path / "first")
    first = app.resolve_agent_runtime_dir()
    monkeypatch.setattr(app, "BASE_DIR", tmp_path / "second")
    assert app.resolve_agent_runtime_dir() != first
    assert first.name == "agent"
    assert first.parent.name.startswith("newsreader-workbench-")
    assert updater.PROJECT_RUNTIME_DIR.name.startswith("newsreader-workbench-")
