"""Real OAR transport tests with a synthetic local Codex app-server."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest
import codex_client
import oar_client

THREAD = '0199a213-81c0-7800-8aa1-bbab2a035a53'


@pytest.fixture
def runtime(tmp_path):
    node = shutil.which('node')
    if not node or not (oar_client.ROOT / 'node_modules/@botiverse/oar').exists():
        pytest.skip('Installed OAR requires npm ci')
    script = tmp_path / 'codex'
    script.write_text(f'#!{sys.executable}\n' + (Path(__file__).parent / 'fixtures/fake_codex_app_server.py').read_text())
    script.chmod(0o755)
    env = {'PATH': os.environ['PATH'], 'HOME': str(tmp_path), 'CODEX_HOME': str(tmp_path / 'codex-home'),
           'NEWS_READER_CODEX': str(script), 'NR_RPC_LOG': str(tmp_path / 'rpc.jsonl'),
           'NR_RPC_PIDS': str(tmp_path / 'pids.json')}
    config = {'model': 'fixture', 'reasoning_effort': 'high', 'sandbox': 'read-only'}
    request = codex_client.oar_request(config, 'private first prompt', state_dir=tmp_path, cwd=tmp_path,
                                       resume_prompt='private follow-up', live_search=True)
    return [node, str(oar_client.ROOT / 'scripts/oar_bridge.mjs')], request, env


def run(runtime, *, timeout=10):
    command, request, env = runtime
    parser = codex_client.OarEventParser(request['resume'])
    result = oar_client.run_result(command, request, env, timeout, parser=parser.parse,
        errors=codex_client.ERROR_MESSAGES, timeout_error='codex_timeout', protocol_error='codex_protocol_error',
        failure_error='codex_turn_failed')
    return result, parser.thread_id


def test_installed_oar_codex_starts_resumes_and_keeps_policy_and_inputs_private(runtime):
    command, request, env = runtime
    assert run(runtime) == ({'type': 'oar_result', 'outcome': 'completed',
        'answer': 'OAR native transport answer', 'native_id': THREAD}, THREAD)
    request['resume'] = THREAD
    assert run(runtime)[1] == THREAD
    calls = [json.loads(line) for line in Path(env['NR_RPC_LOG']).read_text().splitlines()]
    opens = [call for call in calls if call['method'] in ('thread/start', 'thread/resume')]
    assert [call['method'] for call in opens] == ['thread/start', 'thread/resume']
    assert opens[1]['params']['threadId'] == THREAD
    for call in opens:
        assert call['params']['approvalPolicy'] == 'never'
        assert call['params']['model'] == 'fixture'
        args = call['args']
        assert 'sandbox_mode="read-only"' in args and 'service_tier="default"' in args
        assert 'features.fast_mode=false' in args and 'web_search="live"' in args
        assert 'private' not in json.dumps(args)
        assert '--profile' not in args
        assert not any(arg.startswith('model_provider=') for arg in args)
    turns = [call for call in calls if call['method'] == 'turn/start']
    assert [call['params']['input'][0]['text'] for call in turns] == ['private first prompt', 'private follow-up']
    assert not (Path(request['state_dir']) / 'codex-session.json').exists()


def test_installed_oar_codex_auth_failure_is_safe(runtime):
    runtime[2]['NR_RPC_MODE'] = 'auth'
    with pytest.raises(RuntimeError, match='^codex_auth_required$'):
        run(runtime)


@pytest.mark.parametrize('mode', ['commentary', 'revised_answer'])
def test_archive_uses_completed_final_message_not_progress_or_stream_draft(runtime, mode):
    runtime[1].update(task='chat_archive', web_search='disabled')
    runtime[2]['NR_RPC_MODE'] = mode
    result, _ = run(runtime)
    assert result['answer'] == 'OAR native transport answer'
    calls = [json.loads(line) for line in Path(runtime[2]['NR_RPC_LOG']).read_text().splitlines()]
    opened = next(call['params'] for call in calls if call['method'] == 'thread/start')
    configured, host = opened['developerInstructions'].split('\n\n', 1)
    assert configured == 'Fixture developer instructions.'
    assert 'summary' in host


def test_archive_rejects_commentary_without_a_final_answer(runtime):
    runtime[1].update(task='chat_archive', web_search='disabled')
    runtime[2]['NR_RPC_MODE'] = 'commentary_only'
    with pytest.raises(RuntimeError, match='^codex_empty_answer$'):
        run(runtime)


@pytest.mark.parametrize('mode', ['commentary', 'revised_answer', 'commentary_only', 'malformed_archive', 'oversized_archive', 'empty_archive'])
def test_archive_http_saves_only_final_conclusion_and_preserves_conversation(runtime, tmp_path, monkeypatch, mode):
    from api_fixtures import _setup_agent_api_fixture
    from test_chat_archive import store_conversation
    from test_codex_client import select_codex

    app, client, item, *_ = _setup_agent_api_fixture(tmp_path, monkeypatch)
    select_codex(app, monkeypatch, runtime[1]['codex'])
    runtime[2]['NR_RPC_MODE'] = mode
    monkeypatch.setattr(codex_client, 'oar_environment', lambda: runtime[2])
    path = f"/api/news/{item['id']}"
    session_id = store_conversation(app, client, path, [
        {'role': 'user', 'content': '哪些结论值得保留？'},
        {'role': 'assistant', 'content': '需要区分政策利率与实际融资成本，关注银行传导、风险溢价和适用条件。' * 8},
    ])
    before_chat = client.get(path + '/agent/session').json
    assert client.post(path + '/notes', json={'note': '已有研读想法'}).status_code == 200
    before_notes = client.get(path + '/detail').json['notes']
    response = client.post(path + '/chat/archive', json={'agent_session_id': session_id})
    after_notes = client.get(path + '/detail').json['notes']
    errors = {'commentary_only': 'codex_empty_answer', 'malformed_archive': 'codex_protocol_error',
              'oversized_archive': 'invalid_archive_summary', 'empty_archive': 'empty_archive_summary'}
    if mode in errors:
        assert response.status_code == 502
        assert response.json['error'] == errors[mode]
        assert after_notes == before_notes
    else:
        assert response.status_code == 200, response.json
        assert response.json['archive_summary'] == 'OAR native transport answer'
        assert len(after_notes) == len(before_notes) + 1
        assert after_notes[-1]['author'] == 'agent'
        assert after_notes[-1]['archive_summary'] == response.json['archive_summary']
        assert after_notes[-1]['source_session_id'] == session_id
    assert client.get(path + '/agent/session').json == before_chat
    # Long commentary must not trigger a second, unnecessary compression turn.
    calls = [json.loads(line) for line in Path(runtime[2]['NR_RPC_LOG']).read_text().splitlines()]
    turns = [call for call in calls if call['method'] == 'turn/start']
    assert len(turns) == (2 if mode in {'commentary_only', 'oversized_archive', 'empty_archive'} else 1)
    limit = min(300, sum(len(message['content'].strip()) for message in before_chat['messages']))
    assert all(call['params']['outputSchema']['properties']['summary']['maxLength'] == limit for call in turns)


def test_oar_codex_timeout_reaps_detached_app_server_and_its_tool(runtime):
    runtime[2]['NR_RPC_MODE'] = 'hang'
    with pytest.raises(RuntimeError, match='^codex_timeout$'):
        run(runtime, timeout=2)
    pids = json.loads(Path(runtime[2]['NR_RPC_PIDS']).read_text())
    for pid in pids:
        for _ in range(30):
            status = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True).stdout.strip()
            if not status or status.startswith('Z'):
                break
            time.sleep(.03)
        assert not status or status.startswith('Z'), f'Owned process {pid} survived cancellation'


def test_oar_parser_rejects_a_foreign_identity_and_completion_without_identity():
    parser = codex_client.OarEventParser(THREAD)
    with pytest.raises(RuntimeError, match='codex_session_invalid'):
        parser.parse(json.dumps({'type': 'oar_session', 'runtime': 'oar-codex', 'id': '0199a213-81c0-7800-8aa1-bbab2a035a54'}))
    with pytest.raises(RuntimeError, match='codex_protocol_error'):
        codex_client.OarEventParser().parse(json.dumps({'type': 'oar_result', 'outcome': 'completed', 'answer': 'unowned', 'native_id': THREAD}))


@pytest.mark.parametrize('web', [False, True])
def test_summary_runner_uses_installed_oar_and_checks_native_search(runtime, monkeypatch, web):
    import news_summaries
    command, request, env = runtime
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert not hasattr(codex_client, 'command')
    diagnostics = []
    config = {'runtime': 'codex', 'codex': request['codex']}
    if web:
        assert news_summaries.run_oar(config, 'research', lambda: False, diagnostics, allow_research=True) == 'OAR native transport answer'
        assert diagnostics[0]['research_tools'] == 1
    else:
        with pytest.raises(RuntimeError, match='summary_tool_used'):
            news_summaries.run_oar(config, 'materials only', lambda: False, diagnostics)
    calls = [json.loads(line) for line in Path(env['NR_RPC_LOG']).read_text().splitlines()]
    opened = next(c for c in calls if c['method'] == 'thread/start')
    assert opened['params']['ephemeral'] is True
    assert ('web_search="live"' if web else 'web_search="disabled"') in opened['args']
