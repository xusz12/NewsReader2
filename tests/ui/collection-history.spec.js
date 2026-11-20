import { openSettingsItem, chooseTheme, collectionHeatmap, openSettings } from './helpers.js';
import { test, expect } from '@playwright/test';

const today = '2026-11-09';
const dailyErrors = { '2026-11-02': 0, '2026-11-03': 1, '2026-11-04': 3, '2026-11-05': 4, '2026-11-06': 7 };
const failedItem = { item_id: 'item-one', title: '标题 <img src=x onerror=window.collectionInjected=true>', status: 'failed', attempts: 3,
  url: 'https://www.reuters.com/world/china/a-long-article-title-to-abbreviate/?entry=17&edition=cn#report',
  reason: '模型已响应，但标题译文未检测到汉字。', error: 'translation_failed | deepseek: translation_not_chinese:title | pi: translation_not_chinese:title' };
const waitingItem = { item_id: 'item-two', title: '仍在重试的新闻', status: 'retry_wait', attempts: 2, reason: '模型未完整返回译文。',
  error: 'translation_incomplete', retry_at: '2026-11-09T18:05:00+08:00' };
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
  const errorCount = rows.reduce((sum, row) => sum + row.error_count, 0);
  return { ok: true, today, day, unresolved_count: 1, records: rows,
    heatmap: collectionHeatmap(day, today, { ...dailyErrors, [day]: rows.length ? errorCount : null }),
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
  await expect(page.locator('#upstreamHeatmap button[aria-pressed="true"]')).toHaveAccessibleName(/无采集记录/);
  await expect(page.locator('.upstream-stat-value')).toHaveText(['0 条', '0 条', '0 条', '0 条']);
  await page.locator('#closeUpstreamErrorsBtn').click();
  await openSettings(page);
  await openSettingsItem(page, 'pipeline');
  await page.locator('#settingsCollectBtn').click();
  await expect(page.locator('#settingsCollectBtn')).toBeDisabled();
  await page.keyboard.press('Escape');
  await expect(page.locator('#pipelineStatus')).toContainText('正在采集', {timeout:12000});
  await page.locator('#upstreamErrorsBtn').click();
  await expect(page.locator('.upstream-error-card')).toHaveCount(1);
  await expect(page.locator('#upstreamHeatmap button[aria-pressed="true"]')).toHaveAccessibleName(/0 次报错/);
  await expect(page.locator('.upstream-card-head > .card-tag')).toHaveText('等待采集');
  await page.getByText('来源明细（12）', { exact: true }).click();
  await expect(page.locator('[data-upstream-source] .card-tag')).toHaveText(Array(12).fill('等待采集'));
  await page.getByRole('button', { name: '仅错误与提示', exact: true }).click();
  await expect(page.locator('.upstream-error-card')).toHaveCount(0);
  await expect(page.locator('.upstream-empty')).toContainText('当天没有错误或提示');
  await expect(page.locator('#upstreamErrorBadge')).toBeHidden();
});

test('annual heatmap aligns weekdays across weeks and opens the selected day', async ({ page }) => {
  await mockHistory(page, params => {
    const day = params.get('day') || today;
    const count = dailyErrors[day];
    const rows = day === today ? records : count == null ? [] : count === 0 ? [records[0]]
      : Array.from({ length: count }, (_, i) => ({ ...records[1], source_id: `failed-${i}` }));
    return payload(rows, day);
  });
  await page.goto('/');
  await page.locator('#upstreamErrorsBtn').click();
  const group = page.getByRole('grid', { name: '运行热力图' });
  const day = date => group.getByRole('button', { name: new RegExp(`^${date}`) });
  await expect(group.getByRole('button')).toHaveCount(365);
  await expect(group.locator('tbody tr')).toHaveCount(7);
  await expect(group.locator('tbody tr').first().locator('td')).toHaveCount(53);
  await expect(group.locator('button:not(:empty)')).toHaveCount(0);
  const wed = await day('2026-11-04').boundingBox();
  const thu = await day('2026-11-05').boundingBox();
  const priorWeek = await day('2026-10-28').boundingBox();
  expect(wed.x).toBe(thu.x);
  expect(wed.y).toBeLessThan(thu.y);
  expect(priorWeek.y).toBe(wed.y);
  expect(priorWeek.x).toBeLessThan(wed.x);
  expect(Math.abs(wed.width - wed.height)).toBeLessThan(1);
  for (const [date, status] of [['01', 'empty'], ['02', 'success'], ['03', 'warning'], ['04', 'warning'], ['05', 'failed']]) {
    await expect(day(`2026-11-${date}`)).toHaveAttribute('data-status', status);
  }
  await page.getByRole('button', { name: '仅错误与提示', exact: true }).click();
  await day('2026-11-04').click();
  const currentErrors = page.locator('.upstream-stat-value').last();
  await expect(currentErrors).toHaveText('3 条');
  await expect(day('2026-11-04')).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await expect(day('2026-11-05')).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(currentErrors).toHaveText('4 条');
  await expect(day('2026-11-05')).toHaveAttribute('aria-pressed', 'true');
  await expect(day('2026-11-05')).toBeFocused();
  await expect(page.locator('#upstreamErrorDate')).toHaveValue('2026-11-05');
  await expect(page.getByRole('button', { name: '仅错误与提示', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await page.route('**/api/error-stats?details=1&day=2026-11-01', route => route.fulfill({ status: 500, json: { error: 'request_failed' } }), { times: 1 });
  await day('2026-11-01').click();
  await expect(page.locator('#upstreamLoadMessage')).toContainText('仍显示 2026-11-05');
  await expect(day('2026-11-05')).toHaveAttribute('aria-pressed', 'true');
  await page.locator('[data-upstream-retry]').click();
  await expect(day('2026-11-01')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.upstream-empty')).toContainText('当天没有记录');
  await page.locator('#upstreamPreviousDayBtn').click();
  await expect(day('2026-10-31')).toHaveAttribute('aria-pressed', 'true');
  await page.locator('#upstreamErrorDate').fill('2024-02-29');
  await page.locator('#upstreamErrorDate').press('Enter');
  await expect(day('2024-02-29')).toHaveAttribute('aria-pressed', 'true');
  await expect(day(today)).toHaveCount(0);
});

test('year grid fits desktop and opens the latest weeks on a narrow screen', async ({ page }) => {
  const data = payload(records.map(row => ({ ...row, error: row.error ? 'HTTP 429 · 来源请求限流' : '' })));
  for (let offset = 1; offset < 310; offset++) {
    const date = new Date(Date.parse(today) - offset * 86400000).toISOString().slice(0, 10);
    const sample = (offset * 67 + Math.floor(offset / 7) * 19) % 101;
    if (sample >= 13) data.heatmap.errors_by_day[date] = sample < 19 ? 4 : sample < 34 ? 2 : 0;
  }
  await mockHistory(page, () => data);
  await page.goto('/');
  await chooseTheme(page, 'light');
  await page.locator('#upstreamErrorsBtn').click();
  const chart = page.locator('#upstreamHeatmap');
  const scroller = chart.locator('.upstream-heat-scroll');
  const current = chart.locator('[data-heat-day][aria-pressed="true"]');
  await expect(current).toHaveAttribute('data-heat-day', today);
  expect(await scroller.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
  await chart.screenshot({ path: '.ui-artifacts/collection-heatmap-desktop.png' });
  await page.locator('#upstreamErrorsPanel').screenshot({ path: '.ui-artifacts/collection-heatmap-panel.png' });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => scroller.evaluate(node => node.scrollLeft)).toBeGreaterThan(0);
  const viewport = await scroller.boundingBox();
  const latestCell = await current.boundingBox();
  expect(latestCell.x + latestCell.width).toBeLessThanOrEqual(viewport.x + viewport.width);
  await chart.screenshot({ path: '.ui-artifacts/collection-heatmap-mobile.png' });
  const table = await scroller.locator('table').elementHandle();
  await scroller.evaluate(node => { node.scrollLeft = 0; });
  const response = page.waitForResponse(r => r.url().includes('/api/error-stats?details=1'));
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await response;
  await expect(page.locator('#upstreamErrorsContent')).toHaveAttribute('aria-busy', 'false');
  expect(await table.evaluate(node => node.isConnected)).toBe(true);
  expect(await scroller.evaluate(node => node.scrollLeft)).toBe(0);
  await page.locator('#closeUpstreamErrorsBtn').click();
  await page.setViewportSize({ width: 1440, height: 900 });
  await chooseTheme(page, 'dark');
  await page.locator('#upstreamErrorsBtn').click();
  await expect(current).toHaveAttribute('data-heat-day', today);
  await chart.screenshot({ path: '.ui-artifacts/collection-heatmap-dark.png' });
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
  await page.getByText('来源明细（2）', { exact: true }).click();
  const table = page.getByRole('table', { name: /来源明细/ });
  await expect(table.getByRole('columnheader')).toHaveText(['来源', '抓取', '去重', '纳入条数']);
  await expect(table).not.toContainText(/\d{2}:\d{2}:\d{2}/);
  const waiting = page.locator('[data-upstream-source="source-4"]');
  await expect(waiting).toContainText('处理中');
  await expect(waiting.getByRole('cell').last()).toHaveText('0 条');
  await expect(page.locator('[data-upstream-source="source-2"], [data-upstream-source="source-3"]')).toHaveCount(0);
  await expect(table.getByRole('rowheader')).toHaveCount(2);
  await expect(page.locator('.upstream-error-card').filter({ hasText: 'X · ilyasut' })).toContainText('跨日入库');
  await page.getByRole('button', { name: '仅错误与提示', exact: true }).click();
  await expect(page.locator('.upstream-error-card')).toHaveCount(0);
  await expect(page.locator('.upstream-empty')).toContainText('当天没有错误或提示');
  await expect(page.locator('.upstream-stat-value')).toHaveText(['45 条', '38 条', '7 条', '0 条']);
  await page.getByRole('button', { name: '全部信息', exact: true }).click();
  await expect(page.locator('.upstream-error-card')).toHaveCount(2);
});

test('historical unknown counts remain explicit and a recovered badge does not hide errors', async ({ page }) => {
  const old = records.map(row => ({ ...row, duplicate_count: null, ingested_count: null }));
  await mockHistory(page, () => ({ ...payload(old), unresolved_count: 0 }));
  await page.goto('/');
  await page.locator('#upstreamErrorsBtn').click();
  await expect(page.locator('#upstreamErrorBadge')).toBeHidden();
  await expect(page.locator('.upstream-stat-value').nth(1)).toHaveText('未记录');
  await expect(page.locator('.upstream-stat-value').nth(1).locator('..')).toHaveAttribute('title', /未保存去重数，无法还原完整数量/);
  await expect(page.locator('.collection-record-summary').first()).toContainText('去重 未记录');
  await page.getByText('来源明细（6）', { exact: true }).click();
  await expect(page.locator('[data-upstream-source="source-0"]').getByRole('cell')).toHaveText(['25', '未记录', '未记录']);
  await page.getByRole('button', { name: '仅错误与提示', exact: true }).click();
  await expect(page.locator('.upstream-error-card')).toHaveCount(1);
  await expect(page.locator('.upstream-log-box:visible')).toHaveCount(1);
  await expect(page.locator('#upstreamErrorsContent script')).toHaveCount(0);
  expect(await page.evaluate(() => window.collectionInjected)).toBeUndefined();
});

test('a completed batch with only zero additions keeps its totals and drops the empty disclosure on refresh', async ({ page }) => {
  let status = 'running';
  await mockHistory(page, () => payload([{ ...records[3], status }]));
  await page.goto('/');
  await page.locator('#upstreamErrorsBtn').click();
  const batch = page.locator('[data-upstream-batch="evening"]');
  await batch.locator('summary').click();
  await expect(batch.getByRole('rowheader')).toContainText('采集中');
  await batch.locator('summary').focus();
  status = 'done';
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(batch.locator('details')).toHaveCount(0);
  await expect(batch.locator('.collection-record-summary')).toHaveText('抓取 12 条 · 去重 12 条 · 纳入数据库 0 条 · 错误 0 条');
  await expect(page.locator('.upstream-stat-value')).toHaveText(['12 条', '12 条', '0 条', '0 条']);
  await expect(page.getByRole('button', { name: '全部信息', exact: true })).toBeFocused();
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
  await expect(page.locator('.upstream-log-box:visible')).toHaveCount(1);
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
  await expect(page.locator('[data-upstream-source="source-4"]').getByRole('cell').last()).toHaveText('2 条');
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
  await expect(batch.locator('.upstream-card-head .upstream-source-title')).toHaveText('2026-11-09 18:00:00 采集');
  await expect(batch.locator('.collection-record-summary')).toHaveText('抓取 45 条 · 去重 38 条 · 纳入数据库 5 条 · 错误 1 条');
  const sources = batch.locator('[data-upstream-details="evening"]');
  await expect(sources).not.toHaveAttribute('open', '');
  await expect(batch.locator('.upstream-log-box:visible')).toHaveCount(0);
  await expect(batch).not.toContainText('TIMEOUT');
  await expect(page.getByRole('button', { name: '按时间分类' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '按来源分类' })).toHaveCount(0);
  await sources.locator('summary').click();
  await expect(batch.locator('[data-upstream-source]')).toHaveCount(4);
  const failedSource = batch.locator('[data-upstream-source="source-1"]');
  await expect(failedSource.getByRole('rowheader')).toContainText('采集错误');
  await expect(failedSource.locator('.status-text')).toHaveAttribute('data-status', 'failed');
  await expect(batch.locator('[data-upstream-source="source-0"]')).toBeVisible();
  await page.getByRole('button', { name: '仅错误与提示', exact: true }).click();
  await expect(page.locator('.upstream-error-card')).toHaveCount(1);
  await expect(batch.locator('.upstream-log-box:visible')).toContainText('等待下次采集');
  await expect(batch.locator('.upstream-log-box:visible pre')).toHaveText(records[1].error);
  await expect(batch.locator('.upstream-log-box button')).toHaveText('复制日志');
  await expect(failedSource.getByRole('rowheader')).toContainText('连接超时');
  await expect(sources).toHaveAttribute('open', '');
  await page.getByRole('button', { name: '全部信息', exact: true }).click();
  await expect(page.locator('.upstream-error-card')).toHaveCount(3);
  await expect(sources).toHaveAttribute('open', '');
  await expect(batch).not.toContainText('连接超时');
  await expect(page.locator('[data-upstream-copy]')).toHaveCount(0);
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
    await page.getByText('来源明细（4）', { exact: true }).click();
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

test('an isolated news item can be retried without disturbing a sibling in backoff', async ({ page }) => {
  let retried = false;
  const requests = [];
  await mockHistory(page, () => {
    const items = retried ? [waitingItem] : [failedItem, waitingItem];
    return { ...payload([]), active_notices: [{ run_id: 'evening', source_id: 'source-4', label: 'Reuters · Technology',
      date: '2026-11-08', notices: [{ stage: 'translation', count: items.length, items }] }] };
  });
  await page.route('**/api/collection/retry-translation', route => {
    requests.push(route.request().postDataJSON());
    retried = true;
    return route.fulfill({ status: 202, json: { ok: true, retried: 1 } });
  });
  await page.goto('/');
  await page.locator('#upstreamErrorsBtn').click();
  const card = page.locator('[data-upstream-current-notice]');
  await expect(card).toContainText('2026-11-08');
  const notice = card.locator('.collection-record-notice').filter({ hasText: failedItem.title });
  await expect(notice).toContainText('Reuters · Technology · 已隔离 · 已尝试 3 次');
  await expect(notice.getByText(failedItem.reason + '其他新闻可继续入库。')).toBeVisible();
  await expect(notice.locator('img')).toHaveCount(0);
  const log = notice.locator('pre');
  await expect(log).toBeHidden();
  await expect(notice.getByRole('link')).toHaveCount(0);
  await page.getByRole('button', { name: '仅错误与提示', exact: true }).click();
  const link = notice.getByRole('link', { name: /打开原文/ });
  await expect(link).toHaveAttribute('href', failedItem.url);
  await expect(link).toHaveText('原文 · reuters.com/…/a-long-article-ti…');
  await expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  await page.context().route('https://www.reuters.com/**', route => route.fulfill({ contentType: 'text/plain', body: '原文' }));
  const opened = page.waitForEvent('popup');
  await link.click();
  const article = await opened;
  await expect(article).toHaveURL(failedItem.url);
  await article.close();
  await expect(page.getByRole('button', { name: '仅错误与提示', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await notice.getByText('技术详情', { exact: true }).click();
  await expect(log).toBeVisible();
  await expect(log).toHaveText(failedItem.error);
  const waiting = card.locator('.collection-record-notice').filter({ hasText: waitingItem.title });
  await expect(waiting).toContainText('11-09 18:05 自动重试');
  await expect(waiting.getByRole('link')).toHaveCount(0);
  await expect(waiting.getByRole('button')).toHaveCount(0);
  await notice.getByRole('button', { name: '重试此条 · ' + failedItem.title }).press('Enter');
  await expect(notice).toHaveCount(0);
  await expect(waiting).toBeVisible();
  expect(requests).toEqual([{ run_id: 'evening', stage: 'translation', source_id: 'source-4', item_id: 'item-one' }]);
});

test('daily translation notices retain their own links and disclosures on refresh', async ({ page }) => {
  let attempts = 1;
  const waiting = { ...waitingItem, url: 'https://example.org/story?id=17#original' };
  const unsafe = { ...failedItem, item_id: 'unsafe', url: 'javascript:void(0)' };
  const privateURL = { ...failedItem, item_id: 'private-url', url: 'https://name:password@example.org/story' };
  await mockHistory(page, () => payload([{ ...records[0], notices: [{ stage: 'translation', count: 4,
    items: [failedItem, { ...waiting, attempts }, unsafe, privateURL] }] }]));
  await page.goto('/');
  await page.locator('#upstreamErrorsBtn').click();
  await page.getByRole('button', { name: '仅错误与提示', exact: true }).click();
  const notices = page.locator('.collection-record-notice');
  await expect(notices.getByRole('link')).toHaveCount(2);
  const notice = notices.filter({ hasText: waiting.title });
  const details = notice.locator('details');
  await details.locator('summary').press('Enter');
  await expect(details.locator('pre')).toBeVisible();
  const link = notice.getByRole('link');
  await expect(link).toHaveAttribute('href', waiting.url);
  await link.focus();
  attempts++;
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(notice).toContainText('已尝试 2 次');
  await expect(link).toBeFocused();
  await expect(details.locator('pre')).toBeVisible();
  await expect(notices.locator('details[open]')).toHaveCount(1);
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
  await retry.press('Enter');
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
  test('expanded source details keep names and actual additions aligned on desktop and phones', async ({ page }) => {
    const rows = [
      { ...records[0], time: '18:00:30', fetched_count: 28, duplicate_count: 14, filtered_count: 2, ingested_count: 12 },
      { ...records[4], filtered_count: 0, notices: [] },
      { ...records[3], fetched_count: 9, duplicate_count: 3, filtered_count: 1, ingested_count: 5 },
      { ...records[2], fetched_count: 10, duplicate_count: 7, filtered_count: 3 },
      { ...records[1], error: 'HTTP 429 · 来源请求限流', filtered_count: 0 },
    ];
    await mockHistory(page, () => payload(rows));
    await page.goto('/');
    await chooseTheme(page, 'light');
    await page.locator('#upstreamErrorsBtn').click();
    const details = page.locator('[data-upstream-details="evening"]');
    await details.locator('summary').focus();
    await page.keyboard.press('Enter');
    const table = details.getByRole('table', { name: /来源明细/ });
    await expect(details.locator('summary')).toHaveText('来源明细（4）');
    await expect(table.getByRole('row', { name: /TechCrunch/ })).toHaveCount(0);
    const headers = table.getByRole('columnheader');
    const source = table.getByRole('row').filter({ hasText: 'Reuters · World' });
    const desktop = surface === 'desktop';
    await expect(headers).toHaveText(desktop ? ['来源', '抓取', '去重', '过滤', '纳入条数'] : ['来源', '纳入条数']);
    await expect(source.getByRole('cell')).toHaveText(desktop ? ['28', '14', '2', '12 条'] : ['12 条']);
    const secondary = source.locator('.collection-source-meta');
    if (desktop) await expect(secondary).toBeHidden();
    else {
      await expect(secondary).toBeVisible();
      await expect(secondary).toHaveText('抓取 28 · 去重 14 · 过滤 2');
    }
    await expect(table).not.toContainText(/\d{2}:\d{2}:\d{2}/);
    const card = details.locator('.data-table-card');
    expect(await card.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
    await headers.first().click();
    await page.locator('[data-upstream-batch="evening"]').screenshot({ path: `.ui-artifacts/collection-source-details-${surface}.png` });
    if (!desktop) {
      await page.locator('#upstreamErrorsContent').evaluate(node => { node.scrollTop = 0; });
      await page.locator('#upstreamErrorsPanel').screenshot({ path: '.ui-artifacts/collection-compact-controls-mobile.png' });
      await page.getByRole('button', { name: '仅错误与提示', exact: true }).tap();
      const log = page.locator('.upstream-log-box');
      const copy = log.getByRole('button', { name: /复制.*日志/ });
      await expect(copy).toBeVisible();
      expect((await copy.boundingBox()).height).toBe(24);
      const contentHeight = (await log.locator('pre').boundingBox()).height;
      expect((await log.boundingBox()).height - contentHeight).toBeLessThanOrEqual(44);
      await log.screenshot({ path: '.ui-artifacts/collection-compact-error-mobile.png' });
      await page.getByRole('button', { name: '全部信息', exact: true }).tap();
      await page.setViewportSize({ width: 320, height: 844 });
    }
    rows[0].label = '很长的来源名称 · ' + 'InternationalTechnology'.repeat(4);
    rows[0].fetched_count = 1016;
    rows[0].ingested_count = 1000;
    await page.locator('#upstreamTodayBtn').click();
    const longSource = table.getByRole('row').filter({ hasText: 'InternationalTechnology' });
    await expect(longSource.getByRole('cell').last()).toHaveText('1000 条');
    const bounds = await longSource.evaluate(row => ({
      name: row.querySelector('strong').getBoundingClientRect().right,
      count: row.querySelector('td:last-child strong').getBoundingClientRect().left,
      table: row.closest('.data-table-card').getBoundingClientRect().right,
      end: row.querySelector('td:last-child').getBoundingClientRect().right,
    }));
    expect(bounds.name).toBeLessThan(bounds.count);
    expect(bounds.end).toBeLessThanOrEqual(bounds.table);
  });

  for (const [theme, colorScheme] of [['light', 'light'], ['dark', 'dark'], ['system', 'light'], ['system', 'dark'], ['eink', 'light']]) {
    test(`collection history fits ${theme}/${colorScheme} and every reading size`, async ({ page }) => {
      await mockHistory(page, () => payload(records.map(record => record.source_id === 'source-4'
        ? { ...record, notices: [{ stage: 'translation', count: 2, items: [failedItem, waitingItem] }] } : record)));
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
        const tiles = page.locator('.upstream-heatmap-legend .upstream-heat-tile');
        await expect(tiles).toHaveCount(4);
        const fills = await tiles.evaluateAll(nodes => nodes.map(node => {
          const style = getComputedStyle(node);
          return [style.backgroundColor, style.borderStyle].join(' ');
        }));
        expect(new Set(fills).size).toBe(4);
        const panel = page.locator('#upstreamErrorsPanel');
        const bounds = await panel.boundingBox();
        expect(bounds.x).toBeGreaterThanOrEqual(12);
        expect(bounds.x + bounds.width).toBeLessThanOrEqual(options.viewport.width - 12);
        expect(bounds.y + bounds.height).toBeLessThanOrEqual(options.viewport.height - 12);
        expect(await panel.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(options.viewport.width);
        if (size === 'standard') await page.screenshot({ path: `.ui-artifacts/v051-history-${surface}-${theme}-${colorScheme}.png` });
        const details = page.locator('[data-upstream-details="evening"]');
        if (!(await details.evaluate(node => node.open))) await details.locator('summary').click();
        const source = details.locator('[data-upstream-source="source-0"]');
        await expect(source.getByRole('rowheader').locator('strong')).toHaveText('Reuters · World');
        await expect(source.getByRole('cell').last().locator('strong')).toHaveText('5 条');
        await expect(source.getByRole('cell').last()).toHaveCSS('text-align', 'end');
        const name = source.getByRole('rowheader').locator('strong');
        const count = source.locator('.metric-value');
        const bodyFont = await page.locator('body').evaluate(node => getComputedStyle(node).fontFamily);
        for (const field of [name, count, page.locator('#upstreamErrorDate'), page.locator('#upstreamErrorsTitle'), page.locator('.upstream-stat-value').first()]) {
          await expect(field).toHaveCSS('font-family', bodyFont);
        }
        await expect(count).toHaveCSS('font-size', await name.evaluate(node => getComputedStyle(node).fontSize));
        const failed = details.locator('[data-upstream-source="source-1"]');
        const errorName = failed.getByRole('rowheader').locator('strong');
        const color = await errorName.evaluate(node => getComputedStyle(node).color);
        await expect(failed.locator('.card-tag')).toHaveCSS('color', color);
        await expect(page.locator('[data-upstream-batch="evening"] .upstream-card-head [data-status="failed"]')).toHaveCSS('color', color);
        if (theme !== 'eink') await expect(name).not.toHaveCSS('color', color);
        await expect(failed.locator('.card-tag')).toHaveText('采集错误');
        expect(await details.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
        await page.getByRole('button', { name: '仅错误与提示', exact: true }).click();
        const notice = panel.locator('.collection-record-notice').filter({ hasText: failedItem.title });
        await expect(notice.getByRole('link')).toBeVisible();
        await notice.locator('summary').click();
        await expect(notice.locator('pre')).toBeVisible();
        expect(await notice.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
        expect(await panel.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
        await notice.locator('summary').click();
        await page.getByRole('button', { name: '全部信息', exact: true }).click();
        await page.locator('#closeUpstreamErrorsBtn').click();
      }
    });
  }
});
