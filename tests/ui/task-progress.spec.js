import {test,expect} from '@playwright/test';
import {chooseTheme,withClassificationBatch} from './helpers.js';

const indicator=page=>page.locator('#classificationProgressIndicator');
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
  const state=await start(page);
  await expect(indicator(page)).toHaveAttribute('aria-label','任务进度');
  await expect(indicator(page)).not.toHaveAttribute('aria-valuenow');
  await indicator(page).focus();
  await expect(panel(page)).toContainText('正在整理历史脉络');
  await expect(panel(page)).toContainText('编写报告');
  await expect(page.locator('#taskProgressClassification')).toBeHidden();
  state.data.reports.items[0].progress={stage:'selecting',completed:2,total:5};
  state.data.topics.items[0]={...topic(),job:{kind:'milestone',status:'pending',error:''},matching:{pending:8,waiting_update:3}};
  await page.clock.runFor(2100);
  await expect(panel(page)).toContainText('已完成 2 / 5 批');
  await expect(panel(page)).toContainText('待匹配 8 条');
  await expect(panel(page)).toContainText('待整理 3 条');
  state.data=empty();await page.clock.runFor(2100);
  await expect(page.locator('#classificationProgress')).toBeHidden();await expect(panel(page)).toBeHidden();
  // Background work is discovered even while another collection is open.
  await page.locator('#navFavoritesBtn').click();
  state.data.topics.items=[topic()];await page.clock.runFor(30100);
  await expect(indicator(page)).toHaveAttribute('aria-label','主题追踪进度');
});

test('多任务不混算百分比，其他任务完成后恢复分类圆环',async({page})=>{
  const state=await start(page,{classification:true});
  await indicator(page).hover();
  await expect(page.locator('.task-progress-section:visible')).toHaveCount(3);
  await expect(indicator(page)).not.toHaveAttribute('aria-valuenow');
  await expect(page.locator('#classificationProgressCount')).toHaveText('此次已分类 25/100 条');
  state.data=empty();await page.clock.runFor(2100);
  await expect(indicator(page)).toHaveAttribute('aria-label','新闻分类进度');
  await expect(indicator(page)).toHaveAttribute('aria-valuenow','25');
  await expect(page.locator('#classificationProgressArc')).toHaveCSS('stroke-dashoffset','75px');
});

test('失败和自动重试保留原因，断线显示上次结果且可以恢复',async({page})=>{
  const state=await start(page,{topics:[{...topic(),job:{kind:'initialize',status:'failed',error:'topic_search_required'}}],
    reports:[{...report(),status:'pending',error:'report_index_permission_denied',next_retry_at:'2026-10-31T15:00:00+08:00'}]});
  state.data.reports.counts={pending:1,running:0,failed:0};
  await indicator(page).hover();
  await expect(panel(page)).toContainText('未完成联网检索');
  await expect(panel(page)).toContainText('没有权限读取新闻目录或文件');
  await expect(panel(page)).toContainText('10/31 15:00 重试');
  state.failed=true;await page.clock.runFor(2100);
  await expect(panel(page)).toContainText('显示上次结果');
  await expect(page.locator('[data-task-id="report-evening"]')).toBeVisible();
  state.failed=false;state.data=empty();await page.locator('#taskProgressRefresh').click();
  await expect(page.locator('#classificationProgress')).toBeHidden();
});

test('初次状态读取失败保持可见并提供重新读取，不伪造空队列',async({page})=>{
  await page.route('**/api/tasks/progress',route=>route.fulfill({status:503,json:{ok:false}}));
  await page.route('**/api/news/classification',route=>route.fulfill({json:{enabled:false,batch:{}}}));
  await page.goto('/');await expect(indicator(page)).toBeVisible();await indicator(page).focus();
  await expect(panel(page)).toContainText('暂时无法读取主题追踪与日报进度');
  await expect(page.locator('#taskProgressRefresh')).toBeVisible();
  await expect(indicator(page)).not.toHaveAttribute('aria-valuenow');
});

test('轮询保留焦点、隐藏页面暂停，任务变更立即更新',async({page})=>{
  const state=await start(page);await indicator(page).focus();
  const button=page.locator('[data-task-id="report-evening"]');await button.focus();
  state.data.reports.items[0].progress={stage:'validating'};await page.clock.runFor(2100);
  await expect(button).toBeFocused();await expect(button).toContainText('校验内容与来源');
  await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'));});
  await page.clock.runFor(100);const count=state.requests;await page.clock.runFor(10000);expect(state.requests).toBe(count);
  state.data=empty();
  await page.evaluate(()=>{delete document.hidden;document.dispatchEvent(new Event('visibilitychange'));});
  await expect(page.locator('#classificationProgress')).toBeHidden();
  state.data.topics.items=[topic()];
  await page.evaluate(()=>document.dispatchEvent(new Event('tasks:changed')));
  await expect(indicator(page)).toBeVisible();
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
    const card=await panel(page).boundingBox();expect(card.x).toBeGreaterThanOrEqual(0);expect(card.x+card.width).toBeLessThanOrEqual(width);
    expect(card.y+card.height).toBeLessThanOrEqual(width===320?568:900);
    if(width<=920)expect(card.y+card.height).toBeLessThanOrEqual((await page.locator('.mobile-bottom-nav').boundingBox()).y);
    expect(await panel(page).evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
    await panel(page).evaluate(node=>{node.scrollTop=node.scrollHeight;});
    await expect(page.locator('[data-task-id="report-evening"]')).toBeInViewport();
    await panel(page).evaluate(node=>{node.scrollTop=0;});
    await page.screenshot({path:`output/task-progress-check/progress-${width}-${theme}-${scheme}.png`});
    await page.keyboard.press('Escape');await expect(panel(page)).toBeHidden();
  }
  expect(state.requests).toBeGreaterThan(0);
});

test('手机轻触查看任务，Esc 与通知浮层保持互斥',async({browser})=>{
  const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  const page=await context.newPage();
  try {
    await start(page);await indicator(page).tap();await expect(panel(page)).toBeVisible();
    await page.keyboard.press('Escape');await expect(panel(page)).toBeHidden();
    await indicator(page).tap();await page.locator('#feedCardList .news-card').first().tap();
    await expect(panel(page)).toBeHidden();
  } finally {await context.close();}
});
