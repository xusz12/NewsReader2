"""CLI model metadata with a local cache. Catalogs never override native defaults."""
from __future__ import annotations
import json
import hashlib
import os
from pathlib import Path
import selectors
import subprocess
import time
import tempfile
import codex_client
from settings import resolve_app_settings_path
from oar_client import terminate_group

MAX_BYTES = 2 * 1024 * 1024


def _cache_path(config):
    c = codex_client.normalize_config(config)
    context = [c.get('provider', ''), c['profile'],
               str(Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex').expanduser().resolve()),
               os.environ.get('NEWS_READER_CODEX') or 'codex']
    key = hashlib.sha256(json.dumps(context).encode()).hexdigest()
    return resolve_app_settings_path().expanduser().with_suffix('.model-catalogs') / f'{key}.json'


def _valid_catalog(catalog):
    if not isinstance(catalog, dict) or catalog.get('error') or not isinstance(catalog.get('options'), list):
        return False
    return all(isinstance(option, dict) and isinstance(option.get('value'), str)
               and 0 < len(option['value']) <= 120
               and isinstance(option.get('reasoning_efforts'), list)
               and all(isinstance(effort, str) and effort in codex_client.REASONING_EFFORTS
                       for effort in option['reasoning_efforts'])
               for option in catalog['options'])


def cached_catalog(config):
    """Restore model metadata without starting the CLI or reading credentials."""
    try:
        path = _cache_path(config)
        if path.stat().st_size <= MAX_BYTES:
            record = json.loads(path.read_text(encoding='utf-8'))
            if record.get('version') == 1 and _valid_catalog(record.get('catalog')):
                return {**record['catalog'], 'cached': True, 'cached_at': record.get('saved_at', '')}
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return {'source': 'unavailable', 'options': [], 'error': '', 'cached': False,
            'default_label': '沿用本机默认', 'resolved_default_model': ''}


def _save_catalog(config, catalog):
    path = _cache_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Store only the public metadata used by model selection, never CLI output.
    metadata = {key: catalog[key] for key in ('source', 'options', 'error', 'notice',
                'default_label', 'resolved_default_model') if key in catalog}
    record = {'version': 1, 'saved_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'catalog': metadata}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(record, stream, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()

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
    try:
        catalog = codex_catalog(config)
        if not _valid_catalog(catalog):
            raise ValueError('catalog_invalid')
    except Exception:
        # Never expose raw provider errors, config values, tokens or URLs.
        previous = cached_catalog(config)
        message = ('模型目录刷新失败，已保留上次获取的目录。' if previous['cached']
                   else '模型目录读取失败，请检查本机配置或重试。')
        return {**previous, 'error': message}
    try:
        _save_catalog(config, catalog)
    except OSError:
        return {**catalog, 'notice': '模型目录已获取，但无法保存到本地；请检查配置目录的写入权限。'}
    return catalog

def validate_effort(config):
    c = codex_client.normalize_config(config)
    if not c.get('reasoning_effort'): return c
    # Unknown models (including inherited native selection) cannot claim known capabilities.
    catalog = safe_catalog(c)
    model = next((m for m in catalog['options'] if m['value']==c['model']),None)
    if not model or c['reasoning_effort'] not in model['reasoning_efforts']:
        raise ValueError('unsupported_reasoning_effort')
    return c
