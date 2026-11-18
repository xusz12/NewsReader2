import {test,expect} from '@playwright/test';
import {chooseTheme,withClassificationBatch} from './helpers.js';

const indicator=page=>page.locator('#classificationProgressIndicator');
const meter=page=>page.locator('#taskProgressSummary');
const panel=page=>page.locator('#classificationProgressPanel');
const topic=(id='test-acquisition')=>({id,name:'北辰科技收购远川数据',job:{kind:'initialize',status:'running',error:''},matching:{}});
const report=()=>({id:'report-evening',kind:'evening',kind_label:'晚报',title:'合成晚报',status:'running',error:'',progress:{stage:'writing'}});
const empty=()=>({database_token:'test',topics:{items:[],matching_configured:true,matching_error:''},reports:{items:[],total:0,counts:{pending:0,running:0,failed:0}}});

async function start(page,{classification=false,topics=[topic()],reports=[report()]}={}) {
  const state={data:empty(),requests:0,failed:false};
  state.data.topics.items=topics;state.data.reports.items=reports;state.data.reports.total=reports.length;
  state.data.reports.counts.running=reports.length;
  await page.clock.install();
  await page.route('**/api/news/classification',route=>route.fulfill({json:withClassificationBatch({ok:true,enabled:classification,
    configured:true,total:100,done:25,pending:70,running:5,failed:0})}));
  await page.route('**/api/tasks/progress',route=>{
    state.requests++;return route.fulfill(state.failed?{status:503,json:{ok:false,error:'task_progress_unavailable'}}:{json:state.data});
  });
  await page.goto('/');await expect(page.locator('#feedCardList .news-card').first()).toBeVisible();
  if(topics.length||reports.length||classification)await expect(indicator(page)).toBeVisible();
  return state;
}

test.beforeEach(async({request})=>{
  await request.post('/__test/topics');await request.post('/__test/reports');
});

test('分类关闭时主题和日报仍显示，阶段来自接口且完成后隐藏',async({page})=>{
  const state=await start(page,{reports:[{...report(),title:'市场速览'}]});
  await expect(indicator(page)).toHaveAttribute('aria-label','任务进度');
  await expect(indicator(page)).toHaveText('');
  await expect(indicator(page).locator('svg')).toBeVisible();
  await expect(panel(page)).toBeHidden();
  await expect(meter(page)).not.toHaveAttribute('aria-valuenow');
  await indicator(page).focus();
  await expect(page.locator('#taskProgressTitle')).toHaveText('任务进度（2 项）');
  await expect(panel(page).locator('[data-task-type="report"] strong')).toHaveText('晚报 · 市场速览');
  await expect(panel(page)).toContainText('正在整理历史脉络');
  await expect(panel(page)).toContainText('编写报告');
  await expect(page.locator('#taskProgressClassification')).toBeHidden();
  state.data.reports.items[0].progress={stage:'selecting',completed:2,total:5};
  state.data.topics.items[0]={...topic(),job:{kind:'milestone',status:'pending',error:''},matching:{pending:8,waiting_update:3}};
  await page.clock.runFor(2100);
  await expect(indicator(page)).toHaveText('');
  await expect(meter(page)).toHaveAttribute('aria-valuenow','2');
  await expect(meter(page)).toHaveAttribute('aria-valuemax','5');
  await expect(page.locator('#classificationProgressArc')).toHaveCSS('stroke-dashoffset','60px');
  await expect(panel(page)).toContainText('已完成 2 / 5 批');
  await expect(panel(page)).toContainText('待匹配 8 条');
  await expect(panel(page)).toContainText('待整理 3 条');
  state.data.reports.items[0].progress={stage:'reviewing',total:5};
  await page.clock.runFor(2100);
  await expect(meter(page)).not.toHaveAttribute('aria-valuenow');
  await expect(panel(page).locator('[data-task-type="report"] .task-progress-stage')).toHaveText('复核报告全文');
  state.data=empty();await page.clock.runFor(2100);
  await expect(page.locator('#classificationProgress')).toBeHidden();await expect(panel(page)).toBeHidden();
  // Background work is discovered even while another collection is open.
  await page.locator('#navFavoritesBtn').click();
  state.data.topics.items=[topic()];await page.clock.runFor(30100);
  await expect(indicator(page)).toHaveAttribute('aria-label','主题追踪进度');
  await expect(indicator(page)).toHaveText('');
  await expect(panel(page)).toBeHidden();
  await indicator(page).press('Enter');
  await expect(panel(page)).toContainText('整理历史脉络');
});

test('多任务不混算百分比，其他任务完成后恢复分类圆环',async({page})=>{
  const state=await start(page,{classification:true});
  await indicator(page).hover();
  await expect(page.locator('.task-progress-section:visible')).toHaveCount(3);
  await expect(meter(page)).not.toHaveAttribute('aria-valuenow');
  await expect(page.locator('#classificationProgressCount')).toHaveText('此次已分类 25/100 条');
  state.data=empty();await page.clock.runFor(2100);
  await expect(indicator(page)).toHaveAttribute('aria-label','新闻分类进度');
  await expect(meter(page)).toHaveAttribute('aria-valuenow','25');
  await expect(page.locator('#classificationProgressArc')).toHaveCSS('stroke-dashoffset','75px');
});

test('失败和自动重试保留原因，断线显示上次结果且可以恢复',async({page})=>{
  const state=await start(page,{topics:[{...topic(),job:{kind:'initialize',status:'failed',error:'topic_search_required'}}],
    reports:[{...report(),status:'pending',error:'report_index_permission_denied',next_retry_at:'2026-10-31T15:00:00+08:00'}]});
  state.data.reports.counts={pending:1,running:0,failed:0};
  await indicator(page).hover();
  await expect(panel(page)).toContainText('未完成联网检索');
  await expect(panel(page)).toContainText('重试将直接使用当前新闻库');
  await expect(panel(page)).toContainText('10/31 15:00 重试');
  state.failed=true;await page.clock.runFor(2100);
  await expect(panel(page)).toContainText('显示上次结果');
  await expect(indicator(page).locator('.task-progress-alert')).toBeVisible();
  await expect(indicator(page)).toHaveText('');
  await expect(page.locator('[data-task-id="report-evening"]')).toBeVisible();
  await page.getByRole('button',{name:'清理异常'}).click();
  await expect(page.locator('#taskProgressErrorText')).not.toHaveClass(/error-state/);
  await expect(panel(page)).toContainText('显示上次进度');
  state.failed=false;state.data=empty();await page.locator('#taskProgressRefresh').click();
  await expect(page.locator('#classificationProgress')).toBeHidden();
});

test('初次状态读取失败保持可见并提供重新读取，不伪造空队列',async({page})=>{
  await page.route('**/api/tasks/progress',route=>route.fulfill({status:503,json:{ok:false}}));
  await page.route('**/api/news/classification',route=>route.fulfill({json:{enabled:false,batch:{}}}));
  await page.goto('/');await expect(indicator(page)).toBeVisible();await indicator(page).focus();
  await expect(panel(page)).toContainText('暂时无法读取主题追踪与日报进度');
  await expect(page.locator('#taskProgressRefresh')).toBeVisible();
  await expect(meter(page)).not.toHaveAttribute('aria-valuenow');
});

test('轮询保留焦点、隐藏页面暂停，任务变更立即更新',async({page})=>{
  const state=await start(page);await indicator(page).focus();
  const button=page.locator('[data-task-id="report-evening"]');await button.focus();
  state.data.topics.items[0].job.error='topic_search_required';
  state.data.reports.items[0].progress={stage:'validating'};await page.clock.runFor(2100);
  await expect(button).toBeFocused();await expect(button).toContainText('校验内容与来源');
  await expect(panel(page)).not.toContainText('未完成联网检索');
  await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'));});
  await page.clock.runFor(100);const count=state.requests;await page.clock.runFor(10000);expect(state.requests).toBe(count);
  state.data=empty();
  await page.evaluate(()=>{delete document.hidden;document.dispatchEvent(new Event('visibilitychange'));});
  await expect(page.locator('#classificationProgress')).toBeHidden();
  state.data.topics.items=[topic()];
  await page.evaluate(()=>document.dispatchEvent(new Event('tasks:changed')));
  await expect(indicator(page)).toBeVisible();
});

for(const width of [390,1440]) for(const collecting of [false,true])
test(`任务完成后焦点返回可用顶栏入口 ${width} collecting=${collecting}`,async({page})=>{
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.setViewportSize({width,height:900});
  await page.route('**/api/collection',async route=>{
    const response=await route.fetch(),data=await response.json();
    await route.fulfill({json:{...data,running:collecting}});
  });
  const state=await start(page);await indicator(page).focus();
  await expect(page.locator('#upstreamErrorsBtn')).toBeEnabled();
  await page.locator('[data-task-id="report-evening"]').focus();
  state.data=empty();
  await page.evaluate(()=>document.dispatchEvent(new Event('tasks:changed')));
  await expect(page.locator('#classificationProgress')).toBeHidden();
  await expect(page.locator('#upstreamErrorsBtn')).toBeFocused();
  expect(errors).toEqual([]);
});

for(const width of [390,1440]) test('点击任务进入对应主题和报告，不会触发重新生成 '+width,async({page})=>{
  await page.setViewportSize({width,height:900});
  await start(page);await indicator(page).focus();
  const writes=[];page.on('request',request=>{if(request.method()!=='GET')writes.push(request.url());});
  await page.locator('[data-task-id="test-acquisition"]').click();
  await expect(page.locator('#detailPanelTopics')).toHaveClass(/active/);
  await expect(page.locator('#topicContent .article-title')).toHaveText('北辰科技收购远川数据');
  await expect(panel(page)).toBeHidden();
  await indicator(page).focus();await page.locator('[data-task-id="report-evening"]').click();
  await expect(page.locator('#reportContent h1')).toHaveText('合成晚报');
  expect(writes.filter(url=>/reports|topics/.test(url))).toEqual([]);
});

test('任务标题按纯文本呈现，旧响应不会覆盖任务变更后的状态',async({page})=>{
  const state=await start(page,{topics:[{...topic(),name:'<img src=x onerror="window.taskInjected=true">'}]});
  await indicator(page).focus();await expect(panel(page).locator('img')).toHaveCount(0);
  expect(await page.evaluate(()=>window.taskInjected)).toBeUndefined();
  let release;const gate=new Promise(resolve=>release=resolve);
  await page.route('**/api/tasks/progress',async route=>{await gate;await route.fulfill({json:state.data});});
  await page.evaluate(()=>document.dispatchEvent(new Event('tasks:changed')));
  await page.unroute('**/api/tasks/progress');
  await page.route('**/api/tasks/progress',route=>route.fulfill({json:empty()}));
  await page.evaluate(()=>document.dispatchEvent(new Event('tasks:changed')));release();
  await expect(page.locator('#classificationProgress')).toBeHidden();
});

for(const width of [320,390,921,1440]) test('三类任务卡片适配视口与主题 '+width,async({page})=>{
  await page.setViewportSize({width,height:width===320?568:900});
  const state=await start(page,{classification:true,topics:Array.from({length:5},(_,i)=>({...topic('t'+i),name:'主题 '+i}))});
  for(const [theme,scheme] of [['light','light'],['dark','dark'],['system','light'],['system','dark'],['eink','light']]) {
    await page.emulateMedia({colorScheme:scheme});await chooseTheme(page,theme);await indicator(page).focus();
    await expect(indicator(page)).toHaveText('');
    await expect(indicator(page).locator('svg')).toBeVisible();
    const bounds=await indicator(page).boundingBox();
    expect(bounds.width).toBe(bounds.height);
    const card=await panel(page).boundingBox();expect(card.x).toBeGreaterThanOrEqual(0);expect(card.x+card.width).toBeLessThanOrEqual(width);
    expect(card.y+card.height).toBeLessThanOrEqual(width===320?568:900);
    if(width<=920)expect(card.y+card.height).toBeLessThanOrEqual((await page.locator('.mobile-bottom-nav').boundingBox()).y);
    expect(await panel(page).evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
    await expect(page.locator('[data-task-id="report-evening"]')).toBeInViewport();
    await panel(page).evaluate(node=>{node.scrollTop=node.scrollHeight;});
    await expect(page.locator('[data-task-id="t4"]')).toBeInViewport();
    await expect(page.getByRole('button',{name:'关闭任务进度'})).toBeInViewport();
    await panel(page).evaluate(node=>{node.scrollTop=0;});
    await page.screenshot({path:`output/task-progress-check/progress-${width}-${theme}-${scheme}.png`});
    await page.keyboard.press('Escape');await expect(panel(page)).toBeHidden();
  }
  expect(state.requests).toBeGreaterThan(0);
});

test('手机单击固定详情，松手、轮询和阅读详情后仍可再次点击关闭',async({browser})=>{
  const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  const page=await context.newPage();
  try {
    const state=await start(page);await indicator(page).tap();await expect(panel(page)).toBeVisible();
    state.data.reports.items[0].progress={stage:'reviewing',completed:2,total:3};
    await page.clock.runFor(2500);
    await expect(panel(page).locator('[data-task-type="report"] .task-progress-stage')).toHaveText('复核报告全文 · 已完成 2 / 3 章');
    await expect(indicator(page)).toHaveText('');
    await expect(indicator(page).locator('svg')).toBeVisible();
    await expect(panel(page)).toBeVisible();
    state.data.topics.items.push({...topic('failed-topic'),job:{status:'failed',kind:'initialize',error:'topic_search_required'}});
    await page.clock.runFor(2100);
    const firstGroup=panel(page).locator('.task-progress-section:visible').first();
    await expect(firstGroup).toHaveAttribute('id','taskProgressReports');
    await panel(page).locator('#taskProgressTitle').tap();
    await page.clock.runFor(300);await expect(indicator(page)).toHaveAttribute('aria-expanded','true');
    await indicator(page).tap();await expect(panel(page)).toBeHidden();
    await indicator(page).tap();
    await expect(firstGroup).toHaveAttribute('id','taskProgressTopics');
    await expect(firstGroup.locator('[data-task-id]').first()).toHaveAttribute('data-task-id','failed-topic');
    await page.getByRole('button',{name:'关闭任务进度'}).tap();
    await expect(panel(page)).toBeHidden();
    await indicator(page).tap();
    await page.keyboard.press('Escape');await expect(panel(page)).toBeHidden();
    await indicator(page).tap();await page.locator('#openNavBtn').tap();
    await expect(panel(page)).toBeHidden();
  } finally {await context.close();}
});

test('圆环提示异常，展开详情可查看匹配队列、等待恢复及未列出的报告失败数量',async({page})=>{
  const state=await start(page,{topics:[{...topic(),job:null,matching:{pending:13}}],reports:[]});
  const alert=indicator(page).locator('.task-progress-alert');
  await expect(alert).toBeHidden();
  await indicator(page).press('Enter');
  await expect(panel(page)).toContainText('待匹配 13 条');
  state.data.topics.matching_waiting=true;
  await page.clock.runFor(2100);
  await expect(panel(page)).toContainText('等待恢复');
  await expect(indicator(page)).toHaveAttribute('data-status','warning');
  await expect(alert).toBeVisible();
  await page.keyboard.press('Escape');
  state.data.topics.items=[];
  state.data.reports={items:[report()],total:11,counts:{pending:0,running:10,failed:1}};
  await page.evaluate(()=>document.dispatchEvent(new Event('tasks:changed')));
  await expect(indicator(page)).toHaveAttribute('data-status','failed');
  await expect(indicator(page)).toHaveText('');
  await expect(panel(page)).toBeHidden();
  await indicator(page).press('Enter');
  await expect(panel(page).locator('#taskProgressReportCount')).toHaveText('生成中 10 · 失败 1');
  await expect(panel(page).locator('#taskProgressTitle')).toHaveText('任务进度（11 项）');
});

test('分类进度首次读取失败可以从入口发现并单独恢复',async({page})=>{
  let unavailable=true;
  await page.route('**/api/news/classification',route=>route.fulfill(unavailable
    ? {status:503,json:{ok:false}} : {json:{enabled:false,batch:{}}}));
  await page.route('**/api/tasks/progress',route=>route.fulfill({json:empty()}));
  await page.goto('/');
  await expect(indicator(page).locator('.task-progress-alert')).toBeVisible();
  await expect(indicator(page)).toHaveText('');
  await indicator(page).press('Enter');
  await expect(panel(page)).toContainText('暂时无法读取分类进度');
  await indicator(page).press('Space');
  await expect(panel(page)).toBeHidden();
  await indicator(page).press('Enter');
  unavailable=false;
  await page.getByRole('button',{name:'重新读取进度'}).click();
  await expect(indicator(page)).toBeHidden();
});

test('窄手机详情页同时显示返回、异常、通知和报告进度时各入口可点',async({page})=>{
  await page.setViewportSize({width:320,height:568});
  await page.addInitScript(()=>localStorage.setItem('newsreader_workbench_font_size','extra-large'));
  await page.route('**/api/agent/notifications',route=>route.fulfill({json:{db_scope:'test',
    notifications:[{id:'notice',session_id:'test',title:'已完成',preview:'已完成'}]}}));
  await page.route('**/api/error-stats?*',async route=>{
    const response=await route.fetch(),data=await response.json();
    await route.fulfill({response,json:{...data,unresolved_count:105}});
  });
  await start(page,{reports:[{...report(),progress:{stage:'planning'}}]});
  await page.locator('#feedCardList .news-card-title').first().click();
  await expect(page.locator('#mobileReaderBackBtn')).toBeVisible();
  await expect(page.locator('#upstreamErrorBadge')).toHaveText('99+');
  await expect(page.locator('#agentNotificationsBtn')).toBeVisible();
  await expect(indicator(page)).toHaveText('');
  await expect(indicator(page).locator('svg')).toBeVisible();
  await expect(indicator(page)).toHaveCSS('width','36px');
  const controls=page.locator('.topbar button:visible');
  expect(await controls.evaluateAll(buttons=>buttons.every(button=>{
    const {x,y,width,height}=button.getBoundingClientRect();
    return x>=0 && x+width<=innerWidth+1 && button.contains(document.elementFromPoint(x+width/2,y+height/2));
  }))).toBe(true);
  await indicator(page).click();
  await expect(panel(page)).toContainText('统筹主题与跨日进展');
});

test('手机清理的报告异常在另一浏览器同步，同一报告再次失败重新提示',async({browser,request})=>{
  await request.post('/__test/topics',{data:{empty:true}});
  await request.post('/__test/reports',{data:{failed:true}});
  const mobile=await browser.newContext({viewport:{width:320,height:568},hasTouch:true,isMobile:true});
  const desktop=await browser.newContext();
  try {
    const first=await mobile.newPage(), second=await desktop.newPage();
    await first.addInitScript(()=>localStorage.setItem('newsreader_workbench_font_size','extra-large'));
    for(const page of [first,second]){
      await page.route('**/api/news/classification',r=>r.fulfill({json:{enabled:false,batch:{}}}));
      await page.clock.install();await page.goto('/');
      await expect(indicator(page).locator('.task-progress-alert')).toBeVisible();
      await expect(indicator(page)).toHaveText('');
    }
    await indicator(first).tap();
    await expect(panel(first)).toContainText('生成失败');
    const clear=first.getByRole('button',{name:'清理异常',exact:true});
    await expect(clear).toBeInViewport();
    await clear.tap();
    await expect(indicator(first)).toBeHidden();
    await second.clock.runFor(30100);
    await expect(indicator(second)).toBeHidden();
    await first.reload();await expect(first.locator('#feedCardList .news-card').first()).toBeVisible();
    await expect(indicator(first)).toBeHidden();
    expect((await (await request.get('/api/reports/report-evening')).json()).report.status).toBe('failed');
    await request.post('/__test/reports',{data:{failed:true}});
    await first.evaluate(()=>document.dispatchEvent(new Event('tasks:changed')));
    await expect(indicator(first)).toBeVisible();
    await indicator(first).tap();await expect(clear).toBeVisible();
  } finally {await mobile.close();await desktop.close();}
});

test('离线可清理读取异常，刷新保留，恢复后再次断线重新出现',async({page})=>{
  let offline=true, polls=0, writes=0;
  await page.clock.install();
  await page.route('**/api/news/classification',r=>r.fulfill({json:{enabled:false,batch:{}}}));
  await page.route('**/api/tasks/progress',r=>{
    polls++;
    return r.fulfill(offline?{status:503,json:{ok:false}}:{json:empty()});
  });
  await page.route('**/api/tasks/progress/dismiss',r=>{writes++;return r.abort();});
  await page.goto('/');await indicator(page).press('Enter');
  await page.getByRole('button',{name:'清理异常'}).press('Enter');
  await expect(indicator(page)).toBeHidden();
  await expect(page.locator('#upstreamErrorsBtn')).toBeFocused();
  const before=polls;await page.reload();await page.clock.runFor(30100);
  expect(polls).toBeGreaterThan(before);await expect(indicator(page)).toBeHidden();
  offline=false;
  await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/tasks/progress') && r.ok()),
    page.evaluate(()=>document.dispatchEvent(new Event('tasks:changed')))]);
  await page.clock.runFor(30100);
  offline=true;await page.evaluate(()=>document.dispatchEvent(new Event('tasks:changed')));
  await expect(indicator(page).locator('.task-progress-alert')).toBeVisible();
  await indicator(page).press('Enter');
  await expect(panel(page).locator('#taskProgressErrorText')).toContainText('进度暂时无法更新');
  expect(writes).toBe(0);
});

test('清理服务告警后保留排队任务，服务恢复后再度暂停会重新提示',async({page})=>{
  const state=await start(page,{topics:[{...topic(),job:null,matching:{pending:13}}],reports:[]});
  state.data.topics.matching_waiting=true;
  await page.clock.runFor(2100);await indicator(page).click();
  await expect(indicator(page)).toHaveAttribute('data-status','warning');
  await page.getByRole('button',{name:'清理异常'}).click();
  await expect(indicator(page)).toHaveAttribute('data-status','pending');
  await expect(panel(page)).toContainText('待匹配 13 条');
  await expect(panel(page)).not.toContainText('等待判断服务恢复');
  await page.clock.runFor(2100);await expect(page.getByRole('button',{name:'清理异常'})).toBeHidden();
  state.data.topics.matching_waiting=false;state.data.topics.items[0].matching.pending=14;
  await page.evaluate(()=>document.dispatchEvent(new Event('tasks:changed')));
  await expect(panel(page)).toContainText('待匹配 14 条');
  state.data.topics.matching_waiting=true;await page.evaluate(()=>document.dispatchEvent(new Event('tasks:changed')));
  await expect(page.getByRole('button',{name:'清理异常'})).toBeVisible();
});

test('清理写入失败保留原异常，再点清理可以成功',async({page,request})=>{
  await request.post('/__test/topics',{data:{empty:true}});
  await request.post('/__test/reports',{data:{failed:true}});
  await page.route('**/api/news/classification',r=>r.fulfill({json:{enabled:false,batch:{}}}));
  await page.route('**/api/tasks/progress/dismiss',r=>r.fulfill({status:503,json:{ok:false}}),{times:1});
  await page.goto('/');
  const clear=page.getByRole('button',{name:'清理异常'});
  for(const [theme,scheme] of [['light','light'],['dark','dark'],['system','light'],['system','dark'],['eink','light']]){
    await page.emulateMedia({colorScheme:scheme});await chooseTheme(page,theme);await indicator(page).press('Enter');
    await expect(clear).toBeInViewport();await expect(page.getByRole('button',{name:'关闭任务进度'})).toBeInViewport();
    await page.keyboard.press('Escape');
  }
  await indicator(page).press('Enter');
  await clear.click();await expect(page.locator('#toast')).toHaveText('任务异常清理失败，请重试');
  await expect(panel(page)).toContainText('生成失败');await expect(clear).toBeVisible();
  await clear.click();await expect(indicator(page)).toBeHidden();
});

test('清理分类异常只确认所见批次，进行中的分类继续显示真实进度',async({page})=>{
  const progress={...withClassificationBatch({enabled:true,configured:true,done:3,failed:1,pending:2,running:0,total:6}),
    database_token:'test',unacknowledged_failed:1,notices:{classification:17}};
  progress.batch.id='batch-a';
  let confirmation;
  await page.route('**/api/tasks/progress',r=>r.fulfill({json:empty()}));
  await page.route('**/api/news/classification',r=>r.fulfill({json:progress}));
  await page.route('**/api/tasks/progress/dismiss',r=>{
    confirmation=r.request().postDataJSON();
    progress.unacknowledged_failed=0;progress.notices={};
    return r.fulfill({json:{ok:true}});
  });
  await page.goto('/');await indicator(page).press('Enter');
  await expect(indicator(page)).toHaveAttribute('data-status','failed');
  await page.getByRole('button',{name:'清理异常'}).click();
  await expect(indicator(page)).toHaveAttribute('data-status','running');
  await expect(meter(page)).toHaveAttribute('aria-valuenow','3');
  await expect(meter(page)).toHaveAttribute('aria-valuemax','6');
  expect(confirmation).toEqual({database_token:'test',notices:{classification:17},batch_id:'batch-a'});
  await expect(page.getByRole('button',{name:'清理异常'})).toBeHidden();
  await expect(page.getByRole('button',{name:'重试失败项'})).toBeVisible();
});
