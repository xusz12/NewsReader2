"""Per-report choices use isolated data; no paid model calls or user settings."""
from contextlib import closing
from copy import deepcopy
import json
import uuid

import pytest
import periodic_reports as pr
from test_report_comparison import service, db, rows, START, END


@pytest.fixture(autouse=True)
def catalogs(monkeypatch):
    monkeypatch.setattr(pr.report_model.model_catalogs, 'cached_catalog', lambda config: {
        'options': [{'value': 'astra', 'reasoning_efforts': ['low', 'high']}]})
    monkeypatch.setattr(pr.report_model, 'pi_metadata', lambda request: {
        'reasoning_efforts': ['off', 'low', 'high'],
        'options': [{'value': 'gemini', 'provider': 'test', 'reasoning_efforts': ['off', 'low', 'high']}]})


def add(service, override=None, ident=None):
    return service.create_manual('evening', START, END, ident or uuid.uuid4().hex,
        service.manual_options()['database_token'], override)


def test_override_is_bound_without_mutating_saved_settings_and_replay_skips_validation(service, db, monkeypatch):
    saved = service.settings()
    saved['feature_models']['reports']['codex'].update(profile='research', provider='local')
    before = deepcopy(saved)
    service.settings = lambda: saved
    ident = uuid.uuid4().hex
    a = add(service, {'runtime': 'codex', 'model': 'astra', 'reasoning_effort': 'high'}, ident)
    b = add(service, {'runtime': 'pi', 'provider': 'test', 'model': 'gemini', 'reasoning_effort': 'low'})
    assert saved == before
    configs = {r['id']: json.loads(r['config_json']) for r in rows(db)}
    assert configs[a['id']]['codex'] == {'model': 'astra', 'profile': 'research', 'provider': 'local', 'sandbox': 'read-only', 'reasoning_effort': 'high'}
    assert configs[b['id']] == {'runtime': 'pi', 'provider': 'test', 'model': 'gemini', 'reasoning_effort': 'low'}
    assert a['model_label'] == 'Codex · astra · high'
    assert b['model_label'] == 'Pi · gemini · low'
    monkeypatch.setattr(pr.report_model, 'configuration', lambda *a, **kw: pytest.fail('Replay resolved settings'))
    assert add(service, {'invalid': True}, ident)['id'] == a['id']


def test_temporary_reports_share_material_and_retry_keeps_effort(service, db):
    a = add(service, {'runtime': 'codex', 'model': 'astra', 'reasoning_effort': 'high'})
    b = add(service, {'runtime': 'pi', 'provider': 'test', 'model': 'gemini', 'reasoning_effort': 'off'})
    seen = []
    original = service.runner
    service.runner = lambda config, prompt: (seen.append(deepcopy(config)), original(config, prompt))[1]
    service.run_one(); service.run_one()
    stored = {r['id']: r for r in rows(db)}
    assert stored[a['id']]['snapshot_id'] == stored[b['id']]['snapshot_id']
    assert {r['status'] for r in stored.values()} == {'done'}
    assert any(c.get('reasoning_effort') == 'off' for c in seen)
    with closing(db()) as conn:
        conn.execute("UPDATE periodic_reports SET status='failed',attempts=3 WHERE id=?", (b['id'],)); conn.commit()
    service.settings = lambda: {'feature_models': {'reports': {'runtime': 'codex', 'codex': {}, 'pi': {}}}}
    service.retry(b['id'], service.manual_options()['database_token'])
    assert json.loads(next(r for r in rows(db) if r['id'] == b['id'])['config_json'])['reasoning_effort'] == 'off'


@pytest.mark.parametrize('override,error', [
    ({'runtime': 'bad'}, 'report_model_selection_invalid'),
    ([], 'report_model_selection_invalid'),
    ({'runtime': 'codex', 'model': 'astra', 'sandbox': 'danger-full-access'}, 'report_model_selection_invalid'),
    ({'runtime': 'codex', 'model': 'astra', 'provider': 'unapproved'}, 'report_model_selection_invalid'),
    ({'runtime': 'codex', 'model': 'astra', 'reasoning_effort': 'medium'}, 'report_reasoning_unsupported'),
    ({'runtime': 'codex', 'model': '--injected'}, 'report_model_selection_invalid'),
    ({'runtime': 'pi', 'provider': '', 'model': 'gemini'}, 'report_model_selection_invalid'),
    ({'runtime': 'pi', 'provider': 'test', 'model': 'gemini', 'reasoning_effort': 'ultra'}, 'report_reasoning_unsupported'),
    ({'runtime': 'pi', 'provider': 'test', 'model': 'line\nbreak'}, 'report_model_selection_invalid'),
])
def test_invalid_overrides_never_enqueue(service, db, override, error):
    with pytest.raises(ValueError, match='^'+error+'$'): add(service, override)
    assert rows(db) == []


def test_catalog_validation_does_not_hold_database_write_lock(service, db, monkeypatch):
    def metadata(request):
        with closing(db()) as conn:
            conn.execute('BEGIN IMMEDIATE'); conn.rollback()
        return {'reasoning_efforts': ['high']}
    monkeypatch.setattr(pr.report_model, 'pi_metadata', metadata)
    assert add(service, {'runtime': 'pi', 'provider': 'test', 'model': 'gemini', 'reasoning_effort': 'high'})['status'] == 'pending'


def test_failed_codex_catalog_is_not_misreported_as_unsupported_reasoning(service, db, monkeypatch):
    monkeypatch.setattr(pr.report_model, 'catalog', lambda *args: {'options': [], 'error': 'unavailable'})
    with pytest.raises(RuntimeError, match='^report_model_catalog_failed$'):
        add(service, {'runtime': 'codex', 'model': 'astra', 'reasoning_effort': 'high'})
    assert rows(db) == []


def test_api_fixed_and_custom_windows_and_safe_catalog(service, db, monkeypatch):
    import app
    monkeypatch.setattr(app, 'REPORT_SERVICE', service)
    monkeypatch.setattr(service, 'start', lambda: None)
    client = app.app.test_client()
    options = client.get('/api/reports/manual-options').json
    assert options['model_settings']['codex']['model'] == 'astra'
    for runtime in ('codex', 'pi'):
        assert client.post('/api/reports/model-options', json={'runtime': runtime}).json['catalog']['options']
    assert client.post('/api/reports/model-options', json={'runtime': 'shell'}).json['error'] == 'report_model_selection_invalid'
    for window in ({'selection': '2026-10-29'}, {'period_start': START, 'period_end': END}):
        response = client.post('/api/reports/manual', json={
            'kind': 'evening', **window, 'request_id': uuid.uuid4().hex, 'database_token': options['database_token'],
            'model_override': {'runtime': 'pi', 'provider': 'test', 'model': 'gemini', 'reasoning_effort': 'high'}})
        assert response.status_code == 202 and response.json['report']['model_label'] == 'Pi · gemini · high'
    for error in ('report_model_catalog_failed', 'report_model_dependencies_missing'):
        def broken(request): raise RuntimeError(error)
        monkeypatch.setattr(pr.report_model, 'pi_metadata', broken)
        response = client.post('/api/reports/model-options', json={'runtime': 'pi'})
        assert response.status_code == 409 and response.json['error'] == error
