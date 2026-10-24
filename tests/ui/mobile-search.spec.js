import { test, expect } from '@playwright/test';
import { chooseTheme, openNavigation } from './helpers.js';

const toggle = page => page.locator('#feedSearchToggleBtn');
const input = page => page.locator('#feedSearchInput');
const row = page => page.locator('#feedSearchRow');
const cardIds = page => page.locator('#feedCardList .news-card').evaluateAll(nodes => nodes.map(node => node.dataset.id));
test.beforeEach(async ({ request }) => { await request.post('/__test/reset-reading'); });

for (const width of [320, 390]) {
  test(`mobile search clears on collapse and restores the current category at ${width}px`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 844 });
    await page.goto('/');
    await expect(page.locator('#feedCardList .news-card')).toHaveCount(30);
    await expect(input(page)).toBeHidden();
    await expect(row(page)).toHaveJSProperty('inert', true);
    await expect(toggle(page)).toHaveAttribute('aria-expanded', 'false');
    expect(await toggle(page).evaluate(node => node.previousElementSibling.id)).toBe('markAllReadBtn');
    expect(await toggle(page).evaluate(node => node.nextElementSibling)).toBeNull();
    expect((await row(page).boundingBox()).height).toBe(0);
    await page.locator('#feedColFeed .feed-header').screenshot({ path: `.ui-artifacts/mobile-search-closed-${width}.png` });

    await page.locator('[data-category="economy"]').click();
    await expect(page.locator('#feedCardList .news-category-tag')).toHaveText(Array(30).fill('经济与金融'));
    const originalIds = await cardIds(page);
    await toggle(page).click();
    await expect(input(page)).toBeFocused();
    await expect(toggle(page)).toHaveAttribute('aria-expanded', 'true');
    await input(page).fill('台积电');
    const params = new URLSearchParams({ q: '台积电', category: 'economy', read_filter: 'unread', feed_filters: '1' });
    const expected = await (await request.get(`/api/search?${params}`)).json();
    expect(expected.total).toBeGreaterThan(0);
    await expect(page.locator('#feedCardList .news-card')).toHaveCount(expected.total);
    await toggle(page).click();
    await expect(input(page)).toBeHidden();
    await expect(toggle(page)).toBeFocused();
    await expect(input(page)).toHaveValue('');
    await expect.poll(() => cardIds(page)).toEqual(originalIds);
    await expect(page.locator('[data-category="economy"]')).toHaveAttribute('aria-pressed', 'true');
    await toggle(page).click();
    await expect(input(page)).toBeFocused();
    await expect(input(page)).toHaveValue('');
    await expect(page.locator('#feedCardList .news-card')).toHaveCount(30);
    await expect(page.locator('#feedColFeed [data-filter="unread"]')).toHaveText('未读 (65)');
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    await page.locator('#feedColFeed .feed-header').screenshot({ path: `.ui-artifacts/mobile-search-open-${width}.png` });
  });
}

for (const pendingValue of ['新关键词', '']) {
  test(`collapsing cancels a pending search edit to ${pendingValue || 'empty text'}`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.clock.install();
    await page.goto('/');
    await expect(page.locator('#feedCardList .news-card')).toHaveCount(30);
    const originalIds = await cardIds(page);
    await toggle(page).click();
    await input(page).fill('不存在的新闻关键词');
    await page.clock.runFor(300);
    await expect(page.locator('#feedCardList .news-card')).toHaveCount(0);
    const listRequests = [];
    page.on('request', req => {
      const url = new URL(req.url());
      if (['/api/news', '/api/search'].includes(url.pathname) && !url.searchParams.has('count_only')) listRequests.push(url);
    });
    // Edit and collapse within the same event turn, before the 250ms debounce can fire.
    await page.evaluate(value => {
      const input = document.querySelector('#feedSearchInput');
      input.value = value;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      document.querySelector('#feedSearchToggleBtn').click();
    }, pendingValue);
    await expect(input(page)).toHaveValue('');
    await expect.poll(() => cardIds(page)).toEqual(originalIds);
    await page.clock.runFor(300);
    expect(listRequests.map(url => [url.pathname, url.searchParams.get('q')])).toEqual([['/api/news', '']]);
  });
}

test('collapsing aborts an in-flight search and preserves source, category, reading filter and sort', async ({ page, request }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await openNavigation(page);
  await page.locator('[data-source="reuters"]').click();
  await page.locator('[data-category="economy"]').click();
  await page.locator('#sortNewsBtn').click();
  const scope = { collection: 'feed', source_filter: 'reuters', category: 'economy', read_filter: 'unread', sort_order: 'reverse' };
  const expected = await (await request.get(`/api/news?${new URLSearchParams({ ...scope, per: '30' })}`)).json();
  const originalIds = expected.items.map(item => item.id);
  expect(originalIds.length).toBeGreaterThan(0);
  await expect.poll(() => cardIds(page)).toEqual(originalIds);
  let releaseSearch, searchStarted;
  const held = new Promise(resolve => { releaseSearch = resolve; });
  const started = new Promise(resolve => { searchStarted = resolve; });
  await page.route('**/api/search?**', async route => {
    if (new URL(route.request().url()).searchParams.has('count_only')) return route.continue();
    searchStarted();
    await held;
    await route.fulfill({ json: { items: [], total: 0, pages: 1, page: 1, has_more: false } });
  });
  await toggle(page).click();
  await input(page).fill('台积电');
  await started;
  const aborted = page.waitForEvent('requestfailed', req => new URL(req.url()).pathname === '/api/search');
  const restored = page.waitForRequest(req => {
    const url = new URL(req.url());
    return url.pathname === '/api/news' && !url.searchParams.has('count_only');
  });
  await toggle(page).click();
  await aborted;
  const params = new URL((await restored).url()).searchParams;
  for (const [key, value] of Object.entries({ ...scope, q: '' })) expect(params.get(key)).toBe(value);
  await expect.poll(() => cardIds(page)).toEqual(originalIds);
  releaseSearch();
  await page.unrouteAll({ behavior: 'wait' });
  await expect(input(page)).toHaveValue('');
  await expect.poll(() => cardIds(page)).toEqual(originalIds);
});

test('search keyboard controls and responsive visibility stay in sync at 920/921px', async ({ page }) => {
  await page.setViewportSize({ width: 920, height: 900 });
  await page.goto('/');
  await expect(input(page)).toBeHidden();
  await toggle(page).focus();
  await page.keyboard.press('Enter');
  await expect(input(page)).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(input(page)).toBeHidden();
  await expect(toggle(page)).toBeFocused();
  await page.keyboard.press('/');
  await expect(input(page)).toBeFocused();
  await toggle(page).click();
  await page.setViewportSize({ width: 921, height: 900 });
  await expect(input(page)).toBeVisible();
  await expect(row(page)).toHaveJSProperty('inert', false);
  await expect(toggle(page)).toBeHidden();
  await input(page).focus();
  await page.setViewportSize({ width: 920, height: 900 });
  await expect(input(page)).toBeHidden();
  await expect(row(page)).toHaveJSProperty('inert', true);
  await expect(toggle(page)).toBeFocused();
});

for (const [theme, reducedMotion] of [['light', 'no-preference'], ['dark', 'no-preference'], ['eink', 'no-preference'], ['light', 'reduce']]) {
  test(`search expands quickly and respects motion preferences in ${theme}/${reducedMotion}`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.emulateMedia({ reducedMotion });
    await page.goto('/');
    await chooseTheme(page, theme);
    await expect(input(page)).toBeHidden();
    const animations = await page.evaluate(() => {
      document.querySelector('#feedSearchToggleBtn').click();
      return document.querySelector('#feedSearchRow').getAnimations().map(animation => animation.effect.getTiming().duration);
    });
    if (theme === 'eink' || reducedMotion === 'reduce') expect(animations).toEqual([]);
    else { expect(animations.length).toBeGreaterThan(0); expect(Math.max(...animations)).toBe(120); }
    await expect(input(page)).toBeFocused();
    await expect.poll(async () => (await row(page).boundingBox()).height).toBeGreaterThan(25);
    await page.locator('#feedColFeed .feed-header').screenshot({ path: `.ui-artifacts/mobile-search-${theme}-${reducedMotion}.png` });
    await toggle(page).click();
    await expect(input(page)).toBeHidden();
    expect((await row(page).boundingBox()).height).toBe(0);
  });
}
