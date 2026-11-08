import { test, expect } from '@playwright/test';
import { answerConfirm, chooseTheme, openSettings, openModelAdvanced } from './helpers.js';

const card = (page, provider) => page.locator(`[data-api-provider="${provider}"]`);
const action = (page, provider, name) => card(page, provider).locator(`[data-api-action="${name}"]`);
const terminal = (page, provider) => card(page, provider).locator('.terminal-box');

async function showProvider(page, provider) {
  const section = provider === 'jev' ? 'classification' : 'deepseek';
  if (await page.locator(`[data-settings-tab="${section}"]`).getAttribute('aria-current') === 'true') return;
  const back = page.locator('#settingsCategoryBackBtn');
  if (page.viewportSize().width <= 920 && await back.isVisible()) await back.click();
  await page.locator(`[data-settings-tab="${section}"]`).click();
}

async function setup(page, request, configured = true) {
  const saved = await (await request.get('/api/settings')).json();
  for (const provider of ['deepseek', 'jev', 'opencode-go']) saved.api_status[provider] = { configured, masked_key: `${provider}••••1234` };
  saved.llm.translation = {provider:'deepseek', model:'deepseek-saved', fallback_enabled:true, pi_fallback_enabled:true};
  saved.api_models = { jev: { model: 'jev-saved' }, 'opencode-go': { model: 'opencode-go-saved' } };
  saved.agent.runtime = 'codex';
  saved.agent.codex.model = 'chat-model';
  saved.agent.codex.reasoning_effort = '';
  saved.feature_models.reports = { runtime: 'pi', pi: { provider: 'opencode-go', model: 'report-model' }, codex: {} };
  await page.route('**/api/settings', route => route.request().method() === 'GET' ? route.fulfill({ json: saved }) : route.continue());
  await page.goto('/');
  await openSettings(page);
  await page.locator('[data-settings-tab="deepseek"]').click();
  return saved;
}

test('all provider sections stay expanded, preserve independent drafts, and save only models', async ({ page, request }) => {
  await setup(page, request, false);
  await expect(page.locator('#settingsDetailTitle')).toHaveText('API 管理');
  await expect(page.getByRole('combobox', { name: 'API 服务', exact: true })).toHaveCount(0);
  await expect(page.locator('#translationSettings [data-api-provider]')).toHaveCount(2);
  await expect(page.locator('#classificationApiSettings[data-api-provider="jev"]')).toHaveCount(1);
  await expect(card(page, 'jev')).toBeHidden();
  await expect(page.locator('#translationSettings [name^="classification."]')).toHaveCount(0);
  for (const provider of ['deepseek', 'jev', 'opencode-go']) {
    await showProvider(page, provider);
    await expect(card(page, provider)).toBeVisible();
    await card(page, provider).locator('input[name]').fill(`${provider}-draft`);
    await page.locator(`#${provider}Secret`).fill(`${provider}-secret-draft`);
    await expect(action(page, provider, 'translationTestBtn')).toBeDisabled();
  }
  await page.locator('#closeSettingsBtn').click();
  await openSettings(page);
  for (const provider of ['deepseek', 'jev', 'opencode-go']) {
    await expect(page.locator(`#${provider}Secret`)).toHaveValue(`${provider}-secret-draft`);
    await expect(card(page, provider).locator('input[name]')).toHaveValue(`${provider}-draft`);
  }
  const saving = page.waitForRequest(r => r.url().endsWith('/api/settings') && r.method() === 'PUT');
  await page.locator('#settingsSaveBtn').click();
  const body = (await saving).postDataJSON();
  expect(body.api_models).toEqual({ jev: { model: 'jev-draft' }, 'opencode-go': { model: 'opencode-go-draft' } });
  expect(body.llm.translation.model).toBe('deepseek-draft');
  expect(JSON.stringify(body)).not.toContain('secret-draft');
  await expect(page.locator('#settingsStatus')).toHaveText('设置已保存');
  expect((await (await request.get('/api/settings')).json()).api_models['opencode-go'].model).toBe('opencode-go-draft');
});

for (const provider of ['jev', 'opencode-go']) test(`${provider} key save and delete preserve other providers and model drafts`, async ({ page, request }) => {
  const saved = await setup(page, request, false);
  const calls = [];
  await page.route('**/api/settings/secrets/*', async route => {
    calls.push({ url: route.request().url(), method: route.request().method() });
    await route.fulfill({ json: { ...saved, api_status: { ...saved.api_status, [provider]: { configured: route.request().method() !== 'DELETE', masked_key: 'new-••••5678' } } } });
  });
  await card(page, 'deepseek').locator('input[name]').fill('deepseek-keep');
  await showProvider(page, provider);
  await card(page, provider).locator('input[name]').fill('model-keep');
  await page.locator(`#${provider}Secret`).fill('isolated-test-secret');
  await action(page, provider, 'saveSecretBtn').click();
  await expect(card(page, provider).locator('.masked-secret')).toHaveText('new-••••5678');
  await expect(card(page, provider).locator('input[name]')).toHaveValue('model-keep');
  await expect(action(page, provider, 'translationTestBtn')).toBeEnabled();
  await action(page, provider, 'deleteSecretBtn').click();
  await expect(page.getByRole('dialog')).toContainText(`删除 ${provider === 'jev' ? 'Jev' : 'OpenCode Go 订阅'} 密钥`);
  await answerConfirm(page, false);
  expect(calls).toHaveLength(1);
  await action(page, provider, 'deleteSecretBtn').click();
  await answerConfirm(page);
  await expect(page.locator(`#${provider}Secret`)).toBeVisible();
  await expect(action(page, provider, 'translationTestBtn')).toBeDisabled();
  expect(calls.map(c => c.method)).toEqual(['PUT', 'DELETE']);
  expect(calls.every(c => c.url.endsWith(`/secrets/${provider}`))).toBe(true);
  await expect(card(page, 'deepseek').locator('input[name]')).toHaveValue('deepseek-keep');
});

test('late secret save preserves another provider input, focus and open model menu', async ({ page, request }) => {
  const saved = await setup(page, request, false);
  let pending;
  await page.route('**/api/settings/secrets/opencode-go', route => { pending = route; });
  await page.locator('#opencode-goSecret').fill('opencode-go-key-to-save');
  await action(page, 'opencode-go', 'saveSecretBtn').click();
  await expect.poll(() => !!pending).toBe(true);
  await page.locator('#deepseekSecret').fill('deepseek-keep-draft');
  await pending.fulfill({ json: { ...saved, api_status: { ...saved.api_status, 'opencode-go': { configured: true, masked_key: 'open••••saved' } } } });
  await expect(page.locator('#toast')).toContainText('OpenCode Go 订阅 密钥已保存');
  await expect(page.locator('#deepseekSecret')).toHaveValue('deepseek-keep-draft');
  await expect(page.locator('#deepseekSecret')).toBeFocused();
  await expect(page.locator('#opencode-goSecret')).toHaveCount(0);
  let modelRequest;
  await page.route('**/api/settings/apis/opencode-go/models', route => { modelRequest = route; });
  await action(page, 'opencode-go', 'translationModelsBtn').click();
  await expect.poll(() => !!modelRequest).toBe(true);
  const picker = card(page, 'deepseek').getByRole('combobox');
  await picker.scrollIntoViewIfNeeded();
  await picker.click();
  await modelRequest.fulfill({ json: { ok: true, catalog: { options: [{ value: 'new-model', source: 'official' }] } } });
  await expect(card(page, 'opencode-go')).toContainText('已获取 1 个模型');
  await expect(picker).toHaveAttribute('aria-expanded', 'true');
  await page.keyboard.press('Escape');
  await expect(picker).toBeFocused();
});

test('concurrent provider tests stay isolated and model edits invalidate late answers', async ({ page, request }) => {
  await setup(page, request);
  const pending = {};
  await page.route('**/api/settings/apis/*/test', route => { pending[route.request().url().split('/').at(-2)] = route; });
  for (const provider of ['jev', 'opencode-go']) {
    await showProvider(page, provider);
    await action(page, provider, 'translationTestBtn').click();
    await expect.poll(() => !!pending[provider]).toBe(true);
    expect(pending[provider].request().postDataJSON()).toEqual({ model: `${provider}-saved` });
  }
  await card(page, 'opencode-go').locator('input[name]').fill('opencode-go-next');
  await pending.jev.fulfill({ json: { ok: true, message: 'Jev 已连接', model: 'jev-saved', elapsed_ms: 12 } });
  await pending['opencode-go'].fulfill({ json: { ok: true, message: 'OLD RESULT', model: 'opencode-go-saved', elapsed_ms: 12 } });
  await expect(terminal(page, 'jev')).toContainText('Jev 已连接');
  await expect(terminal(page, 'deepseek')).not.toContainText('Jev 已连接');
  await expect(terminal(page, 'opencode-go')).toContainText('重新测试');
  await expect(terminal(page, 'opencode-go')).not.toContainText('OLD RESULT');
  await expect(action(page, 'opencode-go', 'translationTestBtn')).toBeEnabled();
  await card(page, 'opencode-go').locator('input[name]').fill('');
  await expect(action(page, 'opencode-go', 'translationTestBtn')).toBeDisabled();
});

test('reset ignores outstanding tests and failed model lookups retain the catalog', async ({ page, request }) => {
  await setup(page, request);
  let failed = false;
  await page.route('**/api/settings/apis/opencode-go/models', route => route.fulfill(failed
    ? { status: 502, json: { ok: false, error: 'models_unavailable' } }
    : { json: { ok: true, catalog: { options: [{ value: 'model-found', source: 'official' }] } } }));
  await action(page, 'opencode-go', 'translationModelsBtn').click();
  await expect(card(page, 'opencode-go')).toContainText('已获取 1 个模型');
  failed = true;
  await action(page, 'opencode-go', 'translationModelsBtn').click();
  await expect(card(page, 'opencode-go')).toContainText('获取模型列表失败');
  await expect(page.locator('select[name="api_models.opencode-go.model"] option[value="model-found"]')).toHaveCount(1);
  let pending;
  await page.route('**/api/settings/apis/opencode-go/test', route => { pending = route; });
  await card(page, 'opencode-go').locator('input[name]').fill('unsaved-model');
  await action(page, 'opencode-go', 'translationTestBtn').click();
  await expect.poll(() => !!pending).toBe(true);
  await page.locator('#settingsResetBtn').click();
  await pending.fulfill({ json: { ok: true, message: 'STALE SUCCESS', model: 'unsaved-model', elapsed_ms: 12 } });
  await expect(card(page, 'opencode-go').locator('input[name]')).toHaveValue('opencode-go-saved');
  await expect(terminal(page, 'opencode-go')).not.toContainText('STALE SUCCESS');
});

for (const width of [1440, 390]) test(`catalog error details preserve drafts and allow retry at ${width}px`, async ({ page, request }) => {
  await page.setViewportSize({ width, height: 900 });
  await setup(page, request);
  let failure = null, calls = 0;
  await page.route('**/api/settings/apis/opencode-go/models', route => {
    calls++;
    return route.fulfill(failure || { json: { ok: true, catalog: { options: [{ value: 'model-found', source: 'official' }] } } });
  });
  await action(page, 'opencode-go', 'translationModelsBtn').click();
  const status = card(page, 'opencode-go').locator('[id$="translationStatus"]');
  await expect(status).toHaveText('已获取 1 个模型');
  await card(page, 'opencode-go').locator('input[name]').fill('unsaved-model');
  const errors = [
    [504, 'connection_timeout', '模型目录请求超时（25 秒，含连接、HTTPS 握手或响应读取），请检查网络后重试。'],
    [502, 'authentication_failed', '获取模型失败（HTTP 401）。密钥鉴权失败，请检查密钥是否有效。'],
    [502, 'access_denied', '获取模型失败（HTTP 403）。服务端拒绝访问，请检查所选模型、账户权限及客户端限制。'],
    [502, 'response_parse_failed', '模型目录响应解析失败：返回内容不是有效的 JSON 或模型目录格式，请稍后重试。'],
  ];
  for (const [httpStatus, reason, message] of errors) {
    failure = { status: httpStatus, json: { ok: false, error: 'models_unavailable', reason, message } };
    await action(page, 'opencode-go', 'translationModelsBtn').click();
    await expect(status).toHaveText(message);
    await expect(action(page, 'opencode-go', 'translationModelsBtn')).toBeEnabled();
    await expect(card(page, 'opencode-go').locator('input[name]')).toHaveValue('unsaved-model');
    await expect(page.locator('select[name="api_models.opencode-go.model"] option[value="model-found"]')).toHaveCount(1);
  }
  failure = null;
  await action(page, 'opencode-go', 'translationModelsBtn').click();
  await expect(status).toHaveText('已获取 1 个模型');
  await expect(card(page, 'opencode-go').locator('input[name]')).toHaveValue('unsaved-model');
  expect(calls).toBe(6);
});

test('OpenCode Go 订阅 filtered catalog and 429 reason preserve model drafts', async ({page, request}) => {
  await setup(page, request);
  await page.route('**/api/settings/apis/opencode-go/models', route => route.fulfill({json:{ok:true,catalog:{options:[
    {value:'paid-model',label:'paid-model',source:'official'},
  ]}}}));
  await action(page,'opencode-go','translationModelsBtn').click();
  const picker = card(page,'opencode-go').getByRole('combobox');
  await picker.click();
  await expect(page.getByRole('option',{name:/big-pickle|free/})).toHaveCount(0);
  await page.getByRole('option',{name:'paid-model',exact:true}).click();
  let calls = 0;
  await page.route('**/api/settings/apis/opencode-go/test', route => {
    calls++;
    expect(route.request().postDataJSON()).toEqual({model:'paid-model'});
    return route.fulfill({status:502,json:{ok:false,error:'api_test_failed',reason:'rate_limit',
      message:'模型测试失败（HTTP 429）。服务端请求频率限制已触发。服务端建议等待 2 分钟后再试。'}});
  });
  await action(page,'opencode-go','translationTestBtn').click();
  await expect(terminal(page,'opencode-go')).toContainText('请求频率限制已触发');
  await expect(terminal(page,'opencode-go')).toContainText('等待 2 分钟');
  await expect(picker).toContainText('paid-model');
  await expect(card(page,'deepseek').locator('input[name]')).toHaveValue('deepseek-saved');
  await expect(action(page,'opencode-go','translationTestBtn')).toBeEnabled();
  expect(calls).toBe(1);
});

test('usage describes saved feature selections and fallback limits', async ({ page, request }) => {
  await setup(page, request);
  await expect(card(page, 'deepseek')).toContainText('按上方顺序切换 API');
  await expect(card(page, 'jev')).toContainText('不会自动改用其他服务');
  await expect(card(page, 'opencode-go')).toContainText('使用 Go 订阅完成新闻正文翻译与精读');
  await expect(page.locator('[data-api-provider="opencode"]')).toHaveCount(0);
  await expect(page.getByRole('radio',{name:'OpenCode',exact:true})).toHaveCount(0);
  await expect(page.locator('[data-api-usage="chat"]')).toContainText('Codex CLI · chat-model');
  await expect(page.locator('[data-api-usage="reports"]')).toContainText('Pi · opencode-go / report-model');
  await expect(page.locator('#apiUsageTitle').locator('..').locator('..')).toContainText('最多尝试 3 次');
  await page.locator('#apiFeatureSettingsBtn').click();
  await expect(page.locator('#settingsDetailTitle')).toHaveText('模型与 Agent');
  await openModelAdvanced(page);
  await page.locator('input[name="agent.codex.model"]').fill('new-chat-model');
  await page.locator('[data-settings-tab="deepseek"]').click();
  await expect(page.locator('[data-api-usage="chat"]')).toContainText('chat-model');
  await expect(page.locator('[data-api-usage="chat"]')).not.toContainText('new-chat-model');
  await page.locator('#settingsSaveBtn').click();
  await expect(page.locator('[data-api-usage="chat"]')).toContainText('new-chat-model');
});

for (const width of [1440, 390]) for (const theme of ['light', 'dark', 'system-light', 'system-dark', 'eink']) {
  test(`API management at ${width}px in ${theme}`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 900 });
    if (theme.startsWith('system')) await page.emulateMedia({ colorScheme: theme.split('-')[1] });
    await setup(page, request);
    await chooseTheme(page, theme.startsWith('system') ? 'system' : theme);
    const primary = page.getByRole('radio', {name:'OpenCode Go 订阅',exact:true});
    await primary.focus();
    await primary.press('Space');
    await expect(primary).toBeChecked();
    await expect(page.locator('#translationRouteDescription')).toContainText('OpenCode Go 订阅 → DeepSeek → Pi');
    await page.locator('#translationRouting').scrollIntoViewIfNeeded();
    await page.screenshot({path:`.ui-artifacts/translation-routing-${width}-${theme}.png`});
    for (const provider of ['deepseek', 'jev', 'opencode-go']) {
      await showProvider(page, provider);
      await expect(card(page, provider)).toBeVisible();
      await card(page, provider).locator('input[name]').scrollIntoViewIfNeeded();
      await expect(card(page, provider).locator('input[name]')).toBeInViewport();
      if (provider === 'jev') await page.screenshot({path:`.ui-artifacts/classification-api-${width}-${theme}.png`});
    }
    const picker = card(page, 'opencode-go').getByRole('combobox');
    await picker.scrollIntoViewIfNeeded();
    await picker.focus();
    await picker.press('ArrowDown');
    await picker.press('End');
    await picker.press('Enter');
    await expect(picker).toBeFocused();
    const fontSize = await card(page, 'opencode-go').locator('.field-input').evaluate(node => getComputedStyle(node).fontSize);
    await page.evaluate(() => document.documentElement.dataset.fontSize = 'extra-large');
    await expect(card(page, 'opencode-go').locator('.field-input')).toHaveCSS('font-size', fontSize);
    // Same shared form styles as the existing model settings page.
    const styles = await card(page, 'opencode-go').locator('.field-input').evaluate(node => {
      const s = getComputedStyle(node); return {fontSize:s.fontSize, borderRadius:s.borderRadius, padding:s.padding, color:s.color};
    });
    const existing = await page.locator('#modelSettings .field-input').first().evaluate(node => {
      const s = getComputedStyle(node); return {fontSize:s.fontSize, borderRadius:s.borderRadius, padding:s.padding, color:s.color};
    });
    expect(styles).toEqual(existing);
    await page.locator('#apiFeatureSettingsBtn').scrollIntoViewIfNeeded();
    await expect(page.locator('#apiFeatureSettingsBtn')).toBeInViewport();
    const panel = await page.locator('#detailPanelSettings').boundingBox();
    expect(panel.x).toBeGreaterThanOrEqual(0);
    expect(panel.x + panel.width).toBeLessThanOrEqual(width + 1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    const timeoutMessage = '模型目录请求超时（25 秒，含连接、HTTPS 握手或响应读取），请检查网络后重试。';
    await page.route('**/api/settings/apis/opencode-go/models', route => route.fulfill({ status: 504,
      json: { ok: false, error: 'models_unavailable', reason: 'connection_timeout', message: timeoutMessage } }));
    await action(page, 'opencode-go', 'translationModelsBtn').click();
    const notice = card(page, 'opencode-go').locator('[id$="translationStatus"]');
    await expect(notice).toHaveText(timeoutMessage);
    await notice.scrollIntoViewIfNeeded();
    await expect(notice).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.screenshot({ path: `.ui-artifacts/api-catalog-timeout-${width}-${theme}.png` });
    await page.locator('#apiTitle-opencode-go').scrollIntoViewIfNeeded();
    await page.screenshot({ path: `.ui-artifacts/api-management-${width}-${theme}.png` });
  });
}

test('translation preference and fallback switches preserve both models and persist on save', async ({page, request}) => {
  await setup(page, request);
  const primary = page.getByRole('radio', {name:'OpenCode Go 订阅',exact:true});
  await primary.check();
  await expect(page.locator('#translationRouteDescription')).toContainText('OpenCode Go 订阅 → DeepSeek → Pi');
  await expect(card(page,'deepseek').locator('input[name]')).toHaveValue('deepseek-saved');
  await expect(card(page,'opencode-go').locator('input[name]')).toHaveValue('opencode-go-saved');
  await page.getByRole('switch', {name:'API 均失败后尝试 Pi',exact:true}).uncheck();
  await expect(page.locator('#translationRouteDescription')).toHaveText('调用顺序：OpenCode Go 订阅 → DeepSeek。');
  await showProvider(page,'jev');
  await card(page,'jev').locator('input[name]').fill('jev-independent');
  await showProvider(page,'opencode-go');
  await expect(primary).toBeChecked();
  await page.locator('#closeSettingsBtn').click();
  await openSettings(page);
  await expect(primary).toBeChecked();
  const saving = page.waitForRequest(r => r.url().endsWith('/api/settings') && r.method() === 'PUT');
  await page.locator('#settingsSaveBtn').click();
  const data = (await saving).postDataJSON();
  expect(data.llm.translation).toEqual({provider:'opencode-go',model:'deepseek-saved',fallback_enabled:true,pi_fallback_enabled:false});
  expect(data.api_models).toEqual({jev:{model:'jev-independent'},'opencode-go':{model:'opencode-go-saved'}});
  await expect(page.locator('#settingsStatus')).toHaveText('设置已保存');
  await expect(page.locator('#translationRouteDescription')).toContainText('尚未配置密钥或模型');
  await page.getByRole('switch', {name:'失败后切换其他 API',exact:true}).uncheck();
  await expect(page.locator('#translationRouteDescription')).toContainText('调用顺序：OpenCode Go 订阅。');
  await page.locator('#settingsResetBtn').click();
  await expect(page.locator('#translationRouteDescription')).toContainText('调用顺序：OpenCode Go 订阅 → DeepSeek。');
});
