import json
import os
from pathlib import Path

import pytest

import pi_models
import settings


@pytest.mark.parametrize("effort", ["off", "low", "high", "max"])
def test_pi_effort_persists_per_feature_and_reaches_task_snapshots(tmp_path, monkeypatch, effort):
    import app, news_summaries, report_model
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(pi_models, "metadata", lambda request: {"reasoning_efforts": ["off", "low", "high", "max"]})
    monkeypatch.setattr(news_summaries, "native_config_stamp", lambda _: [])
    value = settings.default_app_settings()
    pi = {"provider": "fixture", "model": "model", "reasoning_effort": effort}
    value["llm"]["pi_chat"] = pi.copy()
    value["feature_models"] = {name: {"runtime": "pi", "pi": pi.copy()} for name in settings.MODEL_FEATURES}
    saved = app.validate_runtime_settings(value)
    settings.save_app_settings(saved)
    loaded = app.current_runtime_settings()
    assert loaded["llm"]["pi_chat"] == pi
    assert app._agent_executor_settings()["executor_config"] == {"reasoning_effort": effort}
    for feature in ("topics", "summaries"):
        captured = news_summaries.capture_config(loaded, feature)
        assert captured["pi_reasoning_effort"] == effort
    assert report_model.configuration(loaded, resolve=False)["reasoning_effort"] == effort
    assert report_model.selection(loaded)["pi"]["reasoning_effort"] == effort
    assert "reasoning_effort" not in report_model.configuration(loaded, resolve=False,
        override={"runtime": "pi", "provider": "fixture", "model": "model", "reasoning_effort": ""})
    # Older clients retain a selected effort; changing models does not inherit it.
    assert pi_models.normalize_config({"provider": "fixture", "model": "model"}, pi) == pi
    assert "reasoning_effort" not in pi_models.normalize_config({"model": "other"}, pi)


@pytest.mark.parametrize("effort", ["guess", False, [], {}, None])
def test_invalid_pi_effort_is_rejected(effort):
    with pytest.raises(ValueError, match="invalid_pi_reasoning_effort"):
        pi_models.normalize_config({"reasoning_effort": effort})


def test_unsupported_effort_cannot_be_saved_or_tested(monkeypatch):
    import app, connection_tests
    value = settings.default_app_settings()
    monkeypatch.setattr(app, "load_app_settings", lambda: value)
    monkeypatch.setattr(pi_models, "metadata", lambda request: {"reasoning_efforts": ["off"]})
    selected = {"provider": "fixture", "model": "plain", "reasoning_effort": "high"}
    client = app.app.test_client()
    response = client.post("/api/settings/connection-test", json={"runtime": "pi", "pi": selected})
    assert response.status_code == 400
    assert response.json["error"] == "invalid_pi_reasoning_effort"
    assert not connection_tests.LOCK.locked()
    for payload in ({"llm": {**value["llm"], "pi_chat": selected}},
                    {"llm": value["llm"], "feature_models": {"topics": {"pi": selected}}}):
        with pytest.raises(ValueError, match="invalid_pi_reasoning_effort"):
            app.validate_runtime_settings(payload)


def test_sdk_catalog_reads_logout_and_model_capabilities_without_inference(tmp_path, monkeypatch):
    # A private model registry; no real credentials, settings, or remote requests.
    if not (pi_models.ROOT / "node_modules/@earendil-works/pi-coding-agent").exists():
        pytest.skip("Pi SDK requires npm ci")
    provider = "newsreader-test"
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({provider: {"type": "api_key", "key": "fixture-key"}}))
    (tmp_path / "models.json").write_text(json.dumps({"providers": {provider: {
        "baseUrl": "http://127.0.0.1:1/v1", "api": "openai-completions",
        "models": [{"id": "reasoner", "name": "Reasoner", "reasoning": True,
                    "thinkingLevelMap": {"minimal": None, "medium": None, "xhigh": "xhigh", "max": None},
                    "input": ["text"], "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                    "contextWindow": 32768, "maxTokens": 4096}]
    }}}))
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "NEWS_READER_PI_AGENT_DIR": str(tmp_path), "PI_OFFLINE": "1"}
    monkeypatch.setattr(pi_models, "_pi_subprocess_env", lambda: env)
    available = [m for m in pi_models.catalog()["options"] if m["provider"] == provider]
    assert len(available) == 1
    assert available[0]["reasoning_efforts"] == ["off", "low", "high", "xhigh"]
    auth.write_text("{}")
    assert not [m for m in pi_models.catalog()["options"] if m["provider"] == provider]
    # An independent configured API key remains usable after login removal.
    models_file = tmp_path / "models.json"
    models = json.loads(models_file.read_text())
    models["providers"][provider]["apiKey"] = "fixture-managed-key"
    models_file.write_text(json.dumps(models))
    assert [m for m in pi_models.catalog()["options"] if m["provider"] == provider]


def test_pi_connection_uses_current_model_and_effort(tmp_path, monkeypatch):
    import connection_tests
    from test_model_settings import fake_pi
    log = fake_pi(tmp_path, monkeypatch)
    script = Path(os.environ["NEWS_READER_PI"])
    script.write_text(script.read_text().replace("--no-prompt-templates'", "--no-prompt-templates --thinking'"))
    monkeypatch.setattr(pi_models, "metadata", lambda request: {"reasoning_efforts": ["off", "high"]})
    for effort in ("off", "high"):
        result = connection_tests.test_connection({"runtime": "pi", "pi": {
            "provider": "chosen-provider", "model": "chosen-model", "reasoning_effort": effort}})
        assert result["status"] == "success"
        args = json.loads(log.read_text())
        assert args[args.index("--thinking") + 1] == effort
        assert args[args.index("--provider") + 1] == "chosen-provider"
        assert args[args.index("--model") + 1] == "chosen-model"
