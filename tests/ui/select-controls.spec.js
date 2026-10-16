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
  await page.locator(page.viewportSize().width < 768 ? '#mobNavSettings' : '#leftNavSettingsBtn').click();
  await page.locator('[data-settings-tab="models"]').click();
  await expect(page.locator('#modelSettings')).toBeVisible();
  return { saved, submitted: () => submitted };
}
const modelBox = page => page.getByRole('combobox', { name: '模型选择 (Model)', exact: true });
const customModel = page => page.locator('input[name="agent.codex.model"]');

test.describe('touch selection', () => {
  test.use({ hasTouch: true, viewport: { width: 390, height: 844 } });

  test('touch opens the custom menu and commits a catalog model', async ({ page, request }) => {
    await settings(page, request);
    await page.locator('#setting-agent-codex-model-label').tap();
    await expect(modelBox(page)).toHaveAttribute('aria-expanded', 'false');
    await modelBox(page).tap();
    await expect(page.getByRole('listbox')).toBeVisible();
    await page.getByRole('option', { name: '目录模型 B', exact: true }).tap();
    await expect(modelBox(page)).toHaveText('目录模型 B');
    await expect(customModel(page)).toHaveValue('');
    await expect(page.getByRole('listbox')).toBeHidden();
  });
});

test('custom selects open only inside the visible field', async ({ page, request }) => {
  await settings(page, request);
  async function checkField(name) {
    const field = page.getByRole('combobox', { name, exact: true });
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
  await checkField('模型选择 (Model)');
  await checkField('推理强度 (Reasoning Effort)');
  await checkField('临时会话保留时间');
  await page.locator('[data-settings-tab="deepseek"]').click();
  await checkField('翻译模型');
});

test('Codex catalog and custom models stay synchronized through typing, save and reopen', async ({ page, request }) => {
  const state = await settings(page, request);
  await expect(customModel(page)).toHaveValue('');
  await expect(modelBox(page)).toHaveText('目录模型 A');
  await customModel(page).pressSequentially('private-model');
  await expect(customModel(page)).toBeFocused();
  await expect(modelBox(page)).toHaveText('private-model');
  await expect(page.locator('select[name="agent.codex.reasoning_effort"]')).toHaveValue('');
  await expect(page.locator('select[name="agent.codex.model"] [data-custom-option]')).toHaveCount(1);
  await page.locator('#settingsSaveBtn').click();
  expect(state.submitted().agent.codex.model).toBe('private-model');
  await page.locator('#closeSettingsBtn').click();
  await page.locator('#leftNavSettingsBtn').click();
  await expect(customModel(page)).toHaveValue('private-model');
  await expect(modelBox(page)).toHaveText('private-model');
  await modelBox(page).click();
  await page.getByRole('option', { name: '目录模型 B', exact: true }).click();
  await expect(customModel(page)).toHaveValue('');
  await expect(modelBox(page)).toBeFocused();
  await expect(modelBox(page)).toHaveText('目录模型 B');
  await expect(page.locator('select[name="agent.codex.reasoning_effort"] option')).toHaveCount(2);
  await page.locator('#settingsSaveBtn').click();
  expect(state.submitted().agent.codex.model).toBe('catalog-b');
  await page.reload();
  await page.locator('#leftNavSettingsBtn').click();
  await expect(customModel(page)).toHaveValue('');
});

test('refresh recognizes a custom model without replacing the selected value', async ({ page, request }) => {
  await settings(page, request, { model: 'private-model' });
  await expect(customModel(page)).toHaveValue('private-model');
  await page.route('**/api/settings/models', route => route.fulfill({ json: { catalog: { options: [{ value: 'private-model', reasoning_efforts: ['high'] }] } } }));
  await page.locator('#refreshModelsBtn').click();
  await expect(customModel(page)).toHaveValue('');
  await expect(modelBox(page)).toHaveText('private-model');
  await modelBox(page).click();
  await page.getByRole('option', { name: '沿用本机默认', exact: true }).click();
  await expect(customModel(page)).toHaveValue('');
  await expect(page.locator('select[name="agent.codex.model"]')).toHaveValue('');
});

test('catalogs persist through real settings save, page reload, runtime changes and reset', async ({ page, request }) => {
  const saved = await (await request.get('/api/settings')).json();
  await request.put('/api/settings', { data: {
    llm: saved.llm,
    agent: { ...saved.agent, runtime: 'codex', codex: { model: 'ui-catalog-a', provider: '', profile: '', sandbox: 'read-only' } },
  } });
  let refreshes = 0;
  page.on('request', req => { if (req.url().endsWith('/api/settings/models')) refreshes++; });
  await page.goto('/');
  await page.locator('#leftNavSettingsBtn').click();
  await page.locator('#refreshModelsBtn').click();
  await expect(customModel(page)).toHaveValue('');
  await modelBox(page).click();
  await page.getByRole('option', { name: 'ui-catalog-b', exact: true }).click();
  await page.getByRole('combobox', { name: '推理强度 (Reasoning Effort)', exact: true }).click();
  await page.getByRole('option', { name: 'medium', exact: true }).click();
  await page.locator('#settingsSaveBtn').click();
  await expect(page.locator('#settingsStatus')).toHaveText('设置已保存');
  await page.reload();
  await page.locator('#leftNavSettingsBtn').click();
  await expect(modelBox(page)).toHaveText('ui-catalog-b');
  await expect(customModel(page)).toHaveValue('');
  await expect(page.getByRole('combobox', { name: '推理强度 (Reasoning Effort)', exact: true })).toHaveText('medium');
  await page.locator('[data-runtime="pi"]').click();
  await page.locator('[data-runtime="codex"]').click();
  await page.locator('#settingsResetBtn').click();
  await modelBox(page).click();
  await expect(page.getByRole('option', { name: 'ui-catalog-a', exact: true })).toBeVisible();
  await page.getByRole('option', { name: 'ui-catalog-a', exact: true }).click();
  await expect(customModel(page)).toHaveValue('');
  expect(refreshes).toBe(1);
  await page.route('**/api/settings/models', route => route.fulfill({ json: { catalog: { error: '刷新暂不可用', options: [] } } }));
  await page.locator('#refreshModelsBtn').click();
  await expect(page.locator('#modelsNotice')).toHaveText('刷新暂不可用');
  await modelBox(page).click();
  await expect(page.getByRole('option', { name: 'ui-catalog-b', exact: true })).toBeVisible();
});

test('provider and profile changes keep model catalogs separate and restore known choices', async ({ page, request }) => {
  await settings(page, request);
  for (const name of ['agent.codex.provider', 'agent.codex.profile']) {
    const input = page.locator(`input[name="${name}"]`);
    await input.fill('other');
    await expect(input).toBeFocused();
    await expect(customModel(page)).toHaveValue('catalog-a');
    await expect(page.locator('select[name="agent.codex.model"] option[value="catalog-b"]')).toHaveCount(0);
    await input.fill('');
    await expect(customModel(page)).toHaveValue('');
    await expect(page.locator('select[name="agent.codex.model"] option[value="catalog-b"]')).toHaveCount(1);
  }
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
  const effort = page.getByRole('combobox', { name: '推理强度 (Reasoning Effort)', exact: true });
  await expect(effort).toBeFocused();
  await page.keyboard.press('m');
  await expect(effort).toHaveAttribute('aria-expanded', 'true');
  await page.keyboard.press('Enter');
  await expect(effort).toHaveText('medium');
  await effort.click();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('listbox')).toBeHidden();
  await expect(customModel(page)).toBeFocused();
});

for (const [width, height, theme] of [[1440, 900, 'light'], [390, 844, 'dark']]) {
  test(`menu fits ${width}px viewport, scrolls independently and follows ${theme} theme`, async ({ page, request }) => {
    await page.setViewportSize({ width, height });
    await settings(page, request, { model: 'model-05', many: true });
    await page.locator('#themeSelect').selectOption(theme);
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
    await expect(customModel(page)).toHaveValue('');
    await modelBox(page).click();
    await page.locator('#settingsDetailTitle').click();
    await expect(menu).toBeHidden();
  });
}
