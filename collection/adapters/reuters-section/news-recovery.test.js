import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { runInNewContext } from 'node:vm';
import { getRegistry } from '@jackwener/opencli/registry';
import { ArgumentError, AuthRequiredError, CommandExecutionError, EmptyResultError, TimeoutError, toEnvelope } from '@jackwener/opencli/errors';
import './top10.js';
import { buildFetchScript, formatItems, inspectListPage, readSectionNews, retryAfterMs, TOTAL_BUDGET_MS } from './news-recovery.js';

const items = [{ title: 'Fixture headline', canonical_url: '/world/example/', display_date: '2026-10-17T12:30:00Z' }];
const success = () => ({ ok: true, status: 200, data: { result: { articles: items } } });
const failure = (status, extras = {}) => ({ ok: false, status, ...extras });
function makePage() {
  return {
    goto: vi.fn().mockResolvedValue(undefined),
    evaluate: vi.fn().mockResolvedValue({ kind: 'ready' }),
    evaluateOnce: vi.fn().mockResolvedValue(success()),
  };
}

describe('Reuters list registry and formatting', () => {
  const command = getRegistry().get('ReutersBrowser/news');
  it('owns browser navigation and preserves output columns', () => {
    expect(command.navigateBefore).toBe(false);
    expect(command.browser).toBe(true);
    expect(command.columns).toEqual(['rank', 'title', 'time', 'url']);
  });
  it.each(['', 'https://example.com/world/', 'javascript:alert(1)', 'ftp://www.reuters.com/world/', 'https://user:pass@www.reuters.com/world/'])('rejects invalid URL %s before navigation', async url => {
    const page = makePage();
    await expect(command.func(page, { url })).rejects.toBeInstanceOf(ArgumentError);
    expect(page.goto).not.toHaveBeenCalled();
  });
  it.each([[0, 10], [51, 50], [-5, 1], ['invalid', 10]])('preserves existing limit handling for %s', async (limit, count) => {
    const page = makePage();
    await command.func(page, { url: 'https://www.reuters.com/world/', limit });
    expect(page.evaluateOnce.mock.calls[0][0]).toContain(encodeURIComponent(JSON.stringify({ collection_alias: 'world', exclude: '', size: count, website: 'reuters' })));
  });
  it.each([
    ['https://www.reuters.com/', 'world', 10],
    ['https://www.reuters.com/world/americas/?x=1', 'world-americas', 3],
  ])('maps URL %s to a safe API query', async (url, alias, limit) => {
    const page = makePage();
    await command.func(page, { url, limit });
    expect(page.evaluateOnce.mock.calls[0][0]).toContain(encodeURIComponent(JSON.stringify({ collection_alias: alias, exclude: '', size: limit, website: 'reuters' })));
    expect(page.goto.mock.calls).toEqual([['https://www.reuters.com']]);
  });
  it.each([
    { result: { articles: items } }, { data: { data: { content_elements: items } } },
    { data: { list: items } }, { articles: items },
  ])('preserves response variants and Shanghai timestamps', data => {
    expect(formatItems(data, 3)).toEqual([{ rank: 1, title: 'Fixture headline', time: '2026-10-17 20:30:00', url: 'https://www.reuters.com/world/example/' }]);
  });
  it('validates rows and the response structure', () => {
    expect(formatItems({ articles: [null, {}, { title: 'bad', url: 'javascript:alert(1)' }, ...items] }, 50)).toHaveLength(1);
    expect(() => formatItems({ unexpected: [] }, 3)).toThrow(CommandExecutionError);
  });
  it('interprets Retry-After seconds and HTTP dates without shortening the server delay', () => {
    const now = Date.parse('2026-10-18T00:00:00Z');
    expect(retryAfterMs('6', now)).toBe(6000);
    expect(retryAfterMs('Sun, 18 Oct 2026 00:00:09 GMT', now)).toBe(9000);
    expect(retryAfterMs('Sun, 18 Oct 2026 00:00:00 GMT', now)).toBe(0);
    expect(retryAfterMs('invalid', now)).toBe(0);
  });
});

describe('Reuters list bounded recovery', () => {
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(0); });
  afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); });

  it('fetches once on a healthy page and clears timers', async () => {
    const page = makePage();
    expect(await readSectionNews(page, 'world-americas', 3)).toHaveLength(1);
    expect(page.evaluateOnce).toHaveBeenCalledTimes(1);
    expect(page.evaluate).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('recovers when a 403 creates an iframe after the initial page-state check', async () => {
    const page = makePage();
    const times = [];
    page.evaluate.mockImplementation(async () => ({ kind: times.length === 1 && Date.now() < 4000 ? 'automatic_verification' : 'ready' }));
    page.evaluateOnce.mockImplementation(async () => { times.push(Date.now()); return times.length === 1 ? failure(403) : success(); });
    const result = readSectionNews(page, 'world', 3);
    await vi.advanceTimersByTimeAsync(3999);
    expect(times).toEqual([0]);
    await vi.advanceTimersByTimeAsync(1);
    expect(await result).toHaveLength(1);
    expect(times).toEqual([0, 4000]);
    expect(page.goto).toHaveBeenCalledTimes(1);
  });

  it.each([401, 403])('retries a bare HTTP %s without requiring an iframe signal', async status => {
    const page = makePage();
    page.evaluateOnce.mockResolvedValueOnce(failure(status));
    const result = readSectionNews(page, 'world', 3);
    await vi.advanceTimersByTimeAsync(1999);
    expect(page.evaluateOnce).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(await result).toHaveLength(1);
    expect(page.evaluateOnce).toHaveBeenCalledTimes(2);
  });

  it.each([401, 403, 429, 503])('caps persistent HTTP %s at three requests spaced by 2s and 5s', async status => {
    const page = makePage();
    const times = [];
    page.evaluateOnce.mockImplementation(async () => { times.push(Date.now()); return failure(status); });
    const result = readSectionNews(page, 'world', 3).catch(error => error);
    await vi.runAllTimersAsync();
    expect(times).toEqual([0, 2000, 7000]);
    const error = await result;
    expect(error.code).toBe([401, 403].includes(status) ? 'AUTH_REQUIRED' : 'NETWORK_ERROR');
    expect(toEnvelope(error).error.code).not.toBe('UNKNOWN');
    expect(page.goto).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('starts backoff after response completion, not request start', async () => {
    const page = makePage();
    const times = [];
    page.evaluateOnce.mockImplementation(async () => {
      times.push(Date.now());
      if (times.length > 1) return success();
      await new Promise(resolve => setTimeout(resolve, 3000));
      return failure(403);
    });
    const result = readSectionNews(page, 'world', 3);
    await vi.advanceTimersByTimeAsync(5000);
    expect(await result).toHaveLength(1);
    expect(times).toEqual([0, 5000]);
  });

  it.each(['6', 'Thu, 01 Jan 1970 00:00:06 GMT'])('honors Retry-After %s', async retryAfter => {
    const page = makePage();
    page.evaluateOnce.mockResolvedValueOnce(failure(429, { retryAfter }));
    const result = readSectionNews(page, 'world', 3);
    await vi.advanceTimersByTimeAsync(5999);
    expect(page.evaluateOnce).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(await result).toHaveLength(1);
  });

  it('never retries early when Retry-After exceeds the remaining total budget', async () => {
    const page = makePage();
    page.evaluateOnce.mockResolvedValue(failure(429, { retryAfter: '120' }));
    const rejected = expect(readSectionNews(page, 'world', 3)).rejects.toBeInstanceOf(TimeoutError);
    await vi.advanceTimersByTimeAsync(TOTAL_BUDGET_MS);
    await rejected;
    expect(page.evaluateOnce).toHaveBeenCalledTimes(1);
  });

  it('includes navigation time in the same 30-second deadline', async () => {
    const page = makePage();
    page.goto.mockImplementation(() => new Promise(resolve => setTimeout(resolve, 26_000)));
    page.evaluateOnce.mockResolvedValue(failure(503));
    const rejected = expect(readSectionNews(page, 'world', 3)).rejects.toBeInstanceOf(TimeoutError);
    await vi.advanceTimersByTimeAsync(TOTAL_BUDGET_MS); await rejected;
    expect(page.evaluateOnce).toHaveBeenCalledTimes(2);
    expect(Date.now()).toBe(TOTAL_BUDGET_MS);
  });

  it.each(['automatic_verification', 'loading'])('waits for %s without requesting the API', async kind => {
    const page = makePage();
    page.evaluate.mockResolvedValue({ kind });
    const rejected = expect(readSectionNews(page, 'world', 3)).rejects.toBeInstanceOf(TimeoutError);
    await vi.advanceTimersByTimeAsync(TOTAL_BUDGET_MS);
    await rejected;
    expect(page.evaluateOnce).not.toHaveBeenCalled();
    expect(page.goto).toHaveBeenCalledTimes(1);
  });

  it.each(['human_verification', 'access_required', 'blocked'])('stops for %s without requesting the API', async kind => {
    const page = makePage();
    page.evaluate.mockResolvedValue({ kind });
    await expect(readSectionNews(page, 'world', 3)).rejects.toBeInstanceOf(AuthRequiredError);
    expect(page.evaluateOnce).not.toHaveBeenCalled();
  });

  it('detects an interactive captcha that appears after a 403 and stops', async () => {
    const page = makePage();
    page.evaluate.mockResolvedValueOnce({ kind: 'ready' }).mockResolvedValue({ kind: 'human_verification' });
    page.evaluateOnce.mockResolvedValue(failure(403));
    await expect(readSectionNews(page, 'world', 3)).rejects.toBeInstanceOf(AuthRequiredError);
    expect(page.evaluateOnce).toHaveBeenCalledTimes(1);
  });

  it('counts a failed fetch evaluation as an attempt and waits before retrying', async () => {
    const page = makePage();
    page.evaluateOnce.mockRejectedValueOnce(Object.assign(new Error('redirect'), { code: 'target_navigated' }));
    const result = readSectionNews(page, 'world', 3);
    await vi.advanceTimersByTimeAsync(2000);
    expect(await result).toHaveLength(1);
    expect(page.evaluateOnce).toHaveBeenCalledTimes(2);
  });

  it('recovers a destroyed state-check context without replaying a fetch', async () => {
    const page = makePage();
    page.evaluate.mockRejectedValueOnce(new Error('Execution context was destroyed'));
    const result = readSectionNews(page, 'world', 3);
    await vi.advanceTimersByTimeAsync(1000);
    expect(await result).toHaveLength(1);
    expect(page.evaluateOnce).toHaveBeenCalledTimes(1);
  });

  it('limits state-check transport failures to three', async () => {
    const page = makePage();
    page.evaluate.mockRejectedValue(new Error('Browser connection dropped'));
    const rejected = expect(readSectionNews(page, 'world', 3)).rejects.toThrow('Browser connection dropped');
    await vi.runAllTimersAsync();
    await rejected;
    expect(page.evaluate).toHaveBeenCalledTimes(3);
    expect(page.evaluateOnce).not.toHaveBeenCalled();
  });

  it('retries a rejected navigation once, within the same total budget', async () => {
    const page = makePage();
    page.goto.mockRejectedValueOnce(new Error('Navigation rejected'));
    const result = readSectionNews(page, 'world', 3);
    await vi.advanceTimersByTimeAsync(1000);
    expect(await result).toHaveLength(1);
    expect(page.goto).toHaveBeenCalledTimes(2);
  });

  it('stops after two rejected navigations', async () => {
    const page = makePage();
    page.goto.mockRejectedValue(new Error('Navigation rejected'));
    const rejected = expect(readSectionNews(page, 'world', 3)).rejects.toThrow('Navigation rejected');
    await vi.runAllTimersAsync(); await rejected;
    expect(page.goto).toHaveBeenCalledTimes(2);
  });

  it.each(['goto', 'evaluate', 'evaluateOnce'])('bounds a hung %s call without overlapping retries', async method => {
    const page = makePage();
    page[method].mockImplementation(() => new Promise(() => {}));
    const rejected = expect(readSectionNews(page, 'world', 3)).rejects.toBeInstanceOf(TimeoutError);
    await vi.advanceTimersByTimeAsync(TOTAL_BUDGET_MS); await rejected;
    expect(page[method]).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each([
    [failure(404), EmptyResultError],
    [failure(400), CommandExecutionError],
    [failure(200, { parseError: true, textPreview: 'broken JSON' }), CommandExecutionError],
    [{ ok: true, status: 200, data: {} }, CommandExecutionError],
  ])('does not retry deterministic response errors: %j', async (response, type) => {
    const page = makePage(); page.evaluateOnce.mockResolvedValue(response);
    await expect(readSectionNews(page, 'world', 3)).rejects.toBeInstanceOf(type);
    expect(page.evaluateOnce).toHaveBeenCalledTimes(1);
  });

  it.each([
    failure(0, { timeout: true }),
    failure(0, { error: 'Failed to fetch' }),
    failure(200, { parseError: true, textPreview: '<html>DataDome Device Check</html>' }),
    { ok: true, status: 200, data: { articles: [] } },
  ])('recovers transient fetch/response failures: %j', async response => {
    const page = makePage(); page.evaluateOnce.mockResolvedValueOnce(response);
    const result = readSectionNews(page, 'world', 3);
    await vi.advanceTimersByTimeAsync(2000);
    expect(await result).toHaveLength(1);
    expect(page.evaluateOnce).toHaveBeenCalledTimes(2);
  });
});

function stateFixture({ href = 'https://www.reuters.com/', title = 'Reuters | News', text = 'News '.repeat(300), frames = [], main = true } = {}) {
  const document = {
    title, readyState: 'complete', body: { innerText: text },
    querySelector: () => main ? {} : null,
    querySelectorAll: () => frames.map(frame => ({
      hidden: frame.hidden || false,
      getAttribute: key => frame[key] || '',
      getClientRects: () => frame.visible === false ? [] : [{}],
    })),
  };
  return runInNewContext(`(${inspectListPage.toString()})()`, {
    document, URL, window: { location: { href }, getComputedStyle: () => ({ display: 'block', visibility: 'visible' }) },
  });
}

describe('Reuters list page-state detection', () => {
  it.each([
    [{}, 'ready'],
    [{ frames: [{ src: 'https://geo.captcha-delivery.com/interstitial/' }] }, 'automatic_verification'],
    [{ frames: [{ src: 'https://geo.captcha-delivery.com/captcha/' }] }, 'human_verification'],
    [{ href: 'https://geo.captcha-delivery.com/interstitial/' }, 'automatic_verification'],
    [{ href: 'https://geo.captcha-delivery.com/captcha/' }, 'human_verification'],
    [{ frames: [{ title: 'Device Check', src: 'about:blank' }] }, 'automatic_verification'],
    [{ frames: [{ src: 'https://geo.captcha-delivery.com/captcha/', visible: false }] }, 'ready'],
    [{ frames: [{ src: 'https://other.com/?captcha-delivery.com' }] }, 'ready'],
    [{ title: 'Device Check' }, 'automatic_verification'],
    [{ main: false, text: 'Verify you are human' }, 'automatic_verification'],
    [{ main: false, text: 'Drag the slider' }, 'human_verification'],
    [{ main: false, text: 'Subscribe to continue' }, 'access_required'],
    [{ main: false, text: 'Access to this page has been denied' }, 'blocked'],
    [{ href: 'https://example.com/' }, 'loading'],
    [{ text: 'A Reuters headline about DataDome captcha and subscription required. '.repeat(30) }, 'ready'],
  ])('classifies %j as %s', (options, kind) => {
    expect(stateFixture(options).kind).toBe(kind);
  });
});

describe('Reuters list in-page fetch', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); });
  const execute = fetch => runInNewContext(buildFetchScript('world-americas', 3, 8000), { fetch, AbortController, setTimeout, clearTimeout });
  it('uses session cookies and a cancellable signal, preserving response and Retry-After', async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, headers: { get: () => '6' }, text: async () => JSON.stringify({ articles: items }) });
    const result = await execute(fetch);
    expect(result).toMatchObject({ ok: true, status: 200, retryAfter: '6', data: { articles: items } });
    expect(fetch.mock.calls[0][1].credentials).toBe('include');
    expect(fetch.mock.calls[0][1].signal).toBeInstanceOf(AbortSignal);
    expect(vi.getTimerCount()).toBe(0);
  });
  it('preserves HTTP 403 and Retry-After, even when the body is HTML', async () => {
    const cancel = vi.fn().mockResolvedValue(undefined);
    const result = await execute(async () => ({ ok: false, status: 403, headers: { get: () => '5' }, body: { cancel } }));
    expect(result).toMatchObject({ ok: false, status: 403, retryAfter: '5' });
    expect(cancel).toHaveBeenCalledTimes(1);
  });
  it('retains a bounded preview of a 200 challenge page instead of throwing JSON errors', async () => {
    const result = await execute(async () => ({ ok: true, status: 200, headers: { get: () => null }, text: async () => '<html>Device Check</html>'.repeat(100) }));
    expect(result.parseError).toBe(true);
    expect(result.textPreview).toHaveLength(500);
  });
  it('aborts a stalled network request after eight seconds', async () => {
    const fetch = vi.fn((_, { signal }) => new Promise((resolve, reject) => signal.addEventListener('abort', () => reject(new Error('aborted')))));
    const result = execute(fetch);
    await vi.advanceTimersByTimeAsync(8000);
    expect(await result).toMatchObject({ ok: false, status: 0, timeout: true });
    expect(fetch.mock.calls[0][1].signal.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('also bounds reading a stalled response body', async () => {
    const fetch = vi.fn(async (_, { signal }) => ({
      ok: true, status: 200, headers: { get: () => null },
      text: () => new Promise((resolve, reject) => signal.addEventListener('abort', () => reject(new Error('body aborted')))),
    }));
    const result = execute(fetch);
    await vi.advanceTimersByTimeAsync(8000);
    expect(await result).toMatchObject({ ok: false, status: 0, timeout: true });
    expect(vi.getTimerCount()).toBe(0);
  });
});
