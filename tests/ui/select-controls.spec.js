import { openSettingsItem, expandProvider, choosePicker, chooseTheme, openSettings } from './helpers.js';
import { test, expect } from '@playwright/test';

async function settings(page, request, { model = 'catalog-a', many = false } = {}) {
  const saved = await (await request.get('/api/settings')).json();
  saved.agent.runtime = 'codex';
  saved.agent.codex = { ...saved.agent.codex, provider: '', profile: '', model, reasoning_effort: 'high' };
  saved.model_catalogs.codex = { options: many
    ? Array.from({ length: 40 }, (_, i) => ({ value: `model-${String(i).padStart(2, '0')}`, label: `model-${String(i).padStart(2, '0')}`, reasoning_efforts: ['low', 'high'] }))
    : [{ value: 'catalog-a', label: '目录模型 A', reasoning_efforts: ['low', 'high'] }, { value: 'catalog-b', label: '目录模型 B', reasoning_efforts: ['medium'] }] };
  let submitted;
  await page.route('**/api/settings', async route => {
    if (route.request().method() === 'PUT') {
      submitted = route.request().postDataJSON();
      Object.assign(saved, submitted);
    }
    await route.fulfill({ json: saved });
  });
  await page.goto('/');
  await expect(page.locator('#feedCardList .news-card').first()).toBeVisible();
  await openSettings(page);
  await openSettingsItem(page, 'models');
  await expect(page.locator('#modelSettings')).toBeVisible();
  return { saved, submitted: () => submitted };
}
const modelBox = page => page.locator('[data-model-feature="chat"]').getByRole('combobox', { name: '模型', exact: true });
const customModel = page => page.locator('input[name="agent.codex.model"]');

test.describe('touch selection', () => {
  test.use({ hasTouch: true, viewport: { width: 390, height: 844 } });

  test('touch opens the custom menu and commits a catalog model', async ({ page, request }) => {
    await settings(page, request, {model:'retired-model'});
    await expect(page.locator('#testConnectionBtn')).toBeDisabled();
    await page.locator('#setting-agent-codex-model-label').tap();
    await expect(modelBox(page)).toHaveAttribute('aria-expanded', 'false');
    await modelBox(page).tap();
    await expect(page.getByRole('listbox')).toBeVisible();
    await page.getByRole('option', { name: '目录模型 B', exact: true }).tap();
    await expect(modelBox(page)).toHaveText('目录模型 B');
    await expect(customModel(page)).toHaveCount(0);
    await expect(page.locator('#testConnectionBtn')).toBeEnabled();
    await expect(page.getByRole('listbox')).toBeHidden();
  });
});

test('custom selects open only inside the visible field', async ({ page, request }) => {
  await settings(page, request);
  async function checkField(name, scope = page.locator('[data-model-feature="chat"]')) {
    const field = scope.getByRole('combobox', { name, exact: true });
    const label = page.locator(`#${await field.getAttribute('aria-labelledby')}`);
    await label.click();
    await expect(field).toHaveAttribute('aria-expanded', 'false');
    const labelBox = await label.boundingBox();
    await page.mouse.click(labelBox.x + labelBox.width - 2, labelBox.y + labelBox.height / 2);
    await expect(field).toHaveAttribute('aria-expanded', 'false');
    await field.scrollIntoViewIfNeeded();
    const fieldBox = await field.boundingBox();
    await page.mouse.click(fieldBox.x + fieldBox.width / 2, fieldBox.y - 3);
    await expect(field).toHaveAttribute('aria-expanded', 'false');
    for (const target of [field.locator('.select-value'), field.locator('.select-chevron')]) {
      await target.click();
      await expect(field).toHaveAttribute('aria-expanded', 'true');
      await page.keyboard.press('Escape');
    }
  }
  await checkField('模型');
  await checkField('推理强度');
  await checkField('临时会话保留时间');
  await openSettingsItem(page, 'deepseek');
  for (const provider of ['deepseek', 'opencode-go']) {
    await expandProvider(page, provider);
    await checkField('翻译模型', page.locator(`[data-api-provider="${provider}"]`));
  }
});

test('Codex only offers catalog models and preserves selection through save and reopen', async ({ page, request }) => {
  const state = await settings(page, request);
  await expect(customModel(page)).toHaveCount(0);
  await expect(modelBox(page)).toHaveText('目录模型 A');
  await expect(page.locator('#modelAdvanced, input[name="agent.codex.provider"], input[name="agent.codex.profile"]')).toHaveCount(0);
  await modelBox(page).click();
  await page.getByRole('option', { name: '目录模型 B', exact: true }).click();
  await expect(customModel(page)).toHaveCount(0);
  await expect(modelBox(page)).toBeFocused();
  await expect(modelBox(page)).toHaveText('目录模型 B');
  await expect(page.locator('select[name="agent.codex.reasoning_effort"] option')).toHaveCount(2);
  await page.locator('#settingsSaveBtn').click();
  expect(state.submitted().agent.codex.model).toBe('catalog-b');
  await page.reload();
  await page.locator('#leftNavSettingsBtn').click();
  await openSettingsItem(page, 'models');
  await expect(modelBox(page)).toHaveText('目录模型 B');
  await expect(customModel(page)).toHaveCount(0);
});

test('an old unknown model requires a catalog refresh or a new selection', async ({ page, request }) => {
  await settings(page, request, { model: 'private-model' });
  await expect(customModel(page)).toHaveCount(0);
  await expect(page.locator('#modelsNotice')).toContainText('当前不在模型列表');
  await expect(page.locator('#testConnectionBtn')).toBeDisabled();
  await expect(page.locator('select[name="agent.codex.model"] option[value="private-model"]')).toHaveCount(0);
  await page.route('**/api/settings/models', route => route.fulfill({ json: { catalog: { options: [{ value: 'private-model', reasoning_efforts: ['high'] }] } } }));
  await page.locator('#refreshModelsBtn').click();
  await expect(page.locator('#testConnectionBtn')).toBeEnabled();
  await expect(modelBox(page)).toHaveText('private-model');
  await modelBox(page).click();
  await page.getByRole('option', { name: '沿用本机默认', exact: true }).click();
  await expect(customModel(page)).toHaveCount(0);
  await expect(page.locator('select[name="agent.codex.model"]')).toHaveValue('');
});

test('catalogs persist through real settings save, page reload, runtime changes and reset', async ({ page, request }) => {
  const saved = await (await request.get('/api/settings')).json();
  await request.put('/api/settings', { data: {
    llm: saved.llm,
    agent: { ...saved.agent, runtime: 'codex', codex: { model: 'ui-catalog-a', provider: '', profile: '', sandbox: 'read-only' } },
  } });
  const refreshes = [];
  page.on('request', req => { if (req.url().endsWith('/api/settings/models')) refreshes.push(req.postDataJSON().runtime); });
  await page.goto('/');
  await page.locator('#leftNavSettingsBtn').click();
  await openSettingsItem(page, 'models');
  await page.locator('#refreshModelsBtn').click();
  await expect(customModel(page)).toHaveCount(0);
  await modelBox(page).click();
  await page.getByRole('option', { name: 'ui-catalog-b', exact: true }).click();
  await page.locator('[data-model-feature="chat"]').getByRole('combobox', { name: '推理强度', exact: true }).click();
  await page.getByRole('option', { name: 'medium', exact: true }).click();
  await page.locator('#settingsSaveBtn').click();
  await expect(page.locator('#settingsStatus')).toHaveText('设置已保存');
  await page.reload();
  await page.locator('#leftNavSettingsBtn').click();
  await openSettingsItem(page, 'models');
  await expect(modelBox(page)).toHaveText('ui-catalog-b');
  await expect(customModel(page)).toHaveCount(0);
  await expect(page.locator('[data-model-feature="chat"]').getByRole('combobox', { name: '推理强度', exact: true })).toHaveText('medium');
  await choosePicker(page,'Runtime','Pi','chat');
  await choosePicker(page,'Runtime','Codex','chat');
  await expect(page.locator('#settingsResetBtn')).toBeHidden();
  await choosePicker(page,'Runtime','Pi','chat');
  await page.locator('#settingsResetBtn').click();
  await modelBox(page).click();
  await expect(page.getByRole('option', { name: 'ui-catalog-a', exact: true })).toBeVisible();
  await page.getByRole('option', { name: 'ui-catalog-a', exact: true }).click();
  await expect(customModel(page)).toHaveCount(0);
  expect(refreshes.filter(runtime => runtime === 'codex')).toHaveLength(1);
  expect(refreshes.filter(runtime => runtime === 'pi').length).toBeGreaterThanOrEqual(2);
  await page.route('**/api/settings/models', route => route.fulfill({ json: { catalog: { error: '刷新暂不可用', options: [] } } }));
  await page.locator('#refreshModelsBtn').click();
  await expect(page.locator('#modelsNotice')).toHaveText('刷新暂不可用');
  await modelBox(page).click();
  await expect(page.getByRole('option', { name: 'ui-catalog-b', exact: true })).toBeVisible();
});

test('all Codex feature settings omit retired provider, profile and custom model inputs', async ({ page, request }) => {
  await settings(page, request);
  await expect(page.locator('input[name$=".codex.provider"], input[name$=".codex.profile"], input[name$=".codex.model"]')).toHaveCount(0);
  await expect(page.locator('select[name="agent.codex.model"] option[value="catalog-b"]')).toHaveCount(1);
});

test('a retired Codex thinking level requires selection before connection testing', async ({ page, request }) => {
  await settings(page, request);
  await page.route('**/api/settings/models', route => route.fulfill({json:{catalog:{options:[
    {value:'catalog-a',label:'目录模型 A',reasoning_efforts:['low']},
  ]}}}));
  await page.locator('#refreshModelsBtn').click();
  await expect(page.locator('#modelsNotice')).toContainText('不支持保存的推理强度 high');
  await expect(page.locator('#testConnectionBtn')).toBeDisabled();
  await choosePicker(page,'推理强度','low','chat');
  await expect(page.locator('#testConnectionBtn')).toBeEnabled();
});

test('custom menus support keyboard selection, cancel, typeahead and normal Tab order', async ({ page, request }) => {
  await settings(page, request);
  await modelBox(page).focus();
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('End');
  await page.keyboard.press('Escape');
  await expect(modelBox(page)).toHaveAttribute('aria-expanded', 'false');
  await expect(modelBox(page)).toHaveText('目录模型 A');
  await expect(page.locator('#modelSettings')).toBeVisible();
  await expect(page.locator('select[name="agent.codex.reasoning_effort"]')).toHaveValue('high');
  await page.keyboard.press('Enter');
  await page.keyboard.press('End');
  await page.keyboard.press('Enter');
  await expect(modelBox(page)).toHaveText('目录模型 B');
  await page.keyboard.press('Tab');
  const effort = page.locator('[data-model-feature="chat"]').getByRole('combobox', { name: '推理强度', exact: true });
  await expect(effort).toBeFocused();
  await page.keyboard.press('m');
  await expect(effort).toHaveAttribute('aria-expanded', 'true');
  await page.keyboard.press('Enter');
  await expect(effort).toHaveText('medium');
  await effort.click();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('listbox')).toBeHidden();
  await expect(page.locator('#refreshModelsBtn')).toBeFocused();
});

for (const [width, height, theme] of [[1440, 900, 'light'], [390, 844, 'dark']]) {
  test(`menu fits ${width}px viewport, scrolls independently and follows ${theme} theme`, async ({ page, request }) => {
    await page.setViewportSize({ width, height });
    await settings(page, request, { model: 'model-05', many: true });
    await chooseTheme(page, theme);
    await modelBox(page).click();
    const menu = page.getByRole('listbox');
    await expect(menu).toBeVisible();
    const box = await menu.boundingBox();
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(width);
    expect(box.y).toBeGreaterThanOrEqual(0);
    expect(box.y + box.height).toBeLessThanOrEqual(height);
    const focused = await modelBox(page).getAttribute('aria-activedescendant');
    expect(focused).toBeTruthy();
    await page.screenshot({ path: `.ui-artifacts/custom-select-${theme}-${width}.png`, animations: 'disabled' });
    await page.keyboard.press('End');
    await expect(page.locator('.select-option.focused')).toHaveText('model-39');
    await expect(menu).toBeVisible();
    const optionBox = await page.locator('.select-option.focused').boundingBox();
    expect(optionBox.y + optionBox.height).toBeLessThanOrEqual(box.y + box.height);
    await page.keyboard.press('Enter');
    await expect(modelBox(page)).toHaveText('model-39');
    await expect(customModel(page)).toHaveCount(0);
    await modelBox(page).click();
    await page.locator('#settingsDetailTitle').click();
    await expect(menu).toBeHidden();
  });
}
