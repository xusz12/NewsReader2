import { test, expect } from '@playwright/test';

const detail = async (request, id) => (await request.get(`/api/news/${id}/detail`)).json();
const card = (page, id = 'ui-064') => page.locator(`#feedCardList [data-id="${id}"]`);
const later = (page, id = 'ui-064') => card(page, id).locator('[data-state="read_later"]');
async function start(page) {
  await page.goto('/');
  // Explicitly queued articles are now read in the feed. Use all news to keep
  // the same fixture available while testing its independent saved state.
  await page.locator('#feedColFeed [data-filter="all"]').click();
  await showOldestFirst(page);
  await expect(card(page)).toBeVisible();
  await card(page).locator('.news-card-title').click();
  await expect(page.locator('#articleMainTitle')).toBeVisible();
}
async function showOldestFirst(page) {
  // Completed/all lists now start newest first; these state tests use the oldest fixtures.
  const sort = page.locator('#sortNewsBtn');
  await expect(sort).toHaveAttribute('aria-label', '排序：新到旧，点击切换');
  await sort.click();
  await expect(sort).toHaveAttribute('aria-label', '排序：旧到新，点击切换');
}
test.beforeEach(async ({ request }) => {
  await request.post('/__test/reset-reading');
  await request.post('/api/news/clear-read-later', { data: {} });
});
test.afterEach(async ({ page }) => {
  // Finish in-flight mocked responses before Playwright disposes the context.
  await page.unrouteAll({ behavior: 'wait' });
});

test('adding from unread feed updates the card and counts without completing saved reading', async ({ page, request }) => {
  await page.goto('/');
  await expect(card(page)).toBeVisible();
  await expect(later(page)).toHaveText('已看完');
  const before = (await (await request.get('/api/nav-summary')).json()).summary.feed_unread;
  await expect(page.locator('#unreadBadge')).toHaveText(String(before));
  const position = await page.locator('#feedCardList').evaluate(node => node.scrollTop);
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  await page.route('**/api/news/ui-064/state', async route => {
    expect(route.request().postDataJSON()).toEqual({ read: true, read_later: true });
    await gate;
    await route.continue();
  });
  await later(page).click();
  try {
    await expect(later(page)).toHaveAttribute('aria-busy', 'true');
    await expect(card(page)).toHaveClass(/\bread\b/);
    await expect(card(page).locator('.unread-dot')).toHaveCount(0);
    await expect(page.locator('#unreadBadge')).toHaveText(String(before - 1));
    await expect(page.locator('#feedColFeed [data-filter="unread"]')).toHaveText(`未读 (${before - 1})`);
  } finally { release(); }
  await expect(later(page)).toHaveAttribute('aria-busy', 'false');
  const saved = await detail(request, 'ui-064');
  expect(saved.read_at).toBeTruthy();
  expect(saved.read_later_at).toBeTruthy();
  expect(saved.read_later_done_at).toBeNull();
  expect(await page.locator('#feedCardList').evaluate(node => node.scrollTop)).toBe(position);
  await page.locator('#navReadLaterBtn').click();
  await expect(later(page)).toHaveText('全文待看');
});

for (const explicit of [false, true]) {
  for (const action of ['button', 'card', 'enter', 's']) {
    test(`cached article completes via ${action} in read later (explicit flag: ${explicit})`, async ({ page, request }) => {
      await request.patch('/api/news/ui-064/state', { data: { read_later: explicit } });
      await start(page);
      // Opening in the news feed, and merely switching collections, leave it pending.
      await card(page).locator('.news-card-title').click();
      await expect(page.locator('#articleBodyContent')).not.toBeEmpty();
      await expect(later(page)).toHaveText('全文待看');
      await expect(later(page)).toHaveAttribute('aria-pressed', 'true');
      await expect(page.locator('#readLaterNavBadge')).toHaveText('1');
      await page.locator('#navReadLaterBtn').click();
      await expect(card(page)).toBeVisible();
      await expect(page.locator('#feedColFeed [data-filter="unread"]')).toHaveText('待阅读 (1)');
      await expect(later(page)).toHaveText('全文待看');
      expect((await detail(request, 'ui-064')).read_later_done_at).toBeNull();
      if (action === 'button') await later(page).click();
      else if (action === 'card') await card(page).locator('.news-card-title').click();
      else if (action === 'enter') await card(page).press('Enter');
      else await page.keyboard.press(action);
      await expect(later(page)).toHaveText('已看完');
      await expect(later(page)).toHaveAttribute('aria-busy', 'false');
      const completed = await detail(request, 'ui-064');
      expect(completed.read_later_done_at).toBeTruthy();
      if (explicit) expect(completed.read_at).toBeTruthy();
      else expect(completed.read_at).toBeNull();
      await expect(page.locator('#readLaterNavBadge')).toBeHidden();
      await page.locator('#feedColFeed [data-filter="read"]').click();
      await showOldestFirst(page);
      await expect(card(page)).toBeVisible();
      await start(page);
      await expect(later(page)).toHaveText('已看完');
      await later(page).click();
      await expect(later(page)).toHaveText('全文待看');
      await expect(later(page)).toHaveAttribute('aria-busy', 'false');
      await expect(page.locator('#readLaterNavBadge')).toHaveText('1');
      await page.locator('#navReadLaterBtn').click();
      await expect(card(page)).toBeVisible();
    });
  }
}

test('opening only completes after the body loads successfully; retry preserves the opening intent', async ({ page, request }) => {
  await request.patch('/api/news/ui-064/state', { data: { read_later: false } });
  await start(page);
  await page.locator('#navReadLaterBtn').click();
  await page.route('**/api/news/ui-064/detail', route => route.fulfill({ status: 500, json: { error: '正文读取失败' } }));
  await card(page).locator('.news-card-title').click();
  await expect(page.locator('#retryReaderBtn')).toBeVisible();
  await expect(later(page)).toHaveText('全文待看');
  expect((await detail(request, 'ui-064')).read_later_done_at).toBeNull();
  await page.unroute('**/api/news/ui-064/detail');
  await page.locator('#retryReaderBtn').click();
  await expect(page.locator('#articleBodyContent')).not.toBeEmpty();
  await expect(later(page)).toHaveText('已看完');
  await expect(later(page)).toHaveAttribute('aria-busy', 'false');
  expect((await detail(request, 'ui-064')).read_later_done_at).toBeTruthy();
});

// Simulate only the fetch pipeline; article state is still saved by the real API.
async function pipeline(page, initial) {
  let status = initial;
  const sent = [];
  const patch = data => {
    data.detail_status = status;
    data.detail_ready = status === 'success';
    if (status !== 'success') Object.assign(data, { detail: null, ai: null, ai_status: 'missing' });
    return data;
  };
  await page.route('**/api/news?*', async route => {
    const data = await (await route.fetch()).json();
    const item = data.items?.find(row => row.id === 'ui-064');
    if (item) patch(item);
    await route.fulfill({ json: data });
  });
  await page.route('**/api/news/ui-064/detail', async route => route.fulfill({ json: patch(await (await route.fetch()).json()) }));
  await page.route('**/api/news/status?*', async route => {
    const data = await (await route.fetch()).json();
    const item = data.items.find(row => row.id === 'ui-064');
    if (item) patch(item);
    await route.fulfill({ json: data });
  });
  await page.route('**/api/news/ui-064/state', async route => {
    const body = route.request().postDataJSON();
    sent.push(body);
    if (body.read_later === false && status === 'pending') status = 'canceled';
    else if (body.read_later && ['canceled', 'failed'].includes(status)) status = 'pending';
    await route.fulfill({ json: patch(await (await route.fetch()).json()) });
  });
  return { sent, setStatus: value => { status = value; } };
}

test('queued fetch cancels to the initial button and can be queued again', async ({ page, request }) => {
  await request.patch('/api/news/ui-064/state', { data: { read_later: true } });
  const flow = await pipeline(page, 'pending');
  await start(page);
  await page.locator('#navReadLaterBtn').click();
  await card(page).locator('.news-card-title').click();
  await expect(later(page)).toHaveText('排队中');
  expect(flow.sent).toEqual([]);
  await later(page).click();
  await expect(later(page)).toHaveText('稍后再看');
  await expect(later(page)).toHaveAttribute('aria-busy', 'false');
  await expect(later(page)).toHaveAttribute('aria-pressed', 'false');
  expect(flow.sent).toEqual([{ read_later: false }]);
  expect((await detail(request, 'ui-064')).read_later_done_at).toBeNull();
  await later(page).click();
  await expect(later(page)).toHaveText('排队中');
  await expect(later(page)).toHaveAttribute('aria-busy', 'false');
  expect(flow.sent).toEqual([{ read_later: false }, { read_later: true }]);
});

test('running fetch cannot be toggled out and becomes ready even when its article is open', async ({ page, request }) => {
  await page.clock.install();
  await request.patch('/api/news/ui-064/state', { data: { read_later: true } });
  const flow = await pipeline(page, 'running');
  await start(page);
  await page.locator('#navReadLaterBtn').click();
  await card(page).locator('.news-card-title').click();
  await expect(later(page)).toHaveText('抓取中');
  await later(page).click();
  await expect(page.locator('#toast')).toContainText('完成后会进入全文待看');
  await expect(later(page)).toHaveAttribute('aria-pressed', 'true');
  expect(flow.sent).toEqual([]);
  flow.setStatus('success');
  await page.clock.fastForward(3100);
  await expect(later(page)).toHaveText('全文待看');
  await expect(page.locator('#articleBodyContent')).not.toBeEmpty();
  expect(flow.sent).toEqual([]);
  expect((await detail(request, 'ui-064')).read_later_done_at).toBeNull();
  await card(page).locator('.news-card-title').click();
  await expect(later(page)).toHaveText('已看完');
  await expect(later(page)).toHaveAttribute('aria-busy', 'false');
  expect(flow.sent).toEqual([{ read_later: false, read_later_done: true }]);
});

for (const explicit of [false, true]) {
  test(`failed fetch always retries instead of toggling membership (explicit flag: ${explicit})`, async ({ page, request }) => {
    await request.patch('/api/news/ui-064/state', { data: { read_later: explicit } });
    const flow = await pipeline(page, 'failed');
    await start(page);
    await expect(later(page)).toHaveText('抓取失败');
    await later(page).click();
    await expect(later(page)).toHaveText('排队中');
    await expect(later(page)).toHaveAttribute('aria-busy', 'false');
    expect(flow.sent).toEqual([explicit ? { read_later: true } : { read: true, read_later: true }]);
    // Saving the flag finishes before the reader's follow-up detail response.
    await expect(page.locator('#detailPanelFeed .cache-label')).toHaveText('等待抓取正文');
  });
}

test('failed completion restores ready state and retry persists it', async ({ page, request }) => {
  await request.patch('/api/news/ui-064/state', { data: { read_later: false } });
  await start(page);
  await page.locator('#navReadLaterBtn').click();
  await page.route('**/api/news/ui-064/state', route => route.fulfill({ status: 500, json: { error: '保存失败' } }));
  await card(page).locator('.news-card-title').click();
  await expect(page.locator('#toast')).toContainText('保存失败');
  await expect(later(page)).toHaveText('全文待看');
  await expect(later(page)).toHaveAttribute('aria-pressed', 'true');
  expect((await detail(request, 'ui-064')).read_later_done_at).toBeNull();
  await page.unroute('**/api/news/ui-064/state');
  await later(page).click();
  await expect(later(page)).toHaveText('已看完');
  await expect(later(page)).toHaveAttribute('aria-busy', 'false');
  expect((await detail(request, 'ui-064')).read_later_done_at).toBeTruthy();
});

test('search and bulk completion include cached articles without a queue flag', async ({ page, request }) => {
  await request.patch('/api/news/ui-064/state', { data: { read_later: false } });
  await start(page);
  await page.locator('#navReadLaterBtn').click();
  await page.locator('#feedSearchInput').fill('历史 64');
  await expect(page.locator('#feedColFeed [data-filter="unread"]')).toHaveText('待阅读 (1)');
  await expect(card(page)).toBeVisible();
  await page.locator('#markAllReadBtn').click();
  await expect(card(page)).toHaveCount(0);
  expect((await detail(request, 'ui-064')).read_later_done_at).toBeTruthy();
  await expect(page.locator('#readLaterNavBadge')).toBeHidden();
});

for (const leave of ['settings', 'hidden', 'mobile back', 'leave and return']) {
  test(`late body does not complete a card after ${leave}`, async ({ page, request }) => {
    await request.patch('/api/news/ui-064/state', { data: { read_later: true } });
    if (leave === 'mobile back') await page.setViewportSize({ width: 360, height: 800 });
    await page.goto('/');
    await expect(page.locator('#feedCardList .news-card').first()).toBeVisible();
    if (leave === 'mobile back') await page.locator('.mobile-bottom-nav [data-collection="read_later"]').click();
    else {
      await expect(page.locator('#articleBodyContent')).not.toBeEmpty();
      await page.locator('#navReadLaterBtn').click();
    }
    let release, captured;
    const gate = new Promise(resolve => { release = resolve; });
    const started = new Promise(resolve => { captured = resolve; });
    await page.route('**/api/news/ui-064/detail', async route => {
      const response = await route.fetch();
      captured();
      await gate;
      await route.fulfill({ response });
    });
    await card(page).locator('.news-card-title').click();
    await started;
    if (leave === 'settings') await page.locator('#leftNavSettingsBtn').click();
    else if (leave === 'hidden') await page.evaluate(() => Object.defineProperty(document, 'hidden', { configurable: true, value: true }));
    else if (leave === 'mobile back') await page.locator('#mobileReaderBackBtn').click();
    else {
      await page.locator('#navFeedBtn').click();
      await page.locator('#navReadLaterBtn').click();
      await expect(card(page)).toBeVisible();
    }
    release();
    await expect(page.locator('#articleBodyContent')).not.toBeEmpty();
    await expect(later(page)).toHaveText('全文待看');
    expect((await detail(request, 'ui-064')).read_later_done_at).toBeNull();
  });
}

test('failed search bulk completion reports failure and keeps unfinished articles', async ({ page, request }) => {
  await request.patch('/api/news/ui-064/state', { data: { read_later: false } });
  await start(page);
  await page.locator('#navReadLaterBtn').click();
  await page.locator('#feedSearchInput').fill('历史 64');
  await expect(page.locator('#feedColFeed [data-filter="unread"]')).toHaveText('待阅读 (1)');
  await page.route('**/api/news/ui-064/state', route => route.fulfill({ status: 500, json: { error: '保存失败' } }));
  await page.locator('#markAllReadBtn').click();
  await expect(page.locator('#markAllReadBtn')).toBeEnabled();
  await expect(page.locator('#toast')).toContainText('1 篇未能标为已看完');
  await expect(later(page)).toHaveText('全文待看');
  expect((await detail(request, 'ui-064')).read_later_done_at).toBeNull();
});

test('bulk completion waits for a previous re-add to save', async ({ page, request }) => {
  await start(page);
  await page.locator('#navReadLaterBtn').click();
  await page.locator('#feedColFeed [data-filter="all"]').click();
  await showOldestFirst(page);
  await expect(later(page)).toHaveText('已看完');
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const order = [];
  await page.route('**/api/news/ui-064/state', async route => {
    await gate;
    const response = await route.fetch();
    order.push('saved');
    await route.fulfill({ response });
  });
  await page.route('**/api/news/clear-read-later', async route => {
    order.push('bulk');
    await route.continue();
  });
  await later(page).click();
  await expect(later(page)).toHaveAttribute('aria-busy', 'true');
  await page.locator('#markAllReadBtn').click();
  await expect(page.locator('#markAllReadBtn')).toBeDisabled();
  release();
  await expect(page.locator('#markAllReadBtn')).toBeEnabled();
  await expect(later(page)).toHaveText('已看完');
  expect(order).toEqual(['saved', 'bulk']);
  expect((await detail(request, 'ui-064')).read_later_done_at).toBeTruthy();
});

test('read-later preserves feed read flags, card appearance, keyboard completion and source scope', async ({ page, request }) => {
  await page.clock.install();
  await request.patch('/api/news/ui-064/state', { data: { read_later: true, read: true } });
  await request.patch('/api/news/ui-063/state', { data: { read_later: true, read: false } });
  const seenAt = (await detail(request, 'ui-064')).read_at;
  await page.goto('/');
  await expect(card(page, 'ui-063')).toBeVisible();
  await page.locator('#navReadLaterBtn').click();
  await expect(page.locator('#feedColFeed [data-filter="unread"]')).toHaveText('待阅读 (2)');
  await expect(page.locator('#feedCardList .news-card.read, #feedCardList .unread-dot')).toHaveCount(0);
  await page.locator('#feedCardList').evaluate(node => { node.scrollTop = node.scrollHeight; });
  await page.clock.fastForward(6000);
  expect((await detail(request, 'ui-063')).read_at).toBeNull();
  await card(page).locator('.news-card-title').click();
  await expect(later(page)).toHaveText('已看完');
  await expect(later(page)).toHaveAttribute('aria-busy', 'false');
  expect((await detail(request, 'ui-064')).read_at).toBe(seenAt);
  await expect(card(page)).toHaveCSS('opacity', '1');
  await page.keyboard.press('d');
  await expect(later(page)).toHaveText('全文待看');
  await expect(later(page)).toHaveAttribute('aria-busy', 'false');
  await page.keyboard.press('m');
  await expect(later(page)).toHaveText('已看完');
  await expect(later(page)).toHaveAttribute('aria-busy', 'false');
  expect((await detail(request, 'ui-064')).read_at).toBe(seenAt);
  expect((await detail(request, 'ui-063')).read_later_at).toBeTruthy();
  // A selected source limits both displayed pending items and bulk completion.
  await page.locator('#feedColFeed [data-filter="read"]').click();
  await showOldestFirst(page);
  await expect(card(page)).toBeVisible();
  await later(page).click();
  await expect(later(page)).toHaveAttribute('aria-busy', 'false');
  await page.locator('#navReadLaterBtn').click();
  const sources = (await (await request.get('/api/sources?collection=read_later&read_filter=unread')).json()).sources;
  expect(sources).toHaveLength(2);
  await page.locator(`#sourceFilters [data-source="${sources[0].key}"]`).click();
  await expect(page.locator('#feedCardList .news-card')).toHaveCount(1);
  const scoped = await page.locator('#feedCardList .news-card').getAttribute('data-id');
  await page.locator('#markAllReadBtn').click();
  await expect(page.locator('#feedCardList .news-card')).toHaveCount(0);
  expect((await detail(request, scoped)).read_later_done_at).toBeTruthy();
  expect((await detail(request, scoped === 'ui-064' ? 'ui-063' : 'ui-064')).read_later_at).toBeTruthy();
});

test('delayed unopened-card status cannot undo bulk completion after the list reloads', async ({ page, request }) => {
  await start(page);
  await page.locator('#navReadLaterBtn').click();
  await page.locator('#feedColFeed [data-filter="all"]').click();
  await showOldestFirst(page);
  await expect(later(page, 'ui-063')).toHaveText('已看完');
  let release, captured;
  const gate = new Promise(resolve => { release = resolve; });
  const started = new Promise(resolve => { captured = resolve; });
  await page.route('**/api/news/status?ids=ui-063', async route => {
    const response = await route.fetch();
    captured();
    await gate;
    await route.fulfill({ response });
  });
  await later(page, 'ui-063').click();
  await expect(later(page, 'ui-063')).toHaveAttribute('aria-busy', 'false');
  await started;
  await page.locator('#markAllReadBtn').click();
  await expect(page.locator('#markAllReadBtn')).toBeEnabled();
  await expect(later(page, 'ui-063')).toHaveText('已看完');
  const lateResponse = page.waitForResponse('**/api/news/status?ids=ui-063');
  release();
  await (await lateResponse).finished();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await expect(later(page, 'ui-063')).toHaveText('已看完');
  expect((await detail(request, 'ui-063')).read_later_done_at).toBeTruthy();
});
