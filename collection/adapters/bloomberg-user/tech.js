import { cli, Strategy } from '@jackwener/opencli/registry';
import { fetchRssFeed } from '../rss-feed.js';

// Keep the other BloombergUser commands on their existing shared helper.
cli({
  site: 'BloombergUser',
  name: 'tech',
  description: 'Bloomberg Tech top stories via RSS with Asia/Shanghai time',
  access: 'read',
  domain: 'feeds.bloomberg.com',
  strategy: Strategy.PUBLIC,
  browser: false,
  args: [{ name: 'limit', type: 'int', default: 10, help: 'Number of feed items to return (max 30)' }],
  columns: ['rank', 'title', 'time', 'url', 'summary'],
  func: async kwargs => fetchRssFeed({
    url: 'https://feeds.bloomberg.com/technology/news.rss', source: 'Bloomberg', limit: kwargs.limit, summary: true,
  }),
});
