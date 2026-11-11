import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { getRegistry } from '@jackwener/opencli/registry';
import { fetchArticleHtml, isVerificationHtml, parseRetryAfter, TOTAL_BUDGET_MS } from './article-http.js';
import { ARTICLE_BUDGET_MS } from '../ars-home/article-recovery.js';
import './article.js';

const URL = 'https://techcrunch.com/2026/10/17/example/';
const BODY = 'A real article paragraph with enough reported detail to satisfy the content minimum. '.repeat(4).trim();
const HTML = `<html><title>Headline</title><meta property="og:title" content="Headline"><meta property="article:published_time" content="2026-10-17"><meta name="author" content="Jane Doe"><div class="entry-content"><p>${BODY}</p></div></html>`;
const response = (status = 200, html = HTML, retryAfter = null) => ({
  status, headers: new Headers(retryAfter === null ? {} : { 'retry-after': retryAfter }),
  text: vi.fn().mockResolvedValue(html), body: { cancel: vi.fn().mockResolvedValue() },
});
const cmd = getRegistry().get('TechcrunchPublic/article');
let fetchMock;

// Optional integration check against the actual NewsReader classifier, loaded
// with AST to avoid importing the app or touching its database/settings.
function readerRetries(error) {
  return JSON.parse(execFileSync('python3', ['-c', `
import ast,json,re,sys
source=ast.parse(open(sys.argv[1]).read())
node=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='_detail_error_is_retryable')
ns={'re':re};exec(compile(ast.Module(body=[node],type_ignores=[]),sys.argv[1],'exec'),ns)
print(json.dumps(ns['_detail_error_is_retryable'](sys.argv[2])))
`, process.env.NEWSREADER_SOURCE, `${error.code}: ${error.message}`], { encoding: 'utf8' }));
}

function readerLimits() {
  return JSON.parse(execFileSync('python3', ['-c', `
import ast,json,sys
names={'DETAIL_MAX_ATTEMPTS','DETAIL_RETRY_DELAYS','DETAIL_COMMAND_ROUTES'}
tree=ast.parse(open(sys.argv[1]).read())
print(json.dumps({n.targets[0].id:ast.literal_eval(n.value) for n in tree.body
  if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name) and n.targets[0].id in names}))
`, process.env.NEWSREADER_SOURCE], { encoding: 'utf8' }));
}

describe('TechCrunch HTTP recovery and command contract', () => {
  beforeEach(() => {
    vi.useFakeTimers(); vi.setSystemTime(new Date('2026-10-18T00:00:00Z'));
    fetchMock = vi.fn().mockImplementation(async () => response()); vi.stubGlobal('fetch', fetchMock);
  });
  afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals(); });

  it('returns the existing success record using a single request', async () => {
    expect(cmd).toMatchObject({ browser: false, strategy: 'public' });
    expect(await cmd.func({ url: URL })).toEqual([{
      source: 'TechCrunch', title: 'Headline', url: URL, published_at: '2026-10-17', author: 'Jane Doe',
      content: BODY, content_length: BODY.length, status: 'success',
    }]);
    expect(fetchMock).toHaveBeenCalledTimes(1); expect(vi.getTimerCount()).toBe(0);
  });
  it.each(['', 'https://example.com/a', 'https://techcrunch.com.evil.test/a'])('validates URL before requesting: %s', async url => {
    expect((await cmd.func({ url }))[0].status).toBe('failed'); expect(fetchMock).not.toHaveBeenCalled();
  });
  it.each([408, 429, 500, 502, 503, 504])('retries HTTP %s only once, at least five seconds later', async status => {
    fetchMock.mockResolvedValueOnce(response(status));
    const result = fetchArticleHtml(URL);
    await vi.advanceTimersByTimeAsync(4999); expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1); expect(await result).toBe(HTML);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
  it.each(['headers', 'body'])('recovers a transient %s network failure', async stage => {
    if (stage === 'headers') fetchMock.mockRejectedValueOnce(new TypeError('fetch failed'));
    else { const r = response(); r.text.mockRejectedValue(new TypeError('terminated')); fetchMock.mockResolvedValueOnce(r); }
    const result = fetchArticleHtml(URL);
    await vi.advanceTimersByTimeAsync(5000); expect(await result).toBe(HTML); expect(fetchMock).toHaveBeenCalledTimes(2);
  });
  it.each(['headers', 'body'])('aborts stalled %s reads and caps requests at two within 30s', async stage => {
    fetchMock.mockImplementation(async () => {
      if (stage === 'headers') return new Promise(() => {});
      const r = response(); r.text.mockImplementation(() => new Promise(() => {})); return r;
    });
    const start = Date.now(); const result = fetchArticleHtml(URL).catch(e => e);
    await vi.advanceTimersByTimeAsync(7999); expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1); expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true);
    await vi.advanceTimersByTimeAsync(4999); expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1); expect(fetchMock).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(8000);
    expect(await result).toMatchObject({ code: 'TIMEOUT' });
    expect(Date.now() - start).toBe(21000); expect(fetchMock.mock.calls[1][1].signal.aborted).toBe(true);
    await vi.advanceTimersByTimeAsync(60000); expect(fetchMock).toHaveBeenCalledTimes(2); expect(vi.getTimerCount()).toBe(0);
  });
  it.each([[401, 'AUTH_REQUIRED'], [403, 'AUTH_REQUIRED'], [404, 'NOT_FOUND'], [400, 'HTTP_ERROR']])('never retries HTTP %s', async (status, code) => {
    const r = response(status); fetchMock.mockResolvedValue(r);
    await expect(fetchArticleHtml(URL)).rejects.toMatchObject({ code });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    if (status !== 400) { expect(r.text).not.toHaveBeenCalled(); expect(r.body.cancel).toHaveBeenCalledTimes(1); }
  });
  it.each([200, 503])('stops verification HTML even with HTTP %s', async status => {
    fetchMock.mockResolvedValue(response(status, '<html><title>Verify you are human</title><form id="challenge-form"></form></html>'));
    await expect(fetchArticleHtml(URL)).rejects.toMatchObject({ code: 'AUTH_REQUIRED' });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it.each(['10', 'Sun, 18 Oct 2026 00:00:10 GMT'])('honors Retry-After %s', async value => {
    fetchMock.mockResolvedValueOnce(response(429, '', value));
    const result = fetchArticleHtml(URL);
    await vi.advanceTimersByTimeAsync(9999); expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1); expect(await result).toBe(HTML); expect(fetchMock).toHaveBeenCalledTimes(2);
  });
  it('shares the 30s budget with response-body download time', async () => {
    const r = response(503, '', '28'); r.text.mockImplementation(() => new Promise(resolve => setTimeout(() => resolve('busy'), 7000)));
    fetchMock.mockResolvedValueOnce(r);
    const result = fetchArticleHtml(URL).catch(e => e);
    await vi.advanceTimersByTimeAsync(7000);
    expect(await result).toMatchObject({ code: 'RETRY_AFTER' }); expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it('does not turn long Retry-After into an immediate outer retry', async () => {
    fetchMock.mockResolvedValue(response(503, '', '120'));
    const error = await fetchArticleHtml(URL).catch(e => e);
    expect(error).toMatchObject({ code: 'RETRY_AFTER' });
    expect(error.message).toContain('2026-10-18T00:02:00.000Z'); expect(error.message).not.toMatch(/HTTP\s+(429|503)/);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    if (process.env.NEWSREADER_SOURCE) expect(readerRetries(error)).toBe(false);
  });
  it('preserves Retry-After if response-body reading fails', async () => {
    const r = response(503, '', '120'); r.text.mockRejectedValue(new Error('terminated')); fetchMock.mockResolvedValue(r);
    const error = await fetchArticleHtml(URL).catch(e => e);
    expect(error.code).toBe('RETRY_AFTER'); expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it('preserves Retry-After if response-body reading stalls', async () => {
    const r = response(429, '', '120'); r.text.mockImplementation(() => new Promise(() => {})); fetchMock.mockResolvedValue(r);
    const result = fetchArticleHtml(URL).catch(e => e);
    await vi.advanceTimersByTimeAsync(8000); expect((await result).code).toBe('RETRY_AFTER'); expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it('also honors a new cooldown returned on the last allowed attempt', async () => {
    fetchMock.mockResolvedValueOnce(response(503)).mockResolvedValueOnce(response(429, '', '60'));
    const result = fetchArticleHtml(URL).catch(e => e);
    await vi.advanceTimersByTimeAsync(5000); const error = await result;
    expect(error).toMatchObject({ code: 'RETRY_AFTER' }); expect(fetchMock).toHaveBeenCalledTimes(2);
    if (process.env.NEWSREADER_SOURCE) expect(readerRetries(error)).toBe(false);
  });
  it('keeps ordinary exhausted network errors retryable by NewsReader', async () => {
    fetchMock.mockRejectedValue(new Error('connection lost'));
    const result = fetchArticleHtml(URL).catch(e => e); await vi.advanceTimersByTimeAsync(5000);
    const error = await result; expect(error.code).toBe('NETWORK_ERROR'); expect(fetchMock).toHaveBeenCalledTimes(2);
    if (process.env.NEWSREADER_SOURCE) expect(readerRetries(error)).toBe(true);
  });
  it.each([['<html><title>News</title></html>', 'CONTENT_UNAVAILABLE'], ['<div class="entry-content"><p>short</p></div>', 'CONTENT_TOO_SHORT']])('does not retry a parse/content failure', async (html, code) => {
    fetchMock.mockResolvedValue(response(200, html));
    expect((await cmd.func({ url: URL }))[0]).toMatchObject({ status: 'failed', error_code: code }); expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it('preserves the failed-row contract for exhausted network errors', async () => {
    fetchMock.mockRejectedValue(new Error('connection lost'));
    const result = cmd.func({ url: URL }); await vi.advanceTimersByTimeAsync(5000);
    expect((await result)[0]).toMatchObject({ status: 'failed', error_code: 'NETWORK_ERROR', content: '' });
  });
  it.runIf(Boolean(process.env.NEWSREADER_SOURCE))('combines with actual NewsReader limits without a rapid retry cascade', async () => {
    const limits = readerLimits();
    expect(TOTAL_BUDGET_MS).toBeLessThan(limits.DETAIL_COMMAND_ROUTES['techcrunch.com'].timeout * 1000);
    expect(ARTICLE_BUDGET_MS).toBeLessThan(limits.DETAIL_COMMAND_ROUTES['arstechnica.com'].timeout * 1000);
    const start = Date.now(); const requestTimes = [];
    fetchMock.mockImplementation(async () => { requestTimes.push(Date.now() - start); return response(503, 'Service unavailable'); });
    for (let attempt = 0; attempt < limits.DETAIL_MAX_ATTEMPTS; attempt++) {
      const pending = cmd.func({ url: URL });
      await vi.advanceTimersByTimeAsync(5000);
      const row = (await pending)[0];
      expect(readerRetries({ code: row.error_code, message: row.reason })).toBe(true);
      if (attempt + 1 < limits.DETAIL_MAX_ATTEMPTS) {
        await vi.advanceTimersByTimeAsync(limits.DETAIL_RETRY_DELAYS[attempt] * 1000);
      }
    }
    expect(requestTimes).toEqual([0, 5000, 10000, 15000, 30000, 35000]);
    expect(fetchMock).toHaveBeenCalledTimes(6);
  });
  it.runIf(Boolean(process.env.NEWSREADER_SOURCE))('keeps terminal access/parse errors out of the actual task retry queue', async () => {
    for (const error of [
      { code: 'AUTH_REQUIRED', message: 'Resolve human verification' },
      { code: 'NOT_FOUND', message: 'Article page returned 404' },
      { code: 'CONTENT_UNAVAILABLE', message: 'Article content container not found' },
      { code: 'CONTENT_TOO_SHORT', message: 'Extracted content length 10 < 200' },
    ]) expect(readerRetries(error)).toBe(false);
    expect(readerRetries({ code: 'BROWSER_CONNECT', message: 'Ars article browser temporarily unavailable: Execution context was destroyed' })).toBe(true);
  });
});

describe('TechCrunch challenge detection and cooldown parsing', () => {
  it.each([null, '', 'garbage'])('ignores invalid Retry-After %s', value => expect(parseRetryAfter(value, 0)).toBe(null));
  it('supports seconds, fractional seconds, and past dates', () => {
    expect(parseRetryAfter('2', 0)).toBe(2000); expect(parseRetryAfter('0.5', 0)).toBe(500);
    expect(parseRetryAfter('Thu, 01 Jan 1970 00:00:01 GMT', 2000)).toBe(0);
  });
  it('does not misclassify article text about CAPTCHA or a challenge library', () => {
    expect(isVerificationHtml(HTML.replace('Headline', 'CAPTCHA explained') + '<script src="recaptcha.js"></script>')).toBe(false);
  });
  it('detects challenge iframe markup without readable challenge text', () => {
    expect(isVerificationHtml('<html><iframe src="https://geo.captcha-delivery.com/captcha/"></iframe></html>')).toBe(true);
  });
});
