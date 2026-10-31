import { choosePicker, openModelAdvanced } from "./helpers.js";
import {test,expect} from '@playwright/test';
import {openSettings,openNavigation,chooseTheme} from './helpers.js';

function fixture() {
  const record = {id:'report-evening',kind:'evening',kind_label:'晚报',title:'合成晚报',
    period_start:'2026-10-29T21:30:00+08:00',period_end:'2026-10-30T21:30:00+08:00',
    scheduled_at:'2026-10-30T21:30:00+08:00',updated_at:'2026-10-30T21:31:00+08:00',
    status:'pending',error:'report_index_permission_denied',attempts:1,
    next_retry_at:'2026-10-30T21:36:00+08:00',trigger:'preview',phase:null};
  return {ok:true,database_token:'fixture-report-db',schedule_control:{paused:false,paused_at:null},snapshot_storage:{bytes:2048,clearable_bytes:1024,protected_bytes:1024,count:2,clearable_count:1},observed_at:'2026-10-30T21:32:00+08:00',activated_at:'2026-10-30T14:00:00+08:00',
    scheduler:{status:'running',error:'',last_checked_at:'2026-10-30T21:31:50+08:00'},
    worker_running:true,queue:{pending:2,running:1,failed:1,skipped:0},retrying:1,calendar_warning:false,
    current:{...record,title:'合成周报',phase:'editing'},
    schedules:[['morning','早报','交易日 08:30','2026-11-02T08:30:00+08:00'],['noon','午报','交易日 14:30','2026-11-02T14:30:00+08:00'],
      ['evening','晚报','每天 21:30','2026-10-31T21:30:00+08:00'],['weekly','周报','每周一 08:30','2026-11-02T08:30:00+08:00'],
      ['monthly','月报','每月 1 日 08:30','2026-11-01T08:30:00+08:00']].map(([kind,kind_label,rule,next_run_at])=>({kind,kind_label,rule,next_run_at,latest:null,window_end_time:{morning:'08:30',noon:'14:30',evening:'21:30'}[kind],window_label:{morning:'前日 21:30—当日 08:30',noon:'当日 08:30—14:30',evening:'前日 21:30—当日 21:30',weekly:'上周一 00:00—本周一 00:00',monthly:'上月 1 日 00:00—本月 1 日 00:00'}[kind]})).concat([{kind:'snapshot_cleanup',kind_label:'材料快照清理',frequency:'每 7 天',rule:'每 7 天',editable:false,next_run_at:'2026-11-06T10:00:00+08:00',last_run_at:null,error:'',removed_count:0}]),
    recent:[record,{...record,id:'report-noon',kind:'noon',kind_label:'午报',title:'合成午报',status:'done',error:'',trigger:'scheduled',next_retry_at:null}]};
}
async function section(page,name) {
  if(await page.locator('#feedColSettings').evaluate(node=>node.inert)) await page.locator('#settingsCategoryBackBtn').click();
  await page.locator(`[data-settings-tab="${name}"]`).click();
}
async function expectControlColor(page,tone) {
  const button=page.locator('[data-schedule-pause]');
  await expect(button).toHaveAttribute('data-tone',tone);
  await expect(button).toHaveText('');
  await expect(button.locator('svg')).toHaveAttribute('aria-hidden','true');
  await expect.poll(()=>button.evaluate((node,tone)=>{
    const probe=document.createElement('span');probe.style.color=`var(--status-${tone})`;node.append(probe);
    const expected=getComputedStyle(probe).color;probe.remove();
    return getComputedStyle(node).color===expected;
  },tone)).toBe(true);
}
async function observe(page) {
  const state={data:fixture(),failed:false,requests:0};
  await page.clock.install();
  await page.route('**/api/reports/schedule-status',route=>{
    state.requests++;
    return route.fulfill(state.failed ? {status:503,json:{ok:false,error:'report_schedule_unavailable'}} : {json:state.data});
  });
  await page.goto('/');await openSettings(page);await section(page,'schedules');
  await expect(page.locator('[data-schedule-kind]')).toHaveCount(6);
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

test('观察真实接口只读，不再显示最近执行',async({page,request})=>{
  await request.post('/__test/reports',{data:{archive:true}});
  const writes=[];
  page.on('request',request=>{if(request.method()!=='GET')writes.push(request.url());});
  await page.goto('/');await openSettings(page);
  await expect(page.locator('#modelSettings')).toBeAttached();
  writes.length=0; // Startup's independent indexing is outside this observation.
  await section(page,'schedules');
  await expect(page.locator('[data-schedule-kind]')).toHaveCount(6);
  await expect(page.locator('#scheduleSettings')).toContainText('任务服务未运行'); // Test server has no workers.
  await expect(page.locator('[data-schedule-report]')).toHaveCount(0);
  await expect(page.locator('#scheduleSettings')).not.toContainText('最近执行');
  await expect(page.locator('[data-schedule-storage]')).toBeAttached();
  expect(writes.filter(url=>/reports|reindex|settings|mark-read/.test(url))).toEqual([]);
});

test('手动清理更新大小，保留任务占用快照和设置草稿，取消不写入',async({page,request})=>{
  await request.post('/__test/reports',{data:{snapshots:true}});
  const before=await (await request.get('/api/reports/schedule-status')).json();
  const reportBefore=await (await request.get('/api/reports/report-evening')).json();
  await page.goto('/');await openSettings(page);await section(page,'schedules');
  await page.locator('[data-schedule-edit]').click();
  await choosePicker(page,'晚报小时','20 时');
  const clear=page.locator('[data-snapshot-clear]');
  await expect(clear).toBeEnabled();
  await expect(page.locator('[data-snapshot-protected]')).toContainText('任务使用中');
  await clear.click();
  await expect(page.locator('#appConfirmDialog')).toBeVisible();
  await page.locator('#appConfirmCancel').click();
  expect((await (await request.get('/api/reports/schedule-status')).json()).snapshot_storage).toEqual(before.snapshot_storage);
  await clear.click();
  await page.locator('#appConfirmAccept').click();
  await expect(page.locator('[data-snapshot-result]')).toContainText('已清除');
  await expect(clear).toBeDisabled(); // All remaining material is in use.
  await expect(page.getByRole('combobox',{name:'晚报小时',exact:true})).toHaveText('20 时');
  await expect(page.locator('#settingsSaveBtn')).toBeVisible();
  const after=await (await request.get('/api/reports/schedule-status')).json();
  expect(after.snapshot_storage.bytes).toBe(before.snapshot_storage.protected_bytes);
  expect(after.snapshot_storage.clearable_count).toBe(0);
  expect(after.recent).toHaveLength(before.recent.length);
  expect((await (await request.get('/api/reports/report-evening')).json()).report.content).toEqual(reportBefore.report.content);
  await page.reload();await openSettings(page);await section(page,'schedules');
  await expect(page.locator('[data-snapshot-size]')).toHaveText((after.snapshot_storage.bytes/1024).toFixed(1)+' KiB');
});

test('清理失败显示原因并保留大小，空快照禁用清理按钮',async({page,request})=>{
  const state=await observe(page);
  await page.route('**/api/reports/snapshots/clear',route=>route.fulfill({status:409,json:{ok:false,error:'report_materials_busy'}}));
  const clear=page.locator('[data-snapshot-clear]');
  await clear.click();await page.locator('#appConfirmAccept').click();
  await expect(page.locator('[data-snapshot-error]')).toHaveText('索引正在更新，请稍后清理。');
  await expect(page.locator('[data-snapshot-size]')).toHaveText('2.0 KiB');
  await expect(clear).toBeEnabled();
  state.data.snapshot_storage={bytes:0,clearable_bytes:0,protected_bytes:0,count:0,clearable_count:0};
  await page.locator('[data-schedule-refresh]').click();
  await expect(clear).toBeDisabled();
  await expect(page.locator('[data-snapshot-size]')).toHaveText('0.0 KiB');
});

test('暂停立即生效且保留时间草稿，重载后仍暂停，恢复不会创建报告',async({page,request})=>{
  await page.goto('/');await openSettings(page);await section(page,'schedules');
  await page.locator('[data-schedule-edit]').click();
  await choosePicker(page,'晚报小时','20 时');
  const pause=page.locator('[data-schedule-pause]');
  await pause.click();
  await expect(pause).toHaveAccessibleName('恢复定时');
  await expect(page.locator('[data-schedule-paused]')).toBeVisible();
  await expect(page.getByRole('combobox',{name:'晚报小时',exact:true})).toHaveText('20 时');
  await expect(page.locator('#settingsSaveBtn')).toBeVisible();
  let data=await (await request.get('/api/reports/schedule-status')).json();
  expect(data.schedule_control.paused).toBe(true);
  expect(data.schedules.filter(row=>row.kind!=='snapshot_cleanup').every(row=>row.paused && row.next_run_at===null)).toBe(true);
  expect(data.schedules[0].kind).toBe('snapshot_cleanup');
  await page.locator('#settingsResetBtn').click();
  await expect(pause).toHaveAccessibleName('恢复定时'); // Draft reset is not a resume request.
  await page.reload();await openSettings(page);await section(page,'schedules');
  await expect(pause).toHaveAccessibleName('恢复定时');
  await pause.click();
  await expect(pause).toHaveAccessibleName('暂停定时');
  await expect(page.locator('[data-schedule-paused]')).toBeHidden();
  await expect(page.locator('[data-schedule-kind="evening"] [data-schedule-next]')).not.toHaveText('已暂停');
  data=await (await request.get('/api/reports/schedule-status')).json();
  expect(data.schedule_control.paused).toBe(false);
  expect(data.recent).toHaveLength(5);
});

test('暂停请求防重复，失败后恢复按钮并显示错误',async({page})=>{
  await observe(page);
  let release, calls=0;
  const held=new Promise(resolve=>release=resolve);
  await page.route('**/api/reports/schedule-pause',async route=>{
    calls++;await held;
    await route.fulfill({status:503,json:{ok:false,error:'report_pause_unavailable'}});
  });
  const pause=page.locator('[data-schedule-pause]');
  await pause.click();
  await expect(pause).toBeDisabled();
  await expect(pause).toHaveAccessibleName('正在更改定时状态');
  await page.clock.runFor(10100);
  expect(calls).toBe(1);
  release();
  await expect(pause).toBeEnabled();
  await expect(pause).toHaveAccessibleName('暂停定时');
  await expect(page.locator('[data-schedule-pause-error]')).toContainText('未能确认');
  await expect(page.locator('[data-schedule-paused]')).toBeHidden();
});

for (const width of [320,390,1440]) test('暂停排期在各主题下保持清晰且不溢出 '+width,async({page})=>{
  await page.setViewportSize({width,height:1000});
  const state=await observe(page);
  state.data.schedule_control={paused:true,paused_at:'2026-10-30T20:00:00+08:00'};
  for(const row of state.data.schedules) if(row.kind!=='snapshot_cleanup') {row.paused=true;row.next_run_at=null;}
  await page.locator('[data-schedule-refresh]').click();
  await expect(page.locator('[data-schedule-pause]')).toHaveAccessibleName('恢复定时');
  await expect(page.locator('[data-schedule-kind="evening"] [data-schedule-next]')).toHaveText('已暂停');
  await expect(page.locator('[data-schedule-kind="snapshot_cleanup"] [data-schedule-next]')).toContainText('11/06');
  for(const [theme,colorScheme] of [['light','light'],['dark','dark'],['system','light'],['system','dark'],['eink','light']]) {
    await page.emulateMedia({colorScheme});await chooseTheme(page,theme);
    await expectControlColor(page,'success');
    await page.locator('[data-schedule-rules]').scrollIntoViewIfNeeded();
    expect(await page.locator('#scheduleSettings').evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
    await expect(page.locator('[data-schedule-pause]')).toBeInViewport();
    await page.screenshot({path:`output/report-storage-check/paused-${width}-${theme}-${colorScheme}.png`});
  }
});

test('排期按下次时间从近到远排列，无日期的任务在最后',async({page})=>{
  const state = await observe(page);
  const kinds = () => page.locator('[data-schedule-kind]').evaluateAll(nodes => nodes.map(node=>node.dataset.scheduleKind));
  expect(await kinds()).toEqual(['evening','monthly','morning','weekly','noon','snapshot_cleanup']);
  state.data.schedules.find(row=>row.kind==='morning').next_run_at=null;
  await page.locator('[data-schedule-refresh]').click();
  await expect.poll(kinds).toEqual(['evening','monthly','weekly','noon','snapshot_cleanup','morning']);
});

test('排期默认文本、编辑可取消且不影响其他设置草稿，清理失败可见',async({page})=>{
  const state = await observe(page);
  await expect(page.locator('#settingsDetailTitle')).toHaveText('日报任务');
  await expect(page.locator('#scheduleRulesTitle')).toHaveText('报告排期');
  await expect(page.locator('[data-schedule-rules] [role="combobox"]:visible')).toHaveCount(0);
  await expect(page.locator('[data-schedule-kind="evening"] [data-schedule-rule]')).toHaveText('晚报 · 每天 21:30');
  const cleanup = page.locator('[data-schedule-kind="snapshot_cleanup"]');
  await expect(cleanup).toContainText('每 7 天');
  await expect(cleanup).toContainText('11/06 10:00');
  await section(page,'models');
  await choosePicker(page,'Runtime','Codex CLI');
  await openModelAdvanced(page);
  await page.locator('input[name="agent.codex.model"]').fill('retain-this-model-draft');
  await section(page,'schedules');
  await page.locator('[data-schedule-edit]').click();
  await expect(page.locator('[data-schedule-rules] [role="combobox"]:visible')).toHaveCount(33);
  await expect(cleanup.getByRole('combobox')).toHaveCount(0);
  await choosePicker(page,'晚报小时','20 时');
  await page.locator('[data-schedule-edit]').click();
  await expect(page.locator('[data-schedule-kind="evening"] [data-schedule-rule]')).toHaveText('晚报 · 每天 21:30');
  await expect(page.locator('[data-schedule-rules] [role="combobox"]:visible')).toHaveCount(0);
  await section(page,'models');
  await expect(page.locator('input[name="agent.codex.model"]')).toHaveValue('retain-this-model-draft');
  await expect(page.locator('#settingsSaveBtn')).toBeVisible();
  await section(page,'schedules');
  const row=state.data.schedules.find(row=>row.kind==='snapshot_cleanup');
  row.error='report_snapshot_cleanup_failed';row.last_run_at='2026-10-30T10:00:00+08:00';row.removed_count=3;
  await page.locator('[data-schedule-refresh]').click();
  await expect(cleanup).toContainText('3 份材料');
  await expect(cleanup.getByRole('alert')).toContainText('5 分钟后自动重试');
});

test('时间 Picker 草稿和焦点在刷新中保留，切换分类与撤销沿用设置流程',async({page})=>{
  await observe(page);
  await page.locator('[data-schedule-edit]').click();
  await choosePicker(page,'晚报小时','20 时');
  await choosePicker(page,'晚报分钟','15 分');
  await expect(page.locator('#settingsSaveBtn')).toBeVisible();
  const picker = page.getByRole('combobox',{name:'晚报分钟',exact:true});
  await picker.click();
  await page.clock.runFor(10100);
  await expect(picker).toBeFocused();
  await expect(picker).toHaveAttribute('aria-expanded','true');
  await expect(page.getByRole('option',{name:'15 分',exact:true})).toHaveAttribute('aria-selected','true');
  await page.keyboard.press('Escape');
  await section(page,'models');await section(page,'schedules');
  await expect(page.getByRole('combobox',{name:'晚报小时',exact:true})).toHaveText('20 时');
  await expect(picker).toHaveText('15 分');
  await page.locator('#settingsResetBtn').click();
  await expect(page.getByRole('combobox',{name:'晚报小时',exact:true})).toBeHidden();
  await expect(page.locator('[data-schedule-kind="evening"] [data-schedule-rule]')).toHaveText('晚报 · 每天 21:30');
  await expect(page.locator('#settingsSaveBtn')).toBeHidden();
});

test('保存时间后立即更新下次排期，重载保留且非法顺序不能保存',async({page,request})=>{
  const original = await (await request.get('/api/settings')).json();
  try {
    await page.goto('/');await openSettings(page);await section(page,'schedules');
    await page.locator('[data-schedule-edit]').click();
    await choosePicker(page,'晚报小时','22 时');
    await choosePicker(page,'晚报分钟','15 分');
    await choosePicker(page,'周报小时','07 时');
    await page.locator('#settingsSaveBtn').click();
    await expect(page.locator('#settingsStatus')).toHaveText('设置已保存');
    await expect(page.locator('[data-schedule-kind="evening"] [data-schedule-next]')).toContainText('22:15');
    expect((await (await request.get('/api/settings')).json()).report_schedule.evening).toBe('22:15');
    await expect(page.getByRole('combobox',{name:'晚报小时',exact:true})).toBeHidden();
    await page.reload();await openSettings(page);await section(page,'schedules');
    await page.locator('[data-schedule-edit]').click();
    await expect(page.getByRole('combobox',{name:'晚报小时',exact:true})).toHaveText('22 时');
    await expect(page.getByRole('combobox',{name:'晚报分钟',exact:true})).toHaveText('15 分');
    await choosePicker(page,'午报小时','05 时');
    await page.locator('#settingsSaveBtn').click();
    await expect(page.locator('#settingsStatus')).toHaveText('早报时间须早于午报，午报时间须早于晚报');
    expect((await (await request.get('/api/settings')).json()).report_schedule.noon).toBe('14:30');
  } finally {
    const restored = await request.put('/api/settings',{data:{llm:original.llm,report_schedule:original.report_schedule}});
    expect(restored.ok()).toBe(true);
  }
});

test('改排期不改变手动新闻窗口，提交也不再携带排期时刻',async({page,request})=>{
  const original = await (await request.get('/api/settings')).json();
  try {
    const changed = await request.put('/api/settings',{data:{llm:original.llm,report_schedule:{morning:'09:15',noon:'15:10',evening:'22:15'}}});
    expect(changed.ok()).toBe(true);
    await page.goto('/');await openNavigation(page);await page.locator('#navReportsBtn').click();
    await page.locator('#createReportBtn').click();await choosePicker(page,'报告类型','早报');
    await page.locator('#reportSelection').fill('2026-10-28');
    await expect(page.locator('#reportCreationPeriod')).toContainText('10/27 21:30 — 10/28 08:30');
    await choosePicker(page,'报告类型','午报');await page.locator('#reportSelection').fill('2026-10-28');
    await expect(page.locator('#reportCreationPeriod')).toContainText('10/28 08:30 — 10/28 14:30');
    await choosePicker(page,'报告类型','晚报');await page.locator('#reportSelection').fill('2026-10-28');
    await expect(page.locator('#reportCreationPeriod')).toContainText('10/27 21:30 — 10/28 21:30');
    // Another settings tab may change timing while this form remains open.
    const restored = await request.put('/api/settings',{data:{llm:original.llm,report_schedule:original.report_schedule}});
    expect(restored.ok()).toBe(true);
    const response = page.waitForResponse(res=>res.url().endsWith('/api/reports/manual') && res.request().method()==='POST');
    await page.getByRole('button',{name:'开始生成',exact:true}).click();
    const created = await response;
    expect(created.request().postDataJSON().schedule_times).toBeUndefined();
    const report = (await created.json()).report;
    expect(report.period_start).toBe('2026-10-27T21:30:00+08:00');
    expect(report.period_end).toBe('2026-10-28T21:30:00+08:00');
  } finally {await request.put('/api/settings',{data:{llm:original.llm,report_schedule:original.report_schedule}});}
});

test('排期独立展示固定窗口，早于窗口结束的时间不能保存',async({page,request})=>{
  const saved = await (await request.get('/api/settings')).json();
  await page.goto('/');await openSettings(page);await section(page,'schedules');
  const evening = page.locator('[data-schedule-kind="evening"]');
  await expect(evening.locator('[data-schedule-window]')).toHaveText('新闻窗口：前日 21:30—当日 21:30');
  await expect(page.locator('[data-schedule-kind="noon"] [data-schedule-window]')).toContainText('08:30—14:30');
  await page.locator('[data-schedule-edit]').click();
  await expect(page.locator('[data-schedule-edit-note]')).toContainText('开始生成与新闻窗口独立设置');
  await choosePicker(page,'晚报小时','21 时');await choosePicker(page,'晚报分钟','29 分');
  await page.locator('#settingsSaveBtn').click();
  await expect(page.locator('#settingsStatus')).toHaveText('晚报生成时间不能早于新闻窗口结束（21:30）');
  expect((await (await request.get('/api/settings')).json()).report_schedule).toEqual(saved.report_schedule);
  await expect(evening.locator('[data-schedule-window]')).toHaveText('新闻窗口：前日 21:30—当日 21:30');
});

test('新闻窗口可独立编辑、撤销和保存，手动表单绑定所见范围',async({page,request})=>{
  const original = await (await request.get('/api/settings')).json();
  try {
    await page.goto('/');await openSettings(page);await section(page,'schedules');
    await page.locator('[data-schedule-edit]').click();
    await choosePicker(page,'晚报新闻起点日期','当日');
    await choosePicker(page,'晚报新闻起点小时','00 时');
    await choosePicker(page,'晚报新闻起点分钟','00 分');
    await choosePicker(page,'晚报新闻终点分钟','00 分');
    const window = page.locator('[data-schedule-kind="evening"] [data-schedule-window]');
    await expect(window).toHaveText('新闻窗口：当日 00:00—21:00');
    await page.locator('[data-schedule-refresh]').click();
    await expect(window).toHaveText('新闻窗口：当日 00:00—21:00');
    await section(page,'models');await section(page,'schedules');
    await expect(page.getByRole('combobox',{name:'晚报新闻起点小时',exact:true})).toHaveText('00 时');
    // Cancel discards both launch and window drafts without saving them.
    await page.locator('[data-schedule-edit]').click();
    await expect(window).toHaveText('新闻窗口：前日 21:30—当日 21:30');
    await page.locator('[data-schedule-edit]').click();
    await choosePicker(page,'晚报新闻起点日期','当日');
    await choosePicker(page,'晚报新闻起点小时','00 时');
    await choosePicker(page,'晚报新闻起点分钟','00 分');
    await choosePicker(page,'晚报新闻终点分钟','00 分');
    await choosePicker(page,'周报新闻终点小时','02 时');
    await choosePicker(page,'月报新闻起点小时','01 时');
    await choosePicker(page,'月报新闻终点小时','04 时');
    await page.locator('#settingsSaveBtn').click();
    await expect(page.locator('#settingsStatus')).toHaveText('设置已保存');
    const saved = await (await request.get('/api/settings')).json();
    expect(saved.report_schedule).toEqual(original.report_schedule);
    expect(saved.report_windows.evening).toEqual({start_day:0,start:'00:00',end:'21:00'});
    expect(saved.report_windows.weekly.end).toBe('02:00');
    await page.reload();await openSettings(page);await section(page,'schedules');
    await expect(window).toHaveText('新闻窗口：当日 00:00—21:00');
    await page.locator('#closeSettingsBtn').click();await openNavigation(page);await page.locator('#navReportsBtn').click();
    await page.locator('#createReportBtn').click();await choosePicker(page,'报告类型','周报');
    await page.locator('#reportSelection').fill('2026-10-19');
    await expect(page.locator('#reportCreationPeriod')).toContainText('10/19 00:00 — 10/26 02:00');
    await choosePicker(page,'报告类型','月报');await page.locator('#reportSelection').fill('2026-09');
    await expect(page.locator('#reportCreationPeriod')).toContainText('09/01 01:00 — 10/01 04:00');
    await choosePicker(page,'报告类型','晚报');await page.locator('#reportSelection').fill('2026-10-28');
    await expect(page.locator('#reportCreationPeriod')).toContainText('10/28 00:00 — 10/28 21:00');
    // Changing settings in another tab must not change this form's window.
    const updated = await request.put('/api/settings',{data:{llm:original.llm,report_windows:{evening:{end:'20:00'}}}});
    expect(updated.ok()).toBe(true);
    const pending = page.waitForResponse(res=>res.url().endsWith('/api/reports/manual') && res.request().method()==='POST');
    await page.getByRole('button',{name:'开始生成',exact:true}).click();
    const response = await pending;
    expect(response.ok()).toBe(true);
    expect(response.request().postDataJSON().window).toEqual(saved.report_windows.evening);
    const report = (await response.json()).report;
    expect(report.period_start).toBe('2026-10-28T00:00:00+08:00');
    expect(report.period_end).toBe('2026-10-28T21:00:00+08:00');
  } finally {
    const restored = await request.put('/api/settings',{data:{llm:original.llm,report_schedule:original.report_schedule,report_windows:original.report_windows}});
    expect(restored.ok()).toBe(true);
  }
});

test('同日窗口起止顺序与生成截止校验，窗口 Picker 刷新保留焦点',async({page,request})=>{
  const original = await (await request.get('/api/settings')).json();
  await observe(page);await page.locator('[data-schedule-edit]').click();
  await choosePicker(page,'午报新闻起点小时','15 时');
  await page.locator('#settingsSaveBtn').click();
  await expect(page.locator('#settingsStatus')).toHaveText('午报新闻起点必须早于终点');
  await choosePicker(page,'午报新闻起点小时','08 时');
  await choosePicker(page,'周报新闻终点小时','09 时');
  await page.locator('#settingsSaveBtn').click();
  await expect(page.locator('#settingsStatus')).toHaveText('周报生成时间不能早于新闻窗口结束（09:00）');
  const picker = page.getByRole('combobox',{name:'周报新闻终点分钟',exact:true});
  await picker.click();await page.clock.runFor(10100);
  await expect(picker).toBeFocused();await expect(picker).toHaveAttribute('aria-expanded','true');
  expect((await (await request.get('/api/settings')).json()).report_windows).toEqual(original.report_windows);
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

for(const width of [320,390,921,1440]) test('排期与快照管理沿用设置布局 '+width,async({page})=>{
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.setViewportSize({width,height:1000});
  const state=await observe(page);
  await expect(page.locator('[data-schedule-count]')).toHaveText(['1','2','1']);
  await expect(page.locator('#scheduleSettings')).toContainText('正在整理材料、生成报告');
  await page.locator('[data-schedule-rules]').scrollIntoViewIfNeeded();
  for(const [theme,colorScheme] of [['light','light'],['dark','dark'],['system','light'],['system','dark'],['eink','light']]) {
    await page.emulateMedia({colorScheme});await chooseTheme(page,theme);
    await expectControlColor(page,'danger');
    expect(await page.locator('.settings-main').evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
    expect(await page.locator('#scheduleSettings').evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
    await expect(page.getByRole('combobox',{name:'晚报小时',exact:true})).toBeHidden();
    await page.screenshot({path:`output/report-storage-check/schedules-${width}-${theme}-${colorScheme}.png`});
    await page.locator('[data-schedule-edit]').click();
    await page.locator('[data-snapshot-clear]').scrollIntoViewIfNeeded();
    await expect(page.locator('[data-snapshot-clear]')).toBeInViewport();
    await page.screenshot({path:`output/report-storage-check/storage-${width}-${theme}-${colorScheme}.png`});
    const hour=await page.getByRole('combobox',{name:'晚报小时',exact:true}).boundingBox();
    const minute=await page.getByRole('combobox',{name:'晚报分钟',exact:true}).boundingBox();
    expect(hour.y).toBe(minute.y);
    await page.screenshot({path:`output/report-tasks-check/editing-${width}-${theme}-${colorScheme}.png`});
    await page.locator('[data-schedule-edit]').click();
  }
  await chooseTheme(page,'light');
  await page.screenshot({path:`output/report-optimization-check/schedules-${width}.png`});
  if(width<=920) {
    await page.locator('#settingsCategoryBackBtn').click();
    const count=state.requests;await page.clock.runFor(30100);expect(state.requests).toBe(count);
    await expect(page.locator('.settings-category-card:visible')).toHaveCount(9);
    await section(page,'schedules');
  }
  await page.locator('[data-snapshot-clear]').scrollIntoViewIfNeeded();
  await expect(page.locator('[data-snapshot-size]')).toHaveText('2.0 KiB');
  expect(errors).toEqual([]);
});
