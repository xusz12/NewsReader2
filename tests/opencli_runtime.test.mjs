import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {mkdtempSync, writeFileSync, readFileSync, mkdirSync, rmSync, copyFileSync, symlinkSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const manifest = JSON.parse(readFileSync(path.join(root, 'collection/opencli-manifest.json')));
const run = (args, env = {}, nodeArgs = []) => spawnSync(process.execPath,
  [...nodeArgs, path.join(root, 'scripts/opencli.mjs'), ...args], {encoding: 'utf8', env: {...process.env, ...env}});

test('all required commands import successfully with external module loading prohibited', () => {
  const p = run(['--nr-check'], {NODE_OPTIONS: '--import=' + new URL('../scripts/opencli-isolation-guard.mjs', import.meta.url).href});
  assert.equal(p.status, 0, p.stdout + p.stderr);
  const report = JSON.parse(p.stdout).data;
  assert.equal(report.version, manifest.version);
  assert.equal(report.adapter_commit, manifest.adapters.commit);
  assert.equal(report.adapter_version, manifest.adapters.version);
  assert.equal(report.isolated, true);
  assert.deepEqual(report.commands, manifest.commands.map(c => c.site + ' ' + c.name));
});

test('personal adapters and startup plugins cannot override the project registry', () => {
  const temporary = mkdtempSync(path.join(tmpdir(), 'nr-personal-trap-'));
  try {
    const site = path.join(temporary, '.opencli/clis/ReutersBrowser');
    const plugin = path.join(temporary, '.opencli/plugins/trap');
    mkdirSync(site, {recursive: true}); mkdirSync(plugin, {recursive: true});
    writeFileSync(path.join(temporary, '.opencli/package.json'), '{"type":"module"}');
    const trap = 'throw new Error("PERSONAL_CODE_LOADED"); // cli( onStartup(\n';
    writeFileSync(path.join(site, 'news.js'), trap);
    writeFileSync(path.join(plugin, 'index.js'), trap);
    writeFileSync(path.join(plugin, 'package.json'), '{"type":"module","main":"index.js"}');
    // Change the API only in this child, never the real HOME or personal files.
    const preload = path.join(temporary, 'home-api.mjs');
    writeFileSync(preload, `import os from 'node:os'; import {syncBuiltinESMExports} from 'node:module'; os.homedir = () => ${JSON.stringify(temporary)}; syncBuiltinESMExports();`);
    const p = run(['--nr-check'], {}, ['--import', preload]);
    assert.equal(p.status, 0, p.stdout + p.stderr);
    assert.equal(JSON.parse(p.stdout).data.isolated, true);
  } finally { rmSync(temporary, {recursive: true, force: true}); }
});

test('runtime exposes neither plugin installation nor independent updates', () => {
  for (const args of [['plugin', 'install', 'trap'], ['update'], ['twitter', 'post', 'trap']]) {
    const p = run(args);
    assert.equal(p.status, 2);
    assert.equal(JSON.parse(p.stdout).error.code, 'UNKNOWN_COMMAND');
  }
});

test('every configured source and detail route is covered by the required command manifest', () => {
  const keys = new Set(manifest.commands.map(c => c.site + ' ' + c.name));
  for (const source of JSON.parse(readFileSync(path.join(root, 'collection/sources.json')))) {
    assert.equal(source.command[0], 'opencli');
    assert.ok(keys.has(source.command.slice(1, 3).join(' ')));
    assert.equal(source.fallback_command, undefined);
  }
  const app = readFileSync(path.join(root, 'app.py'), 'utf8');
  for (const match of app.matchAll(/\["opencli", "([^"]+)", "([^"]+)"\]/g)) {
    assert.ok(keys.has(match[1] + ' ' + match[2]), match[0]);
  }
});

test('missing dependencies, global symlinks, version drift and incomplete adapters fail closed', () => {
  const temporary = mkdtempSync(path.join(tmpdir(), 'nr-broken-install-'));
  try {
    for (const name of ['package.json', 'package-lock.json', 'scripts/opencli.mjs', 'collection/opencli-manifest.json']) {
      mkdirSync(path.dirname(path.join(temporary, name)), {recursive: true});
      copyFileSync(path.join(root, name), path.join(temporary, name));
    }
    const check = () => {
      const p = spawnSync(process.execPath, [path.join(temporary, 'scripts/opencli.mjs'), '--nr-check'], {encoding: 'utf8'});
      assert.equal(p.status, 2, p.stdout + p.stderr);
      assert.equal(JSON.parse(p.stdout).error.code, 'OPENCLI_SETUP');
      return JSON.parse(p.stdout).error.message;
    };
    check();
    const pkg = path.join(temporary, 'node_modules/@jackwener/opencli');
    mkdirSync(path.dirname(pkg), {recursive: true});
    symlinkSync(path.join(root, 'node_modules/@jackwener/opencli'), pkg);
    assert.match(check(), /不能链接到全局安装/);
    rmSync(pkg); mkdirSync(pkg);
    writeFileSync(path.join(pkg, 'package.json'), JSON.stringify({name: manifest.package, version: '0.0.0'}));
    assert.match(check(), /版本.*不一致/);
    writeFileSync(path.join(pkg, 'package.json'), JSON.stringify({name: manifest.package, version: manifest.version}));
    assert.match(check(), /adapters/);
  } finally {rmSync(temporary, {recursive: true, force: true});}
});
