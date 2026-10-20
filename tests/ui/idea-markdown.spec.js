import { test, expect } from '@playwright/test';
import { openNavigation } from './helpers.js';

const sample = '# 观察方向\n\n**需求**与*价格*，关注 `利率`。\n\n- 第一条\n- 第二条\n\n> 尚需核实\n\n```js\nconst risk = 1;\n```\n\n[来源](https://example.com/research)';
let idea;
const editor = page => page.locator('#ideaNote');
async function open(page, width = 1440) {
  await page.setViewportSize({ width, height: 900 });
  await page.goto('/');
  await expect(page.locator('#feedCardList .news-card').first()).toBeVisible();
  await openNavigation(page);
  await page.locator('#navNotesBtn').click();
  await page.locator(`[data-idea-id="${idea.idea_id}"]`).click();
  await expect(editor(page)).toBeVisible();
}
async function paste(page, text, html = '') {
  await editor(page).focus();
  await editor(page).evaluate((node, { text, html }) => {
    const clipboardData = new DataTransfer();
    clipboardData.setData('text/plain', text);
    if (html) clipboardData.setData('text/html', html);
    node.dispatchEvent(new ClipboardEvent('paste', { bubbles: true, cancelable: true, clipboardData }));
  }, { text, html });
}
async function readSaved(request) {
  return (await (await request.get('/api/ideas?type=standalone&per=100')).json()).items.find(row => row.idea_id === idea.idea_id);
}
test.beforeEach(async ({ request }) => {
  await request.post('/__test/reset-reading');
  for (const row of (await (await request.get('/api/ideas?type=standalone&per=100')).json()).items)
    await request.delete(`/api/standalone-ideas/${row.standalone_id}`);
  idea = (await (await request.post('/api/standalone-ideas', { data: { title: 'Markdown 研究备忘', note: sample } })).json()).idea;
});

test('stored Markdown renders in the editor without dirtying or rewriting an untouched body', async ({ page, request }) => {
  await open(page);
  await expect(editor(page)).toHaveAttribute('contenteditable', 'true');
  await expect(editor(page).locator('h1')).toHaveText('观察方向');
  await expect(editor(page).locator('strong')).toHaveText('需求');
  await expect(editor(page).locator('em')).toHaveText('价格');
  await expect(editor(page).locator('ul li')).toHaveCount(2);
  await expect(editor(page).locator('blockquote')).toHaveText('尚需核实');
  await expect(editor(page).locator('pre code')).toHaveText('const risk = 1;');
  await expect(editor(page).locator('a')).toHaveAttribute('href', 'https://example.com/research');
  await expect(page.locator('#previewIdeaBtn, #copyIdeaBtn')).toHaveCount(0);
  await expect(page.locator('#saveIdeaBtn')).toBeDisabled();
  await page.locator('#ideaTitle').fill('仅修改标题');
  await page.locator('#saveIdeaBtn').click();
  await expect(page.locator('#ideaToolbarTime')).toHaveText('已保存');
  expect((await readSaved(request)).note).toBe(sample);
});

test('syntax reference copies raw Markdown for direct paste without changing the draft', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await open(page);
  await page.getByRole('button', { name: '复制粗体语法', exact: true }).click();
  await expect(page.locator('#toast')).toHaveText('已复制粗体语法，可粘贴到编辑区');
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe('**粗体文字**');
  await expect(page.locator('#saveIdeaBtn')).toBeDisabled();
  await page.getByRole('button', { name: '复制代码块语法', exact: true }).click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe('```\n代码内容\n```');
  await page.locator('#newIdeaBtn').click();
  await editor(page).press('ControlOrMeta+v');
  await expect(editor(page).locator('pre code')).toHaveText('代码内容');
  await page.locator('.standalone-markdown-help summary').click();
  await page.locator(`[data-idea-id="${idea.idea_id}"]`).click();
  await expect(page.locator('.standalone-markdown-help')).not.toHaveAttribute('open', '');
});

test('reselecting a refreshed idea loads external edits while preserving unsaved local drafts', async ({ page, request }) => {
  await open(page);
  const card = page.locator(`[data-idea-id="${idea.idea_id}"]`);
  const refresh = async (title, note) => {
    await request.patch(`/api/standalone-ideas/${idea.standalone_id}`, { data: { title, note } });
    await page.locator('[data-idea-type="standalone"]').click();
    await expect(card.locator('.news-card-title')).toHaveText(title);
    await card.click();
  };
  await refresh('其他窗口已更新', '## 新的研究方向\n\n**新的依据**');
  await expect(editor(page).locator('h2')).toHaveText('新的研究方向');
  await expect(editor(page).locator('strong')).toHaveText('新的依据');
  await expect(page.locator('#saveIdeaBtn')).toBeDisabled();
  await editor(page).fill('本地尚未保存的补充');
  await refresh('其他窗口再次更新', '另一份正文');
  await expect(editor(page)).toHaveText('本地尚未保存的补充');
  await expect(page.locator('#ideaToolbarTime')).toHaveText('尚未保存');
});

test('typing Markdown transforms inline, list continuation works, and formatted content round-trips', async ({ page, request }) => {
  await open(page);
  await page.locator('#newIdeaBtn').click();
  await editor(page).pressSequentially('# ');
  await editor(page).pressSequentially('Heading');
  await expect(editor(page).locator('h1')).toHaveText('Heading');
  await editor(page).press('Enter');
  await editor(page).pressSequentially('**Bold**');
  await expect(editor(page).locator('strong')).toHaveText('Bold');
  await editor(page).pressSequentially(' plain');
  await expect(editor(page).locator('strong')).toHaveText('Bold');
  await editor(page).press('Enter');
  await editor(page).pressSequentially('- one');
  await editor(page).press('Enter');
  await editor(page).pressSequentially('two');
  await expect(editor(page).locator('ul > li')).toHaveCount(2);
  await editor(page).press('Tab');
  await expect(editor(page).locator('ul ul')).toHaveCount(1);
  await editor(page).press('Shift+Tab');
  await expect(editor(page).locator('ul ul')).toHaveCount(0);
  await editor(page).press('Enter');
  await editor(page).press('Enter');
  await editor(page).pressSequentially('> evidence');
  await expect(editor(page).locator('blockquote')).toHaveText('evidence');
  await editor(page).press('Control+Enter');
  await expect(page.locator('#ideaToolbarTime')).toHaveText('已保存');
  const saved = (await (await request.get('/api/ideas?type=standalone&per=100')).json()).items.find(row => row.note.includes('# Heading'));
  expect(saved.note).toContain('**Bold** plain');
  expect(saved.note).toMatch(/[*-] one\n[*-] two/);
  expect(saved.note).toContain('> evidence');
  await page.reload();
  await openNavigation(page);
  await page.locator('#navNotesBtn').click();
  await page.locator(`[data-idea-id="${saved.idea_id}"]`).click();
  await expect(editor(page).locator('strong')).toHaveText('Bold');
  await expect(editor(page).locator('ul li')).toHaveCount(2);
});

test('Markdown paste, code fences and formatting shortcuts remain editable with undo and redo', async ({ page }) => {
  await open(page);
  await page.locator('#newIdeaBtn').click();
  await paste(page, '## 粘贴标题\n\n**中文粗体**\n\n1. 一\n2. 二');
  await expect(editor(page).locator('h2')).toHaveText('粘贴标题');
  await expect(editor(page).locator('strong')).toHaveText('中文粗体');
  await expect(editor(page).locator('ol li')).toHaveCount(2);
  await editor(page).press('ControlOrMeta+z');
  await expect(editor(page)).toHaveText('');
  await editor(page).press('ControlOrMeta+Shift+z');
  await expect(editor(page).locator('h2')).toHaveText('粘贴标题');
  await editor(page).fill('');
  await editor(page).press('ControlOrMeta+Alt+0');
  await editor(page).pressSequentially('```js');
  await editor(page).press('Enter');
  await editor(page).pressSequentially('const x = 1;');
  await expect(editor(page).locator('pre')).toHaveText('const x = 1;');
  await editor(page).press('Shift+Enter');
  await editor(page).press('ControlOrMeta+b');
  await editor(page).pressSequentially('Keyboard');
  await editor(page).press('ControlOrMeta+b');
  await expect(editor(page).locator('strong')).toHaveText('Keyboard');
  await editor(page).press('Enter');
  await editor(page).pressSequentially('**undo**');
  await editor(page).press('Backspace');
  await expect(editor(page)).toContainText('**undo**');
});

test('unsafe Markdown and pasted HTML stay inert', async ({ page, request }) => {
  await request.patch(`/api/standalone-ideas/${idea.standalone_id}`, { data: {
    note: '<script>window.badIdea = true</script>\n\n[bad](javascript:alert(1))\n\n![image](data:image/svg+xml,bad)',
  } });
  await open(page);
  await expect(editor(page).locator('script, a[href^="javascript:"], img[src^="data:"]')).toHaveCount(0);
  await editor(page).fill('');
  await paste(page, 'safe unsafe', '<p><strong>safe</strong><a href="javascript:alert(1)">unsafe</a><img src="bad" onerror="window.badIdea=true"></p>');
  await expect(editor(page).locator('strong')).toHaveText('safe');
  await expect(editor(page).locator('a[href^="javascript:"], [onerror], script')).toHaveCount(0);
  expect(await page.evaluate(() => window.badIdea)).toBeUndefined();
});

test('overlong paste is rejected without losing content, while a 5000-character note can be saved', async ({ page, request }) => {
  await open(page);
  await editor(page).fill('保留原稿');
  await editor(page).press('ControlOrMeta+a');
  await editor(page).press('ArrowRight');
  await paste(page, 'x'.repeat(5001));
  await expect(page.locator('#ideaSaveError')).toContainText('5000');
  await expect(editor(page)).toHaveText('保留原稿');
  await editor(page).press('ControlOrMeta+Alt+0');
  await editor(page).fill('文'.repeat(5000));
  await page.locator('#saveIdeaBtn').click();
  await expect(page.locator('#ideaToolbarTime')).toHaveText('已保存');
  expect((await readSaved(request)).note).toBe('文'.repeat(5000));
});

test('Chinese composition keeps its caret and saving from a code block preserves the document', async ({ page, request }) => {
  await open(page);
  await page.locator('#newIdeaBtn').click();
  await editor(page).focus();
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Input.imeSetComposition', { text: '中文输入', selectionStart: 4, selectionEnd: 4 });
  await expect(editor(page)).toHaveText('中文输入');
  await cdp.send('Input.insertText', { text: '中文输入' });
  await editor(page).pressSequentially('**重点**');
  await expect(editor(page).locator('strong')).toHaveText('重点');
  await expect(editor(page)).toHaveText('中文输入重点');
  await editor(page).press('Enter');
  await editor(page).pressSequentially('```js');
  await editor(page).press('Enter');
  await editor(page).pressSequentially('const value = 1;');
  await editor(page).press('ControlOrMeta+Enter');
  await expect(page.locator('#ideaToolbarTime')).toHaveText('已保存');
  await expect(editor(page)).toBeFocused();
  await expect(editor(page).locator('pre')).toHaveText('const value = 1;');
  const saved = (await (await request.get('/api/ideas?type=standalone&per=100')).json()).items.find(row => row.note.startsWith('中文输入'));
  expect(saved.note).toContain('中文输入**重点**');
  expect(saved.note).toContain('```js\nconst value = 1;\n```');
  await cdp.detach();
});

for (const width of [1440, 390]) {
  test(`formatted editor layout and draft history at ${width}px`, async ({ page }) => {
    await open(page, width);
    for (const theme of ['light', 'dark', 'eink']) {
      await page.evaluate(theme => document.documentElement.dataset.theme = theme, theme);
      expect(await editor(page).evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
      await page.screenshot({ path: `.ui-artifacts/idea-markdown-${width}-${theme}.png` });
      const help = page.locator('.standalone-markdown-help');
      await help.scrollIntoViewIfNeeded();
      expect(await help.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
      await page.screenshot({ path: `.ui-artifacts/idea-markdown-help-${width}-${theme}.png` });
      await page.locator('#ideaScrollArea').evaluate(node => node.scrollTop = 0);
    }
    await editor(page).press('ControlOrMeta+a');
    await editor(page).press('ArrowRight');
    await editor(page).press('Enter');
    await editor(page).pressSequentially('draft');
    if (width <= 920) await page.locator('#mobileReaderBackBtn').click();
    await page.locator('#newIdeaBtn').click();
    if (width <= 920) await page.locator('#mobileReaderBackBtn').click();
    await page.locator(`[data-idea-id="${idea.idea_id}"]`).click();
    await expect(editor(page)).toContainText('draft');
    await editor(page).press('ControlOrMeta+z');
    await expect(editor(page)).not.toContainText('draft');
    await expect(editor(page).locator('h1')).toHaveText('观察方向');
  });
}
