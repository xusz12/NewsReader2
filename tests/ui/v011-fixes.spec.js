import { test, expect } from '@playwright/test';
import { chooseTheme, openSettings } from './helpers.js';

async function start(page, width = 1440) {
  await page.setViewportSize({ width, height: 900 });
  await page.goto('/'); await page.bringToFront();
  await expect(page.locator('#feedCardList .news-card').first()).toBeVisible();
}
async function agent(page, width = 1440) {
  await start(page, width);
  await page.locator('[data-id="ui-064"]').click();
  await page.locator('#agentDockToggleBtn').click();
  await expect(page.locator('#agentSendBtn')).toBeEnabled();
}
test.beforeEach(async ({ request }) => { await request.post('/__test/reset-agent'); await request.post('/__test/reset-reading'); });

for (const width of [1440, 390]) for (const theme of ['light', 'dark', 'eink']) {
  test(`update conclusion and ghost refresh use clear theme controls at ${width} ${theme}`, async ({ page }) => {
    let release;
    await page.route('**/api/update/status', route => route.fulfill({ json: { ok: true, status: 'idle' } }));
    await page.route('**/api/update/check?*', async route => { await new Promise(resolve => { release = resolve; }); await route.fulfill({ json: {
      ok: true, enabled: true, current_version: 'v0.1.1', available: true, can_update: false,
      latest: { version: 'v0.1.2', commit: 'b'.repeat(40) }, blockers: ['dirty_worktree'], checked_at: 1792555200,
    } }); });
    await start(page, width); await chooseTheme(page, theme);
    const refresh = page.locator('#refreshIndexBtn');
    await page.mouse.move(0, 0);
    await expect(refresh).toHaveCSS('border-top-color', 'rgba(0, 0, 0, 0)');
    await expect(refresh).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
    await refresh.hover(); await expect(refresh).not.toHaveCSS('border-top-color', 'rgba(0, 0, 0, 0)');
    await page.mouse.move(0, 0); await refresh.focus();
    await page.keyboard.press('Tab'); await page.keyboard.press('Shift+Tab');
    await expect(refresh).toBeFocused();
    await expect(refresh).not.toHaveCSS('border-top-color', 'rgba(0, 0, 0, 0)');
    await openSettings(page); await page.locator('[data-settings-tab="system"]').click();
    await page.locator('#checkUpdateBtn').click();
    await expect(page.locator('#updateResult')).toHaveAttribute('data-state', 'checking');
    await expect(page.locator('#updateConclusion')).toHaveText('正在检查最新稳定版本…');
    await expect(page.locator('#updateCheckedAt')).toBeHidden();
    await expect.poll(() => !!release).toBe(true); release();
    await expect(page.locator('#updateResult')).toHaveAttribute('data-state', 'blocked');
    await expect(page.locator('#updateConclusion')).toHaveText('发现新版本 v0.1.2，暂时无法更新');
    await expect(page.locator('#updateExplanation')).toContainText('未提交的修改');
    await expect(page.locator('#updateCheckedAt')).toContainText('检查时间');
    await expect(page.locator('#applyUpdateBtn')).toBeHidden();
    await expect(page.locator('#updateConclusion')).toHaveCSS('font-weight', '600');
    const sizes = await page.evaluate(() => ({ main: getComputedStyle(document.querySelector('#updateConclusion')).fontSize, body: getComputedStyle(document.documentElement).getPropertyValue('--fs-body').trim(), secondary: getComputedStyle(document.querySelector('#updateExplanation')).fontSize }));
    expect(sizes.main).toBe(sizes.body);
    expect(parseFloat(sizes.main)).toBeGreaterThan(parseFloat(sizes.secondary));
    expect(await page.locator('#updateResult').evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
    await page.screenshot({ path: `.ui-artifacts/task425-update-${width}-${theme}.png` });
  });
}

test('error dropdown is nonmodal, mutually exclusive, keyboard reachable and ignores late close response', async ({ page }) => {
  let hold = false, release;
  await page.route('**/api/error-stats?*', async route => {
    if (hold && new URL(route.request().url()).searchParams.get('details') === '1') await new Promise(resolve => { release = resolve; });
    await route.fulfill({ json: { ok: true, day: '2026-10-21', today: '2026-10-21', days: [] } });
  });
  await start(page);
  const entry = page.locator('#upstreamErrorsBtn'), panel = page.locator('#upstreamErrorsPanel');
  await entry.focus(); await page.keyboard.press('ArrowDown');
  await expect(panel).toBeVisible(); await expect(page.locator('#upstreamErrorDate')).toBeFocused();
  expect(await panel.evaluate(node => node.tagName)).toBe('SECTION');
  await expect(panel).toHaveAttribute('aria-modal', 'false');
  await page.keyboard.press('Escape'); await expect(panel).toBeHidden(); await expect(entry).toBeFocused();
  await entry.click(); await entry.click(); await expect(panel).toBeHidden();
  await entry.click(); await page.locator('#agentNotificationsBtn').click();
  await expect(panel).toBeHidden(); await expect(page.locator('#agentNotificationsPanel')).toBeHidden();
  await page.locator('#agentNotificationsBtn').click();
  await expect(panel).toBeHidden(); await expect(page.locator('#agentNotificationsPanel')).toBeVisible();
  await entry.click(); await expect(panel).toBeVisible(); await expect(page.locator('#agentNotificationsPanel')).toBeHidden();
  // The first outside press only dismisses and restores focus to the entry.
  const outside = page.locator('#navFeedBtn');
  await outside.click();
  await expect(panel).toBeHidden(); await expect(entry).toBeFocused();
  hold = true; await entry.click();
  await expect.poll(() => !!release).toBe(true);
  await outside.click(); release();
  await expect(panel).toBeHidden(); await expect(entry).toBeFocused();
});

test('desktop multiline input grows, preserves line breaks and drafts, and protects IME and failed sends', async ({ page }) => {
  await agent(page);
  const input = page.locator('#agentPromptInput');
  await expect(input).toHaveAttribute('rows', '2');
  const initial = (await input.boundingBox()).height;
  const text = Array.from({ length: 40 }, (_, n) => `第${n}行研究材料与问题`).join('\n');
  await input.fill(text);
  await expect.poll(async () => (await input.boundingBox()).height).toBeGreaterThan(initial);
  expect((await input.boundingBox()).height).toBeLessThan(190);
  await expect(input).toHaveCSS('overflow-y', 'auto');
  await page.screenshot({ path: '.ui-artifacts/task425-desktop-input-long.png' });
  await input.evaluate(node => { node.scrollTop = node.scrollHeight; });
  expect(await input.evaluate(node => node.scrollTop)).toBeGreaterThan(0);
  await page.locator('#agentDockToggleBtn').click();
  await page.locator('[data-id="ui-063"]').click(); await page.locator('[data-id="ui-064"]').click();
  if (await page.locator('#agentDock').evaluate(node => node.classList.contains('minimized'))) await page.locator('#agentDockToggleBtn').click();
  await expect(input).toHaveValue(text);
  await input.fill('第一行'); await input.press('Shift+Enter'); await input.pressSequentially('第二行');
  await expect(input).toHaveValue('第一行\n第二行');
  let sent = 0;
  await page.route('**/api/news/*/agent/jobs', async route => { sent++; await route.fulfill({ status: 503, json: { ok: false, error: 'request_failed' } }); });
  await input.dispatchEvent('keydown', { key: 'Enter', keyCode: 229, bubbles: true });
  await input.dispatchEvent('compositionstart'); await input.press('Enter'); await input.dispatchEvent('compositionend');
  expect(sent).toBe(0);
  const draft = await input.inputValue();
  await input.press('ControlOrMeta+Enter');
  await expect.poll(() => sent).toBe(1);
  await expect(input).toHaveValue(draft);
  await expect(page.locator('#agentSendBtn')).toBeEnabled();
  await input.press('Enter');
  await expect.poll(() => sent).toBe(2);
  await expect(input).toHaveValue(draft);
  await page.screenshot({ path: '.ui-artifacts/task425-desktop-input.png' });
});

test('mobile Enter adds a line; visible keyboard viewport keeps long input and send reachable', async ({ page, request }) => {
  await agent(page, 390);
  const input = page.locator('#agentPromptInput');
  await expect(input).toHaveCSS('font-size', '16px');
  expect((await page.locator('#agentSendBtn').boundingBox()).y + (await page.locator('#agentSendBtn').boundingBox()).height).toBeLessThanOrEqual(900 - 56);
  await input.fill('手机第一行'); await input.press('Enter'); await input.pressSequentially('第二行');
  await expect(input).toHaveValue('手机第一行\n第二行');
  expect((await (await request.get('/api/news/ui-064/agent/session')).json()).jobs).toHaveLength(0);
  await input.fill('手机长问题，保留所有换行。\n'.repeat(35));
  await expect(input).toHaveCSS('overflow-y', 'auto');
  expect((await input.boundingBox()).height).toBeLessThanOrEqual(140);
  await page.screenshot({ path: '.ui-artifacts/task425-mobile-input-long.png' });
  await page.evaluate(() => {
    Object.defineProperty(visualViewport, 'height', { configurable: true, get: () => 410 });
    Object.defineProperty(visualViewport, 'offsetTop', { configurable: true, get: () => 20 });
    visualViewport.dispatchEvent(new Event('resize'));
  });
  await expect.poll(async () => (await page.locator('#agentSendBtn').boundingBox()).y + (await page.locator('#agentSendBtn').boundingBox()).height).toBeLessThanOrEqual(430);
  expect((await input.boundingBox()).height).toBeLessThanOrEqual(410 / 3 + 1);
  await page.screenshot({ path: '.ui-artifacts/task425-mobile-keyboard-simulated.png' });
  const response = page.waitForResponse(response => response.url().endsWith('/api/news/ui-064/agent/jobs') && response.request().method() === 'POST');
  await input.fill('实际发送\n第二行'); await input.press('ControlOrMeta+Enter');
  const result = await (await response).json();
  expect(result.job.question).toBe('实际发送\n第二行');
  await expect(input).toHaveValue('');
  await expect.poll(async () => (await input.boundingBox()).height).toBeLessThan(75);
  await request.post('/__test/agent-complete', { data: { item_id: 'ui-064', job_id: result.job_id } });
});

for (const width of [1440, 390]) {
  test(`multiline input remains usable in all themes at ${width}`, async ({ page }) => {
    await agent(page, width);
    const input = page.locator('#agentPromptInput');
    for (const theme of ['light', 'dark', 'eink']) {
      await page.locator('#agentDockToggleBtn').click();
      await chooseTheme(page, theme);
      await page.locator('#agentDockToggleBtn').click();
      await input.fill('每一行问题都可以滚动查看，发送按钮保持可用。\n'.repeat(24));
      await expect(input).toHaveCSS('overflow-y', 'auto');
      await expect(page.locator('#agentSendBtn')).toBeEnabled();
      const box = await page.locator('#agentSendBtn').boundingBox();
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(width);
      expect(box.y + box.height).toBeLessThanOrEqual(900 - (width < 920 ? 56 : 0));
      expect(await input.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
      await page.screenshot({ path: `.ui-artifacts/task425-input-${width}-${theme}.png` });
    }
  });
}
