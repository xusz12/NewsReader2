"""Daily and weekly/monthly model groups use isolated settings and SQLite."""
from contextlib import closing
from copy import deepcopy
import json
import uuid

import pytest
import settings
import periodic_reports as pr
from test_periodic_reports import db, runner


def choices(model, runtime="codex"):
    return {"runtime": runtime, "codex": {"model": model, "profile": model + "-profile"},
            "pi": {"provider": "test", "model": model, "reasoning_effort": "high"}}


@pytest.fixture
def configured(db, monkeypatch):
    monkeypatch.setattr(pr, "now", lambda: pr.local("2026-11-02T22:00:00+08:00"))
    value = settings.default_app_settings()
    value["feature_models"] = settings.normalize_feature_models({
        "reports": choices("daily"), "reports_periodic": choices("periodic", "pi"),
    }, value)
    service = pr.Service(db, lambda: value, lambda: None, runner)
    return service, value


def config_for(db, ident):
    with closing(db()) as conn:
        return json.loads(conn.execute("SELECT config_json FROM periodic_reports WHERE id=?", (ident,)).fetchone()[0])


def assert_choice(config, kind):
    if kind in {"weekly", "monthly"}:
        assert config["runtime"] == "pi"
        assert config["model"] == "periodic" and config["reasoning_effort"] == "high"
    else:
        assert config["runtime"] == "codex"
        assert config["codex"]["model"] == "daily"
        assert config["codex"]["profile"] == "daily-profile"


def test_old_reports_migrate_once_and_partial_clients_preserve_each_group(tmp_path, monkeypatch):
    import app
    path = tmp_path / "settings.json"
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(path))
    monkeypatch.setattr(settings.pi_models, "metadata", lambda request: {"reasoning_efforts": ["high"]})
    original = settings.default_app_settings()
    original["agent"].update(runtime="codex", codex={"model": "chat"})
    original["feature_models"] = {"reports": choices("old-report", "pi")}
    path.write_text(json.dumps(original))
    loaded = settings.load_app_settings()
    for feature in settings.REPORT_MODEL_FEATURES.values():
        assert loaded["feature_models"][feature] == loaded["feature_models"]["reports"]
    loaded["feature_models"]["reports"]["pi"]["model"] = "daily-only"
    assert loaded["feature_models"]["reports_periodic"]["pi"]["model"] == "old-report"
    settings.save_app_settings(loaded)
    changed = app.validate_runtime_settings({"llm": original["llm"],
        "feature_models": {"reports_periodic": choices("new-periodic")}})
    settings.save_app_settings(changed)
    persisted = settings.load_app_settings()["feature_models"]
    assert persisted["reports"]["pi"]["model"] == "daily-only"
    assert persisted["reports_periodic"]["codex"]["model"] == "new-periodic"
    # Old clients omit all the new keys or even feature_models itself.
    assert app.validate_runtime_settings({"llm": original["llm"]})["feature_models"] == persisted


@pytest.mark.parametrize("kind", pr.KINDS)
def test_manual_and_preview_bind_correct_defaults_and_retry_keeps_them(configured, db, kind):
    service, value = configured
    data = service.manual_options()
    default = data["model_defaults"][kind]
    assert default["model_settings"] == value["feature_models"][settings.report_model_feature(kind)]
    ident = uuid.uuid4().hex
    report = service.create_manual(kind, "2026-10-30T00:00:00+08:00", "2026-10-31T00:00:00+08:00",
                                   ident, data["database_token"])
    assert report["model_label"] == default["model_label"]
    assert_choice(config_for(db, report["id"]), kind)
    period = next(p for p in service.preview_options()["periods"] if p["kind"] == kind)
    preview = service.create_preview(kind, period["scheduled_at"], uuid.uuid4().hex, data["database_token"])
    assert_choice(config_for(db, preview["id"]), kind)
    value["feature_models"][settings.report_model_feature(kind)] = choices("changed")
    replay = service.create_manual(kind, report["period_start"], report["period_end"], ident, data["database_token"])
    assert replay["id"] == report["id"] and replay["model_label"] == report["model_label"]
    with closing(db()) as conn, conn:
        conn.execute("UPDATE periodic_reports SET status='failed' WHERE id=?", (report["id"],))
    service.retry(report["id"], data["database_token"])
    assert_choice(config_for(db, report["id"]), kind)


def test_automatic_jobs_bind_the_two_group_defaults(configured, db):
    service, value = configured
    service.started_at = pr.local("2026-10-30T00:00:00+08:00")
    service.schedule_once()
    with closing(db()) as conn:
        rows = conn.execute("SELECT kind,config_json FROM periodic_reports").fetchall()
    assert {row["kind"] for row in rows} == set(pr.KINDS)
    for row in rows:
        assert_choice(json.loads(row["config_json"]), row["kind"])


def test_missing_daily_model_does_not_block_weekly_monthly_schedules(configured, db):
    service, value = configured
    value["feature_models"]["reports"] = {"runtime": "pi", "codex": {}, "pi": {}}
    service.started_at = pr.local("2026-10-30T00:00:00+08:00")
    service.schedule_once()
    with closing(db()) as conn:
        rows = conn.execute("SELECT kind,config_json FROM periodic_reports").fetchall()
    assert {row["kind"] for row in rows} == set(pr.KINDS)
    for row in rows:
        if row["kind"] in {"morning", "noon", "evening"}:
            assert row["config_json"] is None
        else:
            assert_choice(json.loads(row["config_json"]), row["kind"])


@pytest.mark.parametrize("kind", pr.KINDS)
@pytest.mark.parametrize("action", ["run", "retry"])
def test_legacy_unbound_jobs_resolve_their_own_kind(configured, db, kind, action):
    service, _ = configured
    token = service.manual_options()["database_token"]
    report = service.create_manual(kind, "2026-10-30T00:00:00+08:00", "2026-10-31T00:00:00+08:00",
                                   uuid.uuid4().hex, token)
    with closing(db()) as conn, conn:
        conn.execute("UPDATE periodic_reports SET config_json=NULL,status=? WHERE id=?",
                     ("failed" if action == "retry" else "pending", report["id"]))
    if action == "retry":
        service.retry(report["id"], token)
    else:
        assert service.run_one()
    assert_choice(config_for(db, report["id"]), kind)


def test_kind_catalog_and_temporary_override_use_its_profile(configured, db, monkeypatch):
    import app
    service, value = configured
    original = deepcopy(value)
    monkeypatch.setattr(app, "REPORT_SERVICE", service)
    monkeypatch.setattr(service, "start", lambda: None)
    seen = []
    def catalog(config):
        seen.append(config)
        return {"options": [{"value": "temporary", "reasoning_efforts": ["high"]}]}
    monkeypatch.setattr(pr.report_model.model_catalogs, "cached_catalog", catalog)
    client = app.app.test_client()
    for kind in pr.KINDS:
        response = client.post("/api/reports/model-options", json={"kind": kind, "runtime": "codex"})
        group = "periodic" if kind in {"weekly", "monthly"} else "daily"
        assert response.status_code == 200 and seen[-1]["profile"] == group + "-profile"
    for kind in ["daily", "custom", "unknown", [], None]:
        response = client.post("/api/reports/model-options", json={"kind": kind, "runtime": "codex"})
        assert response.status_code == 409 and response.json["error"] == "report_model_selection_invalid"
    token = service.manual_options()["database_token"]
    response = client.post("/api/reports/manual", json={"kind": "weekly", "selection": "2026-10-19",
        "request_id": uuid.uuid4().hex, "database_token": token,
        "model_override": {"runtime": "codex", "model": "temporary", "reasoning_effort": "high"}})
    assert response.status_code == 202
    assert seen[-1]["profile"] == "periodic-profile"
    bound = config_for(db, response.json["report"]["id"])["codex"]
    assert (bound["model"], bound["profile"], bound["reasoning_effort"]) == ("temporary", "periodic-profile", "high")
    assert value == original


def test_options_exclude_custom_and_share_exactly_two_defaults(configured, monkeypatch):
    import app
    service, _ = configured
    monkeypatch.setattr(app, "REPORT_SERVICE", service)
    client = app.app.test_client()
    data = client.get('/api/reports/manual-options').json
    assert {p['kind'] for p in data['periods']} == set(pr.KINDS)
    defaults = data['model_defaults']
    assert set(defaults) == set(pr.KINDS)
    assert defaults['morning'] == defaults['noon'] == defaults['evening']
    assert defaults['weekly'] == defaults['monthly'] != defaults['morning']
    response = client.post('/api/reports/manual', json={'kind': 'custom', 'request_id': uuid.uuid4().hex,
        'database_token': data['database_token'], 'period_start': '2026-10-29T00:00:00+08:00',
        'period_end': '2026-10-30T00:00:00+08:00'})
    assert response.status_code == 409 and response.json['error'] == 'report_window_invalid'


def test_draft_per_kind_settings_consolidate_without_retaining_custom_settings():
    value = {'reports': choices('removed-custom'),
             **{f'reports_{kind}': choices(kind) for kind in pr.KINDS}}
    result = settings.normalize_feature_models(value, settings.default_app_settings())
    assert set(result) == set(settings.MODEL_FEATURES)
    assert result['reports']['codex']['model'] == 'morning'
    assert result['reports_periodic']['codex']['model'] == 'weekly'


def test_saved_default_effort_does_not_inherit_chat_effort(tmp_path, monkeypatch):
    import app
    monkeypatch.setenv('NEWS_READER_APP_SETTINGS_PATH', str(tmp_path / 'settings.json'))
    value = settings.default_app_settings()
    value['llm']['pi_chat'] = {'provider': 'test', 'model': 'same-model', 'reasoning_effort': 'high'}
    value['feature_models'] = {'reports': {'runtime': 'pi', 'pi': {'provider': 'test', 'model': 'same-model'}}}
    settings.save_app_settings(value)
    loaded = app.current_runtime_settings()
    assert loaded['llm']['pi_chat']['reasoning_effort'] == 'high'
    for key in ('reports', 'reports_periodic'):
        assert loaded['feature_models'][key]['pi'] == {'provider': 'test', 'model': 'same-model'}
    settings.save_app_settings(loaded)
    assert settings.load_app_settings()['feature_models'] == loaded['feature_models']
