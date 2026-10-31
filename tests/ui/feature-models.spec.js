import {test,expect} from '@playwright/test';
import {choosePicker,chooseTheme,openSettings,openModelAdvanced} from './helpers.js';

async function openModels(page) {
  await page.goto('/'); await openSettings(page);
  await page.locator('[data-settings-tab="models"]').click();
}

test('四个功能独立编辑、保存、重载，连接测试使用当前功能草稿',async({page,request})=>{
  const original=await (await request.get('/api/settings')).json();
  const codex={model:'ui-catalog-a',provider:'',profile:'',sandbox:'read-only'};
  const config={runtime:'codex',codex,pi:{provider:'',model:''}};
  await request.put('/api/settings',{data:{llm:original.llm,agent:{...original.agent,runtime:'codex',codex},
    feature_models:Object.fromEntries(['topics','summaries','reports'].map(key=>[key,config]))}});
  try {
    await openModels(page);
    await expect(page.locator('#connTestTerminal')).toBeHidden();
    await page.locator('#refreshModelsBtn').click();
    await expect(page.locator('#modelsNotice')).toContainText('刷新');
    await choosePicker(page,'模型','ui-catalog-b');
    await choosePicker(page,'功能','主题追踪');
    await expect(page.getByRole('combobox',{name:'模型',exact:true})).toHaveText('ui-catalog-a');
    await choosePicker(page,'Runtime','Pi SDK / OAR');
    await choosePicker(page,'服务商','Test provider');
    await choosePicker(page,'模型','Test model');
    await choosePicker(page,'功能','新闻总结');
    await choosePicker(page,'推理强度','high');
    await choosePicker(page,'功能','日报');
    await openModelAdvanced(page);
    await page.locator('input[name="feature_models.reports.codex.profile"]').fill('report-profile');
    await page.locator('input[name="feature_models.reports.codex.model"]').fill('report-only');
    let tested;
    await page.route('**/api/settings/connection-test',route=>{
      tested=route.request().postDataJSON();
      return route.fulfill({json:{status:'success',message:'本次模型请求成功。',elapsed_ms:1}});
    });
    await page.locator('#testConnectionBtn').click();
    await expect(page.locator('#connTestTerminal')).toContainText('[SUCCESS]');
    expect(tested.codex.model).toBe('report-only');
    expect(tested.codex.profile).toBe('report-profile');
    await choosePicker(page,'功能','Chat');
    await expect(page.locator('#connTestTerminal')).toBeHidden();
    await expect(page.getByRole('combobox',{name:'模型',exact:true})).toHaveText('ui-catalog-b');
    await page.locator('#settingsSaveBtn').click();
    await expect(page.locator('#settingsStatus')).toHaveText('设置已保存');
    const saved=await (await request.get('/api/settings')).json();
    expect(saved.agent.codex.model).toBe('ui-catalog-b');
    expect(saved.feature_models.topics.runtime).toBe('pi');
    expect(saved.feature_models.topics.pi).toEqual({provider:'test',model:'test-model'});
    expect(saved.feature_models.summaries.codex.model).toBe('ui-catalog-a');
    expect(saved.feature_models.summaries.codex.reasoning_effort).toBe('high');
    expect(saved.feature_models.reports.codex.model).toBe('report-only');
    await openModels(page);
    for(const [feature,runtime,model] of [['Chat','Codex CLI','ui-catalog-b'],['主题追踪','Pi SDK / OAR','Test model'],
      ['新闻总结','Codex CLI','ui-catalog-a'],['日报','Codex CLI','report-only']]) {
      await choosePicker(page,'功能',feature);
      await expect(page.getByRole('combobox',{name:'Runtime',exact:true})).toHaveText(runtime);
      await expect(page.getByRole('combobox',{name:'模型',exact:true})).toHaveText(model);
    }
    await expect(page.locator('#settingsSaveBtn')).toBeHidden();
  } finally {await request.put('/api/settings',{data:original}).catch(()=>{});}
});

test('切换功能后迟到的连接结果和目录错误不覆盖当前页面',async({page})=>{
  let releaseTest,releaseCatalog;
  const testGate=new Promise(resolve=>releaseTest=resolve), catalogGate=new Promise(resolve=>releaseCatalog=resolve);
  await page.route('**/api/settings/connection-test',async route=>{await testGate;await route.fulfill({json:{status:'success',message:'旧功能结果'}});});
  await page.route('**/api/settings/models',async route=>{await catalogGate;await route.fulfill({status:503,json:{error:'旧目录失败'}});});
  await openModels(page);
  await page.locator('#testConnectionBtn').click();
  await page.locator('#refreshModelsBtn').click();
  await choosePicker(page,'功能','日报');
  releaseTest();releaseCatalog();
  await expect(page.locator('#testConnectionBtn')).toBeEnabled();
  await expect(page.locator('#connTestTerminal')).toBeHidden();
  await expect(page.locator('#modelSettings')).not.toContainText('旧功能结果');
  await expect(page.locator('#modelSettings')).not.toContainText('旧目录失败');
});

for (const width of [390,1440]) test('功能 Picker 与模型设置适配主题及窄屏 '+width,async({page})=>{
  await page.setViewportSize({width,height:900});await openModels(page);
  await choosePicker(page,'功能','日报');await choosePicker(page,'Runtime','Codex CLI');
  for(const [theme,scheme] of [['light','light'],['dark','dark'],['system','light'],['system','dark'],['eink','light']]) {
    await page.emulateMedia({colorScheme:scheme});await chooseTheme(page,theme);
    await page.getByRole('combobox',{name:'功能',exact:true}).click();
    const menu=page.getByRole('listbox');await expect(menu).toBeVisible();
    const box=await menu.boundingBox();expect(box.x).toBeGreaterThanOrEqual(0);expect(box.x+box.width).toBeLessThanOrEqual(width);
    await page.keyboard.press('Escape');
    expect(await page.locator('#modelSettings').evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
    await expect(page.getByRole('combobox',{name:'功能',exact:true})).toHaveCSS('color',await page.getByRole('combobox',{name:'模型',exact:true}).evaluate(node=>getComputedStyle(node).color));
    await page.screenshot({path:`output/feature-picker-check/settings-${width}-${theme}-${scheme}.png`});
  }
});
