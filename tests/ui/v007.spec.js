import { test, expect } from '@playwright/test';

test.beforeEach(async ({ request }) => {
  await request.post('/__test/reset-reading');
});

test('opening the page scans once before loading news and counts', async ({ page }) => {
  let scans = 0;
  let releaseScan;
  const scanGate = new Promise(resolve => { releaseScan = resolve; });
  const reads = [];
  page.on('request', request => {
    if (/\/api\/(news\?|nav-summary|sources\?)/.test(request.url())) reads.push(request.url());
  });
  await page.route('**/api/reindex', async route => {
    scans++;
    expect(route.request().method()).toBe('POST');
    expect(route.request().postData()).toBeNull();
    await scanGate;
    await route.continue();
  });
  await page.goto('/');
  await expect(page.locator('#refreshIndexBtn')).toBeDisabled();
  await expect(page.locator('#pipelineStatus')).toContainText('正在扫描');
  await expect(page.locator('#feedCardList')).toContainText('正在读取新闻');
  expect(reads).toEqual([]);
  releaseScan();
  await expect(page.locator('#feedCardList .news-card').first()).toBeVisible();
  await expect(page.locator('#unreadBadge')).toHaveText('65');
  await expect(page.locator('#sourceFilters [data-source="all"] .nav-badge')).toHaveText('65');
  await expect(page.locator('#refreshIndexBtn')).toBeEnabled();
  expect(scans).toBe(1);
  await page.reload();
  await expect(page.locator('#feedCardList .news-card').first()).toBeVisible();
  expect(scans).toBe(2);
});

test('a failed startup scan still loads existing news and can be retried', async ({ page }) => {
  let scans = 0;
  await page.route('**/api/reindex', async route => {
    if (++scans === 1) {
      await route.fulfill({ status: 500, json: { ok: false, error: 'scan_failed' } });
    } else {
      await route.continue();
    }
  });
  await page.goto('/');
  await expect(page.locator('#feedCardList .news-card').first()).toBeVisible();
  await expect(page.locator('#pipelineStatus')).toContainText('索引刷新失败');
  await expect(page.locator('#refreshIndexBtn')).toBeEnabled();
  expect(scans).toBe(1);
  await page.locator('#refreshIndexBtn').click();
  await expect(page.locator('#pipelineStatus')).toContainText('DailyNews 增量索引已同步');
  await expect(page.locator('#toast')).toHaveText('新闻索引已刷新');
  await expect(page.locator('#refreshIndexBtn')).toBeEnabled();
  expect(scans).toBe(2);
});
