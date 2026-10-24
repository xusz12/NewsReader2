import { test, expect } from '@playwright/test';
import { chooseTheme } from './helpers.js';

const list = page => page.locator('#feedCardList');
const groups = page => page.locator('#feedCardList .feed-date-group');
const dayCount = (page, date) => page.locator(`.feed-date-group[data-date="${date}"] .feed-date-count`);
async function expectDates(page, expected) {
  for (const group of await groups(page).all()) {
    const date = await group.getAttribute('data-date');
    await expect(group.locator('.feed-date-title')).toHaveText(date);
    await expect(group.locator('.feed-date-count')).toHaveText(`${expected[date] || 0} 条`);
  }
}
test.beforeEach(async ({ request }) => { await request.post('/__test/reset-reading'); });

test('date groups include unloaded news, merge across pages and scope counts to category and search', async ({ page, request }) => {
  await page.goto('/');
  await expect(list(page).locator('.news-card')).toHaveCount(30);
  await expect(groups(page)).toHaveCount(3);
  await expect(dayCount(page, '2026-10-12')).toHaveText('15 条');
  await expect(page.locator('.feed-date-group[data-date="2026-10-12"] .news-card')).toHaveCount(10);
  for (const timestamp of await list(page).locator('.meta-time-row').allTextContents()) expect(timestamp).toMatch(/^\d{2}:\d{2}$/);
  await page.locator('#loadMoreBtn').evaluate(button => button.click());
  await expect(list(page).locator('.news-card')).toHaveCount(60);
  await expect(groups(page)).toHaveCount(5);
  await expect(page.locator('.feed-date-group[data-date="2026-10-12"]')).toHaveCount(1);
  await expect(page.locator('.feed-date-group[data-date="2026-10-12"] .news-card')).toHaveCount(15);

  await page.locator('[data-category="economy"]').click();
  const scoped = await (await request.get('/api/news?category=economy&read_filter=unread&per=100')).json();
  await expect(groups(page).first()).toHaveAttribute('data-date', '2026-10-12');
  await expectDates(page, scoped.date_counts);
  await expect(dayCount(page, '2026-10-12')).toHaveText('10 条');
  await expect(page.locator('#feedColFeed [data-filter="unread"]')).toHaveText('未读 (65)');
  await page.locator('[data-source="reuters"]').click();
  await page.locator('#feedSearchInput').fill('isolated interface test fixture');
  const search = await (await request.get(`/api/search?${new URLSearchParams({ q: 'isolated interface test fixture', category: 'economy', source_filter: 'reuters', read_filter: 'unread', feed_filters: '1', per: '100' })}`)).json();
  await expect(list(page).locator('.news-card')).toHaveCount(search.total);
  await expectDates(page, search.date_counts);
  await page.locator('#feedSearchInput').fill('没有这种新闻的关键词');
  await expect(list(page)).toContainText('没有符合条件的新闻');
  await expect(groups(page)).toHaveCount(0);
});

for (const width of [1440, 390]) {
  for (const theme of ['light', 'dark', 'eink']) {
    test(`transparent date headers stick, switch dates and reverse at ${width}px in ${theme}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.goto('/');
      await chooseTheme(page, theme);
      await page.locator('#feedColFeed [data-filter="all"]').click();
      await expect(dayCount(page, '2026-10-10')).toHaveText('5 条');
      const first = groups(page).first().locator('.feed-date-heading');
      const second = groups(page).nth(1).locator('.feed-date-heading');
      const bounds = await list(page).boundingBox();
      await list(page).evaluate(node => { node.scrollTop = 100; });
      await expect.poll(async () => (await first.boundingBox()).y).toBeCloseTo(bounds.y, 0);
      await expect(first).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
      await expect(first).toHaveCSS('box-shadow', 'none');
      await expect(groups(page).first().locator('.feed-date-cards')).not.toHaveCSS('clip-path', 'none');
      expect(await first.evaluate(node => {
        const box = node.getBoundingClientRect();
        return !!document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2)?.closest('.news-card');
      })).toBe(false);
      await page.locator('#feedColFeed').screenshot({ path: `.ui-artifacts/date-header-${width}-${theme}.png` });
      await list(page).evaluate(node => {
        const next = node.querySelectorAll('.feed-date-group')[1];
        node.scrollTop += next.getBoundingClientRect().top - node.getBoundingClientRect().top + 50;
      });
      await expect.poll(async () => (await second.boundingBox()).y).toBeCloseTo(bounds.y, 0);
      expect((await first.boundingBox()).y).toBeLessThan(bounds.y);
      await expect(second.locator('.feed-date-title')).toHaveText('2026-10-11');
      await list(page).evaluate(node => { node.scrollTop = 100; });
      await expect.poll(async () => (await first.boundingBox()).y).toBeCloseTo(bounds.y, 0);
      await page.locator('#sortNewsBtn').click();
      await expect(groups(page).first()).toHaveAttribute('data-date', '2026-10-14');
      await expect(dayCount(page, '2026-10-14')).toHaveText('15 条');
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    });
  }
}

test('daily unread count updates immediately and rolls back a failed read without moving the list', async ({ page }) => {
  await page.goto('/');
  await expect(dayCount(page, '2026-10-10')).toHaveText('5 条');
  let release;
  const held = new Promise(resolve => { release = resolve; });
  await page.route('**/api/news/mark-read-by-ids', async route => {
    await held;
    await route.fulfill({ status: 503, json: { error: '保存失败' } });
  });
  try {
    await list(page).evaluate(node => {
      const card = node.querySelector('.news-card');
      node.scrollTop += card.getBoundingClientRect().bottom - node.getBoundingClientRect().top + 2;
    });
    await expect(dayCount(page, '2026-10-10')).toHaveText('4 条');
    const scroll = await list(page).evaluate(node => node.scrollTop);
    release();
    await expect(dayCount(page, '2026-10-10')).toHaveText('5 条');
    expect(await list(page).evaluate(node => node.scrollTop)).toBe(scroll);
  } finally { release(); await page.unrouteAll({ behavior: 'wait' }); }
});
