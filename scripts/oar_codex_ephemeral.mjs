import fs from 'node:fs';
import { registerHooks } from 'node:module';

export function ephemeralSource(source) {
  const seam = '                ...instructionParams,\n                ...configParams({ ...effortConfig, ...mcpConfig }),';
  if (source.split(seam).length !== 2) throw new Error('oar_compatibility_mismatch');
  return source.replace(seam,
    '                ...(options.nrEphemeral === true ? { ephemeral: true } : {}),\n' + seam);
}

let installed = false;
export function installEphemeralOption() {
  if (installed) return;
  const root = new URL('../../', import.meta.resolve('@botiverse/oar/runtimes/codex'));
  const manifest = JSON.parse(fs.readFileSync(new URL('../package.json', root), 'utf8'));
  if (manifest.version !== '0.128.1') throw new Error('oar_compatibility_mismatch');
  const target = new URL('runtimes/codex/open.js', root).href;
  // Validate before loading the runtime so an upgrade cannot silently persist
  // an archival conversation in the user's native Codex session history.
  ephemeralSource(fs.readFileSync(new URL(target), 'utf8'));
  registerHooks({ load(url, context, nextLoad) {
    const result = nextLoad(url, context);
    if (url !== target) return result;
    const source = typeof result.source === 'string' ? result.source : Buffer.from(result.source).toString('utf8');
    return { ...result, source: ephemeralSource(source) };
  } });
  installed = true;
}
