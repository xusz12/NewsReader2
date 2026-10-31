import {test,expect} from '@playwright/test';
import {choosePicker,chooseTheme,openNavigation} from './helpers.js';

const config = {runtime:'codex',codex:{model:'ui-catalog-a',profile:'',provider:'',sandbox:'read-only',reasoning_effort:''},pi:{provider:'test',model:'test-model'}};
let saved;
test.beforeEach(async({request})=>{
  saved = await (await request.get('/api/settings')).json();
  await request.put('/api/settings',{data:{llm:saved.llm,feature_models:{...saved.feature_models,reports:config}}});
  await request.post('/__test/reports',{data:{empty:true}});
});
test.afterEach(async({request})=>{
  await request.put('/api/settings',{data:{llm:saved.llm,feature_models:saved.feature_models}});
  await request.post('/__test/reports',{data:{empty:true}});
});
async function open(page) {
  await page.goto('/'); await openNavigation(page); await page.locator('#navReportsBtn').click();
  await page.locator('#createReportBtn').click();
  await expect(page.locator('#reportCreationModel')).toHaveText('Codex · ui-catalog-a');
  await page.locator('#reportSelection').fill('2026-10-28');
}
async function expand(page) {
  await page.locator('#reportTemporaryModel summary').click();
  await expect(page.getByRole('combobox',{name:'模型',exact:true})).toBeEnabled();
}
async function submit(page) {
  const result = page.waitForResponse(r=>r.url().endsWith('/api/reports/manual') && r.request().method()==='POST');
  await page.getByRole('button',{name:'开始生成',exact:true}).click();
  const response = await result;
  expect(response.status()).toBe(202);
  return {body:response.request().postDataJSON(),report:(await response.json()).report};
}

test('临时模型默认折叠，不展开时不读取目录并沿用日报设置',async({page})=>{
  let catalogs = 0;
  page.on('request',r=>{if(r.url().endsWith('/api/reports/model-options'))catalogs++;});
  await open(page);
  await expect(page.locator('#reportTemporaryModel')).not.toHaveAttribute('open');
  await expect(page.getByRole('combobox',{name:'Runtime',exact:true})).not.toBeVisible();
  const {body,report} = await submit(page);
  expect(body.model_override).toBeUndefined();expect(catalogs).toBe(0);
  expect(report.model_label).toBe('Codex · ui-catalog-a');
});

test('临时 Codex 模型和推理程度绑定本次，关闭重开保留，后续新建恢复默认',async({page,request})=>{
  await open(page);await expand(page);
  await choosePicker(page,'推理程度','high');
  await expect(page.locator('#reportCreationModel')).toHaveText('Codex · ui-catalog-a · high · 临时');
  await request.put('/api/settings',{data:{llm:saved.llm,feature_models:{...saved.feature_models,reports:{...config,runtime:'pi'}}}});
  await page.keyboard.press('Escape');await page.locator('#createReportBtn').click();
  await expect(page.getByRole('combobox',{name:'推理程度',exact:true})).toHaveText('high');
  await choosePicker(page,'模型','ui-catalog-b');
  await expect(page.getByRole('combobox',{name:'推理程度',exact:true})).toHaveText('沿用默认');
  await choosePicker(page,'推理程度','medium');
  const {body,report}=await submit(page);
  expect(body.model_override).toEqual({runtime:'codex',model:'ui-catalog-b',reasoning_effort:'medium'});
  expect(report.model_label).toBe('Codex · ui-catalog-b · medium');
  expect((await (await request.get('/api/settings')).json()).feature_models.reports.codex.model).toBe('ui-catalog-a');
  expect((await (await request.get('/api/settings')).json()).feature_models.reports.runtime).toBe('pi');
  await page.locator('#createReportBtn').click();
  await expect(page.locator('#reportTemporaryModel')).not.toHaveAttribute('open');
  await expect(page.locator('#reportCreationModel')).toHaveText('Pi · test-model');
});

test('临时 Pi 使用独立模型和推理程度，窗口不变并共用取材',async({page,request})=>{
  await open(page);
  const first=(await submit(page)).report;
  await page.locator('#createReportBtn').click();await page.locator('#reportSelection').fill('2026-10-28');await expand(page);
  await choosePicker(page,'Runtime','Pi SDK / OAR');
  await expect(page.getByRole('combobox',{name:'模型',exact:true})).toBeEnabled();
  await choosePicker(page,'模型','comparison-model · test');await choosePicker(page,'推理程度','high');
  const {body,report}=await submit(page);
  expect(body.model_override).toEqual({runtime:'pi',provider:'test',model:'comparison-model',reasoning_effort:'high'});
  expect(report.model_label).toBe('Pi · comparison-model · high');
  await request.post('/__test/reports/run');await request.post('/__test/reports/run');
  const a=(await (await request.get('/api/reports/'+first.id)).json()).report;
  const b=(await (await request.get('/api/reports/'+report.id)).json()).report;
  expect(a.status).toBe('done');expect(b.status).toBe('done');expect(a.snapshot_id).toBeTruthy();expect(a.snapshot_id).toBe(b.snapshot_id);
  expect((await (await request.get('/api/settings')).json()).feature_models.reports.runtime).toBe('codex');
});

test('恢复日报设置移除临时选择；目录读取失败可以重试',async({page})=>{
  await page.route('**/api/reports/model-options',route=>route.fulfill({status:409,json:{ok:false,error:'report_model_catalog_failed'}}),{times:1});
  await open(page);await expand(page);
  await expect(page.locator('#reportModelChoices')).toContainText('模型目录读取失败');
  await page.getByRole('button',{name:'重试',exact:true}).click();
  await expect(page.getByRole('combobox',{name:'推理程度',exact:true})).toBeEnabled();
  await choosePicker(page,'推理程度','high');await page.getByRole('button',{name:'恢复日报设置',exact:true}).click();
  await expect(page.locator('#reportCreationModel')).toHaveText('Codex · ui-catalog-a');
  expect((await submit(page)).body.model_override).toBeUndefined();
});

test('切换 Runtime 后迟到的目录不会覆盖当前选项，键盘选择后保留焦点',async({page})=>{
  let release;const gate=new Promise(resolve=>release=resolve);
  await page.route('**/api/reports/model-options',async route=>{
    if(route.request().postDataJSON().runtime==='codex')await gate;
    await route.continue();
  });
  await open(page);await page.locator('#reportTemporaryModel summary').click();
  await choosePicker(page,'Runtime','Pi SDK / OAR');
  await expect(page.getByRole('combobox',{name:'模型',exact:true})).toBeEnabled();
  release();
  await expect(page.getByRole('combobox',{name:'Runtime',exact:true})).toHaveText('Pi SDK / OAR');
  const effort=page.getByRole('combobox',{name:'推理程度',exact:true});await effort.focus();
  await page.keyboard.press('Enter');await page.keyboard.press('End');await page.keyboard.press('Enter');
  await expect(effort).toHaveText('high');await expect(effort).toBeFocused();
});

for(const width of [320,390,921,1440])test('临时模型选择沿用应用主题和窄屏滚动 '+width,async({page})=>{
  await page.setViewportSize({width,height:width===320?568:900});await open(page);
  for(const [theme,scheme] of [['light','light'],['dark','dark'],['system','light'],['system','dark'],['eink','light']]){
    if(await page.locator('#reportCreationPopover').isVisible())await page.keyboard.press('Escape');
    await page.emulateMedia({colorScheme:scheme});await chooseTheme(page,theme);
    await page.locator('#createReportBtn').click();
    const details=page.locator('#reportTemporaryModel');
    if(!await details.evaluate(node=>node.open))await expand(page);
    await page.getByRole('combobox',{name:'Runtime',exact:true}).scrollIntoViewIfNeeded();
    const popover=page.locator('#reportCreationPopover');
    expect(await popover.evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
    await page.screenshot({path:`output/report-model-picker-check/${width}-${theme}-${scheme}.png`});
    await page.getByRole('button',{name:'开始生成',exact:true}).scrollIntoViewIfNeeded();
    await expect(page.getByRole('button',{name:'开始生成',exact:true})).toBeInViewport();
  }
});
