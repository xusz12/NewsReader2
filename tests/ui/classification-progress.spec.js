import { test, expect } from '@playwright/test';
import { chooseTheme } from './helpers.js';

const indicator = page => page.getByRole('progressbar', { name: '新闻分类进度' });
const panel = page => page.getByRole('region', { name: '新闻分类进度详情' });
const base = () => ({ ok: true, total: 100, done: 25, pending: 67, running: 8, failed: 0,
  configured: true, unread_only: false, completed_revision: 200,
  throttle: { configured_concurrency: 8, effective_concurrency: 8, cooldown_seconds: 0, rate_limited: false } });

async function start(page, progress = base()) {
  const state = { progress, failed: false, requests: 0 };
  await page.clock.install();
  await page.route('**/api/news/classification', route => {
    state.requests++;
    return route.fulfill(state.failed ? { status: 503, json: { error: 'unavailable' } } : { json: state.progress });
  });
  await page.goto('/');
  await expect(page.locator('#feedCardList .news-card').first()).toBeVisible();
  await expect(indicator(page)).toHaveAttribute('aria-valuenow', String(progress.done));
  return state;
}

test('non-button ring lives beside notifications, follows real counts and hides on completion', async ({ page }) => {
  const state = await start(page);
  expect(await indicator(page).evaluate(el => el.tagName)).toBe('DIV');
  expect(await page.locator('#classificationProgress').evaluate(el => el.nextElementSibling.id)).toBe('agentNotifications');
  await expect(page.locator('#classificationNotice')).toHaveCount(0);
  await expect(indicator(page).getByRole('button')).toHaveCount(0);
  await expect(page.locator('#classificationProgressArc')).toHaveCSS('stroke-dashoffset', '75px');
  await expect(panel(page)).toBeHidden();
  const first = await page.locator('#feedCardList .news-card').first().getAttribute('data-id');
  await indicator(page).hover();
  await expect(panel(page)).toContainText('25/100 条');
  await expect(page.locator('#classificationProgressScope')).toHaveText('当前模式：全部');
  await panel(page).hover();
  await page.clock.runFor(300);
  await expect(panel(page)).toBeVisible();
  state.progress.done = 60;
  state.progress.pending = 32;
  state.progress.unread_only = true;
  await page.clock.runFor(2100);
  await expect(indicator(page)).toHaveAttribute('aria-valuenow', '60');
  await expect(page.locator('#classificationProgressArc')).toHaveCSS('stroke-dashoffset', '40px');
  await expect(panel(page)).toContainText('60/100 条');
  await expect(page.locator('#classificationProgressScope')).toHaveText('当前模式：仅未读');
  await expect(page.locator('#feedCardList .news-card').first()).toHaveAttribute('data-id', first);
  Object.assign(state.progress, { done: 100, pending: 0, running: 0 });
  await page.clock.runFor(2100);
  await expect(page.locator('#classificationProgress')).toBeHidden();
  await expect(panel(page)).toBeHidden();
  // New indexed work makes the indicator return, without revisiting the news feed.
  Object.assign(state.progress, { total: 110, pending: 10 });
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(indicator(page)).toHaveAttribute('aria-valuemax', '110');
});

test('hover, keyboard and other header popovers share a dismissible card', async ({ page }) => {
  await start(page);
  await indicator(page).focus();
  await expect(panel(page)).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(panel(page)).toBeHidden();
  await expect(indicator(page)).toBeFocused();
  await page.locator('#agentNotificationsBtn').click();
  await expect(page.locator('#agentNotificationsPanel')).toBeVisible();
  await indicator(page).hover();
  await expect(panel(page)).toBeVisible();
  await expect(page.locator('#agentNotificationsPanel')).toBeHidden();
  await page.mouse.move(600, 500);
  await page.clock.runFor(200);
  await expect(panel(page)).toBeHidden();
});

test('progress refreshes in other collections and keeps last counts on request failure', async ({ page }) => {
  const state = await start(page);
  await page.locator('#navFavoritesBtn').click();
  await expect(page.locator('#newsCategoryFilters')).toBeHidden();
  state.progress.done = 40;
  state.progress.pending = 52;
  await page.clock.runFor(2100);
  await expect(indicator(page)).toHaveAttribute('aria-valuenow', '40');
  state.failed = true;
  await indicator(page).hover();
  await expect(panel(page)).toContainText('进度暂时无法更新，显示上次结果');
  await expect(indicator(page)).toHaveAttribute('aria-valuenow', '40');
  await page.clock.runFor(5000);
  await expect(indicator(page)).toHaveAttribute('aria-valuenow', '40');
  state.failed = false;
  state.progress.done = 50;
  state.progress.pending = 42;
  await page.clock.runFor(2100);
  await expect(indicator(page)).toHaveAttribute('aria-valuenow', '50');
  await expect(panel(page)).not.toContainText('暂时无法更新');
});

test('hidden pages stop polling and visibility resumes from current counts', async ({ page }) => {
  const state = await start(page);
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  const requests = state.requests;
  await page.clock.runFor(10000);
  expect(state.requests).toBe(requests);
  state.progress.done = 75;
  state.progress.pending = 17;
  await page.evaluate(() => {
    delete document.hidden;
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect(indicator(page)).toHaveAttribute('aria-valuenow', '75');
});

test('zero percent, failures and unread scope stay explicit without pretending completion', async ({ page }) => {
  const state = await start(page, { ...base(), done: 0, pending: 100, running: 0, unread_only: true });
  await expect(page.locator('#classificationProgressArc')).toHaveCSS('opacity', '0');
  await indicator(page).hover();
  await expect(page.locator('#classificationProgressScope')).toHaveText('当前模式：仅未读');
  Object.assign(state.progress, { done: 98, pending: 0, failed: 2 });
  await page.clock.runFor(2100);
  await expect(indicator(page)).toHaveAttribute('aria-valuenow', '98');
  await expect(panel(page)).toContainText('有失败项待重试');
  await expect(page.locator('#classificationProgressRetry')).toBeVisible();
});

for (const width of [320, 390, 920, 921, 1279, 1280, 1440]) {
  test(`header progress fits ${width}px and hover card stays inside viewport`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await start(page);
    await indicator(page).hover();
    await expect(panel(page)).toBeVisible();
    const ring = await indicator(page).boundingBox();
    const card = await panel(page).boundingBox();
    expect(ring.x).toBeGreaterThanOrEqual(0);
    expect(ring.x + ring.width).toBeLessThanOrEqual(width);
    expect(card.x).toBeGreaterThanOrEqual(0);
    expect(card.x + card.width).toBeLessThanOrEqual(width);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  });
}

for (const theme of ['light', 'dark', 'eink', 'system-light', 'system-dark']) {
  test(`progress uses theme color and an empty ring in ${theme}`, async ({ page }) => {
    await page.setViewportSize({ width: theme === 'eink' ? 390 : 1440, height: 900 });
    await start(page);
    if (theme.startsWith('system-')) {
      await page.emulateMedia({ colorScheme: theme.slice(7) });
      await chooseTheme(page, 'system');
    } else await chooseTheme(page, theme);
    await indicator(page).hover();
    expect(await page.locator('#classificationProgressArc').evaluate(el => {
      const probe = document.createElement('span');
      probe.style.color = 'var(--accent)';
      el.parentElement.after(probe);
      const expected = getComputedStyle(probe).color;
      probe.remove();
      return getComputedStyle(el).stroke === expected && getComputedStyle(el).fill === 'none';
    })).toBe(true);
    await page.screenshot({ path: `.ui-artifacts/classification-progress-${theme}.png` });
  });
}

test('touch can inspect the status card without triggering a classification action', async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const page = await context.newPage();
  try {
    await start(page);
    const actions = [];
    page.on('request', request => { if (request.method() === 'POST') actions.push(request.url()); });
    await indicator(page).tap();
    await expect(panel(page)).toBeVisible();
    await page.locator('#feedCardList .news-card').first().tap();
    await expect(panel(page)).toBeHidden();
    expect(actions.filter(url => url.includes('/classification'))).toEqual([]);
  } finally { await context.close(); }
});
