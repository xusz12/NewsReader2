import {test,expect} from '@playwright/test';
import {choosePicker,openSettings,openNavigation,openModelAdvanced} from './helpers.js';

const kinds={morning:'早报',noon:'午报',evening:'晚报',weekly:'周报',monthly:'月报'};
const group=kind=>['weekly','monthly'].includes(kind)?'periodic':'daily';
const feature=kind=>group(kind)==='periodic'?'reports_periodic':'reports';
const config=kind=>({runtime:'codex',codex:{model:`${kind}-default`,profile:`${kind}-profile`,provider:'',sandbox:'read-only'},
  pi:{provider:'test',model:'test-model',reasoning_effort:''}});
let original;
test.beforeEach(async({request})=>{
  original=await (await request.get('/api/settings')).json();
  const result=await request.put('/api/settings',{data:{llm:original.llm,
    feature_models:{reports:config('daily'),reports_periodic:config('periodic')}}});
  expect(result.ok(),await result.text()).toBe(true);
  await request.post('/__test/reports',{data:{empty:true}});
});
test.afterEach(async({request})=>{
  const feature_models=Object.fromEntries(Object.entries(original.feature_models).map(([key,value])=>
    [key,{...value,pi:{...value.pi,reasoning_effort:value.pi.reasoning_effort || ''}}]));
  const restored=await request.put('/api/settings',{data:{llm:original.llm,agent:original.agent,feature_models}});
  expect(restored.ok(),await restored.text()).toBe(true);
  await request.post('/__test/reports',{data:{empty:true}});
});

for(const width of [390,1440]) test('日报与周月报只有两组配置，独立保存撤销和重新载入 '+width,async({page,request})=>{
  await page.setViewportSize({width,height:900});
  await page.goto('/');await openSettings(page);await page.locator('[data-settings-tab="models"]').click();
  await expect(page.locator('[data-model-feature]')).toHaveCount(5);
  await expect(page.locator('#modelSettings')).not.toContainText('自定义报告');
  await expect(page.locator('[data-model-feature="reports"] .section-card-title')).toHaveText('日报（早／午／晚）');
  await expect(page.locator('[data-model-feature="reports_periodic"] .section-card-title')).toHaveText('周报／月报');
  for(const kind of ['morning','weekly']) {
    await openModelAdvanced(page,feature(kind));
    await page.locator(`input[name="feature_models.${feature(kind)}.codex.model"]`).fill(`${group(kind)}-edited`);
  }
  await choosePicker(page,'Runtime','Pi SDK / OAR',feature('noon'));
  await choosePicker(page,'推理强度','high',feature('noon'));
  await page.locator('#settingsSaveBtn').click();
  await expect(page.locator('#settingsStatus')).toHaveText('设置已保存');
  const saved=await (await request.get('/api/settings')).json();
  for(const kind of ['morning','weekly']) {
    expect(saved.feature_models[feature(kind)].codex.model).toBe(`${group(kind)}-edited`);
    expect(saved.feature_models[feature(kind)].codex.profile).toBe(`${group(kind)}-profile`);
  }
  expect(saved.feature_models.reports.pi.reasoning_effort).toBe('high');
  expect(saved.feature_models.reports.runtime).toBe('pi');
  expect(Object.keys(saved.feature_models).sort()).toEqual(['reports','reports_periodic','summaries','topics']);
  expect(saved.agent).toEqual(original.agent);
  await page.reload();await openSettings(page);await page.locator('[data-settings-tab="models"]').click();
  await expect(page.locator('[data-model-feature="reports"]').getByRole('combobox',{name:'模型',exact:true})).toHaveText('Test model');
  await expect(page.locator('[data-model-feature="reports_periodic"]').getByRole('combobox',{name:'模型',exact:true})).toHaveText('periodic-edited');
  await openModelAdvanced(page,feature('weekly'));
  await page.locator('input[name="feature_models.reports_periodic.codex.model"]').fill('discard-me');
  await page.locator('#settingsResetBtn').click();
  await expect(page.locator('[data-model-feature="reports_periodic"]').getByRole('combobox',{name:'模型',exact:true})).toHaveText('periodic-edited');
  await expect(page.locator('#settingsSaveBtn')).toBeHidden();
  const picker=page.locator('[data-model-feature="reports_periodic"]').getByRole('combobox',{name:'模型',exact:true});
  const baseFont=await picker.evaluate(node=>getComputedStyle(node).fontSize);
  for(const value of ['extra-large','small','standard']) {
    const label=await page.locator(`#fontSizeSelect-native option[value="${value}"]`).textContent();
    await choosePicker(page,'阅读字号',label);
    await expect(picker).toHaveCSS('font-size',baseFont);
  }
  await page.screenshot({path:`.ui-artifacts/report-defaults-${width}.png`});
});

async function openReports(page) {
  await page.goto('/');await openNavigation(page);await page.locator('#navReportsBtn').click();
  await page.locator('#createReportBtn').click();
  await expect(page.locator('#reportCreationModel')).toHaveText('Codex · daily-default');
}

test('新增报告随类型显示默认模型并提交对应配置',async({page,request})=>{
  await openReports(page);
  await page.getByRole('combobox',{name:'报告类型',exact:true}).click();
  await expect(page.getByRole('option',{name:'自定义',exact:true})).toHaveCount(0);
  await page.keyboard.press('Escape');
  for(const [kind,label] of Object.entries(kinds)) {
    await choosePicker(page,'报告类型',label);
    await expect(page.locator('#reportCreationModel')).toHaveText(`Codex · ${group(kind)}-default`);
  }
  await choosePicker(page,'报告类型','月报');
  await page.locator('#reportSelection').fill('2026-09');
  const pending=page.waitForResponse(r=>r.url().endsWith('/api/reports/manual')&&r.request().method()==='POST');
  await page.getByRole('button',{name:'开始生成',exact:true}).click();
  const response=await pending;
  expect(response.request().postDataJSON().model_override).toBeUndefined();
  expect((await response.json()).report.model_label).toBe('Codex · periodic-default');
  const saved=await (await request.get('/api/settings')).json();
  expect(saved.feature_models.reports.codex.model).toBe('daily-default');
});

test('临时选择跨类型保留，恢复使用当前类型最新默认模型，目录请求包含类型',async({page,request})=>{
  const queried=[];
  page.on('request',req=>{if(req.url().endsWith('/api/reports/model-options')) queried.push(req.postDataJSON());});
  await openReports(page);await page.locator('#reportTemporaryModel summary').click();
  await expect(page.getByRole('combobox',{name:'模型',exact:true})).toBeEnabled();
  await choosePicker(page,'模型','ui-catalog-a');
  await choosePicker(page,'推理程度','high');
  await choosePicker(page,'报告类型','周报');
  await expect(page.locator('#reportCreationModel')).toHaveText('Codex · ui-catalog-a · high · 临时');
  await expect.poll(()=>queried.some(c=>c.kind==='weekly'&&c.runtime==='codex')).toBe(true);
  await page.getByRole('button',{name:'恢复默认模型',exact:true}).click();
  await expect(page.locator('#reportCreationModel')).toHaveText('Codex · periodic-default');
  await page.locator('#reportSelection').fill('2026-10-19');
  await page.keyboard.press('Escape');
  await request.put('/api/settings',{data:{llm:original.llm,
    feature_models:{reports_periodic:{...config('periodic'),runtime:'pi',pi:{provider:'test',model:'test-model',reasoning_effort:'low'}}}}});
  await page.locator('#createReportBtn').click();
  await expect(page.locator('#reportCreationModel')).toHaveText('Pi · test-model · low');
  await expect(page.locator('#reportSelection')).toHaveValue('2026-10-19');
});

test('类型切换时迟到目录不会覆盖新类型的选项',async({page})=>{
  let release;
  const gate=new Promise(resolve=>release=resolve);
  await page.route('**/api/reports/model-options',async route=>{
    const {kind}=route.request().postDataJSON();
    if(kind==='noon') await gate;
    await route.fulfill({json:{ok:true,catalog:{options:[{value:`${kind}-catalog`,reasoning_efforts:['high']}]}}});
  });
  try {
    await openReports(page);await page.locator('#reportTemporaryModel summary').click();
    await choosePicker(page,'报告类型','月报');
    await expect(page.getByRole('combobox',{name:'模型',exact:true})).toBeEnabled();
    release();
    await choosePicker(page,'模型','monthly-catalog');
    await expect(page.locator('#reportCreationModel')).toHaveText('Codex · monthly-catalog · 临时');
  } finally {release();}
});

test('重新打开保留两个 Runtime 的临时草稿',async({page})=>{
  await openReports(page);await page.locator('#reportTemporaryModel summary').click();
  await expect(page.getByRole('combobox',{name:'模型',exact:true})).toBeEnabled();
  await choosePicker(page,'模型','ui-catalog-b');
  await choosePicker(page,'推理程度','medium');
  await choosePicker(page,'Runtime','Pi SDK / OAR');
  await expect(page.getByRole('combobox',{name:'模型',exact:true})).toBeEnabled();
  await choosePicker(page,'模型','comparison-model · test');
  await page.keyboard.press('Escape');await page.locator('#createReportBtn').click();
  await expect(page.locator('#reportCreationModel')).toContainText('Pi · comparison-model');
  await choosePicker(page,'Runtime','Codex CLI');
  await expect(page.getByRole('combobox',{name:'模型',exact:true})).toHaveText('ui-catalog-b');
  await expect(page.getByRole('combobox',{name:'推理程度',exact:true})).toHaveText('medium');
});
