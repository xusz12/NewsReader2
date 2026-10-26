import { test, expect } from '@playwright/test';
import { chooseTheme, openNavigation } from './helpers.js';

const buttonFor = page => page.locator('[data-topic-read-later="ui-001"]');
async function expectPlainIcon(button) {
  await expect(button).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
  await expect(button).toHaveCSS('border-top-width', '0px');
  await expect(button).toHaveCSS('box-shadow', 'none');
}
async function openTopic(page) {
  await page.goto('/');
  await expect(page.locator('#feedCardList .news-card').first()).toBeVisible();
  await openNavigation(page);
  await page.locator('#navTopicsBtn').click();
  await page.locator('[data-topic-id="test-acquisition"]').click();
  await expect(buttonFor(page)).toBeAttached();
}
test.beforeEach(async ({ request }) => {
  await request.post('/__test/topics');
  await request.post('/__test/reset-reading');
  // The fixture has cached bodies; begin with a finished saved article that
  // the user has independently marked unread, then add it again.
  await request.patch('/api/news/ui-001/state', {
    data: { read: false, read_later: false, read_later_done: true },
  });
});

test('topic read later acknowledges the feed, stays in the right pane and syncs across topics', async ({ page, request }) => {
  await openTopic(page);
  const button = buttonFor(page);
  const card = button.locator('xpath=ancestor::article');
  await card.scrollIntoViewIfNeeded();
  await page.mouse.move(0, 0);
  await expect(button).toHaveCSS('opacity', '1');
  await button.focus();
  await expect(button).toHaveCSS('opacity', '1');
  await button.blur();
  await card.hover();
  await expect(button).toHaveCSS('opacity', '1');
  const position = await button.boundingBox();
  const time = await card.locator('time').boundingBox();
  expect(position.x + position.width).toBeLessThan(time.x);
  expect(Math.abs(position.y + position.height / 2 - time.y - time.height / 2)).toBeLessThan(2);
  const middle = await page.locator('#feedColTopics').boundingBox();
  const scroll = await page.locator('#topicScrollArea').evaluate(node => node.scrollTop);
  const unread = Number(await page.locator('#unreadBadge').textContent());
  const otherBefore = await (await request.get('/api/news/ui-064/detail')).json();
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  await page.route('**/api/news/ui-001/state', async route => {
    expect(route.request().postDataJSON()).toEqual({ read: true, read_later: true });
    await gate;
    await route.continue();
  });
  await button.click();
  await expect(button).toHaveAttribute('aria-busy', 'true');
  await expectPlainIcon(button);
  await expect(button).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#topicContent')).toBeVisible();
  release();
  await expect(button).toHaveAttribute('aria-busy', 'false');
  await expect(button).toHaveAttribute('aria-label', /全文待看/);
  await expect(page.locator('#unreadBadge')).toHaveText(String(unread - 1));
  const persisted = await (await request.get('/api/news/ui-001/detail')).json();
  expect(persisted.read_at).toBeTruthy();
  expect(persisted.read_later_at).toBeTruthy();
  expect(persisted.read_later_done_at).toBeNull();
  const otherAfter = await (await request.get('/api/news/ui-064/detail')).json();
  expect(otherAfter.read_at).toBe(otherBefore.read_at);
  expect(otherAfter.read_later_at).toBe(otherBefore.read_later_at);
  expect((await page.locator('#feedColTopics').boundingBox()).x).toBe(middle.x);
  expect(await page.locator('#topicScrollArea').evaluate(node => node.scrollTop)).toBe(scroll);
  await page.locator('[data-topic-id="test-industry"]').click();
  await expect(button).toHaveAttribute('aria-label', /全文待看/);
  await openTopic(page);
  await expect(button).toHaveAttribute('aria-pressed', 'true');
  await expect(button).toHaveAttribute('aria-label', /全文待看/);
});

test('uncached topic articles reveal on hover or focus; fetching the body makes the icon persistent', async ({ page }) => {
  let ready = false;
  await page.route('**/api/topics/test-acquisition*', async route => {
    if (route.request().method() !== 'GET') return route.continue();
    const data = await (await route.fetch()).json();
    const item = data.items.find(row => row.id === 'ui-001');
    Object.assign(item, { detail_ready: ready, detail_status: ready ? 'success' : 'running', read_later_done_at: null, read_later_at: 'queued' });
    await route.fulfill({ json: data });
  });
  await openTopic(page);
  const button = buttonFor(page), card = button.locator('xpath=ancestor::article');
  await card.scrollIntoViewIfNeeded();
  await page.mouse.move(0, 0);
  await expect(button).toHaveCSS('opacity', '0');
  await button.focus();
  await page.keyboard.press('Tab');
  await page.keyboard.press('Shift+Tab');
  await expect(button).toBeFocused();
  await expect(button).toHaveCSS('opacity', '1');
  await expectPlainIcon(button);
  await expect(button).toHaveCSS('outline-style', 'solid');
  await button.blur();
  await card.hover();
  await expect(button).toHaveCSS('opacity', '1');
  await page.mouse.move(0, 0);
  await expect(button).toHaveCSS('opacity', '0');
  ready = true;
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await expect(button).toHaveAttribute('aria-label', /全文待看/);
  await expect(button).toHaveCSS('opacity', '1');
  await expectPlainIcon(button);
});

test('topic read later rolls back both states on save failure without navigating', async ({ page, request }) => {
  await openTopic(page);
  await page.route('**/api/news/ui-001/state', route => route.fulfill({ status: 500, json: { error: 'save_failed' } }));
  const button = buttonFor(page);
  await button.locator('xpath=ancestor::article').hover();
  await button.click();
  await expect(button).toHaveAttribute('aria-busy', 'false');
  await expect(button).toHaveAttribute('aria-pressed', 'false');
  await expect(button).toHaveAttribute('aria-label', /已看完/);
  await expect(page.locator('#topicContent')).toBeVisible();
  const persisted = await (await request.get('/api/news/ui-001/detail')).json();
  expect(persisted.read_at).toBeNull();
  expect(persisted.read_later_at).toBeNull();
  expect(persisted.read_later_done_at).toBeTruthy();
});

for (const mobile of [false, true]) {
  for (const theme of ['light', 'dark', 'system', 'eink']) {
    test.describe(`${mobile ? 'mobile' : 'desktop'} ${theme}`, () => {
      test.use({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 900 }, isMobile: mobile, hasTouch: mobile, colorScheme: 'dark' });
      test('topic read later uses the shared button and is reachable', async ({ page }) => {
        await openTopic(page);
        await chooseTheme(page, theme);
        const button = buttonFor(page);
        await button.scrollIntoViewIfNeeded();
        if (!mobile) await page.mouse.move(0, 0);
        await expect(button).toHaveCSS('opacity', '1');
        await expectPlainIcon(button);
        if (!mobile) await button.hover();
        await expectPlainIcon(button);
        await button.click();
        await expect(button).toHaveAttribute('aria-busy', 'false');
        await expect(button).toHaveAttribute('aria-label', /全文待看/);
        if (!mobile) await page.mouse.move(0, 0);
        await expectPlainIcon(button);
        await expect(button).toHaveCSS('opacity', '1');
        await expect(button).toHaveClass(/card-action-btn--icon/);
        await expect(button.locator('svg')).toHaveCount(1);
        expect(await button.textContent()).toBe('');
        const box = await button.boundingBox();
        expect(box.width).toBeGreaterThanOrEqual(mobile ? 32 : 26);
        expect(box.height).toBeGreaterThanOrEqual(mobile ? 32 : 26);
        expect(box.x).toBeGreaterThanOrEqual(0);
        expect(box.x + box.width).toBeLessThanOrEqual(mobile ? 390 : 1440);
        await page.screenshot({ path: `.ui-artifacts/topic-read-later-${mobile ? 'mobile' : 'desktop'}-${theme}.png` });
      });
    });
  }
}
