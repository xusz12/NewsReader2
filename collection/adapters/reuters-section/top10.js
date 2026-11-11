import { ArgumentError } from '@jackwener/opencli/errors';
import { cli, Strategy } from '@jackwener/opencli/registry';
import { readSectionNews } from './news-recovery.js';

cli({
  site: 'ReutersBrowser',
  name: 'news',
  description: 'Reuters section top stories with Asia/Shanghai local time',
  access: 'read',
  domain: 'www.reuters.com',
  strategy: Strategy.COOKIE,
  browser: true,
  navigateBefore: false,
  args: [
    { name: 'url', type: 'str', required: true, positional: true, help: 'Reuters section URL' },
    { name: 'limit', type: 'int', default: 10, help: 'Number of items to return (max 50)' },
  ],
  columns: ['rank', 'title', 'time', 'url'],
  func: async (page, kwargs) => {
    const sectionUrl = String(kwargs.url || '').trim();
    let parsed;
    try { parsed = new URL(sectionUrl); } catch {
      throw new ArgumentError('A valid Reuters section URL is required');
    }
    if (!['http:', 'https:'].includes(parsed.protocol) || !/(^|\.)reuters\.com$/i.test(parsed.hostname)
        || parsed.username || parsed.password) {
      throw new ArgumentError('Only HTTP(S) reuters.com URLs are supported');
    }
    const count = Math.max(1, Math.min(Number(kwargs.limit) || 10, 50));
    const cleanPath = parsed.pathname.replace(/^\/+|\/+$/g, '');
    const alias = cleanPath ? cleanPath.replace(/\//g, '-') : 'world';
    return readSectionNews(page, alias, count);
  },
});
