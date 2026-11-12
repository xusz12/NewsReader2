import { test, expect } from '@playwright/test';
import { answerConfirm, chooseTheme, openNavigation } from './helpers.js';

const prefix = '续页验证';
const cards = page => page.locator('#ideasCardList [data-idea-id]');
const list = page => page.locator('#ideasCardList');
const ids = page => cards(page).evaluateAll(nodes => nodes.map(node => node.dataset.ideaId));
const gate = () => {
  let release;
  const promise = new Promise(resolve => { release = resolve; });
  return { promise, release };
};
let created;

test.beforeEach(async ({ request }) => {
  created = [];
  for (let i = 0; i < 67; i++) {
    const data = await (await request.post('/api/standalone-ideas', { data: {
      title: `${prefix} ${String(i).padStart(2, '0')}`, note: `分页内容 ${i}，保存后继续浏览。`,
    } })).json();
    created.push(data.idea);
  }
});
test.afterEach(async ({ request }) => {
  for (const idea of created) await request.delete(`/api/standalone-ideas/${idea.standalone_id}`);
});

async function start(page, width = 1440) {
  await page.setViewportSize({ width, height: 900 });
  await page.goto('/');
  await expect(page.locator('#feedCardList .news-card').first()).toBeVisible();
  await openNavigation(page);
  await page.locator('#navNotesBtn').click();
  const searched = page.waitForResponse(response => {
    const url = new URL(response.url());
    return url.pathname === '/api/ideas' && url.searchParams.get('q') === prefix;
  });
  await page.locator('#ideasSearchInput').fill(prefix);
  await searched;
  await expect(cards(page)).toHaveCount(30);
}
async function scrollToEnd(page) {
  await list(page).evaluate(node => { node.scrollTop = node.scrollHeight; });
}
async function position(page) {
  return list(page).evaluate(node => {
    const top = node.getBoundingClientRect().top;
    const card = [...node.querySelectorAll('[data-idea-id]')].find(card => card.getBoundingClientRect().bottom > top);
    return { id: card?.dataset.ideaId, offset: card?.getBoundingClientRect().top - top, scroll: node.scrollTop };
  });
}
async function expectPosition(page, before) {
  const after = await position(page);
  expect(after.id).toBe(before.id);
  expect(Math.abs(after.offset - before.offset)).toBeLessThan(1);
  expect(Math.abs(after.scroll - before.scroll)).toBeLessThan(1);
}

for (const width of [1440, 390]) test(`auto paging and saving keep every loaded card and position at ${width}px`, async ({ page }) => {
  await start(page, width);
  await scrollToEnd(page);
  await expect(cards(page)).toHaveCount(60);
  const loaded = await ids(page);
  await cards(page).nth(35).click();
  const requests = [];
  page.on('request', request => {
    if (new URL(request.url()).pathname === '/api/ideas') requests.push(request.url());
  });
  for (const [theme, colorScheme] of [['light', 'light'], ['dark', 'dark'], ['system', 'light'], ['system', 'dark'], ['eink', 'light']]) {
    await page.emulateMedia({ colorScheme });
    await chooseTheme(page, theme);
    for (const size of ['standard', 'extra-large']) {
      const label = await page.locator(`#fontSizeSelect-native option[value="${size}"]`).textContent();
      await page.getByRole('combobox', { name: '阅读字号', exact: true }).click();
      await page.getByRole('option', { name: label, exact: true }).click();
      const before = await position(page);
      await page.locator('#ideaNote').fill(`保存后的内容 ${theme} ${colorScheme} ${size}`);
      await page.locator('#saveIdeaBtn').click();
      await expect(page.locator('#ideaToolbarTime')).toHaveText('已保存');
      expect(await ids(page)).toEqual(loaded);
      await expectPosition(page, before);
      expect(requests).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }
  }
  if (width <= 920) {
    const before = await position(page);
    await page.locator('#mobileReaderBackBtn').click();
    await expectPosition(page, before);
  }
  await page.screenshot({ path: `.ui-artifacts/ideas-paging-${width}.png` });
  await scrollToEnd(page);
  await expect(cards(page)).toHaveCount(67);
  expect(new Set(await ids(page))).toEqual(new Set(created.map(idea => idea.idea_id)));
  await expect(page.locator('#loadMoreIdeasBtn')).toHaveCount(0);
});

test('append failure retains cards and retries the same cursor once', async ({ page }) => {
  await start(page);
  const loaded = await ids(page), requests = [];
  let fail = true;
  await page.route('**/api/ideas?*', async route => {
    const url = new URL(route.request().url());
    if (url.searchParams.get('page') !== '2') return route.continue();
    requests.push(url.searchParams.get('cursor_id'));
    if (fail) return route.fulfill({ status: 500, json: { error: 'request_failed' } });
    return route.continue();
  });
  await scrollToEnd(page);
  await expect(page.locator('#loadMoreIdeasBtn')).toHaveText('重试');
  expect(await ids(page)).toEqual(loaded);
  await list(page).evaluate(node => node.dispatchEvent(new Event('scroll')));
  expect(requests).toHaveLength(1);
  fail = false;
  await page.locator('#loadMoreIdeasBtn').click();
  await expect(cards(page)).toHaveCount(60);
  expect(requests).toEqual([loaded.at(-1), loaded.at(-1)]);
  expect((await ids(page)).slice(0, 30)).toEqual(loaded);
});

test('saving during an in-flight append keeps the page and never rolls back the saved card', async ({ page }) => {
  await start(page);
  const loaded = await ids(page);
  await cards(page).nth(12).click();
  const held = gate(), reached = gate();
  let appends = 0;
  await page.route('**/api/ideas?*', async route => {
    if (new URL(route.request().url()).searchParams.get('page') !== '2') return route.continue();
    appends++;
    const response = await route.fetch();
    reached.release();
    await held.promise;
    await route.fulfill({ response });
  });
  try {
    await scrollToEnd(page);
    await reached.promise;
    await list(page).evaluate(node => {
      for (let i = 0; i < 5; i++) node.dispatchEvent(new Event('scroll'));
    });
    await page.locator('#ideaNote').fill('续页期间保存的新内容');
    await page.locator('#saveIdeaBtn').click();
    await expect(page.locator('#ideaToolbarTime')).toHaveText('已保存');
    expect(await ids(page)).toEqual(loaded);
    expect(appends).toBe(1);
  } finally { held.release(); }
  await expect(cards(page)).toHaveCount(60);
  await expect(page.locator(`[data-idea-id="${loaded[12]}"] .news-card-snippet`)).toHaveText('续页期间保存的新内容');
  await scrollToEnd(page);
  await expect(cards(page)).toHaveCount(67);
  expect(new Set(await ids(page))).toEqual(new Set(created.map(idea => idea.idea_id)));
});

test('an older first-page response cannot overwrite a save acknowledged during the request', async ({ page }) => {
  await start(page);
  const id = (await ids(page))[0];
  await cards(page).first().click();
  const held = gate(), reached = gate();
  await page.route('**/api/ideas?*', async route => {
    const response = await route.fetch();
    reached.release();
    await held.promise;
    await route.fulfill({ response });
  });
  try {
    await page.locator('[data-idea-type="standalone"]').click();
    await reached.promise;
    await page.locator('#ideaNote').fill('请求开始后保存的新内容');
    await page.locator('#saveIdeaBtn').click();
    await expect(page.locator('#ideaToolbarTime')).toHaveText('已保存');
  } finally { held.release(); }
  await expect(cards(page)).toHaveCount(30);
  await expect(page.locator(`[data-idea-id="${id}"] .news-card-snippet`)).toHaveText('请求开始后保存的新内容');
});

test('deleting a loaded idea keeps the boundary and exposes every remaining page', async ({ page }) => {
  await start(page);
  await scrollToEnd(page);
  await expect(cards(page)).toHaveCount(60);
  const loaded = await ids(page), removed = loaded[35];
  await cards(page).nth(35).click();
  await page.locator('#deleteIdeaBtn').click();
  await answerConfirm(page);
  await expect(cards(page)).toHaveCount(59);
  expect(await ids(page)).toEqual(loaded.filter(id => id !== removed));
  await scrollToEnd(page);
  await expect(cards(page)).toHaveCount(66);
  expect(new Set(await ids(page))).toEqual(new Set(created.map(idea => idea.idea_id).filter(id => id !== removed)));
});

test('creating an idea above the viewport preserves its visible anchor and older pages', async ({ page }) => {
  await start(page);
  await scrollToEnd(page);
  await expect(cards(page)).toHaveCount(60);
  const loaded = await ids(page);
  await cards(page).nth(35).scrollIntoViewIfNeeded();
  await page.locator('#newIdeaBtn').click();
  await page.locator('#ideaTitle').fill(`${prefix} 新建`);
  await page.locator('#ideaNote').fill('新增后保留已经加载的分页');
  const before = await position(page);
  const response = page.waitForResponse(response => response.url().endsWith('/api/standalone-ideas') && response.request().method() === 'POST');
  await page.locator('#saveIdeaBtn').click();
  const saved = (await (await response).json()).idea;
  created.push(saved);
  await expect(page.locator('#ideaToolbarTime')).toHaveText('已保存');
  expect(await ids(page)).toEqual([saved.idea_id, ...loaded]);
  const after = await position(page);
  expect(after.id).toBe(before.id);
  expect(Math.abs(after.offset - before.offset)).toBeLessThan(1);
  await scrollToEnd(page);
  await expect(cards(page)).toHaveCount(68);
  expect(new Set(await ids(page))).toEqual(new Set(created.map(idea => idea.idea_id)));
});

test('a late append cannot replace the new search or restart old pagination', async ({ page }) => {
  await start(page);
  const loaded = await ids(page), unseen = created.find(idea => !loaded.includes(idea.idea_id));
  const held = gate(), reached = gate();
  await page.route('**/api/ideas?*', async route => {
    if (new URL(route.request().url()).searchParams.get('page') !== '2') return route.continue();
    const response = await route.fetch();
    reached.release();
    await held.promise;
    await route.fulfill({ response });
  });
  try {
    await scrollToEnd(page);
    await reached.promise;
    await page.locator('#ideasSearchInput').fill(unseen.title);
    await expect(cards(page)).toHaveCount(1);
    await expect(cards(page)).toHaveAttribute('data-idea-id', unseen.idea_id);
    expect(await list(page).evaluate(node => node.scrollTop)).toBe(0);
  } finally { held.release(); }
  await page.unrouteAll({ behavior: 'wait' });
  await expect(cards(page)).toHaveCount(1);
  await expect(cards(page)).toHaveAttribute('data-idea-id', unseen.idea_id);
  await expect(page.locator('#loadMoreIdeasBtn')).toHaveCount(0);
});

test('linked note saves retain the loaded standalone pages', async ({ page, request }) => {
  const path = '/api/news/ui-064';
  const saved = await (await request.post(path + '/notes', { data: { note: `${prefix} 关联新闻` } })).json();
  const noteId = saved.notes.at(-1).id;
  let addedId;
  try {
    await start(page);
    await scrollToEnd(page);
    await expect(cards(page)).toHaveCount(60);
    // Same-second timestamps sort by idea ID, so the article can be on page 3.
    await scrollToEnd(page);
    await expect(cards(page)).toHaveCount(68);
    const loaded = await ids(page);
    await page.locator('[data-idea-id="article:ui-064"]').click();
    await page.getByRole('button', { name: '查看想法', exact: true }).click();
    await page.getByRole('button', { name: '写想法', exact: true }).click();
    const before = await position(page);
    await page.locator('#researchNoteInput').fill('关联想法保存后继续浏览');
    const response = page.waitForResponse(response => response.url().endsWith(path + '/notes') && response.request().method() === 'POST');
    await page.locator('#researchNoteInput').press('Control+Enter');
    addedId = (await (await response).json()).notes.at(-1).id;
    await expect(page.locator('[data-idea-id="article:ui-064"] .news-card-snippet')).toHaveText('关联想法保存后继续浏览');
    expect(await ids(page)).toEqual(loaded);
    await expectPosition(page, before);
  } finally {
    await request.delete(path + '/notes/' + noteId);
    if (addedId) await request.delete(path + '/notes/' + addedId);
  }
});
