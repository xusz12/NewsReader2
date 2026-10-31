import { choosePicker, openModelAdvanced } from "./helpers.js";
import {test,expect} from '@playwright/test';
import {openSettings,chooseTheme} from './helpers.js';

function fixture() {
  const record = {id:'report-evening',kind:'evening',kind_label:'晚报',title:'合成晚报',
    period_start:'2026-10-29T21:30:00+08:00',period_end:'2026-10-30T21:30:00+08:00',
    scheduled_at:'2026-10-30T21:30:00+08:00',updated_at:'2026-10-30T21:31:00+08:00',
    status:'pending',error:'report_index_permission_denied',attempts:1,
    next_retry_at:'2026-10-30T21:36:00+08:00',trigger:'preview',phase:null};
  return {ok:true,observed_at:'2026-10-30T21:32:00+08:00',activated_at:'2026-10-30T14:00:00+08:00',
    scheduler:{status:'running',error:'',last_checked_at:'2026-10-30T21:31:50+08:00'},
    worker_running:true,queue:{pending:2,running:1,failed:1,skipped:0},retrying:1,calendar_warning:false,
    current:{...record,title:'合成周报',phase:'editing'},
    schedules:[['morning','早报','交易日 08:30','2026-11-02T08:30:00+08:00'],['noon','午报','交易日 14:30','2026-11-02T14:30:00+08:00'],
      ['evening','晚报','每天 21:30','2026-10-31T21:30:00+08:00'],['weekly','周报','每周一 08:30','2026-11-02T08:30:00+08:00'],
      ['monthly','月报','每月 1 日 08:30','2026-11-01T08:30:00+08:00']].map(([kind,kind_label,rule,next_run_at])=>({kind,kind_label,rule,next_run_at,latest:null})),
    recent:[record,{...record,id:'report-noon',kind:'noon',kind_label:'午报',title:'合成午报',status:'done',error:'',trigger:'scheduled',next_retry_at:null}]};
}
async function section(page,name) {
  if(await page.locator('#feedColSettings').evaluate(node=>node.inert)) await page.locator('#settingsCategoryBackBtn').click();
  await page.locator(`[data-settings-tab="${name}"]`).click();
}
async function observe(page) {
  const state={data:fixture(),failed:false,requests:0};
  await page.clock.install();
  await page.route('**/api/reports/schedule-status',route=>{
    state.requests++;
    return route.fulfill(state.failed ? {status:503,json:{ok:false,error:'report_schedule_unavailable'}} : {json:state.data});
  });
  await page.goto('/');await openSettings(page);await section(page,'schedules');
  await expect(page.locator('[data-schedule-kind]')).toHaveCount(5);
  return state;
}

test.beforeEach(async({request})=>{await request.post('/__test/reports');});

test('功能选择包含各功能，独立连接测试显示精简结果',async({page})=>{
  await page.route('**/api/settings/connection-test',route=>route.fulfill({json:{ok:true,status:'success',message:'本次模型请求成功。',elapsed_ms:42}}));
  await page.goto('/');await openSettings(page);await section(page,'models');
  await page.getByRole('combobox',{name:'功能',exact:true}).click();
  for(const text of ['Chat','主题追踪','新闻总结','日报']) await expect(page.getByRole('option',{name:text,exact:true})).toBeVisible();
  await page.keyboard.press('Escape');
  await page.locator('#testConnectionBtn').click();
  await expect(page.locator('#connTestTerminal')).toContainText('[SUCCESS] 本次模型请求成功。');
  await expect(page.locator('#connTestTerminal')).not.toContainText('不代表后续稳定或网页工具可用');
});

test('观察真实接口只读，排期与执行记录可见',async({page})=>{
  const writes=[];
  page.on('request',request=>{if(request.method()!=='GET')writes.push(request.url());});
  await page.goto('/');await openSettings(page);
  await expect(page.locator('#modelSettings')).toBeAttached();
  writes.length=0; // Startup's independent indexing is outside this observation.
  await section(page,'schedules');
  await expect(page.locator('[data-schedule-kind]')).toHaveCount(5);
  await expect(page.locator('#scheduleSettings')).toContainText('任务服务未运行'); // Test server has no workers.
  await expect(page.locator('[data-schedule-report]')).toHaveCount(5);
  expect(writes.filter(url=>/reports|reindex|settings|mark-read/.test(url))).toEqual([]);
});

test('自动刷新保留设置草稿，离开分类后停止，失败保留最后成功状态',async({page})=>{
  const state=await observe(page);
  await section(page,'models');
  await choosePicker(page,'Runtime','Codex CLI');
  await openModelAdvanced(page);
  await page.locator('input[name="agent.codex.model"]').fill('unsaved-test-model');
  await section(page,'schedules');
  await expect(page.locator('[data-schedule-count="pending"]')).toHaveText('2');
  state.data.queue.pending=4;
  await page.clock.runFor(10100);
  await expect(page.locator('[data-schedule-count="pending"]')).toHaveText('4');
  state.failed=true;
  await page.locator('[data-schedule-refresh]').click();
  await expect(page.locator('#scheduleSettings')).toContainText('以下为上次结果');
  await expect(page.locator('[data-schedule-count="pending"]')).toHaveText('4');
  await expect(page.locator('[data-schedule-updated]')).toContainText('21:32');
  await section(page,'models');
  await expect(page.locator('input[name="agent.codex.model"]')).toHaveValue('unsaved-test-model');
  await expect(page.locator('#settingsSaveBtn')).toBeVisible();
  const count=state.requests;await page.clock.runFor(30100);expect(state.requests).toBe(count);
  state.failed=false;await section(page,'schedules');
  await expect(page.locator('#scheduleSettings')).not.toContainText('以下为上次结果');
  await page.locator('#closeSettingsBtn').click();
  const closed=state.requests;await page.clock.runFor(30100);expect(state.requests).toBe(closed);
});

test('首次请求失败不伪造空队列，恢复后显示排期异常',async({page})=>{
  let failed=true;
  const data=fixture();data.scheduler.status='error';data.scheduler.error='report_calendar_failed';data.calendar_warning=true;
  await page.route('**/api/reports/schedule-status',route=>route.fulfill(failed ? {status:503,json:{ok:false}} : {json:data}));
  await page.goto('/');await openSettings(page);await section(page,'schedules');
  await expect(page.locator('#scheduleSettings')).toContainText('任务状态读取失败');
  await expect(page.locator('[data-schedule-count]')).toHaveCount(0);
  failed=false;await page.locator('[data-schedule-refresh]').click();
  await expect(page.locator('#scheduleSettings')).toContainText('交易日历读取失败');
  await expect(page.locator('#scheduleSettings')).toContainText('排期检查失败');
});

test('迟到响应不覆盖已切换页面',async({page})=>{
  let release;
  const held=new Promise(resolve=>release=resolve);
  await page.route('**/api/reports/schedule-status',async route=>{await held;await route.fulfill({json:fixture()}).catch(()=>{});});
  await page.goto('/');await openSettings(page);await section(page,'schedules');
  await expect(page.locator('#scheduleSettings')).toContainText('正在读取任务状态');
  await section(page,'models');release();
  await expect(page.getByRole('combobox',{name:'功能',exact:true})).toBeVisible();
  await expect(page.locator('#scheduleSettings')).toBeHidden();
});

for(const width of [320,390,921,1440]) test('排期、错误与报告跳转沿用设置布局 '+width,async({page})=>{
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.setViewportSize({width,height:1000});
  const state=await observe(page);
  await expect(page.locator('[data-schedule-count]')).toHaveText(['1','2','1']);
  await expect(page.locator('#scheduleSettings')).toContainText('正在整理材料、生成报告');
  const record=page.locator('[data-schedule-report="report-evening"]');
  await expect(record).toContainText('试生成');await expect(record).toContainText('等待重试');
  await expect(record).toContainText('没有权限读取新闻目录或文件');
  await expect(record).toContainText('21:36');
  for(const [theme,colorScheme] of [['light','light'],['dark','dark'],['system','light'],['system','dark'],['eink','light']]) {
    await page.emulateMedia({colorScheme});await chooseTheme(page,theme);
    expect(await page.locator('.settings-main').evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
    expect(await page.locator('#scheduleSettings').evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
  }
  await chooseTheme(page,'light');
  await page.screenshot({path:`output/report-optimization-check/schedules-${width}.png`});
  if(width<=920) {
    await page.locator('#settingsCategoryBackBtn').click();
    const count=state.requests;await page.clock.runFor(30100);expect(state.requests).toBe(count);
    await expect(page.locator('.settings-category-card:visible')).toHaveCount(9);
    await section(page,'schedules');
  }
  await record.click();
  await expect(page.locator('#reportContent h1')).toHaveText('合成晚报');
  await expect(page.locator('#feedView')).not.toHaveClass(/settings-active/);
  if(width<=920) await expect(page.locator('#detailPanelReports')).toBeInViewport();
  expect(errors).toEqual([]);
});
