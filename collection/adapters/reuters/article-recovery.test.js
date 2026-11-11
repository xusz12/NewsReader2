import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { runInNewContext } from 'node:vm';
import { AuthRequiredError, CommandExecutionError, TimeoutError, toEnvelope } from '@jackwener/opencli/errors';
import { ARTICLE_BUDGET_MS, readArticleDetail } from './article-recovery.js';
import { buildArticleDetailScript } from './utils.js';

const URL = 'https://www.reuters.com/world/americas/example-2026-10-17/';
const BODY = 'A reported paragraph containing enough actual article text to count as ready. '.repeat(4).trim();
const payload = (kind, bodyText = null) => ({
    ok: true, state: { kind }, body: { article: { title: 'Headline' }, bodyText },
});
const ready = () => payload('ready', BODY);
const makePage = () => ({
    goto: vi.fn().mockResolvedValue(undefined),
    evaluate: vi.fn().mockResolvedValue(ready()),
});

describe('Reuters bounded article recovery', () => {
    beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(0); });
    afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); });

    it('opens only the requested article and returns immediately when ready', async () => {
        const page = makePage();
        const detail = await readArticleDetail(page, URL);
        expect(detail.body).toBe(BODY);
        expect(page.goto.mock.calls).toEqual([[URL]]);
        expect(page.evaluate).toHaveBeenCalledTimes(1);
        expect(vi.getTimerCount()).toBe(0);
    });

    it('waits through device check and loading without reloading or fetching an API', async () => {
        const page = makePage();
        page.evaluate.mockResolvedValueOnce(payload('automatic_verification'))
            .mockResolvedValueOnce(payload('loading'));
        const result = readArticleDetail(page, URL);
        await vi.advanceTimersByTimeAsync(2000);
        expect((await result).body).toBe(BODY);
        expect(page.goto).toHaveBeenCalledTimes(1);
        expect(page.evaluate).toHaveBeenCalledTimes(3);
        expect(page.evaluate.mock.calls[0][0]).not.toContain('fetch(');
    });

    it('retries one rejected navigation after a delay', async () => {
        const page = makePage();
        page.goto.mockRejectedValueOnce(new Error('Navigation rejected'));
        const result = readArticleDetail(page, URL);
        await vi.advanceTimersByTimeAsync(999);
        expect(page.goto).toHaveBeenCalledTimes(1);
        await vi.advanceTimersByTimeAsync(1);
        expect((await result).body).toBe(BODY);
        expect(page.goto.mock.calls).toEqual([[URL], [URL]]);
    });

    it('stops after two rejected navigation attempts', async () => {
        const page = makePage();
        page.goto.mockRejectedValue(new Error('Navigation rejected'));
        const rejected = expect(readArticleDetail(page, URL)).rejects.toThrow('Navigation rejected');
        await vi.runAllTimersAsync();
        await rejected;
        expect(page.goto).toHaveBeenCalledTimes(2);
        expect(page.evaluate).not.toHaveBeenCalled();
    });

    it('does not retry a deterministic navigation failure', async () => {
        const page = makePage();
        page.goto.mockRejectedValue(new CommandExecutionError('Unsupported browser operation'));
        await expect(readArticleDetail(page, URL)).rejects.toThrow('Unsupported browser operation');
        expect(page.goto).toHaveBeenCalledTimes(1);
    });

    it('re-evaluates after a redirect destroys the execution context', async () => {
        const page = makePage();
        page.evaluate.mockRejectedValueOnce(new Error('Execution context was destroyed'))
            .mockResolvedValueOnce(payload('automatic_verification'))
            .mockRejectedValueOnce(Object.assign(new Error('redirect'), { code: 'target_navigated' }));
        const result = readArticleDetail(page, URL);
        await vi.advanceTimersByTimeAsync(3000);
        expect((await result).body).toBe(BODY);
        expect(page.evaluate).toHaveBeenCalledTimes(4);
        expect(page.goto).toHaveBeenCalledTimes(1);
    });

    it('caps transient evaluation failures at three without reloading', async () => {
        const page = makePage();
        page.evaluate.mockRejectedValue(new Error('Browser connection dropped'));
        const rejected = expect(readArticleDetail(page, URL)).rejects.toThrow('Browser connection dropped');
        await vi.runAllTimersAsync();
        await rejected;
        expect(page.evaluate).toHaveBeenCalledTimes(3);
        expect(page.goto).toHaveBeenCalledTimes(1);
    });

    it.each(['automatic_verification', 'loading'])('bounds an indefinitely %s page to 30 seconds', async kind => {
        const page = makePage();
        page.evaluate.mockResolvedValue(payload(kind));
        const result = readArticleDetail(page, URL).catch(error => error);
        await vi.advanceTimersByTimeAsync(ARTICLE_BUDGET_MS);
        const error = await result;
        expect(error).toBeInstanceOf(TimeoutError);
        expect(error.message).toContain(`state=${kind}`);
        expect(toEnvelope(error).error).toMatchObject({ code: 'TIMEOUT', exitCode: 75 });
        expect(page.goto).toHaveBeenCalledTimes(1);
        expect(page.evaluate).toHaveBeenCalledTimes(30);
        expect(vi.getTimerCount()).toBe(0);
    });

    it.each(['goto', 'evaluate'])('bounds a stalled %s bridge call without launching overlapping retries', async method => {
        const page = makePage();
        page[method].mockImplementation(() => new Promise(() => {}));
        const rejected = expect(readArticleDetail(page, URL)).rejects.toBeInstanceOf(TimeoutError);
        await vi.advanceTimersByTimeAsync(ARTICLE_BUDGET_MS);
        await rejected;
        expect(page[method]).toHaveBeenCalledTimes(1);
        await vi.advanceTimersByTimeAsync(ARTICLE_BUDGET_MS);
        expect(page[method]).toHaveBeenCalledTimes(1);
        expect(vi.getTimerCount()).toBe(0);
    });

    it('shares the deadline between a slow navigation and readiness polling', async () => {
        const page = makePage();
        page.goto.mockImplementation(() => new Promise(resolve => setTimeout(resolve, 20_000)));
        page.evaluate.mockResolvedValue(payload('loading'));
        const rejected = expect(readArticleDetail(page, URL)).rejects.toBeInstanceOf(TimeoutError);
        await vi.advanceTimersByTimeAsync(ARTICLE_BUDGET_MS);
        await rejected;
        expect(page.evaluate).toHaveBeenCalledTimes(10);
        expect(Date.now()).toBe(ARTICLE_BUDGET_MS);
    });

    it.each(['human_verification', 'access_required', 'blocked'])('stops immediately for %s even when a long teaser is present', async kind => {
        const page = makePage();
        page.evaluate.mockResolvedValue(payload(kind, BODY));
        await expect(readArticleDetail(page, URL)).rejects.toBeInstanceOf(AuthRequiredError);
        expect(page.goto).toHaveBeenCalledTimes(1);
        expect(page.evaluate).toHaveBeenCalledTimes(1);
        expect(vi.getTimerCount()).toBe(0);
    });

    it('does not accept a title-only result marked ready', async () => {
        const page = makePage();
        page.evaluate.mockResolvedValue(payload('ready', ''));
        await expect(readArticleDetail(page, URL)).rejects.toThrow('without a usable article body');
    });

    it.each([null, { ok: false, error: 'syntax failure' }, { ok: true, state: { kind: 'unknown' } }])('does not retry invalid extraction results: %j', async result => {
        const page = makePage();
        page.evaluate.mockResolvedValue(result);
        await expect(readArticleDetail(page, URL)).rejects.toBeInstanceOf(CommandExecutionError);
        expect(page.evaluate).toHaveBeenCalledTimes(1);
    });
});

// Exercise the generated browser script, including DOM extraction and classification.
function inspect({ href = URL, title = 'Headline | Reuters', text = '', paragraphs = [], frames = [], paywalls = [], articleText = '' } = {}) {
    const element = (options = {}) => ({
        textContent: options.text || '', innerText: options.text || '', hidden: Boolean(options.hidden),
        getAttribute: name => options[name] || null,
        getClientRects: () => options.visible === false ? [] : [{}],
        querySelector: selector => selector === 'article' && options.containsArticle ? {} : null,
    });
    const document = {
        title, readyState: 'complete', body: { innerText: text },
        getElementById: () => null,
        querySelector: selector => {
            if (selector.includes('h1')) return element({ text: 'Headline' });
            if (selector === 'article') return { innerText: articleText };
            return null;
        },
        querySelectorAll: selector => {
            if (selector === 'iframe') return frames.map(element);
            if (selector.includes('paywall')) return paywalls.map(element);
            if (selector.includes('paragraph-')) return paragraphs.map(text => element({ text }));
            throw new Error(`Unexpected selector: ${selector}`);
        },
    };
    const window = {
        location: { href },
        getComputedStyle: () => ({ display: 'block', visibility: 'visible' }),
    };
    return runInNewContext(buildArticleDetailScript(), { document, window, URL: globalThis.URL });
}

describe('Reuters article browser state detection', () => {
    it('requires paragraphs, not just a heading or article container text', () => {
        const result = inspect({ articleText: BODY });
        expect(result.ok).toBe(true);
        expect(result.state.kind).toBe('loading');
        expect(result.body.bodyText).toBeNull();
    });

    it('extracts real body while retaining the existing paragraph noise filters', () => {
        const result = inspect({ paragraphs: ['Advertisement', BODY, 'Reporting by Alice', 'Our Standards:', 'Footer noise'] });
        expect(result.state.kind).toBe('ready');
        expect(result.body.bodyText).toBe(`${BODY}\n\nReporting by Alice`);
    });

    it('does not mistake article text about verification or subscriptions for an access wall', () => {
        const paragraph = `${BODY} DataDome captcha and human verification; subscription required. ${BODY}`;
        const result = inspect({ paragraphs: [paragraph], text: paragraph });
        expect(result.state.kind).toBe('ready');
    });

    it('detects a visible device-check iframe even when title and article look normal', () => {
        const result = inspect({ paragraphs: [BODY], frames: [{ src: 'https://geo.captcha-delivery.com/interstitial/?x=1' }] });
        expect(result.state.kind).toBe('automatic_verification');
        expect(result.authRequired).toBe(false);
    });

    it('detects a visible interactive captcha iframe with a normal Reuters title', () => {
        const result = inspect({ frames: [{ src: 'https://geo.captcha-delivery.com/captcha/?x=1' }] });
        expect(result.state.kind).toBe('human_verification');
        expect(result.authRequired).toBe(true);
    });

    it('recognizes a device-check iframe by title while its source is still loading', () => {
        expect(inspect({ frames: [{ title: 'DataDome Device Check', src: 'about:blank' }] }).state.kind)
            .toBe('automatic_verification');
    });

    it.each([
        ['https://geo.captcha-delivery.com/interstitial/', 'automatic_verification'],
        ['https://geo.captcha-delivery.com/captcha/', 'human_verification'],
    ])('detects top-level challenge navigation: %s', (href, kind) => {
        expect(inspect({ href }).state.kind).toBe(kind);
    });

    it('ignores hidden or unrelated iframes', () => {
        const result = inspect({ paragraphs: [BODY], frames: [
            { src: 'https://geo.captcha-delivery.com/captcha/', visible: false },
            { src: 'https://geo.captcha-delivery.com/captcha/', hidden: true },
            { src: 'https://example.com/?next=captcha-delivery.com/captcha/' },
        ] });
        expect(result.state.kind).toBe('ready');
    });

    it.each([
        [{ title: 'Device Check' }, 'automatic_verification'],
        [{ text: 'Please verify you are human' }, 'automatic_verification'],
        [{ text: 'Drag the slider to continue' }, 'human_verification'],
        [{ text: 'Subscribe to continue reading' }, 'access_required'],
        [{ text: 'Sign in to read' }, 'access_required'],
        [{ title: 'Access to this page has been denied' }, 'blocked'],
        [{ title: 'Page not found | Reuters' }, 'not_found'],
    ])('classifies access/loading text: %j', (options, kind) => {
        expect(inspect(options).state.kind).toBe(kind);
    });

    it('rejects a visible paywall even when a long teaser exists', () => {
        expect(inspect({ paragraphs: [BODY], paywalls: [{ text: 'Subscribe for unlimited access' }] }).state.kind).toBe('access_required');
    });

    it('ignores a hidden paywall template', () => {
        expect(inspect({ paragraphs: [BODY], paywalls: [{ visible: false, text: 'Subscribe for unlimited access' }] }).state.kind).toBe('ready');
    });

    it('does not treat the live Reuters MainContent paywall-* wrapper as an access prompt', () => {
        expect(inspect({ paragraphs: [BODY], paywalls: [{ containsArticle: true, text: `${BODY} Subscribe` }] }).state.kind).toBe('ready');
    });

    it('ignores an empty paywall placeholder', () => {
        expect(inspect({ paragraphs: [BODY], paywalls: [{}] }).state.kind).toBe('ready');
    });

    it('does not accept article-like content on an unrelated domain', () => {
        expect(inspect({ href: 'https://example.com/', paragraphs: [BODY] }).state.kind).toBe('loading');
    });
});

// Minimal DOM tree for the observed ArticleList > ul/ol > li > div[Body]
// layout. Selection follows document order and includes descendants matching p;
// it does not inject list text into the old paragraph-only selector.
function domElement(tagName, attributes = {}, ...children) {
    const node = {
        nodeType: 1, tagName, attributes, parentElement: null,
        childNodes: children.map(child => typeof child === 'string' ? { nodeType: 3, textContent: child } : child),
        getAttribute(name) { return this.attributes[name] || null; },
        closest(selector) {
            if (selector !== '[data-testid="ArticleList"]') throw new Error(`Unexpected ancestor selector: ${selector}`);
            for (let current = this; current; current = current.parentElement) {
                if (current.getAttribute('data-testid') === 'ArticleList') return current;
            }
            return null;
        },
        get textContent() { return this.childNodes.map(child => child.textContent).join(''); },
    };
    for (const child of node.childNodes) child.parentElement = node;
    return node;
}

function inspectLayout(...blocks) {
    const root = domElement('ARTICLE', {}, ...blocks);
    const all = [];
    const visit = node => {
        if (node.nodeType !== 1) return;
        all.push(node);
        node.childNodes.forEach(visit);
    };
    visit(root);
    const document = {
        title: 'Headline | Reuters', readyState: 'complete', body: { innerText: root.textContent },
        getElementById: () => null,
        querySelector: selector => selector.includes('h1') ? { textContent: 'Headline' } : null,
        querySelectorAll: selector => {
            if (selector === 'iframe' || selector.includes('paywall')) return [];
            if (!selector.includes('paragraph-')) throw new Error(`Unexpected content selector: ${selector}`);
            const includesLists = selector.includes('article [data-testid="ArticleList"]');
            return all.filter(node => node.tagName === 'P'
                || (node.getAttribute('data-testid') || '').startsWith('paragraph-')
                || (includesLists && node.getAttribute('data-testid') === 'ArticleList'));
        },
    };
    const result = runInNewContext(buildArticleDetailScript(), {
        document, window: { location: { href: URL } }, URL: globalThis.URL,
    });
    expect(result.ok).toBe(true);
    return result;
}

const paragraph = text => domElement('DIV', { 'data-testid': 'paragraph-0' }, text);
const articleList = (...items) => domElement('DIV', { 'data-testid': 'ArticleList' }, domElement('UL', {}, ...items));
const listItem = text => domElement('LI', {}, domElement('DIV', { 'data-testid': 'Body' }, text));

describe('Reuters ArticleList body extraction', () => {
    it('includes all seven list items between the introduction and signoff, excluding navigation lists', () => {
        const details = Array.from({ length: 7 }, (_, index) => `Detail ${index + 1}: reported facts in a Reuters body list.`);
        const result = inspectLayout(
            domElement('NAV', {}, domElement('UL', {}, domElement('LI', {}, 'Do not include navigation'))),
            paragraph(BODY), paragraph('Here are some details:'),
            domElement('P', { 'data-testid': 'promo-box' }, 'Sign up here.'),
            articleList(...details.map(listItem)),
            domElement('P', { 'data-testid': 'SignOff' }, 'Reporting by Alice; Editing by Bob'),
            domElement('P', {}, 'Our Standards: The Thomson Reuters Trust Principles.'),
        );
        expect(result.body.bodyText).toBe([BODY, 'Here are some details:', ...details, 'Reporting by Alice; Editing by Bob'].join('\n\n'));
        expect(result.state.kind).toBe('ready');
    });

    it('extracts bare ordered/unordered list items as well as Body wrappers', () => {
        const result = inspectLayout(paragraph(BODY), articleList(
            domElement('LI', {}, 'First unordered detail.'),
            listItem('Body-wrapped detail.'),
        ), domElement('DIV', { 'data-testid': 'ArticleList' },
            domElement('OL', {}, domElement('LI', {}, 'First ordered detail.'))));
        expect(result.body.bodyText).toBe([BODY, 'First unordered detail.', 'Body-wrapped detail.', 'First ordered detail.'].join('\n\n'));
    });

    it('keeps nested list text in order without repeating descendants', () => {
        const result = inspectLayout(paragraph(BODY), articleList(domElement('LI', {},
            'Parent before the child list.',
            domElement('UL', {}, listItem('Nested child detail.')),
            'Parent after the child list.',
        )));
        expect(result.body.bodyText).toBe([BODY, 'Parent before the child list.', 'Nested child detail.', 'Parent after the child list.'].join('\n\n'));
    });

    it('does not duplicate paragraphs selected both directly and through ArticleList', () => {
        const result = inspectLayout(paragraph(BODY), articleList(domElement('LI', {},
            domElement('P', {}, 'First paragraph in the item.'),
            domElement('P', {}, 'Second paragraph in the item.'),
        )));
        expect(result.body.bodyText).toBe([BODY, 'First paragraph in the item.', 'Second paragraph in the item.'].join('\n\n'));
    });

    it('does not repeat nested ArticleList containers or discard genuinely repeated items', () => {
        const result = inspectLayout(paragraph(BODY), articleList(
            listItem('Repeated detail.'),
            domElement('LI', {}, articleList(listItem('Nested detail.'))),
            listItem('Repeated detail.'),
        ));
        expect(result.body.bodyText).toBe([BODY, 'Repeated detail.', 'Nested detail.', 'Repeated detail.'].join('\n\n'));
    });

    it('preserves inline links and line breaks without collecting scripts or embedded controls', () => {
        const result = inspectLayout(paragraph(BODY), articleList(domElement('LI', {},
            'The company ', domElement('A', {}, 'announced'), ' its findings.', domElement('BR'), 'More detail follows.',
            domElement('SCRIPT', {}, 'not article text'), domElement('BUTTON', {}, 'Share'),
        )));
        expect(result.body.bodyText).toBe(`${BODY}\n\nThe company announced its findings. More detail follows.`);
    });

    it('leaves paragraph-only output unchanged and ignores an empty body list', () => {
        const result = inspectLayout(paragraph(BODY), articleList(), paragraph('The final paragraph.'));
        expect(result.body.bodyText).toBe(`${BODY}\n\nThe final paragraph.`);
    });

    it('accepts usable list-only articles, not just introductions', () => {
        const result = inspectLayout(articleList(listItem(BODY)));
        expect(result.body.bodyText).toBe(BODY);
        expect(result.state.kind).toBe('ready');
    });

    it('applies the existing promotion filters and footer boundary to body lists', () => {
        const result = inspectLayout(paragraph(BODY), articleList(
            listItem('Sign up here.'), listItem('A detail to retain.'),
            listItem('Our Standards: Footer begins here.'), listItem('Do not include footer material.'),
        ));
        expect(result.body.bodyText).toBe(`${BODY}\n\nA detail to retain.`);
    });
});
