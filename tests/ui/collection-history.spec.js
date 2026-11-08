import { test, expect } from '@playwright/test';
import { chooseTheme } from './helpers.js';

const today = '2026-11-09';
const records = [
  { label: 'Reuters · World', time: '18:00:00', fetched_count: 25, duplicate_count: 20, ingested_count: 5 },
  { label: 'Bloomberg · Tech', time: '18:00:04', fetched_count: 0, duplicate_count: 0, ingested_count: 0,
    status: 'failed', error_count: 1, error: 'TIMEOUT\n<script>window.collectionInjected=true</script>' },
  { label: 'TechCrunch', time: '18:00:08', fetched_count: 0, duplicate_count: 0, ingested_count: 0 },
  { label: 'Ars Technica', time: '18:00:12', fetched_count: 12, duplicate_count: 12, ingested_count: 0 },
  { label: 'Reuters · Technology', time: '18:00:15', fetched_count: 8, duplicate_count: 6, ingested_count: 0, publication_status: 'pending', notices: [{ stage: 'translation', count: 2, error: '2 条：HTTP_429' }] },
  { label: 'X · mingchikuo', time: '18:00:20', fetched_count: 0, duplicate_count: 0, ingested_count: 0, status: 'running' },
  { label: 'X · ilyasut', time: '00:01:00', fetched_count: 0, duplicate_count: 0, ingested_count: 2, kind: 'publication' },
].map((row, index) => ({ date: today, kind: 'collection', status: 'done', publication_status: 'done', error_count: 0, error: '', notices: [],
  run_id: row.kind === 'publication' ? 'overnight' : 'evening', source_id: `source-${index}`,
  batch_started_at: row.kind === 'publication' ? '2026-11-08T23:58:00+08:00' : '2026-11-09T18:00:00+08:00', ...row }));

function payload(rows = records, day = today) {
  return { ok: true, today, day, unresolved_count: 1, records: rows,
    stats: { ...Object.fromEntries(['fetched_count', 'duplicate_count', 'ingested_count', 'error_count']
      .map(key => [key, rows.reduce((sum, row) => sum + (row[key] || 0), 0)])),
      duplicate_unknown: rows.filter(row => row.duplicate_count === null).length } };
}

async function mockHistory(page, response = () => payload()) {
  await page.route('**/api/error-stats?*', route => {
    const params = new URL(route.request().url()).searchParams;
    const data = response(params);
    return route.fulfill({ json: params.get('details') === '0'
      ? { ok: true, day: data.day, today: data.today, unresolved_count: data.unresolved_count } : data });
  });
}

test.beforeEach(async ({ request }) => { await request.post('/__test/reset-collection'); });
test.afterEach(async ({ page, request }) => {
  await page.unrouteAll({ behavior: 'wait' });
  await request.post('/__test/reset-collection');
});

test('real collection API exposes queued runs even with zero errors', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#pipelineStatus')).toContainText('尚未采集');
  await page.locator('#upstreamErrorsBtn').click();
  await expect(page.locator('.upstream-empty')).toContainText('当天没有记录');
  await expect(page.locator('.upstream-stat-value')).toHaveText(['0 条', '0 条', '0 条', '0 条']);
  await page.locator('#closeUpstreamErrorsBtn').click();
  await page.locator('#collectNewsBtn').click();
  await expect(page.locator('#pipelineStatus')).toContainText('正在采集');
  await page.locator('#upstreamErrorsBtn').click();
  await expect(page.locator('.upstream-error-card')).toHaveCount(1);
  await expect(page.locator('.upstream-card-head > .card-tag')).toHaveText('等待采集');
  await page.getByText('来源明细（12）', { exact: true }).click();
  await expect(page.locator('[data-upstream-source] .card-tag')).toHaveText(Array(12).fill('等待采集'));
  await page.getByRole('button', { name: '仅错误与提示', exact: true }).click();
  await expect(page.locator('.upstream-error-card')).toHaveCount(0);
  await expect(page.locator('.upstream-empty')).toContainText('当天没有错误或提示');
  await expect(page.locator('#upstreamErrorBadge')).toBeHidden();
});

test('successes, zero additions, duplicates and delayed publication have clear counts', async ({ page }) => {
  const rows = records.filter(row => row.status === 'done').map(row => ({ ...row, notices: [] }));
  await mockHistory(page, () => ({ ...payload(rows), unresolved_count: 0 }));
  await page.goto('/');
  await page.locator('#upstreamErrorsBtn').click();
  await expect(page.getByRole('dialog', { name: '采集记录', exact: true })).toBeVisible();
  await expect(page.locator('#upstreamErrorBadge')).toBeHidden();
  await expect(page.locator('.upstream-error-card')).toHaveCount(2);
  await expect(page.locator('.upstream-log-box')).toHaveCount(0);
  await expect(page.locator('.upstream-stat-value')).toHaveText(['45 条', '38 条', '7 条', '0 条']);
  await page.getByText('来源明细（4）', { exact: true }).click();
  const waiting = page.locator('[data-upstream-source="source-4"]');
  await expect(waiting).toContainText('等待整批入库');
  await expect(waiting).toContainText('纳入数据库 0 条');
  await expect(page.locator('[data-upstream-source="source-2"]')).toContainText('抓取 0 条 · 去重 0 条 · 纳入数据库 0 条');
  await expect(page.locator('.upstream-error-card').filter({ hasText: 'X · ilyasut' })).toContainText('跨日入库');
  await page.getByRole('button', { name: '仅错误与提示', exact: true }).click();
  await expect(page.locator('.upstream-error-card')).toHaveCount(0);
  await expect(page.locator('.upstream-empty')).toContainText('当天没有错误或提示');
  await expect(page.locator('.upstream-stat-value')).toHaveText(['45 条', '38 条', '7 条', '0 条']);
  await page.getByRole('button', { name: '全部信息', exact: true }).click();
  await expect(page.locator('.upstream-error-card')).toHaveCount(2);
});

test('historical unknown counts remain explicit and a recovered badge does not hide errors', async ({ page }) => {
  const old = records.map(row => ({ ...row, duplicate_count: null }));
  await mockHistory(page, () => ({ ...payload(old), unresolved_count: 0 }));
  await page.goto('/');
  await page.locator('#upstreamErrorsBtn').click();
  await expect(page.locator('#upstreamErrorBadge')).toBeHidden();
  await expect(page.locator('.upstream-stat-value').nth(1)).toHaveText('未记录');
  await expect(page.locator('.upstream-stat-value').nth(1).locator('..')).toHaveAttribute('title', /未保存去重数，无法还原完整数量/);
  await expect(page.locator('.collection-record-summary').first()).toContainText('去重 未记录');
  await page.getByRole('button', { name: '仅错误与提示', exact: true }).click();
  await expect(page.locator('.upstream-error-card')).toHaveCount(1);
  await expect(page.locator('.upstream-log-box')).toHaveCount(1);
  await expect(page.locator('#upstreamErrorsContent script')).toHaveCount(0);
  expect(await page.evaluate(() => window.collectionInjected)).toBeUndefined();
});

test('automatic refresh updates attention records while keeping the selected view and scroll', async ({ page }) => {
  let published = false, detailRequests = 0;
  await mockHistory(page, params => {
    if (params.get('details') === '1') detailRequests++;
    const rows = records.map(row => row.label === 'Reuters · Technology' && published
      ? { ...row, ingested_count: 2, publication_status: 'done', notices: [] } : row);
    return payload(rows);
  });
  await page.goto('/');
  await page.locator('#upstreamErrorsBtn').click();
  await expect(page.locator('.upstream-error-card')).toHaveCount(2);
  await page.getByRole('button', { name: '仅错误与提示', exact: true }).click();
  await expect(page.locator('.upstream-error-card')).toHaveCount(1);
  await expect(page.locator('.collection-record-notice')).toHaveCount(1);
  await expect(page.locator('.upstream-log-box')).toHaveCount(1);
  const summary = page.locator('[data-upstream-details="evening"] summary');
  await summary.click();
  await summary.focus();
  published = true;
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(page.locator('.collection-record-notice')).toHaveCount(0);
  await expect(summary).toBeFocused();
  await expect(page.locator('[data-upstream-details="evening"]')).toHaveAttribute('open', '');
  await expect(page.locator('.upstream-error-card')).toHaveCount(1);
  await expect(page.getByRole('button', { name: '仅错误与提示', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.upstream-stat-value').nth(2)).toHaveText('9 条');
  await expect(page.locator('#upstreamErrorDate')).toHaveValue(today);
  await page.getByRole('button', { name: '全部信息', exact: true }).click();
  await expect(page.locator('[data-upstream-source="source-4"]')).toContainText('纳入数据库 2 条');
  const scroll = await page.locator('#upstreamErrorsContent').evaluate(node => { node.scrollTop = 80; window.historyCard = node.querySelector('article'); return node.scrollTop; });
  const before = detailRequests;
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect.poll(() => detailRequests).toBeGreaterThan(before);
  await expect(page.locator('#upstreamErrorsContent')).toHaveAttribute('aria-busy', 'false');
  expect(await page.evaluate(() => window.historyCard.isConnected)).toBe(true);
  expect(await page.locator('#upstreamErrorsContent').evaluate(node => node.scrollTop)).toBe(scroll);
});

test('sources share their collection batch and distinct runs remain separate at the same time', async ({ page }) => {
  const rows = [...records, { ...records[0], run_id: 'another-run', source_id: 'another-source' }];
  await mockHistory(page, () => payload(rows));
  await page.goto('/');
  await page.locator('#upstreamErrorsBtn').click();
  await expect(page.locator('.upstream-error-card')).toHaveCount(3);
  const batch = page.locator('[data-upstream-batch="evening"]');
  await expect(batch.locator('.upstream-source-title')).toHaveText('2026-11-09 18:00:00 采集');
  await expect(batch.locator('.collection-record-summary')).toHaveText('抓取 45 条 · 去重 38 条 · 纳入数据库 5 条 · 错误 1 条');
  await expect(batch.locator('details')).not.toHaveAttribute('open', '');
  await expect(batch.locator('.upstream-log-box')).toContainText('等待下次采集');
  await expect(batch.locator('.upstream-log-box button')).toHaveText('复制日志');
  await expect(page.getByRole('button', { name: '按时间分类' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '按来源分类' })).toHaveCount(0);
  await batch.locator('summary').click();
  await expect(batch.locator('[data-upstream-source]')).toHaveCount(6);
  await expect(batch.locator('[data-upstream-source="source-0"]')).toBeVisible();
  await page.getByRole('button', { name: '仅错误与提示', exact: true }).click();
  await expect(page.locator('.upstream-error-card')).toHaveCount(1);
  await expect(batch.locator('details')).toHaveAttribute('open', '');
  await page.getByRole('button', { name: '全部信息', exact: true }).click();
  await expect(page.locator('.upstream-error-card')).toHaveCount(3);
  await expect(batch.locator('details')).toHaveAttribute('open', '');
});

test('translation notices retry only their source and keep acquisition errors and expanded details', async ({ page }) => {
  let retried = false, release;
  const requests = [];
  const gate = new Promise(resolve => { release = resolve; });
  await mockHistory(page, () => payload(records.map(row => row.source_id === 'source-4' && retried ? { ...row, notices: [] } : row)));
  await page.route('**/api/collection/retry-translation', async route => {
    requests.push(route.request().postDataJSON());
    await gate;
    retried = true;
    await route.fulfill({ status: 202, json: { ok: true, retried: 2 } });
  });
  try {
    await page.goto('/');
    await page.locator('#upstreamErrorsBtn').click();
    await page.getByRole('button', { name: '仅错误与提示', exact: true }).click();
    await page.getByText('来源明细（6）', { exact: true }).click();
    const retry = page.getByRole('button', { name: '重试翻译 · Reuters · Technology', exact: true });
    await retry.focus();
    await page.keyboard.press('Enter');
    await expect(retry).toBeDisabled();
    await expect(retry).toHaveAttribute('aria-busy', 'true');
    expect(requests).toEqual([{ run_id: 'evening', stage: 'translation', source_id: 'source-4' }]);
    await expect(page.locator('[data-upstream-details="evening"]')).toHaveAttribute('open', '');
    release();
    await expect(page.locator('.collection-record-notice')).toHaveCount(0);
    await expect(page.locator('.upstream-log-box')).toHaveCount(1);
    await expect(page.locator('.upstream-stat-value').last()).toHaveText('1 条');
    await expect(page.getByRole('button', { name: '仅错误与提示', exact: true })).toBeFocused();
    await expect(page.locator('#upstreamErrorDate')).toHaveValue(today);
    await expect(page.locator('[data-upstream-details="evening"]')).toHaveAttribute('open', '');
  } finally { release(); }
});

test('publication notices provide one batch retry and retain actionable feedback on failure', async ({ page }) => {
  let complete = false;
  const requests = [];
  const rows = records.slice(0, 2).map(row => ({ ...row, status: 'done', error: '', error_count: 0,
    publication_status: 'failed', notices: [{ stage: 'publication', error: 'collection_publish_failed' }] }));
  await mockHistory(page, () => payload(rows.map(row => complete ? { ...row, publication_status: 'done', notices: [] } : row)));
  await page.route('**/api/collection/retry-translation', route => {
    requests.push(route.request().postDataJSON());
    complete = requests.length > 1;
    return route.fulfill({ status: complete ? 202 : 503, json: complete ? { ok: true, retried: 1 } : { ok: false, error: 'unavailable' } });
  });
  await page.goto('/');
  await page.locator('#upstreamErrorsBtn').click();
  await page.getByRole('button', { name: '仅错误与提示', exact: true }).click();
  await expect(page.locator('.collection-record-notice')).toHaveCount(1);
  await expect(page.locator('.upstream-log-box')).toHaveCount(0);
  await expect(page.locator('.upstream-stat-value').last()).toHaveText('0 条');
  const retry = page.getByRole('button', { name: '重试入库', exact: true });
  await retry.click();
  await expect(page.locator('.collection-record-notice [role="alert"]')).toBeVisible();
  await expect(retry).toBeEnabled();
  await expect(retry).toBeFocused();
  await retry.click();
  await expect(page.locator('.upstream-empty')).toContainText('当天没有错误或提示');
  expect(requests).toEqual(Array(2).fill({ run_id: 'evening', stage: 'publication' }));
});

for (const [surface, options] of [
  ['desktop', { viewport: { width: 1440, height: 900 } }],
  ['mobile', { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true }],
]) test.describe(surface, () => {
  test.use(options);
  for (const [theme, colorScheme] of [['light', 'light'], ['dark', 'dark'], ['system', 'light'], ['system', 'dark'], ['eink', 'light']]) {
    test(`collection history fits ${theme}/${colorScheme} and every reading size`, async ({ page }) => {
      await mockHistory(page);
      await page.emulateMedia({ colorScheme });
      await page.goto('/');
      await chooseTheme(page, theme);
      if (surface === 'mobile') expect(await page.evaluate(() => matchMedia('(pointer: coarse)').matches)).toBe(true);
      for (const size of ['small', 'standard', 'large', 'extra-large']) {
        const label = await page.locator(`#fontSizeSelect-native option[value="${size}"]`).textContent();
        await page.getByRole('combobox', { name: '阅读字号', exact: true }).click();
        await page.getByRole('option', { name: label, exact: true }).click();
        await page.locator('#upstreamErrorsBtn').click();
        await expect(page.locator('.upstream-stat-label')).toHaveText(['抓取', '去重', '纳入数据库', '错误']);
        await expect(page.locator('.upstream-stat-value')).toHaveText(['45 条', '38 条', '7 条', '1 条']);
        await expect(page.locator('.collection-record-summary').first()).toHaveCSS('font-size', '12px');
        await expect(page.locator('#upstreamErrorsTitle')).toHaveCSS('font-size', '16px');
        const panel = page.locator('#upstreamErrorsPanel');
        const bounds = await panel.boundingBox();
        expect(bounds.x).toBeGreaterThanOrEqual(12);
        expect(bounds.x + bounds.width).toBeLessThanOrEqual(options.viewport.width - 12);
        expect(bounds.y + bounds.height).toBeLessThanOrEqual(options.viewport.height - 12);
        expect(await panel.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(options.viewport.width);
        if (size === 'standard') await page.screenshot({ path: `.ui-artifacts/v051-history-${surface}-${theme}-${colorScheme}.png` });
        await page.locator('#closeUpstreamErrorsBtn').click();
      }
    });
  }
});
