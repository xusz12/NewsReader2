import {test,expect} from '@playwright/test';
import {chooseTheme,openNavigation,openSettings} from './helpers.js';

const codex={runtime:'codex',codex:{model:'ui-catalog-a',profile:'',provider:'',sandbox:'read-only',reasoning_effort:''},pi:{provider:'test-provider',model:'test-model'}};
const pi={...codex,runtime:'pi'};
let saved;
test.beforeEach(async({request})=>{
  saved=await (await request.get('/api/settings')).json();
  await request.post('/__test/reports',{data:{empty:true}});
});
test.afterEach(async({request})=>{
  await request.put('/api/settings',{data:{llm:saved.llm,feature_models:saved.feature_models}});
  await request.post('/__test/reports',{data:{empty:true}});
});
async function create(request, config) {
  expect((await request.put('/api/settings',{data:{llm:saved.llm,feature_models:{...saved.feature_models,reports:config}}})).ok()).toBe(true);
  const {database_token}=await (await request.get('/api/reports/manual-options')).json();
  const response=await request.post('/api/reports/manual',{data:{database_token,request_id:crypto.randomUUID(),
    kind:'custom',period_start:'2026-10-10T00:00:00+08:00',period_end:'2026-10-23T00:00:00+08:00'}});
  expect(response.status()).toBe(202);
  return (await response.json()).report;
}
async function openReports(page) {await openNavigation(page);await page.locator('#navReportsBtn').click();}

test('新建表单显示日报模型，重新打开时更新模型并保留所选日期',async({page,request})=>{
  expect((await request.put('/api/settings',{data:{llm:saved.llm,feature_models:{...saved.feature_models,reports:codex}}})).ok()).toBe(true);
  await page.goto('/');await openReports(page);await page.locator('#createReportBtn').click();
  await expect(page.locator('#reportCreationModel')).toHaveText('Codex · ui-catalog-a');
  await expect(page.locator('#reportSelection')).toHaveValue('2026-10-30');
  await page.locator('#reportSelection').fill('2026-10-28');
  await page.keyboard.press('Escape');
  expect((await request.put('/api/settings',{data:{llm:saved.llm,feature_models:{...saved.feature_models,reports:pi}}})).ok()).toBe(true);
  await page.locator('#createReportBtn').click();
  await expect(page.locator('#reportCreationModel')).toHaveText('Pi · test-model');
  await expect(page.locator('#reportSelection')).toHaveValue('2026-10-28');
  const result = page.waitForResponse(response=>response.url().endsWith('/api/reports/manual') && response.request().method()==='POST');
  await page.getByRole('button',{name:'开始生成',exact:true}).click();
  expect((await (await result).json()).report.model_label).toBe('Pi · test-model');
});

test('先后切换配置并入队，卡片、正文和顶部状态仍标明各自模型及相同取材时间',async({page,request})=>{
  const a=await create(request,codex), b=await create(request,pi);
  await request.put('/api/settings',{data:{llm:saved.llm,feature_models:{...saved.feature_models,reports:{...codex,codex:{...codex.codex,model:'ui-catalog-b'}}}}});
  await page.goto('/');await openReports(page);
  await expect(page.locator(`[data-report-id="${a.id}"]`)).toContainText('Codex · ui-catalog-a');
  await expect(page.locator(`[data-report-id="${b.id}"]`)).toContainText('Pi · test-model');
  await page.locator('#classificationProgressIndicator').focus();
  await expect(page.locator(`#taskProgressReportList [data-task-id="${a.id}"]`)).toContainText('Codex · ui-catalog-a');
  await expect(page.locator(`#taskProgressReportList [data-task-id="${b.id}"]`)).toContainText('Pi · test-model');
  await page.keyboard.press('Escape');
  await request.post('/__test/reports/run');await request.post('/__test/reports/run');
  const [first,second]=await Promise.all([a,b].map(async report=>(await (await request.get('/api/reports/'+report.id)).json()).report));
  expect(first.status).toBe('done');expect(second.status).toBe('done');
  expect(first.snapshot_id).toBeTruthy();expect(first.snapshot_id).toBe(second.snapshot_id);
  expect(first.snapshot_captured_at).toBe(second.snapshot_captured_at);
  await page.reload();await openReports(page);await page.locator(`[data-report-id="${a.id}"]`).click();
  await expect(page.locator('#reportContent header')).toContainText('Codex · ui-catalog-a');
  await expect(page.locator('#reportContent header')).toContainText('取材于');
  await page.locator(`[data-report-id="${b.id}"]`).click();
  await expect(page.locator('#reportContent header')).toContainText('Pi · test-model');
});

for(const width of [320,390,1440])test('模型信息沿用阅读布局，设置展示多份当前任务 '+width,async({page,request})=>{
  await page.setViewportSize({width,height:width===320?568:900});
  const a=await create(request,codex),b=await create(request,pi);
  await request.post('/__test/reports/run');await request.post('/__test/reports/run');
  await page.goto('/');await openReports(page);
  for(const [theme,scheme] of [['light','light'],['dark','dark'],['system','light'],['system','dark'],['eink','light']]){
    await page.emulateMedia({colorScheme:scheme});await chooseTheme(page,theme);
    await page.locator(`[data-report-id="${a.id}"]`).click();
    await expect(page.locator('#reportContent header')).toContainText('Codex · ui-catalog-a');
    expect(await page.locator('#reportContent').evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
    await page.screenshot({path:`output/report-comparison-check/report-${width}-${theme}-${scheme}.png`});
    if(width<=920)await page.locator('#mobileReaderBackBtn').click();
  }
  const observation=await (await request.get('/api/reports/schedule-status')).json();
  const current=[a,b].map(row=>({...row,trigger:'manual',status:'running',phase:'editing',progress:{stage:'writing'}}));
  await page.route('**/api/reports/schedule-status',route=>route.fulfill({json:{...observation,worker_running:true,
    scheduler:{...observation.scheduler,status:'running'},concurrency:3,queue:{pending:0,running:2,failed:0},current:current[0],current_reports:current}}));
  await openSettings(page);await page.locator('[data-settings-tab="schedules"]').click();
  const status=page.locator('section[aria-labelledby="scheduleStatusTitle"]');
  await expect(status).toContainText('最多并行 3 份');await expect(status).toContainText('Codex · ui-catalog-a');await expect(status).toContainText('Pi · test-model');
  expect(await status.evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
});
