import { test, expect } from '@playwright/test';
import { openSettings, chooseTheme } from './helpers.js';

const defaults = { enabled: true, concurrency: 8, unread_only: false };
async function reset(request) {
  const saved = await (await request.get('/api/settings')).json();
  await request.put('/api/settings', { data: { llm: saved.llm, classification: defaults } });
}
async function section(page, name) {
  const button = page.locator(`[data-settings-tab="${name}"]`);
  if (await page.locator('#feedColSettings').evaluate(node => node.inert)) {
    await page.locator('#settingsCategoryBackBtn').click();
  }
  await button.click();
}
async function save(page) {
  await page.locator('#settingsSaveBtn').click();
  await expect(page.locator('#settingsStatus')).toHaveText('设置已保存');
}
test.beforeEach(async ({ request }) => reset(request));
test.afterEach(async ({ request }) => reset(request));

for (const width of [390, 1050, 1440]) {
  test(`classification owns controls and retains drafts and switches at ${width}px`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.route('**/api/news/classification', async route => {
      const result = await route.fetch();
      await route.fulfill({ json: { ...await result.json(), total: 65, done: 64, pending: 1 } });
    });
    await page.goto('/');
    await expect(page.locator('#classificationProgress')).toBeVisible();
    await openSettings(page);
    await section(page, 'classification');
    await expect(page.locator('#settingsDetailTitle')).toHaveText('新闻分类');
    const enabled = page.getByRole('switch', { name: '启用自动分类' });
    const unread = page.getByRole('switch', { name: '仅对未读新闻分类' });
    const concurrency = page.getByRole('spinbutton', { name: '分类并发上限' });
    await expect(enabled).toBeChecked();
    await expect(unread).not.toBeChecked();
    await expect(concurrency).toHaveValue('8');
    await enabled.focus();
    await enabled.press('Space');
    await expect(unread).toBeDisabled();
    await expect(concurrency).toBeDisabled();
    const disabledBox = await unread.boundingBox();
    await page.mouse.click(disabledBox.x + disabledBox.width / 2, disabledBox.y + disabledBox.height / 2);
    await expect(unread).not.toBeChecked();
    await enabled.focus();
    await page.keyboard.press('Tab');
    await expect(page.locator('#classificationApiSettingsBtn')).toBeFocused();
    await enabled.check();
    await expect(unread).toBeEnabled();
    await expect(concurrency).toBeEnabled();
    await unread.check();
    await concurrency.fill('33');
    await page.locator('#settingsSaveBtn').click();
    await expect(page.locator('#settingsStatus')).toHaveText('分类并发上限必须是 1–32 的整数');
    await concurrency.fill('12');
    await enabled.uncheck();
    await page.locator('#classificationApiSettingsBtn').click();
    await expect(page.locator('#settingsDetailTitle')).toHaveText('API 管理');
    await expect(page.getByRole('combobox', { name: 'API 服务', exact: true })).toContainText('Jev');
    await expect(page.locator('#translationSettings [name^="classification."], #translationSettings [name$=".concurrency"], #translationSettings [name$=".unread_only"]')).toHaveCount(0);
    await page.locator('input[name="api_models.jev.model"]').fill('jev-classification-test');
    await section(page, 'classification');
    await expect(enabled).not.toBeChecked();
    await expect(unread).toBeChecked();
    await expect(concurrency).toHaveValue('12');
    await expect(unread).toBeDisabled();
    await expect(concurrency).toBeDisabled();
    await page.locator('#settingsResetBtn').click();
    await expect(enabled).toBeChecked();
    await expect(unread).not.toBeChecked();
    await expect(concurrency).toHaveValue('8');
    await expect(unread).toBeEnabled();
    await expect(concurrency).toBeEnabled();
    await unread.check();
    await concurrency.fill('12');
    await enabled.uncheck();
    if (width <= 920) await page.locator('#settingsCategoryBackBtn').click();
    await page.locator('#closeSettingsBtn').click();
    await openSettings(page);
    await section(page, 'classification');
    await expect(enabled).not.toBeChecked();
    await save(page);
    const saved = await (await request.get('/api/settings')).json();
    expect(saved.classification).toEqual({ enabled: false, concurrency: 12, unread_only: true });
    expect(Object.keys(saved.api_models.jev)).toEqual(['model']);
    await expect(page.locator('#classificationProgress')).toBeHidden();
    await page.reload();
    await openSettings(page);
    await section(page, 'classification');
    await expect(enabled).not.toBeChecked();
    await expect(unread).toBeChecked();
    await expect(concurrency).toHaveValue('12');
    await expect(unread).toBeDisabled();
    await expect(concurrency).toBeDisabled();
    await enabled.check();
    await expect(unread).toBeEnabled();
    await expect(concurrency).toBeEnabled();
    await save(page);
    await expect(page.locator('#classificationProgress')).toBeVisible();
    expect((await (await request.get('/api/news/classification')).json()).enabled).toBe(true);
  });
}

test('failed save keeps the draft and leaves the live service enabled', async ({ page, request }) => {
  await page.goto('/');
  await openSettings(page);
  await section(page, 'classification');
  const enabled = page.getByRole('switch', { name: '启用自动分类' });
  await enabled.uncheck();
  await page.route('**/api/settings', route => route.request().method() === 'PUT'
    ? route.fulfill({ status: 503, json: { error: '保存失败' } }) : route.continue());
  await page.locator('#settingsSaveBtn').click();
  await expect(page.locator('#settingsStatus')).toContainText('保存失败');
  await expect(enabled).not.toBeChecked();
  expect((await (await request.get('/api/news/classification')).json()).enabled).toBe(true);
  await page.unroute('**/api/settings');
  await save(page);
  expect((await (await request.get('/api/news/classification')).json()).enabled).toBe(false);
});

test('closing classification with an invalid draft points to the switch so it can be corrected', async ({ page }) => {
  await page.goto('/');
  await openSettings(page);
  await section(page, 'classification');
  const enabled = page.getByRole('switch', { name: '启用自动分类' });
  const concurrency = page.getByRole('spinbutton', { name: '分类并发上限' });
  for (const value of ['33', '', '1.5']) {
    await concurrency.fill(value);
    await enabled.uncheck();
    await page.locator('#settingsSaveBtn').click();
    await expect(page.locator('#settingsStatus')).toContainText('请先启用自动分类');
    await expect(enabled).toBeFocused();
    await enabled.check();
  }
  await concurrency.fill('12');
  await enabled.uncheck();
  await save(page);
  await expect(concurrency).toBeDisabled();
});

for (const width of [390, 1440]) for (const theme of ['light', 'dark', 'eink']) {
  test(`classification settings at ${width}px in ${theme}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/');
    await chooseTheme(page, theme);
    await openSettings(page);
    await expect(page.locator('.settings-category-card:visible')).toHaveCount(8);
    await section(page, 'classification');
    await expect(page.getByRole('switch', { name: '启用自动分类' })).toBeVisible();
    expect(await page.locator('#classificationSettings').evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
    await page.locator('#classificationApiSettingsBtn').scrollIntoViewIfNeeded();
    await expect(page.locator('#classificationApiSettingsBtn')).toBeInViewport();
    await page.screenshot({ path: `.ui-artifacts/classification-settings-${width}-${theme}.png` });
    await page.getByRole('switch', { name: '仅对未读新闻分类' }).check();
    await page.getByRole('switch', { name: '启用自动分类' }).uncheck();
    await expect(page.getByRole('switch', { name: '仅对未读新闻分类' })).toBeDisabled();
    await expect(page.getByRole('spinbutton', { name: '分类并发上限' })).toBeDisabled();
    await page.screenshot({ path: `.ui-artifacts/classification-disabled-${width}-${theme}.png` });
  });
}
