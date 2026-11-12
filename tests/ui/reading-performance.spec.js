import { test, expect } from '@playwright/test';

const gate = () => {
  let release;
  const promise = new Promise(resolve => { release = resolve; });
  return { promise, release };
};

test('first page precedes auxiliary statistics and loads no editor or settings catalogs', async ({ page }) => {
  const held = gate(), reached = gate(), requests = [];
  page.on('request', request => requests.push(new URL(request.url())));
  await page.route('**/api/news?*', async route => {
    if (new URL(route.request().url()).searchParams.has('count_only')) return route.continue();
    const response = await route.fetch();
    reached.release();
    await held.promise;
    await route.fulfill({ response });
  });
  try {
    await page.goto('/');
    await reached.promise;
    expect(requests.filter(url => ['/api/settings', '/api/news-summaries', '/api/nav-summary', '/api/sources'].includes(url.pathname))).toEqual([]);
    expect(requests.some(url => url.pathname.endsWith('/idea-markdown-editor.js'))).toBe(false);
  } finally { held.release(); }
  await expect(page.locator('#feedCardList .news-card[data-id]').first()).toBeVisible();
  await expect(page.locator('[data-source="all"] .nav-badge')).toBeVisible();
  expect(requests.filter(url => url.pathname === '/api/news' && url.searchParams.has('count_only') && !url.searchParams.has('after_batch'))).toEqual([]);
  expect(requests.filter(url => url.pathname === '/api/sources')).toHaveLength(1);
  await page.unrouteAll({ behavior: 'wait' });
});

test('returning to a saved collection shows its preview then reconciles cross-device changes', async ({ page, request }) => {
  await request.patch('/api/news/ui-004/state', { data: { favorite: true } });
  await page.goto('/');
  await page.locator('#navFavoritesBtn').click();
  const card = page.locator('#feedCardList [data-id="ui-004"]');
  await expect(card).toBeVisible();
  await page.locator('#navImportantBtn').click();
  await expect(page.locator('#feedColFeed [data-filter="all"].active')).toBeVisible();
  const held = gate(), reached = gate();
  await request.patch('/api/news/ui-004/state', { data: { favorite: false } });
  await page.route('**/api/news?*collection=favorites*', async route => {
    const response = await route.fetch();
    reached.release();
    await held.promise;
    await route.fulfill({ response });
  });
  try {
    await page.locator('#navFavoritesBtn').click();
    await reached.promise;
    await expect(card).toBeVisible();
  } finally { held.release(); }
  await expect(card).toHaveCount(0);
  await page.unrouteAll({ behavior: 'wait' });
  await request.patch('/api/news/ui-004/state', { data: { favorite: true } });
});

test('idea search reaches beyond the first page and only loads the editor on demand', async ({ page, request }) => {
  const created = [];
  try {
    for (let i = 0; i < 35; i++) {
      const data = await (await request.post('/api/standalone-ideas', { data: { title: `性能分页 ${i}`, note: `隔离想法正文 ${i}` } })).json();
      created.push(data.idea);
    }
    await page.goto('/');
    await page.locator('#navNotesBtn').click();
    await page.locator('[data-idea-type="standalone"]').click();
    await expect(page.locator('#ideasCardList [data-idea-id]')).toHaveCount(30);
    const shown = await page.locator('#ideasCardList [data-idea-id]').evaluateAll(nodes => nodes.map(node => node.dataset.ideaId));
    const unseen = created.find(idea => !shown.includes(idea.idea_id));
    await page.locator('#ideasSearchInput').fill(unseen.title);
    await expect(page.locator(`#ideasCardList [data-idea-id="${unseen.idea_id}"]`)).toBeVisible();
    await page.locator(`#ideasCardList [data-idea-id="${unseen.idea_id}"]`).click();
    await expect(page.locator('#ideaEditorMount .ProseMirror')).toContainText(unseen.note);
  } finally {
    for (const idea of created) await request.delete(`/api/standalone-ideas/${idea.standalone_id}`);
  }
});
