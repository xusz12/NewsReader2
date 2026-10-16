import json
import os
import subprocess
import sys
from copy import deepcopy

import pytest

import model_catalogs as catalogs
import settings


CONFIG = {'model': 'model-a', 'provider': '', 'profile': '', 'sandbox': 'read-only'}
CATALOG = {
    'source': 'codex-app-server', 'error': '', 'default_label': '沿用本机默认',
    'resolved_default_model': '',
    'options': [
        {'value': 'model-a', 'label': 'model-a', 'reasoning_efforts': ['low', 'high']},
        {'value': 'model-b', 'label': 'model-b', 'reasoning_efforts': ['medium']},
    ],
}


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv('NEWS_READER_APP_SETTINGS_PATH', str(tmp_path / 'settings.json'))
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    monkeypatch.setenv('NEWS_READER_CODEX', 'test-codex')


def test_catalog_survives_new_process_without_changing_settings(monkeypatch):
    settings.save_app_settings(settings.default_app_settings())
    before = settings.resolve_app_settings_path().read_bytes()
    monkeypatch.setattr(catalogs, 'codex_catalog', lambda config: deepcopy(CATALOG))
    assert catalogs.safe_catalog(CONFIG)['options'] == CATALOG['options']
    # The child has no patched CLI and cannot discover test-codex; it must read disk.
    child = subprocess.run(
        [sys.executable, '-c', 'import json, model_catalogs; print(json.dumps(model_catalogs.cached_catalog({})))'],
        capture_output=True, text=True, check=True, env=os.environ.copy(), timeout=10,
    )
    restored = json.loads(child.stdout)
    assert restored['cached'] and restored['cached_at']
    assert restored['options'] == CATALOG['options']
    assert settings.resolve_app_settings_path().read_bytes() == before


@pytest.mark.parametrize('override', [{'provider': 'other'}, {'profile': 'research'}])
def test_catalog_is_partitioned_by_provider_and_profile(monkeypatch, override):
    monkeypatch.setattr(catalogs, 'codex_catalog', lambda config: deepcopy(CATALOG))
    catalogs.safe_catalog(CONFIG)
    assert catalogs.cached_catalog({**CONFIG, **override})['options'] == []
    assert catalogs.cached_catalog({**CONFIG, 'model': 'model-b', 'reasoning_effort': 'medium'})['options'] == CATALOG['options']


def test_failed_refresh_preserves_last_success_and_next_refresh_replaces_it(monkeypatch):
    monkeypatch.setattr(catalogs, 'codex_catalog', lambda config: deepcopy(CATALOG))
    catalogs.safe_catalog(CONFIG)
    def fail(config):
        raise RuntimeError('SECRET provider credentials')
    monkeypatch.setattr(catalogs, 'codex_catalog', fail)
    failed = catalogs.safe_catalog(CONFIG)
    assert failed['options'] == CATALOG['options'] and failed['cached']
    assert '保留上次' in failed['error'] and 'SECRET' not in json.dumps(failed)
    assert not catalogs.cached_catalog(CONFIG)['error']
    updated = {**CATALOG, 'options': CATALOG['options'][1:]}
    monkeypatch.setattr(catalogs, 'codex_catalog', lambda config: deepcopy(updated))
    catalogs.safe_catalog(CONFIG)
    assert catalogs.cached_catalog(CONFIG)['options'] == updated['options']


@pytest.mark.parametrize('contents', ['{broken', '[]', '{"version": 1, "catalog": {"options": [null]}}'])
def test_invalid_cache_does_not_break_settings(monkeypatch, contents):
    path = catalogs._cache_path(CONFIG)
    path.parent.mkdir(parents=True)
    path.write_text(contents)
    assert catalogs.cached_catalog(CONFIG)['options'] == []
    monkeypatch.setattr(catalogs, 'codex_catalog', lambda config: deepcopy(CATALOG))
    catalogs.safe_catalog(CONFIG)
    assert catalogs.cached_catalog(CONFIG)['options'] == CATALOG['options']


def test_settings_api_restores_catalog_without_cli_refresh(tmp_path, monkeypatch):
    from test_api import _setup_agent_api_fixture
    app, client, *_ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(app, 'deepseek_settings_snapshot', lambda *args: {'service': {}, 'catalog': {}})
    monkeypatch.setattr(app, 'pi_chat_settings_snapshot', lambda *args: {'service': {}, 'catalog': {}})
    calls = []
    def fetch(config):
        calls.append(config)
        return deepcopy(CATALOG)
    monkeypatch.setattr(catalogs, 'codex_catalog', fetch)
    initial = client.get('/api/settings').json
    assert not initial['model_catalogs']['codex']['options'] and not calls
    response = client.post('/api/settings/models', json={'runtime': 'codex', 'codex': CONFIG})
    assert response.status_code == 200
    for _ in range(2):
        restored = client.get('/api/settings').json
        assert restored['model_catalogs']['codex']['options'] == CATALOG['options']
        assert restored['agent'] == initial['agent']
    assert len(calls) == 1
