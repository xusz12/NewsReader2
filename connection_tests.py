"""Independent ephemeral connection checks: no application records or resumed chats."""
from __future__ import annotations
from datetime import datetime, timezone
import json
import os
import selectors
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
import re
import codex_client
from oar_client import terminate_group

LOCK = threading.Lock()
MAX_BYTES = 1024 * 1024
TIMEOUT = 45
class Busy(Exception): pass

def classify(text):
    # Explicit structured upstream codes, or complete established diagnostic lines.
    # Never infer quota/network/application blame from isolated English words.
    codes = {'insufficient_quota':'quota', 'quota_exceeded':'quota',
             'rate_limit_exceeded':'rate', 'rate_limit_error':'rate',
             'overloaded_error':'capacity', 'service_overloaded':'capacity',
             'invalid_api_key':'credentials', 'authentication_error':'credentials',
             'model_not_found':'model', 'model_not_supported':'model',
             'context_length_exceeded':'input'}
    for line in str(text).splitlines():
        try:
            data=json.loads(line)
            error=data.get('error', data) if isinstance(data,dict) else {}
            if isinstance(error,dict):
                for key in ('code','type'):
                    if error.get(key) in codes: return codes[error[key]]
        except (ValueError,TypeError): pass
        value=line.strip().lower()
        if value in ('401 unauthorized', 'not authenticated', 'invalid api key', 'no api key'): return 'credentials'
        if value in ('capacity exceeded', 'service overloaded'): return 'capacity'
        if value in ('model not found', 'model not supported', 'unknown model'): return 'model'
        if value in ('econnreset','econnrefused','enotfound','enetunreach','connection refused','connection reset','network unreachable'): return 'connection'
        if value in ('timed out','etimedout'): return 'timeout'
    return 'unknown'

def pi_command(config):
    for key,limit in [('provider',40),('model',120)]:
        v=config.get(key,'')
        if not isinstance(v,str) or not v or len(v)>limit or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]*',v):
            raise ValueError('pi_model_not_selected')
    executable = shutil.which(os.environ.get('NEWS_READER_PI') or 'pi')
    if not executable: raise RuntimeError('cli_missing')
    # Capability check fails closed on older versions; do not silently drop flags.
    help_result = subprocess.run([executable,'--help'],capture_output=True,text=True,timeout=5)
    required = ['--no-tools','--no-mcp','--no-extensions','--no-session','--no-skills','--no-prompt-templates']
    if help_result.returncode or not all(flag in help_result.stdout for flag in required):
        raise RuntimeError('no_tool_mode_unsupported')
    return [executable,'-p','--mode','json',*required,'--provider',config['provider'],'--model',config['model'],
            '--system-prompt','Answer only the connection-check prompt. No tools are available.']

def run_pi(config):
    command = pi_command(config)
    with tempfile.TemporaryDirectory(prefix='newsreader-connect-') as cwd:
        # Empty cwd avoids project resources; own login remains CLI-managed.
        proc = subprocess.Popen(command,cwd=cwd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                start_new_session=True)
        sel=selectors.DefaultSelector()
        sel.register(proc.stdout,selectors.EVENT_READ,'out');sel.register(proc.stderr,selectors.EVENT_READ,'err')
        proc.stdin.write(b'Reply with exactly NEWSREADER_CONNECTION_OK.\n');proc.stdin.close()
        out=bytearray(); err=bytearray(); end=time.monotonic()+TIMEOUT
        try:
            while sel.get_map():
                if time.monotonic()>end: raise RuntimeError('timeout')
                for key,_ in sel.select(.1):
                    chunk=os.read(key.fileobj.fileno(),65536)
                    if not chunk: sel.unregister(key.fileobj);continue
                    (out if key.data=='out' else err).extend(chunk)
                    if len(out)+len(err)>MAX_BYTES: raise RuntimeError('unknown')
            code=proc.wait(timeout=max(.01,end-time.monotonic()))
            ended=False;answer='';failure='';tool=False
            for line in out.decode('utf8').splitlines():
                e=json.loads(line)
                if e.get('type')=='tool_execution_start': tool=True
                if e.get('type')=='agent_end': ended=True
                m=e.get('message',{})
                if e.get('type')=='message_end' and m.get('role')=='assistant':
                    answer=''.join(c.get('text','') for c in m.get('content',[]) if c.get('type')=='text')
                    if m.get('stopReason') in ('error','aborted'):failure=m.get('errorMessage','unknown')
                    if any(c.get('type')=='toolCall' for c in m.get('content',[])):tool=True
            if tool: raise RuntimeError('tool_mode_violation')
            if code or failure: raise RuntimeError(classify(failure+' '+err.decode('utf8',errors='replace')))
            if not ended or answer.strip()!='NEWSREADER_CONNECTION_OK': raise RuntimeError('invalid_answer')
        finally:
            terminate_group(proc);sel.close();proc.stdout.close();proc.stderr.close()

def test_connection(payload):
    if not isinstance(payload,dict) or payload.get('runtime') not in ('pi','codex'):
        raise ValueError('invalid_agent_runtime')
    if not LOCK.acquire(blocking=False): raise Busy()
    start=time.monotonic()
    try:
        category='';status='success'
        if payload['runtime']=='codex':
            config = codex_client.normalize_config(payload.get('codex',{}))
            try:
                answer, _ = codex_client.run_once(config, 'hi', timeout=TIMEOUT, ephemeral=True, finish_on_completion=True)
                if not isinstance(answer, str) or not answer.strip():
                    category='invalid_answer';status='failed'
            except RuntimeError as exc:
                category={'codex_missing':'cli_missing', 'codex_auth_required':'credentials',
                          'codex_timeout':'timeout', 'codex_empty_answer':'invalid_answer',
                          'codex_protocol_error':'invalid_answer', 'codex_probe_failed':'cli_incompatible'}.get(str(exc),'unknown')
                status='failed'
            except Exception:
                category='unknown';status='failed'
        else:
            try: run_pi(payload.get('pi',{}))
            except ValueError: raise
            except (subprocess.TimeoutExpired,TimeoutError): category='timeout';status='failed'
            except RuntimeError as exc: category=str(exc);status='failed'
            except Exception: category='unknown';status='failed'
        labels={'no_tool_mode_unsupported':'当前版本暂不支持无工具连接测试。','credentials':'凭据或登录被服务拒绝。',
                'quota':'服务明确报告账户额度不足。','rate':'服务限制请求频率，不等于额度耗尽。','input':'输入超出服务限制。','connection':'服务连接未建立或断开。','capacity':'服务容量或请求频率受限。','model':'模型不可用或不受支持。',
                'timeout':'连接测试超时。','invalid_answer':'未收到有效测试答案。','cli_missing':'未找到 CLI。','cli_incompatible':'CLI能力检查失败，请检查本机版本和配置。',
                'tool_mode_violation':'无工具模式验证失败，未报告连接成功。','unknown':'测试失败，原因尚不能确定。'}
        return {'status':status,'category':category,'message':'本次模型请求成功；不代表后续稳定或网页工具可用。' if not category else labels.get(category,labels['unknown']),
                'elapsed_ms':round((time.monotonic()-start)*1000),'checked_at':datetime.now(timezone.utc).isoformat()}
    finally: LOCK.release()
