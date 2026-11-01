"""Go replaces the managed pay-as-you-go entry, retaining its key and model."""
from uuid import UUID

import pytest
import model_apis
from test_model_apis import api_fixture, Reply


@pytest.mark.parametrize('go_model,expected', [('', 'deepseek-v4.1-flash'), ('go-selected', 'go-selected')])
def test_legacy_entry_migrates_to_one_subscription_provider(api_fixture, go_model, expected):
    app, client, secrets = api_fixture
    secrets['OPENCODE_API_KEY'] = 'existing-subscription-key'
    initial = app.load_app_settings()
    initial['llm']['translation']['provider'] = 'opencode'
    initial['api_models'] = {'jev': {'model': 'jev-kept'}, 'opencode': {'model': 'deepseek-v4.1-flash'},
                             'opencode-go': {'model': go_model}}
    app.save_app_settings(initial)
    current = client.get('/api/settings').json
    assert [p['id'] for p in current['api_providers']] == ['deepseek', 'jev', 'opencode-go']
    assert current['llm']['translation']['provider'] == 'opencode-go'
    assert current['api_models'] == {'jev': {'model': 'jev-kept'}, 'opencode-go': {'model': expected}}
    assert current['api_status']['opencode-go']['configured'] is True
    assert 'opencode' not in current['api_status']
    assert app.provider_secret_value('opencode-go') == 'existing-subscription-key'
    assert current['llm']['pi_chat'] == initial['llm']['pi_chat']
    assert 'existing-subscription-key' not in str(current)
    # Even a stale client must not reintroduce a pay-as-you-go translation route.
    saved = client.put('/api/settings', json={'llm': {'translation': {'provider': 'opencode'}},
                                            'api_models': {'opencode': {'model': 'older-client-model'}}})
    assert saved.status_code == 200
    assert saved.json['llm']['translation']['provider'] == 'opencode-go'
    assert saved.json['api_models']['opencode-go']['model'] == expected
    assert 'opencode' not in app.load_app_settings()['api_models']
    # Once migrated, clearing the Go model must not resurrect the legacy value.
    cleared = client.put('/api/settings', json={'llm': saved.json['llm'], 'api_models': {'opencode-go': {'model': ''}}})
    assert cleared.status_code == 200 and cleared.json['api_models']['opencode-go']['model'] == ''


def test_go_and_legacy_secret_routes_use_the_existing_key_slot(api_fixture, monkeypatch):
    app, client, secrets = api_fixture
    secrets['OPENCODE_API_KEY'] = 'existing-subscription-key'
    response = client.put('/api/settings/secrets/opencode-go', json={'key': 'new-subscription-key'})
    assert response.status_code == 200
    assert secrets == {'OPENCODE_API_KEY': 'new-subscription-key'}
    assert 'new-subscription-key' not in response.text
    assert app.provider_secret_value('opencode') == 'new-subscription-key'
    monkeypatch.setenv('OPENCODE_API_KEY', 'go-environment-key')
    assert app.provider_secret_value('opencode-go') == 'go-environment-key'
    response = client.delete('/api/settings/secrets/opencode')
    assert response.status_code == 200 and secrets == {}
    assert response.json['api_status']['opencode-go']['environment_override'] is True
    monkeypatch.delenv('OPENCODE_API_KEY')
    assert not app.provider_has_configured_key('opencode-go')


def test_go_catalog_and_test_use_subscription_endpoint_key_and_session(api_fixture, monkeypatch):
    app, client, secrets = api_fixture
    secrets['OPENCODE_API_KEY'] = 'go-key'
    before = app.load_app_settings()
    calls = []

    def request(req, **kwargs):
        calls.append(req)
        assert req.get_header('Authorization') == 'Bearer go-key'
        assert req.get_header('User-agent') == 'news-reader-settings/1.0'
        assert UUID(req.get_header('X-opencode-session').removeprefix('newsreader-')).version == 4
        if req.get_method() == 'GET':
            assert req.full_url == 'https://opencode.ai/inference/go/v1/models'
            return Reply({'data': [{'id': 'deepseek-v4.1-flash', 'api': {'chat-completions': {}}}]})
        assert req.full_url == 'https://opencode.ai/inference/go/openai/v1/chat/completions'
        import json
        assert json.loads(req.data)['model'] == 'deepseek-v4.1-flash'
        return Reply({'choices': [{'message': {'content': 'OK'}}]})

    monkeypatch.setattr(app, 'urlopen', request)
    catalog = client.post('/api/settings/apis/opencode-go/models', json={})
    assert catalog.status_code == 200
    assert [m['value'] for m in catalog.json['catalog']['options']] == ['deepseek-v4.1-flash']
    for provider in ('opencode', 'opencode-go'):
        response = client.post(f'/api/settings/apis/{provider}/test', json={'model': 'deepseek-v4.1-flash'})
        assert response.status_code == 200 and response.json['provider'] == 'opencode-go'
    assert len({req.get_header('X-opencode-session') for req in calls}) == 3
    assert app.load_app_settings() == before  # Tests use a draft model, not a settings write.


@pytest.mark.parametrize('provider', ['opencode', 'opencode-go'])
def test_direct_catalog_excludes_other_api_families_and_free_models(provider):
    payload = {'data': [
        {'id': 'deepseek-v4.1-flash', 'api': {'chat-completions': {}}},
        {'id': 'other-claude', 'api': {'messages': {}}},
        {'id': 'other-gpt', 'api': {'responses': {}}},
        {'id': 'other-gemini', 'api': {'generate-content': {}}},
        {'id': 'some-free', 'api': {'chat-completions': {}}},
        {'id': 'older-catalog-no-metadata'},
    ]}
    assert [m['value'] for m in model_apis.parse_model_options(provider, payload)] == [
        'deepseek-v4.1-flash', 'older-catalog-no-metadata']


def test_go_with_only_filtered_models_is_a_valid_empty_catalog(api_fixture, monkeypatch):
    app, client, secrets = api_fixture
    secrets['OPENCODE_API_KEY'] = 'go-key'
    monkeypatch.setattr(app, 'urlopen', lambda *a, **kw: Reply({'data': [
        {'id': 'some-free'}, {'id': 'other-claude', 'api': {'messages': {}}},
    ]}))
    response = client.post('/api/settings/apis/opencode-go/models', json={})
    assert response.status_code == 200 and response.json['catalog']['options'] == []
