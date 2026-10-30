import {test,expect} from '@playwright/test';
import {chooseTheme,openNavigation,openSettings} from './helpers.js';

async function openReports(page) {
  await openNavigation(page);
  await page.locator('#navReportsBtn').click();
  await expect(page.locator('#feedColReports')).toHaveClass(/active/);
}
test.beforeEach(async({request}) => {await request.post('/__test/reports');});

test('日报归集三个期别，周月切换、来源按需展开且无阅读写入', async({page}) => {
  const writes=[]; const errors=[];
  page.on('request', request=>{if(request.method() !== 'GET') writes.push(request.url());});
  page.on('pageerror', error=>errors.push(error.message));
  await page.goto('/'); await openReports(page);
  await expect(page.locator('[data-report-id]')).toHaveCount(3);
  await expect(page.locator('#reportContent h1')).toHaveText('合成晚报');
  writes.length = 0;
  await expect(page.locator('#reportContent a:visible')).toHaveCount(0);
  expect(await page.evaluate(()=>window.reportInjected)).toBeUndefined();
  await page.locator('.report-sources summary').first().click();
  await expect(page.locator('.report-sources[open]')).toHaveCount(1);
  await expect(page.locator('.report-sources[open] a')).toHaveCount(1);
  await page.locator('.report-sources summary').nth(1).click();
  await expect(page.locator('.report-sources[open]')).toHaveCount(1);
  await page.locator('.report-sources summary').nth(1).press('Enter');
  await expect(page.locator('.report-sources[open]')).toHaveCount(0);
  await page.locator('[data-report-kind="weekly"]').click();
  await expect(page.locator('[data-report-id]')).toHaveCount(1);
  await expect(page.locator('#reportContent h1')).toHaveText('合成周报');
  await page.locator('[data-report-kind="weekly"]').press('ArrowRight');
  await expect(page.locator('[data-report-kind="monthly"]')).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('#reportContent h1')).toHaveText('合成月报');
  await page.locator('#reportScrollArea').evaluate(node=>node.scrollTop=node.scrollHeight);
  await expect(page.locator('.report-end')).toBeInViewport();
  expect(writes.filter(url=>/mark-read|\/state|checkpoint|\/seen/.test(url))).toEqual([]);
  expect(errors).toEqual([]);
});

test('启用后空列表不塞入样稿，索引失败可重试',async({page,request})=>{
  await request.post('/__test/reports',{data:{empty:true}});
  await page.goto('/'); await openReports(page);
  await expect(page.locator('#reportCardList')).toContainText('尚无日报');
  await expect(page.locator('[data-report-id]')).toHaveCount(0);
  await expect(page.locator('#reportContent')).toContainText('从本次更新后开始生成');
  await request.post('/__test/reports',{data:{failed:true}});
  await page.reload(); await openReports(page);
  await expect(page.locator('#reportContent')).toContainText('索引刷新失败');
  await page.locator('#retryReportBtn').click();
  await expect(page.locator('#reportContent')).toContainText('等待生成');
  await expect(page.locator('#retryReportBtn')).toBeHidden();
});

test('手动重试时无法确认模型容量会显示可操作的原因',async({page,request})=>{
  await request.post('/__test/reports',{data:{failed:true}});
  await page.route('**/api/reports/report-evening/retry',route=>route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({ok:false,error:'report_model_capacity_unknown'})}));
  await page.goto('/'); await openReports(page);
  await page.locator('#retryReportBtn').click();
  await expect(page.locator('#toast')).toContainText('无法确认所选模型的上下文容量');
  await expect(page.locator('#retryReportBtn')).toBeEnabled();
});

test('迟到的日报响应不会覆盖已经切换的月报',async({page})=>{
  let release;
  const hold = new Promise(resolve=>release=resolve);
  await page.route('**/api/reports?kind=daily*',async route=>{await hold;await route.continue();});
  await page.goto('/'); await openReports(page);
  await page.locator('[data-report-kind="monthly"]').click();
  await expect(page.locator('#reportContent h1')).toHaveText('合成月报');
  release();
  await expect(page.locator('[data-report-kind="monthly"]')).toHaveAttribute('aria-pressed','true');
  await expect(page.locator('#reportContent h1')).toHaveText('合成月报');
});

for (const width of [390, 1280]) test('刷新失败在列表、通知和详情显示原因及自动重试 '+width,async({page,request})=>{
  await page.setViewportSize({width,height:900});
  await request.post('/__test/reports',{data:{failed:true,pending:true,error:'report_index_permission_denied'}});
  await page.goto('/'); await openReports(page);
  await expect(page.locator('[data-report-id="report-evening"]')).toContainText('等待重试');
  await expect(page.locator('[data-report-id="report-evening"]')).toContainText('没有权限读取新闻目录或文件');
  await expect(page.locator('#toast')).toContainText('没有权限读取新闻目录或文件');
  await page.locator('[data-report-id="report-evening"]').click();
  await expect(page.locator('#reportContent [role="alert"]')).toContainText('没有权限读取新闻目录或文件');
  await expect(page.locator('#reportContent')).toContainText('已尝试 1 / 3 次');
  await expect(page.locator('#reportContent')).toContainText('自动重试');
  await expect(page.locator('#retryReportBtn')).toBeHidden();
  for(const [theme,colorScheme] of [['light','light'],['dark','dark'],['system','light'],['system','dark'],['eink','light']]) {
    await page.emulateMedia({colorScheme});
    await chooseTheme(page,theme);
    expect(await page.locator('#reportScrollArea').evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
    await expect(page.locator('#reportContent [role="alert"]')).toContainText('没有权限读取新闻目录或文件');
  }
});

test('手机抽屉入口、详情返回、设置往返与来源键盘操作',async({page})=>{
  await page.setViewportSize({width:390,height:844});
  await page.goto('/'); await openReports(page);
  await expect(page.locator('.mobile-nav-btn')).toHaveCount(3);
  await expect(page.locator('#detailPanelReports')).not.toBeInViewport();
  await page.locator('[data-report-id="report-evening"]').click();
  await expect(page.locator('#detailPanelReports')).toBeInViewport();
  await expect(page.locator('#mobileReaderBackBtn')).toHaveAttribute('aria-label','返回报告列表');
  await page.locator('.report-sources summary').first().click();
  await expect(page.locator('.report-sources[open]')).toHaveCount(1);
  await openSettings(page);
  const resumed = page.waitForResponse(response=>response.url().includes('/api/reports?kind=daily'));
  await page.locator('#closeSettingsBtn').click();
  await resumed;
  await expect(page.locator('#detailPanelReports')).toBeInViewport();
  await expect(page.locator('.report-sources[open]')).toHaveCount(1);
  await page.locator('#mobileReaderBackBtn').click();
  await expect(page.locator('#feedColReports')).toBeInViewport();
  await page.locator('[data-report-id="report-evening"]').click();
  await expect(page.locator('#reportContent h1')).toHaveText('合成晚报');
  await openNavigation(page); await page.locator('#navFeedBtn').click();
  await expect(page.locator('#feedColFeed')).toHaveClass(/active/);
});

for (const width of [320,390,920,921,1279,1280]) test('报告复用阅读尺度与主题，布局无溢出 '+width,async({page})=>{
  await page.setViewportSize({width,height:1000});
  await page.goto('/');
  await page.locator('#feedCardList .news-card').first().click();
  await expect(page.locator('#detailContent .article-body').first()).toBeVisible();
  const readerStyle=await page.locator('#detailContent .article-body').first().evaluate(node=>{const css=getComputedStyle(node);return [css.fontFamily,css.fontSize,css.lineHeight];});
  const feedWidth=await page.locator('#feedColFeed').evaluate(node=>getComputedStyle(node).width);
  if(width<=920) await page.locator('#mobileReaderBackBtn').click();
  await openReports(page);
  await page.locator('[data-report-id="report-evening"]').click();
  const reportStyle=await page.locator('#reportContent .report-section .article-body').first().evaluate(node=>{const css=getComputedStyle(node);return [css.fontFamily,css.fontSize,css.lineHeight];});
  expect(reportStyle).toEqual(readerStyle);
  expect(await page.locator('#feedColReports').evaluate(node=>getComputedStyle(node).width)).toBe(feedWidth);
  for(const [size,scale] of [['small',.9],['standard',1],['large',1.15],['extra-large',1.3]]) {
    const label = await page.locator(`#fontSizeSelect-native option[value="${size}"]`).textContent();
    await page.getByRole('combobox',{name:'阅读字号',exact:true}).click();
    await page.getByRole('option',{name:label,exact:true}).click();
    expect(await page.locator('#reportContent .article-body').first().evaluate(node=>parseFloat(getComputedStyle(node).fontSize))).toBeCloseTo(14*scale);
    expect(await page.locator('#reportScrollArea').evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
  }
  for(const [theme,colorScheme] of [['light','light'],['dark','dark'],['system','light'],['system','dark'],['eink','light']]) {
    await page.emulateMedia({colorScheme});
    await chooseTheme(page,theme);
    expect(await page.locator('#reportScrollArea').evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
    expect(await page.locator('#reportContent .article-body').first().evaluate(node=>getComputedStyle(node).fontSize)).toBe('18.2px');
    await page.locator('.report-sources summary').first().click();
    await expect(page.locator('.report-sources[open]')).toHaveCount(1);
    expect(await page.locator('#reportScrollArea').evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
    await page.locator('.report-sources summary').first().click();
  }
  if([390,1280].includes(width)) {
    await chooseTheme(page,width===390?'dark':'light');
    await page.locator('#reportScrollArea').evaluate(node=>node.scrollTop=0);
    await page.screenshot({path:'.ui-artifacts/reports-'+width+'.png'});
  }
});

for (const width of [390,1280]) test('重点与简讯沿用阅读样式，来源折叠且兼容只有简讯的报告 '+width,async({page,request})=>{
  await page.setViewportSize({width,height:1000});
  await request.post('/__test/reports',{data:{briefs:true}});
  await page.goto('/'); await openReports(page);
  await page.locator('[data-report-id="report-evening"]').click();
  const briefs=page.locator('section[aria-labelledby="report-briefs"]');
  await expect(briefs.locator('.article-body li')).toHaveCount(2);
  await expect(page.locator('#reportContent .article-meta-top')).toContainText('2个主题 · 2条简讯');
  await expect(briefs.locator('a:visible')).toHaveCount(0);
  expect(await page.evaluate(()=>window.reportInjected)).toBeUndefined();
  for(const theme of ['light','dark','system','eink']) {
    await chooseTheme(page,theme);
    const style=await briefs.locator('.article-body').evaluate(node=>getComputedStyle(node).fontSize);
    expect(style).toBe(await page.locator('.report-section .article-body').first().evaluate(node=>getComputedStyle(node).fontSize));
    expect(await page.locator('#reportScrollArea').evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
  }
  await chooseTheme(page,width===390?'dark':'light');
  await briefs.scrollIntoViewIfNeeded();
  await page.screenshot({path:'.ui-artifacts/report-briefs-'+width+'.png'});
  await briefs.locator('summary').click();
  await expect(briefs.locator('a:visible')).toHaveCount(1);
  await request.post('/__test/reports',{data:{briefs_only:true}});
  await page.reload(); await openReports(page);
  await page.locator('[data-report-id="report-evening"]').click();
  await expect(page.locator('#reportContent')).not.toContainText('暂无符合条件');
  await expect(page.locator('#reportContent .report-section')).toHaveCount(1);
  await expect(page.locator('.report-end')).toBeVisible();
});

for (const width of [320,390,1280]) test('手动生成可立即排队、阅读及单独删除 '+width,async({page,request})=>{
  await page.setViewportSize({width,height:900});
  const errors=[], writes=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/'); await openReports(page);
  await page.locator('#createReportBtn').click();
  await expect(page.getByRole('combobox',{name:'报告类型',exact:true})).toBeVisible();
  await expect(page.locator('#reportCreationKind-native option')).toHaveCount(6);
  await page.getByRole('combobox',{name:'报告类型',exact:true}).click();
  await page.getByRole('option',{name:/周报$/}).click();
  await expect(page.locator('#reportCreationPeriod')).toContainText('2026-10-19');
  await page.locator('#reportSelection').fill('2026-10-20');
  await expect(page.locator('#reportCreationPeriod')).toContainText('2026-10-26 · 共 7 天');
  for (const [theme,colorScheme] of [['light','light'],['dark','dark'],['system','light'],['system','dark'],['eink','light']]) {
    await page.emulateMedia({colorScheme}); await chooseTheme(page,theme);
    expect(await page.locator('#reportScrollArea').evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
    await expect(page.locator('#reportSelection')).toHaveCSS('color-scheme',theme==='dark'||theme==='system'&&colorScheme==='dark'?'dark':'light');
  }
  const refreshed=page.waitForResponse(response=>response.url().includes('/api/reports?kind=daily'));
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await refreshed;
  await expect(page.getByRole('combobox',{name:'报告类型',exact:true})).toContainText('周报');
  if(width!==320) {
    await chooseTheme(page,width===390?'dark':'light');
    await page.screenshot({path:'.ui-artifacts/report-preview-form-'+width+'.png'});
  }
  page.on('request',r=>{if(r.method()!=='GET')writes.push(r.url());});
  const submitted=page.waitForResponse(response=>response.url().endsWith('/api/reports/manual') && response.request().method()==='POST');
  await page.getByRole('button',{name:'开始生成',exact:true}).click();
  const created=(await (await submitted).json()).report;
  expect(created.is_manual).toBe(true);
  expect(created.period_start).toBe('2026-10-20T00:00:00+08:00');
  expect(created.period_end).toBe('2026-10-27T00:00:00+08:00');
  await expect(page.locator('[data-report-kind="weekly"]')).toHaveAttribute('aria-pressed','true');
  await expect(page.locator('#reportContent')).toContainText('等待生成');
  await expect(page.locator(`[data-report-id="${created.id}"]`)).toContainText('手动生成');
  expect((await (await request.post('/__test/reports/run')).json()).worked).toBe(true);
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await expect(page.locator('#reportContent .article-meta-top')).toContainText('手动生成');
  await expect(page.locator('.report-end')).toBeVisible();
  await page.locator('#deleteReportBtn').click();
  await expect(page.locator('#appConfirmDialog')).toBeVisible();
  await expect(page.locator('#appConfirmCancel')).toBeFocused();
  await page.locator('#appConfirmAccept').click();
  await expect(page.locator(`[data-report-id="${created.id}"]`)).toHaveCount(0);
  await expect(page.locator('[data-report-id="report-weekly"]')).toHaveCount(1);
  await page.locator('[data-report-id="report-weekly"]').click();
  await expect(page.locator('#deleteReportBtn')).toBeEnabled();
  expect(writes.filter(url=>/mark-read|\/state|checkpoint|\/seen/.test(url))).toEqual([]);
  expect(errors).toEqual([]);
});

test('手动生成提交失败保留选择，返回操作不创建任务',async({page})=>{
  await page.goto('/'); await openReports(page);
  await page.locator('#createReportBtn').click();
  await expect(page.getByRole('combobox',{name:'报告类型',exact:true})).toBeVisible();
  await page.route('**/api/reports/manual',route=>route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({ok:false,error:'report_database_changed'})}));
  await page.getByRole('button',{name:'开始生成',exact:true}).click();
  await expect(page.locator('#reportCreationError')).toContainText('新闻库已切换');
  await expect(page.getByRole('button',{name:'开始生成',exact:true})).toBeEnabled();
  await page.getByRole('button',{name:'返回报告',exact:true}).click();
  await expect(page.locator('#reportCreationForm')).toHaveCount(0);
  await expect(page.locator('#reportContent h1')).toHaveText('合成晚报');
});

for(const width of [390,1440]) test('研究导航统一隐藏来源，返回阅读恢复 '+width,async({page})=>{
  await page.setViewportSize({width,height:900});
  await page.goto('/');await openNavigation(page);
  const section=page.locator('#navReportsBtn').locator('..');
  await expect(section.locator('.nav-title')).toHaveText('研究');
  await expect(section.locator('[data-collection]')).toHaveCount(3);
  for(const id of ['navReportsBtn','navTopicsBtn','navSummariesBtn']) {
    await openNavigation(page);await page.locator('#'+id).click();await openNavigation(page);
    await expect(page.locator('#sourceFilters').locator('..')).toBeHidden();
  }
  await page.locator('#navFeedBtn').click();await openNavigation(page);
  await expect(page.locator('#sourceFilters').locator('..')).toBeVisible();
});

for(const width of [390,1440]) test('生成进度随阶段和批次更新，生成中禁删 '+width,async({page,request})=>{
  await page.setViewportSize({width,height:900});
  await request.post('/__test/reports',{data:{running:true,progress:{stage:'selecting',completed:1,total:4,source_count:650}}});
  await page.goto('/');await openReports(page);
  await page.locator('[data-report-id="report-evening"]').click();
  await expect(page.locator('#reportContent')).toContainText('已完成 1 / 4 批');
  await expect(page.locator('#reportContent')).toContainText('已读取 650 条新闻');
  await expect(page.locator('#reportElapsed')).toContainText('已用时');
  await expect(page.locator('#deleteReportBtn')).toBeDisabled();
  await request.post('/__test/reports',{data:{running:true,progress:{stage:'writing',source_count:650}}});
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await expect(page.locator('[data-report-id="report-evening"]')).toContainText('编写报告');
  await expect(page.locator('#reportContent')).not.toContainText('已完成 1 / 4 批');
  await expect(page.locator('#reportContent [aria-current="step"]')).toHaveText('3 · 编写报告 · 进行中');
  for(const [theme,colorScheme] of [['light','light'],['dark','dark'],['system','light'],['system','dark'],['eink','light']]) {
    await page.emulateMedia({colorScheme});await chooseTheme(page,theme);
    expect(await page.locator('#reportScrollArea').evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
  }
  await chooseTheme(page,'light');
  await page.screenshot({path:`output/report-optimization-check/report-progress-${width}.png`});
  await request.post('/__test/reports');
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await expect(page.locator('.report-end')).toBeAttached();
  await expect(page.locator('#reportContent')).not.toContainText('生成进度');
  await expect(page.locator('#deleteReportBtn')).toBeEnabled();
});

test('删除普通报告经确认，只删除选中报告',async({page,request})=>{
  await page.goto('/');await openReports(page);
  await page.locator('#deleteReportBtn').click();
  await expect(page.locator('#appConfirmMessage')).toContainText('合成晚报');
  await page.locator('#appConfirmCancel').click();
  await expect(page.locator('[data-report-id="report-evening"]')).toHaveCount(1);
  await page.locator('#deleteReportBtn').click();await page.locator('#appConfirmAccept').click();
  await expect(page.locator('[data-report-id="report-evening"]')).toHaveCount(0);
  expect((await request.get('/api/reports/report-evening')).status()).toBe(404);
  expect((await request.get('/api/reports/report-noon')).status()).toBe(200);
});

test('非法时间窗口阻止提交且保留草稿',async({page})=>{
  const writes=[];page.on('request',request=>{if(request.url().endsWith('/api/reports/manual'))writes.push(request);});
  await page.goto('/');await openReports(page);await page.locator('#createReportBtn').click();
  await page.getByRole('combobox',{name:'报告类型',exact:true}).click();
  await page.getByRole('option',{name:'自定义',exact:true}).click();
  await page.locator('#reportStart').fill('2026-10-29T10:00');
  await page.locator('#reportEnd').fill('2026-10-29T09:00');
  await page.getByRole('button',{name:'开始生成',exact:true}).click();
  expect(await page.locator('#reportEnd').evaluate(input=>input.validationMessage)).toContain('结束时间须晚于开始时间');
  await page.locator('#reportEnd').fill('2030-10-30T09:00');
  await page.getByRole('button',{name:'开始生成',exact:true}).click();
  expect(await page.locator('#reportEnd').evaluate(input=>input.validity.rangeOverflow)).toBe(true);
  expect(writes).toHaveLength(0);
  await expect(page.locator('#reportStart')).toHaveValue('2026-10-29T10:00');
});

for(const width of [390,1440]) test('日期、连续七天、整月和自定义按北京时间生成 '+width,async({page})=>{
  await page.setViewportSize({width,height:900});
  await page.goto('/');await openReports(page);
  for(const [kind,label,selection,start,end,tab] of [
    ['morning','早报','2026-10-29','2026-10-28T21:30:00+08:00','2026-10-29T08:30:00+08:00','daily'],
    ['noon','午报','2026-10-29','2026-10-29T08:30:00+08:00','2026-10-29T14:30:00+08:00','daily'],
    ['evening','晚报','2026-10-29','2026-10-28T21:30:00+08:00','2026-10-29T21:30:00+08:00','daily'],
    ['weekly','周报','2026-10-21','2026-10-21T00:00:00+08:00','2026-10-28T00:00:00+08:00','weekly'],
    ['monthly','月报','2024-02','2024-02-01T00:00:00+08:00','2024-03-01T00:00:00+08:00','monthly'],
    ['custom','自定义','', '2026-10-29T10:15:00+08:00','2026-10-29T11:45:00+08:00','daily'],
  ]) {
    if(width<=920 && await page.locator('#feedView').evaluate(node=>node.classList.contains('mobile-reader-active')))
      await page.locator('#mobileReaderBackBtn').click();
    await page.locator('#createReportBtn').click();
    await page.getByRole('combobox',{name:'报告类型',exact:true}).click();
    await page.getByRole('option',{name:label,exact:true}).click();
    if(kind==='custom') {
      await expect(page.locator('#reportSelection')).toHaveCount(0);
      await page.locator('#reportStart').fill(start.slice(0,16));await page.locator('#reportEnd').fill(end.slice(0,16));
    } else {
      await expect(page.locator('#reportStart, #reportEnd')).toHaveCount(0);
      await expect(page.locator('#reportSelection')).toHaveAttribute('type',kind==='monthly'?'month':'date');
      await page.locator('#reportSelection').fill(selection);
    }
    if(kind==='weekly') await expect(page.locator('#reportCreationPeriod')).toContainText('2026-10-21 — 2026-10-27 · 共 7 天');
    if(kind==='monthly') await expect(page.locator('#reportCreationPeriod')).toContainText('2024-02 · 整月');
    for(const [theme,colorScheme] of [['light','light'],['dark','dark'],['system','light'],['system','dark'],['eink','light']]) {
      await page.emulateMedia({colorScheme});await chooseTheme(page,theme);
      expect(await page.locator('#reportScrollArea').evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
      await expect(page.locator('#reportPeriodFields input').first()).toHaveCSS('color-scheme',theme==='dark'||theme==='system'&&colorScheme==='dark'?'dark':'light');
    }
    await chooseTheme(page,'light');
    if(kind==='weekly'||kind==='monthly') await page.screenshot({path:`output/report-optimization-check/report-${kind}-selection-${width}.png`});
    const submitted=page.waitForResponse(response=>response.url().endsWith('/api/reports/manual') && response.request().method()==='POST');
    await page.getByRole('button',{name:'开始生成',exact:true}).click();
    const response=await submitted, report=(await response.json()).report;
    expect(response.status()).toBe(202);expect(report.kind).toBe(kind);
    expect(report.period_start).toBe(start);expect(report.period_end).toBe(end);
    await expect(page.locator(`[data-report-kind="${tab}"]`)).toHaveAttribute('aria-pressed','true');
    await expect(page.locator('#reportContent')).toContainText('等待生成');
    if(kind==='custom') await expect(page.locator('#reportToolbarTitle')).toContainText('自定义');
  }
});

test('尚未结束的日报周报和月报不能提前生成',async({page})=>{
  const writes=[];page.on('request',request=>{if(request.url().endsWith('/api/reports/manual'))writes.push(request);});
  await page.goto('/');await openReports(page);await page.locator('#createReportBtn').click();
  for(const [label,selection,max] of [['晚报','2026-10-30','2026-10-29'],['周报','2026-10-24','2026-10-23'],['月报','2026-10','2026-09']]) {
    await page.getByRole('combobox',{name:'报告类型',exact:true}).click();await page.getByRole('option',{name:label,exact:true}).click();
    await expect(page.locator('#reportSelection')).toHaveAttribute('max',max);
    await page.locator('#reportSelection').fill(selection);await page.getByRole('button',{name:'开始生成',exact:true}).click();
    expect(await page.locator('#reportSelection').evaluate(input=>input.validationMessage)).toContain('时间范围尚未结束');
  }
  expect(writes).toHaveLength(0);
});
