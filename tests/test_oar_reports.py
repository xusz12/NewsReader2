import json
import os
from pathlib import Path

import pytest

import report_model
from report_workspace import Workspace, TOOLS
from test_report_generation import source, Checkpoints
from test_oar_codex_native import native_runtime
from test_oar_pi_news import pi_runtime
from test_oar_codex import runtime as codex_transport
from test_oar_assistant import assert_stopped_processes


def scripted_report(config):
    snapshot = {'kind': 'evening', 'sources': [source(1, '项目获批'), source(2, '融资仍待落实', body='尚未到账')]}
    preview = Workspace(snapshot, config)
    review = {'batch_id': 'B0001', 'events': [{'text': '建设与融资', 'refs': ['N00001', 'N00002']}], 'omitted': []}
    preview.invoke('report_review', review)
    plan = {'parts': [{'id': 'main', 'title': '项目进展', 'event_ids': ['B0001:E0001']}], 'omitted_events': []}
    revision = preview.invoke('report_plan', plan)['revision']
    actions = [('report_status', {}), ('report_sources', {}), ('report_review', review),
        ('report_events', {}), ('report_plan', plan), ('report_source', {'ref': 'N00002'}),
        ('report_draft', {'part_id': 'main', 'revision': revision,
                         'briefs': [{'text': '项目获批但融资尚未到账。', 'refs': ['N00001', 'N00002']}]}),
        ('report_draft', {'part_id': 'main'}), ('report_finish', {'revision': revision, 'audit': '核对批准与到账，回读完整草稿'})]
    return snapshot, [('mcp__newsreader_report__' + name, args) for name, args in actions]


def generate(config):
    from report_workspace import generate_workspace
    snapshot, _ = scripted_report(config)
    result = generate_workspace(snapshot, config, report_model.run, Checkpoints(), lambda *a, **kw: None)
    assert result['briefs'][0]['text'] == '项目获批但融资尚未到账。'
    assert len(result['sources']) == 2
    assert result['generation']['calls'][0]['transport'] == 'oar'
    assert result['generation']['calls'][0]['outcome'] == 'completed'
    return result


def test_installed_pi_report_agent_executes_private_mcp_and_finishes_workspace(pi_runtime, monkeypatch):
    source_dir, cwd, state, request, calls, responses, run = pi_runtime
    env = {'PATH': os.environ['PATH'], 'HOME': str(source_dir.parent), 'PI_OFFLINE': '1',
           'NEWS_READER_PI_AGENT_DIR': str(source_dir)}
    monkeypatch.setattr(report_model, '_pi_subprocess_env', lambda: env)
    marker = source_dir.parent / 'unexpected-extension'
    (source_dir / 'extensions').mkdir()
    (source_dir / 'extensions/fixture.ts').write_text(
        "import fs from 'node:fs';export default function(){fs.writeFileSync(" + json.dumps(str(marker)) + ", 'loaded');}")
    config = {'runtime': 'pi', 'provider': 'nr-fixture', 'model': 'fixture',
              'limits': {'input_tokens': 16000, 'output_tokens': 2048}}
    snapshot, actions = scripted_report(config)
    responses.extend(actions)
    generate(config)
    assert len(calls) == len(actions) + 1
    assert not marker.exists()
    names = {'mcp__newsreader_report__' + tool['name'] for tool in TOOLS}
    assert all({tool['function']['name'] for tool in call.get('tools', [])} == names for call in calls)
    assert all((call.get('max_tokens') or call.get('max_completion_tokens')) == 2048 for call in calls)
    assert '尚未到账' in json.dumps(calls[-1], ensure_ascii=False)
    assert not list(source_dir.rglob('*.jsonl'))


@pytest.mark.parametrize('workspace', [False, True])
def test_installed_codex_reports_keep_model_tools_and_ephemeral_policy(native_runtime, monkeypatch, workspace):
    command, request, env, calls, native_home, selected = native_runtime
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    config = {'runtime': 'codex', 'codex': selected, '_delegation': False,
              'limits': {'input_tokens': 16000, 'output_tokens': 2048}}
    if workspace:
        snapshot, actions = scripted_report(config)
        calls.responses.extend(actions)
        generate(config)
    else:
        assert report_model.run(config, '只处理本期材料', timeout=25) == 'Local response 1'
    names = {tool['name'] for tool in TOOLS}
    for call in calls:
        assert call['model'] == selected['model'] and call['reasoning']['effort'] == selected['reasoning_effort']
        assert call.get('service_tier') != 'priority'
        tools = call.get('tools', [])
        assert not any(tool.get('type', '').startswith('web_search') for tool in tools)
        for tool in tools:
            if tool.get('type') == 'namespace':
                assert tool['name'] == 'mcp__newsreader_report'
                assert {item['name'] for item in tool['tools']} == names
            else:
                assert tool.get('name') in {'tool_search', 'list_mcp_resources', 'list_mcp_resource_templates',
                                            'read_mcp_resource', 'request_user_input'}
        if workspace:
            assert any(tool.get('name') == 'mcp__newsreader_report' for tool in tools)
    assert not list((native_home / 'sessions').rglob('*.jsonl'))


@pytest.mark.parametrize('reason', ['timeout', 'stop'])
def test_report_cancel_reaps_owned_oar_process_tree(codex_transport, monkeypatch, reason):
    command, request, env = codex_transport
    for name, value in {**env, 'NR_RPC_MODE': 'hang'}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(report_model.codex_client, 'require_ready', lambda: None)
    monkeypatch.setattr(report_model, 'codex_restrictions', lambda *args: [])
    pids = Path(env['NR_RPC_PIDS'])
    config = {'runtime': 'codex', 'codex': request['codex'], '_cancelled': pids.exists if reason == 'stop' else lambda: False}
    with pytest.raises(RuntimeError, match='report_model_timeout' if reason == 'timeout' else 'report_interrupted'):
        report_model.run(config, 'fixture', timeout=3)
    assert pids.exists()
    assert_stopped_processes(pids)
