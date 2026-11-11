// Used by the live release check: reject any JavaScript dependency outside
// this checkout. No changes to HOME, global packages or personal adapters.
import {registerHooks} from 'node:module';
import {fileURLToPath} from 'node:url';
import {realpathSync} from 'node:fs';
import path from 'node:path';
const root = realpathSync(fileURLToPath(new URL('..', import.meta.url))) + path.sep;
registerHooks({resolve(specifier, context, nextResolve) {
  const result = nextResolve(specifier, context);
  if (result.url.startsWith('file:') && !realpathSync(fileURLToPath(result.url)).startsWith(root)) {
    throw new Error('Isolation check rejected an external module: ' + specifier);
  }
  return result;
}});
