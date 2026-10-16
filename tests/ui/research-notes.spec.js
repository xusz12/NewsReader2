import { test, expect } from '@playwright/test';

const path = '/api/news/ui-064';
const conversation = [{ role: 'user', content: '如何判断流动性传导？' }, { role: 'assistant', content: '关注准备金与融资利率，政策传导仍待核实。' }];
const write = page => page.getByRole('button', { name: '写想法', exact: true });
const input = page => page.getByRole('textbox', { name: '写下研读想法' });
async function start(page, width = 1440) {
  await page.setViewportSize({ width, height: 1000 });
  await page.goto('/');
  if (width < 768) await page.locator('#feedCardList .news-card-title').first().click();
  await expect(page.locator('#articleMainTitle')).toBeVisible();
  await expect(write(page)).toBeVisible();
}
async function selectParagraph(page, index = 1) {
  const node = page.locator('#articleBodyContent > p').nth(index);
  await node.scrollIntoViewIfNeeded();
  return node.evaluate(element => {
    const range = document.createRange(); range.selectNodeContents(element);
    const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
    element.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
    return element.textContent;
  });
}
test.beforeEach(async ({ request }) => {
  await request.post('/__test/reset-reading');
  await request.put(path + '/note', { data: { note: '' } });
});

test('collapsed composer, multiple thoughts, keyboard publish, hover deletion and reload', async ({ page, request }) => {
  await start(page);
  await expect(page.locator('#researchNoteComposer')).toBeHidden();
  await expect(page.locator('.thoughts-count')).toHaveCount(0);
  await expect(page.locator('.research-notes h3')).toHaveCount(0);
  await write(page).click();
  await expect(input(page)).toBeFocused();
  await input(page).fill('第一个时间点的判断');
  await input(page).press('Escape');
  await expect(page.locator('#researchNoteComposer')).toBeHidden();
  await expect(write(page)).toBeFocused();
  await write(page).click();
  await expect(input(page)).toHaveValue('第一个时间点的判断');
  await input(page).press('Control+Enter');
  await expect(page.locator('.saved-note')).toHaveCount(1);
  await expect(page.locator('#researchNoteComposer')).toBeHidden();
  await expect(write(page)).toBeFocused();
  await write(page).click();
  await input(page).fill('第二个时间点 <script>不可执行</script>');
  await page.getByRole('button', { name: '发布想法', exact: true }).click();
  await expect(page.locator('.saved-note')).toHaveCount(2);
  await expect(page.locator('[data-id="ui-064"] .note-badge')).toHaveText('2');
  await page.mouse.move(0, 0);
  const first = page.locator('.saved-note').first();
  const remove = first.getByRole('button', { name: '删除想法' });
  await expect(remove).toHaveCSS('opacity', '0');
  await first.hover();
  await expect(remove).toHaveCSS('opacity', '1');
  await remove.click();
  await expect(page.locator('.saved-note')).toHaveCount(1);
  await expect(page.locator('.saved-note')).toContainText('第二个时间点 <script>不可执行</script>');
  await expect(page.locator('.saved-note script')).toHaveCount(0);
  expect((await (await request.get(path + '/detail')).json()).notes).toHaveLength(1);
  await page.reload();
  await expect(page.locator('.saved-note')).toHaveCount(1);
  await expect(page.locator('#researchNoteComposer')).toBeHidden();
});

test('quote original or translated paragraphs and remove a draft quote', async ({ page, request }) => {
  await start(page);
  const translated = await selectParagraph(page);
  await page.locator('#quoteNoteBtn').click();
  await expect(page.locator('.draft-quote')).toContainText('引用译文第 2 段');
  await expect(page.locator('.draft-quote')).toContainText(translated);
  await input(page).fill('针对译文的判断');
  await input(page).press('Meta+Enter');
  await expect(page.locator('.saved-note .thought-quote')).toContainText(translated);
  await page.locator('[data-body-mode="original"]').click();
  const original = await selectParagraph(page);
  await page.locator('#quoteNoteBtn').click();
  await expect(page.locator('.draft-quote')).toContainText('引用原文第 2 段');
  await input(page).fill('针对原文的判断');
  await page.getByRole('button', { name: '发布想法', exact: true }).click();
  await expect(page.locator('.saved-note')).toHaveCount(2);
  const notes = (await (await request.get(path + '/detail')).json()).notes;
  expect(notes.map(n => [n.quote_source, n.quote_paragraph, n.quote_text])).toEqual([['translated', 2, translated], ['original', 2, original]]);
  await selectParagraph(page);
  await page.locator('#quoteNoteBtn').click();
  await page.getByRole('button', { name: '移除引用' }).click();
  await expect(page.locator('.draft-quote')).toHaveCount(0);
  await expect(input(page)).toBeFocused();
  await page.getByRole('button', { name: '取消', exact: true }).click();
  await expect(page.locator('#researchNoteComposer')).toBeHidden();
});

test('draft survives article changes and failed publish; pending publish cannot discard newer input', async ({ page }) => {
  await start(page);
  await write(page).click();
  await input(page).fill('保留这份草稿');
  await page.locator('[data-id="ui-063"] .news-card-title').click();
  await expect(page.locator('#researchNoteComposer')).toBeHidden();
  await page.locator('[data-id="ui-064"] .news-card-title').click();
  await expect(input(page)).toHaveValue('保留这份草稿');
  await page.route('**/api/news/ui-064/notes', route => route.fulfill({ status: 500, json: { ok: false, error: 'request_failed' } }));
  await input(page).press('Control+Enter');
  await expect(page.locator('.research-note-error')).toBeVisible();
  await expect(input(page)).toHaveValue('保留这份草稿');
  await expect(page.locator('.saved-note')).toHaveCount(0);
  await page.unroute('**/api/news/ui-064/notes');
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  await page.route('**/api/news/ui-064/notes', async route => { await gate; await route.continue(); });
  await input(page).press('Control+Enter');
  await expect(page.locator('#publishResearchNoteBtn')).toBeDisabled();
  await input(page).fill('发布等待期间的新草稿');
  release();
  await expect(page.locator('.saved-note')).toHaveCount(1);
  await expect(input(page)).toHaveValue('发布等待期间的新草稿');
  await expect(input(page)).toBeFocused();
  await page.locator('[data-body-mode="original"]').click();
  await expect(input(page)).toHaveValue('发布等待期间的新草稿');
});

test('Agent archive calls the summary API and adds an authored card without erasing a draft', async ({ page, request }) => {
  const quotedConversation = conversation.map(message => message.role === 'user'
    ? { ...message, quote_text: '政策利率调整如何传导至企业融资成本？' }
    : message);
  await page.route('**/api/news/ui-064/agent/session', route => route.fulfill({ json: {
    ok: true, session: null, context_available: true, messages: quotedConversation, jobs: [],
  } }));
  await start(page);
  await write(page).click();
  await input(page).fill('尚未发布的个人想法');
  await page.locator('#agentDockToggleBtn').click();
  await expect(page.locator('#archiveAgentBtn')).toBeEnabled();
  const response = page.waitForResponse(r => r.url().endsWith('/chat/archive'));
  await page.locator('#archiveAgentBtn').click();
  const archived = await response;
  expect(archived.status()).toBe(200);
  expect(archived.request().postDataJSON().messages).toEqual(quotedConversation);
  await expect(page.locator('.agent-note')).toHaveCount(1);
  await expect(page.locator('.thought-agent-badge')).toHaveText('AI 研读归档');
  await expect(page.locator('.agent-note')).toContainText('流动性传导研读结论');
  await expect(page.locator('.thought-origin')).toHaveCount(0);
  await expect(input(page)).toHaveValue('尚未发布的个人想法');
  const notes = (await (await request.get(path + '/detail')).json()).notes;
  expect(notes[0].author).toBe('agent');
  expect(notes[0].archive_summary).toContain('\n');
  // Delete controls remain accessible by keyboard even when the mouse is elsewhere.
  await page.locator('#agentDockToggleBtn').click();
  const remove = page.locator('.agent-note .note-delete-btn');
  await remove.focus();
  await expect(remove).toHaveCSS('opacity', '1');
  await remove.press('Enter');
  await expect(page.locator('.agent-note')).toHaveCount(0);
  await expect(input(page)).toHaveValue('尚未发布的个人想法');
});

for (const width of [1440, 390]) {
  test(`timeline layout at ${width}px with user and Agent cards`, async ({ page, request }) => {
    await request.post(path + '/notes', { data: { note: '核心交易主线依然是美债 2Y/10Y 陡峭化。隔夜逆回购用量的变化值得持续跟踪。', quote: { text: '当逆回购余额降至技术底线时，国债供给与财政部一般账户的任何剧烈变动，都将直接抽调商业银行的准备金。', source: 'original', paragraph: 2 } } });
    await request.post(path + '/chat/archive', { data: { messages: conversation } });
    await request.post(path + '/notes', { data: { note: '关注下周财政部再融资计划，继续核实期限结构变化与市场定价的关系。' } });
    await start(page, width);
    await expect(page.locator('.saved-note')).toHaveCount(3);
    await write(page).click();
    await input(page).fill('记录当前的研读判断…');
    // Opening preserves the heading position; scroll explicitly to inspect the composer.
    await page.locator('#researchNoteComposer').scrollIntoViewIfNeeded();
    const notes = page.locator('.research-notes');
    expect(await notes.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
    expect(await page.locator('#publishResearchNoteBtn').evaluate(button => {
      const rect = button.getBoundingClientRect();
      return button.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2));
    })).toBe(true);
    await page.screenshot({ path: `.ui-artifacts/research-notes-page-${width}.png` });
    await notes.screenshot({ path: `.ui-artifacts/research-notes-${width}.png`, style: '#agentDock, .mobile-bottom-nav { visibility: hidden; }' });
    await page.evaluate(() => document.documentElement.dataset.theme = 'dark');
    await notes.screenshot({ path: `.ui-artifacts/research-notes-dark-${width}.png`, style: '#agentDock, .mobile-bottom-nav { visibility: hidden; }' });
    await page.getByRole('button', { name: '发布想法', exact: true }).click();
    await expect(page.locator('.saved-note')).toHaveCount(4);
  });
}
