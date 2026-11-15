"""The real installed Codex and OAR, against an isolated local Responses server.

No login, user configuration, paid model, or public web request is required.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import threading

import pytest

import codex_client
import oar_client


@pytest.fixture
def native_runtime(tmp_path):
    codex = shutil.which('codex')
    node = shutil.which('node')
    if not codex or not node or not (oar_client.ROOT / 'node_modules/@botiverse/oar').exists():
        pytest.skip('Requires installed Codex, Node and npm dependencies')
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(body)
            answer = f'Local response {len(calls)}'
            if body.get('text', {}).get('format', {}).get('type') == 'json_schema':
                answer = json.dumps({'summary': answer})
            item = {'id': f'msg_{len(calls)}', 'type': 'message', 'role': 'assistant',
                    'status': 'completed', 'phase': 'final_answer',
                    'content': [{'type': 'output_text', 'text': answer, 'annotations': []}]}
            response = {'id': f'resp_{len(calls)}', 'object': 'response', 'status': 'completed',
                        'model': 'nr-fixture', 'output': [item],
                        'usage': {'input_tokens': 10, 'output_tokens': 4, 'total_tokens': 14}}
            events = [
                {'type': 'response.created', 'response': {**response, 'status': 'in_progress', 'output': []}},
                {'type': 'response.output_item.added', 'output_index': 0,
                 'item': {**item, 'status': 'in_progress', 'content': []}},
                {'type': 'response.content_part.added', 'item_id': item['id'], 'output_index': 0,
                 'content_index': 0, 'part': {'type': 'output_text', 'text': '', 'annotations': []}},
                {'type': 'response.output_text.delta', 'item_id': item['id'], 'output_index': 0,
                 'content_index': 0, 'delta': answer},
                {'type': 'response.output_text.done', 'item_id': item['id'], 'output_index': 0,
                 'content_index': 0, 'text': answer},
                {'type': 'response.output_item.done', 'output_index': 0, 'item': item},
                {'type': 'response.completed', 'response': response},
            ]
            data = ''.join(f'event: {event["type"]}\ndata: {json.dumps(event)}\n\n' for event in events).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    native_home = tmp_path / 'codex-home'
    native_home.mkdir()
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
    config_text = f'''model = "nr-fixture"
model_provider = "nr-local"
web_search = "disabled"
[model_providers.nr-local]
name = "Local test only"
base_url = "http://127.0.0.1:{server.server_port}/v1"
wire_api = "responses"
requires_openai_auth = false
'''
    (native_home / 'config.toml').write_text(config_text)
    env = {'PATH': os.environ['PATH'], 'HOME': str(tmp_path), 'CODEX_HOME': str(native_home),
           'NEWS_READER_CODEX': codex}
    config = {'model': 'nr-fixture', 
              'reasoning_effort': 'high', 'sandbox': 'read-only'}
    request = codex_client.oar_request(config, 'first private question', state_dir=tmp_path / 'nr-state',
        cwd=workspace, resume_prompt='follow-up private question', live_search=True)
    try:
        yield [node, str(oar_client.ROOT / 'scripts/oar_bridge.mjs')], request, env, calls, native_home, config
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
    assert (native_home / 'config.toml').read_text() == config_text


@pytest.mark.parametrize('legacy_cli_session', [False, True])
def test_real_codex_new_and_legacy_sessions_resume_through_oar(native_runtime, legacy_cli_session, monkeypatch):
    command, request, env, calls, native_home, config = native_runtime

    def invoke():
        parser = codex_client.OarEventParser(request['resume'])
        result = oar_client.run_result(command, request, env, 25, parser=parser.parse,
            errors=codex_client.ERROR_MESSAGES, timeout_error='codex_timeout',
            protocol_error='codex_protocol_error', failure_error='codex_turn_failed')
        assert result['answer'] == f'Local response {len(calls)}'
        return parser.thread_id

    if legacy_cli_session:
        monkeypatch.setattr(codex_client, 'executable', lambda: env['NEWS_READER_CODEX'])
        result = subprocess.run(codex_client.command(config, cwd=Path(request['cwd']), live_search=True),
            input=request['prompt'], env=env, capture_output=True, text=True, timeout=25)
        assert result.returncode == 0, result.stderr
        native_id = next(json.loads(line)['thread_id'] for line in result.stdout.splitlines()
                         if json.loads(line).get('type') == 'thread.started')
    else:
        native_id = invoke()
    request['resume'] = native_id
    assert invoke() == native_id
    assert len(calls) == 2
    for body in calls:
        assert body['model'] == 'nr-fixture'
        assert body['reasoning']['effort'] == 'high'
        assert body.get('service_tier') != 'priority'
        search = next(tool for tool in body['tools'] if tool['type'].startswith('web_search'))
        assert search.get('external_web_access', True) is True
    second_input = json.dumps(calls[1]['input'])
    assert second_input.count('first private question') == 1
    assert second_input.count('follow-up private question') == 1
    assert 'Local response 1' in second_input
    # Verify native policy in the actual persisted rollout, not only OAR args.
    rollouts = list((native_home / 'sessions').rglob(f'*{native_id}*.jsonl'))
    assert len(rollouts) == 1
    records = [json.loads(line) for line in rollouts[0].read_text().splitlines()]
    contexts = [record['payload'] for record in records if record['type'] == 'turn_context']
    assert contexts and all(context['approval_policy'] == 'never' for context in contexts)
    assert all(context['sandbox_policy']['type'] == 'read-only' for context in contexts)
    assert not (Path(request['state_dir']) / 'codex-session.json').exists()


def test_real_codex_archive_is_ephemeral_and_never_resumes_or_searches(native_runtime):
    command, request, env, calls, native_home, config = native_runtime
    request.update(task='chat_archive', web_search='disabled', archive_max_length=72)
    ids = []
    for _ in range(2):
        parser = codex_client.OarEventParser()
        result = oar_client.run_result(command, request, env, 25, parser=parser.parse,
            errors=codex_client.ERROR_MESSAGES, protocol_error='codex_protocol_error')
        assert result['answer'] == f'Local response {len(calls)}'
        ids.append(parser.thread_id)
    assert ids[0] != ids[1]
    assert not list((native_home / 'sessions').rglob('*.jsonl'))
    assert all(not any(t['type'].startswith('web_search') for t in c.get('tools', [])) for c in calls)
    assert 'Local response 1' not in json.dumps(calls[1]['input'])
    for call in calls:
        output_format = call['text']['format']
        assert output_format['type'] == 'json_schema'
        assert output_format['strict'] is True
        assert output_format['schema'] == {
            'type': 'object', 'properties': {'summary': {'type': 'string', 'maxLength': 72}},
            'required': ['summary'], 'additionalProperties': False,
        }
    assert calls[0]['model'] == config['model']
    assert calls[0]['reasoning']['effort'] == config['reasoning_effort']
