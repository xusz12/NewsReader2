/**
 * Reuters article-detail — full article body + canonical metadata.
 *
 * Pairs with `reuters search` (use the `url` column to round-trip into
 * detail). Reads the in-page Fusion globalContent payload + paragraph DOM.
 */
import { ArgumentError } from '@jackwener/opencli/errors';
import { cli, Strategy } from '@jackwener/opencli/registry';
import { readArticleDetail } from './article-recovery.js';

const REUTERS_HOST = /^https?:\/\/(?:www\.)?reuters\.com\//i;

cli({
    site: 'reuters',
    name: 'article-detail',
    access: 'read',
    description: 'Reuters 路透社文章详情：标题/作者/正文文本',
    domain: 'www.reuters.com',
    strategy: Strategy.COOKIE,
    browser: true,
    navigateBefore: false,
    args: [
        { name: 'url', required: true, positional: true, help: 'Reuters article URL (must be on reuters.com)' },
    ],
    columns: ['title', 'date', 'section', 'section_path', 'authors', 'description', 'word_count', 'url', 'body'],
    func: async (page, kwargs) => {
        const url = String(kwargs.url || '').trim();
        if (!url) {
            throw new ArgumentError('Article URL cannot be empty');
        }
        if (!REUTERS_HOST.test(url)) {
            throw new ArgumentError(`URL must be on reuters.com, got ${url}`);
        }
        return [await readArticleDetail(page, url)];
    },
});
