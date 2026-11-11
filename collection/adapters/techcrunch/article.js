import { cli, Strategy } from '@jackwener/opencli/registry';
import { fetchArticleHtml } from './article-http.js';

const SITE = 'TechCrunch';
const DOMAIN_RE = /^https?:\/\/(?:www\.)?techcrunch\.com\//i;

function failure(url, errorCode, reason) {
  return {
    source: SITE,
    title: '',
    url,
    published_at: '',
    author: '',
    content: '',
    content_length: 0,
    status: 'failed',
    error_code: errorCode,
    reason,
  };
}

function decodeHtml(text) {
  return String(text || '')
    .replace(/&#8216;/g, "'")
    .replace(/&#8217;/g, "'")
    .replace(/&#8220;/g, '"')
    .replace(/&#8221;/g, '"')
    .replace(/&#8230;/g, '...')
    .replace(/&#038;/g, '&')
    .replace(/&#039;/g, "'")
    .replace(/&#(\d+);/g, (_, code) => {
      const n = Number(code);
      return Number.isFinite(n) ? String.fromCodePoint(n) : _;
    })
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'");
}

function extractMeta(html, property) {
  const escaped = property.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const re = new RegExp(`<meta[^>]+(?:property|name)=["']${escaped}["'][^>]+content=["']([^"']+)["'][^>]*>`, 'i');
  const m = html.match(re);
  return m ? decodeHtml(m[1]).trim() : '';
}

function extractTitle(html) {
  const heading = html.match(/<h1[^>]*class=["'][^"']*article-hero__title[^"']*["'][^>]*>([\s\S]*?)<\/h1>/i);
  if (heading) {
    return decodeHtml(heading[1].replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ')).trim();
  }
  return extractMeta(html, 'og:title');
}

function extractAuthor(html) {
  const byline = html.match(/<a[^>]*class=["'][^"']*article-hero__byline-link[^"']*["'][^>]*>([\s\S]*?)<\/a>/i);
  if (byline) {
    return decodeHtml(byline[1].replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ')).trim();
  }
  return extractMeta(html, 'author');
}

function extractByBalancedTag(html, tag, classNeedle) {
  const openRe = new RegExp(`<${tag}[^>]*class=["'][^"']*${classNeedle}[^"']*["'][^>]*>`, 'i');
  const openMatch = openRe.exec(html);
  if (!openMatch) return '';

  const start = openMatch.index;
  const tagRe = new RegExp(`<\\/?${tag}\\b[^>]*>`, 'gi');
  tagRe.lastIndex = start;

  let depth = 0;
  let begin = -1;
  let end = -1;
  let m;
  while ((m = tagRe.exec(html))) {
    const isClose = m[0][1] === '/';
    if (!isClose) {
      if (depth === 0 && begin === -1) begin = m.index;
      depth += 1;
    } else {
      depth -= 1;
      if (depth === 0) {
        end = tagRe.lastIndex;
        break;
      }
    }
  }
  if (begin === -1 || end === -1) return '';
  return html.slice(begin, end);
}

function toLinesFromHtml(block) {
  let cleaned = String(block || '');
  cleaned = cleaned.replace(/<script[\s\S]*?<\/script>/gi, '');
  cleaned = cleaned.replace(/<style[\s\S]*?<\/style>/gi, '');
  cleaned = cleaned.replace(/<noscript[\s\S]*?<\/noscript>/gi, '');
  cleaned = cleaned.replace(/<div[^>]*class=["'][^"']*(?:ad-unit|wp-block-tc-ads-ad-slot|newsletter-signup|article-social|wp-block-buttons)[^"']*["'][\s\S]*?<\/div>/gi, '');
  cleaned = cleaned.replace(/<aside[\s\S]*?<\/aside>/gi, '');
  cleaned = cleaned.replace(/<figure[\s\S]*?<\/figure>/gi, '');
  cleaned = cleaned.replace(/<\/p>/gi, '\n');
  cleaned = cleaned.replace(/<\/(?:h2|h3|h4|blockquote|li|ul|ol)>/gi, '\n');
  cleaned = cleaned.replace(/<br\s*\/?\s*>/gi, '\n');
  cleaned = decodeHtml(cleaned.replace(/<[^>]+>/g, ' '));

  return cleaned
    .split(/\n+/)
    .map((line) => line.replace(/\s+/g, ' ').trim())
    .filter(Boolean)
    .filter((line) => !/^(Most Popular|Events|Startups Weekly|TechCrunch Mobility|Sign up here\.?|Read More)$/i.test(line))
    .filter((line) => !/^(Image Credits?|Advertisement)$/i.test(line))
    .filter((line) => !/TechCrunch\s+Events/i.test(line))
    .filter((line) => !/The Reuters Iran Briefing newsletter/i.test(line));
}

function normalizeContent(lines) {
  const output = [];
  for (const line of lines) {
    if (!line) continue;
    if (output[output.length - 1] === line) continue;
    output.push(line);
  }
  return output.join('\n\n').trim();
}

cli({
  site: 'TechcrunchPublic',
  name: 'article',
  description: 'TechCrunch article detail by URL (title, author, published_at, content)',
  access: 'read',
  domain: 'techcrunch.com',
  strategy: Strategy.PUBLIC,
  browser: false,
  args: [
    { name: 'url', positional: true, required: true, help: 'TechCrunch article URL' },
  ],
  columns: ['source', 'title', 'url', 'published_at', 'author', 'content', 'content_length', 'status'],
  func: async (kwargs) => {
    const url = String(kwargs.url || '').trim();
    if (!url) return [failure('', 'MALFORMED_URL', 'URL cannot be empty')];
    if (!DOMAIN_RE.test(url)) return [failure(url, 'UNSUPPORTED_URL', 'Only techcrunch.com article URL is supported')];

    let html;
    try {
      html = await fetchArticleHtml(url);
    } catch (error) {
      return [failure(url, error.code || 'FETCH_FAILED', error.message || String(error))];
    }
    const block = extractByBalancedTag(html, 'div', 'entry-content');
    if (!block) return [failure(url, 'CONTENT_UNAVAILABLE', 'Article content container not found')];

    const content = normalizeContent(toLinesFromHtml(block));
    const contentLength = content.length;
    if (contentLength < 200) {
      return [failure(url, 'CONTENT_TOO_SHORT', `Extracted content length ${contentLength} < 200`)];
    }

    return [{
      source: SITE,
      title: extractTitle(html),
      url,
      published_at: extractMeta(html, 'article:published_time'),
      author: extractAuthor(html),
      content,
      content_length: contentLength,
      status: 'success',
    }];
  },
});
