/** One request per process. stdout is JSONL protocol only, never runtime logs. */
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';

const MAX_REQUEST_BYTES = 2 * 1024 * 1024;
const CONFIG_FILES = ['auth.json', 'models.json', 'models-store.json', 'settings.json', 'trust.json',
  'mcp.json', 'SYSTEM.md', 'APPEND_SYSTEM.md', 'AGENTS.override.md', 'AGENTS.md', 'AGENTS.MD', 'CLAUDE.md', 'CLAUDE.MD'];
const RESOURCE_DIRS = ['extensions', 'skills', 'prompts', 'themes', 'npm', 'git', 'bin'];

export async function prepareConfig(source, stateDir, configId) {
  // OAR Pi writes trust.json and may refresh auth/settings. Never aim it at the
  // user's real agentDir. Sessions stay here; fresh config/resources are copied.
  if (configId !== undefined && (typeof configId !== 'string' || !/^[a-f0-9]{32}$/.test(configId))) {
    throw new Error('oar_invalid_request');
  }
  await fs.mkdir(stateDir, { recursive: true, mode: 0o700 });
  const agentDir = await fs.mkdtemp(path.join(stateDir, configId ? `config-${configId}-` : 'config-'));
  try {
  const sessionsDir = path.join(stateDir, 'sessions');
  await fs.mkdir(sessionsDir, { recursive: true, mode: 0o700 });
  await fs.symlink(sessionsDir, path.join(agentDir, 'sessions'));
  await fs.chmod(stateDir, 0o700);
  await fs.chmod(agentDir, 0o700);
  for (const name of CONFIG_FILES) {
    const dest = path.join(agentDir, name);
    try {
      await fs.copyFile(path.join(source, name), dest);
      await fs.chmod(dest, 0o600);
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
      await fs.rm(dest, { force: true });
    }
  }
  for (const name of RESOURCE_DIRS) {
    const dest = path.join(agentDir, name);
    await fs.rm(dest, { recursive: true, force: true });
    try { await fs.cp(path.join(source, name), dest, { recursive: true, dereference: false }); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
  // Global resource paths are based on the original agentDir, not on this
  // random temporary directory. Keep external paths, remap copied resources.
  const settingsFile = path.join(agentDir, 'settings.json');
  try {
    const settings = JSON.parse(await fs.readFile(settingsFile, 'utf8'));
    const sdkRoot = new URL('./', import.meta.resolve('@earendil-works/pi-coding-agent'));
    const { isLocalPath, resolvePath } = await import(new URL('utils/paths.js', sdkRoot));
    const relocate = (value, patterns = false) => {
      if (typeof value !== 'string') return value;
      const prefix = patterns && /^[+!-]/u.test(value) ? value[0] : '';
      const raw = prefix ? value.slice(1) : value;
      if (!isLocalPath(raw)) return value;
      const resolved = resolvePath(raw, source);
      const relative = path.relative(path.resolve(source), resolved);
      const copied = RESOURCE_DIRS.some(name => relative === name || relative.startsWith(`${name}${path.sep}`));
      return prefix + (copied ? path.join(agentDir, relative) : resolved);
    };
    for (const key of ['extensions', 'skills', 'prompts', 'themes']) {
      if (Array.isArray(settings[key])) settings[key] = settings[key].map(value => relocate(value, true));
    }
    if (Array.isArray(settings.packages)) settings.packages = settings.packages.map(pkg =>
      typeof pkg === 'string' ? relocate(pkg) : pkg && typeof pkg === 'object' ? { ...pkg, source: relocate(pkg.source) } : pkg);
    await fs.writeFile(settingsFile, JSON.stringify(settings), { mode: 0o600 });
  } catch (error) {
    // Let the native runtime report malformed settings, never repair real files.
    if (error.code !== 'ENOENT' && !(error instanceof SyntaxError)) throw error;
  }
  return agentDir;
  } catch (error) {
    await fs.rm(agentDir, { recursive: true, force: true });
    throw error;
  }
}

/** Gate upstream's automatic trust using the pinned Pi CLI's noninteractive rule.
 * No project files are loaded before its original global trust handlers decide.
 * Untrusted projects are routed to native CLI, never implicitly authorized.
 */
export async function checkResources(cwd, agentDir) {
  const sdk = await import('@earendil-works/pi-coding-agent');
  const sdkRoot = new URL('./', import.meta.resolve('@earendil-works/pi-coding-agent'));
  const { resolveProjectTrusted } = await import(new URL('core/project-trust.js', sdkRoot));
  const { createProjectTrustContext } = await import(new URL('cli/project-trust.js', sdkRoot));
  const requiresTrust = sdk.hasTrustRequiringProjectResources(cwd);
  const settingsManager = sdk.SettingsManager.create(cwd, agentDir, { projectTrusted: !requiresTrust });
  const trustStore = new sdk.ProjectTrustStore(agentDir);
  const loader = new sdk.DefaultResourceLoader({ cwd, agentDir, settingsManager });
  const checkErrors = result => {
    if (result.errors.length) throw new Error('oar_extension_failed');
  };
  await loader.reload(requiresTrust ? { resolveProjectTrust: async ({ extensionsResult }) => {
    checkErrors(extensionsResult);
    let handlerFailed = false;
    const trusted = await resolveProjectTrusted({
      cwd, trustStore, defaultProjectTrust: settingsManager.getDefaultProjectTrust(),
      extensionsResult,
      projectTrustContext: createProjectTrustContext({ cwd, mode: 'json', hasUI: false, settingsManager }),
      onExtensionError: () => { handlerFailed = true; },
    });
    if (handlerFailed) throw new Error('oar_extension_failed');
    if (!trusted) throw new Error('oar_project_untrusted');
    return true;
  } } : undefined);
  checkErrors(loader.getExtensions());
  if (loader.getSkills().diagnostics.some(diagnostic => diagnostic.type === 'error')) {
    throw new Error('oar_extension_failed');
  }
  // OAR 0.52.0 does not load Pi CLI's native MCP/codemode/tool-search
  // built-ins. Keep those configurations native instead of silently losing tools.
  let nativeBuiltins = (settingsManager.getDefaultTools() ?? []).some(name =>
    ['codemode', 'tool_search'].includes(name));
  nativeBuiltins ||= loader.getExtensions().runtime.mcpServers.list().length > 0;
  for (const file of [path.join(agentDir, 'mcp.json'), path.join(cwd, '.pi', 'mcp.json')]) {
    try { await fs.access(file); nativeBuiltins = true; }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
  return { nativeBuiltins };
}

export async function importLegacySession(request, source, listSessions) {
  if (!request.legacy_native_id) return;
  const mappingFile = path.join(request.state_dir, 'session.json');
  try { await fs.access(mappingFile); return; }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  const slug = path.resolve(request.cwd).replace(/^[/\\]/u, '').replaceAll(/[/\\:]/gu, '-');
  const sourceDir = path.join(source, 'sessions', `--${slug}--`);
  const lister = listSessions ?? (await import('@earendil-works/pi-coding-agent')).SessionManager.list;
  const sessions = await lister(request.cwd, sourceDir);
  const found = sessions.find(session => session.id === request.legacy_native_id);
  if (!found || path.dirname(path.resolve(found.path)) !== path.resolve(sourceDir)) {
    throw new Error('oar_legacy_session_missing');
  }
  const destinationDir = path.join(request.state_dir, 'sessions', `--${slug}--`);
  await fs.mkdir(destinationDir, { recursive: true, mode: 0o700 });
  const destination = path.join(destinationDir, path.basename(found.path));
  await fs.copyFile(found.path, destination);
  await fs.chmod(destination, 0o600);
  await fs.writeFile(mappingFile, JSON.stringify({ id: found.id, history_replayed: false }), { mode: 0o600 });
}

export function validateRequest(request) {
  if (!request || typeof request !== 'object' || Array.isArray(request) ||
      typeof request.prompt !== 'string' || !request.prompt.trim() ||
      typeof request.resume_prompt !== 'string' ||
      typeof request.cwd !== 'string' || !path.isAbsolute(request.cwd) ||
      typeof request.state_dir !== 'string' || !path.isAbsolute(request.state_dir) ||
      typeof request.provider !== 'string' ||
      typeof request.model !== 'string' ||
      (request.config_id !== undefined &&
       (typeof request.config_id !== 'string' || !/^[a-f0-9]{32}$/.test(request.config_id))) ||
      (request.legacy_native_id !== undefined &&
       (typeof request.legacy_native_id !== 'string' || !/^[a-zA-Z0-9_-]{1,200}$/.test(request.legacy_native_id)))) throw new Error('oar_invalid_request');
  return request;
}

export async function execute(request, runtime, installation, emit, signal, waitForTurn) {
  validateRequest(request);
  const mappingFile = path.join(request.state_dir, 'session.json');
  let nativeId;
  let historyReplayed = Boolean(request.history_replayed);
  try {
    const saved = JSON.parse(await fs.readFile(mappingFile, 'utf8'));
    if (typeof saved.id !== 'string' || !saved.id || (saved.runtime && saved.runtime !== 'oar')) throw new Error('oar_invalid_session');
    nativeId = saved.id;
    historyReplayed = Boolean(saved.history_replayed);
  } catch (error) { if (error.code !== 'ENOENT') throw error; }
  const options = { cwd: request.cwd };
  // Resumed sessions own their model; a changed application default must not replace it.
  if (!nativeId) {
    if (!request.provider || !request.model) throw new Error('oar_model_unavailable');
    options.model = `${request.provider}/${request.model}`;
  }
  let session;
  let resumed = false;
  try {
    if (nativeId) {
      try { session = await runtime.session(installation, { ...options, resume: nativeId }); resumed = true; }
      catch (error) {
        if (error.name !== 'SessionNotFoundError') throw error;
        if (request.legacy_native_id) throw new Error('oar_legacy_session_missing');
        // Rebuilding is a new native session: pin the application's saved model,
        // never inherit a possibly changed local default. Legacy configurations
        // that cannot be determined must fail instead of reporting a new success.
        if (!request.provider || !request.model) throw new Error('oar_legacy_session_missing');
        options.model = `${request.provider}/${request.model}`;
        historyReplayed = Boolean(request.history_replayed);
      }
    }
    session ??= await runtime.session(installation, options);
    if (signal?.aborted) throw new Error('oar_stopped');
    emit({ type: 'oar_session', id: session.id, resumed, runtime: 'oar-pi' });
    session.events(event => {
      // Never forward raw event payloads, tool arguments or provider diagnostics.
      if (event.agentPath?.length) return;
      if (event.kind === 'text_delta') emit({ type: 'text_delta', delta: event.text });
      if (event.kind === 'tool_call_started') emit({ type: 'oar_tool', tool: event.tool });
    });
    const promptAndWait = waitForTurn ?? (await import('@botiverse/oar')).promptAndWait;
    const result = await promptAndWait(session, resumed ? request.resume_prompt : request.prompt, { signal });
    if (result.kind !== 'ended' || result.outcome?.kind !== 'completed') {
      const reason = result.reason || result.outcome?.reason || '';
      const failure = result.failure ?? result.outcome?.failure;
      const code = failure === 'model_unavailable' ? 'oar_model_unavailable'
        : /No API key|not authenticated|unauthenticated|login/i.test(reason)
          ? 'oar_auth_required' : signal?.aborted ? 'oar_stopped' : 'oar_turn_failed';
      emit({ type: 'oar_result', outcome: 'failed', error: code });
      return false;
    }
    if (typeof result.text !== 'string' || !result.text.trim()) {
      emit({ type: 'oar_result', outcome: 'failed', error: 'oar_empty_answer' });
      return false;
    }
    await session.dispose();
    const id = session.id;
    session = undefined;
    // Atomic mapping only for a completed turn. Failed migrations keep the old history.
    const tmp = `${mappingFile}.tmp`;
    await fs.writeFile(tmp, JSON.stringify({ id, runtime: 'oar', history_replayed: historyReplayed }), { mode: 0o600 });
    await fs.rename(tmp, mappingFile);
    // Explicit terminal result is mandatory. Exit 0 alone is not success.
    emit({ type: 'oar_result', outcome: 'completed', answer: result.text });
    return true;
  } finally { if (session) { try { await session.dispose(); } catch {} } }
}

/** Keep execution family stable for a conversation, including retries. */
export async function selectRoute(request, check = checkResources) {
  const routeFile = path.join(request.state_dir, 'route.json');
  let saved;
  try {
    saved = JSON.parse(await fs.readFile(routeFile, 'utf8'));
    if (!['oar', 'cli'].includes(saved.runtime)) throw new Error('oar_invalid_session');
  } catch (error) { if (error.code !== 'ENOENT') throw error; }
  if (saved?.runtime === 'cli') return saved;
  let route = { runtime: 'oar', reason: '' };
  if (request.legacy_native_id && !saved) route = { runtime: 'cli', reason: 'legacy_session' };
  else {
    try {
      const resources = await check(request.cwd, request.agentDir);
      if (resources?.nativeBuiltins || request.provider === 'llama.cpp') {
        if (saved) throw new Error('oar_resources_changed');
        route = { runtime: 'cli', reason: 'native_builtins' };
      }
    }
    catch (error) {
      if (['oar_extension_failed', 'oar_resources_changed'].includes(error.message)) throw error;
      if (saved) throw new Error('oar_trust_changed');
      route = { runtime: 'cli', reason: error.message === 'oar_project_untrusted' ? 'project_trust' : 'trust_unavailable' };
    }
  }
  if (!saved) {
    const tmp = `${routeFile}.tmp`;
    await fs.writeFile(tmp, JSON.stringify(route), { mode: 0o600 });
    await fs.rename(tmp, routeFile);
  }
  return saved ?? route;
}

/** Translate the original CLI's JSON events into the same strict private protocol. */
export async function executeCli(request, emit, signal, spawnProcess = spawn) {
  const mappingFile = path.join(request.state_dir, 'session.json');
  let saved;
  try { saved = JSON.parse(await fs.readFile(mappingFile, 'utf8')); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  if (saved && (typeof saved.id !== 'string' || !/^[a-zA-Z0-9_-]{1,200}$/.test(saved.id) || (saved.runtime && saved.runtime !== 'cli'))) {
    throw new Error('oar_invalid_session');
  }
  const nativeId = saved?.id ?? randomUUID();
  const slug = path.resolve(request.cwd).replace(/^[/\\]/u, '').replaceAll(/[/\\:]/gu, '-');
  const sessionDir = path.join(request.state_dir, 'sessions', `--${slug}--`);
  await fs.mkdir(sessionDir, { recursive: true, mode: 0o700 });
  // --session-id would recreate a missing old session: --session requires it.
  const sessionArgs = saved ? ['--session', nativeId] : ['--session-id', nativeId];
  const cliFile = fileURLToPath(new URL('bundle/cli.js', import.meta.resolve('@earendil-works/pi-coding-agent')));
  const args = [cliFile, '-p', '--mode', 'json', '--session-dir', sessionDir,
    ...sessionArgs];
  if (!saved) {
    if (!request.provider || !request.model) throw new Error('oar_model_unavailable');
    args.push('--provider', request.provider, '--model', request.model);
  }
  if (signal?.aborted) throw new Error('oar_stopped');
  const child = spawnProcess(process.execPath, args, { cwd: request.cwd, env: process.env, stdio: ['pipe','pipe','pipe'] });
  const stop = () => child.kill('SIGTERM');
  signal?.addEventListener('abort', stop, {once:true});
  if (signal?.aborted) stop();
  let answer = '', ended = false, failure = '', observedId = '', malformed = false, raw = '';
  const handle = line => {
    if (!line.trim()) return;
    let event;
    try { event = JSON.parse(line); } catch { malformed = true; stop(); return; }
    if (event.type === 'session') {
      observedId = typeof event.id === 'string' ? event.id : '';
      emit({ type: 'oar_session', id: observedId, resumed: Boolean(saved), runtime: 'pi-cli' });
    } else if (event.type === 'message_update' && event.assistantMessageEvent?.type === 'text_delta') {
      emit({ type: 'text_delta', delta: event.assistantMessageEvent.delta });
    } else if (event.type === 'tool_execution_start' && typeof event.toolName === 'string') {
      emit({ type: 'oar_tool', tool: event.toolName });
    } else if (event.type === 'message_end' && event.message?.role === 'assistant') {
      const message = event.message;
      if (message.stopReason === 'error' || message.stopReason === 'aborted') {
        failure = /No API key|not authenticated|login/i.test(message.errorMessage ?? '') ? 'oar_auth_required' : 'oar_turn_failed';
      }
      answer = Array.isArray(message.content) ? message.content.filter(c => c.type === 'text').map(c => c.text).join('') : '';
    } else if (event.type === 'agent_end') ended = true;
  };
  child.stdout.setEncoding('utf8');
  child.stdout.on('data', chunk => {
    raw += chunk;
    if (Buffer.byteLength(raw) > MAX_REQUEST_BYTES) { malformed = true; raw = ''; stop(); return; }
    let index;
    while ((index = raw.indexOf('\n')) >= 0) { const line = raw.slice(0,index);raw = raw.slice(index+1);handle(line); }
  });
  // Classify startup errors without retaining or exposing provider diagnostics.
  child.stderr.setEncoding('utf8');
  child.stderr.on('data', chunk => {
    if (/No API key|not authenticated|unauthenticated|login required/i.test(chunk)) failure = 'oar_auth_required';
    if (/Session.*not found|Session file not found/i.test(chunk) && saved) failure = 'oar_legacy_session_missing';
  });
  child.stdin.on('error', () => {});
  const exited = new Promise((resolve, reject) => { child.once('error',reject);child.once('close',resolve); });
  child.stdin.end(saved ? request.resume_prompt : request.prompt);
  let code;
  try { code = await exited; if (raw.trim()) handle(raw); }
  finally { signal?.removeEventListener('abort', stop); }
  if (signal?.aborted) throw new Error('oar_stopped');
  if (code !== 0 || malformed || !ended || failure || !answer.trim() || observedId !== nativeId) {
    emit({ type: 'oar_result', outcome: 'failed', error: failure || 'oar_turn_failed' });
    return false;
  }
  const tmp = `${mappingFile}.tmp`;
  await fs.writeFile(tmp, JSON.stringify({ id: nativeId, runtime: 'cli',
    history_replayed: saved ? Boolean(saved.history_replayed) : Boolean(request.history_replayed) }), { mode: 0o600 });
  await fs.rename(tmp, mappingFile);
  emit({ type: 'oar_result', outcome: 'completed', answer });
  return true;
}

async function main() {
  const writeProtocol = process.stdout.write.bind(process.stdout);
  const emit = value => writeProtocol(`${JSON.stringify(value)}\n`);
  // Extensions are not protocol producers. Silence their logs, including direct
  // writes; only our whitelisted fields can leave this process.
  const discard = (_chunk, encoding, callback) => { (typeof encoding === 'function' ? encoding : callback)?.(); return true; };
  process.stdout.write = discard;
  process.stderr.write = discard;
  const abort = new AbortController();
  for (const signame of ['SIGTERM', 'SIGINT']) process.on(signame, () => abort.abort());
  let agentDir;
  try {
    if (Number(process.versions.node.split('.')[0]) < 24) throw new Error('oar_node_version');
    process.stdin.setEncoding('utf8');
    let raw = '';
    for await (const chunk of process.stdin) {
      raw += chunk;
      if (Buffer.byteLength(raw) > MAX_REQUEST_BYTES) throw new Error('oar_invalid_request');
    }
    const request = validateRequest(JSON.parse(raw));
    // Native Pi keys session directories by the physical process cwd (macOS
    // /var aliases /private/var); use the same identity for trust and resume.
    request.cwd = await fs.realpath(request.cwd);
    const source = process.env.NEWS_READER_PI_AGENT_DIR || process.env.PI_CODING_AGENT_DIR || path.join(os.homedir(), '.pi', 'agent');
    agentDir = await prepareConfig(source, request.state_dir, request.config_id);
    delete process.env.PI_PACKAGE_DIR;
    process.env.OAR_PI_AGENT_DIR = agentDir;
    process.env.PI_CODING_AGENT_DIR = agentDir;
    request.agentDir = agentDir;
    const route = await selectRoute(request);
    await importLegacySession(request, source);
    if (route.runtime === 'cli') {
      process.exitCode = await executeCli(request, emit, abort.signal) ? 0 : 1;
      return;
    }
    const { defaultRuntimes } = await import('@botiverse/oar');
    const runtime = defaultRuntimes.require('pi');
    const installation = await runtime.installation();
    if (installation.kind !== 'available') throw new Error('oar_dependency_missing');
    process.exitCode = await execute(request, runtime, installation, emit, abort.signal) ? 0 : 1;
  } catch (error) {
    const code = error.failure === 'model_unavailable' ? 'oar_model_unavailable'
      : error.failure === 'unauthenticated' ? 'oar_auth_required'
      : error.code === 'ERR_MODULE_NOT_FOUND' ? 'oar_dependency_missing'
      : /^oar_[a-z_]+$/.test(error.message) ? error.message : 'oar_runtime_failed';
    emit({ type: 'oar_result', outcome: 'failed', error: code });
    process.exitCode = 1;
  } finally {
    if (agentDir) await fs.rm(agentDir, { recursive: true, force: true });
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();
