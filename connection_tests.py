"""Independent ephemeral connection checks: no application records or resumed chats."""
from __future__ import annotations
from datetime import datetime, timezone
import threading
import time
import codex_client
import pi_models
import runtime_tasks

LOCK = threading.Lock()
TIMEOUT = 45
class Busy(Exception): pass

def test_connection(payload):
    if not isinstance(payload, dict) or payload.get('runtime') not in ('pi', 'codex'):
        raise ValueError('invalid_agent_runtime')
    if not LOCK.acquire(blocking=False):
        raise Busy()
    start = time.monotonic()
    try:
        runtime = payload['runtime']
        selected = (codex_client.normalize_config(payload.get('codex', {})) if runtime == 'codex'
                    else pi_models.validate_effort(payload.get('pi', {})))
        if runtime == 'pi' and (not selected['provider'] or not selected['model']):
            raise ValueError('pi_model_not_selected')
        category = ''
        try:
            answer = runtime_tasks.run({'runtime': runtime, runtime: selected},
                'Reply with exactly NEWSREADER_CONNECTION_OK.', task='connection_test', timeout=TIMEOUT)
            if answer.strip() != 'NEWSREADER_CONNECTION_OK':
                category = 'invalid_answer'
        except RuntimeError as exc:
            category = {'codex_missing': 'cli_missing', 'codex_probe_failed': 'cli_incompatible',
                'oar_quota': 'quota', 'oar_rate_limited': 'rate', 'oar_input_too_large': 'input', 'oar_overloaded': 'capacity',
                'codex_auth_required': 'credentials', 'oar_auth_required': 'credentials',
                'oar_timeout': 'timeout', 'codex_timeout': 'timeout',
                'oar_empty_answer': 'invalid_answer', 'oar_incomplete_answer': 'invalid_answer',
                'oar_protocol_error': 'invalid_answer', 'codex_protocol_error': 'invalid_answer',
                'codex_empty_answer': 'invalid_answer', 'oar_model_unavailable': 'model',
                'oar_node_missing': 'dependencies', 'oar_node_version': 'dependencies',
                'oar_dependency_missing': 'dependencies', 'oar_compatibility_mismatch': 'dependencies',
                'oar_tools_not_allowed': 'tool_mode_violation'}.get(str(exc), 'unknown')
        except Exception:
            category = 'unknown'
        labels = {'credentials': '凭据或登录被服务拒绝。', 'model': '模型不可用或不受支持。',
            'quota': '服务明确报告账户额度不足。', 'rate': '服务限制请求频率，不等于额度耗尽。',
            'input': '输入超出服务限制。', 'capacity': '服务容量暂不可用。',
            'timeout': '连接测试超时。', 'invalid_answer': '未收到有效测试答案。',
            'cli_missing': '未找到 Codex CLI。', 'cli_incompatible': 'Codex app-server 能力检查失败，请检查本机版本和配置。',
            'dependencies': 'OAR 运行依赖不可用，请检查 Node.js、项目依赖及 runtime 版本。',
            'tool_mode_violation': '无工具模式验证失败，未报告连接成功。',
            'unknown': '测试失败，原因尚不能确定。'}
        return {'status': 'failed' if category else 'success', 'category': category,
            'message': labels[category] if category else '本次模型请求成功。',
            'elapsed_ms': round((time.monotonic()-start)*1000), 'checked_at': datetime.now(timezone.utc).isoformat()}
    finally:
        LOCK.release()
