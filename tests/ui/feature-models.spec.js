import {test,expect} from '@playwright/test';
import {choosePicker,chooseTheme,openSettings,openModelAdvanced,contrast} from './helpers.js';

async function openModels(page) {
  await page.goto('/'); await openSettings(page);
  await page.locator('[data-settings-tab="models"]').click();
}

async function saveModels(request,data) {
  const result=await request.put('/api/settings',{data:{llm:data.llm,agent:data.agent,feature_models:data.feature_models}});
  expect(result.ok(),await result.text()).toBeTruthy();
}

const section = (page, feature) => page.locator(`[data-model-feature="${feature}"]`);
const box = (page, feature, name) => section(page, feature).getByRole('combobox',{name,exact:true});
const pick = (page, feature, name, label) => choosePicker(page, name, label, feature);

for (const width of [390,1440]) test('连接测试状态区分成功失败与运行中，主题切换与重绘保持状态 '+width,async({page,request})=>{
  const original=await (await request.get('/api/settings')).json();
  const config=model=>({runtime:'codex',codex:{model,profile:'',provider:'',sandbox:'read-only'},pi:{provider:'',model:''}});
  await saveModels(request,{...original,agent:{...original.agent,...config('ui-running')},
    feature_models:{...original.feature_models,topics:config('ui-failed'),reports:config('ui-success')}});
  let release;
  const gate=new Promise(resolve=>release=resolve);
  await page.route('**/api/settings/connection-test',async route=>{
    const model=route.request().postDataJSON().codex.model;
    if(model==='ui-running') await gate;
    await route.fulfill({json:{status:model==='ui-failed'?'failed':'success',message:model==='ui-failed'?'模型连接失败':'模型连接成功',elapsed_ms:15}});
  });
  try {
    await page.setViewportSize({width,height:900});await openModels(page);
    for(const id of ['testConnectionBtn','testConnectionBtn-topics','testConnectionBtn-reports']) await page.locator('#'+id).click();
    const terminals=[page.locator('#connTestTerminal'),page.locator('#connTestTerminal-topics'),page.locator('#connTestTerminal-reports')];
    for(const [index,status] of ['running','failed','success'].entries()) await expect(terminals[index]).toHaveAttribute('data-status',status);
    for(const [theme,colorScheme] of [['light','light'],['dark','dark'],['system','light'],['system','dark'],['eink','light']]) {
      await page.emulateMedia({colorScheme});await chooseTheme(page,theme);
      const colors=[];
      for(const terminal of terminals) {
        await expect.poll(()=>contrast(terminal)).toBeGreaterThanOrEqual(4.5);
        expect(await terminal.evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
        colors.push(await terminal.evaluate(node=>getComputedStyle(node).borderInlineStartColor));
        if(width===1440 && ['light','dark'].includes(theme)) await terminal.screenshot({path:`.ui-artifacts/connection-${await terminal.getAttribute('data-status')}-${theme}.png`});
      }
      expect(new Set(colors).size).toBe(theme==='eink'?1:3);
      await page.screenshot({path:`.ui-artifacts/connection-status-${width}-${theme}-${colorScheme}.png`});
    }
    if(width===390) await page.locator('#settingsCategoryBackBtn').click();
    await page.locator('[data-settings-tab="system"]').click();
    if(width===390) await page.locator('#settingsCategoryBackBtn').click();
    await page.locator('[data-settings-tab="models"]').click();
    for(const [index,status] of ['running','failed','success'].entries()) await expect(terminals[index]).toHaveAttribute('data-status',status);
    release();
    await expect(terminals[0]).toHaveAttribute('data-status','success');
    await page.unroute('**/api/settings/connection-test');
    await page.route('**/api/settings/connection-test',route=>route.abort('failed'));
    await page.locator('#testConnectionBtn-reports').click();
    await expect(terminals[2]).toHaveAttribute('data-status','failed');
    await expect(terminals[2]).toContainText('[FAILED]');
  } finally {release();await request.put('/api/settings',{data:original});}
});

test('四个功能同时展开、独立编辑保存和连接测试',async({page,request})=>{
  const original=await (await request.get('/api/settings')).json();
  const codex={model:'ui-catalog-a',provider:'',profile:'',sandbox:'read-only'};
  const config={runtime:'codex',codex,pi:{provider:'',model:''}};
  await request.put('/api/settings',{data:{llm:original.llm,agent:{...original.agent,runtime:'codex',codex},
    feature_models:Object.fromEntries(['topics','summaries','reports'].map(key=>[key,config]))}});
  try {
    await openModels(page);
    await expect(page.getByRole('combobox',{name:'功能',exact:true})).toHaveCount(0);
    await expect(page.locator('[data-model-feature]')).toHaveCount(4);
    for (const feature of ['chat','topics','summaries','reports']) await expect(box(page,feature,'模型')).toHaveText('ui-catalog-a');
    await page.locator('#refreshModelsBtn').click();
    await expect(page.locator('#modelsNotice')).toContainText('刷新');
    await pick(page,'chat','模型','ui-catalog-b');
    await pick(page,'topics','Runtime','Pi SDK / OAR');
    await pick(page,'topics','服务商','Test provider');
    await pick(page,'topics','模型','Test model');
    await page.locator('#refreshModelsBtn-summaries').click();
    await expect(page.locator('#modelsNotice-summaries')).toContainText('刷新');
    await pick(page,'summaries','推理强度','high');
    await openModelAdvanced(page,'reports');
    await page.locator('input[name="feature_models.reports.codex.profile"]').fill('report-profile');
    await page.locator('input[name="feature_models.reports.codex.model"]').fill('report-only');
    let tested;
    await page.route('**/api/settings/connection-test',route=>{
      tested=route.request().postDataJSON();
      return route.fulfill({json:{status:'success',message:'本次模型请求成功。',elapsed_ms:1}});
    });
    await page.locator('#testConnectionBtn-reports').click();
    await expect(page.locator('#connTestTerminal-reports')).toContainText('[SUCCESS]');
    await expect(page.locator('#connTestTerminal-reports')).toHaveAttribute('data-status','success');
    expect(tested.codex).toMatchObject({model:'report-only',profile:'report-profile'});
    await expect(page.locator('#connTestTerminal')).toBeHidden();
    await expect(page.locator('#connTestTerminal')).toHaveAttribute('data-status','ready');
    await expect(box(page,'chat','模型')).toHaveText('ui-catalog-b');
    await page.locator('[data-settings-tab="system"]').click();
    await page.locator('[data-settings-tab="models"]').click();
    await expect(box(page,'reports','模型')).toHaveText('report-only');
    await page.locator('#settingsSaveBtn').click();
    await expect(page.locator('#settingsStatus')).toHaveText('设置已保存');
    const saved=await (await request.get('/api/settings')).json();
    expect(saved.agent.codex.model).toBe('ui-catalog-b');
    expect(saved.feature_models.topics.runtime).toBe('pi');
    expect(saved.feature_models.topics.pi).toEqual({provider:'test',model:'test-model'});
    expect(saved.feature_models.summaries.codex.reasoning_effort).toBe('high');
    expect(saved.feature_models.reports.codex.model).toBe('report-only');
    await openModels(page);
    for(const [feature,runtime,model] of [['chat','Codex CLI','ui-catalog-b'],['topics','Pi SDK / OAR','Test model'],
      ['summaries','Codex CLI','ui-catalog-a'],['reports','Codex CLI','report-only']]) {
      await expect(box(page,feature,'Runtime')).toHaveText(runtime);
      await expect(box(page,feature,'模型')).toHaveText(model);
    }
    await expect(page.locator('#settingsSaveBtn')).toBeHidden();
  } finally {await request.put('/api/settings',{data:original}).catch(()=>{});}
});

test('并行连接测试和迟到目录只更新对应功能，编辑后的旧结果失效',async({page,request})=>{
  const original=await (await request.get('/api/settings')).json();
  const codex={model:'ui-catalog-a',provider:'',profile:'',sandbox:'read-only'};
  await saveModels(request,{...original,agent:{...original.agent,runtime:'codex',codex},
    feature_models:{...original.feature_models,reports:{runtime:'codex',codex:{...codex,model:'report-model'},pi:{provider:'',model:''}}}});
  let releaseTest,releaseCatalog;
  const testGate=new Promise(resolve=>releaseTest=resolve), catalogGate=new Promise(resolve=>releaseCatalog=resolve);
  await page.route('**/api/settings/connection-test',async route=>{
    const chat=route.request().postDataJSON().codex.model==='ui-catalog-a';
    if(chat) await testGate;
    await route.fulfill({json:{status:'success',message:chat?'旧功能结果':'日报连接成功'}});
  });
  await page.route('**/api/settings/models',async route=>{await catalogGate;await route.fulfill({status:503,json:{error:'旧目录失败'}});});
  try {
    await openModels(page);
    await page.locator('#testConnectionBtn').click();
    await page.locator('#refreshModelsBtn').click();
    await page.locator('#testConnectionBtn-reports').click();
    await expect(page.locator('#connTestTerminal-reports')).toContainText('日报连接成功');
    await openModelAdvanced(page);
    await page.locator('input[name="agent.codex.profile"]').fill('new-profile');
    releaseTest();releaseCatalog();
    await expect(page.locator('#testConnectionBtn')).toBeEnabled();
    await expect(page.locator('#refreshModelsBtn')).toBeEnabled();
    await expect(page.locator('#connTestTerminal')).toBeHidden();
    await expect(page.locator('#connTestTerminal')).toHaveAttribute('data-status','ready');
    await expect(section(page,'chat')).not.toContainText('旧功能结果');
    await expect(section(page,'chat')).not.toContainText('旧目录失败');
    await expect(page.locator('#connTestTerminal-reports')).toContainText('日报连接成功');
  } finally {releaseTest();releaseCatalog();await request.put('/api/settings',{data:original});}
});

for (const width of [390,1440]) test('各功能分区复用设置样式，适配主题及窄屏 '+width,async({page})=>{
  await page.setViewportSize({width,height:900});await openModels(page);
  await pick(page,'reports','Runtime','Codex CLI');
  for(const [theme,scheme] of [['light','light'],['dark','dark'],['system','light'],['system','dark'],['eink','light']]) {
    await page.emulateMedia({colorScheme:scheme});await chooseTheme(page,theme);
    for(const feature of ['chat','topics','summaries','reports']) {
      await expect(section(page,feature)).toBeVisible();
      expect(await section(page,feature).evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
    }
    await box(page,'reports','模型').click();
    const menu=page.getByRole('listbox');await expect(menu).toBeVisible();
    const rect=await menu.boundingBox();expect(rect.x).toBeGreaterThanOrEqual(0);expect(rect.x+rect.width).toBeLessThanOrEqual(width);
    await page.keyboard.press('Escape');
    await page.screenshot({path:`.ui-artifacts/model-sections-${width}-${theme}-${scheme}.png`});
    await pick(page,'reports','Runtime','Pi SDK / OAR');
    await pick(page,'reports','服务商','Test provider');
    await pick(page,'reports','模型','Test model');
    await box(page,'reports','推理强度').click();
    await expect(menu).toBeVisible();
    const piBox=await menu.boundingBox();expect(piBox.x).toBeGreaterThanOrEqual(0);expect(piBox.x+piBox.width).toBeLessThanOrEqual(width);
    await page.keyboard.press('Escape');
    await expect(box(page,'reports','推理强度')).toBeFocused();
    expect(await section(page,'reports').evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
    await page.screenshot({path:`.ui-artifacts/model-sections-pi-${width}-${theme}-${scheme}.png`});
    await pick(page,'reports','Runtime','Codex CLI');
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
    await expect(section(page,'chat').getByRole('combobox',{name:'服务商',exact:true})).toHaveText('请选择服务商');
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
    await expect(section(page,'chat').getByRole('combobox',{name:'服务商',exact:true})).toHaveText('test');
    await expect(section(page,'chat').getByRole('combobox',{name:'模型',exact:true})).toHaveText('Test model');
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
    await expect(section(page,'chat').getByRole('combobox',{name:'推理强度',exact:true})).toBeEnabled();
    await pick(page,'chat','推理强度','high');
    await pick(page,'chat','模型','Plain model');
    await expect(section(page,'chat').getByRole('combobox',{name:'推理强度',exact:true})).toHaveText('沿用默认');
    await expect(page.locator('select[name="llm.pi_chat.reasoning_effort"] option')).toHaveText(['沿用默认','off']);
    await pick(page,'chat','模型','Test model');
    await pick(page,'chat','推理强度','high');
    await pick(page,'topics','推理强度','off');
    await page.locator('#testConnectionBtn-topics').click();
    await expect(page.locator('#connTestTerminal-topics')).toContainText('[SUCCESS] test · test-model · off');
    expect(tested.pi).toEqual({...pi,reasoning_effort:'off'});
    await pick(page,'reports','推理强度','low');
    await page.locator('#settingsSaveBtn').click();
    await expect(page.locator('#settingsStatus')).toHaveText('设置已保存');
    const saved=await (await request.get('/api/settings')).json();
    expect(saved.llm.pi_chat.reasoning_effort).toBe('high');
    expect(saved.feature_models.topics.pi.reasoning_effort).toBe('off');
    expect(saved.feature_models.reports.pi.reasoning_effort).toBe('low');
    await expect(section(page,'chat').getByRole('combobox',{name:'推理强度',exact:true})).toHaveText('high');
    await pick(page,'chat','服务商','请选择服务商');
    await expect(section(page,'chat').getByRole('combobox',{name:'推理强度',exact:true})).toHaveText('沿用默认');
    await expect(section(page,'chat').getByRole('combobox',{name:'推理强度',exact:true})).toBeDisabled();
  } finally {await saveModels(request,original);}
});
