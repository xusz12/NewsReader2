import { test, expect } from '@playwright/test';
import { openNavigation } from './helpers.js';

const path = '/api/news/ui-064';
const linked = page => page.locator('[data-idea-id="article:ui-064"]');
const conversation = [{ role: 'user', content: '如何判断流动性传导？' }, { role: 'assistant', content: '关注准备金与融资利率。' }];
let standalone;

async function start(page, width = 1440) {
  await page.setViewportSize({ width, height: 900 });
  await page.goto('/');
  await expect(page.locator('#feedCardList .news-card').first()).toBeVisible();
  await openNavigation(page);
  await page.locator('#navNotesBtn').click();
  await expect(linked(page)).toBeVisible();
}
const standaloneCard = page => page.locator(`[data-idea-id="${standalone.idea_id}"]`);

test.beforeEach(async ({ request }) => {
  await request.post('/__test/reset-reading');
  const settings = await (await request.get('/api/settings')).json();
  await request.put('/api/settings', { data: { llm: { ...settings.llm, pi_chat: { provider: 'test', model: 'test-model' } }, agent: { ...settings.agent, runtime: 'pi' }, feed: { hidden_source_subkeys: [] } } });
  await request.put(path + '/note', { data: { note: '' } });
  await request.post(path + '/notes', { data: { note: '既有研究判断', quote: { text: '原文引用证据', source: 'original', paragraph: 2 } } });
  const ideas = (await (await request.get('/api/ideas?type=standalone&per=100')).json()).items;
  for (const idea of ideas) await request.delete(`/api/standalone-ideas/${idea.standalone_id}`);
  standalone = (await (await request.post('/api/standalone-ideas', { data: {
    title: '研究备忘', note: '政策信号与市场定价之间仍有距离。\n\n- 继续观察融资利率\n- 核实准备金变化',
  } })).json()).idea;
  const highlights = (await (await request.get(path + '/highlights')).json()).highlight_surfaces;
  for (const surface of Object.values(highlights))
    for (const entry of surface.highlights) await request.delete(path + `/highlights/${entry.id}`);
});

test('linked idea reuses full reader, retains highlights and authorship, and archives in place', async ({ page, request }) => {
  await page.route('**/api/news/ui-064/agent/session', route => route.fulfill({ json: {
    ok: true, session: null, context_available: true, messages: conversation, jobs: [],
  } }));
  await start(page);
  await linked(page).click();
  await expect(page.locator('#feedColIdeas')).toBeVisible();
  await expect(page.locator('#navNotesBtn')).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('#articleBodyContent')).toBeVisible();
  await expect(page.locator('.digest-lead')).toBeVisible();
  await expect(page.locator('.saved-note .thought-quote')).toContainText('原文引用证据');
  const paragraph = page.locator('#articleBodyContent > p').nth(1);
  await paragraph.scrollIntoViewIfNeeded();
  await paragraph.evaluate(element => {
    const range = document.createRange(); range.selectNodeContents(element);
    const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
    element.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
  });
  await page.getByRole('button', { name: '高亮', exact: true }).click();
  await expect(paragraph.locator('mark')).not.toHaveCount(0);
  await page.locator('[data-body-mode="bilingual"]').click();
  await page.locator('#detailScrollArea').evaluate(node => node.scrollTop = 210);
  const scroll = await page.locator('#detailScrollArea').evaluate(node => node.scrollTop);
  await standaloneCard(page).click();
  await expect(page.locator('#ideaNote')).toBeVisible();
  await linked(page).click();
  await expect(page.locator('[data-body-mode="bilingual"]')).toHaveClass(/active/);
  await expect.poll(() => page.locator('#detailScrollArea').evaluate(node => node.scrollTop)).toBe(scroll);
  await page.locator('[data-body-mode="translated"]').click();
  await expect(paragraph.locator('mark')).not.toHaveCount(0);
  await page.getByRole('button', { name: '查看想法', exact: true }).click();
  await page.getByRole('button', { name: '写想法', exact: true }).click();
  await page.locator('#researchNoteInput').fill('从想法页继续补充');
  await page.locator('#researchNoteInput').press('Control+Enter');
  await expect(page.locator('.saved-note')).toHaveCount(2);
  await expect(linked(page).locator('.note-badge')).toHaveText('2');
  await expect(linked(page).locator('.news-card-snippet')).toContainText('从想法页继续补充');
  await page.locator('#agentDockToggleBtn').click();
  await expect(page.locator('#archiveAgentBtn')).toBeEnabled();
  await page.locator('#archiveAgentBtn').click();
  await expect(page.locator('.agent-note')).toHaveCount(1);
  await expect(linked(page).locator('.note-badge')).toHaveText('3');
  const notes = (await (await request.get(path + '/detail')).json()).notes;
  expect(notes.map(note => note.author)).toEqual(['user', 'user', 'agent']);
  expect(notes[0].quote_text).toBe('原文引用证据');
  await page.locator('#agentDockToggleBtn').click();
  await page.locator('.saved-note').nth(1).hover();
  await page.locator('.saved-note').nth(1).getByRole('button', { name: '删除想法' }).click();
  await expect(linked(page).locator('.note-badge')).toHaveText('2');
  await expect(page.locator('#feedColIdeas')).toBeVisible();
});

test('lightweight editor keeps drafts and retries without overwriting newer input', async ({ page, request }) => {
  await start(page);
  await standaloneCard(page).click();
  await expect(page.locator('#ideaNote')).toBeVisible();
  await expect(page.locator('#saveIdeaBtn')).toBeDisabled();
  await page.locator('#ideaTitle').fill('我的标题');
  await page.locator('#ideaNote').fill('**第一稿**\n\n<script>不会执行</script>');
  await linked(page).click();
  await standaloneCard(page).click();
  await expect(page.locator('#ideaNote')).toHaveText(/\*\*第一稿\*\*[\s\S]*<script>不会执行<\/script>/);
  await page.locator('#ideaNote').blur();
  await page.keyboard.press(',');
  await expect(page.locator('#detailPanelSettings')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('#ideaTitle')).toHaveValue('我的标题');
  const url = `**/api/standalone-ideas/${standalone.standalone_id}`;
  await page.route(url, route => route.fulfill({ status: 500, json: { ok: false, error: 'request_failed' } }));
  await page.locator('#ideaNote').press('Control+Enter');
  await expect(page.locator('#ideaSaveError')).toBeVisible();
  await expect(page.locator('#ideaToolbarTime')).toHaveText('尚未保存');
  await page.unroute(url);
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  await page.route(url, async route => { await gate; await route.continue(); });
  await page.locator('#saveIdeaBtn').click();
  await expect(page.locator('#saveIdeaBtn')).toBeDisabled();
  await page.locator('#ideaNote').fill('请求期间写入的第二稿');
  await linked(page).click();
  release();
  await expect(standaloneCard(page).locator('.news-card-title')).toHaveText('我的标题');
  await expect(page.locator('#detailPanelFeed')).toBeVisible();
  await standaloneCard(page).click();
  await expect(page.locator('#ideaNote')).toHaveText('请求期间写入的第二稿');
  await expect(page.locator('#ideaToolbarTime')).toHaveText('尚未保存');
  await page.locator('#saveIdeaBtn').click();
  await expect(page.locator('#ideaToolbarTime')).toHaveText('已保存');
  const saved = (await (await request.get('/api/ideas?type=standalone')).json()).items[0];
  expect(saved.note).toBe('请求期间写入的第二稿');
  expect(saved.title).toBe('我的标题');
});

test('new idea resolves to one saved record even when the selection changes during creation', async ({ page, request }) => {
  await start(page);
  await page.locator('#newIdeaBtn').click();
  await page.locator('#ideaNote').fill('无标题的新想法');
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  await page.route('**/api/standalone-ideas', async route => { await gate; await route.continue(); });
  await page.locator('#saveIdeaBtn').click();
  await expect(page.locator('#saveIdeaBtn')).toBeDisabled();
  await standaloneCard(page).click();
  release();
  const created = page.locator('#ideasCardList .news-card').filter({ hasText: '无标题的新想法' });
  await expect(created).toHaveCount(1);
  await expect(page.locator('#ideaTitle')).toHaveValue('研究备忘');
  await created.click();
  await page.locator('#ideaNote').fill('无标题的新想法，继续补充');
  await page.locator('#saveIdeaBtn').click();
  await expect(page.locator('#ideaToolbarTime')).toHaveText('已保存');
  const saved = (await (await request.get('/api/ideas?type=standalone')).json()).items;
  expect(saved).toHaveLength(2);
  expect(saved.filter(idea => idea.note.startsWith('无标题的新想法'))).toHaveLength(1);
});

test('missing article body retains notes and late article responses cannot replace standalone editing', async ({ page }) => {
  await page.route('**/api/news/ui-064/detail', async route => {
    const data = await (await route.fetch()).json();
    data.detail = null; data.ai = null; data.detail_status = 'failed'; data.ai_status = 'none';
    await route.fulfill({ json: data });
  });
  await start(page);
  await linked(page).click();
  await expect(page.locator('.saved-note')).toContainText('既有研究判断');
  await expect(page.locator('#articleBodyContent')).toHaveCount(0);
  await expect(page.locator('#fetchArticleBtn')).toBeEnabled();
  await page.unroute('**/api/news/ui-064/detail');
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  await page.route('**/api/news/ui-064/detail', async route => { await gate; await route.continue(); });
  await linked(page).click();
  await standaloneCard(page).click();
  await page.locator('#ideaNote').fill('迟到响应不应覆盖此草稿');
  const response = page.waitForResponse(r => r.url().endsWith('/ui-064/detail'));
  release();
  await response;
  await expect(page.locator('#detailPanelFeed')).toBeHidden();
  await expect(page.locator('#ideaNote')).toHaveText('迟到响应不应覆盖此草稿');
  await expect(page.locator('#ideaToolbarTime')).toHaveText('尚未保存');
});

for (const width of [1440, 390, 360]) {
  test(`lightweight layout and mobile return at ${width}px`, async ({ page }) => {
    await start(page, width);
    await standaloneCard(page).click();
    await expect(page.locator('#ideaTitle')).toHaveValue('研究备忘');
    await page.locator('#ideaNote').fill('政策信号与市场定价之间仍有距离。\n\n下次阅读时，重点观察：\n\n- 融资利率的实际变化\n- 准备金规模与政策预期\n\n先记录判断，再逐步补充依据。');
    for (const theme of ['light', 'dark', 'eink']) {
      await page.evaluate(theme => document.documentElement.dataset.theme = theme, theme);
      await expect(page.locator('#ideaToolbarTime')).toBeVisible();
      expect(await page.locator('#detailPanelIdeas').evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
      expect(await page.locator('#saveIdeaBtn').evaluate(node => {
        const r = node.getBoundingClientRect();
        return node.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2));
      })).toBe(true);
      await page.locator('#ideaNote').blur();
      await page.screenshot({ path: `.ui-artifacts/ideas-editor-${width}-${theme}.png` });
    }
    if (width <= 920) {
      await expect(page.locator('.mobile-bottom-nav button')).toHaveText([/新闻流/, /稍后再看/, /收藏/]);
      await page.locator('#mobileReaderBackBtn').click();
      await expect(page.locator('#feedColIdeas')).toBeVisible();
      await linked(page).click();
      await expect(page.locator('#articleBodyContent')).toBeVisible();
      await expect(page.locator('#mobileReaderBackBtn')).toHaveAttribute('aria-label', '返回想法列表');
      await page.locator('#mobileReaderBackBtn').click();
      await standaloneCard(page).click();
      await expect(page.locator('#ideaNote')).toContainText('先记录判断');
    }
  });
}
