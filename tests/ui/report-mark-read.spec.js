import {test,expect} from '@playwright/test';
import {openNavigation} from './helpers.js';

const detail = async (request,id) => (await (await request.get(`/api/news/${id}/detail`)).json());
const nav = async request => (await (await request.get('/api/nav-summary')).json()).summary;
async function openReports(page) {
  await openNavigation(page);
  await page.locator('#navReportsBtn').click();
  await page.locator('[data-report-id="report-evening"]').click();
  await expect(page.locator('#reportProse')).toBeVisible();
}
// Exercise a source outside the loaded feed page, a briefs-only citation and
// unused material without changing the isolated server's standard reports.
async function extraSources(page,request,reportId='report-evening',onlyExtra=false) {
  await page.route(`**/api/reports/${reportId}`, async route => {
    const response=await route.fetch(), data=await response.json(), report=data.report;
    const items = (await (await request.get('/api/news?per=100&read_filter=all')).json()).items;
    for (const [ref,id] of [['N3','ui-064'],['unused','ui-063']]) {
      const item=items.find(item=>item.id===id);
      report.content.sources.push({...item,ref});
      report.source_items[ref]=item;
    }
    report.content.briefs[0].refs=['N3'];
    if (onlyExtra) {
      for (const section of report.content.sections) for (const paragraph of section.paragraphs) paragraph.refs=['N3'];
      report.content.briefs.forEach(brief=>brief.refs=['N3']);
    }
    await route.fulfill({response,json:data});
  });
}

test.beforeEach(async({request})=>{
  await request.post('/__test/reset-reading');
  await request.post('/__test/reports',{data:{linked_sources:true,briefs:true}});
});

for (const width of [390,1440]) test('主动已阅读只标记正文和简讯来源，保留全文状态并同步计数 '+width,async({page,request})=>{
  await page.setViewportSize({width,height:900});
  await request.patch('/api/news/ui-000/state',{data:{read_later:true}});
  await request.patch('/api/news/ui-001/state',{data:{read:true,read_later:false,read_later_done:true}});
  await request.post('/__test/reset-reading',{data:{item_ids:['ui-000']}});
  await extraSources(page,request);
  const before=await Promise.all(['ui-000','ui-001','ui-064'].map(id=>detail(request,id)));
  const counts=await nav(request), writes=[];
  page.on('request',request=>{if(request.url().endsWith('/api/news/mark-read-by-ids')) writes.push(request.postDataJSON().item_ids);});
  await page.goto('/'); await openReports(page);
  const button=page.locator('#markReportReadBtn');
  await expect(button).toBeEnabled();
  await expect(page.locator('#reportContent [data-report-read-badge]')).toBeHidden();
  await expect(page.locator('.report-sources[open]')).toHaveCount(0);
  expect((await detail(request,'ui-000')).read_at).toBeNull();
  expect(writes).toEqual([]);
  await button.focus(); await page.keyboard.press('Enter');
  await expect(button).toHaveAttribute('aria-busy','false');
  await expect(button).toBeDisabled();
  await expect(button).toHaveClass(/is-complete/);
  await expect(button).toHaveText('已完成');
  await expect(button).toHaveAttribute('title','已经阅读完');
  await expect(page.locator('#toast')).toContainText('已经阅读完');
  await expect(page.locator('#reportContent [data-report-read-badge]')).toBeVisible();
  await expect(page.locator('[data-report-id="report-evening"] [data-report-read-badge]')).not.toHaveAttribute('hidden');
  await expect(page.locator('[data-report-id="report-noon"] [data-report-read-badge]')).toBeHidden();
  expect(writes).toHaveLength(1);
  expect(writes[0].sort()).toEqual(['ui-000','ui-064']);
  const after=await Promise.all(['ui-000','ui-001','ui-064'].map(id=>detail(request,id)));
  for (let i=0;i<after.length;i++) {
    expect(after[i].read_at).toBeTruthy();
    for (const field of ['read_later_at','read_later_done_at','important_at','favorite_at']) expect(after[i][field]).toBe(before[i][field]);
  }
  expect(after[1].read_at).toBe(before[1].read_at);
  for (const id of ['ui-002','ui-063']) expect((await detail(request,id)).read_at).toBeNull();
  expect((await nav(request)).feed_unread).toBe(counts.feed_unread-2);
  expect((await nav(request)).read_later_unread).toBe(counts.read_later_unread);
  await expect(page.locator('#mobUnreadBadge')).toHaveText(String(counts.feed_unread-2));
  await expect(page.locator('.report-sources[open]')).toHaveCount(0);
  await page.reload(); await openReports(page);
  await expect(button).toBeDisabled();
  await expect(button).toHaveClass(/is-complete/);
  await expect(page.locator('#reportContent [data-report-read-badge]')).toHaveText('已经阅读完');
  await expect(page.locator('#reportContent [data-report-read-badge]')).toBeVisible();
  await expect(page.locator('[data-report-id="report-evening"] [data-report-read-badge]')).not.toHaveAttribute('hidden');
  expect(writes).toHaveLength(1);
});

test('保存失败恢复可重试，防止重复提交并保留来源展开和滚动位置',async({page,request})=>{
  let release;
  const gate=new Promise(resolve=>release=resolve), writes=[];
  await page.route('**/api/news/mark-read-by-ids',async route=>{
    writes.push(route.request().postDataJSON().item_ids);
    await gate;
    await route.fulfill({status:500,json:{ok:false,error:'test_failure'}});
  });
  await page.goto('/'); await openReports(page);
  await page.locator('.report-sources summary').first().click();
  const scroll=await page.locator('#reportScrollArea').evaluate(node=>{node.scrollTop=150;return node.scrollTop;});
  const button=page.locator('#markReportReadBtn');
  await button.click();
  await expect(button).toHaveAttribute('aria-busy','true');
  await expect(button).toBeDisabled();
  await button.evaluate(node=>node.click());
  await expect.poll(()=>writes.length).toBe(1);
  release();
  await expect(button).toHaveAttribute('aria-busy','false');
  await expect(button).toBeEnabled();
  await expect(page.locator('#toast')).not.toContainText('已经阅读完');
  await expect(button).not.toHaveClass(/is-complete/);
  await expect(page.locator('#reportContent [data-report-read-badge]')).toBeHidden();
  await expect(page.locator('[data-report-id="report-evening"] [data-report-read-badge]')).toBeHidden();
  for (const id of ['ui-000','ui-001']) expect((await detail(request,id)).read_at).toBeNull();
  await expect(page.locator('.report-sources').first()).toHaveJSProperty('open',true);
  expect(await page.locator('#reportScrollArea').evaluate(node=>node.scrollTop)).toBe(scroll);
  await page.unroute('**/api/news/mark-read-by-ids');
  await button.click();
  await expect(button).toHaveAttribute('title','已经阅读完');
  for (const id of ['ui-000','ui-001']) expect((await detail(request,id)).read_at).toBeTruthy();
});

test('保存途中切换报告不误标新来源或显示旧成功提示',async({page,request})=>{
  await extraSources(page,request,'report-weekly',true);
  let release;
  const gate=new Promise(resolve=>release=resolve);
  await page.route('**/api/news/mark-read-by-ids',async route=>{await gate;await route.continue();});
  await page.goto('/'); await openReports(page);
  const button=page.locator('#markReportReadBtn');
  await button.click();
  await expect(button).toHaveAttribute('aria-busy','true');
  await page.locator('[data-report-kind="weekly"]').click();
  await expect(page.locator('#reportContent h1')).toHaveText('合成周报');
  release();
  await expect(button).toBeEnabled();
  await expect(page.locator('#reportContent h1')).toHaveText('合成周报');
  await expect(page.locator('#toast')).not.toContainText('已经阅读完');
  await expect(page.locator('#reportContent [data-report-read-badge]')).toBeHidden();
  expect((await (await request.get('/api/reports/report-evening')).json()).report.read_completed_at).toBeTruthy();
  expect((await (await request.get('/api/reports/report-weekly')).json()).report.read_completed_at).toBeNull();
  for (const id of ['ui-000','ui-001']) expect((await detail(request,id)).read_at).toBeTruthy();
  expect((await detail(request,'ui-064')).read_at).toBeNull();
  await button.click();
  await expect(button).toHaveAttribute('title','已经阅读完');
  expect((await detail(request,'ui-064')).read_at).toBeTruthy();
});

test('失效来源不误标其他新闻，未完成报告隐藏，仅有简讯仍可完成',async({page,request})=>{
  await request.post('/__test/reports');
  await page.goto('/'); await openReports(page);
  const button=page.locator('#markReportReadBtn');
  await expect(button).toBeEnabled();
  await button.click();
  await expect(button).toHaveClass(/is-complete/);
  await expect(page.locator('#reportContent [data-report-read-badge]')).toBeVisible();
  for (const id of ['ui-000','ui-001']) expect((await detail(request,id)).read_at).toBeNull();
  for (const state of [{failed:true},{running:true}]) {
    await request.post('/__test/reports',{data:state});
    await page.reload();
    await openNavigation(page); await page.locator('#navReportsBtn').click();
    await expect(page.locator('#reportContent h2')).toHaveText('合成晚报');
    await expect(button).toBeHidden();
  }
  await request.post('/__test/reports',{data:{linked_sources:true,briefs_only:true}});
  await page.reload(); await openReports(page);
  await expect(button).toBeEnabled(); await button.click();
  await expect(button).toHaveAttribute('title','已经阅读完');
  for (const id of ['ui-000','ui-001']) expect((await detail(request,id)).read_at).toBeTruthy();
});

test('完成记录保存失败可重试，来源已浏览不提前显示报告完成',async({page,request})=>{
  await page.route('**/api/reports/report-evening/read',route=>route.fulfill({status:500,json:{ok:false,error:'test_failure'}}));
  await page.goto('/'); await openReports(page);
  const button=page.locator('#markReportReadBtn');
  await button.click();
  await expect(button).toHaveAttribute('aria-busy','false');
  await expect(button).toBeEnabled();
  await expect(button).not.toHaveClass(/is-complete/);
  await expect(page.locator('#reportContent [data-report-read-badge]')).toBeHidden();
  for (const id of ['ui-000','ui-001']) expect((await detail(request,id)).read_at).toBeTruthy();
  expect((await (await request.get('/api/reports/report-evening')).json()).report.read_completed_at).toBeNull();
  await page.unroute('**/api/reports/report-evening/read');
  await button.click();
  await expect(button).toHaveClass(/is-complete/);
  await expect(page.locator('#reportContent [data-report-read-badge]')).toBeVisible();
});

test('来源已全部浏览仍需主动完成本报告，迟到列表不撤回完成标签',async({page,request})=>{
  await request.post('/api/news/mark-read-by-ids',{data:{item_ids:['ui-000','ui-001']}});
  await page.goto('/'); await openReports(page);
  const button=page.locator('#markReportReadBtn');
  await expect(button).toBeEnabled();
  await expect(page.locator('#reportContent [data-report-read-badge]')).toBeHidden();
  let release,entered;
  const gate=new Promise(resolve=>release=resolve),entry=new Promise(resolve=>entered=resolve);
  await page.route('**/api/reports?*',async route=>{
    const response=await route.fetch();entered();await gate;await route.fulfill({response});
  });
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await entry;
  await button.click();
  await expect(button).toHaveClass(/is-complete/);
  const restored=page.waitForResponse(response=>response.url().includes('/api/reports?'));
  release();await restored;
  await expect(page.locator('[data-report-id="report-evening"] [data-report-read-badge]')).toBeVisible();
  await expect(page.locator('#reportContent [data-report-read-badge]')).toBeVisible();
  await page.locator('[data-report-id="report-noon"]').click();
  await expect(button).toBeEnabled();
  await expect(page.locator('#reportContent [data-report-read-badge]')).toBeHidden();
  await expect(page.locator('[data-report-id="report-noon"] [data-report-read-badge]')).toBeHidden();
});
