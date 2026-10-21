import { answerConfirm } from "./helpers.js";
import { test, expect } from '@playwright/test';

const itemId = 'ui-064';
const input = page => page.locator('#agentPromptInput');
const stream = page => page.locator('#agentChatStream');
const session = async request => (await (await request.get(`/api/news/${itemId}/agent/session`)).json());
const notices = async request => (await (await request.get('/api/agent/notifications')).json()).notifications;
async function start(page, width = 1440) {
  await page.setViewportSize({ width, height: 900 });
  await page.goto('/');
  await page.bringToFront();
  await expect(page.locator(`[data-id="${itemId}"]`)).toBeVisible();
  await page.locator(`[data-id="${itemId}"]`).click();
  await page.locator('#agentDockToggleBtn').click();
  await expect(input(page)).toBeVisible();
  await expect(page.locator('#agentSendBtn')).toBeEnabled();
}
async function complete(request, response, answer) {
  const result = await response.json();
  expect(response.ok()).toBeTruthy();
  await request.post('/__test/agent-complete', { data: { item_id: itemId, job_id: result.job_id, answer } });
  return result;
}

test.beforeEach(async ({ request }) => {
  await request.post('/__test/reset-agent');
  await request.post('/__test/reset-reading');
  await request.post('/__test/agent-article', { data: { item_id: itemId, missing: true } });
});
test.afterEach(async ({ request }) => {
  await request.post('/__test/agent-article', { data: { item_id: itemId, missing: false } });
  await request.post('/__test/reset-agent');
});

for (const width of [1440, 390]) {
  test(`title-only chat preserves drafts and opens its notified reply at ${width}px`, async ({ page, request }) => {
    let fetches = 0;
    page.on('request', request => { if (request.url().includes('/detail/retry')) fetches++; });
    await start(page, width);
    const title = (await (await request.get(`/api/news/${itemId}/detail`)).json()).agent.context_title;
    await expect(page.locator('#agentContext')).toContainText(`引用新闻标题：${title}`);
    await expect(page.locator('#agentContext')).toContainText('尚未提供全文');
    expect((await session(request)).messages).toHaveLength(0);
    await input(page).fill('标题聊天尚未发送的草稿');
    if (width < 920) await page.locator('#mobileReaderBackBtn').click();
    await page.locator('[data-id="ui-063"]').click();
    if (width < 920) await page.locator('#mobileReaderBackBtn').click();
    await page.locator(`[data-id="${itemId}"]`).click();
    if (await page.locator('#agentDock').evaluate(node => node.classList.contains('minimized'))) await page.locator('#agentDockToggleBtn').click();
    await expect(input(page)).toHaveValue('标题聊天尚未发送的草稿');
    await expect(page.locator('#agentSendBtn')).toBeEnabled();
    await page.screenshot({ path: `.ui-artifacts/task422-title-chat-${width}.png` });
    const response = page.waitForResponse(response => response.url().endsWith(`/api/news/${itemId}/agent/jobs`) && response.request().method() === 'POST');
    await page.locator('#agentSendBtn').click();
    const sentResponse = await response;
    const sent = await sentResponse.json();
    await expect(input(page)).toHaveValue('');
    await input(page).fill('保留下一轮草稿');
    await page.locator('#agentDockToggleBtn').click();
    if (width < 920) await page.locator('#mobileReaderBackBtn').click();
    await page.locator('[data-id="ui-063"]').click();
    await complete(request, sentResponse, '标题聊天的隔离研究回复');
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await expect(page.locator('#agentNotificationsBtn')).toHaveClass(/has-unread/);
    await page.locator('#agentNotificationsBtn').click();
    await page.locator(`[data-open-notice="${sent.job_id}"]`).click();
    await expect(stream(page)).toContainText('标题聊天的隔离研究回复');
    await expect(page.locator('#agentContext')).toContainText('尚未提供全文');
    await expect(input(page)).toHaveValue('保留下一轮草稿');
    await expect.poll(async () => (await notices(request)).length).toBe(0);
    expect((await session(request)).session.id).toBe(sent.session.id);
    expect(fetches).toBe(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
    await page.screenshot({ path: `.ui-artifacts/task422-title-reply-${width}.png` });
  });
}

test('full text arriving during a title conversation retains history and title scope until a new session', async ({ page, request }) => {
  await page.route(`**/api/news/${itemId}/detail`, async route => {
    const response = await route.fetch();
    const json = await response.json();
    if (!json.detail?.content) json.detail_status = 'running';
    await route.fulfill({ response, json });
  });
  await start(page);
  await input(page).fill('首轮标题问题');
  const response = page.waitForResponse(response => response.url().endsWith(`/api/news/${itemId}/agent/jobs`) && response.request().method() === 'POST');
  await page.locator('#agentSendBtn').click();
  const submitted = await complete(request, await response, '保留的标题会话回答');
  await expect(stream(page)).toContainText('保留的标题会话回答');
  await input(page).fill('正文到达时的下一轮草稿');
  await request.post('/__test/agent-article', { data: { item_id: itemId, missing: false } });
  await expect(page.locator('#articleMainTitle')).toBeVisible();
  await expect(page.locator('#agentStatus')).toHaveText('资料已更新，可新建会话读取最新内容', { timeout: 7000 });
  await expect(page.locator('#agentContext')).toContainText('尚未提供全文');
  await expect(stream(page)).toContainText('保留的标题会话回答');
  await expect(input(page)).toHaveValue('正文到达时的下一轮草稿');
  expect((await session(request)).session.id).toBe(submitted.session.id);
  await page.locator('#agentNewBtn').click();
  await answerConfirm(page);
  await expect(page.locator('#agentContext')).toBeHidden();
  await expect(input(page)).toHaveValue('正文到达时的下一轮草稿');
  expect((await session(request)).session.context_level).toBe('full_detail');
});
