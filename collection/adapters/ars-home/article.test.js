import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { runInNewContext } from 'node:vm';
import { getRegistry } from '@jackwener/opencli/registry';
import { buildArticleDetailScript, mapDetail, readArticle } from './article.js';

const URL = 'https://arstechnica.com/science/example/';
const BODY = 'A real article paragraph with sufficient detail to satisfy the minimum article length. '.repeat(4).trim();
const payload = (bodyText = BODY, extra = {}) => ({ ok: true, authRequired: false, body: {
  article: { title: 'Headline', author: 'Jane Doe', published_at: '2026-10-17', canonical_url: URL }, bodyText,
}, ...extra });
const page = () => ({ goto: vi.fn().mockResolvedValue(), evaluateOnce: vi.fn().mockResolvedValue(payload()) });
const cmd = getRegistry().get('ArsPublic/article');

describe('Ars article command and bounded recovery', () => {
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(0); });
  afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); });

  it('retains metadata and normalized paragraph breaks', () => {
    const detail = mapDetail({ title: ' Headline ', author: 'Jane Doe' }, 'First\n\nFirst\n\nSecond', URL);
    expect(detail).toMatchObject({ title: 'Headline', authors: 'Jane Doe', url: URL, content: 'First\n\nSecond', content_length: 13 });
  });
  it('owns navigation and opens only the requested article once on success', async () => {
    expect(cmd).toMatchObject({ browser: true, navigateBefore: false, access: 'read', strategy: 'ui' });
    const p = page();
    expect(await cmd.func(p, { url: URL })).toEqual([{
      source: 'Ars Technica', title: 'Headline', url: URL, published_at: '2026-10-17', author: 'Jane Doe',
      content: BODY, content_length: BODY.length, status: 'success',
    }]);
    expect(p.goto.mock.calls).toEqual([[URL]]);
    expect(p.evaluateOnce).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });
  it.each(['', 'https://example.com/article', 'https://arstechnica.com.evil.test/article'])('rejects invalid URL %s before navigation', async url => {
    const p = page();
    await expect(cmd.func(p, { url })).rejects.toMatchObject({ code: 'ARGUMENT' });
    expect(p.goto).not.toHaveBeenCalled();
  });
  it('polls normal loading once a second without more navigation', async () => {
    const p = page(); p.evaluateOnce.mockResolvedValueOnce(payload('')).mockResolvedValueOnce(payload('short'));
    const result = readArticle(p, URL);
    await vi.advanceTimersByTimeAsync(999); expect(p.evaluateOnce).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1001);
    expect((await result).detail.content).toBe(BODY);
    expect(p.goto).toHaveBeenCalledTimes(1); expect(p.evaluateOnce).toHaveBeenCalledTimes(3);
  });
  it('waits five seconds before its only navigation retry', async () => {
    const p = page(); p.goto.mockRejectedValueOnce(new Error('Navigation rejected'));
    const result = readArticle(p, URL);
    await vi.advanceTimersByTimeAsync(4999); expect(p.goto).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1); await result;
    expect(p.goto.mock.calls).toEqual([[URL], [URL]]);
  });
  it('exhausts navigation after two calls and returns a NewsReader-retryable error', async () => {
    const p = page(); p.goto.mockRejectedValue(new Error('Navigation rejected'));
    const result = readArticle(p, URL).catch(e => e);
    await vi.runAllTimersAsync();
    expect(await result).toMatchObject({ code: 'BROWSER_CONNECT' });
    expect(p.goto).toHaveBeenCalledTimes(2); expect(p.evaluateOnce).not.toHaveBeenCalled();
  });
  it('does not retry permanent navigation errors', async () => {
    const p = page(); p.goto.mockRejectedValue(Object.assign(new Error('denied'), { code: 'AUTH_REQUIRED', exitCode: 77 }));
    await expect(readArticle(p, URL)).rejects.toMatchObject({ code: 'AUTH_REQUIRED' });
    expect(p.goto).toHaveBeenCalledTimes(1);
  });
  it('recovers a destroyed DOM context with a read-only evaluation, never a reload', async () => {
    const p = page(); p.evaluateOnce.mockRejectedValueOnce(new Error('Execution context was destroyed'));
    const result = readArticle(p, URL);
    await vi.advanceTimersByTimeAsync(1000); await result;
    expect(p.goto).toHaveBeenCalledTimes(1); expect(p.evaluateOnce).toHaveBeenCalledTimes(2);
  });
  it('caps transient DOM failures across intervening loading results', async () => {
    const p = page();
    p.evaluateOnce.mockRejectedValueOnce(new Error('Execution context was destroyed'))
      .mockResolvedValueOnce(payload('')).mockRejectedValueOnce(new Error('Execution context was destroyed'))
      .mockRejectedValueOnce(new Error('Execution context was destroyed'));
    const result = readArticle(p, URL).catch(e => e);
    await vi.runAllTimersAsync();
    expect(await result).toMatchObject({ code: 'BROWSER_CONNECT' });
    expect(p.evaluateOnce).toHaveBeenCalledTimes(4); expect(p.goto).toHaveBeenCalledTimes(1);
  });
  it.each([null, { error: 'broken extraction' }, { ok: false }])('stops invalid extraction without retry', async value => {
    const p = page(); p.evaluateOnce.mockResolvedValue(value);
    await expect(readArticle(p, URL)).rejects.toMatchObject({ code: 'COMMAND_EXEC' });
    expect(p.evaluateOnce).toHaveBeenCalledTimes(1);
  });
  it('stops human verification immediately even with a long teaser', async () => {
    const p = page(); p.evaluateOnce.mockResolvedValue(payload(BODY, { authRequired: true }));
    await expect(readArticle(p, URL)).rejects.toMatchObject({ code: 'AUTH_REQUIRED' });
    expect(Date.now()).toBe(0); expect(p.evaluateOnce).toHaveBeenCalledTimes(1); expect(vi.getTimerCount()).toBe(0);
  });
  it('stops missing pages without reloading', async () => {
    const p = page(); p.evaluateOnce.mockResolvedValue(payload('', { notFound: true }));
    await expect(readArticle(p, URL)).rejects.toMatchObject({ code: 'EMPTY_RESULT' });
    expect(p.goto).toHaveBeenCalledTimes(1);
  });
  it.each(['navigation', 'evaluation', 'loading'])('bounds stalled %s to 30s including bridge calls', async kind => {
    const p = page();
    if (kind === 'navigation') p.goto.mockImplementation(() => new Promise(() => {}));
    else if (kind === 'evaluation') p.evaluateOnce.mockImplementation(() => new Promise(() => {}));
    else p.evaluateOnce.mockResolvedValue(payload(''));
    const result = readArticle(p, URL).catch(e => e);
    await vi.advanceTimersByTimeAsync(30000);
    expect(await result).toMatchObject({ code: 'TIMEOUT' });
    const counts = [p.goto.mock.calls.length, p.evaluateOnce.mock.calls.length];
    await vi.advanceTimersByTimeAsync(30000);
    expect([p.goto.mock.calls.length, p.evaluateOnce.mock.calls.length]).toEqual(counts);
    expect(p.goto).toHaveBeenCalledTimes(1); expect(vi.getTimerCount()).toBe(0);
  });
  it('shares one budget between slow navigation and repeated slow DOM reads', async () => {
    const p = page();
    p.goto.mockImplementation(() => new Promise(r => setTimeout(r, 20000)));
    p.evaluateOnce.mockImplementation(() => new Promise(r => setTimeout(() => r(payload('')), 2000)));
    const result = readArticle(p, URL).catch(e => e);
    await vi.advanceTimersByTimeAsync(30000);
    expect(await result).toMatchObject({ code: 'TIMEOUT' });
    expect(p.evaluateOnce).toHaveBeenCalledTimes(4); expect(p.goto).toHaveBeenCalledTimes(1);
  });
});

function domPayload({ title = 'Headline', text = '', paragraphs = [BODY], gates = [] } = {}) {
  const doc = {
    title, body: { innerText: text },
    querySelector: selector => selector.includes('h1') ? { textContent: title } : null,
    querySelectorAll: selector => selector.startsWith('iframe,') ? gates
      : selector.includes('.post-content p') ? paragraphs.map(textContent => ({ textContent, closest: () => null })) : [],
  };
  return runInNewContext(buildArticleDetailScript(), {
    document: doc, location: { href: URL }, getComputedStyle: node => node.style || {},
  });
}
function gate({ tag = 'IFRAME', src = 'https://geo.captcha-delivery.com/captcha/', text = '', visible = true, insideArticle = false, style = {} } = {}) {
  return { tagName: tag, innerText: text, style, getClientRects: () => visible ? [{}] : [],
    getAttribute: name => name === 'src' ? src : '', closest: selector => insideArticle && selector === '.post-content' ? {} : null,
    matches: () => false };
}

describe('Ars DOM verification detection', () => {
  it('extracts real paragraphs and keeps the existing noise selectors', () => {
    expect(domPayload()).toMatchObject({ ok: true, authRequired: false, body: { bodyText: BODY } });
    expect(buildArticleDetailScript()).toContain('.ars-interlude-container');
  });
  it.each(['Verify you are human', 'Access denied', 'Press and hold', 'Subscription required'])('identifies blocked page: %s', title => {
    expect(domPayload({ title, paragraphs: [] })).toMatchObject({ ok: true, authRequired: true });
  });
  it('detects a visible challenge iframe despite article teaser text', () => {
    expect(domPayload({ gates: [gate()] }).authRequired).toBe(true);
  });
  it('detects an access dialog despite article text', () => {
    expect(domPayload({ gates: [gate({ tag: 'DIV', text: 'Subscribe to continue' })] }).authRequired).toBe(true);
  });
  it.each([{ visible: false }, { style: { display: 'none' } }, { style: { visibility: 'hidden' } }, { style: { opacity: '0' } }, { insideArticle: true }, { src: 'https://example.com/video' }])('ignores non-gating iframe %j', options => {
    expect(domPayload({ gates: [gate(options)] }).authRequired).toBe(false);
  });
  it('does not mistake reporting about CAPTCHA for verification', () => {
    expect(domPayload({ title: 'CAPTCHA and DataDome explained', text: 'Verify you are human: an investigation' }).authRequired).toBe(false);
  });
  it('recognizes a 404 page', () => {
    expect(domPayload({ title: '404 Page not found', paragraphs: [] }).notFound).toBe(true);
  });
});
