import { CliError } from '@jackwener/opencli/errors';

// One request per invocation. NewsFlow owns retries and backoff.
export const RSS_TIMEOUT_MS = 15_000;
const TRANSIENT_STATUSES = new Set([408, 429, 500, 502, 503, 504]);
const USER_AGENT = 'Mozilla/5.0 (compatible; opencli-custom)';

export function rssError(code, message, { status = null, retryable = false, retryAt = null } = {}) {
  const error = new CliError(code, message, retryable ? 'NewsFlow may retry this temporary failure after a delay.' : 'Resolve the reported source error before retrying.');
  // opencli 2.0 serializes code/message/hint/cause, but drops arbitrary fields.
  // Keep machine metadata in cause and a readable message/hint for CLI users.
  error.cause = new Error(JSON.stringify({
    schema: 'newsflow.rss-error/v1', http_status: status, retryable, retry_at: retryAt,
  }));
  return error;
}

export function parseRetryAfter(value, now = Date.now()) {
  if (!value?.trim()) return null;
  const text = value.trim();
  let until;
  if (/^\d+$/.test(text)) {
    // Conservatively cap extreme values at a year, well beyond this run's budget.
    until = now + Math.min(Number(text), 365 * 86400) * 1000;
  } else if (/^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)/i.test(text)) {
    until = Date.parse(text);
  }
  return Number.isFinite(until) ? new Date(Math.max(now, until)).toISOString() : null;
}

export function decodeEntities(text) {
  return String(text).replace(/&(#x[\da-f]+|#\d+|amp|lt|gt|quot|apos|nbsp);/gi, (entity, key) => {
    const lower = key.toLowerCase();
    if (lower[0] !== '#') return { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ' }[lower];
    const point = Number.parseInt(lower.slice(lower[1] === 'x' ? 2 : 1), lower[1] === 'x' ? 16 : 10);
    return point > 0 && point <= 0x10ffff && !(point >= 0xd800 && point <= 0xdfff) ? String.fromCodePoint(point) : entity;
  });
}

function cleanText(text) {
  // Strip real HTML before decoding escaped angle brackets in news headlines.
  return decodeEntities(String(text).trim().replace(/^<!\[CDATA\[([\s\S]*)\]\]>$/, '$1')
    .replace(/<[^>]*>/g, ' ')).replace(/\s+/g, ' ').trim();
}
function tag(block, name) {
  return cleanText(block.match(new RegExp(`<${name}(?:\\s[^>]*)?>([\\s\\S]*?)<\\/${name}>`, 'i'))?.[1] || '');
}
function articleUrl(value) {
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : ''; }
  catch { return ''; }
}
function shanghaiTime(raw) {
  if (!raw || Number.isNaN(new Date(raw).getTime())) return raw;
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
  }).formatToParts(new Date(raw));
  const p = Object.fromEntries(parts.map(part => [part.type, part.value]));
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}:${p.second}`;
}

export function parseRss(xml, { source, limit = 10, summary = false }) {
  // Validate the RSS 2.0 envelope before extracting items. HTML must never look
  // like a successful empty result. Do not classify article text as a CAPTCHA.
  const envelope = xml.replace(/^\uFEFF/, '').replace(/<\?xml[\s\S]*?\?>/gi, '').replace(/<!--[\s\S]*?-->/g, '').trim();
  if (!/^<rss\b[^>]*>[\s\S]*<\/rss>$/i.test(envelope) || !/<channel\b[^>]*>[\s\S]*<\/channel>/i.test(envelope)) {
    const text = envelope.replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1>/gi, '').replace(/<[^>]+>/g, ' ');
    if (/verify (?:that )?you are human|human verification|captcha|press (?:and|&) hold|access denied|unusual (?:traffic|activity)|checking your browser|just a moment/i.test(text)
      || /<(?:iframe|form)\b[^>]*(?:captcha-delivery\.com|challenges\.cloudflare\.com|challenge-form)/i.test(envelope)) {
      throw rssError('AUTH_REQUIRED', `${source} RSS returned a verification/access page`);
    }
    throw rssError('INVALID_FEED', `${source} returned HTML or an invalid RSS document`);
  }
  // Ignore markup-like strings inside CDATA when checking truncated items.
  const structural = envelope.replace(/<!\[CDATA\[[\s\S]*?\]\]>/g, '');
  const opened = (structural.match(/<item\b[^>]*>/gi) || []).length;
  const closed = (structural.match(/<\/item>/gi) || []).length;
  if (opened !== closed) throw rssError('INVALID_FEED', `${source} RSS contains a truncated item`);
  const count = Math.max(1, Math.min(Number(limit) || 10, 30));
  const items = [];
  for (const match of envelope.matchAll(/<item\b[^>]*>([\s\S]*?)<\/item>/gi)) {
    const block = match[1];
    const title = tag(block, 'title');
    const url = articleUrl(tag(block, 'link')) || articleUrl(tag(block, 'guid'));
    if (!title || !url) continue;
    const row = { rank: items.length + 1, title, time: shanghaiTime(tag(block, 'pubDate')), url };
    if (summary) row.summary = tag(block, 'description');
    items.push(row);
    if (items.length >= count) break;
  }
  if (!items.length) throw rssError(opened ? 'INVALID_FEED' : 'EMPTY_RESULT', `${source} RSS ${opened ? 'contains no valid news items' : 'contains no items'}`);
  return items;
}

export async function fetchRssFeed({ url, source, limit, summary = false }) {
  const controller = new AbortController();
  let timer;
  let xml;
  try {
    xml = await Promise.race([
      (async () => {
        const response = await fetch(url, { headers: { 'User-Agent': USER_AGENT }, signal: controller.signal });
        if (!response.ok) {
          response.body?.cancel().catch(() => {});
          const status = response.status;
          const code = [401, 403].includes(status) ? 'AUTH_REQUIRED' : status === 404 ? 'NOT_FOUND' : 'HTTP_ERROR';
          throw rssError(code, `${source} RSS HTTP ${status}`, {
            status, retryable: TRANSIENT_STATUSES.has(status), retryAt: parseRetryAfter(response.headers.get('retry-after')),
          });
        }
        return response.text();
      })(),
      new Promise((_, reject) => {
        timer = setTimeout(() => {
          controller.abort();
          reject(rssError('TIMEOUT', `${source} RSS request/body exceeded ${RSS_TIMEOUT_MS / 1000}s`, { retryable: true }));
        }, RSS_TIMEOUT_MS);
      }),
    ]);
  } catch (error) {
    if (controller.signal.aborted) throw rssError('TIMEOUT', `${source} RSS request/body exceeded ${RSS_TIMEOUT_MS / 1000}s`, { retryable: true });
    if (error instanceof CliError) throw error;
    throw rssError('NETWORK_ERROR', `${source} RSS request/body failed: ${error?.message || error}`, { retryable: true });
  } finally { clearTimeout(timer); }
  return parseRss(xml, { source, limit, summary });
}
