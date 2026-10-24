import { test, expect } from '@playwright/test';
import { chooseTheme } from './helpers.js';

const today = '2026-10-19';
const entries = [
  { label: 'Bloomberg world', error: 'HTTP 429 Too Many Requests\nopencli: fetch failed\nRetry-After: 28' },
  { label: 'Reuters world', error: 'HTTP 403 Forbidden\n<script>window.upstreamInjected=true</script>\nAccess denied < & >' },
  { label: 'Reuters world', error: 'connect ETIMEDOUT\nUnable to connect to the upstream source.' },
];
function payload(day = today, rows = entries) {
  return { ok: true, day, today,
    stats: day === today ? { availability: 80, available_sources: 8, total_sources: 10, ingested_count: 151 }
      : { availability: null, available_sources: 0, total_sources: 0, ingested_count: 0 },
    days: rows.length ? [{ date: day, groups: rows.map((entry, index) => ({ time: index ? '12:40:05' : '13:58:12', labels: [`${entry.label} error`], entries: [entry] })) }] : [] };
}
test.beforeEach(async ({ request }) => { await request.post('/__test/reset-reading'); });
test.afterEach(async ({ page }) => { await page.unrouteAll({ behavior: 'wait' }); });
async function start(page) {
  await page.route('**/api/error-stats?*', route => route.fulfill({ json: payload() }));
  await page.goto('/');
  await expect(page.locator('#upstreamErrorBadge')).toHaveText('3');
  await page.locator('#upstreamErrorsBtn').click();
  await expect(page.locator('.upstream-error-card')).toHaveCount(3);
}

test('upstream prototype layout uses live counts, source/time filters and escaped copyable logs', async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(navigator, 'clipboard', { value: { writeText: async text => { window.copiedUpstreamLog = text; } } }));
  await start(page);
  await expect(page.locator('#upstreamErrorsTitle')).toHaveText('上游信源报错查看');
  await expect(page.locator('#upstreamErrorsBtn')).toHaveClass(/has-errors/);
  await expect(page.locator('.upstream-stat-label')).toHaveText(['当日抓取可用率', '异常来源', '今日已入库资讯', '当日报错']);
  await expect(page.locator('.upstream-stat-value')).toHaveText(['80%', '2 个', '151 条', '3 条']);
  await expect(page.locator('.upstream-error-group')).toHaveCount(2);
  await expect(page.locator('#upstreamErrorsContent script')).toHaveCount(0);
  expect(await page.evaluate(() => window.upstreamInjected)).toBeUndefined();
  await page.locator('[data-upstream-filter="13:58:12"]').click();
  await expect(page.locator('.upstream-error-card')).toHaveCount(1);
  await expect(page.locator('.upstream-showing-count')).toHaveText('当前展示 1 / 3 条报错');
  await expect(page.locator('.upstream-stat-value')).toHaveText(['80%', '2 个', '151 条', '3 条']);
  await page.locator('[data-upstream-dimension="source"]').click();
  await expect(page.locator('.upstream-error-card')).toHaveCount(3);
  await page.locator('[data-upstream-filter="Reuters world"]').click();
  await expect(page.locator('.upstream-error-card')).toHaveCount(2);
  await page.locator('[data-upstream-copy]').first().click();
  await expect(page.locator('.upstream-copy-feedback').first()).toHaveText('日志已复制');
  expect(await page.evaluate(() => window.copiedUpstreamLog)).toBe(entries[1].error);
  await page.evaluate(() => { navigator.clipboard.writeText = async () => { throw new Error('denied'); }; });
  await page.locator('[data-upstream-copy]').last().click();
  await expect(page.locator('.upstream-copy-feedback').last()).toContainText('请手动复制');
  expect(await page.evaluate(() => getSelection().toString())).toBe(entries[2].error);
  await page.keyboard.press('Escape');
  await expect(page.locator('#upstreamErrorsPanel')).toBeHidden();
  await expect(page.locator('#upstreamErrorsBtn')).toBeFocused();
});

test('historical queries, stale responses and read failures never replace the current-day badge', async ({ page }) => {
  let rows = entries, fail = false, release, reached, currentDay = today;
  const gate = new Promise(resolve => { release = resolve; });
  const requested = new Promise(resolve => { reached = resolve; });
  await page.route('**/api/error-stats?*', async route => {
    const params = new URL(route.request().url()).searchParams;
    const day = params.get('day') || currentDay;
    if (day === '2026-10-17') { reached(); await gate; }
    await route.fulfill(fail ? { status: 500, json: { ok: false, error: 'request_failed' } } : { json: payload(day, day === today ? rows : []) });
  });
  await page.goto('/');
  await expect(page.locator('#upstreamErrorBadge')).toHaveText('3');
  await page.locator('#upstreamErrorsBtn').click();
  await expect(page.locator('.upstream-error-card')).toHaveCount(3);
  const dialogBox = await page.locator('#upstreamErrorsPanel').boundingBox();
  await expect(page.locator('#upstreamNextDayBtn')).toBeDisabled();
  await expect(page.locator('#upstreamErrorDate')).toHaveAttribute('data-max', today);
  await page.locator('[data-upstream-day-offset="-1"]').click();
  await expect(page.locator('#upstreamErrorDate')).toHaveValue('2026-10-18');
  await expect(page.locator('[data-upstream-day-offset="-1"]')).toHaveAttribute('aria-pressed', 'true');
  await page.locator('#upstreamNextDayBtn').click();
  await expect(page.locator('.upstream-error-card')).toHaveCount(3);
  await expect(page.locator('#upstreamNextDayBtn')).toBeDisabled();
  await page.locator('#upstreamErrorDate').fill('2026-10-18');
  await page.locator('#upstreamErrorDate').press('Enter');
  await expect(page.locator('#upstreamErrorsContent')).toContainText('当天没有记录');
  const emptyBox = await page.locator('#upstreamErrorsPanel').boundingBox();
  expect({ ...emptyBox, height: dialogBox.height }).toEqual(dialogBox);
  expect(emptyBox.height).toBeLessThan(dialogBox.height);
  await expect(page.locator('.upstream-stat-label').nth(2)).toHaveText('当日已入库资讯');
  await expect(page.locator('.upstream-stat-value')).toHaveText(['—', '0 个', '0 条', '0 条']);
  await expect(page.locator('#upstreamErrorBadge')).toHaveText('3');
  await expect(page.locator('#upstreamTodayBtn')).toHaveText('今天 (10/19)', { useInnerText: true });
  await page.locator('#upstreamErrorDate').fill('2026-10-01');
  await page.locator('#upstreamErrorDate').press('Enter');
  await expect(page.locator('#upstreamErrorsContent')).toHaveAttribute('aria-busy', 'false');
  await page.locator('#upstreamPreviousDayBtn').click();
  await expect(page.locator('#upstreamErrorDate')).toHaveValue('2026-09-30');
  await expect(page.locator('#upstreamErrorsContent')).toHaveAttribute('aria-busy', 'false');
  await page.locator('#upstreamNextDayBtn').click();
  await expect(page.locator('#upstreamErrorDate')).toHaveValue('2026-10-01');
  await page.locator('#upstreamTodayBtn').click();
  await expect(page.locator('#upstreamErrorDate')).toHaveValue(today);
  await expect(page.locator('.upstream-error-card')).toHaveCount(3);
  await page.locator('#upstreamErrorDate').fill('2026-10-18');
  await page.locator('#upstreamErrorDate').press('Enter');
  await expect(page.locator('#upstreamErrorsContent')).toContainText('当天没有记录');
  await page.locator('#closeUpstreamErrorsBtn').click();
  await page.locator('#upstreamErrorsBtn').click();
  await expect(page.locator('#upstreamErrorDate')).toHaveValue(today);
  await expect(page.locator('.upstream-error-card')).toHaveCount(3);
  await page.locator('#upstreamErrorDate').fill('2026-10-17');
  await page.locator('#upstreamErrorDate').press('Enter');
  await requested;
  expect(await page.locator('#upstreamErrorsPanel').boundingBox()).toEqual(dialogBox);
  await page.locator('#upstreamTodayBtn').click();
  await expect(page.locator('.upstream-error-card')).toHaveCount(3);
  release();
  await page.unrouteAll({ behavior: 'wait' });
  await expect(page.locator('#upstreamErrorDate')).toHaveValue(today);
  await expect(page.locator('.upstream-error-card')).toHaveCount(3);
  await page.route('**/api/error-stats?*', route => route.fulfill(fail ? { status: 500, json: { ok: false, error: 'request_failed' } } : { json: { ...payload(currentDay, rows), today: currentDay } }));
  fail = true;
  await page.locator('#upstreamTodayBtn').click();
  await expect(page.locator('#upstreamErrorBadge')).toHaveText('?');
  await expect(page.locator('[data-upstream-retry]')).toBeVisible();
  expect(await page.locator('#upstreamErrorsPanel').boundingBox()).toEqual(dialogBox);
  fail = false; rows = [];
  await page.locator('#upstreamTodayBtn').click();
  await expect(page.locator('#upstreamErrorBadge')).toBeHidden();
  await expect(page.locator('#upstreamErrorsBtn')).toHaveClass(/no-errors/);
  await expect(page.locator('#upstreamErrorsContent')).toContainText('当天没有记录');
  currentDay = '2026-10-20';
  await page.locator('#closeUpstreamErrorsBtn').click();
  await page.locator('#upstreamErrorsBtn').click();
  await expect(page.locator('#upstreamErrorDate')).toHaveValue(currentDay);
  await expect(page.locator('#upstreamTodayBtn')).toHaveText('今天 (10/20)', { useInnerText: true });
  await expect(page.locator('#upstreamErrorDate')).toHaveAttribute('data-max', currentDay);
});

for (const width of [1440, 320]) {
  test(`date changes retain visible results and scroll while loading at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await start(page);
    const pending = [];
    await page.route('**/api/error-stats?*', route => {
      if (new URL(route.request().url()).searchParams.get('details') === '1') pending.push(route);
      else return route.fulfill({ json: payload() });
    });
    const content = page.locator('#upstreamErrorsContent');
    const message = page.locator('#upstreamLoadMessage');
    const changes = [
      ['2026-10-18', async () => { await page.locator('#upstreamErrorDate').fill('2026-10-18'); await page.locator('#upstreamErrorDate').press('Enter'); }],
      ['2026-10-17', () => page.locator('#upstreamPreviousDayBtn').click()],
      ['2026-10-18', () => page.locator('#upstreamNextDayBtn').click()],
      [today, () => page.locator('#upstreamTodayBtn').click()],
    ];
    for (const [day, change] of changes) {
      const before = await content.evaluate(node => {
        node.scrollTop = 80;
        window.retainedUpstreamCard = node.querySelector('.upstream-error-card');
        return { html: node.innerHTML, scroll: node.scrollTop };
      });
      const box = await content.boundingBox();
      await change();
      await expect.poll(() => pending.length).toBe(1);
      await expect(content).toHaveAttribute('aria-busy', 'true');
      await expect(message).toBeEmpty();
      expect(await content.evaluate(node => ({ html: node.innerHTML, scroll: node.scrollTop }))).toEqual(before);
      expect(await content.boundingBox()).toEqual(box);
      expect(await page.evaluate(() => window.retainedUpstreamCard.isConnected)).toBe(true);
      if (day === '2026-10-17') await page.screenshot({ path: `.ui-artifacts/upstream-pending-${width}.png` });
      await pending.shift().fulfill({ json: payload(day) });
      await expect(content).toHaveAttribute('aria-busy', 'false');
      await expect(content).toHaveAttribute('aria-label', `${day} 的上游报错记录`);
      await expect(message).toBeEmpty();
      expect(await content.evaluate(node => node.scrollTop)).toBe(0);
    }
    // Refreshing the same date with unchanged data should leave the current view untouched.
    await content.evaluate(node => {
      node.scrollTop = 80;
      window.retainedUpstreamCard = node.querySelector('.upstream-error-card');
    });
    await page.locator('#upstreamTodayBtn').click();
    await expect.poll(() => pending.length).toBe(1);
    await pending.shift().fulfill({ json: payload() });
    await expect(content).toHaveAttribute('aria-busy', 'false');
    expect(await page.evaluate(() => window.retainedUpstreamCard.isConnected)).toBe(true);
    expect(await content.evaluate(node => node.scrollTop)).toBe(80);

    // Failures keep the last successful date explicit, and retry requests the selected date.
    await page.locator('#upstreamPreviousDayBtn').click();
    await expect.poll(() => pending.length).toBe(1);
    await pending.shift().fulfill({ status: 500, json: { ok: false, error: 'request_failed' } });
    await expect(content).toHaveAttribute('aria-busy', 'false');
    await expect(message).toContainText(`仍显示 ${today}`);
    await expect(page.locator('.upstream-error-card')).toHaveCount(3);
    expect(await page.evaluate(() => window.retainedUpstreamCard.isConnected)).toBe(true);
    await page.locator('[data-upstream-retry]').click();
    await expect.poll(() => pending.length).toBe(1);
    const retry = pending.shift();
    expect(new URL(retry.request().url()).searchParams.get('day')).toBe('2026-10-18');
    await retry.fulfill({ json: payload('2026-10-18', []) });
    await expect(content).toContainText('当天没有记录');
    await expect(message).toBeEmpty();
    await expect(page.locator('[data-upstream-retry]')).toBeHidden();
  });
}

test('upstream entry and diagnostic dialog fit desktop and narrow screens in each theme', async ({ page }) => {
  await start(page);
  for (const theme of ['light', 'dark', 'eink']) {
    await page.locator('#closeUpstreamErrorsBtn').click();
    await chooseTheme(page, theme);
    const entry = page.locator('#upstreamErrorsBtn');
    await expect(entry.locator('svg')).toHaveCount(0);
    await page.mouse.move(0, 0);
    await expect(entry).not.toHaveCSS('border-top-color', 'rgba(0, 0, 0, 0)');
    await expect(page.locator('#pipelineStatus')).toContainText('DailyNews 增量索引已同步');
    const box = await entry.boundingBox();
    expect(box.x + box.width / 2).toBeCloseTo(page.viewportSize().width / 2, 0);
    await entry.screenshot({ path: `.ui-artifacts/upstream-entry-${theme}.png` });
    await entry.hover();
    await expect(entry).not.toHaveCSS('border-top-color', 'rgba(0, 0, 0, 0)');
    await entry.screenshot({ path: `.ui-artifacts/upstream-entry-${theme}-hover.png` });
    await page.locator('#upstreamErrorsBtn').click();
    await expect(page.locator('.upstream-error-card')).toHaveCount(3);
    const todayButton = await page.locator('#upstreamTodayBtn').boundingBox();
    expect(todayButton.width).toBeLessThan(100);
    const rightGap = await page.locator('.upstream-error-controls').evaluate(node => {
      const style = getComputedStyle(node);
      return node.getBoundingClientRect().right - node.querySelector('#upstreamNextDayBtn').getBoundingClientRect().right
        - parseFloat(style.paddingRight) - parseFloat(style.borderRightWidth);
    });
    expect(Math.abs(rightGap)).toBeLessThan(1);
    const stats = await page.locator('.upstream-health-strip > div').evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().width));
    expect(Math.max(...stats) - Math.min(...stats)).toBeLessThan(1);
    await expect(page.locator('.upstream-health-strip')).toHaveCSS('text-align', 'center');
    const dimensions = await page.locator('.upstream-dim-btn').evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().width));
    expect(Math.max(...dimensions) - Math.min(...dimensions)).toBeLessThan(1);
    await page.screenshot({ path: `.ui-artifacts/upstream-diagnostics-${theme}.png` });
  }
  for (const [width, mobileTheme] of [[390, 'light'], [390, 'dark'], [390, 'eink'], [360, 'light'], [320, 'light']]) {
    await page.locator('#closeUpstreamErrorsBtn').click();
    await page.setViewportSize({ width, height: 844 });
    await chooseTheme(page, mobileTheme);
    const button = await page.locator('#upstreamErrorsBtn').boundingBox();
    await expect(page.locator('#pipelineStatus')).toBeHidden();
    await expect(page.locator('.upstream-pulse-dot')).toBeVisible();
    const refresh = await page.locator('#refreshIndexBtn').boundingBox();
    expect(button.x + button.width).toBeLessThanOrEqual(refresh.x);
    const theme = await page.locator('#themeSelect').boundingBox();
    expect(theme.x + theme.width).toBeLessThanOrEqual(width);
    await page.locator('#upstreamErrorsBtn').click();
    await expect(page.locator('.upstream-error-card')).toHaveCount(3);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    expect(await page.locator('.upstream-dialog-body').evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
    const controls = page.locator('.upstream-error-controls');
    const alignedControls = await controls.evaluate(node => {
      const style = getComputedStyle(node), bounds = node.getBoundingClientRect();
      return {
        leftGap: node.querySelector('.upstream-date-input').getBoundingClientRect().left - bounds.left - parseFloat(style.paddingLeft) - parseFloat(style.borderLeftWidth),
        rightGap: bounds.right - node.querySelector('.upstream-date-shortcuts').getBoundingClientRect().right - parseFloat(style.paddingRight) - parseFloat(style.borderRightWidth),
      };
    });
    expect(Math.abs(alignedControls.leftGap)).toBeLessThan(1);
    expect(Math.abs(alignedControls.rightGap)).toBeLessThan(1);
    await expect(page.locator('#upstreamTodayBtn')).toHaveCSS('width', '40px');
    await expect(page.locator('.upstream-date-shortcuts button:visible')).toHaveCount(3);
    await expect(page.locator('#upstreamPreviousDayBtn')).toBeVisible();
    await expect(page.locator('#upstreamNextDayBtn')).toBeVisible();
    await expect(page.locator('#upstreamTodayBtn')).toHaveText('今日', { useInnerText: true });
    const dateBox = await page.locator('.upstream-date-input').boundingBox();
    const shortcutsBox = await page.locator('.upstream-date-shortcuts').boundingBox();
    expect(dateBox.y + dateBox.height / 2).toBeCloseTo(shortcutsBox.y + shortcutsBox.height / 2, 0);
    const statTops = await page.locator('.upstream-health-strip > div').evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().top));
    expect(new Set(statTops).size).toBe(1);
    for (const label of await page.locator('.upstream-stat-label').all()) await expect(label).toHaveCSS('white-space', 'nowrap');
    expect(await page.locator('#upstreamErrorsContent').evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
    await page.screenshot({ path: `.ui-artifacts/upstream-diagnostics-${width}-${mobileTheme}.png` });
    // Keyboard focus skips yesterday/the day before and keeps arrows and today reachable.
    await page.locator('#upstreamErrorDate').focus();
    for (let step = 0; step < 3; step++) await page.keyboard.press('Tab');
    await expect(page.locator('#upstreamTodayBtn')).toBeFocused();
    const todayBox = await page.locator('#upstreamTodayBtn').boundingBox();
    const controlsBox = await controls.boundingBox();
    expect(todayBox.x).toBeGreaterThanOrEqual(controlsBox.x);
    expect(todayBox.x + todayBox.width).toBeLessThanOrEqual(controlsBox.x + controlsBox.width);
    await page.locator('#upstreamCalendarBtn').click();
    await expect(page.locator('#upstreamCalendar')).toBeVisible();
    await page.locator('[data-calendar-today]').click();
    await expect(page.locator('#upstreamCalendar')).toBeHidden();
  }
});

for (const [width, height] of [[1440, 900], [390, 844], [320, 568], [844, 390]]) {
  test(`content-height diagnostics keep empty states, calendars and long logs reachable at ${width}x${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    let rows = [];
    await page.route('**/api/error-stats?*', route => route.fulfill({ json: payload(today, rows) }));
    await page.goto('/');
    await page.locator('#upstreamErrorsBtn').click();
    await expect(page.locator('.upstream-empty')).toContainText('当天没有记录');
    const panel = page.locator('#upstreamErrorsPanel');
    const emptyBox = await panel.boundingBox();
    expect(emptyBox.height).toBeLessThanOrEqual(640);
    expect(emptyBox.y + emptyBox.height).toBeLessThanOrEqual(height - 12);
    const filter = await page.locator('.upstream-chip-btn').boundingBox();
    const filters = await page.locator('.upstream-filter-chips').boundingBox();
    expect(filter.x).toBeCloseTo(filters.x + 3, 0);
    await page.screenshot({ path: `.ui-artifacts/upstream-empty-${width}.png` });

    // The calendar may extend beyond a short empty panel, but remains clickable in the viewport.
    await page.locator('#upstreamCalendarBtn').click();
    const calendar = page.locator('#upstreamCalendar');
    const calendarBox = await calendar.boundingBox();
    expect(calendarBox.y).toBeGreaterThanOrEqual(12);
    expect(calendarBox.y + calendarBox.height).toBeLessThanOrEqual(height - 12);
    expect(calendarBox.x).toBeGreaterThanOrEqual(12);
    expect(calendarBox.x + calendarBox.width).toBeLessThanOrEqual(width - 12);
    await page.locator('[data-calendar-today]').click();
    await expect(calendar).toBeHidden();

    rows = [{ label: 'Long upstream source', error: 'connection timed out\n'.repeat(80) }, ...Array.from({ length: 12 }, () => entries[0])];
    await page.locator('#upstreamTodayBtn').click();
    await expect(page.locator('.upstream-error-card')).toHaveCount(13);
    const fullBox = await panel.boundingBox();
    expect(fullBox.y).toBe(emptyBox.y);
    expect(fullBox.height).toBeGreaterThanOrEqual(emptyBox.height);
    if (height >= 844) expect(fullBox.height).toBeGreaterThan(emptyBox.height);
    expect(fullBox.height).toBeLessThanOrEqual(640);
    expect(fullBox.y + fullBox.height).toBeLessThanOrEqual(height - 12);
    expect(await panel.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
    const log = page.locator('.upstream-log-box pre').first();
    expect(await log.evaluate(node => node.scrollHeight > node.clientHeight)).toBe(true);
    await log.evaluate(node => { node.scrollTop = node.scrollHeight; });
    expect(await log.evaluate(node => node.scrollTop + node.clientHeight)).toBeGreaterThanOrEqual(await log.evaluate(node => node.scrollHeight - 1));
    const last = page.locator('[data-upstream-copy]').last();
    await last.scrollIntoViewIfNeeded();
    const lastBox = await last.boundingBox();
    expect(lastBox.y).toBeGreaterThan(fullBox.y);
    expect(lastBox.y + lastBox.height).toBeLessThan(fullBox.y + fullBox.height);
    await page.locator('#closeUpstreamErrorsBtn').click();
    await expect(panel).toBeHidden();
  });
}

test('badge refreshes lightweight counts while visible and fits large counts at 320px', async ({ page }) => {
  await page.clock.install();
  let rows = entries, requests = 0, details = 0;
  await page.route('**/api/error-stats?*', route => {
    requests++;
    if (new URL(route.request().url()).searchParams.get('details') === '1') details++;
    return route.fulfill({ json: payload(today, rows) });
  });
  await page.setViewportSize({ width: 320, height: 844 });
  await page.goto('/');
  await expect(page.locator('#upstreamErrorBadge')).toHaveText('3');
  rows = Array.from({ length: 105 }, () => entries[0]);
  await page.clock.fastForward(60000);
  await expect(page.locator('#upstreamErrorBadge')).toHaveText('99+');
  await expect(page.locator('#upstreamErrorsBtn')).toHaveAttribute('title', /105 条/);
  expect(details).toBe(0);
  const theme = await page.locator('#themeSelect').boundingBox();
  expect(theme.x + theme.width).toBeLessThanOrEqual(320);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(320);
  await page.evaluate(() => Object.defineProperty(document, 'hidden', { configurable: true, value: true }));
  const before = requests;
  await page.clock.fastForward(60000);
  expect(requests).toBe(before);
  rows = [];
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, value: false });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect(page.locator('#upstreamErrorBadge')).toBeHidden();
  const entry = page.locator('#upstreamErrorsBtn');
  await page.mouse.move(0, 0);
  await expect(entry).toHaveCSS('border-top-color', 'rgba(0, 0, 0, 0)');
  await entry.screenshot({ path: '.ui-artifacts/upstream-entry-empty.png' });
  await entry.hover();
  await expect(entry).not.toHaveCSS('border-top-color', 'rgba(0, 0, 0, 0)');
  await entry.screenshot({ path: '.ui-artifacts/upstream-entry-empty-hover.png' });
});
