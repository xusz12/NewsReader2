"""Synthetic search MCP; never connects to a public service."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

for line in sys.stdin:
    message = json.loads(line)
    if 'id' not in message:
        continue
    method = message['method']
    result = {}
    if method == 'initialize':
        result = {'protocolVersion': message['params']['protocolVersion'], 'capabilities': {'tools': {}},
                  'serverInfo': {'name': 'nr-synthetic-search', 'version': '1'}}
    elif method == 'tools/list':
        result = {'tools': [{'name': name, 'description': 'Synthetic web search fixture',
            'inputSchema': {'type': 'object', 'properties': {('url' if name == 'web_fetch' else 'query'): {'type': 'string'}}, 'required': ['url' if name == 'web_fetch' else 'query']}}
            for name in (['web_search', 'web_fetch'] if '--managed' in sys.argv else ['web_search', 'hidden_tool'])]}
    elif method == 'tools/call':
        if '--fail' in sys.argv:
            print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': {'isError': True, 'content': [{'type': 'text', 'text': 'Search quota exhausted'}]}}), flush=True)
            continue
        if '--evidence' in sys.argv:
            with Path(sys.argv[sys.argv.index('--evidence') + 1]).open('a') as stream:
                stream.write(message['params']['name'] + '\n')
        if '--pid-file' in sys.argv or (len(sys.argv) > 1 and not sys.argv[1].startswith('--')):
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            child = subprocess.Popen([sys.executable, '-c',
                'import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)'], start_new_session=True)
            Path(sys.argv[sys.argv.index('--pid-file') + 1] if '--pid-file' in sys.argv else sys.argv[1]).write_text(json.dumps([os.getpid(), child.pid]))
            time.sleep(60)
        result = {'content': [{'type': 'text', 'text': 'synthetic-search-evidence https://example.test/news'}]}
    print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': result}), flush=True)
