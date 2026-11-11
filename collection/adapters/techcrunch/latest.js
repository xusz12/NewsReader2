import { cli, Strategy } from '@jackwener/opencli/registry';
import { fetchRssFeed } from '../rss-feed.js';

cli({
  site: 'TechcrunchPublic',
  name: 'news',
  description: 'TechCrunch latest stories via RSS feed (title, time, url) with Asia/Shanghai time',
  access: 'read',
  domain: 'techcrunch.com',
  strategy: Strategy.PUBLIC,
  browser: false,
  args: [{ name: 'limit', type: 'int', default: 10, help: 'Number of items to return (max 30)' }],
  columns: ['rank', 'title', 'time', 'url'],
  func: async kwargs => fetchRssFeed({ url: 'https://techcrunch.com/feed/', source: 'TechCrunch', limit: kwargs.limit }),
});
