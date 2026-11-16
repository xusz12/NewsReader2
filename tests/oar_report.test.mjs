import test from 'node:test';
import assert from 'node:assert/strict';
import os from 'node:os';
import {validateReport, reportEvents, completedPiReport} from '../scripts/oar_report.mjs';
import {limitPayload} from '../scripts/pi_report_extension.mjs';
import {codexOptions} from '../scripts/oar_codex.mjs';
import {validateRequest} from '../scripts/oar_bridge.mjs';

const request = {task: 'report', prompt: 'private material', resume_prompt: '', cwd: os.tmpdir(),
  state_dir: os.tmpdir(), provider: 'fixture', model: 'chosen', output_tokens: 4096, delegation: false};

test('reports reject live research, history and invalid output budgets before runtime creation', () => {
  validateRequest(request);
  for (const extra of [{resume: 'old'}, {legacy_native_id: 'old'}, {web_search: 'live'},
    {output_tokens: 0}, {output_tokens: '4096'}, {output_tokens: 1000001}, {delegation: true},
    {report_session_id: '../private'}, {report_launch_args: [null]}]) {
    assert.throws(() => validateReport({...request, ...extra}), /oar_invalid_request/);
  }
  const options = codexOptions({...request, runtime: 'codex', web_search: 'disabled',
    codex: {sandbox: 'read-only', model: 'chosen', reasoning_effort: 'ultra'},
    nr_tools: {command: '/usr/bin/python3', args: ['private-report-bridge']}});
  assert.equal(options.nrEphemeral, true);
  assert.equal(options.effort, 'ultra');
  assert.equal(options.mcpServers[0].name, 'newsreader_report');
  assert.ok(options.launchArgs.includes('project_doc_max_bytes=0'));
});

test('report observations retain child tools and delegation but only root usage and no private payloads', () => {
  const emitted = [], session = {rawEvents(fn) {this.raw = fn;}};
  const observer = reportEvents(session, request, event => emitted.push(event));
  observer.event({kind: 'usage', usage: {tokens: {input: 12, output: 4}}});
  observer.event({kind: 'usage', agentPath: ['child'], usage: {tokens: {input: 999}}});
  observer.event({kind: 'tool_call_started', agentPath: ['child'], tool: 'mcpToolCall',
    server: 'newsreader_report', toolName: 'report_review', input: 'private evidence'});
  session.raw({kind: 'frame', body: {type: 'item/completed', native: {item: {
    id: 'spawn-1', type: 'collabAgentToolCall', tool: 'spawn_agent', receiverThreadIds: ['child'],
    prompt: 'private delegation', status: 'completed'}}}});
  assert.deepEqual(observer.metadata().usage, {input: 12, output: 4});
  assert.deepEqual(emitted[0], {type: 'oar_tool', tool: 'mcp__newsreader_report__report_review'});
  assert.equal(emitted[1].phase, 'completed');
  assert.deepEqual(emitted[1].receiver_thread_ids, ['child']);
  observer.event({kind: 'session_linked', parent: 'root', child: 'child', via: 'tool_call'});
  assert.deepEqual(emitted[2], {type: 'oar_report_agent', id: 'child'});
  assert.ok(!JSON.stringify(emitted).includes('private'));
});

test('Pi report takes the final native message and refuses truncated or tool-only completion', () => {
  const frame = (text, stopReason = 'stop', extra = {}) => ({kind: 'frame', sessionId: 'root',
    body: {type: 'message_end', native: {message: {role: 'assistant', stopReason, content: [{type: 'text', text}]}}}, ...extra});
  assert.equal(completedPiReport([frame('progress'), frame('final'), frame('child', 'stop', {agentPath: ['child']})], 'root'), 'final');
  assert.throws(() => completedPiReport([frame('partial', 'length'), frame('later')], 'root'), /report_output_truncated/);
  assert.equal(completedPiReport([frame('partial', 'toolUse')], 'root'), '');
  assert.equal(completedPiReport([frame('error', 'error')], 'root'), '');
  const overBudget = frame('complete');
  overBudget.body.native.message.usage = {output: 4097};
  assert.throws(() => completedPiReport([overBudget], 'root', 4096), /report_output_truncated/);
});

test('provider budgets preserve prompt and tool payloads and reserve space beyond explicit thinking', () => {
  const variants = [
    ['openai-completions', {max_completion_tokens: 32000}, 'max_completion_tokens'],
    ['openai-responses', {max_output_tokens: 32000}, 'max_output_tokens'],
    ['anthropic-messages', {max_tokens: 32000, thinking: {type: 'enabled', budget_tokens: 30000}}, 'max_tokens'],
    ['google-generative-ai', {config: {temperature: .2, tools: ['report-only']}}, 'config'],
    ['google-vertex', {config: {temperature: .2}}, 'config'],
    ['pi-messages', {options: {temperature: .2}}, 'options'],
    ['mistral-conversations', {maxTokens: 32000}, 'maxTokens'],
    ['bedrock-converse-stream', {inferenceConfig: {temperature: .2}}, 'inferenceConfig'],
  ];
  for (const [api, values, field] of variants) {
    const original = {...values, messages: ['given material'], tools: ['report-only']};
    const result = limitPayload(original, 4096, api);
    assert.deepEqual(result.messages, original.messages);
    assert.deepEqual(result.tools, original.tools);
    assert.equal(typeof result[field] === 'number' ? result[field] : result[field].maxOutputTokens ?? result[field].maxTokens, 4096);
    if (result.thinking) assert.ok(result.thinking.budget_tokens < result.max_tokens);
    assert.notDeepEqual(original, result);
    if (result.config) assert.equal(result.config.temperature, .2);
  }
  for (const api of ['openai-responses', 'openai-codex-responses']) {
    const original = {input: ['given material'], tools: ['report-only']};
    assert.deepEqual(limitPayload(original, 4096, api), original);
  }
  assert.throws(() => limitPayload({}, 4096, 'unrecognized'), /report_model_failed/);
});
