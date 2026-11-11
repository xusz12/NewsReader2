#!/usr/bin/env node
// Pinned integration with OpenCLI 2.0.3. Upstream's main.js discovers personal
// adapters/plugins and performs update checks; deliberately do not import it.
import { readFileSync, realpathSync } from 'node:fs';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const readJSON = name => JSON.parse(readFileSync(name, 'utf8'));
const fail = (code, message) => {
  process.stdout.write(JSON.stringify({ok: false, error: {code, message}}) + '\n');
  process.exitCode = 2;
};
try {
  if (Number(process.versions.node.split('.')[0]) < 24) throw new Error('需要 Node.js 24 或更高版本。');
  const manifest = readJSON(path.join(root, 'collection/opencli-manifest.json'));
  const packageRoot = path.join(root, 'node_modules/@jackwener/opencli');
  // Also reject a symlink to somebody's global install.
  if (!realpathSync(packageRoot).startsWith(realpathSync(root) + path.sep + 'node_modules' + path.sep)) {
    throw new Error('opencli 必须安装在当前项目内，不能链接到全局安装。');
  }
  const installed = readJSON(path.join(packageRoot, 'package.json'));
  const declared = readJSON(path.join(root, 'package.json')).dependencies[manifest.package];
  const locked = readJSON(path.join(root, 'package-lock.json')).packages['node_modules/@jackwener/opencli'];
  if (installed.name !== manifest.package || installed.version !== manifest.version || declared !== manifest.version || locked?.version !== manifest.version) {
    throw new Error('opencli 版本与项目锁定版本不一致。');
  }
  const args = process.argv.slice(2);
  if (args[0] === '--version' || args[0] === '-V') {
    process.stdout.write(installed.version + '\n');
  } else {
    const bundledRoot = path.join(root, 'collection/adapters');
    for (const [name, hash] of Object.entries(manifest.adapters.files)) {
      const file = path.resolve(bundledRoot, name);
      if (!file.startsWith(bundledRoot + path.sep) || createHash('sha256').update(readFileSync(file)).digest('hex') !== hash) {
        throw new Error(`内置 adapter 缺失或校验不通过：${name}`);
      }
    }
    const internal = name => import(pathToFileURL(path.join(packageRoot, 'dist/src', name)).href);
    const {getRegistry} = await internal('registry.js');
    const {installNodeNetwork} = await internal('node-network.js');
    installNodeNetwork();
    // Explicit imports, no user discovery, plugin registration, compatibility
    // symlinks, automatic downloads or background upgrades. Browser profiles
    // and login sessions remain under upstream's normal browser management.
    const loaded = new Set();
    for (const command of manifest.commands) {
      const base = command.origin === 'builtin' ? path.join(packageRoot, 'clis') : bundledRoot;
      const file = path.resolve(base, command.module);
      if (!file.startsWith(base + path.sep)) throw new Error('非法 adapter 路径。');
      if (!loaded.has(file)) {
        await import(pathToFileURL(file).href);
        loaded.add(file);
      }
    }
    for (const {site, name} of manifest.commands) {
      const registered = getRegistry().get(`${site}/${name}`);
      if (typeof registered?.func !== 'function' || registered.access !== 'read') {
        throw new Error(`必需命令不可用：${site} ${name}`);
      }
    }
    if (args[0] === '--nr-check') {
      process.stdout.write(JSON.stringify({ok: true, data: {
        version: installed.version, isolated: true, adapter_commit: manifest.adapters.commit,
        adapter_version: manifest.adapters.version,
        commands: manifest.commands.map(({site, name}) => `${site} ${name}`),
      }}) + '\n');
    } else if (args[0] === 'doctor' ||
        (args[0] === 'daemon' && ['status', 'restart', 'stop'].includes(args[1])) ||
        manifest.commands.some(({site, name}) => args[0] === site && args[1] === name)) {
      const {runCli} = await internal('cli.js');
      await runCli(path.join(packageRoot, 'clis'), bundledRoot);
    } else {
      fail('UNKNOWN_COMMAND', '此入口只提供 NewsReader 必需命令；用 --nr-check 检查完整性。');
    }
  }
} catch (error) {
  fail('OPENCLI_SETUP', `${error.message} 请在项目目录执行 npm ci --ignore-scripts；仍有问题时检查项目文件完整性。`);
}
