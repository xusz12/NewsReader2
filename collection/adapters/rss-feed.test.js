import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { toEnvelope } from '@jackwener/opencli/errors';
import { getRegistry } from '@jackwener/opencli/registry';
import { fetchRssFeed, parseRss, parseRetryAfter, decodeEntities } from './rss-feed.js';
import './bloomberg-user/tech.js';
import './techcrunch/latest.js';
import './ars-home/top10.js';

const item = (title = 'News', url = 'https://example.com/story') => `<item><title><![CDATA[${title}]]></title><link>${url}</link><pubDate>Sun, 18 Oct 2026 01:02:03 GMT</pubDate><description><![CDATA[<p>A summary &amp; details</p>]]></description></item>`;
const rss = content => `<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>${content}</channel></rss>`;
const xml = rss(item());
const options = { url: 'https://example.com/feed', source: 'Test', limit: 10 };
const response = (status = 200, body = xml, retryAfter = null) => ({
  ok: status >= 200 && status < 300, status, headers: new Headers(retryAfter === null ? {} : { 'Retry-After': retryAfter }),
  text: vi.fn().mockResolvedValue(body), body: { cancel: vi.fn().mockResolvedValue() },
});
let mockFetch;

describe('single-attempt RSS HTTP contract', () => {
  beforeEach(() => {
    vi.useFakeTimers(); vi.setSystemTime(new Date('2026-10-18T00:00:00Z'));
    mockFetch = vi.fn().mockImplementation(async () => response()); vi.stubGlobal('fetch', mockFetch);
  });
  afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals(); });

  it.each([
    ['BloombergUser/tech', 'https://feeds.bloomberg.com/technology/news.rss', true],
    ['TechcrunchPublic/news', 'https://techcrunch.com/feed/', false],
    ['ArsPublic/news', 'https://feeds.arstechnica.com/arstechnica/index', false],
  ])('preserves the %s command shape and performs exactly one fetch', async (name, url, summary) => {
    const command = getRegistry().get(name);
    expect(command).toMatchObject({ browser: false, strategy: 'public', access: 'read' });
    const rows = await command.func({ limit: 10 });
    expect(rows).toEqual([{ rank: 1, title: 'News', url: 'https://example.com/story', time: '2026-10-18 09:02:03', ...(summary ? { summary: 'A summary & details' } : {}) }]);
    expect(mockFetch).toHaveBeenCalledTimes(1); expect(mockFetch.mock.calls[0][0]).toBe(url);
    expect(vi.getTimerCount()).toBe(0);
  });
  it.each([408, 429, 500, 502, 503, 504])('reports HTTP %s as retryable without retrying itself', async status => {
    const r = response(status, 'busy', '60'); mockFetch.mockResolvedValue(r);
    const error = await fetchRssFeed(options).catch(e => e);
    const envelope = toEnvelope(error);
    expect(envelope.ok).toBe(false); expect(envelope.error.code).toBe('HTTP_ERROR');
    expect(JSON.parse(envelope.error.cause)).toEqual({ schema: 'newsflow.rss-error/v1', http_status: status, retryable: true, retry_at: '2026-10-18T00:01:00.000Z' });
    expect(mockFetch).toHaveBeenCalledTimes(1); expect(r.text).not.toHaveBeenCalled(); expect(r.body.cancel).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(60000); expect(mockFetch).toHaveBeenCalledTimes(1);
  });
  it.each([[401,'AUTH_REQUIRED'],[403,'AUTH_REQUIRED'],[404,'NOT_FOUND'],[400,'HTTP_ERROR'],[501,'HTTP_ERROR']])('reports HTTP %s as terminal', async (status, code) => {
    mockFetch.mockResolvedValue(response(status));
    const envelope = toEnvelope(await fetchRssFeed(options).catch(e => e));
    expect(envelope.error.code).toBe(code); expect(JSON.parse(envelope.error.cause).retryable).toBe(false);
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });
  it.each(['headers', 'body'])('bounds and cancels a stalled %s operation in 15 seconds', async stage => {
    if (stage === 'headers') mockFetch.mockImplementation(() => new Promise(() => {}));
    else { const r = response(); r.text.mockImplementation(() => new Promise(() => {})); mockFetch.mockResolvedValue(r); }
    const result = fetchRssFeed(options).catch(e => e);
    await vi.advanceTimersByTimeAsync(15000);
    const error = await result;
    expect(error.code).toBe('TIMEOUT'); expect(mockFetch.mock.calls[0][1].signal.aborted).toBe(true);
    await vi.advanceTimersByTimeAsync(60000); expect(mockFetch).toHaveBeenCalledTimes(1); expect(vi.getTimerCount()).toBe(0);
  });
  it.each(['headers', 'body'])('classifies a %s network error without an extra request', async stage => {
    if (stage === 'headers') mockFetch.mockRejectedValue(new TypeError('fetch failed'));
    else { const r = response(); r.text.mockRejectedValue(new TypeError('terminated')); mockFetch.mockResolvedValue(r); }
    const error = await fetchRssFeed(options).catch(e => e);
    expect(error.code).toBe('NETWORK_ERROR'); expect(JSON.parse(error.cause.message).retryable).toBe(true);
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });
  it.each([
    ['<html><title>Verify you are human</title></html>', 'AUTH_REQUIRED'],
    ['<html><iframe src="https://geo.captcha-delivery.com/captcha/"></iframe></html>', 'AUTH_REQUIRED'],
    ['<html><title>Server error</title></html>', 'INVALID_FEED'],
    ['not XML', 'INVALID_FEED'],
    [rss(''), 'EMPTY_RESULT'],
    [rss('<item><title>Incomplete</title>'), 'INVALID_FEED'],
    [rss('<item><title>Title only</title></item>'), 'INVALID_FEED'],
  ])('never returns successful empty data for a bad feed', async (body, code) => {
    mockFetch.mockResolvedValue(response(200, body));
    const error = await fetchRssFeed(options).catch(e => e);
    expect(error.code).toBe(code); expect(JSON.parse(error.cause.message).retryable).toBe(false);
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });
});

describe('RSS data integrity', () => {
  it('keeps numeric Unicode entities and escaped angle brackets in titles', () => {
    const rows = parseRss(rss(item('AI&#8217;s &#x1F600; &lt;model&gt; &amp; users')), options);
    expect(rows[0].title).toBe('AI’s 😀 <model> & users');
    expect(decodeEntities('&#99999999; &#xD800;')).toBe('&#99999999; &#xD800;');
  });
  it('does not classify news reporting about CAPTCHA as a verification page', () => {
    expect(parseRss(rss(item('CAPTCHA: verify you are human')), options)[0].title).toContain('CAPTCHA');
  });
  it('validates link and GUID fallback as absolute HTTP URLs', () => {
    const body = '<item><title>A</title><link>javascript:bad()</link><guid>https://example.com/a</guid></item>';
    expect(parseRss(rss(body), options)[0].url).toBe('https://example.com/a');
    expect(() => parseRss(rss(item('A','not-a-url')), options)).toThrow('no valid news items');
  });
  it('preserves query strings and limit/rank behavior', () => {
    const body = rss(item('A', 'https://example.com/?a=1&amp;b=2') + item('B', 'https://example.com/b'));
    expect(parseRss(body, { ...options, limit: 1 })).toMatchObject([{ rank: 1, url: 'https://example.com/?a=1&b=2' }]);
  });
  it('handles BOM, whitespace and XML comments', () => {
    expect(parseRss('\uFEFF<!-- RSS -->\n'+xml, options)).toHaveLength(1);
  });
  it('parses both legal Retry-After formats without accepting arbitrary numbers as dates', () => {
    const now = Date.parse('2026-10-18T00:00:00Z');
    expect(parseRetryAfter('60', now)).toBe('2026-10-18T00:01:00.000Z');
    expect(parseRetryAfter('Sun, 18 Oct 2026 00:00:20 GMT', now)).toBe('2026-10-18T00:00:20.000Z');
    for (const value of [null,'','bad','-5','0.5']) expect(parseRetryAfter(value, now)).toBe(null);
  });
});
