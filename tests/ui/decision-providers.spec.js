import { openSettingsItem, expandProvider, openSettings, chooseTheme } from './helpers.js';
import { test, expect } from '@playwright/test';

const card = (page, provider) => page.locator(`[data-api-provider="${provider}"]`);

async function setup(page, request) {
  const saved = await (await request.get('/api/settings')).json();
  saved.classification = {provider:'jev', fallback_enabled:false, enabled:true, unread_only:false, concurrency:8};
  saved.api_models.jev.model = 'jev-latest';
  saved.api_models['openai-decisions'].model = '';
  for (const provider of ['jev', 'openai-decisions']) saved.api_status[provider] = {configured:false};
  const writes = [];
  await page.route('**/api/settings', async route => {
    if (route.request().method() === 'PUT') {
      const body = route.request().postDataJSON();
      writes.push(body);
      Object.assign(saved, body);
    }
    await route.fulfill({json:saved});
  });
  await page.goto('/');
  await openSettings(page);
  await openSettingsItem(page, 'classification');
  return {saved, writes};
}

test('判断服务切换保存与撤销，密钥草稿独立且关闭分类不禁用服务选择', async ({page, request}) => {
  const {writes} = await setup(page, request);
  const jev = page.getByRole('radio', {name:'Jev', exact:true});
  const decisions = page.getByRole('radio', {name:'OpenAI Decisions', exact:true});
  await expect(jev).toBeChecked();
  const fallback = page.getByRole('switch', {name:'自动使用备用判断服务'});
  await expect(fallback).not.toBeChecked();
  await page.getByRole('switch', {name:'启用自动分类'}).uncheck();
  await expect(decisions).toBeEnabled();
  await fallback.check();
  await decisions.check();
  await page.locator('#classificationApiSettingsBtn').click();
  await expect(page.locator('#apiTitle-openai-decisions')).toBeFocused();
  await expandProvider(page,'jev');
  await page.locator('#jevSecret').fill('typesafe-draft');
  await page.locator('#openai-decisionsSecret').fill('openai-draft');
  await card(page, 'jev').locator('input[name]').fill('jev-pinned');
  await card(page, 'openai-decisions').locator('input[name]').fill('gpt-6-luna');
  await openSettingsItem(page, 'classification');
  await page.locator('#settingsSaveBtn').click();
  await expect(page.locator('#settingsStatus')).toHaveText('设置已保存');
  expect(writes[0]).toEqual({classification:{provider:'openai-decisions',fallback_enabled:true,enabled:false,unread_only:false,concurrency:8}});
  await page.locator('#classificationApiSettingsBtn').click();
  await expect(page.locator('#jevSecret')).toHaveValue('typesafe-draft');
  await expect(page.locator('#openai-decisionsSecret')).toHaveValue('openai-draft');
  await page.locator('#settingsSaveBtn').click();
  await expect(page.locator('#settingsStatus')).toHaveText('设置已保存');
  expect(writes[1].api_models.jev.model).toBe('jev-pinned');
  expect(writes[1].api_models['openai-decisions'].model).toBe('gpt-6-luna');
  expect(Object.keys(writes[1]).sort()).toEqual(['api_models']);
  expect(JSON.stringify(writes)).not.toContain('openai-draft');
  expect(JSON.stringify(writes)).not.toContain('typesafe-draft');
  await openSettingsItem(page, 'classification');
  await jev.check();
  await fallback.uncheck();
  await page.locator('#settingsResetBtn').click();
  await expect(fallback).toBeChecked();
  await expect(decisions).toBeChecked();
});

test('Decisions 密钥和连接测试只访问本服务，错误显示所选服务', async ({page, request}) => {
  const {saved} = await setup(page, request);
  const calls = [];
  await page.route('**/api/settings/secrets/openai-decisions', async route => {
    calls.push(route.request().postDataJSON());
    saved.api_status['openai-decisions'] = {configured:true, masked_key:'sk-t••••1234'};
    await route.fulfill({json:saved});
  });
  await page.route('**/api/settings/apis/openai-decisions/test', async route => {
    calls.push(route.request().postDataJSON());
    await route.fulfill({json:{ok:true, model:'gpt-6-luna', message:'模型连接成功', elapsed_ms:12}});
  });
  await openSettingsItem(page, 'deepseek');
  await expandProvider(page,'openai-decisions');
  await page.locator('#openai-decisionsSecret').fill('test-openai-key');
  await card(page, 'openai-decisions').locator('[data-api-action="saveSecretBtn"]').click();
  await expect(card(page, 'openai-decisions').locator('.masked-secret')).toHaveText('sk-t••••1234');
  await card(page, 'openai-decisions').locator('[data-api-action="translationTestBtn"]').click();
  await expect(card(page, 'openai-decisions').locator('.terminal-box')).toContainText('gpt-6-luna');
  await expect(card(page, 'jev').locator('.terminal-box')).not.toContainText('[PASS]');
  expect(calls).toEqual([{key:'test-openai-key'}, {model:''}]);
  await page.route('**/api/news/classification', route => route.fulfill({json:{
    ok:true, provider:'openai-decisions', provider_label:'OpenAI Decisions', enabled:true, configured:false,
    total:2, done:0, pending:2, running:0, failed:0, coverage:{total:2, done:0},
  }}));
  await openSettingsItem(page, 'classification');
  await page.locator('#classificationRefreshBtn').click();
  await expect(page.locator('#classificationSettingsStatus')).toContainText('配置 OpenAI Decisions');
});

test('备用接手、等待和恢复时更新运行状态并保留设置草稿', async ({page, request}) => {
  const progress = {enabled:false, fallback_enabled:true, using_backup:true, provider_label:'OpenAI Decisions',
    preferred_label:'Jev', total:0, pending:0, running:0, done:0, failed:0, coverage:{done:0,total:0}};
  await page.route('**/api/news/classification', route => route.fulfill({json:progress}));
  await setup(page, request);
  const status = page.locator('#decisionRouteStatus');
  await expect(status).toContainText('备用服务 OpenAI Decisions');
  const draft = page.getByRole('radio', {name:'OpenAI Decisions',exact:true});
  await draft.check();
  Object.assign(progress, {using_backup:false, waiting_for_service:true});
  await page.locator('#classificationRefreshBtn').click();
  await expect(status).toContainText('两家判断服务暂不可用');
  Object.assign(progress, {waiting_for_service:false, provider_label:'Jev'});
  await page.locator('#classificationRefreshBtn').click();
  await expect(status).toContainText('首选服务 Jev');
  await expect(draft).toBeChecked();
});

for (const width of [1440,390]) for (const theme of ['light','dark','system-light','system-dark','eink']) {
  test(`判断服务复用共享控件 ${width} ${theme}`, async ({page, request}) => {
    await page.setViewportSize({width,height:900});
    if (theme.startsWith('system')) await page.emulateMedia({colorScheme:theme.split('-')[1]});
    await setup(page, request);
    await chooseTheme(page, theme.startsWith('system') ? 'system' : theme);
    const decisions = page.getByRole('radio', {name:'OpenAI Decisions',exact:true});
    await decisions.focus();
    await decisions.press('Space');
    await expect(decisions).toBeChecked();
    const fallback = page.getByRole('switch', {name:'自动使用备用判断服务'});
    await fallback.focus();
    await fallback.press('Space');
    await expect(fallback).toBeChecked();
    await page.locator('#classificationSettings').scrollIntoViewIfNeeded();
    await page.screenshot({path:`.ui-artifacts/decision-routing-${width}-${theme}.png`});
    await page.locator('#classificationApiSettingsBtn').click();
    const input = card(page,'openai-decisions').locator('input[name]');
    await input.scrollIntoViewIfNeeded();
    await expect(input).toBeInViewport();
    const font = await input.evaluate(node=>getComputedStyle(node).fontSize);
    for (const size of ['small','standard','large','extra-large']) {
      await page.evaluate(size=>document.documentElement.dataset.fontSize=size,size);
      await expect(input).toHaveCSS('font-size',font);
    }
    const styles = await page.locator('[data-api-provider="jev"] input[name], [data-api-provider="openai-decisions"] input[name]').evaluateAll(nodes=>nodes.map(node=>{
      const s=getComputedStyle(node); return [s.fontSize,s.padding,s.borderRadius,s.color];
    }));
    expect(styles[0]).toEqual(styles[1]);
    await page.screenshot({path:`.ui-artifacts/decisions-api-${width}-${theme}.png`});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}
