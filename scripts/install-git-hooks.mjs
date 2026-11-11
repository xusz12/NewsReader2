import {execFileSync} from 'node:child_process';
import {existsSync, mkdirSync, readdirSync, symlinkSync, realpathSync} from 'node:fs';
import path from 'node:path';
const git = args => execFileSync('git', args, {encoding: 'utf8'}).trim();
const root = git(['rev-parse', '--show-toplevel']);
const bridge = path.resolve(root, git(['rev-parse', '--git-path', 'newsreader-hooks']));
let current = '';
try {current = git(['config', '--get', 'core.hooksPath']);} catch {}
if (current === bridge) {
  console.log('OpenCLI 推送检查已启用，原有 Git hooks 已保留。');
} else if (current && current !== '.githooks') {
  const previous = path.resolve(root, current);
  if (existsSync(path.join(previous, 'pre-push'))) {
    throw new Error('已有自定义 pre-push；请在其中调用 python3 scripts/check_opencli_release.py。');
  }
  // Preserve existing hooks (for example the user's Chinese commit-message
  // check) through local symlinks, without changing the global hooks directory.
  mkdirSync(bridge, {recursive: true});
  const hooks = readdirSync(previous).filter(name => !name.startsWith('.'));
  for (const name of [...hooks, 'pre-push']) {
    const target = name === 'pre-push' ? path.join(root, '.githooks/pre-push') : path.join(previous, name);
    const destination = path.join(bridge, name);
    if (!existsSync(destination)) symlinkSync(target, destination);
    else if (realpathSync(destination) !== realpathSync(target)) throw new Error('现有本地 hook 桥接冲突：' + name);
  }
  git(['config', '--local', 'core.hooksPath', bridge]);
  console.log('已启用 OpenCLI 推送检查，并保留原有 Git hooks。');
} else {
  git(['config', '--local', 'core.hooksPath', '.githooks']);
  console.log('已启用推送前的 OpenCLI 版本及完整测试记录检查。');
}
