import { test, expect } from '@playwright/test';

test.beforeEach(async ({ request }) => {
  await request.post('/__test/reset-reading');
  await request.post('/api/news/clear-read-later', { data: {} });
});

const detail = async (request, id) => (await request.get(`/api/news/${id}/detail`)).json();
const card = (page, id) => page.locator(`#feedCardList [data-id="${id}"]`);

async function openQueue(page) {
  await page.goto('/');
  await expect(page.locator('#feedCardList .news-card').first()).toBeVisible();
  await page.locator('#navReadLaterBtn').click();
  await expect(page.locator('#feedColFeed [data-filter="unread"]')).toHaveAttribute('aria-pressed', 'true');
}

test('read-later defaults to pending and opening full text only completes that queue', async ({ page, request }) => {
  await request.patch('/api/news/ui-064/state', { data: { read_later: true, read: true } });
  await request.patch('/api/news/ui-063/state', { data: { read_later: true, read: false } });
  const seenAt = (await detail(request, 'ui-064')).read_at;
  await openQueue(page);
  await expect(page.locator('#feedCardList .news-card')).toHaveCount(2);
  await expect(page.locator('#feedCardList .news-card.read, #feedCardList .unread-dot')).toHaveCount(0);
  for (const id of ['ui-064', 'ui-063']) {
    await expect(card(page, id)).toHaveCSS('opacity', '1');
    await expect(card(page, id).locator('[data-state="read_later"]')).toHaveText('全文待看');
    expect((await detail(request, id)).read_later_at).toBeTruthy();
  }
  await page.locator('#feedCardList').evaluate(node => { node.scrollTop = node.scrollHeight; });
  expect((await detail(request, 'ui-063')).read_at).toBeNull();
  const patches = [];
  page.on('request', req => { if (req.method() === 'PATCH' && req.url().endsWith('/state')) patches.push(req.postDataJSON()); });
  await card(page, 'ui-064').locator('.news-card-title').click();
  await expect(card(page, 'ui-064').locator('[data-state="read_later"]')).toHaveText('已看完');
  await expect(card(page, 'ui-064')).toHaveCSS('opacity', '1');
  expect((await detail(request, 'ui-064')).read_at).toBe(seenAt);
  expect((await detail(request, 'ui-063')).read_later_at).toBeTruthy();
  await card(page, 'ui-063').locator('.news-card-title').click();
  await expect(card(page, 'ui-063').locator('[data-state="read_later"]')).toHaveText('已看完');
  const completed = await detail(request, 'ui-063');
  expect(completed.read_later_at).toBeNull();
  expect(completed.read_later_done_at).toBeTruthy();
  expect(completed.read_at).toBeNull();
  expect(patches).toEqual([{ read_later: false }, { read_later: false }]);
  await expect(page.locator('#readLaterNavBadge')).toBeHidden();
  await page.locator('#feedColFeed [data-filter="read"]').click();
  await expect(card(page, 'ui-063')).toBeVisible();
  await expect(page.locator('#feedCardList .news-card.read')).toHaveCount(0);
  await page.locator('#navFeedBtn').click();
  await expect(card(page, 'ui-063').locator('.unread-dot')).toBeVisible();
  await page.locator('#navReadLaterBtn').click();
  await expect(page.locator('#feedColFeed [data-filter="unread"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#feedCardList .news-card')).toHaveCount(0);
});

test('opening pending full text waits for readable content before completing', async ({ page, request }) => {
  await request.patch('/api/news/ui-064/state', { data: { read_later: true } });
  let ready = false;
  await page.route('**/api/news/ui-064/detail', async route => {
    const data = await (await route.fetch()).json();
    if (!ready) Object.assign(data, { detail: null, ai: null, detail_status: 'running', ai_status: 'none' });
    await route.fulfill({ json: data });
  });
  await openQueue(page);
  await card(page, 'ui-064').locator('.news-card-title').click();
  await expect(page.locator('#detailPanelFeed .cache-label')).toHaveText('正文抓取中…');
  expect((await detail(request, 'ui-064')).read_later_at).toBeTruthy();
  ready = true;
  await expect(card(page, 'ui-064').locator('[data-state="read_later"]')).toHaveText('已看完', { timeout: 10000 });
  await expect(page.locator('#articleBodyContent')).not.toBeEmpty();
  expect((await detail(request, 'ui-064')).read_at).toBeNull();
});

test('a failed completion leaves pending state intact and can be retried', async ({ page, request }) => {
  await request.patch('/api/news/ui-064/state', { data: { read_later: true } });
  await openQueue(page);
  await page.route('**/api/news/ui-064/state', route => route.fulfill({ status: 500, json: { error: '保存失败' } }));
  await card(page, 'ui-064').locator('.news-card-title').click();
  await expect(page.locator('#toast')).toContainText('操作未完成');
  await expect(card(page, 'ui-064').locator('[data-state="read_later"]')).toHaveText('全文待看');
  expect((await detail(request, 'ui-064')).read_later_at).toBeTruthy();
  await page.unroute('**/api/news/ui-064/state');
  await card(page, 'ui-064').locator('.news-card-title').click();
  await expect(card(page, 'ui-064').locator('[data-state="read_later"]')).toHaveText('已看完');
  expect((await detail(request, 'ui-064')).read_at).toBeNull();
});

test('late full text after leaving read-later does not complete the article', async ({ page, request }) => {
  await request.patch('/api/news/ui-064/state', { data: { read_later: true } });
  await openQueue(page);
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  await page.route('**/api/news/ui-064/detail', async route => {
    const response = await route.fetch();
    await gate;
    await route.fulfill({ response });
  });
  await card(page, 'ui-064').locator('.news-card-title').click();
  await page.locator('#navFeedBtn').click();
  release();
  await expect(page.locator('#articleBodyContent')).not.toBeEmpty();
  expect((await detail(request, 'ui-064')).read_later_at).toBeTruthy();
  expect((await detail(request, 'ui-064')).read_later_done_at).toBeNull();
});
