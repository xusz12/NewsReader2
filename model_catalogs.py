"""Read-only CLI model metadata. Catalog defaults never override native defaults."""
from __future__ import annotations
import json
import os
from pathlib import Path
import selectors
import subprocess
import time
import codex_client
from oar_client import terminate_group

MAX_BYTES = 2 * 1024 * 1024

class Rpc:
    def __init__(self, command, timeout=15):
        self.deadline = time.monotonic() + timeout
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
        self.sel = selectors.DefaultSelector()
        self.sel.register(self.proc.stdout, selectors.EVENT_READ)
        self.buf = b''
        self.seq = 0
        self.total = 0
    def close(self):
        terminate_group(self.proc)
        self.sel.close()
        for stream in (self.proc.stdin, self.proc.stdout):
            stream.close()
    def send(self, obj):
        self.proc.stdin.write(json.dumps(obj).encode()+b'\n'); self.proc.stdin.flush()
    def call(self, method, params):
        self.seq += 1
        self.send({'id':self.seq,'method':method,'params':params})
        while time.monotonic() < self.deadline:
            while b'\n' in self.buf:
                line, self.buf = self.buf.split(b'\n',1)
                x = json.loads(line)
                if x.get('id') == self.seq:
                    if 'error' in x: raise RuntimeError('catalog_request_failed')
                    return x['result']
            if self.sel.select(min(0.2,max(0,self.deadline-time.monotonic()))):
                chunk = os.read(self.proc.stdout.fileno(),65536)
                if not chunk: raise RuntimeError('catalog_incomplete')
                self.total += len(chunk)
                if self.total > MAX_BYTES: raise RuntimeError('catalog_too_large')
                self.buf += chunk
        raise RuntimeError('catalog_timeout')

def codex_catalog(config):
    c = codex_client.normalize_config(config)
    command = [codex_client.executable(),'app-server','--stdio']
    if c['profile']: command[1:1] = ['--profile', c['profile']]
    if c.get('provider'): command += ['-c','model_provider='+json.dumps(c['provider'])]
    rpc = Rpc(command)
    try:
        rpc.call('initialize',{'clientInfo':{'name':'newsreader-models','version':'1'}})
        rpc.send({'method':'initialized'})
        cursor = None; seen = set(); models = []
        for _ in range(50):
            result = rpc.call('model/list',{'limit':100, **({'cursor':cursor} if cursor else {})})
            if not isinstance(result.get('data'),list): raise RuntimeError('catalog_invalid')
            for m in result['data']:
                value = m.get('model') or m.get('id')
                if not isinstance(value,str) or not value or len(value)>120: continue
                efforts = [e['reasoningEffort'] for e in m.get('supportedReasoningEfforts',[])
                           if isinstance(e,dict) and e.get('reasoningEffort') in codex_client.REASONING_EFFORTS]
                models.append({'value':value,'label':value,'reasoning_efforts':efforts,
                               'default_reasoning':m.get('defaultReasoningEffort','')})
            cursor = result.get('nextCursor')
            if not cursor: break
            if cursor in seen: raise RuntimeError('catalog_pagination_invalid')
            seen.add(cursor)
        else: raise RuntimeError('catalog_too_many_pages')
        return {'source':'codex-app-server','options':models,'error':'',
                'notice':'模型目录仅提供候选和能力，不证明当前服务商可调用。',
                'default_label':'沿用本机默认','resolved_default_model':''}
    finally: rpc.close()

def safe_catalog(config):
    try: return codex_catalog(config)
    except Exception:
        # Never expose raw provider errors, config values, tokens or URLs.
        return {'source':'unavailable','options':[],'error':'模型目录读取失败，请检查本机配置或重试。',
                'default_label':'沿用本机默认','resolved_default_model':''}

def validate_effort(config):
    c = codex_client.normalize_config(config)
    if not c.get('reasoning_effort'): return c
    # Unknown models (including inherited native selection) cannot claim known capabilities.
    catalog = safe_catalog(c)
    model = next((m for m in catalog['options'] if m['value']==c['model']),None)
    if not model or c['reasoning_effort'] not in model['reasoning_efforts']:
        raise ValueError('unsupported_reasoning_effort')
    return c
