import { test, expect } from '@playwright/test';
import { chooseTheme } from './helpers.js';

const activeFilter = page => page.locator('#feedColFeed .filter-chip.active[data-filter]');
test.beforeEach(async ({ request }) => { await request.post('/__test/reset-reading'); });

async function start(page) {
  await page.goto('/');
  await expect(page.locator('#feedCardList .news-card').first()).toBeVisible();
  await expect(page.locator('#unreadBadge')).toHaveText('65');
}

test('unread filter, source and nav counts follow scrolling without reloading rows', async ({ page, request }) => {
  await start(page);
  const first = page.locator('#feedCardList .news-card').first();
  const id = await first.getAttribute('data-id');
  const end = await first.evaluate(node => node.offsetTop + node.offsetHeight);
  await page.locator('#feedCardList').evaluate((node, y) => node.scrollTop = y + 5, end);
  await expect.poll(async () => (await (await request.get(`/api/news/${id}/detail`)).json()).read_at).toBeTruthy();
  const total = (await (await request.get('/api/news?collection=feed&read_filter=unread&count_only=1')).json()).total;
  await expect(activeFilter(page)).toHaveText(`未读 (${total})`);
  await expect(page.locator('#unreadBadge')).toHaveText(String(total));
  await expect(page.locator('[data-source="all"] .nav-badge')).toHaveText(String(total));
  await expect(page.locator(`[data-id="${id}"]`)).toHaveCount(1);
  const scroll = await page.locator('#feedCardList').evaluate(node => node.scrollTop);
  await request.patch('/api/news/ui-000/state', { data: { read: true } });
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(activeFilter(page)).toHaveText(`未读 (${total - 1})`);
  expect(await page.locator('#feedCardList').evaluate(node => node.scrollTop)).toBe(scroll);
});

test('clear favorites icon removes all pages and filtered-out favorites while preserving other flags', async ({ page, request }) => {
  for (let i = 0; i < 65; i++) await request.patch(`/api/news/ui-${String(i).padStart(3, '0')}/state`, { data: { favorite: true } });
  await request.patch('/api/news/ui-064/state', { data: { important: true, read_later: true } });
  const before = await (await request.get('/api/news/ui-064/detail')).json();
  await start(page);
  await expect(page.locator('#articleBodyContent')).not.toBeEmpty();
  await expect(page.locator('#clearFavoritesBtn')).toBeHidden();
  await page.locator('#navFavoritesBtn').click();
  await expect(page.locator('#feedCardList .news-card')).toHaveCount(30);
  await expect(page.locator('#favoriteSelectionBar, [data-select-favorite]')).toHaveCount(0);
  const clear = page.getByRole('button', { name: '全部取消收藏', exact: true });
  await expect(clear).toBeVisible();
  expect(await clear.evaluate(node => node.previousElementSibling.id)).toBe('sortNewsBtn');
  expect(await clear.textContent()).toBe('');
  await expect(clear.locator('svg')).toHaveCount(1);
  await page.screenshot({ path: '.ui-artifacts/v011-favorites.png' });
  await page.locator('#sourceFilters [data-source="reuters"]').click();
  await page.locator('#feedSearchInput').fill('__no_matching_favorite__');
  await expect(activeFilter(page)).toHaveText('全部 (0)');
  await expect(clear).toBeEnabled();
  await clear.click();
  await expect(page.locator('#toast')).toHaveText('已取消 65 篇收藏');
  await expect(clear).toBeDisabled();
  await expect(page.locator('#starredNavBadge')).toBeHidden();
  expect((await (await request.get('/api/news?collection=favorites&count_only=1')).json()).total).toBe(0);
  const after = await (await request.get('/api/news/ui-064/detail')).json();
  expect(after.favorite_at).toBeNull();
  for (const flag of ['important_at', 'read_at', 'read_later_at', 'read_later_done_at']) expect(after[flag]).toBe(before[flag]);
  await expect(page.locator('#detailPanelFeed .detail-toolbar [data-state="favorite"]')).toHaveAttribute('aria-pressed', 'false');
});

test('clear favorites recovers after failure and retains individual favorite controls', async ({ page, request }) => {
  await request.post('/api/news/clear-favorites');
  await request.patch('/api/news/ui-064/state', { data: { favorite: true } });
  await start(page);
  await page.locator('#navFavoritesBtn').click();
  const clear = page.locator('#clearFavoritesBtn');
  await page.route('**/api/news/clear-favorites', route => route.fulfill({ status: 500, json: { error: '保存失败' } }));
  await clear.click();
  await expect(page.locator('#toast')).toContainText('保存失败');
  await expect(clear).toBeEnabled();
  await expect(page.locator('#feedCardList .news-card')).toHaveCount(1);
  await expect(page.locator('[data-id="ui-064"] [data-state="favorite"]')).toHaveAttribute('aria-pressed', 'true');
  await page.unroute('**/api/news/clear-favorites');
  await clear.click();
  await expect(page.locator('#feedCardList .news-card')).toHaveCount(0);
  await expect(clear).toBeDisabled();
  await page.locator('#navFeedBtn').click();
  await page.locator('[data-id="ui-064"] [data-state="favorite"]').click();
  await expect(page.locator('[data-id="ui-064"] [data-state="favorite"]')).toHaveAttribute('aria-busy', 'false');
  await page.locator('#navFavoritesBtn').click();
  await expect(page.locator('#feedCardList .news-card')).toHaveCount(1);
  await page.locator('[data-id="ui-064"] [data-state="favorite"]').click();
  await expect(page.locator('#feedCardList .news-card')).toHaveCount(0);
});

test('clear favorites waits for an earlier favorite save and blocks overlapping star clicks', async ({ page, request }) => {
  await request.post('/api/news/clear-favorites');
  for (const id of ['ui-063', 'ui-064']) await request.patch(`/api/news/${id}/state`, { data: { favorite: true } });
  await start(page);
  await page.locator('#navFavoritesBtn').click();
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const order = [];
  await page.route('**/api/news/ui-064/state', async route => {
    await gate;
    const response = await route.fetch();
    order.push('saved');
    await route.fulfill({ response });
  });
  await page.route('**/api/news/clear-favorites', async route => {
    order.push('clear');
    await route.continue();
  });
  await page.locator('[data-id="ui-064"] [data-state="favorite"]').click();
  await page.locator('#clearFavoritesBtn').click();
  await expect(page.locator('#clearFavoritesBtn')).toHaveAttribute('aria-busy', 'true');
  await expect(page.locator('[data-id="ui-063"] [data-state="favorite"]')).toBeDisabled();
  release();
  await expect(page.locator('#feedCardList .news-card')).toHaveCount(0);
  expect(order).toEqual(['saved', 'clear']);
});

test('mobile favorites uses the same icon next to sorting without a selection bar', async ({ page, request }) => {
  await request.post('/api/news/clear-favorites');
  await request.patch('/api/news/ui-064/state', { data: { favorite: true } });
  await page.setViewportSize({ width: 360, height: 800 });
  await start(page);
  await page.locator('#openNavBtn').click();
  await page.locator('#navFavoritesBtn').click();
  const clear = page.locator('#clearFavoritesBtn');
  await expect(clear).toBeVisible();
  await expect(page.locator('#favoriteSelectionBar, [data-select-favorite]')).toHaveCount(0);
  const sortBounds = await page.locator('#sortNewsBtn').boundingBox(), clearBounds = await clear.boundingBox();
  expect(clearBounds.x).toBeGreaterThan(sortBounds.x + sortBounds.width);
  expect(clearBounds.y).toBe(sortBounds.y);
  expect(clearBounds.x + clearBounds.width).toBeLessThanOrEqual(360);
  await page.screenshot({ path: '.ui-artifacts/v011-favorites-mobile.png' });
  await clear.click();
  await expect(page.locator('#feedCardList .news-card')).toHaveCount(0);
});

test('new keyboard mapping and input guard', async ({ page, request }) => {
  for (const id of ['ui-064', 'ui-063']) await request.patch(`/api/news/${id}/state`, { data: { favorite: false, important: false, read_later: false, read_later_done: true } });
  await start(page);
  await expect(page.locator('[data-id="ui-064"]')).toHaveAttribute('aria-current', 'true');
  await page.keyboard.press('s');
  await expect(page.locator('[data-id="ui-063"]')).toHaveAttribute('aria-current', 'true');
  await page.keyboard.press('w');
  await expect(page.locator('[data-id="ui-064"]')).toHaveAttribute('aria-current', 'true');
  for (const [key, flag] of [['a', 'important'], ['d', 'read_later'], ['f', 'favorite']]) {
    await page.keyboard.press(key);
    await expect(page.locator(`[data-id="ui-064"] [data-state="${flag}"]`)).toHaveAttribute('aria-pressed', 'true');
  }
  await page.keyboard.press('q');
  await expect(page.locator('#agentPromptInput')).toBeVisible();
  await page.locator('#agentPromptInput').fill('wasdfq');
  await expect(page.locator('[data-id="ui-064"] [data-state="important"]')).toHaveAttribute('aria-pressed', 'true');
  await page.keyboard.press('Escape');
  await page.locator('#feedSearchInput').focus();
  await page.keyboard.type('wasdfq');
  await expect(page.locator('#feedSearchInput')).toHaveValue('wasdfq');
  await page.locator('#feedSearchInput').fill('');
  await page.locator('#feedSearchInput').blur();
  await page.keyboard.press('?');
  await expect(page.locator('#shortcutsModal')).toContainText('q');
});

test('upstream errors support dates, escaped details, retry and popover keyboard isolation', async ({ page }) => {
  let fail = false;
  await page.route('**/api/error-stats?*', route => {
    const day = new URL(route.request().url()).searchParams.get('day') || '2026-10-19';
    return route.fulfill(fail ? { status: 500, json: { ok: false, error: 'request_failed' } } : { json: {
      ok: true, day, days: day === '2026-10-19' ? [{ date: day, groups: [{ time: '12:30:00', entries: [{ label: 'Reuters world', error: '<script>alert(1)</script> Failed to fetch' }] }] }] : [],
    } });
  });
  await start(page);
  await page.locator('#upstreamErrorsBtn').click();
  await expect(page.locator('#upstreamErrorsContent')).toContainText('Reuters world');
  await expect(page.locator('#upstreamErrorsContent script')).toHaveCount(0);
  await page.keyboard.press('s');
  await expect(page.locator('[data-id="ui-064"]')).toHaveAttribute('aria-current', 'true');
  await page.locator('#upstreamErrorDate').fill('2026-10-18');
  await page.locator('#upstreamErrorDate').press('Enter');
  await expect(page.locator('#upstreamErrorsContent')).toContainText('当天没有记录');
  fail = true;
  await page.locator('#upstreamTodayBtn').click();
  await expect(page.locator('[data-upstream-retry]')).toBeVisible();
  fail = false;
  await page.locator('#upstreamErrorDate').fill('2026-10-19');
  await page.locator('#upstreamErrorDate').press('Enter');
  await expect(page.locator('#upstreamErrorsContent')).toContainText('Reuters world');
  await page.screenshot({ path: '.ui-artifacts/v011-upstream-errors.png' });
  await page.keyboard.press('Escape');
  await expect(page.locator('#upstreamErrorsPanel')).not.toBeVisible();
  for (const width of [390, 360, 320]) {
    await page.setViewportSize({ width, height: 844 });
    await chooseTheme(page, 'dark');
    const themeBox = await page.locator('#themeSelect').boundingBox();
    expect(themeBox.x + themeBox.width).toBeLessThanOrEqual(width);
    await page.locator('#upstreamErrorsBtn').click();
    const box = await page.locator('#upstreamErrorsPanel').boundingBox();
    expect(box.x).toBeGreaterThanOrEqual(0); expect(box.x + box.width).toBeLessThanOrEqual(width);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    await page.screenshot({ path: `.ui-artifacts/v011-upstream-${width}.png` });
    await page.locator('#closeUpstreamErrorsBtn').click();
  }
});

test('visible polling catches other-device changes and hidden pages pause polling', async ({ page, request }) => {
  await page.clock.install();
  await start(page);
  await request.patch('/api/news/ui-000/state', { data: { read: true } });
  await page.clock.fastForward(31000);
  await page.clock.runFor(200);
  await expect(activeFilter(page)).toHaveText('未读 (64)');
  await expect(page.locator('#pipelineStatus')).toContainText('DailyNews 增量索引已同步');
  let requests = 0;
  page.on('request', req => { if (req.url().includes('/api/nav-summary')) requests++; });
  await page.evaluate(() => Object.defineProperty(document, 'hidden', { configurable: true, value: true }));
  await request.patch('/api/news/ui-001/state', { data: { read: true } });
  await page.clock.fastForward(31000);
  await page.clock.runFor(200);
  expect(requests).toBe(0);
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, value: false });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await page.clock.runFor(200);
  await expect(activeFilter(page)).toHaveText('未读 (63)');
});

test('quoted user text is posted separately and rendered distinctly in every theme', async ({ page }) => {
  const session = { id: 'quote-session', runtime: 'codex', model: 'fixture' };
  await page.route('**/api/news/*/agent/session', route => route.fulfill({ json: { ok: true, session, context_available: true, messages: [], jobs: [] } }));
  let sent;
  await page.route('**/api/news/*/agent/jobs', route => {
    sent = route.request().postDataJSON();
    return route.fulfill({ json: { ok: true, session, context_available: true, messages: [{ role: 'user', content: sent.question, quote_text: sent.quote_text }], jobs: [] } });
  });
  await start(page);
  await page.locator('.digest-lead').evaluate(element => {
    const range = document.createRange(); range.selectNodeContents(element);
    getSelection().removeAllRanges(); getSelection().addRange(range);
    element.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
  });
  await page.locator('#quoteAgentBtn').click();
  await page.locator('#agentPromptInput').fill('这段话依据是什么？');
  await page.locator('#agentSendBtn').click();
  await expect(page.locator('.agent-message-quote')).toBeVisible();
  expect(sent.question).toBe('这段话依据是什么？');
  expect(sent.quote_text).toBeTruthy(); expect(sent.question).not.toContain(sent.quote_text);
  await expect(page.locator('.agent-message-quote')).toContainText(sent.quote_text);
  for (const theme of ['light', 'dark', 'eink']) {
    await chooseTheme(page, theme);
    await expect(page.locator('.agent-message-quote')).toHaveCSS('border-left-width', '3px');
    await page.screenshot({ path: `.ui-artifacts/v011-quote-${theme}.png` });
  }
});
