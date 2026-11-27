import fs from 'node:fs/promises';
import {execFile} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {promisify} from 'node:util';
import {parseDocument, Document} from 'yaml';
import {decodeSessionMapping} from './oar_assistant_context.mjs';
import {dshRuntime} from '@botiverse/oar/runtimes/dsh';

const installGuide = '未找到本机 DSH。macOS 可安装官方 DeepSeek Harness 桌面版，也可用 npm install -g @deepseek-ai/dsh 安装 CLI。已有安装可用 OAR_DSH_BIN 指定 dsh 可执行文件。';
const desktopCli = 'Contents/Resources/runtime/cli/bin/dsh';
const profileName = 'cordis.patch.yml';

async function optional(file, read) {
  try { return await read(file); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
}

async function readRoutes(file) {
  const input = await optional(file, path => fs.readFile(path, 'utf8'));
  if (!input) return [];
  const doc = parseDocument(input, {logLevel: 'silent'});
  if (doc.errors.length || !Array.isArray(doc.contents?.items)) throw new Error('oar_dsh_incompatible');
  const isRoute = row => /^(llm(?:-|$)|agent-default-model$|deepseek-llm-)/u.test(row?.get?.('id') || '');
  const rows = [];
  for (const row of doc.contents.items) {
    if (isRoute(row)) rows.push(row);
    const inserted = row?.get?.('insert')?.items?.filter(isRoute);
    if (inserted?.length) {
      const copy = row.clone();
      copy.get('insert').items = inserted;
      rows.push(copy);
    }
  }
  return rows;
}

async function* desktopBundles() {
  const name = 'DeepSeek Harness.app';
  const usual = [path.join(os.homedir(), 'Applications', name), path.join('/Applications', name)];
  yield* usual;
  const lookup = 'ObjC.import("AppKit"); const url = $.NSWorkspace.sharedWorkspace.URLForApplicationWithBundleIdentifier("com.deepseek.dsh"); url.isNil() ? "" : ObjC.unwrap(url.path)';
  const {stdout} = await promisify(execFile)('/usr/bin/osascript', ['-l', 'JavaScript', '-e', lookup], {timeout: 2000})
    .catch(() => ({stdout: ''}));
  const registered = stdout.trim();
  if (path.isAbsolute(registered) && !usual.includes(registered)) yield registered;
}

export async function dshInstallation() {
  let result = await dshRuntime.installation();
  if (result.kind !== 'available' && process.platform === 'darwin' && !process.env.OAR_DSH_BIN) {
    for await (const bundle of desktopBundles()) {
      const candidate = await dshRuntime.installation({env: {OAR_DSH_BIN: path.join(bundle, desktopCli)}})
        .catch(() => ({kind: 'unsupported'}));
      if (candidate.kind === 'available') return candidate;
      if (candidate.kind === 'unsupported') result = candidate;
    }
  }
  if (result.kind !== 'available') throw new Error(result.kind === 'unsupported' ? 'oar_dsh_incompatible' : 'oar_dsh_missing');
  return result;
}

export async function prepareDsh(request) {
  const source = path.resolve(process.env.DSH_HOME || path.join(os.homedir(), '.dsh'));
  await fs.mkdir(request.state_dir, {recursive: true, mode: 0o700});
  const home = await fs.mkdtemp(path.join(request.state_dir, 'config-' + (request.config_id ? request.config_id + '-' : '')));
  try {
    for (const name of ['.credentials.yaml', '.env']) {
      await optional(path.join(source, name), async file => {
        const dest = path.join(home, name);
        await fs.copyFile(file, dest);
        await fs.chmod(dest, 0o600);
      });
    }
    const profile = path.join(home, 'profiles', 'acp');
    await fs.mkdir(profile, {recursive: true, mode: 0o700});
    const sourceProfile = name => path.join(source, 'profiles', name, profileName);
    const acp = await readRoutes(sourceProfile('acp'));
    const global = await readRoutes(path.join(source, profileName));
    let legacy = [];
    if (!acp.length && !global.length) {
      for (const type of ['desktop', 'web']) {
        legacy = await readRoutes(sourceProfile(type));
        if (legacy.length) break;
      }
    }
    const modules = path.join(source, 'profiles', 'acp', 'node_modules');
    if (await optional(modules, p => fs.stat(p))) await fs.symlink(modules, path.join(profile, 'node_modules'));
    const sessions = path.join(home, 'sessions');
    if (['news_chat', 'personal_assistant'].includes(request.task)) {
      const saved = await optional(path.join(request.state_dir, 'session.json'), file => fs.readFile(file, 'utf8'));
      if (saved !== undefined) {
        const mapping = decodeSessionMapping(saved);
        if (mapping.runtime !== 'oar-dsh' || (mapping.generation && !/^dsh-sessions-[a-f0-9-]{36}$/.test(mapping.generation))) {
          throw new Error('oar_invalid_session');
        }
        await optional(path.join(request.state_dir, mapping.generation || 'sessions'),
          prior => fs.cp(prior, sessions, {recursive: true}));
        request.previousGeneration = mapping.generation;
      }
    }
    const servers = [];
    if (request.web_tools) servers.push('nr_web');
    if (request.nr_tools) servers.push(request.task === 'report' ? 'newsreader_report' : 'nr_assistant');
    const policy = {home, servers, ...(request.action === 'metadata' ? {
      route: {provider: request.provider, model: request.model}, metadata: path.join(home, 'metadata.json'),
    } : {})};
    const doc = new Document([]);
    doc.contents.items.push(...legacy, ...acp, ...global);
    doc.add({id: 'session-persistence-jsonl', config: {root: sessions}});
    doc.add({insert: [{id: 'newsreader-policy', name: new URL('./dsh_policy.mjs', import.meta.url).href, config: policy}]});
    await fs.writeFile(path.join(profile, profileName), doc.toString(), {mode: 0o600});
    return home;
  } catch (error) {
    await fs.rm(home, {recursive: true, force: true});
    throw error;
  }
}

export async function commitDsh(request) {
  const generation = 'dsh-sessions-' + randomUUID();
  await fs.rename(path.join(request.agentDir, 'sessions'), path.join(request.state_dir, generation));
  return generation;
}

async function query(request) {
  const installation = await dshInstallation();
  const root = await fs.mkdtemp(path.join(request.temp_dir || os.tmpdir(), 'newsreader-dsh-models-'));
  try {
    const home = await prepareDsh({...request, state_dir: root});
    const env = {DSH_HOME: home};
    if (request.action === 'metadata') {
      const session = await dshRuntime.session(installation, {cwd: root, env, model: request.provider + '/' + request.model});
      try { return JSON.parse(await fs.readFile(path.join(home, 'metadata.json'), 'utf8')); }
      finally { await session.dispose(); }
    }
    const result = await dshRuntime.listModels(installation, {cwd: root, env, timeoutMs: 30000});
    if (result.kind !== 'ok') throw new Error('oar_dsh_incompatible');
    const options = result.models.map(model => ({
      value: model.id.slice(model.provider.id.length + 1), provider: model.provider.id,
      label: model.displayName || model.id, reasoning_efforts: model.effortLevels || [],
    }));
    const desktop = installation.command.endsWith('/' + desktopCli);
    const notice = desktop ? `已从 DeepSeek Harness 桌面版 ${installation.version} 读取 ${options.length} 个模型。` : '';
    return {options, error: '', notice: options.length ? notice : '请先在本机 DSH 配置服务商与模型。'};
  } finally { await fs.rm(root, {recursive: true, force: true}); }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  for (const signal of ['SIGTERM', 'SIGINT']) process.once(signal, () => process.exit(1));
  let raw = '';
  for await (const chunk of process.stdin) raw += chunk;
  try { console.log(JSON.stringify(await query(JSON.parse(raw)))); }
  catch (error) {
    const missing = error.message === 'oar_dsh_missing';
    console.log(JSON.stringify({options: [], error: missing ? error.message : 'oar_dsh_incompatible',
      notice: missing ? installGuide : '无法读取 DSH 原生模型配置。请检查 DSH 版本、服务商和模型。'}));
  }
}
