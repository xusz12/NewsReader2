import {defineConfig} from 'vitest/config';
import {fileURLToPath} from 'node:url';
export default defineConfig({test: {
  include: ['collection/adapters/**/*.test.js'],
  env: {NEWSREADER_SOURCE: fileURLToPath(new URL('./nr/article_fetch.py', import.meta.url))},
}});
