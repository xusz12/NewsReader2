import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page, request }) => {
  await request.post('/__test/reset-reading');
  await page.clock.install();
});
async function start(page) {
  await page.goto('/');
  await expect(page.locator('#feedColFeed [data-filter="unread"]')).toHaveText('未读 (65)');
}
const unreadCount = async request => (await (await request.get('/api/news?collection=feed&read_filter=unread&count_only=1')).json()).total;

test('confirmation uses fresh unread counts across pages, supports cancel and skips empty scopes', async ({ page, request }) => {
  await start(page);
  await page.locator('#feedColFeed [data-filter="all"]').click();
  await expect(page.locator('#feedColFeed [data-filter="all"]')).toHaveText('全部 (65)');
  await expect(page.locator('#feedCardList .news-card')).toHaveCount(30);
  await request.patch('/api/news/ui-000/state', { data: { read: true } });
  const readAt = (await (await request.get('/api/news/ui-000/detail')).json()).read_at;
  const writes = [];
  page.on('request', req => {
    if (req.method() === 'POST' && /\/api\/news\/mark-(all-read|read-by-ids)$/.test(new URL(req.url()).pathname))
      writes.push(req.postDataJSON());
  });
  let confirmations = 0, accept = false;
  page.on('dialog', async dialog => {
    confirmations++;
    expect(dialog.type()).toBe('confirm');
    expect(dialog.message()).toContain('全部来源的 64 条未读新闻');
    expect(dialog.message()).toContain('尚未加载');
    if (accept) await dialog.accept();
    else await dialog.dismiss();
  });
  await page.locator('#markAllReadBtn').click();
  await expect(page.locator('#markAllReadBtn')).toBeEnabled();
  expect(confirmations).toBe(1);
  expect(writes).toHaveLength(0);
  expect(await unreadCount(request)).toBe(64);

  accept = true;
  await page.locator('#markAllReadBtn').click();
  await expect(page.locator('#toast')).toHaveText('全部来源：已将 64 条新闻标为已读');
  expect(confirmations).toBe(2);
  expect(writes).toEqual([expect.objectContaining({ collection: 'feed', source_filter: 'all', read_filter: 'unread' })]);
  expect(await unreadCount(request)).toBe(0);
  expect((await (await request.get('/api/news/ui-000/detail')).json()).read_at).toBe(readAt);
  await page.locator('#markAllReadBtn').click();
  await expect(page.locator('#toast')).toHaveText('全部来源：当前范围没有未读新闻');
  expect(confirmations).toBe(2);
  expect(writes).toHaveLength(1);
});

test('confirmation includes the selected source and full-text query, and marks only matching unread IDs', async ({ page, request }) => {
  await start(page);
  await page.locator('[data-source="reuters"]').click();
  const query = 'isolated interface test fixture';
  await page.locator('#feedSearchInput').fill(query);
  await page.clock.runFor(300);
  const result = await (await request.get(`/api/search?${new URLSearchParams({
    q: query, feed_filters: '1', collection: 'feed', source_filter: 'reuters', read_filter: 'unread', per: '100',
  })}`)).json();
  expect(result.total).toBeGreaterThan(0);
  await expect(page.locator('#feedColFeed [data-filter="unread"]')).toHaveText(`未读 (${result.total})`);
  const expectedIds = result.items.map(item => item.id).sort();
  let sent;
  await page.route('**/api/news/mark-read-by-ids', async route => {
    sent = route.request().postDataJSON().item_ids;
    await route.continue();
  });
  let confirmation;
  page.once('dialog', async dialog => {
    confirmation = dialog.message();
    await dialog.accept();
  });
  await page.locator('#markAllReadBtn').click();
  await expect(page.locator('#toast')).toContainText(`已将 ${result.total} 条新闻标为已读`);
  expect(confirmation).toContain('Reuters');
  expect(confirmation).toContain(`${result.total} 条未读新闻`);
  expect(confirmation).toContain(`搜索条件：「${query}」`);
  expect(sent.sort()).toEqual(expectedIds);
  expect(await unreadCount(request)).toBe(65 - result.total);
});

test('a changed source cancels a pending count preview instead of marking the old scope', async ({ page, request }) => {
  await start(page);
  let release, reached;
  const gate = new Promise(resolve => { release = resolve; });
  const requested = new Promise(resolve => { reached = resolve; });
  await page.route('**/api/news?*count_only=1*', async route => {
    const response = await route.fetch();
    reached();
    await gate;
    await route.fulfill({ response });
  });
  let confirmations = 0;
  page.on('dialog', async dialog => { confirmations++; await dialog.dismiss(); });
  await page.locator('#markAllReadBtn').click();
  await requested;
  await page.locator('[data-source="reuters"]').click();
  release();
  await expect(page.locator('#toast')).toHaveText('新闻范围已变化，请重新点击全部已读');
  expect(confirmations).toBe(0);
  expect(await unreadCount(request)).toBe(65);
});
