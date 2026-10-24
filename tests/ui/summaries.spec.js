import { test, expect } from '@playwright/test';
import { chooseTheme, openNavigation } from './helpers.js';

async function setLimits(request, limits = { max_days: 20, max_items: 2 }) {
  const settings = await (await request.get('/api/settings')).json();
  await request.put('/api/settings', { data: { llm: settings.llm, agent: { ...settings.agent, runtime: 'codex', codex: { ...settings.agent.codex, model: 'ui-summary-model', reasoning_effort: '' } }, feed: { hidden_source_subkeys: [] }, news_summary: limits } });
}
async function open(page) {
  await page.goto('/');
  await openNavigation(page);
  await page.locator('#navSummariesBtn').click();
  await expect(page.locator('#catchupStartBtn')).toBeEnabled();
}
async function preview(page) {
  await page.locator('#catchupStartBtn').click();
  await expect(page.locator('#summaryModal')).toBeVisible();
}
async function complete(page) {
  await preview(page);
  await page.locator('#confirmSummaryBtn').click();
  await expect(page.locator('#summaryModal')).not.toBeVisible();
  await expect(page.locator('#summaryCardList .news-card')).toHaveCount(1, { timeout: 15000 });
  await expect(page.locator('#summaryProgress')).toBeEmpty();
}
async function unread(request) {
  return (await (await request.get('/api/news?read_filter=unread&per=100')).json()).total;
}

test.beforeEach(async ({ request }) => {
  await request.post('/__test/reset-summaries');
  await setLimits(request);
});
test.afterEach(async ({ page, request }) => {
  await page.unrouteAll({ behavior: 'wait' });
  await request.post('/__test/reset-summaries');
  await setLimits(request, { max_days: 20, max_items: 6000 });
});

test('fixed snapshot, exclusions, new unread protection, report citations and export', async ({ page, request }) => {
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  await request.patch('/api/news/ui-000/state', { data: { read: true } });
  await open(page);
  const feedHeader = await page.locator('#feedColFeed .feed-header').innerHTML();
  await preview(page);
  await expect(page.locator('#summaryModalBody')).toContainText('纳入总结 2 条');
  await expect(page.locator('#summaryModalBody')).toContainText('成功后标读共 64 条');
  await expect(page.locator('#summaryModalBody')).toContainText('Codex Agent · ui-summary-model');
  expect(await unread(request)).toBe(64);
  // Becomes unread after the snapshot; it must survive this batch.
  await request.patch('/api/news/ui-000/state', { data: { read: false } });
  await page.locator('#confirmSummaryBtn').click();
  await expect(page.locator('#summaryCardList .news-card')).toHaveCount(1);
  await expect(page.locator('#summaryProgress')).toBeEmpty();
  await expect(page.locator('#summaryContent h1')).toContainText('新闻总结');
  await expect(page.locator('#navSummariesBtn .nav-badge, .summary-archived')).toHaveCount(0);
  await expect(page.locator('#feedColSummaries .feed-filter-row button')).toHaveCount(1);
  await expect(page.locator('.catchup-copy')).toHaveText('锁定点击时的全部未读，不受新闻流筛选影响。成功归档后才标读，Bloomberg 视频除外。');
  expect(await unread(request)).toBe(1);
  expect((await (await request.get('/api/news/ui-000/detail')).json()).important_at).toBeTruthy();
  await page.locator('.summary-citation').first().click();
  await expect(page.locator('.summary-sources')).toHaveAttribute('open', '');
  const [download] = await Promise.all([page.waitForEvent('download'), page.locator('#exportSummaryBtn').click()]);
  expect(download.suggestedFilename()).toMatch(/^新闻总结-.*\.md$/);
  await page.locator('#summariesSearchInput').fill('不存在的主题');
  await expect(page.locator('#summaryCardList')).toContainText('没有符合条件的总结');
  await page.locator('#summariesSearchInput').fill('流动性');
  await expect(page.locator('#summaryCardList .news-card')).toHaveCount(1);
  await page.keyboard.press('m');
  expect(await unread(request)).toBe(1);
  // Only existing news controls remain in the feed header.
  expect(await page.locator('#feedColFeed .feed-header').innerHTML()).toBe(feedHeader);
  expect(errors).toEqual([]);
});

test('preview dismissal and Escape do not create a job or change reads', async ({ page, request }) => {
  await open(page); await preview(page);
  await expect(page.locator('#discardSummaryBtn')).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  expect(await page.evaluate(() => document.querySelector('#summaryModal').contains(document.activeElement))).toBe(true);
  await page.keyboard.press('Escape');
  await expect(page.locator('#summaryModal')).not.toBeVisible();
  expect(await unread(request)).toBe(65);
  expect((await (await request.get('/api/news-summaries')).json()).latest_job).toBeNull();
});

test('compact confirmation keeps expiry protection and its inline settings link opens the range', async ({ page, request }) => {
  await page.clock.install();
  await open(page); await preview(page);
  const body = page.locator('#summaryModalBody');
  await expect(body).not.toContainText('成功保存后才标读：');
  await expect(body).not.toContainText('仅在总结通过基本检查');
  await expect(body).not.toContainText('预览有效 15 分钟');
  await expect(page.locator('#summaryExpiryNotice')).toBeHidden();
  await expect(page.locator('.catchup-settings-hint')).toHaveText('范围可在 设置 › 积压新闻总结范围 中调整。');
  await expect(page.locator('#modalSummarySettingsBtn')).toHaveCSS('text-decoration-line', 'underline');
  await page.clock.fastForward(16 * 60 * 1000);
  await expect(page.locator('#confirmSummaryBtn')).toBeDisabled();
  await expect(page.locator('#summaryExpiryNotice')).toBeVisible();
  await expect(page.locator('#summaryExpiryNotice')).toContainText('预览已过期');
  await page.locator('#modalSummarySettingsBtn').click();
  await expect(page.locator('#summaryModal')).not.toBeVisible();
  await expect(page.locator('[data-section="catchup"]')).toBeVisible();
  expect((await (await request.get('/api/news-summaries')).json()).latest_job).toBeNull();
  expect(await unread(request)).toBe(65);
});

test('running job resumes after refresh and cancellation preserves unread', async ({ page, request }) => {
  await request.post('/__test/summary-mode', { data: { mode: 'wait' } });
  await open(page); await preview(page); await page.locator('#confirmSummaryBtn').click();
  await expect(page.locator('#summaryProgress')).toContainText('正在生成');
  await page.reload();
  await page.locator('#navSummariesBtn').click();
  await expect(page.locator('#cancelSummaryBtn')).toBeVisible();
  await expect(page.locator('#catchupStartBtn')).toBeDisabled();
  await page.locator('#cancelSummaryBtn').click();
  await expect(page.locator('#summaryProgress')).toContainText('已停止');
  expect(await unread(request)).toBe(65);
  expect((await (await request.get('/api/news-summaries')).json()).total).toBe(0);
});

test('failed generation preserves unread and permits a new preview and retry', async ({ page, request }) => {
  await request.post('/__test/summary-mode', { data: { mode: 'fail' } });
  await open(page); await preview(page); await page.locator('#confirmSummaryBtn').click();
  await expect(page.locator('#summaryProgress')).toContainText('所选运行方式未完整完成，未标记已读。');
  expect(await unread(request)).toBe(65);
  await request.post('/__test/summary-mode', { data: { mode: 'success' } });
  await complete(page);
  expect(await unread(request)).toBe(0);
});

test('saved scope changes invalidate an open preview', async ({ page, request }) => {
  await open(page); await preview(page);
  await setLimits(request, { max_days: 20, max_items: 3 });
  await page.locator('#confirmSummaryBtn').click();
  await expect(page.locator('#summaryModalError')).toContainText('预览后运行方式或总结限制已改变');
  await expect(page.locator('#confirmSummaryBtn')).toBeDisabled();
  expect(await unread(request)).toBe(65);
  await page.locator('#discardSummaryBtn').click();
  await preview(page);
  await expect(page.locator('#summaryModalBody')).toContainText('纳入总结 3 条');
});

test('lost create response retries the same request without duplicate archives', async ({ page, request }) => {
  let dropped = false;
  await page.route('**/api/news-summaries', async route => {
    if (route.request().method() === 'POST' && !dropped) {
      dropped = true;
      await route.fetch();
      await route.abort('failed');
    } else await route.continue();
  });
  await open(page); await preview(page); await page.locator('#confirmSummaryBtn').click();
  await expect(page.locator('#summaryModalError')).toContainText('网络请求失败。');
  await page.locator('#confirmSummaryBtn').click();
  await expect(page.locator('#summaryModal')).not.toBeVisible();
  await expect(page.locator('#summaryCardList .news-card')).toHaveCount(1);
  await expect(page.locator('#summaryProgress')).toBeEmpty();
  expect((await (await request.get('/api/news-summaries')).json()).total).toBe(1);
});

test('scope settings persist, validate and retain drafts across navigation', async ({ page, request }) => {
  await open(page);
  await page.locator('#catchupSettingsBtn').click();
  await expect(page.locator('[data-section="catchup"]')).toBeVisible();
  await expect(page.locator('#settingsCatchupScope')).toContainText('候选未读 65 条');
  expect(await page.locator('[data-settings-tab]').evaluateAll(nodes => nodes.map(n => n.dataset.settingsTab))).toEqual(['system', 'models', 'deepseek', 'classification', 'pipeline', 'storage', 'catchup', 'shortcuts']);
  await expect(page.locator('[data-section="catchup"]')).not.toContainText('全局默认值');
  await page.locator('[name="news_summary.max_days"]').fill('0');
  await page.locator('#settingsSaveBtn').click();
  await expect(page.locator('#settingsStatus')).toContainText('有效的正整数');
  await page.locator('[name="news_summary.max_days"]').fill('7');
  await page.locator('[name="news_summary.max_items"]').fill('15');
  await expect(page.locator('#settingsCatchupScope')).toContainText('按日期与条数纳入 15 条');
  await expect(page.locator('#settingsCatchupScope')).toContainText('当前填写：7 个自然日、最多 15 条');
  expect((await (await request.get('/api/settings')).json()).news_summary).toEqual({ max_days: 20, max_items: 2 });
  expect((await (await request.get('/api/news-summaries')).json()).latest_job).toBeNull();
  expect(await unread(request)).toBe(65);
  await page.locator('#closeSettingsBtn').click();
  await expect(page.locator('#feedColSummaries')).toHaveClass(/active/);
  await page.locator('#catchupSettingsBtn').click();
  await expect(page.locator('[name="news_summary.max_days"]')).toHaveValue('7');
  await page.locator('#settingsSaveBtn').click();
  await expect(page.locator('#settingsStatus')).toContainText('设置已保存');
  expect((await (await request.get('/api/settings')).json()).news_summary).toEqual({ max_days: 7, max_items: 15 });
  await expect(page.locator('#settingsCatchupScope')).toContainText('当前填写：7 个自然日、最多 15 条');
  await page.locator('#resetCatchupDefaultsBtn').click();
  await expect(page.locator('[name="news_summary.max_days"]')).toHaveValue('20');
  await page.locator('#settingsResetBtn').click();
  await expect(page.locator('[name="news_summary.max_days"]')).toHaveValue('7');
});

test('scope input recalculates once and does not recalculate again on blur', async ({ page }) => {
  await page.clock.install();
  await open(page);
  await page.locator('#catchupSettingsBtn').click();
  const scope = page.locator('#settingsCatchupScope');
  await expect(scope).toContainText('当前填写：');
  const requests = [];
  page.on('request', request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/news-summaries/scope') requests.push(request.postDataJSON());
  });
  for (const [name, value] of [['max_days', '7'], ['max_items', '15']]) {
    const input = page.locator(`[name="news_summary.${name}"]`);
    const before = requests.length;
    await input.fill(value);
    await page.clock.runFor(300);
    await expect(scope).not.toHaveAttribute('aria-busy', 'true');
    expect(requests.length).toBe(before + 1);
    expect(requests.at(-1).news_summary[name]).toBe(Number(value));
    const computed = await scope.textContent();
    if (name === 'max_days') await page.locator('#settingsDetailTitle').click();
    else await input.press('Tab');
    await expect(input).not.toBeFocused();
    await page.clock.runFor(500);
    expect(requests.length).toBe(before + 1);
    await expect(scope).toHaveText(computed);
    // Reselecting or re-entering the same value must also leave the result intact.
    await input.fill(value);
    await page.locator('#settingsDetailTitle').click();
    await page.clock.runFor(500);
    expect(requests.length).toBe(before + 1);
  }
});

test('report, scope and confirmation fit desktop, tablet, mobile and themes', async ({ page, request }) => {
  await open(page); await complete(page);
  for (const [width, theme] of [[1440, 'light'], [1920, 'dark'], [1024, 'eink'], [390, 'light'], [360, 'dark']]) {
    await page.setViewportSize({ width, height: 900 });
    await chooseTheme(page, theme);
    if (width <= 920) {
      await page.locator('#summaryCardList .news-card').first().click();
      await expect(page.locator('#mobileReaderBackBtn')).toHaveAttribute('aria-label', '返回总结列表');
    }
    await expect(page.locator('#summaryContent h1')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(await page.locator('#summaryContent').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    await page.screenshot({ path: `.ui-artifacts/summaries-${width}-${theme}.png` });
    if (width <= 920) await page.locator('#mobileReaderBackBtn').click();
  }
  await request.post('/__test/reset-reading');
  await page.locator('[data-summary-filter="all"]').click();
  await expect(page.locator('#catchupStartBtn')).toBeEnabled();
  await preview(page);
  await expect(page.locator('#confirmSummaryBtn')).toBeInViewport();
  await expect(page.locator('#confirmSummaryBtn')).toHaveCSS('white-space', 'nowrap');
  expect(await page.locator('#summaryModal').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  await page.screenshot({ path: '.ui-artifacts/summary-confirm-mobile.png' });
  await page.locator('#discardSummaryBtn').click();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await chooseTheme(page, 'light');
  await preview(page);
  const dialog = await page.locator('#summaryModal').boundingBox();
  expect(dialog.width / 1440).toBeLessThanOrEqual(0.42);
  expect(dialog.height / 1000).toBeLessThan(0.6);
  await expect(page.locator('#modalSummarySettingsBtn')).toBeInViewport();
  await expect(page.locator('#confirmSummaryBtn')).toBeInViewport();
  await page.screenshot({ path: '.ui-artifacts/summary-confirm-desktop.png' });
  await page.locator('#discardSummaryBtn').click();
  await page.locator('#catchupSettingsBtn').click();
  await expect(page.locator('#settingsCatchupScope')).toContainText('当前填写：');
  await page.screenshot({ path: '.ui-artifacts/summary-settings-desktop.png' });
});

test('draft scope recalculates dates and counts, ignores old requests and clears invalid values', async ({ page, request }) => {
  await setLimits(request, { max_days: 20, max_items: 6000 });
  await open(page);
  await page.locator('#catchupSettingsBtn').click();
  const scope = page.locator('#settingsCatchupScope');
  const days = page.locator('[name="news_summary.max_days"]');
  const items = page.locator('[name="news_summary.max_items"]');
  await expect(scope).toContainText('按日期与条数纳入 65 条');
  await days.fill('1');
  await expect(scope).toContainText('2026-10-14 → 2026-10-14');
  await expect(scope).toContainText('按日期与条数纳入 15 条；日期排除 50 条');
  await items.fill('4');
  await expect(scope).toContainText('按日期与条数纳入 4 条；日期排除 50 条，条数排除 11 条');

  let release, received, finished;
  const held = new Promise(resolve => { release = resolve; });
  const requested = new Promise(resolve => { received = resolve; });
  const handled = new Promise(resolve => { finished = resolve; });
  await page.route('**/api/news-summaries/scope', async route => {
    if (route.request().method() === 'POST' && route.request().postDataJSON().news_summary.max_items === 3) {
      const response = await route.fetch();
      received();
      await held;
      try { await route.fulfill({ response }); } catch { /* superseded request was aborted */ }
      finished();
    } else await route.continue();
  });
  await items.fill('3');
  await requested;
  await items.fill('2');
  await expect(scope).toContainText('按日期与条数纳入 2 条');
  release();
  await handled;
  await expect(scope).toContainText('当前填写：1 个自然日、最多 2 条');
  for (const value of ['', '0', '1.5']) {
    await items.fill(value);
    await expect(scope).toHaveText('请输入有效的正整数以计算当前生效范围。');
  }
  await page.locator('#settingsResetBtn').click();
  await expect(scope).toContainText('按日期与条数纳入 65 条');
  expect((await (await request.get('/api/settings')).json()).news_summary).toEqual({ max_days: 20, max_items: 6000 });
  expect(await unread(request)).toBe(65);
});

test('citation return restores the originating paragraph and focus on desktop and mobile', async ({ page }) => {
  await page.route('**/api/news-summaries/*', async route => {
    const response = await route.fetch();
    const data = await response.json();
    if (data.summary) {
      const ref = data.summary.sources[0].ref;
      data.summary.body = Array.from({ length: 12 }, (_, i) => `## 第 ${i + 1} 段\n\n${'供应链与市场需求的变化仍需结合报道日期观察。'.repeat(8)} [[${ref}]]`).join('\n\n');
    }
    await route.fulfill({ response, json: data });
  });
  await open(page); await complete(page);
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    if (width <= 920) await page.locator('#summaryCardList .news-card').first().click();
    const back = page.locator('#summaryReturnBtn');
    const sources = page.locator('.summary-sources');
    await expect(back).toBeHidden();
    for (const index of [1, 9]) {
      // Keep an already-open references list open after returning, too.
      await sources.evaluate((el, open) => { el.open = open; }, index === 9);
      const citation = page.locator('.summary-citation').nth(index);
      await citation.scrollIntoViewIfNeeded();
      const before = await page.locator('#summaryScrollArea').evaluate(el => el.scrollTop);
      await citation.click();
      await expect(page.locator('[data-summary-ref]').first()).toBeFocused();
      await expect(back).toBeInViewport();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({ path: `.ui-artifacts/summary-citation-return-${width}.png` });
      await back.click();
      await expect(citation).toBeFocused();
      expect(Math.abs(await page.locator('#summaryScrollArea').evaluate(el => el.scrollTop) - before)).toBeLessThan(1);
      expect(await sources.evaluate(el => el.open)).toBe(index === 9);
      await expect(back).toBeHidden();
    }
    await page.locator('.summary-citation').last().click();
    if (width <= 920) await page.locator('#mobileReaderBackBtn').click();
    await page.locator('#summaryCardList .news-card').first().click();
    await expect(page.locator('#summaryContent h1')).toBeVisible();
    await expect(back).toBeHidden();
  }
});

test('mobile summary supports edge-swipe back and settings return', async ({ page }) => {
  await open(page); await complete(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('#summaryCardList .news-card').first().click();
  await expect(page.locator('#summaryContent h1')).toBeVisible();
  await page.evaluate(() => {
    const target = document.querySelector('#summaryScrollArea');
    const start = new Touch({ identifier: 1, target, clientX: 20, clientY: 300 });
    const end = new Touch({ identifier: 1, target, clientX: 140, clientY: 305 });
    for (const [type, touches] of [['touchstart', [start]], ['touchmove', [end]], ['touchend', []]])
      target.dispatchEvent(new TouchEvent(type, { bubbles: true, cancelable: true, touches, changedTouches: [type === 'touchstart' ? start : end] }));
  });
  await expect(page.locator('#detailPanelSummaries')).toBeHidden();
  await expect(page.locator('#summaryCardList')).toBeVisible();
  await page.locator('#catchupSettingsBtn').click();
  await expect(page.locator('[data-section="catchup"]')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('#feedColSummaries')).toBeVisible();
  await expect(page.locator('#detailPanelSummaries')).toBeHidden();
});

test('closed sources stay in preview and summary markdown cannot run HTML', async ({ page, request }) => {
  const settings = await (await request.get('/api/settings')).json();
  const hidden = settings.feed_source_subkeys.groups[0].items[0].key;
  await request.put('/api/settings', { data: { llm: settings.llm, feed: { hidden_source_subkeys: [hidden] } } });
  await open(page);
  await preview(page);
  const visible = await unread(request);
  expect(visible).toBeLessThan(65);
  await expect(page.locator('#summaryModalBody')).toContainText(`新闻流可见未读 ${visible} 条`);
  await expect(page.locator('#summaryModalBody')).toContainText(`其中含已关闭信源 ${65 - visible} 条`);
  await page.locator('#discardSummaryBtn').click();
  await page.route('**/api/news-summaries/*', async route => {
    const response = await route.fetch();
    const data = await response.json();
    if (data.summary) {
      data.summary.body += '\n\n<img src=x onerror="window.summaryInjected=true">';
      data.summary.sources[0].url = 'javascript:window.summaryInjected=true';
    }
    await route.fulfill({ response, json: data });
  });
  await complete(page);
  await expect(page.locator('#summaryProse')).toContainText('<img src=x');
  expect(await page.locator('#summaryProse img, #summaryContent a[href^="javascript:"]').count()).toBe(0);
  expect(await page.evaluate(() => window.summaryInjected)).toBeUndefined();
});
