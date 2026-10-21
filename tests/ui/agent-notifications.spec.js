import { test, expect } from '@playwright/test';
import { chooseTheme } from './helpers.js';

const bell = page => page.locator('#agentNotificationsBtn');
const panel = page => page.locator('#agentNotificationsPanel');
const rows = page => page.locator('[data-open-notice]');
async function complete(request, data = {}) {
  const response = await request.post('/__test/agent-complete', { data });
  expect(response.ok()).toBeTruthy();
  return response.json();
}
async function snapshot(request) { return (await request.get('/api/agent/notifications')).json(); }
async function wake(page) { await page.evaluate(() => window.dispatchEvent(new Event('focus'))); }
async function start(page, width = 1440) {
  await page.setViewportSize({ width, height: 900 });
  await page.goto('/');
  await page.bringToFront();
  await expect(page.locator('#feedCardList .news-card').first()).toBeVisible();
  await expect(bell(page)).toBeVisible();
}
async function dropdown(page) {
  if (!(await panel(page).isVisible())) await bell(page).click();
  else await wake(page);
}
async function openNotice(page, id) {
  await dropdown(page);
  await page.locator(`[data-open-notice="${id}"]`).click();
}
test.beforeEach(async ({ request }) => {
  await request.post('/__test/reset-agent');
  await request.post('/__test/reset-reading');
});

test('collapsed/off-list reply survives reload and opens its exact reply without changing article flags', async ({ page, request }) => {
  await start(page);
  const before = (await (await request.get('/api/news/ui-000/detail')).json());
  const result = await complete(request, { item_id: 'ui-000', answer: '离开页面后的完整研究结论' });
  await wake(page);
  await expect(bell(page)).toHaveClass(/has-unread/);
  await dropdown(page);
  expect((await snapshot(request)).notifications).toHaveLength(1);
  await page.reload();
  await expect(bell(page)).toHaveClass(/has-unread/);
  await expect(page.locator('[data-id="ui-000"]')).toHaveCount(0);
  await page.locator('#feedSearchInput').fill('找不到的新闻筛选');
  await openNotice(page, result.job_id);
  await expect(page.locator('#agentDock')).not.toHaveClass(/minimized/);
  await expect(page.locator('#agentChatStream')).toContainText('离开页面后的完整研究结论');
  await expect.poll(async () => (await snapshot(request)).notifications.length).toBe(0);
  const after = (await (await request.get('/api/news/ui-000/detail')).json());
  for (const key of ['read_at', 'important_at', 'favorite_at', 'read_later_at']) expect(after[key]).toBe(before[key]);
});

test('notification selects the specified session instead of the newer default', async ({ page, request }) => {
  await start(page);
  const old = await complete(request, { item_id: 'ui-000', answer: '指定的旧会话回答' });
  const latest = await complete(request, { item_id: 'ui-000', new_session: true, answer: '默认的新会话回答' });
  await openNotice(page, old.job_id);
  await expect(page.locator('#agentChatStream')).toContainText('指定的旧会话回答');
  await expect(page.locator('#agentChatStream')).not.toContainText('默认的新会话回答');
  await expect.poll(async () => (await snapshot(request)).notifications.map(row => row.id)).toEqual([latest.job_id]);
  expect((await (await request.get('/api/news/ui-000/agent/session')).json()).session.id).toBe(latest.session_id);
});

test('quick notification switches cannot display or acknowledge an older delayed target', async ({ page, request }) => {
  await start(page);
  const a = await complete(request, { item_id: 'ui-000', answer: '延迟目标A' });
  const b = await complete(request, { item_id: 'ui-001', answer: '最新目标B' });
  const data = await snapshot(request);
  const target = await (await request.get(`/api/agent/notifications/${a.job_id}/target?db_scope=${data.db_scope}`)).json();
  let release, entered;
  const gate = new Promise(resolve => { release = resolve; });
  const entry = new Promise(resolve => { entered = resolve; });
  await page.route(`**/api/agent/notifications/${a.job_id}/target?*`, async route => {
    entered(); await gate;
    await route.fulfill({ json: target }).catch(() => {});
  });
  await dropdown(page);
  await page.locator(`[data-open-notice="${a.job_id}"]`).click();
  await entry;
  await page.locator(`[data-open-notice="${b.job_id}"]`).click();
  await expect(page.locator('#agentChatStream')).toContainText('最新目标B');
  release();
  await expect.poll(async () => (await snapshot(request)).notifications.map(row => row.id)).toEqual([a.job_id]);
  await expect(page.locator('#agentChatStream')).not.toContainText('延迟目标A');
});

test('leaving an article keeps the Agent input draft during notification navigation', async ({ page, request }) => {
  await start(page);
  await page.locator('#agentDockToggleBtn').click();
  await page.locator('#agentPromptInput').fill('尚未发送的下一轮问题');
  const result = await complete(request, { item_id: 'ui-000', answer: '另一篇新闻的回答' });
  await openNotice(page, result.job_id);
  await expect(page.locator('#agentChatStream')).toContainText('另一篇新闻的回答');
  await page.locator('[data-id="ui-064"] .news-card-title').click();
  await expect(page.locator('#agentPromptInput')).toHaveValue('尚未发送的下一轮问题');
});

test('streaming completes in the background; old scroll position and opening the list do not acknowledge', async ({ page, request }) => {
  await start(page);
  const initial = await complete(request, { item_id: 'ui-064', answer: Array.from({ length: 35 }, (_, i) => `既有研究段落${i}。`).join('\n\n') });
  await openNotice(page, initial.job_id);
  await expect.poll(async () => (await snapshot(request)).notifications.length).toBe(0);
  await page.locator('#agentChatStream').evaluate(node => node.scrollTop = 0);
  await page.locator('#agentPromptInput').fill('新一轮研究');
  await page.locator('#agentSendBtn').click();
  const job = (await (await request.get('/api/news/ui-064/agent/session')).json()).jobs.at(-1);
  expect(job.status).toBe('queued');
  await complete(request, { item_id: 'ui-064', job_id: job.id, answer: '刚完成的新回复，尚未滚动查看。' });
  await expect(page.locator(`[data-job-id="${job.id}"][data-role="assistant"]`)).toContainText('刚完成的新回复');
  await wake(page); await dropdown(page);
  await expect(bell(page)).toHaveClass(/has-unread/);
  expect((await snapshot(request)).notifications.map(row => row.id)).toContain(job.id);
  await bell(page).click();
  await page.locator('#agentChatStream').evaluate((stream, id) => {
    const reply = stream.querySelector(`[data-job-id="${id}"][data-role="assistant"]`);
    stream.scrollTop += reply.getBoundingClientRect().top - stream.getBoundingClientRect().bottom + 12;
  }, job.id);
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  expect((await snapshot(request)).notifications.map(row => row.id)).toContain(job.id);
  await page.locator('#agentChatStream').evaluate(node => node.scrollTop = node.scrollHeight);
  await expect.poll(async () => (await snapshot(request)).notifications.length).toBe(0);
});

test('a non-foreground window cannot acknowledge a displayed reply until focus returns', async ({ page, request, context }) => {
  await start(page);
  const initial = await complete(request, { item_id: 'ui-064', answer: '开始研究' });
  await openNotice(page, initial.job_id);
  await expect.poll(async () => (await snapshot(request)).notifications.length).toBe(0);
  await page.locator('#agentPromptInput').fill('请继续'); await page.locator('#agentSendBtn').click();
  const job = (await (await request.get('/api/news/ui-064/agent/session')).json()).jobs.at(-1);
  const other = await context.newPage(); await other.goto('about:blank'); await other.bringToFront();
  await complete(request, { item_id: 'ui-064', job_id: job.id, answer: '切走窗口后完成' });
  await expect(page.locator(`[data-job-id="${job.id}"][data-role="assistant"]`)).toContainText('切走窗口后完成');
  expect((await snapshot(request)).notifications.map(row => row.id)).toContain(job.id);
  await page.bringToFront();
  await expect.poll(async () => (await snapshot(request)).notifications.length).toBe(0);
  await other.close();
});

test('same conversation groups unread rounds and bulk action confirms only its captured snapshot', async ({ page, request }) => {
  await start(page);
  await complete(request, { item_id: 'ui-000', answer: '第一轮' });
  await complete(request, { item_id: 'ui-000', answer: '第二轮' });
  await dropdown(page);
  await expect(rows(page)).toHaveCount(1);
  await expect(page.locator('#agentNotificationsList')).toContainText('2条待看回复');
  let release, entered;
  const gate = new Promise(resolve => { release = resolve; });
  const entry = new Promise(resolve => { entered = resolve; });
  await page.route('**/api/agent/notifications/read', async route => { entered(); await gate; await route.continue(); });
  await page.locator('#agentNotificationsReadAll').click(); await entry;
  const later = await complete(request, { item_id: 'ui-000', answer: '批量请求期间新到的第三轮' });
  await wake(page);
  await expect(page.locator('#agentNotificationsList')).toContainText('3条待看回复');
  await expect(page.locator('#agentNotificationsReadAll')).toBeDisabled();
  release();
  await expect.poll(async () => (await snapshot(request)).notifications.map(row => row.id)).toEqual([later.job_id]);
});

test('failed and expired targets stay actionable; preview is plain text and ignore is explicit', async ({ page, request }) => {
  await start(page);
  const expired = await complete(request, { item_id: 'ui-000', expire: true });
  await openNotice(page, expired.job_id);
  await expect(page.locator('#agentNotificationsStatus')).toContainText('会话已过期');
  expect((await snapshot(request)).notifications).toHaveLength(1);
  await page.locator('[data-dismiss-notice]').click();
  await expect.poll(async () => (await snapshot(request)).notifications.length).toBe(0);
  const failed = await complete(request, { item_id: 'ui-001', status: 'failed', answer: '' });
  await wake(page);
  await expect(page.locator('#agentNotificationsList')).toContainText('本轮未完成');
  await expect(page.locator('#agentNotificationsList')).toContainText('身份验证失败');
  await page.locator(`[data-open-notice="${failed.job_id}"]`).click();
  await expect(page.locator('#agentChatStream')).toContainText('身份验证失败');
  await expect.poll(async () => (await snapshot(request)).notifications.length).toBe(0);
  await dropdown(page);
  await complete(request, { item_id: 'ui-002', answer: '<img src=x onerror="window.__noticeXss=true">文本' });
  await wake(page);
  await expect(page.locator('#agentNotificationsList')).toContainText('<img');
  await expect(page.locator('#agentNotificationsList img')).toHaveCount(0);
  expect(await page.evaluate(() => window.__noticeXss)).toBeUndefined();
});

for (const width of [1440, 390]) for (const theme of ['light', 'dark', 'eink']) {
  test(`notification popover and keyboard controls fit ${width}px ${theme}`, async ({ page, request }) => {
    await start(page, width); await chooseTheme(page, theme);
    await complete(request, { item_id: 'ui-000', answer: '这是通知摘要，支持窄屏换行。' });
    await dropdown(page); await expect(rows(page)).toHaveCount(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const box = await panel(page).boundingBox();
    expect(box.x).toBeGreaterThanOrEqual(0); expect(box.x + box.width).toBeLessThanOrEqual(width);
    expect(box.y).toBeGreaterThan(0);
    await page.keyboard.press('Escape'); await expect(panel(page)).toBeHidden();
    await expect(bell(page)).toBeFocused();
    await page.keyboard.press('Enter'); await expect(panel(page)).toBeVisible();
    if (theme === 'eink') await expect(bell(page)).toHaveCSS('border-top-color', 'rgb(0, 0, 0)');
  });
}


test('manual navigation while a target is loading preserves its notification', async ({ page, request }) => {
  await start(page);
  const result = await complete(request, { item_id: 'ui-000', answer: '不应覆盖手动选择' });
  let release, entered;
  const gate = new Promise(resolve => { release = resolve; });
  const entry = new Promise(resolve => { entered = resolve; });
  await page.route(`**/api/agent/notifications/${result.job_id}/target?*`, async route => { entered(); await gate; await route.continue(); });
  await dropdown(page); await rows(page).click(); await entry;
  await page.locator('[data-id="ui-063"] .news-card-title').click();
  release();
  await expect(page.locator('#agentChatStream')).not.toContainText('不应覆盖手动选择');
  await expect(page.locator('[data-id="ui-063"]')).toHaveClass(/is-active/);
  expect((await snapshot(request)).notifications.map(row => row.id)).toContain(result.job_id);
});

test('notification jump from summaries does not prepare or create another conversation', async ({ page, request }) => {
  await start(page);
  const result = await complete(request, { item_id: 'ui-000', answer: '从其他工作区回到此轮回复' });
  await page.locator('#navSummariesBtn').click();
  const preparations = [];
  page.on('request', req => { if (req.method() === 'POST' && req.url().includes('/agent/session')) preparations.push(req.url()); });
  await openNotice(page, result.job_id);
  await expect(page.locator('#agentChatStream')).toContainText('从其他工作区回到此轮回复');
  expect(preparations).toEqual([]);
});


test('mobile notification opens the reply with a reachable bell and unchanged bottom navigation', async ({ page, request }) => {
  await start(page, 390);
  const result = await complete(request, { item_id: 'ui-000', answer: '手机一步到位回复' });
  await openNotice(page, result.job_id);
  await expect(page.locator('#agentChatStream')).toContainText('手机一步到位回复');
  await expect.poll(async () => (await snapshot(request)).notifications.length).toBe(0);
  await expect(bell(page)).toBeVisible();
  await expect(bell(page).locator('svg')).toBeVisible();
  await bell(page).click(); await expect(panel(page)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(page.locator('.mobile-bottom-nav button')).toHaveCount(3);
});


test('target success then detail 503 preserves notice after closing; successful retry opens exact session and confirms', async ({ page, request }) => {
  await start(page);
  const result = await complete(request, { item_id: 'ui-000', answer: '详情加载成功前必须保留的回复' });
  const latest = await complete(request, { item_id: 'ui-000', new_session: true, answer: '不能替换指定会话的较新回复' });
  await page.route('**/api/news/ui-000/detail', route => route.fulfill({ status: 503, json: { ok: false, error: 'fixture_detail_failed' } }));
  await openNotice(page, result.job_id);
  await expect(page.locator('#retryReaderBtn')).toBeVisible();
  await expect(page.locator('#agentNotificationsStatus')).toContainText('通知已保留');
  await expect(page.locator('#agentChatStream')).not.toContainText('详情加载成功前必须保留的回复');
  await bell(page).click(); await expect(panel(page)).toBeHidden();
  await wake(page);
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  expect((await snapshot(request)).notifications.map(row => row.id)).toContain(result.job_id);
  await page.unroute('**/api/news/ui-000/detail');
  await page.locator('#retryReaderBtn').click();
  await expect(page.locator('#agentChatStream')).toContainText('详情加载成功前必须保留的回复');
  await expect(page.locator('#agentChatStream')).not.toContainText('不能替换指定会话的较新回复');
  await expect.poll(async () => (await snapshot(request)).notifications.map(row => row.id)).toEqual([latest.job_id]);
});
