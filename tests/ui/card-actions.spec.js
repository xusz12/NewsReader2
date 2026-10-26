import { test, expect } from '@playwright/test';

const row = page => page.locator('#feedCardList [data-id="ui-064"]');
const button = (page, flag) => row(page).locator(`[data-state="${flag}"]`);
const detail = async request => (await request.get('/api/news/ui-064/detail')).json();
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
};
async function start(page) {
  await page.goto('/');
  await expect(row(page)).toBeVisible();
  await expect(page.locator('#articleBodyContent')).not.toBeEmpty();
}
test.beforeEach(async ({ request }) => {
  await request.post('/__test/reset-reading');
  await request.patch('/api/news/ui-064/state', { data: { important: false, favorite: false, read_later: false, read_later_done: true } });
});

test('arrow keys share WSAD actions and respect inputs and dialogs', async ({ page, request }) => {
  await start(page);
  const selected = page.locator('#feedCardList [aria-current="true"]');
  const secondId = await page.locator('#feedCardList [data-id]').nth(1).getAttribute('data-id');
  for (const [key, id] of [['ArrowDown', secondId], ['w', 'ui-064'], ['s', secondId], ['ArrowUp', 'ui-064']]) {
    await page.keyboard.press(key);
    await expect(selected).toHaveAttribute('data-id', id);
  }
  for (const [key, flag, active] of [
    ['ArrowLeft', 'important', true], ['a', 'important', false],
    ['ArrowRight', 'read_later', true], ['d', 'read_later', false],
  ]) {
    await page.keyboard.press(key);
    await expect(button(page, flag)).toHaveAttribute('aria-pressed', String(active));
    await expect(button(page, flag)).toHaveAttribute('aria-busy', 'false');
    expect(Boolean((await detail(request))[`${flag}_at`])).toBe(active);
  }
  await page.locator('#feedSearchInput').focus();
  for (const key of ['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight']) await page.keyboard.press(key);
  await expect(page.locator('#feedSearchInput')).toBeFocused();
  await expect(selected).toHaveAttribute('data-id', 'ui-064');
  for (const flag of ['important', 'read_later']) await expect(button(page, flag)).toHaveAttribute('aria-pressed', 'false');
  await page.locator('#feedSearchInput').blur();
  await page.keyboard.press('?');
  for (const key of ['ArrowDown', 'ArrowLeft', 'ArrowRight']) await page.keyboard.press(key);
  await expect(selected).toHaveAttribute('data-id', 'ui-064');
  for (const flag of ['important', 'read_later']) await expect(button(page, flag)).toHaveAttribute('aria-pressed', 'false');
  for (const key of ['↑', '↓', '←', '→']) await expect(page.locator('#shortcutsModal kbd').filter({ hasText: key })).toBeVisible();
  await page.keyboard.press('Escape');
});

test('all three buttons respond before a slow save, keep their DOM and save every flag', async ({ page, request }) => {
  await start(page);
  const gate = deferred(), bodies = [];
  await page.route('**/api/news/ui-064/state', async route => {
    bodies.push(route.request().postDataJSON());
    await gate.promise;
    await route.continue();
  });
  await row(page).evaluate(node => { window.originalCard = node; });
  for (const flag of ['important', 'read_later', 'favorite']) {
    await button(page, flag).click();
    await expect(button(page, flag)).toHaveAttribute('aria-pressed', 'true');
    await expect(button(page, flag)).toHaveAttribute('aria-busy', 'true');
    await expect(button(page, flag)).toBeEnabled();
  }
  expect(bodies).toEqual([{ important: true }]);
  expect(await row(page).evaluate(node => node === window.originalCard)).toBe(true);
  expect((await detail(request)).important_at).toBeNull();
  gate.resolve();
  for (const flag of ['important', 'read_later', 'favorite'])
    await expect(button(page, flag)).toHaveAttribute('aria-busy', 'false');
  const saved = await detail(request);
  expect(saved.important_at && saved.favorite_at && saved.read_later_at && saved.read_at).toBeTruthy();
  expect(saved.read_later_done_at).toBeNull();
  expect(bodies).toEqual([{ important: true }, { read: true, read_later: true, favorite: true }]);
  expect(await row(page).evaluate(node => node === window.originalCard)).toBe(true);
});

test('double click retains the final intent while the first response is delayed', async ({ page, request }) => {
  await start(page);
  const gate = deferred(), bodies = [];
  await page.route('**/api/news/ui-064/state', async route => {
    bodies.push(route.request().postDataJSON());
    if (bodies.length === 1) await gate.promise;
    await route.continue();
  });
  await button(page, 'favorite').click();
  await button(page, 'favorite').click();
  await expect(button(page, 'favorite')).toHaveAttribute('aria-pressed', 'false');
  gate.resolve();
  await expect(button(page, 'favorite')).toHaveAttribute('aria-busy', 'false');
  expect(bodies).toEqual([{ favorite: true }, { favorite: false }]);
  expect((await detail(request)).favorite_at).toBeNull();
});

for (const flag of ['important', 'read_later', 'favorite']) {
  test(`${flag} rolls back after a failed save and can be retried`, async ({ page, request }) => {
    await start(page);
    const gate = deferred();
    await page.route('**/api/news/ui-064/state', async route => {
      await gate.promise;
      await route.fulfill({ status: 500, json: { error: '保存失败' } });
    });
    await button(page, flag).click();
    await expect(button(page, flag)).toHaveAttribute('aria-pressed', 'true');
    gate.resolve();
    await expect(page.locator('#toast')).toContainText('保存失败');
    await expect(button(page, flag)).toHaveAttribute('aria-pressed', 'false');
    await expect(button(page, flag)).toHaveAttribute('aria-busy', 'false');
    expect((await detail(request))[`${flag}_at`]).toBeNull();
    await page.unroute('**/api/news/ui-064/state');
    await button(page, flag).click();
    await expect(button(page, flag)).toHaveAttribute('aria-busy', 'false');
    expect((await detail(request))[`${flag}_at`]).toBeTruthy();
  });
}

test('a slow article refresh does not block another read-later click or other buttons', async ({ page, request }) => {
  await start(page);
  const gate = deferred();
  let refreshes = 0;
  await page.route('**/api/news/ui-064/detail', async route => {
    const response = await route.fetch();
    refreshes++;
    await gate.promise;
    await route.fulfill({ response });
  });
  await button(page, 'read_later').click();
  await expect(button(page, 'read_later')).toHaveAttribute('aria-busy', 'false');
  await expect.poll(() => refreshes).toBe(1);
  await button(page, 'read_later').click();
  await expect(button(page, 'read_later')).toHaveAttribute('aria-busy', 'false');
  await expect.poll(() => refreshes).toBe(2);
  await button(page, 'important').click();
  await expect(button(page, 'important')).toHaveAttribute('aria-busy', 'false');
  const saved = await detail(request);
  expect(saved.read_later_at).toBeNull(); expect(saved.important_at).toBeTruthy();
  gate.resolve();
  await expect(button(page, 'read_later')).toHaveAttribute('aria-pressed', 'false');
  await expect(button(page, 'important')).toHaveAttribute('aria-pressed', 'true');
});

test('a detail snapshot taken before the click cannot overwrite the saved flag', async ({ page }) => {
  const gate = deferred(), captured = deferred();
  await page.route('**/api/news/ui-064/detail', async route => {
    const response = await route.fetch();
    captured.resolve();
    await gate.promise;
    await route.fulfill({ response });
  });
  await page.goto('/');
  await captured.promise;
  await button(page, 'favorite').click();
  await expect(button(page, 'favorite')).toHaveAttribute('aria-busy', 'false');
  gate.resolve();
  await expect(page.locator('#articleBodyContent')).not.toBeEmpty();
  await expect(button(page, 'favorite')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#detailPanelFeed .detail-toolbar [data-state="favorite"]')).toHaveAttribute('aria-pressed', 'true');
});

test('saving read-later refreshes an unopened failed card using only its lightweight status', async ({ page, request }) => {
  await request.patch('/api/news/ui-063/state', { data: { read_later: false } });
  await page.route('**/api/news?*', async route => {
    const data = await (await route.fetch()).json();
    const item = data.items?.find(row => row.id === 'ui-063');
    if (item) Object.assign(item, { detail_ready: false, detail_status: 'failed' });
    await route.fulfill({ json: data });
  });
  await start(page);
  let details = 0;
  page.on('request', request => { if (request.url().endsWith('/ui-063/detail')) details++; });
  const later = page.locator('[data-id="ui-063"] [data-state="read_later"]');
  await later.click();
  await expect(later).toHaveAttribute('aria-busy', 'false');
  await expect(later).toHaveText('全文待看');
  expect(details).toBe(0);
});

test('late status polling preserves a newer read-later save without rebuilding cards', async ({ page, request }) => {
  // Keep the queued fixture in the unread feed with an explicit unread choice.
  await request.patch('/api/news/ui-063/state', { data: { read_later: true, read: false } });
  await page.clock.install();
  await page.route('**/api/news?*', async route => {
    const data = await (await route.fetch()).json();
    const item = data.items?.find(row => row.id === 'ui-063');
    if (item) Object.assign(item, { detail_ready: true, detail_status: 'running' });
    await route.fulfill({ json: data });
  });
  const gate = deferred(), captured = deferred();
  let polls = 0;
  await page.route('**/api/news/status?*', async route => {
    const response = await route.fetch();
    if (polls++) return route.fulfill({ response });
    captured.resolve();
    await gate.promise;
    await route.fulfill({ response });
  });
  await start(page);
  const later = page.locator('[data-id="ui-063"] [data-state="read_later"]');
  await later.evaluate(node => { window.originalButton = node; });
  await page.clock.fastForward(3100);
  await captured.promise;
  await later.click();
  await expect(later).toHaveAttribute('aria-pressed', 'false');
  await expect(later).toHaveAttribute('aria-busy', 'false');
  const response = page.waitForResponse('**/api/news/status?*');
  gate.resolve();
  await response;
  await expect(later).toHaveText('已看完');
  await expect(later).toHaveAttribute('aria-pressed', 'false');
  expect(await later.evaluate(node => node === window.originalButton)).toBe(true);
});

test('a late initial detail response cannot replace the ready body from a newer state refresh', async ({ page }) => {
  const gate = deferred(), captured = deferred();
  let first = true;
  await page.route('**/api/news/ui-064/detail', async route => {
    if (!first) return route.continue();
    first = false;
    const data = await (await route.fetch()).json();
    Object.assign(data, { detail: null, ai: null, detail_status: 'running', ai_status: 'none' });
    captured.resolve();
    await gate.promise;
    await route.fulfill({ json: data });
  });
  await page.goto('/');
  await captured.promise;
  await button(page, 'read_later').click();
  await expect(button(page, 'read_later')).toHaveAttribute('aria-busy', 'false');
  await expect(page.locator('#articleBodyContent')).not.toBeEmpty();
  const lateResponse = page.waitForResponse('**/api/news/ui-064/detail');
  gate.resolve();
  await (await lateResponse).finished();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await expect(page.locator('#articleBodyContent')).not.toBeEmpty();
  await expect(button(page, 'read_later')).toHaveText('全文待看');
});
