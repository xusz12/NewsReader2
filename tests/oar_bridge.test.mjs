import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { execute, prepareConfig, validateRequest } from '../scripts/oar_bridge.mjs';

async function fixture(t) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'newsreader-oar-unit-'));
  t.after(() => fs.rm(dir, { recursive: true, force: true }));
  return { prompt: 'full news + visible history', resume_prompt: 'next question', cwd: dir,
    state_dir: dir, provider: 'fixture', model: 'model', history_replayed: true };
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
  await fs.writeFile(path.join(source, 'extensions/test.ts'), 'test extension');
  await fs.symlink(path.join(source, 'extensions'), path.join(source, 'skills/shared'));
  const agentDir = await prepareConfig(source, path.join(request.cwd, 'state'));
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

test('routing preserves original trust: trusted to OAR, undecided/denied to CLI; sticky across retry', async t => {
  const {selectRoute} = await import('../scripts/oar_bridge.mjs');
  const request = await fixture(t);
  const cli = await selectRoute(request, async ()=>{throw new Error('oar_project_untrusted');});
  assert.equal(cli.runtime,'cli');
  assert.equal((await selectRoute(request,async ()=>{})).runtime,'cli');
  await fs.rm(path.join(request.state_dir,'route.json'));
  assert.equal((await selectRoute(request,async ()=>{})).runtime,'oar');
  await assert.rejects(selectRoute(request,async()=>{throw new Error('oar_project_untrusted');}),/oar_trust_changed/);
  await fs.rm(path.join(request.state_dir,'route.json'));
  assert.equal((await selectRoute(request,async()=>{throw new Error('unreadable trust');})).runtime,'cli');
});

test('legacy sync remains native CLI; extension errors are not hidden by route fallback', async t => {
  const {selectRoute} = await import('../scripts/oar_bridge.mjs');
  const request=await fixture(t);request.legacy_native_id='native-legacy';
  assert.equal((await selectRoute(request,()=>{throw new Error('should not pregrant');})).runtime,'cli');
  await fs.rm(path.join(request.state_dir,'route.json'));delete request.legacy_native_id;
  await assert.rejects(selectRoute(request,async()=>{throw new Error('oar_extension_failed');}),/oar_extension_failed/);
});

async function fakeCli(t,events,{code=0,delay=false}={}) {
  const {spawn}=await import('node:child_process');
  const script=path.join(os.tmpdir(),`nr-cli-${process.pid}-${Math.random()}.mjs`);
  t.after(()=>fs.rm(script,{force:true}));
  await fs.writeFile(script,`let raw='';for await(const c of process.stdin)raw+=c;let args=process.argv;const key=args.includes('--session-id')?'--session-id':'--session';const id=args[args.indexOf(key)+1];\n`+
    (delay?'setInterval(()=>{},1000);':`for(const event of ${JSON.stringify(events)}) {if(event.type==='session')event.id=id;console.log(JSON.stringify(event));}process.exit(${code});`));
  return (node,args,options)=>spawn(node,[script,...args],options);
}
const cliEvents=[{type:'session'},{type:'message_update',assistantMessageEvent:{type:'text_delta',delta:'stream'}},
  {type:'tool_execution_start',toolName:'read',args:{private:'do not leak'}},
  {type:'message_end',message:{role:'assistant',stopReason:'stop',content:[{type:'text',text:'cli answer'}]}}, {type:'agent_end'}];

test('CLI adapter streams, waits for native completion, resumes same native id without prompts in argv', async t => {
  const {executeCli}=await import('../scripts/oar_bridge.mjs');
  const request=await fixture(t), events=[];
  let args;
  const runner=await fakeCli(t,cliEvents);
  const wrapped=(node,argv,options)=>{args=argv;return runner(node,argv,options);};
  assert.equal(await executeCli(request,e=>events.push(e),undefined,wrapped),true);
  assert.equal(events[0].runtime,'pi-cli');
  assert.equal(events.at(-1).answer,'cli answer');
  assert.equal(JSON.stringify(events).includes('do not leak'),false);
  assert.equal(JSON.stringify(args).includes(request.prompt),false);
  assert.equal(await executeCli(request,()=>{},undefined,wrapped),true);
  assert.equal(args.includes('--session'),true);
  assert.equal(args.includes('--session-id'),false);
});

for(const [label,events,code] of [['partial',cliEvents.slice(0,-1),0],['nonzero',cliEvents,1],
  ['empty',[{type:'session'},{type:'agent_end'}],0],['provider error',[{type:'session'},
    {type:'message_end',message:{role:'assistant',stopReason:'error',errorMessage:'private error',content:[{type:'text',text:'partial'}]}},{type:'agent_end'}],0]])
  test(`CLI ${label} is not success and does not save native mapping`,async t=>{
    const {executeCli}=await import('../scripts/oar_bridge.mjs');const request=await fixture(t),emitted=[];
    assert.equal(await executeCli(request,e=>emitted.push(e),undefined,await fakeCli(t,events,{code})),false);
    assert.equal(emitted.at(-1).outcome,'failed');
    await assert.rejects(fs.access(path.join(request.state_dir,'session.json')));
  });

test('CLI cancellation reaches actual child, never saves completion',async t=>{
  const {executeCli}=await import('../scripts/oar_bridge.mjs');const request=await fixture(t),controller=new AbortController();
  const runner=await fakeCli(t,[],{delay:true});
  const pending=executeCli(request,()=>{},controller.signal,runner);
  setTimeout(()=>controller.abort(),100);
  await assert.rejects(pending,/oar_stopped/);
  await assert.rejects(fs.access(path.join(request.state_dir,'session.json')));
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

test('execution families refuse foreign native session mappings',async t=>{
  const request=await fixture(t);
  await fs.writeFile(path.join(request.state_dir,'session.json'),JSON.stringify({id:'cli-native',runtime:'cli'}));
  await assert.rejects(execute(request,fakeRuntime(),{},()=>{},undefined,completed),/oar_invalid_session/);
  await fs.writeFile(path.join(request.state_dir,'session.json'),JSON.stringify({id:'oar-native',runtime:'oar'}));
  const {executeCli}=await import('../scripts/oar_bridge.mjs');
  await assert.rejects(executeCli(request,()=>{}),/oar_invalid_session/);
});

test('native built-in configurations use stable CLI compatibility instead of dropping tools',async t=>{
  const {selectRoute,checkResources}=await import('../scripts/oar_bridge.mjs');
  const request=await fixture(t);
  assert.equal((await selectRoute(request,async()=>({nativeBuiltins:true}))).runtime,'cli');
  await fs.rm(path.join(request.state_dir,'route.json'));
  request.provider='llama.cpp';
  assert.equal((await selectRoute(request,async()=>({nativeBuiltins:false}))).runtime,'cli');
  await fs.rm(path.join(request.state_dir,'route.json'));request.provider='fixture';
  await selectRoute(request,async()=>({nativeBuiltins:false}));
  await assert.rejects(selectRoute(request,async()=>({nativeBuiltins:true})),/oar_resources_changed/);
  const source=path.join(request.cwd,'source');await fs.mkdir(source);await fs.writeFile(path.join(source,'mcp.json'),'{}');
  const agentDir=await prepareConfig(source,request.state_dir);
  assert.equal((await checkResources(request.cwd,agentDir)).nativeBuiltins,true);
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
