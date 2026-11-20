"""Synthetic app-server for exercising the installed OAR Codex adapter."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

if '--version' in sys.argv:
    print('codex-cli 0.167.2')
    sys.exit()
if '--help' in sys.argv:
    print('app-server --listen stdio://')
    sys.exit()

thread_id = '0199a213-81c0-7800-8aa1-bbab2a035a53'
mode = os.environ.get('NR_RPC_MODE', 'success')


def send(value):
    print(json.dumps(value), flush=True)


def note(method, **params):
    send({'method': method, 'params': params})


for line in sys.stdin:
    message = json.loads(line)
    method, params = message.get('method'), message.get('params', {})
    if 'id' not in message:
        continue
    result = {}
    if method == 'initialize':
        result = {'userAgent': 'codex-cli/0.167.2'}
    elif method == 'config/read':
        result = {'config': {'developer_instructions': 'Fixture developer instructions.'}}
    elif method in ('thread/start', 'thread/resume'):
        thread_id = params.get('threadId', thread_id)
        result = {'thread': {'id': thread_id}, 'model': params.get('model', 'fixture'),
                  'reasoningEffort': params.get('config', {}).get('model_reasoning_effort', 'high')}
    elif method == 'turn/start':
        result = {'turn': {'id': 'turn-fixture'}}
    with open(os.environ['NR_RPC_LOG'], 'a') as output:
        output.write(json.dumps({'method': method, 'params': params, 'args': sys.argv[1:], 'pid': os.getpid()}) + '\n')
    send({'id': message['id'], 'result': result})
    if method == 'turn/start':
        note('turn/started', threadId=thread_id, turn={'id': 'turn-fixture', 'status': 'inProgress'})
        if mode == 'hang':
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            child = subprocess.Popen([sys.executable, '-c',
                'import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)'], start_new_session=True)
            Path(os.environ['NR_RPC_PIDS']).write_text(json.dumps([os.getpid(), child.pid]))
            time.sleep(60)
        elif mode == 'auth':
            note('turn/completed', threadId=thread_id,
                 turn={'id': 'turn-fixture', 'status': 'failed',
                       'error': {'message': 'DO_NOT_EXPOSE', 'codexErrorInfo': 'unauthorized'}})
        else:
            if mode in ('commentary', 'commentary_only', 'revised_answer'):
                note('item/completed', threadId=thread_id, turnId='turn-fixture',
                     item={'id': 'progress', 'type': 'agentMessage',
                           'text': '正在整理对话中的结论。' * 40, 'phase': 'commentary'})
            if mode == 'revised_answer':
                note('item/started', threadId=thread_id, turnId='turn-fixture',
                     item={'id': 'answer', 'type': 'agentMessage', 'text': '', 'phase': 'final_answer'})
                note('item/agentMessage/delta', threadId=thread_id, turnId='turn-fixture',
                     itemId='answer', delta='尚未定稿的长文本。' * 40)
            note('item/started', threadId=thread_id, turnId='turn-fixture',
                 item={'id': 'search', 'type': 'webSearch', 'query': 'private query'})
            note('item/completed', threadId=thread_id, turnId='turn-fixture',
                 item={'id': 'search', 'type': 'webSearch', 'status': 'completed'})
            if mode != 'commentary_only':
                answer = 'OAR native transport answer'
                if params.get('outputSchema'):
                    if mode == 'oversized_archive':
                        answer = '长' * (params['outputSchema']['properties']['summary']['maxLength'] + 1)
                    if mode == 'empty_archive':
                        answer = ''
                    answer = json.dumps({'summary': answer}, ensure_ascii=False)
                    if mode == 'malformed_archive':
                        answer = 'not structured output'
                note('item/completed', threadId=thread_id, turnId='turn-fixture',
                     item={'id': 'answer', 'type': 'agentMessage', 'text': answer, 'phase': 'final_answer'})
            note('turn/completed', threadId=thread_id, turn={'id': 'turn-fixture', 'status': 'completed'})
