import {test,expect} from '@playwright/test';
import {choosePicker,chooseTheme,openSettings,openModelAdvanced} from './helpers.js';

async function openModels(page) {
  await page.goto('/'); await openSettings(page);
  await page.locator('[data-settings-tab="models"]').click();
}

async function saveModels(request,data) {
  const result=await request.put('/api/settings',{data:{llm:data.llm,agent:data.agent,feature_models:data.feature_models}});
  expect(result.ok(),await result.text()).toBeTruthy();
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
    await choosePicker(page,'Runtime','Pi SDK / OAR');
    await choosePicker(page,'服务商','Test provider');
    await choosePicker(page,'模型','Test model');
    await page.getByRole('combobox',{name:'推理强度',exact:true}).click();
    await expect(menu).toBeVisible();
    const piBox=await menu.boundingBox();expect(piBox.x).toBeGreaterThanOrEqual(0);expect(piBox.x+piBox.width).toBeLessThanOrEqual(width);
    await page.keyboard.press('Escape');
    await expect(page.getByRole('combobox',{name:'推理强度',exact:true})).toBeFocused();
    expect(await page.locator('#modelSettings').evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
    await page.screenshot({path:`output/feature-picker-check/pi-${width}-${theme}-${scheme}.png`});
    await choosePicker(page,'Runtime','Codex CLI');
  }
});

test('Pi refresh removes logged-out providers and accepts an empty catalog without losing saved choices',async({page,request})=>{
  const original=await (await request.get('/api/settings')).json();
  await saveModels(request,{...original,agent:{...original.agent,runtime:'pi'},llm:{...original.llm,pi_chat:{provider:'test',model:'test-model'}}});
  let stage=0, calls=0;
  const option={value:'test-model',label:'Test model',reasoning_efforts:['off','high']};
  await page.route('**/api/settings/models',route=>{
    calls++;
    const groups=stage===0?{test:[option],other:[{value:'other-model'}]}:stage===1?{other:[{value:'other-model'}]}:{};
    return route.fulfill({json:{ok:true,catalog:{error:'',provider_options:Object.keys(groups).map(value=>({value,label:value})),
      model_options_by_provider:groups,options:[]}}});
  });
  try {
    await openModels(page);
    await expect.poll(()=>calls).toBeGreaterThan(0);
    await expect(page.locator('select[name="llm.pi_chat.provider"] option')).toHaveCount(3);
    stage=1;
    await page.locator('#refreshModelsBtn').click();
    await expect(page.getByRole('combobox',{name:'服务商',exact:true})).toHaveText('请选择服务商');
    await expect(page.locator('select[name="llm.pi_chat.provider"] option[value="test"]')).toHaveCount(0);
    await expect(page.locator('select[name="llm.pi_chat.model"] option[value="test-model"]')).toHaveCount(0);
    await expect(page.locator('#modelsNotice')).toContainText('当前不在可用目录');
    await expect(page.locator('#testConnectionBtn')).toBeDisabled();
    stage=2;
    await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
    await expect(page.locator('select[name="llm.pi_chat.provider"] option')).toHaveCount(1);
    await expect(page.locator('#settingsSaveBtn')).toBeHidden();
    // Reopening the category refreshes again, even though settings are cached.
    const before=calls;
    await page.locator('[data-settings-tab="system"]').click();
    stage=0;
    await page.locator('[data-settings-tab="models"]').click();
    await expect.poll(()=>calls).toBeGreaterThan(before);
    await expect(page.getByRole('combobox',{name:'服务商',exact:true})).toHaveText('test');
    await expect(page.getByRole('combobox',{name:'模型',exact:true})).toHaveText('Test model');
    await expect(page.locator('#testConnectionBtn')).toBeEnabled();
  } finally {await saveModels(request,original);}
});

test('Pi thinking levels follow the model, persist per feature, and reach the current connection test',async({page,request})=>{
  const original=await (await request.get('/api/settings')).json();
  const pi={provider:'test',model:'test-model'};
  await saveModels(request,{...original,agent:{...original.agent,runtime:'pi'},llm:{...original.llm,pi_chat:pi},
    feature_models:Object.fromEntries(['topics','summaries','reports'].map(feature=>[feature,{...original.feature_models[feature],runtime:'pi',pi}]))});
  let tested;
  const piCatalog={error:'',provider_options:[{value:'test',label:'Test provider'}],options:[],
    model_options_by_provider:{test:[{value:'test-model',label:'Test model',reasoning_efforts:['off','low','high']},
      {value:'plain-model',label:'Plain model',reasoning_efforts:['off']}]}};
  await page.route('**/api/settings/models',route=>route.fulfill({json:{ok:true,catalog:piCatalog}}));
  await page.route('**/api/settings/connection-test',route=>{
    tested=route.request().postDataJSON();
    return route.fulfill({json:{status:'success',message:'本次模型请求成功。',elapsed_ms:1}});
  });
  try {
    await openModels(page);
    await expect(page.locator('select[name="llm.pi_chat.model"] option[value="plain-model"]')).toHaveCount(1);
    await expect(page.getByRole('combobox',{name:'推理强度',exact:true})).toBeEnabled();
    await choosePicker(page,'推理强度','high');
    await choosePicker(page,'模型','Plain model');
    await expect(page.getByRole('combobox',{name:'推理强度',exact:true})).toHaveText('沿用默认');
    await expect(page.locator('select[name="llm.pi_chat.reasoning_effort"] option')).toHaveText(['沿用默认','off']);
    await choosePicker(page,'模型','Test model');
    await choosePicker(page,'推理强度','high');
    await choosePicker(page,'功能','主题追踪');
    await choosePicker(page,'推理强度','off');
    await page.locator('#testConnectionBtn').click();
    await expect(page.locator('#connTestTerminal')).toContainText('[SUCCESS] test · test-model · off');
    expect(tested.pi).toEqual({...pi,reasoning_effort:'off'});
    await choosePicker(page,'功能','日报');
    await choosePicker(page,'推理强度','low');
    await page.locator('#settingsSaveBtn').click();
    await expect(page.locator('#settingsStatus')).toHaveText('设置已保存');
    const saved=await (await request.get('/api/settings')).json();
    expect(saved.llm.pi_chat.reasoning_effort).toBe('high');
    expect(saved.feature_models.topics.pi.reasoning_effort).toBe('off');
    expect(saved.feature_models.reports.pi.reasoning_effort).toBe('low');
    await choosePicker(page,'功能','Chat');
    await expect(page.getByRole('combobox',{name:'推理强度',exact:true})).toHaveText('high');
    await choosePicker(page,'服务商','请选择服务商');
    await expect(page.getByRole('combobox',{name:'推理强度',exact:true})).toHaveText('沿用默认');
    await expect(page.getByRole('combobox',{name:'推理强度',exact:true})).toBeDisabled();
  } finally {await saveModels(request,original);}
});
