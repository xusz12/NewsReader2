import { test, expect } from '@playwright/test';
import { answerConfirm, chooseTheme, openSettings } from './helpers.js';

async function setup(page, request, configured = true) {
  const saved = await (await request.get('/api/settings')).json();
  saved.api_status.deepseek = { configured, masked_key: 'deep••••1234' };
  saved.api_status.jev = { configured, masked_key: 'jev-••••5678' };
  saved.llm.translation.model = 'deepseek-saved';
  saved.api_models.jev.model = 'jev-saved';
  await page.route('**/api/settings', route => route.request().method() === 'GET' ? route.fulfill({ json: saved }) : route.continue());
  await page.goto('/');
  await openSettings(page);
  await page.locator('[data-settings-tab="deepseek"]').click();
  return saved;
}

async function chooseProvider(page, name) {
  const provider = page.getByRole('combobox', { name: 'API 服务', exact: true });
  // Finish scrolling from the connection test before opening the menu;
  // scrolling outside a dropdown deliberately dismisses it.
  await provider.scrollIntoViewIfNeeded();
  await provider.click();
  await page.getByRole('option', { name, exact: true }).click();
}

test('Jev classification concurrency defaults to eight, validates limits and saves across providers', async ({ page, request }) => {
  await setup(page, request, false);
  await chooseProvider(page, 'Jev');
  const concurrency = page.getByRole('spinbutton', { name: '分类并发上限' });
  await expect(concurrency).toHaveValue('8');
  await concurrency.fill('33');
  await page.locator('#settingsSaveBtn').click();
  await expect(page.locator('#settingsStatus')).toHaveText('分类并发上限必须是 1–32 的整数');
  await concurrency.fill('12');
  await chooseProvider(page, 'DeepSeek');
  await expect(concurrency).toHaveCount(0);
  const saving = page.waitForRequest(r => r.url().endsWith('/api/settings') && r.method() === 'PUT');
  await page.locator('#settingsSaveBtn').click();
  expect((await saving).postDataJSON().api_models.jev.concurrency).toBe(12);
  await expect(page.locator('#settingsStatus')).toHaveText('设置已保存');
  expect((await (await request.get('/api/news/classification')).json()).throttle.configured_concurrency).toBe(12);
  await chooseProvider(page, 'Jev');
  await expect(concurrency).toHaveValue('12');
  // Restore the shared fixture's default for subsequent API management tests.
  await concurrency.fill('8');
  await page.locator('#settingsSaveBtn').click();
  await expect(page.locator('#settingsStatus')).toHaveText('设置已保存');
});

test('unread-only switch preserves drafts, saves live, reloads and can be disabled', async ({ page, request }) => {
  await setup(page, request, false);
  await chooseProvider(page, 'Jev');
  const toggle = page.getByRole('switch', { name: '仅对未读新闻分类' });
  await expect(toggle).not.toBeChecked();
  await toggle.focus();
  await toggle.press('Space');
  await expect(toggle).toBeChecked();
  await expect(page.locator('#settingsStatus')).toHaveText('有未保存的更改');
  await chooseProvider(page, 'DeepSeek');
  await expect(toggle).toHaveCount(0);
  await chooseProvider(page, 'Jev');
  await expect(toggle).toBeChecked();
  await page.locator('#settingsResetBtn').click();
  await expect(toggle).not.toBeChecked();
  await toggle.check();
  await chooseProvider(page, 'DeepSeek');
  await page.locator('#settingsSaveBtn').click();
  await expect(page.locator('#settingsStatus')).toHaveText('设置已保存');
  expect((await (await request.get('/api/news/classification')).json()).unread_only).toBe(true);
  await page.unroute('**/api/settings');
  await page.reload();
  await openSettings(page);
  await page.locator('[data-settings-tab="deepseek"]').click();
  await chooseProvider(page, 'Jev');
  await expect(toggle).toBeChecked();
  await toggle.uncheck();
  await page.locator('#settingsSaveBtn').click();
  await expect(page.locator('#settingsStatus')).toHaveText('设置已保存');
  expect((await (await request.get('/api/news/classification')).json()).unread_only).toBe(false);
});

test('provider models and secret drafts remain independent across navigation and save', async ({ page, request }) => {
  await setup(page, request, false);
  await expect(page.locator('#settingsDetailTitle')).toHaveText('API 管理');
  await page.locator('input[name="llm.translation.model"]').fill('deepseek-draft');
  await page.locator('#deepseekSecret').fill('deepseek-secret-draft');
  await chooseProvider(page, 'Jev');
  await expect(page.locator('#jevSecret')).toHaveValue('');
  await expect(page.locator('#translationTestBtn')).toBeDisabled();
  await page.locator('input[name="api_models.jev.model"]').fill('jev-custom');
  await page.locator('#jevSecret').fill('jev-secret-draft');
  await page.locator('#closeSettingsBtn').click();
  await openSettings(page);
  await expect(page.locator('#jevSecret')).toHaveValue('jev-secret-draft');
  await chooseProvider(page, 'DeepSeek');
  await expect(page.locator('#deepseekSecret')).toHaveValue('deepseek-secret-draft');
  await expect(page.locator('input[name="llm.translation.model"]')).toHaveValue('deepseek-draft');
  const saving = page.waitForRequest(r => r.url().endsWith('/api/settings') && r.method() === 'PUT');
  await page.locator('#settingsSaveBtn').click();
  const body = (await saving).postDataJSON();
  expect(body.api_models.jev.model).toBe('jev-custom');
  expect(body.llm.translation.model).toBe('deepseek-draft');
  expect(JSON.stringify(body)).not.toContain('secret-draft');
  await expect(page.locator('#settingsStatus')).toHaveText('设置已保存');
  expect((await (await request.get('/api/settings')).json()).api_models.jev.model).toBe('jev-custom');
});

test('Jev key save and delete target only Jev and preserve unsaved models', async ({ page, request }) => {
  const saved = await setup(page, request, false);
  const calls = [];
  await page.route('**/api/settings/secrets/*', async route => {
    calls.push({ url: route.request().url(), method: route.request().method(), body: route.request().postDataJSON() });
    await route.fulfill({ json: { ...saved, api_status: { ...saved.api_status, jev: { configured: route.request().method() !== 'DELETE', masked_key: 'new-••••5678' } } } });
  });
  await page.locator('input[name="llm.translation.model"]').fill('deepseek-keep');
  await chooseProvider(page, 'Jev');
  await page.locator('input[name="api_models.jev.model"]').fill('jev-keep');
  await page.locator('#jevSecret').fill('isolated-test-secret');
  await page.locator('#saveSecretBtn').click();
  await expect(page.locator('.masked-secret')).toHaveText('new-••••5678');
  await expect(page.locator('input[name="api_models.jev.model"]')).toHaveValue('jev-keep');
  await expect(page.locator('#translationTestBtn')).toBeEnabled();
  await page.locator('#deleteSecretBtn').click();
  await expect(page.getByRole('dialog')).toContainText('删除 Jev 密钥');
  await answerConfirm(page, false);
  expect(calls).toHaveLength(1);
  await page.locator('#deleteSecretBtn').click();
  await answerConfirm(page);
  await expect(page.locator('#jevSecret')).toBeVisible();
  await expect(page.locator('#translationTestBtn')).toBeDisabled();
  expect(calls.map(c => c.method)).toEqual(['PUT', 'DELETE']);
  expect(calls.every(c => c.url.endsWith('/secrets/jev'))).toBe(true);
  await chooseProvider(page, 'DeepSeek');
  await expect(page.locator('input[name="llm.translation.model"]')).toHaveValue('deepseek-keep');
});

test('a late secret save does not clear the other provider draft', async ({ page, request }) => {
  const saved = await setup(page, request, false);
  let pending;
  await page.route('**/api/settings/secrets/jev', route => { pending = route; });
  await chooseProvider(page, 'Jev');
  await page.locator('#jevSecret').fill('jev-key-to-save');
  await page.locator('#saveSecretBtn').click();
  await expect.poll(() => !!pending).toBe(true);
  await chooseProvider(page, 'DeepSeek');
  await page.locator('#deepseekSecret').fill('deepseek-keep-draft');
  await pending.fulfill({ json: { ...saved, api_status: { ...saved.api_status, jev: { configured: true, masked_key: 'jev-••••saved' } } } });
  await expect(page.locator('#toast')).toContainText('Jev 密钥已保存');
  await expect(page.locator('#deepseekSecret')).toHaveValue('deepseek-keep-draft');
  await chooseProvider(page, 'Jev');
  await expect(page.locator('.masked-secret')).toHaveText('jev-••••saved');
  await expect(page.locator('#jevSecret')).toHaveCount(0);
});

test('late model test stays with its provider, and model changes invalidate its answer', async ({ page, request }) => {
  await setup(page, request);
  let pending;
  await page.route('**/api/settings/apis/jev/test', route => { pending = route; });
  await chooseProvider(page, 'Jev');
  await page.locator('#translationTestBtn').click();
  await expect.poll(() => !!pending).toBe(true);
  expect(pending.request().postDataJSON()).toEqual({ model: 'jev-saved' });
  await chooseProvider(page, 'DeepSeek');
  await pending.fulfill({ json: { ok: true, message: 'Jev 已连接', model: 'jev-saved', elapsed_ms: 12 } });
  await expect(page.locator('#translationTestTerminal')).not.toContainText('Jev 已连接');
  await chooseProvider(page, 'Jev');
  await expect(page.locator('#translationTestTerminal')).toContainText('Jev 已连接');
  pending = null;
  await page.locator('#translationTestBtn').click();
  await expect.poll(() => !!pending).toBe(true);
  await page.locator('input[name="api_models.jev.model"]').fill('jev-next');
  await pending.fulfill({ json: { ok: true, message: 'OLD RESULT', model: 'jev-saved', elapsed_ms: 12 } });
  await expect(page.locator('#translationTestTerminal')).toContainText('重新测试');
  await expect(page.locator('#translationTestTerminal')).not.toContainText('OLD RESULT');
  await expect(page.locator('#translationTestBtn')).toBeEnabled();
});

test('reset ignores outstanding tests and model lookup failures retain a usable catalog', async ({ page, request }) => {
  await setup(page, request);
  await chooseProvider(page, 'Jev');
  let failed = false;
  await page.route('**/api/settings/apis/jev/models', route => route.fulfill(failed
    ? { status: 502, json: { ok: false, error: 'models_unavailable' } }
    : { json: { ok: true, catalog: { options: [{ value: 'jev-found', source: 'official' }] } } }));
  await page.locator('#translationModelsBtn').click();
  await expect(page.locator('#translationStatus')).toContainText('已获取 1 个模型');
  failed = true;
  await page.locator('#translationModelsBtn').click();
  await expect(page.locator('#translationStatus')).toContainText('获取模型列表失败');
  await expect(page.locator('select[name="api_models.jev.model"] option[value="jev-found"]')).toHaveCount(1);
  let pending;
  await page.route('**/api/settings/apis/jev/test', route => { pending = route; });
  await page.locator('input[name="api_models.jev.model"]').fill('jev-unsaved');
  await page.locator('#translationTestBtn').click();
  await expect.poll(() => !!pending).toBe(true);
  await page.locator('#settingsResetBtn').click();
  await pending.fulfill({ json: { ok: true, message: 'STALE SUCCESS', model: 'jev-unsaved', elapsed_ms: 12 } });
  await expect(page.locator('input[name="api_models.jev.model"]')).toHaveValue('jev-saved');
  await expect(page.locator('#translationTestTerminal')).not.toContainText('STALE SUCCESS');
});

for (const width of [1440, 390]) for (const theme of ['light', 'dark', 'eink']) {
  test(`API management at ${width}px in ${theme}`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 900 });
    await setup(page, request);
    await chooseTheme(page, theme);
    const provider = page.getByRole('combobox', { name: 'API 服务', exact: true });
    await provider.focus();
    await provider.press('ArrowDown');
    await provider.press('End');
    await provider.press('Enter');
    await expect(provider).toContainText('Jev');
    await expect(provider).toBeFocused();
    await expect(page.locator('#translationSettings')).toContainText('TypeSafe');
    const toggle = page.getByRole('switch', { name: '仅对未读新闻分类' });
    await toggle.check();
    await expect(toggle).toBeChecked();
    await toggle.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `.ui-artifacts/classification-unread-${width}-${theme}.png` });
    await page.locator('#translationTestTerminal').scrollIntoViewIfNeeded();
    await expect(page.locator('#translationTestBtn')).toBeVisible();
    const panel = await page.locator('#detailPanelSettings').boundingBox();
    expect(panel.x).toBeGreaterThanOrEqual(0);
    expect(panel.x + panel.width).toBeLessThanOrEqual(width + 1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.screenshot({ path: `.ui-artifacts/api-management-${width}-${theme}.png` });
  });
}
