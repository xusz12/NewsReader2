import { test, expect } from '@playwright/test';
import { answerConfirm } from './helpers.js';

test.beforeEach(async ({ page, request }) => {
  await request.post('/__test/reset-reading');
  await page.clock.install();
  await page.goto('/');
  await expect(page.locator('#feedColFeed [data-filter="unread"]')).toHaveText('未读 (65)');
});

test('primary counts span categories while the list and mark-all action keep their category scope', async ({ page, request }) => {
  const row = page.getByRole('group', { name: '新闻分类' });
  await expect(row.getByRole('button')).toHaveText(['全部', '中国', 'AI', '政治与国际', '经济与金融', '商业', '科技与科学', '其他']);
  await row.getByRole('button', { name: '经济与金融', exact: true }).click();
  await expect(page.locator('#feedColFeed [data-filter="unread"]')).toHaveText('未读 (65)');
  await expect(page.locator('#feedCardList .news-card')).toHaveCount(30);
  await expect(page.locator('#feedCardList .news-category-tag')).toHaveText(Array(30).fill('经济与金融'));
  await page.locator('#feedColFeed [data-filter="all"]').click();
  await expect(page.locator('#feedColFeed [data-filter="all"]')).toHaveText('全部 (65)');
  await row.getByRole('button', { name: 'AI', exact: true }).click();
  await expect(page.locator('#feedCardList .news-card')).toHaveCount(4);
  await expect(page.locator('#feedColFeed [data-filter="all"]')).toHaveText('全部 (65)');
  await page.locator('#feedColFeed [data-filter="unread"]').click();
  await row.getByRole('button', { name: '经济与金融', exact: true }).click();
  await expect(page.locator('#feedCardList .news-card')).toHaveCount(30);
  await expect(page.locator('#feedColFeed [data-filter="unread"]')).toHaveText('未读 (65)');
  await page.screenshot({ path: '.ui-artifacts/categories-desktop.png' });
  await page.locator('#markAllReadBtn').click();
  expect(await answerConfirm(page)).toContain('新闻分类：「经济与金融」');
  await expect(page.locator('#feedColFeed [data-filter="unread"]')).toHaveText('未读 (25)');
  await expect(page.locator('#feedCardList .news-card')).toHaveCount(0);
  const total = await (await request.get('/api/news?read_filter=unread&count_only=1')).json();
  expect(total.total).toBe(25);
  await row.getByRole('button', { name: 'AI', exact: true }).click();
  await expect(page.locator('#feedColFeed [data-filter="unread"]')).toHaveText('未读 (25)');
  await expect(page.locator('#feedCardList .news-card')).toHaveCount(4);
});

test('search and source still scope primary counts while category only scopes the list', async ({ page, request }) => {
  await page.locator('[data-category="economy"]').click();
  await page.locator('#feedSearchInput').fill('isolated interface test fixture');
  await page.clock.runFor(300);
  await expect(page.locator('#feedCardList .news-card').first()).toBeVisible();
  for (const tag of await page.locator('#feedCardList .news-category-tag').allTextContents()) expect(tag).toBe('经济与金融');
  await page.locator('[data-source="reuters"]').click();
  const params = new URLSearchParams({ q: 'isolated interface test fixture', source_filter: 'reuters', read_filter: 'unread', feed_filters: '1', count_only: '1' });
  const count = await (await request.get(`/api/search?${params}`)).json();
  await expect(page.locator('#feedColFeed [data-filter="unread"]')).toHaveText(`未读 (${count.total})`);
  await page.locator('[data-category="ai"]').click();
  await expect(page.locator('#feedColFeed [data-filter="unread"]')).toHaveText(`未读 (${count.total})`);
  const collectionRequest = page.waitForRequest(req => req.url().includes('/api/news?') && req.url().includes('collection=favorites'));
  await page.locator('#navFavoritesBtn').click();
  expect(new URL((await collectionRequest).url()).searchParams.get('category')).toBe('all');
  await expect(page.locator('#newsCategoryFilters')).toBeHidden();
  await page.locator('#navFeedBtn').click();
  await expect(page.locator('[data-category="ai"]')).toHaveAttribute('aria-pressed', 'true');
});

test('keyboard focus survives count refresh and can reach the last horizontally clipped category', async ({ page }) => {
  await page.locator('[data-category="all"]').focus();
  await page.keyboard.press('End');
  await expect(page.locator('[data-category="other"]')).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('#feedColFeed [data-filter="unread"]')).toHaveText('未读 (65)');
  await expect(page.locator('[data-category="other"]')).toBeFocused();
  await expect(page.locator('[data-category="other"]')).toHaveAttribute('aria-pressed', 'true');
  await page.clock.runFor(31000);
  await expect(page.locator('[data-category="other"]')).toBeFocused();
});

test('missing key and failed classification remain explicit, retry uses the queue endpoint', async ({ page }) => {
  const progress = { ok: true, total: 65, done: 60, failed: 5, pending: 0, running: 0, configured: true, completed_revision: 0 };
  await page.route('**/api/news/classification', route => route.fulfill({ json: progress }));
  await page.clock.runFor(31000);
  await page.getByRole('progressbar', { name: '新闻分类进度' }).hover();
  await expect(page.locator('#classificationProgressPanel')).toContainText('失败 5');
  const retry = page.waitForRequest('**/api/news/classification/retry');
  await page.locator('#classificationProgressRetry').click();
  expect((await retry).method()).toBe('POST');
  progress.configured = false;
  await page.clock.runFor(31000);
  await page.getByRole('progressbar', { name: '新闻分类进度' }).hover();
  await expect(page.locator('#classificationProgressPanel')).toContainText('等待配置 Jev');
  await page.locator('#classificationProgressSettings').click();
  await expect(page.locator('#settingsCategory-deepseek')).toHaveAttribute('aria-current', 'true');
});

test('unread-only progress uses its scope and hides when eligible work is complete', async ({ page }) => {
  const progress = { ok: true, total: 10, done: 7, pending: 3, running: 0, failed: 0,
    configured: true, unread_only: true, completed_revision: 0 };
  await page.route('**/api/news/classification', route => route.fulfill({ json: progress }));
  await page.clock.runFor(31000);
  await page.getByRole('progressbar', { name: '新闻分类进度' }).hover();
  await expect(page.locator('#classificationProgressScope')).toHaveText('当前模式：仅未读');
  await expect(page.locator('#classificationProgressCount')).toHaveText('7/10 条');
  progress.done = 10;
  progress.pending = 0;
  await page.clock.runFor(31000);
  await expect(page.locator('#classificationProgress')).toBeHidden();
});

test('shared rate limiting is shown as cooldown without replacing the news list', async ({ page }) => {
  await page.route('**/api/news/classification', route => route.fulfill({ json: {
    ok: true, total: 65, done: 30, pending: 35, running: 0, failed: 0, configured: true,
    throttle: { configured_concurrency: 8, effective_concurrency: 4, rate_limited: true, cooldown_seconds: 20 },
  } }));
  const first = await page.locator('#feedCardList .news-card').first().getAttribute('data-id');
  await page.clock.runFor(31000);
  await page.getByRole('progressbar', { name: '新闻分类进度' }).hover();
  await expect(page.locator('#classificationProgressPanel')).toContainText('限流降速 · 并发 4/8 · 20 秒后继续');
  await expect(page.locator('#feedCardList .news-card').first()).toHaveAttribute('data-id', first);
});

for (const theme of ['light', 'dark', 'eink']) {
  test(`mobile ${theme}: category row scrolls without page overflow`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.evaluate(theme => { document.documentElement.dataset.theme = theme; }, theme);
    const row = page.locator('#newsCategoryFilters');
    const metrics = await row.evaluate(el => ({ width: el.clientWidth, scroll: el.scrollWidth,
      overflow: document.documentElement.scrollWidth > innerWidth }));
    expect(metrics.scroll).toBeGreaterThan(metrics.width);
    expect(metrics.overflow).toBe(false);
    await row.locator('[data-category="science"]').click();
    await expect(page.locator('#feedColFeed [data-filter="unread"]')).toHaveText('未读 (65)');
    await expect(page.locator('#feedCardList .news-category-tag').first()).toHaveText('科技与科学');
    const active = row.locator('[data-category="science"]');
    const expected = await active.evaluate(el => {
      const probe = document.createElement('span');
      probe.style.cssText = 'color:var(--accent);background:var(--accent-soft);border:1px solid color-mix(in oklch,var(--accent) 30%,transparent)';
      el.append(probe);
      const css = getComputedStyle(probe);
      const result = { color: css.color, background: css.backgroundColor, border: css.borderTopColor };
      probe.remove();
      return result;
    });
    await expect(active).toHaveCSS('color', expected.color);
    await expect(active).toHaveCSS('background-color', expected.background);
    await expect(active).toHaveCSS('border-top-color', expected.border);
    await page.screenshot({ path: `.ui-artifacts/categories-${theme}-mobile.png` });
  });
}
