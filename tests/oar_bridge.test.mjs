import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { execute, prepareConfig, validateRequest } from '../scripts/oar_bridge.mjs';
import { apply as applyDshPolicy } from '../scripts/dsh_policy.mjs';

async function fixture(t) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'newsreader-oar-unit-'));
  t.after(() => fs.rm(dir, { recursive: true, force: true }));
  return { prompt: 'full news + visible history', resume_prompt: 'next question', cwd: dir,
    state_dir: dir, task: 'news_chat', provider: 'fixture', model: 'model', history_replayed: true };
}
function fakeRuntime({ missing = false, fail = false } = {}) {
  const calls = [];
  const runtime = { calls, async session(_installation, options) {
    calls.push(options);
    if (options.resume && missing) throw Object.assign(new Error('missing'), { name: 'SessionNotFoundError' });
    if (fail) throw new Error('provider failure with private details');
    return { id: options.resume ?? 'native-fixture-id', events(callback) { this.listener = callback; },
      async dispose() { this.disposed = true; } };
  } };
  return runtime;
}
const completed = async (session, prompt) => {
  session.listener({ kind: 'text_delta', text: 'stream' });
  session.listener({ kind: 'tool_call_started', tool: 'read', input: 'must not leak' });
  session.listener({ kind: 'text_delta', text: 'hidden subagent', agentPath: ['child'] });
  return { kind: 'ended', outcome: { kind: 'completed' }, text: `answer:${prompt}` };
};

test('completed turn has explicit terminal and private native mapping; subsequent process resumes', async t => {
  const request = await fixture(t), runtime = fakeRuntime(), events = [];
  assert.equal(await execute(request, runtime, {}, e => events.push(e), undefined, completed), true);
  assert.equal(events.at(-1).outcome, 'completed');
  assert.equal(events.at(-1).answer, `answer:${request.prompt}`);
  assert.equal(events.length, 4);
  assert.deepEqual(events[2], { type: 'oar_tool', tool: 'read' });
  assert.equal(JSON.stringify(events).includes('must not leak'), false);
  const mapping = JSON.parse(await fs.readFile(path.join(request.state_dir, 'session.json')));
  assert.deepEqual(mapping, { id: 'native-fixture-id', runtime: 'oar', history_replayed: true });
  assert.equal((await fs.stat(path.join(request.state_dir, 'session.json'))).mode & 0o777, 0o600);
  const second = [];
  await execute(request, runtime, {}, e => second.push(e), undefined, completed);
  assert.equal(runtime.calls[1].resume, 'native-fixture-id');
  assert.equal(second[0].resumed, true);
  assert.equal(second.at(-1).answer, `answer:${request.resume_prompt}`);
});

test('news chat migrates a CLI mapping only after a successful OAR turn', async t => {
  const request = {...await fixture(t), task: 'news_chat'}, runtime = fakeRuntime();
  const mapping = path.join(request.state_dir, 'session.json'), route = path.join(request.state_dir, 'route.json');
  const before = JSON.stringify({id: 'old-cli', runtime: 'cli', history_replayed: true});
  await fs.writeFile(mapping, before);
  await fs.writeFile(route, JSON.stringify({runtime: 'cli'}));
  assert.equal(await execute(request, runtime, {}, () => {}, undefined,
    async () => ({kind: 'ended', outcome: {kind: 'failed'}, text: 'partial'})), false);
  assert.equal(await fs.readFile(mapping, 'utf8'), before);
  await fs.access(route);
  assert.equal(await execute(request, runtime, {}, () => {}, undefined, completed), true);
  assert.equal(runtime.calls[0].resume, 'old-cli');
  assert.equal(runtime.calls[1].resume, 'old-cli');
  assert.equal(JSON.parse(await fs.readFile(mapping)).runtime, 'oar');
  await assert.rejects(fs.access(route));
});

test('each Pi archive attempt is fresh and cannot replace a conversation mapping', async t => {
  const request = {...await fixture(t), task: 'chat_archive'}, runtime = fakeRuntime();
  const mapping = path.join(request.state_dir, 'session.json');
  const before = JSON.stringify({id: 'original-chat', runtime: 'oar'});
  await fs.writeFile(mapping, before);
  for (let i = 0; i < 2; i++) await execute(request, runtime, {}, () => {}, undefined, completed);
  assert.ok(runtime.calls.every(options => options.resume === undefined));
  assert.ok(runtime.calls.every(options => options.disallowedTools.join() === '*'));
  assert.equal(await fs.readFile(mapping, 'utf8'), before);
  assert.throws(() => validateRequest({...request, nr_tools: {command: '/fixture', args: []}}), /oar_invalid_request/);
});

for (const task of ['translation', 'connection_test']) {
  for (const stopReason of ['stop', 'length']) test(`${task} requires native completion and preserves existing history: ${stopReason}`, async t => {
    const request = {...await fixture(t), task}, runtime = fakeRuntime(), events = [];
    const mapping = path.join(request.state_dir, 'session.json');
    const before = JSON.stringify({id: 'original-chat', runtime: 'oar'});
    await fs.writeFile(mapping, before);
    const success = await execute(request, runtime, {}, e => events.push(e), undefined, async session => {
      session.records = () => [{kind: 'frame', sessionId: session.id, body: {type: 'message_end',
        native: {message: {role: 'assistant', stopReason, content: [{type: 'text', text: 'final answer'}]}}}}];
      return {kind: 'ended', outcome: {kind: 'completed'}, text: 'streamed draft'};
    });
    assert.equal(success, stopReason === 'stop');
    assert.equal(events.at(-1)[success ? 'answer' : 'error'], success ? 'final answer' : 'oar_incomplete_answer');
    assert.equal(runtime.calls[0].resume, undefined);
    assert.deepEqual(runtime.calls[0].disallowedTools, ['*']);
    assert.equal(await fs.readFile(mapping, 'utf8'), before);
    for (const extra of [{resume: 'old'}, {legacy_native_id: 'old'}, {web_search: 'live'}, {nr_tools: {command: '/fixture', args: []}}]) {
      assert.throws(() => validateRequest({...request, ...extra}), /oar_invalid_request/);
    }
  });
}

for (const [failure, expected] of [['quota', 'oar_quota'], ['rate_limited', 'oar_rate_limited'],
  ['overloaded', 'oar_overloaded'], ['input_too_large', 'oar_input_too_large'], ['model_unavailable', 'oar_model_unavailable']]) {
  test(`connection test preserves structured failure category: ${failure}`, async t => {
    const request = {...await fixture(t), task: 'connection_test'}, events = [];
    await execute(request, fakeRuntime(), {}, e => events.push(e), undefined, async session => {
      session.records = () => [];
      return {kind: 'ended', outcome: {kind: 'failed', failure, reason: 'private upstream detail'}};
    });
    assert.deepEqual(events.at(-1), {type: 'oar_result', outcome: 'failed', error: expected});
  });
}

test('corrupt news mappings cannot silently start another conversation', async t => {
  const request = {...await fixture(t), task: 'news_chat'}, runtime = fakeRuntime();
  for (const saved of [null, {}, {id: '../other'}, {id: 'native', runtime: 'other'}]) {
    await fs.writeFile(path.join(request.state_dir, 'session.json'), JSON.stringify(saved));
    await assert.rejects(execute(request, runtime, {}, () => {}, undefined, completed), /oar_invalid_session/);
  }
  assert.equal(runtime.calls.length, 0);
});

test('missing native session replays saved history, but unrelated runtime errors do not', async t => {
  const request = await fixture(t);
  await fs.writeFile(path.join(request.state_dir, 'session.json'), JSON.stringify({ id: 'old' }));
  const runtime = fakeRuntime({ missing: true }), events = [];
  await execute(request, runtime, {}, e => events.push(e), undefined, completed);
  assert.equal(runtime.calls.length, 2);
  assert.equal(events.at(-1).answer, `answer:${request.prompt}`);
  await assert.rejects(execute(request, fakeRuntime({ fail: true }), {}, () => {}, undefined, completed), /provider failure/);
});

for (const [label, result, code] of [
  ['no login', { kind: 'rejected', reason: 'No API key found for fixture' }, 'oar_auth_required'],
  ['interrupted', { kind: 'interrupted' }, 'oar_turn_failed'],
  ['runtime error', { kind: 'ended', outcome: { kind: 'failed' }, text: 'partial' }, 'oar_turn_failed'],
  ['empty', { kind: 'ended', outcome: { kind: 'completed' }, text: '' }, 'oar_empty_answer'],
]) test(`${label} never counts as completed or stores a new mapping`, async t => {
  const request = await fixture(t), events = [];
  const success = await execute(request, fakeRuntime(), {}, e => events.push(e), undefined, async () => result);
  assert.equal(success, false);
  assert.deepEqual(events.at(-1), { type: 'oar_result', outcome: 'failed', error: code });
  await assert.rejects(fs.access(path.join(request.state_dir, 'session.json')));
});

test('cancellation signal prevents prompt execution', async t => {
  const request = await fixture(t), controller = new AbortController(); controller.abort();
  await assert.rejects(execute(request, fakeRuntime(), {}, () => {}, controller.signal, completed), /oar_stopped/);
});

test('config/resource copy isolates account writes and keeps sessions, credentials are removable', async t => {
  const request = await fixture(t), source = path.join(request.cwd, 'source');
  await fs.mkdir(path.join(source, 'extensions'), { recursive: true });
  await fs.mkdir(path.join(source, 'skills'));
  await fs.writeFile(path.join(source, 'auth.json'), '{"fixture":"private"}');
  await fs.writeFile(path.join(source, 'mcp-auth.json'), '{"synthetic-oauth":"private"}');
  await fs.writeFile(path.join(source, 'extensions/test.ts'), 'test extension');
  await fs.symlink(path.join(source, 'extensions'), path.join(source, 'skills/shared'));
  const agentDir = await prepareConfig(source, path.join(request.cwd, 'state'));
  assert.equal(await fs.readFile(path.join(agentDir, 'mcp-auth.json'), 'utf8'), '{"synthetic-oauth":"private"}');
  assert.equal((await fs.stat(path.join(agentDir, 'mcp-auth.json'))).mode & 0o777, 0o600);
  await fs.writeFile(path.join(agentDir, 'mcp-auth.json'), '{}');
  assert.equal(await fs.readFile(path.join(source, 'mcp-auth.json'), 'utf8'), '{"synthetic-oauth":"private"}');
  await fs.writeFile(path.join(agentDir, 'auth.json'), '{}');
  assert.equal(await fs.readFile(path.join(source, 'auth.json'), 'utf8'), '{"fixture":"private"}');
  assert.equal(await fs.readFile(path.join(agentDir, 'skills/shared/test.ts'), 'utf8'), 'test extension');
  assert.equal((await fs.stat(agentDir)).mode & 0o777, 0o700);
  await fs.writeFile(path.join(agentDir, 'sessions/persist.jsonl'), 'session');
  await fs.rm(agentDir, { recursive: true });
  assert.equal(await fs.readFile(path.join(request.cwd, 'state/sessions/persist.jsonl'), 'utf8'), 'session');
  assert.equal((await fs.readdir(path.join(request.cwd, 'state'))).includes('auth.json'), false);
});

test('invalid requests are rejected before creating a runtime', () => {
  for (const request of [null, [], {}, { prompt: 'test', cwd: 'relative' }]) {
    assert.throws(() => validateRequest(request), /oar_invalid_request/);
  }
});

test('old synchronous Pi session is copied read-only, not reset; missing session fails visibly', async t => {
  const { importLegacySession } = await import('../scripts/oar_bridge.mjs');
  const request = await fixture(t);
  request.legacy_native_id = 'legacy-native-id';
  const source = path.join(request.cwd, 'source');
  const slug = path.resolve(request.cwd).replace(/^[/\\]/u, '').replaceAll(/[/\\:]/gu, '-');
  const sourceDir = path.join(source, 'sessions', `--${slug}--`);
  await fs.mkdir(sourceDir, { recursive: true });
  const oldFile = path.join(sourceDir, 'old.jsonl');
  await fs.writeFile(oldFile, 'previous messages and tool records');
  await importLegacySession(request, source, async () => [{ id: 'legacy-native-id', path: oldFile }]);
  const mapping = JSON.parse(await fs.readFile(path.join(request.state_dir, 'session.json')));
  assert.equal(mapping.id, 'legacy-native-id');
  assert.equal(await fs.readFile(oldFile, 'utf8'), 'previous messages and tool records');
  assert.equal(await fs.readFile(path.join(request.state_dir, 'sessions', `--${slug}--`, 'old.jsonl'), 'utf8'), 'previous messages and tool records');
  await fs.rm(path.join(request.state_dir, 'session.json'));
  await assert.rejects(importLegacySession(request, source, async () => []), /oar_legacy_session_missing/);
});

async function trustFixture(t) {
  const request = await fixture(t);
  const cwd = path.join(request.cwd, 'project'), source = path.join(request.cwd, 'source');
  await fs.mkdir(path.join(cwd, '.pi/extensions'), { recursive: true });
  await fs.mkdir(path.join(source, 'extensions'), { recursive: true });
  const marker = path.join(request.cwd, 'project-loaded');
  await fs.writeFile(path.join(cwd, '.pi/extensions/probe.ts'),
    `import fs from 'node:fs'; export default function() {fs.writeFileSync(${JSON.stringify(marker)}, 'loaded');}`);
  return { ...request, cwd, source, marker };
}
for (const decision of [false, null]) test(`original trust ${decision} prevents project extensions before OAR opens`, async t => {
  const { checkResources } = await import('../scripts/oar_bridge.mjs');
  const request = await trustFixture(t);
  await fs.writeFile(path.join(request.source, 'trust.json'), JSON.stringify({ [await fs.realpath(request.cwd)]: decision }));
  const agentDir = await prepareConfig(request.source, request.state_dir);
  await assert.rejects(checkResources(request.cwd, agentDir), /oar_project_untrusted/);
  const decisionForRpc = await checkResources(request.cwd, agentDir, {allowUntrusted: true});
  assert.equal(decisionForRpc.projectTrusted, false);
  await assert.rejects(fs.access(request.marker));
});

test('explicit trust allows original project resources; source trust remains unchanged', async t => {
  const { checkResources } = await import('../scripts/oar_bridge.mjs');
  const request = await trustFixture(t);
  const trust = JSON.stringify({ [await fs.realpath(request.cwd)]: true });
  await fs.writeFile(path.join(request.source, 'trust.json'), trust);
  const agentDir = await prepareConfig(request.source, request.state_dir);
  await checkResources(request.cwd, agentDir);
  assert.equal(await fs.readFile(request.marker, 'utf8'), 'loaded');
  assert.equal(await fs.readFile(path.join(request.source, 'trust.json'), 'utf8'), trust);
});

test('global trust extension veto takes precedence over cached project consent', async t => {
  const { checkResources } = await import('../scripts/oar_bridge.mjs');
  const request = await trustFixture(t);
  await fs.writeFile(path.join(request.source, 'trust.json'), JSON.stringify({ [await fs.realpath(request.cwd)]: true }));
  await fs.writeFile(path.join(request.source, 'extensions/trust.ts'),
    `export default function(pi) {pi.on('project_trust', async ()=>({trusted:'no', remember:false}));}`);
  const agentDir = await prepareConfig(request.source, request.state_dir);
  await assert.rejects(checkResources(request.cwd, agentDir), /oar_project_untrusted/);
  await assert.rejects(fs.access(request.marker));
});

test('global trust extension consent and existing default always are respected', async t => {
  const { checkResources } = await import('../scripts/oar_bridge.mjs');
  const request = await trustFixture(t);
  await fs.writeFile(path.join(request.source, 'extensions/trust.ts'),
    `export default function(pi) {pi.on('project_trust', async ()=>({trusted:'yes', remember:false}));}`);
  let agentDir = await prepareConfig(request.source, request.state_dir);
  await checkResources(request.cwd, agentDir);
  await fs.rm(path.join(request.source, 'extensions/trust.ts'));
  await fs.writeFile(path.join(request.source, 'settings.json'), JSON.stringify({defaultProjectTrust:'always'}));
  agentDir = await prepareConfig(request.source, request.state_dir);
  await checkResources(request.cwd, agentDir);
});

test('failing extension and failing trust handler are explicit, not silently reduced tools', async t => {
  const { checkResources } = await import('../scripts/oar_bridge.mjs');
  const request = await trustFixture(t);
  await fs.writeFile(path.join(request.source, 'extensions/trust.ts'),
    `export default function(pi) {pi.on('project_trust', async ()=>{throw new Error('private diagnostic');});}`);
  let agentDir = await prepareConfig(request.source, request.state_dir);
  await assert.rejects(checkResources(request.cwd, agentDir), /oar_extension_failed/);
  await fs.rm(path.join(request.source, 'extensions/trust.ts'));
  await fs.writeFile(path.join(request.source, 'extensions/broken.ts'), 'export default function() {throw new Error("private diagnostic");}');
  agentDir = await prepareConfig(request.source, request.state_dir);
  await assert.rejects(checkResources(request.cwd, agentDir), /oar_extension_failed/);
});

test('partial private config is removed when preparation itself fails', async t => {
  const request = await fixture(t), source = path.join(request.cwd, 'source');
  await fs.mkdir(path.join(source, 'auth.json'), {recursive: true});
  await assert.rejects(prepareConfig(source, request.state_dir));
  assert.equal((await fs.readdir(request.state_dir)).some(name => name.startsWith('config-')), false);
});

test('legacy native mapping never silently resets if the native runtime cannot resume it', async t => {
  const request = await fixture(t);request.legacy_native_id = 'old-native';
  await fs.writeFile(path.join(request.state_dir,'session.json'), JSON.stringify({id:'old-native'}));
  await assert.rejects(execute(request, fakeRuntime({missing:true}), {}, ()=>{}, undefined, completed), /oar_legacy_session_missing/);
});

test('personal assistant registers its MCP through OAR and migrates an old mapping', async t => {
  const request = {...await fixture(t), task: 'personal_assistant',
    nr_tools: {command:'/usr/bin/python3',args:['/local/nr-tools.py']}}, runtime = fakeRuntime();
  const mapping = path.join(request.state_dir, 'session.json');
  await fs.writeFile(mapping, JSON.stringify({id: 'old-assistant', runtime: 'cli'}));
  await fs.writeFile(path.join(request.state_dir, 'route.json'), JSON.stringify({runtime: 'cli'}));
  await execute(request, runtime, {}, () => {}, undefined, completed);
  assert.deepEqual(runtime.calls[0].mcpServers, [{name: 'nr_assistant', ...request.nr_tools}]);
  assert.ok(runtime.calls[0].disallowedTools.includes('bash'));
  assert.equal(runtime.calls[0].resume, 'old-assistant');
  assert.equal(JSON.parse(await fs.readFile(mapping)).runtime, 'oar');
  await assert.rejects(fs.access(path.join(request.state_dir, 'route.json')), {code: 'ENOENT'});
  assert.throws(() => validateRequest({...request, nr_tools: undefined}), /oar_invalid_request/);
});

test('installed Pi MCP client connects to the NR server and executes library and memory tools', async t => {
  const {McpClient,StdioTransport}=await import('@earendil-works/pi-mcp');
  const {execFileSync}=await import('node:child_process');
  const {fileURLToPath}=await import('node:url');
  const request=await fixture(t), root=fileURLToPath(new URL('..',import.meta.url));
  const python=process.env.NEWS_READER_PYTHON || 'python3';
  execFileSync(python,['-c',`import sys,sqlite3\nfrom pathlib import Path\nfrom personal_assistant import Service\nfrom settings import default_app_settings\np=Path(sys.argv[1])\ndb=p/'news.sqlite3'\nc=sqlite3.connect(db)\nc.execute('CREATE TABLE sample(title TEXT)')\nc.execute("INSERT INTO sample VALUES('政策新闻')")\nc.commit()\nc.close()\ns=Service(p/'assistant',lambda:db,default_app_settings)\ns.ensure()`,request.cwd],{cwd:root});
  const client=new McpClient({name:'nr-integration-test',version:'1',requestTimeoutMs:5000});
  t.after(()=>client.close());
  await client.connect(new StdioTransport({command:python,args:[path.join(root,'personal_assistant_tools.py'),
    '--db',path.join(request.cwd,'news.sqlite3'),'--workspace',path.join(request.cwd,'assistant/workspace'),'--state-db',path.join(request.cwd,'assistant/assistant.sqlite3')]}));
  assert.equal((await client.listTools()).length,6);
  const result=await client.callTool('nr_query',{sql:'SELECT title FROM sample'});
  assert.deepEqual(JSON.parse(result.content[0].text).rows,[['政策新闻']]);
  const memory=await client.callTool('memory_read',{path:'MEMORY.md'});
  assert.match(JSON.parse(memory.content[0].text).content,/长期记忆/);
  const denied=await client.callTool('nr_query',{sql:'DELETE FROM sample'});
  assert.equal(denied.isError,true);
});

test('Pi OAR receives the selected effort for new, resumed, and rebuilt sessions', async t => {
  const request = {...await fixture(t), reasoning_effort:'high'};
  const runtime = fakeRuntime();
  await execute(request, runtime, {}, ()=>{}, undefined, completed);
  await execute(request, runtime, {}, ()=>{}, undefined, completed);
  assert.deepEqual(runtime.calls.map(call=>call.effort), ['high','high']);
  const missing = fakeRuntime({missing:true});
  await execute(request, missing, {}, ()=>{}, undefined, completed);
  assert.deepEqual(missing.calls.map(call=>call.effort), ['high','high']);
  assert.throws(()=>validateRequest({...request,reasoning_effort:'guess'}), /oar_invalid_request/);
});

test('copied global relative resources keep their original meaning and package selections',async t=>{
  const request=await fixture(t),source=path.join(request.cwd,'source');
  await fs.mkdir(path.join(source,'extensions'),{recursive:true});
  const settings={extensions:['extensions/probe.ts','../outside.ts','-builtin:mcp','-extensions/*.ts'],skills:['../skills'],packages:['npm:fixture',{source:'../local-package',extensions:['a.ts']} ]};
  await fs.writeFile(path.join(source,'settings.json'),JSON.stringify(settings));
  const agentDir=await prepareConfig(source,request.state_dir);
  const copied=JSON.parse(await fs.readFile(path.join(agentDir,'settings.json')));
  assert.deepEqual(copied.extensions,[path.join(agentDir,'extensions/probe.ts'),path.join(request.cwd,'outside.ts'),'-builtin:mcp','-'+path.join(agentDir,'extensions/*.ts')]);
  assert.equal(copied.skills[0],path.join(request.cwd,'skills'));
  assert.equal(copied.packages[1].source,path.join(request.cwd,'local-package'));
  assert.deepEqual(copied.packages[1].extensions,['a.ts']);
  assert.deepEqual(JSON.parse(await fs.readFile(path.join(source,'settings.json'))),settings);
});

test('private config cleanup tokens are validated and separate invocation directories', async t => {
  const request = await fixture(t);
  const source = path.join(request.cwd, 'source-token');
  await fs.mkdir(source);
  const firstId = 'a'.repeat(32), secondId = 'b'.repeat(32);
  const first = await prepareConfig(source, request.state_dir, firstId);
  const second = await prepareConfig(source, request.state_dir, secondId);
  assert.ok(path.basename(first).startsWith(`config-${firstId}-`));
  assert.ok(path.basename(second).startsWith(`config-${secondId}-`));
  await fs.rm(first, { recursive: true, force: true });
  await fs.stat(second);
  assert.throws(() => validateRequest({ ...request, config_id: '../other' }), /oar_invalid_request/);
  await assert.rejects(prepareConfig(source, request.state_dir, '../other'), /oar_invalid_request/);
});

test('lost old session rebuild pins saved model after native defaults change and fails if unknown', async t => {
  const request = await fixture(t);
  request.provider = 'old-provider';request.model = 'old-model';
  const mappingFile = path.join(request.state_dir, 'session.json');
  await fs.writeFile(mappingFile, JSON.stringify({id:'old-session', runtime:'oar'}));
  const calls = [], currentDefault = 'new-provider/new-model';
  const runtime = {async session(_installation, options) {
    calls.push({...options});
    if (options.resume) throw Object.assign(new Error('missing'), {name:'SessionNotFoundError'});
    const selected = options.model ?? currentDefault;
    return {id:selected, events(callback){this.listener=callback;}, async dispose(){}};
  }};
  const events = [];
  assert.equal(await execute(request, runtime, {}, e=>events.push(e), undefined, completed), true);
  assert.equal(calls[0].model, undefined); // Ordinary resume does not override the native model.
  assert.equal(calls[1].model, 'old-provider/old-model');
  assert.equal(events[0].id, 'old-provider/old-model');
  assert.equal(events.at(-1).answer, `answer:${request.prompt}`); // Full saved history is replayed.
  assert.equal(JSON.parse(await fs.readFile(mappingFile)).id, 'old-provider/old-model');

  // Unknown original configuration: no reconstruction, no success, mapping unchanged.
  await fs.writeFile(mappingFile, JSON.stringify({id:'old-session', runtime:'oar'}));
  request.provider = '';request.model = '';
  const unknown = fakeRuntime({missing:true}), rejected = [];
  await assert.rejects(execute(request, unknown, {}, e=>rejected.push(e), undefined, completed), /oar_legacy_session_missing/);
  assert.equal(unknown.calls.length, 1);
  assert.equal(rejected.length, 0);
  assert.equal(JSON.parse(await fs.readFile(mappingFile)).id, 'old-session');
  // The same legacy mapping remains resumable when its native session still exists.
  const available = fakeRuntime();
  assert.equal(await execute(request, available, {}, ()=>{}, undefined, completed), true);
  assert.equal(available.calls.length, 1);assert.equal(available.calls[0].model, undefined);
  assert.equal(available.calls[0].resume, 'old-session');
});


test('research budget deduplicates tool calls and asks for a sourced conclusion once', async t => {
  const { researchBudget } = await import('../scripts/oar_bridge.mjs');
  const prompts = [], events = [];
  const budget = researchBudget({ steer: async text => { prompts.push(text); } }, { research_budget_ms: 1000, max_tool_calls: 2 }, event => events.push(event));
  t.after(() => budget.close());
  budget.event({ kind: 'tool_call_started', callId: 'one' });
  budget.event({ kind: 'tool_call_started', callId: 'one' });
  assert.equal(prompts.length, 0);
  budget.event({ kind: 'tool_call_started', callId: 'two' });
  budget.event({ kind: 'tool_call_started', callId: 'three' });
  assert.equal(prompts.length, 1);
  assert.match(prompts[0], /无法核实/);
  assert.deepEqual(events, [{ type: 'oar_progress', phase: 'summarizing' }]);
  assert.equal(events.some(event => event.type === 'oar_result'), false);
});

test('research timer steers a running turn and cleanup prevents a late steer', async () => {
  const { researchBudget } = await import('../scripts/oar_bridge.mjs');
  let count = 0;
  const session = { steer: async () => { count++; } };
  const first = researchBudget(session, { research_budget_ms: 10 }, () => {});
  await new Promise(resolve => setTimeout(resolve, 30));
  first.close();
  assert.equal(count, 1);
  const second = researchBudget(session, { research_budget_ms: 10 }, () => {});
  second.close();
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.equal(count, 1);
});


test('one-shot material and research tasks ignore mappings and do not persist a new one', async t => {
  for (const task of ['material_summary', 'web_research']) {
    const request = {...await fixture(t), task}, runtime = fakeRuntime();
    const mapping = path.join(request.state_dir, 'session.json');
    const saved = JSON.stringify({id: 'unrelated', runtime: 'cli'});
    await fs.writeFile(mapping, saved);
    await execute(request, runtime, {}, () => {}, undefined, completed);
    assert.equal(runtime.calls[0].resume, undefined);
    assert.deepEqual(runtime.calls[0].disallowedTools, task === 'material_summary' ? ['*'] : ['read', 'bash', 'write', 'edit', 'grep', 'find', 'ls']);
    assert.equal(await fs.readFile(mapping, 'utf8'), saved);
    assert.throws(() => validateRequest({...request, legacy_native_id: 'legacy'}), /oar_invalid_request/);
  }
});


test('research evidence requires a completed tool and failed calls remain failures', async () => {
  const {toolEvents} = await import('../scripts/oar_tool_events.mjs');
  const events = [], forward = toolEvents(e => events.push(e), true);
  forward({kind: 'tool_call_ended', callId: 'unknown', result: 'ok'});
  assert.deepEqual(events, []);
  forward({kind: 'tool_call_started', callId: 'a', tool: 'web_search'});
  forward({kind: 'tool_call_ended', callId: 'a', result: 'failed'});
  forward({kind: 'tool_call_ended', callId: 'a', result: 'ok'});
  forward({kind: 'tool_call_started', callId: 'b', tool: 'web_search'});
  forward({kind: 'tool_call_ended', callId: 'b', result: 'ok'});
  assert.deepEqual(events.filter(e => e.phase), [
    {type: 'oar_tool', tool: 'web_search', phase: 'completed', success: false},
    {type: 'oar_tool', tool: 'web_search', phase: 'completed', success: true},
  ]);
});

test('DSH disposes jobs surviving the main turn and ignores late output', async t => {
  const base = await fixture(t);
  const request = {...base, runtime:'dsh', task:'material_summary', agentDir:base.state_dir};
  await fs.writeFile(path.join(base.state_dir, 'policy-native-id'), 'ready');
  const stopped = [], published = [];
  const native = {id:'native-id', events(fn) {this.event = fn;}, records:()=>[],
    async stopTask(id) {stopped.push(id); this.event({kind:'text_delta',text:'late'});},
    async dispose() {this.event({kind:'text_delta',text:'disposed'});}};
  const complete = async () => {
    native.event({kind:'task_started', taskId:'old'}); native.event({kind:'task_ended',taskId:'old'});
    native.event({kind:'task_started', taskId:'background'});
    return {kind:'ended', outcome:{kind:'completed'}, text:'final'};
  };
  assert.ok(await execute(request, {session:async()=>native}, {}, e=>published.push(e), undefined, complete));
  assert.deepEqual(stopped,['background']);
  assert.equal(published.at(-1).answer,'final');
  assert.equal(published.some(e=>e.type==='text_delta'),false);
});

test('DSH report metadata uses the selected route and only consults its installed model catalog', async t => {
  const {state_dir: home} = await fixture(t);
  const route = {provider: 'opencode-go', model: 'selected'};
  const config = {home, route, metadata: path.join(home, 'metadata.json'), servers: []};
  const agent = {session: {id: 'fixture'}, scope: {ctx: {tools: {definitions: () => [], restrict() {}}}}};
  const installed = {provider: route.provider, settingsNs: 'llm-pi-ai', declared: false};
  const catalog = [{id: 'other', maxTokens: 64000}, {id: route.model, maxTokens: 131072}];
  for (const example of [
    {entry: installed, catalog, expected: 131072},
    {entry: installed, configured: 8192, expected: 8192},
    {entry: installed, catalog: [catalog[0]]},
    {entry: installed, catalog: new Error('catalog unavailable')},
    {entry: {...installed, declared: true}},
    {entry: {...installed, settingsNs: 'another-adapter'}},
    {entry: {...installed, provider: 'another-provider'}},
  ]) {
    const lookups = [];
    const llm = {
      async resolveModelInfo(provider, model) {
        assert.deepEqual({provider, model}, route);
        return {provider, id: model, context: {contextWindow: 1048576}, defaultMaxTokens: example.configured};
      },
      listConfigurableProviders: () => [example.entry],
      async discoverModels(namespace, request) {
        lookups.push([namespace, request]);
        if (example.catalog instanceof Error) throw example.catalog;
        return example.catalog;
      },
    };
    let created;
    applyDshPolicy({llm, on(event, handler) { assert.equal(event, 'agent/created'); created = handler; }}, config);
    await created({agent});
    const metadata = JSON.parse(await fs.readFile(config.metadata, 'utf8'));
    assert.equal(metadata.contextWindow, 1048576);
    assert.equal(metadata.maxTokens, example.expected);
    assert.deepEqual(lookups, example.catalog ? [['llm-pi-ai', {provider: route.provider}]] : []);
  }
});

test('assistant compacts before input only with verified capabilities and actual 80 percent usage', async () => {
  const {assistantContext} = await import('../scripts/oar_assistant_context.mjs');
  const usage = {tokens:799,contextWindow:1000,percent:80}, events=[];
  const session = {contextUsage:()=>({value:usage}),compact:async options=>{
    assert.equal(options.timeoutMs,45000);
    events.push('native compact');
    return {kind:'completed'};
  }};
  const capabilities = {manualCompaction:{kind:'supported'},contextUsage:{tokens:true,contextWindow:true}};
  const runtime = {sessionCapabilities:()=>capabilities};
  const request = {task:'personal_assistant',compact_before_turn:true};
  const context = assistantContext(request, runtime, {}, session);
  assert.equal(context.due(),false);
  usage.tokens=800;
  assert.equal(context.due(),true);
  usage.tokens=null;
  assert.equal(context.due(),false);
  await context.prepare(false, event=>events.push(event.phase));
  assert.deepEqual(events,[]);
  await context.prepare(true, event=>events.push(event.phase));
  assert.deepEqual(events,['compacting','native compact','thinking']);
  capabilities.contextUsage.contextWindow=false;
  assert.equal(assistantContext(request,runtime,{},session),undefined);
  assert.equal(assistantContext({...request,task:'news_chat'},runtime,{},session),undefined);
});
