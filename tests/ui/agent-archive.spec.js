import { test, expect } from '@playwright/test';
import { chooseTheme } from './helpers.js';

const article = '/api/news/ui-064';
const errors = {
  empty_archive_summary: '没有可归档的实质内容。',
  codex_timeout: '请求超时。',
  invalid_archive_summary: '归档仍超出长度限制，请重试。',
};

async function prepare(request, runtime) {
  await request.post('/__test/reset-reading');
  await request.post('/__test/reset-agent');
  await request.put(article + '/note', { data: { note: '' } });
  const settings = await (await request.get('/api/settings')).json();
  await request.put('/api/settings', { data: { llm: settings.llm,
    agent: { ...settings.agent, runtime }, feed: { hidden_source_subkeys: [] } } });
  await request.post('/__test/agent-complete', { data: {
    item_id: 'ui-064', answer: '融资成本既受政策利率影响，也受信用风险溢价影响。',
  } });
  return (await request.get(article + '/agent/session')).json();
}

async function openArticle(page, width = 1440) {
  await page.setViewportSize({ width, height: 1000 });
  await page.goto('/');
  if (width <= 920) await page.locator('#feedCardList .news-card-title').first().click();
  await expect(page.locator('#articleMainTitle')).toBeVisible();
  await page.locator('#agentDockToggleBtn').click();
  await expect(page.locator('#agentStatus')).toHaveText('本轮已完成');
}

const scenarios = [
  ['codex', 1440, 'light', 'light', 'empty_archive_summary'],
  ['codex', 390, 'dark', 'dark', 'codex_timeout'],
  ['pi', 1440, 'system', 'light', 'invalid_archive_summary'],
  ['codex', 390, 'system', 'dark', 'empty_archive_summary'],
  ['pi', 1440, 'eink', 'light', 'empty_archive_summary'],
  ['pi', 390, 'eink', 'light', 'invalid_archive_summary'],
];

for (const [runtime, width, theme, colorScheme, code] of scenarios) {
  test(`archive failure stays visible and retry saves a thought: ${runtime}, ${width}, ${theme}`, async ({ page, request }) => {
    const before = await prepare(request, runtime);
    await page.emulateMedia({ colorScheme });
    await openArticle(page, width);
    await page.locator('#agentDockToggleBtn').click();
    await chooseTheme(page, theme);
    await page.locator('#agentDockToggleBtn').click();
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    await page.route('**/api/news/ui-064/chat/archive', async route => {
      await gate;
      await route.fulfill({ status: 502, json: { ok: false, error: code } });
    }, { times: 1 });

    await page.locator('#archiveAgentBtn').click();
    await expect(page.locator('#agentStatus')).toHaveText('正在提炼结论并追加到想法…');
    await expect(page.locator('#archiveAgentBtn')).toBeDisabled();
    await expect(page.locator('#agentNewBtn')).toBeDisabled();
    await expect(page.locator('#agentSendBtn')).toBeDisabled();
    await page.locator('#agentPromptInput').fill('保留下一轮问题草稿');
    await page.locator('#agentPromptInput').press('Control+Enter');
    await expect(page.locator('#agentPromptInput')).toHaveValue('保留下一轮问题草稿');
    release();
    await expect(page.locator('#agentStatus')).toHaveText(`归档失败 · ${errors[code]}`);
    await expect(page.locator('#archiveAgentBtn')).toBeEnabled();
    await expect(page.locator('.agent-note')).toHaveCount(0);
    await expect(page.locator('#toast')).not.toHaveClass(/show/, { timeout: 6000 });
    await expect(page.locator('#agentStatus')).toHaveText(`归档失败 · ${errors[code]}`);
    await page.screenshot({ path: `.ui-artifacts/archive-${runtime}-${width}-${theme}.png` });

    const response = page.waitForResponse(r => r.url().endsWith('/chat/archive'));
    await page.locator('#archiveAgentBtn').click();
    expect((await response).status()).toBe(200);
    await expect(page.locator('#agentStatus')).toHaveText('结论已追加到本篇想法');
    await expect(page.locator('.agent-note')).toHaveCount(1);
    await expect(page.locator('.agent-note')).toContainText('关注准备金与融资利率');
    await expect(page.locator('#agentPromptInput')).toHaveValue('保留下一轮问题草稿');
    const after = await (await request.get(article + '/agent/session')).json();
    expect(after.messages).toEqual(before.messages);
    expect(after.jobs).toEqual(before.jobs);
    const notes = (await (await request.get(article + '/detail')).json()).notes;
    expect(notes).toHaveLength(1);
    expect(notes[0].author).toBe('agent');
    expect(notes[0].source_session_id).toBe(before.session.id);
  });
}

test('late archive results stay with their article while another archive runs', async ({ page, request }) => {
  await prepare(request, 'codex');
  const other = '/api/news/ui-063';
  await request.put(other + '/note', { data: { note: '' } });
  await request.post('/__test/agent-complete', { data: { item_id: 'ui-063', answer: '另一篇新闻的独立研究：应关注企业融资成本的实际变化，判断政策传导仍需更多证据。' } });
  await openArticle(page);
  let releaseFirst, releaseSecond;
  const first = new Promise(resolve => { releaseFirst = resolve; });
  const second = new Promise(resolve => { releaseSecond = resolve; });
  await page.route('**/api/news/ui-064/chat/archive', async route => {
    await first;
    await route.fulfill({ status: 502, json: { ok: false, error: 'codex_timeout' } });
  });
  await page.route('**/api/news/ui-063/chat/archive', async route => {
    await second;
    await route.continue();
  });
  await page.locator('#archiveAgentBtn').click();
  await expect(page.locator('#agentStatus')).toContainText('正在提炼');
  await page.locator('#agentDockToggleBtn').click();
  await page.locator('#feedCardList [data-id="ui-063"] .news-card-title').click();
  await expect(page.locator('#feedCardList [data-id="ui-063"]')).toHaveClass(/active/);
  await page.locator('#agentDockToggleBtn').click();
  await expect(page.locator('#agentStatus')).toHaveText('本轮已完成');
  await page.locator('#archiveAgentBtn').click();
  await expect(page.locator('#agentStatus')).toContainText('正在提炼');
  releaseFirst();
  await expect(page.locator('#toast')).toContainText('归档失败');
  await expect(page.locator('#agentStatus')).toContainText('正在提炼');
  await expect(page.locator('#archiveAgentBtn')).toBeDisabled();
  releaseSecond();
  await expect(page.locator('#agentStatus')).toHaveText('结论已追加到本篇想法');
  await expect(page.locator('.agent-note')).toHaveCount(1);
  expect((await (await request.get(article + '/detail')).json()).notes).toHaveLength(0);
  expect((await (await request.get(other + '/detail')).json()).notes).toHaveLength(1);
  await page.locator('#agentDockToggleBtn').click();
  await page.locator('#feedCardList [data-id="ui-064"] .news-card-title').click();
  await expect(page.locator('#feedCardList [data-id="ui-064"]')).toHaveClass(/active/);
  await page.locator('#agentDockToggleBtn').click();
  await expect(page.locator('#agentStatus')).toHaveText('归档失败 · 请求超时。');
  await expect(page.locator('.agent-note')).toHaveCount(0);
});
