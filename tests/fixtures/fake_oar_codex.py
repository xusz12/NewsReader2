"""Application tests exercise NR's OAR protocol; vendor transport has its own tests."""
import json
import os
import sys
import time

request = json.load(sys.stdin)
mode = os.environ.get('CODEX_TEST_MODE', 'success').removeprefix('diagnostics_')
native_id = request.get('resume') or '0199a213-81c0-7800-8aa1-bbab2a035a53'
prompt = request['resume_prompt'] if request.get('resume') else request['prompt']
with open(os.environ['CODEX_TEST_LOG'], 'a') as output:
    output.write(json.dumps({**request, 'prompt': prompt, 'transport': 'oar', 'args': sys.argv[1:],
                             'cwd': os.getcwd()}) + '\n')


def emit(value):
    print(json.dumps(value), flush=True)


emit({'type': 'oar_session', 'runtime': 'oar-codex', 'id': native_id})
if mode == 'auth':
    emit({'type': 'oar_result', 'outcome': 'failed', 'error': 'codex_auth_required'})
    sys.exit(1)
if mode == 'runtime_error':
    print('private DO_NOT_EXPOSE', file=sys.stderr)
    emit({'type': 'oar_result', 'outcome': 'failed', 'error': 'codex_turn_failed'})
    sys.exit(1)
if mode == 'malformed':
    print('not json', flush=True)
    sys.exit()
if mode == 'unknown':
    emit({'type': 'unknown'})
    sys.exit()
emit({'type': 'text_delta', 'delta': 'partial'})
if mode == 'hang':
    time.sleep(20)
    sys.exit()
if mode == 'missing':
    sys.exit()
result = ({'type': 'oar_result', 'outcome': 'failed', 'error': 'codex_empty_answer'} if mode == 'empty' else
          {'type': 'oar_result', 'outcome': 'completed', 'answer': '有效最终结论', 'native_id': native_id})
emit(result)
if mode in ('duplicate', 'duplicate_item'):
    emit(result)
if mode == 'nonzero':
    sys.exit(1)
