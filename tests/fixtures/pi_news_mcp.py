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
            'inputSchema': {'type': 'object', 'properties': {'query': {'type': 'string'}}, 'required': ['query']}}
            for name in ['web_search', 'hidden_tool']]}
    elif method == 'tools/call':
        if len(sys.argv) > 1:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            child = subprocess.Popen([sys.executable, '-c',
                'import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)'], start_new_session=True)
            Path(sys.argv[1]).write_text(json.dumps([os.getpid(), child.pid]))
            time.sleep(60)
        result = {'content': [{'type': 'text', 'text': 'synthetic-search-evidence https://example.test/news'}]}
    print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': result}), flush=True)
