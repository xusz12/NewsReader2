import { test, expect } from '@playwright/test';
import { openSettings, openSettingsItem, expandProvider } from './helpers.js';

for (const width of [1440,390]) {
  test('all setting editors have a single task location and concrete settings can be found at ' + width, async ({page}) => {
    await page.setViewportSize({width,height:844});
    await page.goto('/');
    await openSettings(page);
    const visible = page.locator('#settingsContent > [data-workspace]:visible');
    await expect(visible).toHaveAttribute('data-workspace','collection');
    await expect(page.locator('#settingsCollectBtn')).toBeInViewport();
    for (const selector of ['#collectionSchedule','#xAccountSettings','#translationRouting','#classificationSettings','#classificationSettingsProgress'])
      await expect(visible.locator(selector)).toBeVisible();
    await openSettingsItem(page,'catchup');
    await expect(visible.locator('#summaryPreferences [data-model-feature="summaries"]')).toHaveCount(1);
    await expect(visible.locator('#scheduleSettings')).toBeVisible();
    await expect(visible.locator('#reportModelSettings [data-model-feature]')).toHaveCount(2);
    await openSettingsItem(page,'deepseek');
    await expect(visible.locator('details[data-api-provider]')).toHaveCount(4);
    await expect(visible.locator('details[data-api-provider][open]')).toHaveCount(0);
    for (const [query, target, group] of [
      ['午报模型','reportDailyModel','research'], ['Jev 密钥','apiTitle-jev','services'],
      ['X 登录','checkCollectionXBtn','services'], ['清理快照','reportSnapshotSettings','application'],
    ]) {
      if (width <= 920) await page.locator('#settingsCategoryBackBtn').click();
      const search = page.getByRole('searchbox',{name:'搜索全部设置'});
      await search.fill(query);
      await page.locator(`#settingsSearchResults [data-target="${target}"]`).click();
      await expect(visible).toHaveAttribute('data-workspace',group);
      await expect(page.locator('#'+target)).toBeFocused();
      await expect(page.locator('#'+target)).toBeInViewport();
      if (target === 'apiTitle-jev') await expect(visible.locator('details[open]')).toHaveAttribute('data-api-provider','jev');
    }
    expect(await page.locator('[data-api-secret]').evaluateAll(inputs=>inputs.every(input=>input.closest('[data-workspace]').dataset.workspace==='services'))).toBe(true);
    for (const selector of ['#clearSessionsBtn','#clearReportSnapshotsBtn','#settingsSourceCleanup'])
      await expect(visible.locator(selector)).toHaveCount(1);
    expect(await page.locator('#settingsContent [id]').evaluateAll(nodes => {
      const ids=nodes.map(node=>node.id);return ids.length-new Set(ids).size;
    })).toBe(0);
    await openSettingsItem(page,'models');
    await page.keyboard.press('Escape');
    await openSettings(page);
    await expect(page.locator('#modelSettings')).toBeInViewport();
  });
}

test('one save combines collection tasks and preserves drafts belonging to research and credentials', async ({page,request}) => {
  const baseline = await (await request.get('/api/settings')).json();
  const writes=[];
  await page.route('**/api/settings', async route=>{
    if (route.request().method()==='PUT') writes.push(route.request().postDataJSON());
    await route.continue();
  });
  try {
    await page.goto('/');await openSettings(page);
    const enabled = page.locator('[name="collection.enabled"]'), route = page.locator('[name="llm.translation.fallback_enabled"]');
    const manual = page.locator('[name="collection.translation_concurrency"]');
    const next = baseline.collection.translation_concurrency===3 ? 5 : 3;
    await enabled.click(); await manual.fill(String(next)); await route.click();
    await openSettingsItem(page,'catchup');
    const days=page.locator('[name="news_summary.max_days"]');await days.fill('0');
    await openSettingsItem(page,'deepseek');
    await expandProvider(page,'deepseek');
    const custom=page.locator('input[name="llm.translation.model"]'); await custom.fill('draft-model');
    await openSettingsItem(page,'pipeline');
    await page.locator('#settingsSaveBtn').click();
    await expect(page.locator('#settingsStatus')).toHaveText('设置已保存');
    expect(writes).toHaveLength(1);
    expect(writes[0]).toEqual({collection:{...baseline.collection, enabled:!baseline.collection.enabled, translation_concurrency:next},llm:{translation:{fallback_enabled:!baseline.llm.translation.fallback_enabled}}});
    await openSettingsItem(page,'deepseek');
    await expect(custom).toHaveValue('draft-model');
    await page.locator('#settingsResetBtn').click();
    await openSettingsItem(page,'catchup');
    await expect(days).toHaveValue('0');
    await expect(page.locator('#settingsSaveBtn')).toBeEnabled();
  } finally {
    await request.put('/api/settings',{data:{collection:baseline.collection,llm:baseline.llm}});
  }
});

test('research retries only the assistant profile after the summary configuration was saved', async ({page,request}) => {
  const baseline = await (await request.get('/api/settings')).json();
  const profile = (await (await request.get('/api/personal-assistant/profile')).json()).profile;
  let profiles = 0, settings = 0;
  await page.route('**/api/settings', async route => {
    if (route.request().method() === 'PUT') settings++;
    await route.continue();
  });
  await page.route('**/api/personal-assistant/profile', async route => {
    if (route.request().method() !== 'PUT') return route.fulfill({json:{profile}});
    if (++profiles === 1) return route.fulfill({status:503,json:{error:'资料保存失败'}});
    await route.fulfill({json:{profile:{...profile,...route.request().postDataJSON()}}});
  });
  try {
    await page.goto('/');await openSettings(page);
    await openSettingsItem(page,'catchup');
    const days = baseline.news_summary.max_days === 5 ? 6 : 5;
    await page.locator('[name="news_summary.max_days"]').fill(String(days));
    await openSettingsItem(page,'personal-assistant');
    await page.locator('[data-personal-field="name"]').fill('研读测试助手');
    await page.locator('#settingsSaveBtn').click();
    await expect(page.locator('#settingsStatus')).toContainText('其他设置已保存，个人助手保存失败');
    expect((await (await request.get('/api/settings')).json()).news_summary.max_days).toBe(days);
    await expect(page.locator('[data-personal-field="name"]')).toHaveValue('研读测试助手');
    await page.locator('#settingsSaveBtn').click();
    await expect(page.locator('#settingsStatus')).toHaveText('设置已保存');
    expect([settings,profiles]).toEqual([1,2]);
  } finally {
    await request.put('/api/settings',{data:{news_summary:baseline.news_summary}});
  }
});

test('reading preferences share values with the toolbar and save locally with their page', async ({page}) => {
  await page.goto('/');await openSettings(page);await openSettingsItem(page,'appearance');
  const writes = [];
  page.on('request', request => {if (request.method()==='PUT' && request.url().endsWith('/api/settings')) writes.push(request.url());});
  const panel = page.locator('#readingAppearance');
  await panel.getByRole('combobox',{name:'阅读主题'}).click();
  await page.getByRole('option',{name:'墨水屏 (E-Ink)'}).click();
  await panel.getByRole('combobox',{name:'正文字号'}).click();
  await page.getByRole('option',{name:'大（115%）',exact:true}).click();
  await expect(page.locator('html')).not.toHaveAttribute('data-theme','eink');
  await page.locator('#settingsSaveBtn').click();
  await expect(page.locator('html')).toHaveAttribute('data-theme','eink');
  await expect(page.locator('#fontSizeSelect-native')).toHaveValue('large');
  await page.locator('#themeSelect').click();
  await page.getByRole('option',{name:'跟随系统'}).click();
  await expect(panel.getByRole('combobox',{name:'阅读主题'})).toHaveText('跟随系统');
  await expect(page.locator('#settingsSaveBtn')).toBeHidden();
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-font-size','large');
  expect(writes).toEqual([]);
});
