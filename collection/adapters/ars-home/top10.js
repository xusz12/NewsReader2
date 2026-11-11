import { cli, Strategy } from '@jackwener/opencli/registry';
import { fetchRssFeed } from '../rss-feed.js';

cli({
  site: 'ArsPublic',
  name: 'news',
  description: 'Ars Technica top stories (title, time, url) with Asia/Shanghai time',
  access: 'read',
  domain: 'feeds.arstechnica.com',
  strategy: Strategy.PUBLIC,
  browser: false,
  args: [{ name: 'limit', type: 'int', default: 10, help: 'Number of items to return (max 30)' }],
  columns: ['rank', 'title', 'time', 'url'],
  func: async kwargs => fetchRssFeed({ url: 'https://feeds.arstechnica.com/arstechnica/index', source: 'Ars Technica', limit: kwargs.limit }),
});
