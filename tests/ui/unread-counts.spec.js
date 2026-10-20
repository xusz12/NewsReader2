import { test, expect } from '@playwright/test';

const chip = page => page.locator('#feedColFeed .filter-chip.active[data-filter]');
const nav = page => page.locator('#unreadBadge');
const sourceBadge = (page, key = 'all') => page.locator(`[data-source="${key}"] .nav-badge`);
const card = (page, id) => page.locator(`#feedCardList [data-id="${id}"]`);
const gate = () => {
  let release;
  const promise = new Promise(resolve => { release = resolve; });
  return { promise, release };
};
async function start(page) {
  await page.goto('/');
  await expect(chip(page)).toHaveText('未读 (65)');
  await expect(nav(page)).toHaveText('65');
  await expect(sourceBadge(page)).toHaveText('65');
  await expect(page.locator('#articleBodyContent')).not.toBeEmpty();
}
async function scrollPast(page, id) {
  await page.locator('#feedCardList').evaluate((list, id) => {
    const row = list.querySelector(`[data-id="${id}"]`);
    list.scrollTop += row.getBoundingClientRect().bottom - list.getBoundingClientRect().top + 2;
  }, id);
}
async function expectCounts(page, count) {
  await expect(chip(page)).toHaveText(`未读 (${count})`);
  await expect(nav(page)).toHaveText(String(count));
  await expect(sourceBadge(page)).toHaveText(String(count));
}
test.beforeEach(async ({ request }) => {
  await request.post('/__test/reset-reading');
});
test.afterEach(async ({ page }) => { await page.unrouteAll({ behavior: 'wait' }); });

test('continuous scrolling updates every count before saves finish, without duplicate writes or replacing rows', async ({ page, request }) => {
  await start(page);
  const ids = await page.locator('#feedCardList .news-card').evaluateAll(rows => rows.slice(0, 3).map(row => row.dataset.id));
  const held = gate(), writes = [];
  await page.route('**/api/news/mark-read-by-ids', async route => {
    writes.push(route.request().postDataJSON().item_ids);
    await held.promise;
    await route.continue();
  });
  await page.evaluate(() => {
    window.originalRow = document.querySelector('#feedCardList .news-card');
    window.originalSource = document.querySelector('[data-source="all"]');
    window.originalChip = document.querySelector('#feedColFeed .filter-chip.active');
  });
  try {
    for (const [index, id] of ids.entries()) {
      await scrollPast(page, id);
      await expectCounts(page, 64 - index);
      await expect(card(page, id).locator('.unread-dot')).toHaveCount(0);
      expect((await (await request.get(`/api/news/${id}/detail`)).json()).read_at).toBeNull();
    }
    expect(writes).toEqual([[ids[0]]]);
    expect(await page.evaluate(() => window.originalRow === document.querySelector('#feedCardList .news-card') &&
      window.originalSource === document.querySelector('[data-source="all"]') &&
      window.originalChip === document.querySelector('#feedColFeed .filter-chip.active'))).toBe(true);
  } finally { held.release(); }
  for (const id of ids)
    await expect.poll(async () => (await (await request.get(`/api/news/${id}/detail`)).json()).read_at).toBeTruthy();
  await expectCounts(page, 62);
  expect(writes.flat().sort()).toEqual([...ids].sort());
});

test('failed reads restore counts and unread dots while subsequent batches still save', async ({ page, request }) => {
  await start(page);
  const ids = await page.locator('#feedCardList .news-card').evaluateAll(rows => rows.slice(0, 2).map(row => row.dataset.id));
  const held = gate();
  let requests = 0;
  await page.route('**/api/news/mark-read-by-ids', async route => {
    if (++requests === 1) {
      await held.promise;
      await route.fulfill({ status: 503, json: { error: '保存失败' } });
    } else await route.continue();
  });
  try {
    await scrollPast(page, ids[0]);
    await expectCounts(page, 64);
    await scrollPast(page, ids[1]);
    await expectCounts(page, 63);
  } finally { held.release(); }
  await expect(card(page, ids[0]).locator('.unread-dot')).toHaveCount(1);
  await expect.poll(async () => (await (await request.get(`/api/news/${ids[1]}/detail`)).json()).read_at).toBeTruthy();
  await expectCounts(page, 64);
  expect((await (await request.get(`/api/news/${ids[0]}/detail`)).json()).read_at).toBeNull();
});

test('manual unread during a slow auto-read wins and does not double-adjust counts', async ({ page, request }) => {
  await start(page);
  const id = await page.locator('#feedCardList .news-card').first().getAttribute('data-id');
  const held = gate();
  await page.route('**/api/news/mark-read-by-ids', async route => {
    await held.promise;
    await route.continue();
  });
  try {
    await scrollPast(page, id);
    await expectCounts(page, 64);
    await page.keyboard.press('m');
    await expectCounts(page, 65);
    await expect(card(page, id).locator('.unread-dot')).toHaveCount(1);
  } finally { held.release(); }
  const response = await page.waitForResponse(res => res.url().endsWith(`/api/news/${id}/state`) && res.request().method() === 'PATCH');
  expect(response.ok()).toBe(true);
  await expectCounts(page, 65);
  expect((await (await request.get(`/api/news/${id}/detail`)).json()).read_at).toBeNull();
});

test('old nav, list-total and source responses cannot undo a newer read', async ({ page }) => {
  await start(page);
  const held = gate(), reached = [gate(), gate(), gate()];
  const seen = new Set();
  const intercept = index => async route => {
    if (seen.has(index)) return route.continue();
    seen.add(index);
    const response = await route.fetch();
    reached[index].release();
    await held.promise;
    await route.fulfill({ response });
  };
  await page.route('**/api/nav-summary', intercept(0));
  await page.route('**/api/news?*count_only=1*', intercept(1));
  await page.route('**/api/sources?collection=feed&read_filter=unread', intercept(2));
  try {
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await Promise.all(reached.map(item => item.promise));
    const id = await page.locator('#feedCardList .news-card').first().getAttribute('data-id');
    await scrollPast(page, id);
    await expectCounts(page, 64);
  } finally { held.release(); }
  await page.unrouteAll({ behavior: 'wait' });
  await expectCounts(page, 64);
});

test('slow source statistics do not block the nav or header count', async ({ page, request }) => {
  await start(page);
  const held = gate(), reached = gate();
  await page.route('**/api/sources?collection=feed&read_filter=unread', async route => {
    reached.release();
    await held.promise;
    await route.continue();
  });
  await request.patch('/api/news/ui-000/state', { data: { read: true } });
  try {
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await reached.promise;
    await expect(nav(page)).toHaveText('64');
    await expect(chip(page)).toHaveText('未读 (64)');
    await expect(sourceBadge(page)).toHaveText('65');
  } finally { held.release(); }
  await expectCounts(page, 64);
});

test('source-filtered reads update global and scoped counts independently and survive a filter change', async ({ page }) => {
  await start(page);
  await page.locator('[data-source="reuters"]').click();
  await expect(chip(page)).toHaveText(/未读 \(\d+\)/);
  const initial = Number((await chip(page).textContent()).match(/\d+/)[0]);
  const id = await page.locator('#feedCardList .news-card').first().getAttribute('data-id');
  const held = gate();
  await page.route('**/api/news/mark-read-by-ids', async route => {
    await held.promise;
    await route.continue();
  });
  try {
    await scrollPast(page, id);
    await expect(chip(page)).toHaveText(`未读 (${initial - 1})`);
    await expect(nav(page)).toHaveText('64');
    await expect(sourceBadge(page, 'reuters')).toHaveText(String(initial - 1));
    await page.locator('[data-source="all"]').click();
    await expect(nav(page)).toHaveText('64');
    await expect(sourceBadge(page)).toHaveText('64');
  } finally { held.release(); }
  await expectCounts(page, 64);
});

test('pagination keeps the live header count and cannot restore a stale total', async ({ page }) => {
  await start(page);
  const heldPage = gate(), heldSave = gate(), reached = gate();
  await page.route('**/api/news?*page=2*', async route => {
    const response = await route.fetch();
    reached.release();
    await heldPage.promise;
    await route.fulfill({ response });
  });
  await page.route('**/api/news/mark-read-by-ids', async route => {
    await heldSave.promise;
    await route.continue();
  });
  try {
    // Request another page without scrolling over the preceding articles.
    await page.locator('#loadMoreBtn').evaluate(button => button.click());
    await reached.promise;
    await expectCounts(page, 65);
    const id = await page.locator('#feedCardList .news-card').first().getAttribute('data-id');
    await scrollPast(page, id);
    await expectCounts(page, 64);
    heldPage.release();
    await expect(page.locator('#feedCardList .news-card')).toHaveCount(60);
    await expectCounts(page, 64);
    await expect(card(page, id).locator('.unread-dot')).toHaveCount(0);
  } finally {
    heldPage.release();
    heldSave.release();
  }
  await expectCounts(page, 64);
});
