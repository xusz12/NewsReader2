"""Translation catalog regressions; no real credentials or provider requests."""
import pytest
from test_api import _setup_agent_api_fixture


@pytest.mark.parametrize('saved_model', ['', 'deepseek-custom'])
def test_no_credentials_has_no_default_model_candidates(tmp_path, monkeypatch, saved_model):
    app, client, *_ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    monkeypatch.setattr(app, 'has_secret', lambda name: False)
    monkeypatch.setattr(app, 'urlopen', lambda *args, **kwargs: pytest.fail('No model lookup without credentials'))
    snapshot = app.deepseek_settings_snapshot(saved_model)
    assert snapshot['service']['configured'] is False
    assert snapshot['service']['used_fallback'] is False
    assert snapshot['catalog']['source'] == 'unavailable'
    options = snapshot['catalog']['options']
    assert [option['value'] for option in options] == ([saved_model] if saved_model else [])
    assert all(option['source'] == 'saved' for option in options)
    assert client.post('/api/settings/translation/models', json={}).json['error'] == 'missing_api_key'


def test_removing_credentials_clears_candidates(tmp_path, monkeypatch):
    app, client, *_ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    secrets = {'DEEPSEEK_API_KEY': 'sk-isolated-test-key'}
    monkeypatch.setattr(app, 'has_secret', lambda name: name in secrets)
    monkeypatch.setattr(app, 'read_secret', lambda name: secrets.get(name, ''))
    monkeypatch.setattr(app, 'delete_secret', lambda name: secrets.pop(name, None))
    monkeypatch.setattr(app, 'pi_chat_settings_snapshot', lambda *args: {'service': {}, 'catalog': {}})
    monkeypatch.setattr(app, 'urlopen', lambda *args, **kwargs: (_ for _ in ()).throw(app.URLError('offline')))
    assert len(app.deepseek_settings_snapshot('')['catalog']['options']) == 2
    response = client.delete('/api/settings/secrets/deepseek')
    assert response.status_code == 200
    assert response.json['api_status']['deepseek']['configured'] is False
    assert response.json['model_catalogs']['translation']['options'] == []
