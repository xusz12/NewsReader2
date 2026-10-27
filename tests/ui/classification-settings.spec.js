import { test, expect } from '@playwright/test';
import { openSettings, chooseTheme, withClassificationBatch } from './helpers.js';

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

const progressFixture = () => ({ok:true,total:120,done:72,pending:40,running:6,failed:2,enabled:true,configured:true,unread_only:false,
  coverage:{total:120,done:72},
  throttle:{configured_concurrency:8,effective_concurrency:4,active_requests:6,rate_limited:true,cooldown_seconds:12}});
async function progressSettings(page, initial=progressFixture()) {
  const state={progress:initial,failed:false,requests:0};
  await page.clock.install();
  await page.route('**/api/news/classification',route=>{
    state.requests++;
    return route.fulfill(state.failed ? {status:503,json:{error:'unavailable'}} : {json:withClassificationBatch(state.progress)});
  });
  await page.goto('/');await openSettings(page);await section(page,'classification');
  return state;
}

test('详细进度跟随真实统计且不覆盖未保存设置',async({page})=>{
  const state=await progressSettings(page);
  await expect(page.locator('#classificationSettingsCompletionText')).toHaveText('72 / 120 条');
  await expect(page.locator('#classificationCoveragePercent')).toHaveText('60%');
  await expect(page.locator('[data-classification-count]')).toHaveText(['72 条','40 条','6 条','2 条']);
  await expect(page.locator('#classificationCoverageChart')).toHaveAccessibleName('全部新闻 120 条，已分类 72 条，未分类 48 条');
  await expect(page.locator('#classificationCoverageArc')).toHaveCSS('stroke-dashoffset','40px');
  await expect(page.locator('#classificationSettingsStatus')).toContainText('12 秒后继续');
  await expect(page.locator('#settingsDetailDescription')).toBeHidden();
  await expect(page.locator('#classificationSettingsRuntime')).toHaveCount(0);
  for(const removed of ['管理自动分类开关、分类范围与并发上限。','当前生效范围','Jev 执行中请求','当前并发上限','已设置并发上限'])
    for(const card of ['#classificationSettingsProgress','#classificationSettings'])
      await expect(page.locator(card)).not.toContainText(removed);
  await page.getByRole('switch',{name:'仅对未读新闻分类'}).check();
  const concurrency=page.getByRole('spinbutton',{name:'分类并发上限'});await concurrency.fill('12');
  Object.assign(state.progress,{done:100,pending:12});
  state.progress.coverage.done=100;
  await page.clock.runFor(2100);
  await expect(page.locator('#classificationSettingsCompletionText')).toHaveText('100 / 120 条');
  await expect(page.locator('#classificationCoveragePercent')).toHaveText('83.3%');
  await expect(concurrency).toHaveValue('12');await expect(concurrency).toBeFocused();
  await expect(page.locator('#classificationSettingsQueueLabel')).toHaveText('分类队列');
  await expect(page.locator('#settingsSaveBtn')).toBeVisible();
});

test('已完成、关闭及空范围的分类进度仍可查看',async({page})=>{
  const state=await progressSettings(page,{...progressFixture(),done:120,pending:0,running:0,failed:0,coverage:{total:120,done:120}});
  await expect(page.locator('#classificationSettingsStatus')).toBeHidden();
  await expect(page.locator('#classificationSettingsProgress')).not.toContainText('当前范围已全部分类完成');
  await expect(page.locator('#classificationProgress')).toBeHidden();
  await expect(page.locator('#classificationCoveragePercent')).toContainText('100%');
  Object.assign(state.progress,{enabled:false,done:90,pending:27,running:1,failed:2,coverage:{total:120,done:90}});
  await page.locator('#classificationRefreshBtn').click();
  await expect(page.locator('#classificationSettingsStatus')).toContainText('正在处理的请求会继续完成');
  await expect(page.locator('[data-classification-count="pending"]')).toHaveText('27 条');
  await expect(page.locator('#classificationRetryBtn')).toBeHidden();
  Object.assign(state.progress,{enabled:true,unread_only:true,total:0,done:0,pending:0,running:0,failed:0,coverage:{total:0,done:0}});
  await page.locator('#classificationRefreshBtn').click();
  await expect(page.locator('#classificationSettingsStatus')).toHaveText('当前范围暂无新闻');
  await expect(page.locator('#classificationCoveragePercent')).toHaveText('—');
  await expect(page.locator('#classificationCoverageArc')).toHaveCSS('opacity','0');
  await expect(page.locator('#classificationSettingsQueueLabel')).toHaveText('仅未读新闻队列');
  Object.assign(state.progress,{total:10000,done:9999,pending:1,coverage:{total:10000,done:9999}});
  await page.locator('#classificationRefreshBtn').click();
  await expect(page.locator('#classificationCoveragePercent')).toContainText('99.9%');
});

test('仅未读队列完成后仍显示全库已分类与全部新闻的对比',async({page})=>{
  await progressSettings(page,{...progressFixture(),unread_only:true,total:10,done:10,pending:0,running:0,failed:0});
  await expect(page.locator('#classificationSettingsCompletionText')).toHaveText('72 / 120 条');
  await expect(page.locator('#classificationCoveragePercent')).toHaveText('60%');
  await expect(page.locator('#classificationSettingsQueueLabel')).toHaveText('仅未读新闻队列');
  await expect(page.locator('[data-classification-count="done"]')).toHaveText('10 条');
  await expect(page.locator('#classificationSettingsStatus')).toBeHidden();
});

test('分类进度读取失败保留最后数据，刷新后恢复',async({page})=>{
  const state=await progressSettings(page);
  await expect(page.locator('#classificationSettingsUpdated')).toContainText('更新于');
  state.failed=true;await page.locator('#classificationRefreshBtn').click();
  await expect(page.locator('#classificationSettingsProgressError')).toContainText('显示上次结果');
  await expect(page.locator('#classificationSettingsCompletionText')).toContainText('72 / 120');
  // An earlier successful response may finish as the network starts failing.
  // Once failure is displayed, further failures must not advance its timestamp.
  const stamp=await page.locator('#classificationSettingsUpdated').textContent();
  await page.clock.runFor(2100);
  await expect(page.locator('#classificationSettingsUpdated')).toHaveText(stamp);
  state.failed=false;state.progress.done=80;state.progress.pending=32;
  state.progress.coverage.done=80;
  await page.locator('#classificationRefreshBtn').click();
  await expect(page.locator('#classificationSettingsCompletionText')).toContainText('80 / 120');
  await expect(page.locator('#classificationSettingsProgressError')).toBeHidden();
});

test('首次读取失败不伪造零进度，配置异常可重试失败项',async({page})=>{
  await page.route('**/api/news/classification',route=>route.fulfill({status:503,json:{error:'unavailable'}}));
  await page.goto('/');await openSettings(page);await section(page,'classification');
  await expect(page.locator('#classificationSettingsProgressError')).toContainText('暂时无法读取');
  await expect(page.locator('#classificationSettingsCounts')).toBeHidden();
  await page.unroute('**/api/news/classification');
  const state={progress:{...progressFixture(),configured:false}};
  await page.route('**/api/news/classification',route=>route.fulfill({json:withClassificationBatch(state.progress)}));
  await page.locator('#classificationRefreshBtn').click();
  await expect(page.locator('#classificationSettingsStatus')).toContainText('等待在 API 管理中配置 Jev');
  await expect(page.locator('#classificationRetryBtn')).toBeHidden();
  Object.assign(state.progress,{configured:true,error:'invalid_api_key'});
  await page.locator('#classificationRefreshBtn').click();
  await expect(page.locator('#classificationSettingsStatus')).toContainText('分类已暂停');
  let retries=0;
  await page.route('**/api/news/classification/retry',route=>{
    retries++;Object.assign(state.progress,{error:'',failed:0,pending:42});
    return route.fulfill({status:200,json:{ok:true,retried:2}});
  });
  await page.locator('#classificationRetryBtn').click();
  await expect(page.locator('[data-classification-count="failed"]')).toHaveText('0 条');
  expect(retries).toBe(1);await expect(page.locator('#classificationRetryBtn')).toBeHidden();
});

for(const width of [390,1440]) test('详细分类进度沿用设置样式并适配各主题 '+width,async({page})=>{
  await page.setViewportSize({width,height:900});await progressSettings(page);
  const card=page.locator('#classificationSettingsProgress');
  for(const [theme,scheme] of [['light','light'],['dark','dark'],['eink','light'],['system','light'],['system','dark']]) {
    await page.emulateMedia({colorScheme:scheme});await chooseTheme(page,theme);
    const readStyle=n=>{const c=getComputedStyle(n);return [c.padding,c.borderRadius,c.fontFamily,c.backgroundColor,c.color];};
    expect(await card.evaluate(readStyle)).toEqual(await page.locator('#classificationSettings').evaluate(readStyle));
    expect(await card.evaluate(n=>n.scrollWidth<=n.clientWidth)).toBe(true);
    await card.scrollIntoViewIfNeeded();
    await page.screenshot({path:'.ui-artifacts/classification-details-'+width+'-'+theme+'-'+scheme+'.png'});
  }
});

for (const width of [390, 1050, 1440]) {
  test(`classification owns controls and retains drafts and switches at ${width}px`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.route('**/api/news/classification', async route => {
      const result = await route.fetch();
      await route.fulfill({ json: withClassificationBatch({ ...await result.json(), batch: null, total: 65, done: 64, pending: 1 }) });
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
    await unread.scrollIntoViewIfNeeded();
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
