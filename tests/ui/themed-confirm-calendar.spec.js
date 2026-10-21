import { test, expect } from '@playwright/test';
import { answerConfirm, chooseTheme, openNavigation, openSettings } from './helpers.js';
const modal = page => page.locator('#appConfirmDialog');
const errorPanel = page => page.locator('#upstreamErrorsPanel');
const calendar = page => page.locator('#upstreamCalendar');
async function start(page, width = 1440) {
  await page.setViewportSize({ width, height: 900 });
  await page.goto('/'); await page.bringToFront();
  await expect(page.locator('#feedCardList .news-card').first()).toBeVisible();
}
async function system(page) {
  await openSettings(page); await page.locator('[data-settings-tab="system"]').click();
}
async function fakeErrors(page, today = '2024-03-01') {
  const days = [];
  await page.route('**/api/error-stats?*', async route => {
    const params = new URL(route.request().url()).searchParams;
    const day = params.get('day') || today;
    if (params.get('details') === '1') days.push(day);
    await route.fulfill({ json: { ok: true, today, day, days: [{ date: day, groups: [{ time: '12:00:00', entries: [{ label: '测试来源', error: `所选日期 ${day}` }] }] }] } });
  });
  return days;
}
async function enterDate(page, value) {
  await page.locator('#upstreamErrorDate').fill(value);
  await page.locator('#upstreamErrorDate').press('Enter');
}
test.beforeEach(async ({ request }) => {
  await request.post('/__test/reset-agent'); await request.post('/__test/reset-reading');
});

for (const width of [1440, 390]) for (const theme of ['light', 'dark', 'eink']) {
  test(`theme confirmation and calendar support focus, cancel and bounded layout at ${width} ${theme}`, async ({ page }) => {
    await fakeErrors(page); await start(page, width); await chooseTheme(page, theme);
    let native = 0; page.on('dialog', async dialog => { native++; await dialog.dismiss(); });
    await page.locator('#markAllReadBtn').click();
    await expect(modal(page)).toBeVisible();
    await expect(page.locator('#appConfirmCancel')).toBeFocused();
    for (let n = 0; n < 5; n++) { await page.keyboard.press('Tab'); expect(await modal(page).evaluate(node => node.contains(document.activeElement))).toBe(true); }
    expect(await modal(page).evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
    await page.screenshot({ path: `.ui-artifacts/task426-confirm-${width}-${theme}.png` });
    await page.keyboard.press('Escape');
    await expect(modal(page)).toBeHidden(); await expect(page.locator('#markAllReadBtn')).toBeFocused();
    expect(native).toBe(0);
    await page.locator('#upstreamErrorsBtn').click();
    await page.locator('#upstreamCalendarBtn').click();
    await expect(calendar(page)).toBeVisible();
    await expect(page.locator('[data-calendar-day="2024-03-01"]')).toBeFocused();
    await expect(page.locator('[data-calendar-day="2024-03-01"]')).toHaveAttribute('aria-current', 'date');
    const box = await calendar(page).boundingBox();
    expect(box.x).toBeGreaterThanOrEqual(12); expect(box.x + box.width).toBeLessThanOrEqual(width - 12);
    expect(await calendar(page).evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
    await page.screenshot({ path: `.ui-artifacts/task426-calendar-${width}-${theme}.png` });
    await page.keyboard.press('Escape'); await expect(calendar(page)).toBeHidden();
    await expect(errorPanel(page)).toBeVisible(); await expect(page.locator('#upstreamCalendarBtn')).toBeFocused();
    await page.keyboard.press('Escape'); await expect(errorPanel(page)).toBeHidden();
  });
}

test('confirmation is plain text, one at a time, and external close cancels', async ({ page }) => {
  await start(page);
  await page.evaluate(async () => {
    const { confirmAction } = await import('/static/confirm-dialog.js');
    window.firstConfirmation = confirmAction({ title: '<img src=x onerror=alert(1)>', message: '<script>bad()</script>', action: '执行测试' }).then(value => { window.confirmationResult = value; });
    window.secondConfirmation = await confirmAction({ title: '第二个', message: '不应替换', action: '执行' });
  });
  await expect(page.locator('#appConfirmTitle')).toHaveText('<img src=x onerror=alert(1)>');
  await expect(modal(page).locator('img,script')).toHaveCount(0);
  expect(await page.evaluate(() => window.secondConfirmation)).toBe(false);
  await page.evaluate(() => document.querySelector('#appConfirmDialog').close());
  await expect.poll(() => page.evaluate(() => window.confirmationResult)).toBe(false);
  await page.evaluate(async () => {
    const { confirmAction } = await import('/static/confirm-dialog.js');
    window.confirmationResult = null;
    confirmAction({ title: '点击外部取消', message: '外部点击不能执行', action: '执行测试' }).then(value => { window.confirmationResult = value; });
  });
  await expect(modal(page)).toBeVisible(); await page.mouse.click(1, 1);
  await expect.poll(() => page.evaluate(() => window.confirmationResult)).toBe(false);
});

test('all-read cancels with zero writes and refuses a changed scope after confirmation opened', async ({ page }) => {
  await start(page);
  let writes = 0;
  page.on('request', req => { if (req.method() === 'POST' && /\/mark-(all-read|read-by-ids)$/.test(new URL(req.url()).pathname)) writes++; });
  await page.locator('#markAllReadBtn').click(); await answerConfirm(page, false);
  expect(writes).toBe(0);
  await page.locator('#markAllReadBtn').click(); await expect(modal(page)).toBeVisible();
  await page.locator('[data-source="reuters"]').evaluate(node => node.click());
  await answerConfirm(page);
  await expect(page.locator('#toast')).toContainText('新闻范围已变化'); expect(writes).toBe(0);
});

test('new Agent session cancels, guards article changes, and confirms only once', async ({ page, request }) => {
  await request.post('/__test/agent-complete', { data: { item_id: 'ui-064' } });
  await start(page); await page.locator('#agentDockToggleBtn').click();
  await expect(page.locator('#agentChatStream')).toContainText('通知样例');
  let resets = 0;
  page.on('request', req => { if (req.method() === 'POST' && req.url().endsWith('/agent/session') && req.postDataJSON().new_session) resets++; });
  await page.locator('#agentNewBtn').click(); await answerConfirm(page, false); expect(resets).toBe(0);
  await page.locator('#agentNewBtn').click(); await expect(modal(page)).toBeVisible();
  await page.locator('[data-id="ui-063"]').evaluate(node => node.click());
  await answerConfirm(page); await expect(page.locator('#toast')).toContainText('会话已变化'); expect(resets).toBe(0);
  await page.locator('[data-id="ui-064"]').click();
  if (await page.locator('#agentDock').evaluate(node => node.classList.contains('minimized'))) await page.locator('#agentDockToggleBtn').click();
  await page.locator('#agentNewBtn').click(); await expect(modal(page)).toBeVisible();
  await page.locator('#appConfirmAccept').evaluate(node => { node.dispatchEvent(new MouseEvent('click')); node.dispatchEvent(new MouseEvent('click')); });
  await expect.poll(() => resets).toBe(1); await expect(page.locator('#agentChatStream')).not.toContainText('通知样例');
});

test('idea delete guards edits while waiting, cancels without requests and removes only its captured record', async ({ page, request }) => {
  const idea = (await (await request.post('/api/standalone-ideas', { data: { title: '确认删除目标', note: '保留原文' } })).json()).idea;
  let deletes = 0;
  page.on('request', req => { if (req.method() === 'DELETE' && req.url().includes('/api/standalone-ideas/')) deletes++; });
  await start(page); await openNavigation(page); await page.locator('#navNotesBtn').click(); await page.locator(`[data-idea-id="${idea.idea_id}"]`).click();
  await page.locator('#deleteIdeaBtn').click(); await answerConfirm(page, false); expect(deletes).toBe(0);
  await page.locator('#deleteIdeaBtn').click(); await expect(modal(page)).toBeVisible();
  await page.locator('#ideaTitle').evaluate(node => { node.value = '等待时的新标题'; node.dispatchEvent(new Event('input', { bubbles: true })); });
  await answerConfirm(page); await expect(page.locator('#toast')).toContainText('想法已变化'); expect(deletes).toBe(0);
  await page.locator('#deleteIdeaBtn').click(); await answerConfirm(page);
  await expect.poll(() => deletes).toBe(1); await expect(page.locator(`[data-idea-id="${idea.idea_id}"]`)).toHaveCount(0);
});

test('clear sessions and secret delete cancel, refuse changed targets, then execute once', async ({ page, request }) => {
  const saved = (await (await request.get('/api/settings')).json());
  saved.api_status.deepseek = { configured: true, masked_key: 'fixture••••' };
  let changed = false, clears = 0, secrets = 0;
  await page.route('**/api/settings', route => route.fulfill({ json: changed ? { ...saved, database: { ...saved.database, path: '/fixture/different.sqlite3' } } : saved }));
  await page.route('**/api/agent/sessions', route => { clears++; return route.fulfill({ json: { ok: true } }); });
  await page.route('**/api/settings/secrets/deepseek', route => { secrets++; return route.fulfill({ json: { ...saved, api_status: { ...saved.api_status, deepseek: { configured: false } } } }); });
  await start(page); await openSettings(page);
  await page.locator('[data-settings-tab="models"]').click();
  await page.locator('#clearSessionsBtn').click(); await answerConfirm(page, false); expect(clears).toBe(0);
  await page.locator('#clearSessionsBtn').click(); await expect(modal(page)).toBeVisible(); changed = true; await answerConfirm(page);
  await expect(page.locator('#toast')).toContainText('新闻库已变化'); expect(clears).toBe(0);
  changed = false; await page.locator('#clearSessionsBtn').click(); await answerConfirm(page); await expect.poll(() => clears).toBe(1);
  await page.locator('[data-settings-tab="deepseek"]').click();
  await page.locator('#deleteSecretBtn').click(); await answerConfirm(page, false); expect(secrets).toBe(0);
  await page.locator('#deleteSecretBtn').click(); await expect(modal(page)).toBeVisible();
  await page.locator('#editSecretBtn').evaluate(node => node.click());
  await answerConfirm(page); await expect(page.locator('#toast')).toContainText('密钥设置已变化'); expect(secrets).toBe(0);
  await page.locator('#cancelSecretBtn').click(); await page.locator('#deleteSecretBtn').click(); await answerConfirm(page);
  await expect.poll(() => secrets).toBe(1);
});

test('update confirmation rechecks its target and cancellation never posts', async ({ page }) => {
  let target = 'v0.1.2', applied = 0;
  await page.route('**/api/update/status', route => route.fulfill({ json: applied ? { run_id: 'test-run', status: 'failed' } : { status: 'idle' } }));
  await page.route('**/api/update/check?*', route => route.fulfill({ json: { enabled: true, current_version: 'v0.1.1', available: true, can_update: true, check_token: target, latest: { version: target, commit: 'b'.repeat(40), tag: target } } }));
  await page.route('**/api/update/apply', route => { applied++; return route.fulfill({ status: 202, json: { run_id: 'test-run' } }); });
  await start(page); await system(page); await page.locator('#checkUpdateBtn').click();
  await page.locator('#applyUpdateBtn').click(); await answerConfirm(page, false); expect(applied).toBe(0);
  await page.locator('#applyUpdateBtn').click(); await expect(modal(page)).toBeVisible();
  target = 'v0.1.3'; await page.locator('#checkUpdateBtn').evaluate(node => node.click());
  await expect(page.locator('#updateConclusion')).toContainText('v0.1.3'); await answerConfirm(page);
  await expect(page.locator('#updateConclusion')).toContainText('已变化'); expect(applied).toBe(0);
  await page.locator('#checkUpdateBtn').click(); await page.locator('#applyUpdateBtn').click(); await answerConfirm(page);
  await expect.poll(() => applied).toBe(1);
});

test('calendar validates input without queries, handles leap/month boundaries and closes with its parent', async ({ page }) => {
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  const days = await fakeErrors(page); await start(page); await page.locator('#upstreamErrorsBtn').click();
  await expect(page.locator('#upstreamErrorsContent')).toContainText('2024-03-01');
  const baseline = days.length;
  for (const value of ['2023-02-29', '2024-02-30', '2024-13-01', '2024-03-02', 'not-a-date']) {
    await enterDate(page, value);
    await expect(page.locator('#upstreamDateError')).not.toBeEmpty();
    await page.locator('#upstreamPreviousDayBtn').click();
    expect(days).toHaveLength(baseline);
    await expect(page.locator('#upstreamErrorsContent')).toContainText('2024-03-01');
  }
  await enterDate(page, '2024-02-29'); await expect(page.locator('#upstreamErrorsContent')).toContainText('2024-02-29');
  await page.locator('#upstreamCalendarBtn').click();
  await expect(page.locator('[data-calendar-day="2024-02-29"]')).toBeFocused();
  await page.keyboard.press('ArrowRight'); await expect(page.locator('[data-calendar-day="2024-03-01"]')).toBeFocused();
  const beforeBrowse = days.length;
  await page.locator('[data-calendar-month="-1"]').click(); expect(days).toHaveLength(beforeBrowse);
  await page.locator('[data-calendar-month="1"]').click(); expect(days).toHaveLength(beforeBrowse);
  await expect(page.locator('[data-calendar-day="2024-03-02"]')).toBeDisabled();
  await page.locator('[data-calendar-day="2024-03-01"]').focus(); await page.keyboard.press('Enter');
  await expect(calendar(page)).toBeHidden(); await expect(page.locator('#upstreamErrorsContent')).toContainText('2024-03-01');
  await page.locator('#upstreamCalendarBtn').click(); await page.locator('#agentNotificationsBtn').click();
  await expect(calendar(page)).toBeHidden(); await expect(errorPanel(page)).toBeHidden();
  expect(errors).toEqual([]);
});

test('discard-and-refresh cancels, guards newer settings, and preserves the browser leave warning', async ({ page }) => {
  const commit = 'c'.repeat(40);
  let servingTarget = false;
  await page.addInitScript(() => localStorage.setItem('newsreader:update-run', 'restored-run'));
  await page.route('**/api/version', route => route.fulfill({ json: { version: servingTarget ? 'v0.1.2' : 'v0.1.1', commit } }));
  await page.route('**/api/update/status', route => route.fulfill({ json: { run_id: 'restored-run', status: 'healthy', version: 'v0.1.2', commit } }));
  await start(page); await expect(page.locator('.brand-version')).toHaveText('v0.1.1');
  servingTarget = true; await system(page); await expect(page.locator('#refreshUpdateBtn')).toBeVisible();
  await page.locator('[data-settings-tab="deepseek"]').click();
  await page.locator('input[name="llm.translation.model"]').fill('unsaved-model');
  await page.locator('[data-settings-tab="system"]').click();
  let navigations = 0, native = 0;
  page.on('framenavigated', frame => { if (frame === page.mainFrame()) navigations++; });
  page.on('dialog', async dialog => { expect(dialog.type()).toBe('beforeunload'); native++; await dialog.accept(); });
  await page.locator('#refreshUpdateBtn').click(); await answerConfirm(page, false); expect(navigations).toBe(0);
  await page.locator('#refreshUpdateBtn').click(); await expect(modal(page)).toBeVisible();
  await page.locator('input[name="llm.translation.model"]').evaluate(node => { node.value = 'newer-unsaved-model'; node.dispatchEvent(new Event('input', { bubbles: true })); });
  await answerConfirm(page); await expect(page.locator('#updateConclusion')).toContainText('已变化'); expect(navigations).toBe(0);
  await page.locator('#refreshUpdateBtn').click();
  const loaded = page.waitForEvent('domcontentloaded'); await answerConfirm(page); await loaded;
  expect(navigations).toBe(1); expect(native).toBe(1);
});

for (const timezoneId of ['America/Los_Angeles', 'Pacific/Kiritimati']) {
  test(`calendar preserves server date and DST day boundaries in ${timezoneId}`, async ({ browser, baseURL }) => {
    const context = await browser.newContext({ baseURL, timezoneId });
    try {
      const page = await context.newPage();
      const days = await fakeErrors(page, '2024-03-11');
      await start(page); await page.locator('#upstreamErrorsBtn').click();
      await enterDate(page, '2024-03-10'); await expect(page.locator('#upstreamErrorsContent')).toContainText('2024-03-10');
      await page.locator('#upstreamCalendarBtn').click();
      await page.keyboard.press('ArrowLeft'); await page.keyboard.press('Enter');
      await expect(page.locator('#upstreamErrorDate')).toHaveValue('2024-03-09');
      await expect(page.locator('#upstreamErrorsContent')).toContainText('2024-03-09');
      expect(days.at(-1)).toBe('2024-03-09');
    } finally { await context.close(); }
  });
}
