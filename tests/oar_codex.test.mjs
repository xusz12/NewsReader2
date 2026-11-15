import test from 'node:test';
import assert from 'node:assert/strict';
import os from 'node:os';
import {codexOptions, executeCodex, codexFailure, completedCodexText, codexArchiveText} from '../scripts/oar_codex.mjs';
import {ephemeralSource} from '../scripts/oar_codex_ephemeral.mjs';

const id = '0199a213-81c0-7800-8aa1-bbab2a035a53';
const request = {runtime: 'codex', cwd: os.tmpdir(), prompt: 'first question', resume_prompt: 'follow-up',
  codex: {model: 'chosen', reasoning_effort: 'high', sandbox: 'read-only'}};

test('archives are independent ephemeral OAR turns, with web research disabled', () => {
  const archive = {...request, task: 'chat_archive'};
  const options = codexOptions(archive);
  assert.equal(options.nrEphemeral, true);
  assert.equal(options.resume, undefined);
  assert.ok(options.launchArgs.includes('web_search="disabled"'));
  assert.deepEqual(options.outputSchema, {type: 'object', properties: {summary: {type: 'string', maxLength: 300}},
    required: ['summary'], additionalProperties: false});
  assert.equal(codexOptions({...archive, archive_max_length: 12}).outputSchema.properties.summary.maxLength, 12);
  assert.equal(codexOptions(request).outputSchema, undefined);
  for (const archive_max_length of [0, 301, -1, 1.5, '300', false, null]) {
    assert.throws(() => codexOptions({...archive, archive_max_length}), /codex_session_invalid/);
  }
  for (const extra of [{resume: id}, {web_search: 'live'}, {task: 'unknown'}]) {
    assert.throws(() => codexOptions({...archive, ...extra}), /codex_session_invalid/);
  }
  assert.throws(() => ephemeralSource('changed upstream'), /oar_compatibility_mismatch/);
});

test('Codex OAR pins NR permissions, standard tier and live search on new and resumed sessions', () => {
  for (const resume of ['', id]) {
    const options = codexOptions({...request, resume, web_search: 'live'});
    assert.equal(options.model, 'chosen');
    assert.equal(options.effort, 'high');
    assert.equal(options.resume, resume || undefined);
    assert.deepEqual(options.launchArgs, ['-c', 'approval_policy="never"', '-c', 'sandbox_mode="read-only"',
      '-c', 'service_tier="default"', '-c', 'features.fast_mode=false', '-c', 'web_search="live"']);
    assert.equal(JSON.stringify(options).includes(request.prompt), false);
  }
});

test('retired provider and profile overrides are refused before opening a session', () => {
  for (const key of ['profile', 'provider']) {
    for (const value of ['saved', null, false, {}]) assert.throws(() => codexOptions({...request, codex: {...request.codex, [key]: value}}), /codex_advanced_config_removed/);
  }
});

test('invalid Codex configurations cannot reach OAR or override permissions', () => {
  for (const value of [null, {...request, cwd: 'relative'}, {...request, resume: '../foreign'},
    {...request, codex: {...request.codex, sandbox: 'danger-full-access'}},
    {...request, codex: {...request.codex, reasoning_effort: 'guess'}}, {...request, web_search: true}]) {
    assert.throws(() => codexOptions(value), /codex_session_invalid/);
  }
});

function fixture() {
  const calls = [], records = [];
  const session = {id, records: () => records,
    complete(text) {
      records.push({seq: 1, kind: 'frame', sessionId: id, agentPath: [], body: {type: 'item/completed',
        native: {turnId: 'turn', item: {type: 'agentMessage', phase: 'final_answer', text}}}});
      records.push({seq: 2, kind: 'frame', sessionId: id, agentPath: [], body: {type: 'turn/completed', native: {turn: {id: 'turn'}}}});
    },
    events(listener) {this.listener = listener;}, async dispose() {this.disposed = true;}};
  return {session, calls, runtime: {async session(_installation, options) {calls.push(options); return session;}}};
}

test('archive saves only the summary field and represents an intentional empty result', async () => {
  for (const summary of ['一条完整结论。', '']) {
    const {runtime, session} = fixture(), events = [];
    const raw = JSON.stringify({summary});
    assert.equal(await executeCodex({...request, task: 'chat_archive'}, runtime, {}, e => events.push(e), undefined,
      async opened => {opened.complete(raw); return {kind: 'ended', outcome: {kind: 'completed'}, text: raw};}), true);
    assert.equal(events.at(-1).answer, summary || '[[NO_ARCHIVE_CONTENT]]');
    assert.equal(session.disposed, true);
  }
  for (const raw of ['not json', 'null', '[]', '{}', '{"summary":1}', '{"summary":"valid","extra":true}']) {
    assert.throws(() => codexArchiveText(raw), /codex_protocol_error/);
  }
});

test('OAR Codex streams root events, resumes the same id and leaves mapping commits to NR', async () => {
  for (const resume of ['', id]) {
    const {runtime, session, calls} = fixture(), events = [];
    const wait = async (opened, prompt) => {
      assert.equal(prompt, resume ? request.resume_prompt : request.prompt);
      opened.listener({kind: 'text_delta', text: 'root'});
      opened.listener({kind: 'tool_call_started', tool: 'webSearch', input: 'private query'});
      opened.listener({kind: 'text_delta', text: 'child answer', agentPath: ['child']});
      opened.complete('final answer');
      return {kind: 'ended', outcome: {kind: 'completed'}, text: 'final answer'};
    };
    assert.equal(await executeCodex({...request, resume}, runtime, {}, e => events.push(e), undefined, wait), true);
    assert.equal(session.disposed, true);
    assert.equal(calls.length, 1);
    assert.deepEqual(events.at(-1), {type: 'oar_result', outcome: 'completed', answer: 'final answer', native_id: id});
    assert.equal(JSON.stringify(events).includes('private query'), false);
    assert.equal(JSON.stringify(events).includes('child answer'), false);
    assert.deepEqual(events[2], {type: 'oar_tool', tool: 'webSearch'});
  }
});

test('final text belongs to this root turn and excludes progress, drafts and later turns', () => {
  const message = (seq, text, options = {}) => ({seq, kind: 'frame', sessionId: options.sessionId ?? id,
    agentPath: options.agentPath ?? [], body: {type: options.type ?? 'item/completed', native: {
      turnId: options.turnId ?? 'current', item: {type: 'agentMessage', text, phase: options.phase ?? 'final_answer'}}}});
  const records = [message(1, 'old'), message(3, 'progress', {phase: 'commentary'}),
    message(4, 'child', {sessionId: 'child'}), message(5, 'child-path', {agentPath: ['child']}),
    message(6, 'draft', {type: 'item/started'}), message(7, 'different turn', {turnId: 'previous'}),
    message(8, 'correct conclusion'),
    {seq: 9, kind: 'frame', sessionId: id, agentPath: [], body: {type: 'turn/completed', native: {turn: {id: 'current'}}}},
    message(10, 'later turn')];
  assert.equal(completedCodexText(records, id, 2), 'correct conclusion');
  assert.equal(completedCodexText(records, id, 9), '');
});

for (const [result, error] of [
  [{kind: 'ended', outcome: {kind: 'failed', failure: 'auth'}, text: 'private'}, 'codex_auth_required'],
  [{kind: 'rejected', failure: 'auth', reason: 'private'}, 'codex_auth_required'],
  [{kind: 'interrupted'}, 'codex_turn_failed'],
  [{kind: 'handled'}, 'codex_turn_failed'],
  [{kind: 'ended', outcome: {kind: 'completed'}, text: ''}, 'codex_empty_answer'],
]) test(`Codex OAR refuses incomplete output: ${JSON.stringify(result)}`, async () => {
  const {runtime, session} = fixture(), events = [];
  assert.equal(await executeCodex(request, runtime, {}, e => events.push(e), undefined, async () => result), false);
  assert.equal(session.disposed, true);
  assert.deepEqual(events.at(-1), {type: 'oar_result', outcome: 'failed', error});
});

test('Codex missing or foreign native sessions never fall back to a new conversation', async () => {
  const {runtime, session, calls} = fixture();
  session.id = '0199a213-81c0-7800-8aa1-bbab2a035a54';
  await assert.rejects(executeCodex({...request, resume: id}, runtime, {}, () => {}), /codex_session_invalid/);
  assert.equal(session.disposed, true);
  assert.equal(calls.length, 1);
  assert.equal(codexFailure({name: 'SessionNotFoundError', message: 'private'}), 'codex_session_invalid');
  assert.equal(codexFailure(new Error('private token=secret')), 'codex_runtime_failed');
});
