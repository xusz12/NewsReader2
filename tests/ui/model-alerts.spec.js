import { test, expect } from '@playwright/test';
import { chooseTheme } from './helpers.js';

const day = '2026-11-12';
const healthy = { ok: true, enabled: true, configured: true, total: 3, done: 3, pending: 0, running: 0, failed: 0, error: '', errors: [] };
const blocked = { ...healthy, done: 0, pending: 3, error: 'http_401', errors: [{ code: 'http_401', status: 'pending', count: 1 }] };
const history = () => ({ ok: true, day, today: day, unresolved_count: 0, notice_count: 0, active_notices: [], records: [],
  stats: { fetched_count: 0, duplicate_count: 0, ingested_count: 0, error_count: 0 } });
const notice = source => ({ run_id: 'old-run', source_id: source, label: source, date: '2026-11-11',
  notices: [{ stage: 'translation', count: 2, error: '2 条：translation_failed | deepseek: HTTP_401 | opencode-go: HTTP_429 | pi: TIMEOUT' }] });

async function mock(page, state) {
  await page.route('**/api/news/classification', route => route.fulfill({ json: state.classification || healthy }));
  await page.route('**/api/error-stats?*', route => {
    const params = new URL(route.request().url()).searchParams, data = state.history || history();
    return route.fulfill({ json: params.get('details') === '0'
      ? { ok: true, today: day, day, unresolved_count: data.unresolved_count, notice_count: data.notice_count }
      : { ...data, day: params.get('day') || day } });
  });
}

for (const width of [390, 1440]) test(`classification failure is visible and retryable from collection history ${width}`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 });
  const state = { classification: blocked }, writes = [];
  await mock(page, state);
  await page.route('**/api/news/classification/retry', async route => {
    writes.push(route.request().method());
    if (writes.length === 1) await route.fulfill({ status: 503, json: { ok: false, error: 'unavailable' } });
    else { state.classification = healthy; await route.fulfill({ json: { ok: true, retried: 3 } }); }
  });
  await page.goto('/');
  await expect(page.locator('#upstreamErrorBadge')).toHaveText('1');
  await expect(page.locator('#upstreamErrorsBtn')).toHaveClass(/has-errors/);
  await expect(page.locator('#upstreamErrorsBtn')).toHaveAttribute('aria-label', /新闻分类.*HTTP 401/);
  await page.locator('#upstreamErrorsBtn').click();
  const card = page.locator('[data-upstream-model="classification"]');
  await expect(card).toContainText('分类已暂停');
  await expect(card).toContainText('鉴权失败（HTTP 401）');
  await page.getByRole('button', { name: '仅错误与提示', exact: true }).click();
  await expect(card).toBeVisible();
  const retry = card.getByRole('button', { name: '重试分类', exact: true });
  await retry.click();
  await expect(card.locator('[role="alert"]')).toBeVisible();
  await expect(retry).toBeEnabled();
  await expect(retry).toBeFocused();
  await retry.click();
  await expect(card).toHaveCount(0);
  await expect(page.locator('#upstreamErrorBadge')).toBeHidden();
  await expect(page.getByRole('button', { name: '仅错误与提示', exact: true })).toBeFocused();
  expect(writes).toEqual(['POST', 'POST']);
});

test('missing configuration, exhausted failures, automatic retries and rate limits all surface without flagging normal work', async ({ page }) => {
  const state = {};
  await mock(page, state);
  for (const [patch, message] of [
    [{ pending: 3, done: 0, configured: false }, '配置 Jev'],
    [{ failed: 3, done: 0 }, '3 条新闻分类失败'],
    [{ pending: 3, done: 0, errors: [{ code: 'network_error', status: 'pending', count: 3 }] }, '正在自动重试'],
    [{ pending: 3, done: 0, throttle: { rate_limited: true, effective_concurrency: 1, configured_concurrency: 4, cooldown_seconds: 30 } }, '30 秒后继续'],
    [{ pending: 3, done: 0 }, null],
    [{ ...blocked, enabled: false }, null],
    [{ configured: false }, null],
  ]) {
    state.classification = { ...healthy, ...patch };
    await page.goto('/');
    await page.locator('#upstreamErrorsBtn').click();
    const card = page.locator('[data-upstream-model="classification"]');
    if (message) {
      await expect(card).toContainText(message);
      await expect(page.locator('#upstreamErrorBadge')).toHaveText('1');
    } else {
      await expect(card).toHaveCount(0);
      await expect(page.locator('#upstreamErrorBadge')).toBeHidden();
    }
  }
  state.classification = { ...healthy, pending: 3, done: 0, configured: false };
  await page.goto('/');
  await page.locator('#upstreamErrorsBtn').click();
  await page.getByRole('button', { name: '检查分类设置', exact: true }).click();
  await expect(page.locator('#upstreamErrorsPanel')).toBeHidden();
  await expect(page.locator('#classificationSettings')).toBeVisible();
});

test('unresolved translation survives a date change, retries only its source, and clears the badge', async ({ page }) => {
  const state = { history: { ...history(), notice_count: 2, active_notices: [notice('Reuters'), notice('Bloomberg')] } };
  const writes = [];
  await mock(page, state);
  await page.route('**/api/collection/retry-translation', async route => {
    const body = route.request().postDataJSON(); writes.push(body);
    state.history.active_notices = state.history.active_notices.filter(row => row.source_id !== body.source_id);
    state.history.notice_count = state.history.active_notices.length;
    await route.fulfill({ status: 202, json: { ok: true, retried: 2 } });
  });
  await page.goto('/');
  await expect(page.locator('#upstreamErrorBadge')).toHaveText('2');
  await page.locator('#upstreamErrorsBtn').click();
  await page.getByRole('button', { name: '仅错误与提示', exact: true }).click();
  await expect(page.locator('[data-upstream-current-notice]')).toHaveCount(1);
  await expect(page.locator('[data-upstream-current-notice] .collection-record-notice')).toHaveCount(2);
  await expect(page.locator('[data-upstream-current-notice]').first()).toContainText('2026-11-11 采集');
  await expect(page.locator('[data-upstream-current-notice]').first()).toContainText('deepseek: HTTP_401');
  await expect(page.locator('.upstream-stat-value')).toHaveText(Array(4).fill('0 条'));
  await page.locator('#upstreamErrorDate').fill('2026-11-10');
  await page.locator('#upstreamErrorDate').press('Enter');
  await expect(page.locator('[data-upstream-current-notice]')).toHaveCount(1);
  await expect(page.locator('[data-upstream-current-notice] .collection-record-notice')).toHaveCount(2);
  await page.getByRole('button', { name: '重试翻译 · Reuters', exact: true }).click();
  await expect(page.locator('#upstreamErrorBadge')).toHaveText('1');
  await expect(page.locator('[data-upstream-current-notice]')).toHaveCount(1);
  await page.getByRole('button', { name: '重试翻译 · Bloomberg', exact: true }).click();
  await expect(page.locator('#upstreamErrorBadge')).toBeHidden();
  await expect(page.locator('[data-upstream-current-notice]')).toHaveCount(0);
  expect(writes).toEqual(['Reuters', 'Bloomberg'].map(source_id => ({ run_id: 'old-run', stage: 'translation', source_id })));
});

test('current and daily translation notices share one retry entry', async ({ page }) => {
  const active = { ...notice('Reuters'), date: day };
  await mock(page, { history: { ...history(), notice_count: 1, active_notices: [active], records: [
    { ...active, time: '09:00:00', batch_started_at: `${day}T09:00:00+08:00`, status: 'done', publication_status: 'pending', error_count: 0,
      fetched_count: 2, duplicate_count: 0, ingested_count: 0 },
  ] } });
  await page.goto('/');
  await page.locator('#upstreamErrorsBtn').click();
  await expect(page.locator('.collection-record-notice')).toHaveCount(1);
  await expect(page.locator('[data-upstream-current-notice]')).toHaveCount(0);
  await expect(page.locator('#upstreamErrorBadge')).toHaveText('1');
});

for (const width of [390, 1440]) for (const [theme, colorScheme] of [
  ['light', 'light'], ['dark', 'dark'], ['system', 'light'], ['system', 'dark'], ['eink', 'light'],
]) test(`model alerts fit shared controls ${width} ${theme}/${colorScheme}`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 });
  await page.emulateMedia({ colorScheme });
  await mock(page, { classification: blocked, history: { ...history(), notice_count: 1, active_notices: [notice('Reuters')] } });
  await page.goto('/');
  await chooseTheme(page, theme);
  for (const size of ['small', 'standard', 'large', 'extra-large']) {
    const label = await page.locator(`#fontSizeSelect-native option[value="${size}"]`).textContent();
    await page.getByRole('combobox', { name: '阅读字号', exact: true }).click();
    await page.getByRole('option', { name: label, exact: true }).click();
    await page.locator('#upstreamErrorsBtn').click();
    await expect(page.locator('#upstreamErrorBadge')).toHaveText('2');
    await expect(page.locator('[data-upstream-model="classification"]')).toBeVisible();
    await expect(page.locator('[data-upstream-current-notice]')).toBeVisible();
    const panel = page.locator('#upstreamErrorsPanel'), bounds = await panel.boundingBox();
    expect(bounds.x).toBeGreaterThanOrEqual(12);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(width - 12);
    expect(bounds.y + bounds.height).toBeLessThanOrEqual(888);
    expect(await panel.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
    if (size === 'standard') await page.screenshot({ path: `.ui-artifacts/model-alerts-${width}-${theme}-${colorScheme}.png` });
    await page.locator('#closeUpstreamErrorsBtn').click();
  }
});
