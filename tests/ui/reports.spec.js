import {test,expect} from '@playwright/test';
import {chooseTheme,choosePicker,openNavigation,openSettings} from './helpers.js';

async function openReports(page) {
  await openNavigation(page);
  await page.locator('#navReportsBtn').click();
  await expect(page.locator('#feedColReports')).toHaveClass(/active/);
}
test.beforeEach(async({request}) => {await request.post('/__test/reports');});

for(const width of [390,1440]) test('移除旧导读，复制完整正文与折叠来源，显示实际执行时间 '+width,async({page,request})=>{
  await page.setViewportSize({width,height:900});
  await request.post('/__test/reports',{data:{briefs:true,started_at:'2026-11-01T13:57:25Z',generation_seconds:125}});
  const report=(await (await request.get('/api/reports/report-evening')).json()).report;
  expect(report.content.overview).toBeUndefined();expect(report.excerpt).toBeUndefined();
  await page.addInitScript(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.copiedReport=text;}}}));
  await page.goto('/');await openReports(page);
  const card=page.locator('[data-report-id="report-evening"]');
  await expect(card.locator('.card-time')).toHaveText('11/01 21:57');
  await expect(card.locator('.news-card-snippet')).toHaveCount(0);
  await card.click();
  await expect(page.locator('.report-overview')).toHaveCount(0);
  await expect(page.locator('#reportContent')).not.toContainText('产线取得技术进展，规模交付仍待验证。');
  await expect(page.locator('.report-sources[open]')).toHaveCount(0);
  await page.getByRole('button',{name:'复制全文',exact:true}).click();
  await expect(page.locator('#toast')).toContainText('已复制报告全文');
  const copied=await page.evaluate(()=>window.copiedReport);
  expect(copied).toContain('合成晚报');expect(copied).toContain('北京时间');
  expect(copied).toContain('2026/10/19');expect(copied).toContain('Pi · test-model · 生成用时 2 分 5 秒');
  for(const section of report.content.sections) {
    expect(copied).toContain(section.title);
    for(const paragraph of section.paragraphs) expect(copied).toContain(paragraph.text);
  }
  for(const brief of report.content.briefs) expect(copied).toContain(brief.text);
  for(const source of report.content.sources) expect(copied).toContain(source.title);
  expect(copied.match(/https:\/\/example.com\/source/g)).toHaveLength(1);
  expect(copied).not.toContain('产线取得技术进展，规模交付仍待验证。');
  expect(copied).not.toContain('复制全文');expect(copied).toContain('本期完');
  expect(await page.evaluate(()=>window.reportInjected)).toBeUndefined();
  await expect(page.locator('.report-sources[open]')).toHaveCount(0);
  expect(await page.locator('#detailPanelReports .detail-toolbar').evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
});

test('复制受限时回退，失败不显示成功，未完成报告不提供复制',async({page,request})=>{
  await page.addInitScript(()=>{
    Object.defineProperty(navigator,'clipboard',{value:{writeText:async()=>{throw new Error('denied');}}});
    document.execCommand=command=>{window.copyFallback=command;return window.allowCopy!==false;};
  });
  await page.goto('/');await openReports(page);
  await expect(page.locator('[data-report-id="report-evening"] .card-time')).toHaveText('未记录执行时间');
  await page.locator('#copyReportBtn').click();
  await expect(page.locator('#toast')).toContainText('已复制报告全文');
  expect(await page.evaluate(()=>window.copyFallback)).toBe('copy');
  await page.evaluate(()=>window.allowCopy=false);
  await page.locator('#copyReportBtn').click();
  await expect(page.locator('#toast')).toContainText('复制失败');
  await expect(page.locator('#copyReportBtn')).toBeEnabled();
  await expect(page.locator('#copyReportBtn')).toBeFocused();
  await request.post('/__test/reports',{data:{failed:true,pending:true}});
  await page.reload();await openReports(page);
  await expect(page.locator('#copyReportBtn')).toBeHidden();
  await expect(page.locator('[data-report-id="report-evening"] .card-time')).toHaveText('等待执行');
});

test('报告模型旁显示固定生成用时，缺失时不编造',async({page,request})=>{
  await request.post('/__test/reports',{data:{generation_seconds:125.375}});
  await page.goto('/');await openReports(page);
  const metadata=page.locator('#reportContent .article-meta-bottom').filter({hasText:'Pi · test-model'});
  await expect(metadata).toContainText('生成用时 2 分 5 秒');
  await expect(metadata.locator('span')).toHaveText(['Pi · test-model','生成用时 2 分 5 秒']);
  await page.locator('[data-report-kind="weekly"]').click();
  await expect(metadata).toContainText('生成用时 2 分 5 秒');
  await request.post('/__test/reports',{data:{generation_seconds:null}});
  await page.reload();await openReports(page);
  await expect(page.locator('#reportContent')).not.toContainText('生成用时');
});

test('错误重试期间累计用时冻结，恢复后继续计时，完成后保留总用时',async({page,request})=>{
  await page.clock.setFixedTime(new Date('2026-11-02T12:00:00+08:00'));
  await request.post('/__test/reports',{data:{failed:true,pending:true,generation_seconds:125}});
  await page.goto('/');await openReports(page);
  const metadata=page.locator('#reportContent .article-meta-bottom').filter({hasText:'Pi · test-model'});
  await expect(metadata).toContainText('生成用时 2 分 5 秒');
  await page.clock.setFixedTime(new Date('2026-11-02T12:10:00+08:00'));
  await expect(metadata).toContainText('生成用时 2 分 5 秒');
  await request.post('/__test/reports',{data:{running:true,generation_seconds:125,started_at:'2026-11-02T12:10:00+08:00'}});
  await page.clock.setFixedTime(new Date('2026-11-02T12:10:10+08:00'));
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await expect(page.locator('#reportElapsed')).toHaveText('已用时 2分15秒');
  await page.clock.setFixedTime(new Date('2026-11-02T12:10:15+08:00'));
  await expect(page.locator('#reportElapsed')).toHaveText('已用时 2分20秒');
  await request.post('/__test/reports',{data:{failed:true,generation_seconds:140}});
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await expect(metadata).toContainText('生成用时 2 分 20 秒');
  await expect(page.locator('#reportElapsed')).toHaveCount(0);
  await request.post('/__test/reports',{data:{running:true,generation_seconds:140,started_at:'2026-11-02T13:00:00+08:00'}});
  await page.clock.setFixedTime(new Date('2026-11-02T13:00:20+08:00'));
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await expect(page.locator('#reportElapsed')).toHaveText('已用时 2分40秒');
  await request.post('/__test/reports',{data:{generation_seconds:160}});
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await expect(metadata).toContainText('生成用时 2 分 40 秒');
  await expect(page.locator('#reportElapsed')).toHaveCount(0);
});

for(const width of [320,390,1440]) test('新增报告仅保留五种日期类型，不提供自定义窗口 '+width,async({page})=>{
  await page.setViewportSize({width,height:width===320?568:900});
  await page.goto('/');await openReports(page);await page.locator('#createReportBtn').click();
  await page.getByRole('combobox',{name:'报告类型',exact:true}).click();
  await expect(page.getByRole('option')).toHaveText(['早报','午报','晚报','周报','月报']);
  await expect(page.getByRole('option',{name:'自定义',exact:true})).toHaveCount(0);
  await page.getByRole('option',{name:'周报',exact:true}).click();
  await expect(page.locator('#reportStart, #reportEnd')).toHaveCount(0);
  await expect(page.locator('#reportCreationPopover input[type="date"],#reportCreationPopover input[type="datetime-local"],#reportCreationPopover input[type="time"]')).toHaveCount(0);
  await expect(page.locator('#reportSelection')).toBeVisible();
  expect(await page.locator('#reportCreationPopover').evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
  await page.screenshot({path:`.ui-artifacts/report-types-${width}.png`});
});

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

for (const [code, message] of [
  ['report_model_capacity_unknown', '无法确认所选模型的上下文容量'],
  ['report_model_dependencies_missing', 'npm ci --ignore-scripts'],
]) test(`手动重试时显示可操作的模型错误：${code}`,async({page,request})=>{
  await request.post('/__test/reports',{data:{failed:true}});
  await page.route('**/api/reports/report-evening/retry',route=>route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({ok:false,error:code})}));
  await page.goto('/'); await openReports(page);
  await page.locator('#retryReportBtn').click();
  await expect(page.locator('#toast')).toContainText(message);
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

for (const width of [320,390,920,921,1279,1280]) test('报告复用阅读尺度与主题，布局无溢出 '+width,async({page,request})=>{
  await request.post('/__test/reports',{data:{generation_seconds:125.375}});
  await page.setViewportSize({width,height:1000});
  await page.goto('/');
  await page.locator('#feedCardList .news-card').first().click();
  await expect(page.locator('#detailContent .article-body').first()).toBeVisible();
  const readerStyle=await page.locator('#detailContent .article-body').first().evaluate(node=>{const css=getComputedStyle(node);return [css.fontFamily,css.fontSize,css.lineHeight];});
  const feedWidth=await page.locator('#feedColFeed').evaluate(node=>getComputedStyle(node).width);
  if(width<=920) await page.locator('#mobileReaderBackBtn').click();
  await openReports(page);
  await page.locator('[data-report-id="report-evening"]').click();
  await expect(page.locator('#reportContent')).toContainText('生成用时 2 分 5 秒');
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
    if ([390,1280].includes(width)) await page.screenshot({path:`output/report-tasks-check/report-${width}-${theme}-${colorScheme}.png`});
    await expect(page.locator('.report-sources[open]')).toHaveCount(1);
    expect(await page.locator('#reportScrollArea').evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
    await page.locator('.report-sources summary').first().click();
  }
  if([390,1280].includes(width)) {
    await page.keyboard.press('Escape');
    await chooseTheme(page,width===390?'dark':'light');
    await page.locator('#createReportBtn').click();
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
  await expect(page.locator('#reportCreationKind-native option')).toHaveCount(5);
  await page.getByRole('combobox',{name:'报告类型',exact:true}).click();
  await page.getByRole('option',{name:/周报$/}).click();
  await expect(page.locator('#reportCreationPeriod')).toContainText('2026-10-19');
  await page.locator('#reportSelection').fill('2026-10-20');
  await expect(page.locator('#reportCreationPeriod')).toContainText('2026-10-26 · 共 7 天');
  for (const [theme,colorScheme] of [['light','light'],['dark','dark'],['system','light'],['system','dark'],['eink','light']]) {
    await page.keyboard.press('Escape');
    await page.emulateMedia({colorScheme}); await chooseTheme(page,theme);
    await page.locator('#createReportBtn').click();
    expect(await page.locator('#reportCreationPopover').evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
    expect(await page.locator('#reportScrollArea').evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
    await expect(page.locator('#reportSelection')).toHaveCSS('color',await page.locator('#reportCreationKind').evaluate(node=>getComputedStyle(node).color));
  }
  const refreshed=page.waitForResponse(response=>response.url().includes('/api/reports?kind=daily'));
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await refreshed;
  await expect(page.getByRole('combobox',{name:'报告类型',exact:true})).toContainText('周报');
  if(width!==320) {
    await page.keyboard.press('Escape');
    await chooseTheme(page,width===390?'dark':'light');
    await page.locator('#createReportBtn').click();
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
  await page.getByRole('button',{name:'取消',exact:true}).click();
  await expect(page.locator('#reportCreationPopover')).toBeHidden();
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
  await request.post('/__test/reports',{data:{running:true,progress:{stage:'drafting',completed:2,total:7,source_count:5678}}});
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await expect(page.locator('#reportContent')).toContainText('编写报告章节 · 已完成 2 / 7 章');
  await expect(page.locator('[data-report-id="report-evening"]')).toContainText('编写报告章节');
  await expect(page.locator('#reportContent [aria-current="step"]')).toHaveText('3 · 编写报告 · 进行中');
  await request.post('/__test/reports',{data:{running:true,progress:{stage:'reviewing',completed:3,total:7,source_count:5678}}});
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await expect(page.locator('#reportContent')).toContainText('复核报告全文 · 已完成 3 / 7 章');
  await expect(page.locator('[data-report-id="report-evening"]')).toContainText('复核报告全文');
  await expect(page.locator('#reportContent [aria-current="step"]')).toHaveText('4 · 校验内容 · 进行中');
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

test('非法日期与未来日期阻止提交且保留草稿',async({page})=>{
  const writes=[];page.on('request',request=>{if(request.url().endsWith('/api/reports/manual'))writes.push(request);});
  await page.goto('/');await openReports(page);await page.locator('#createReportBtn').click();
  await choosePicker(page,'报告类型','早报');
  await page.locator('#reportSelection').fill('2026-02-30');
  await page.getByRole('button',{name:'开始生成',exact:true}).click();
  expect(await page.locator('#reportSelection').evaluate(input=>input.validationMessage)).toContain('请输入有效日期');
  await page.locator('#reportSelection').fill('2030-10-30');
  await page.getByRole('button',{name:'开始生成',exact:true}).click();
  expect(await page.locator('#reportSelection').evaluate(input=>input.validationMessage)).toContain('时间范围尚未结束');
  expect(writes).toHaveLength(0);
  await page.keyboard.press('Escape');await page.locator('#createReportBtn').click();
  await expect(page.locator('#reportSelection')).toHaveValue('2030-10-30');
});

for(const width of [390,1440]) test('日期、连续七天和整月按北京时间生成 '+width,async({page})=>{
  await page.setViewportSize({width,height:900});
  await page.goto('/');await openReports(page);
  for(const [kind,label,selection,start,end,tab] of [
    ['morning','早报','2026-10-29','2026-10-28T21:30:00+08:00','2026-10-29T08:30:00+08:00','daily'],
    ['noon','午报','2026-10-29','2026-10-29T08:30:00+08:00','2026-10-29T14:30:00+08:00','daily'],
    ['evening','晚报','2026-10-29','2026-10-28T21:30:00+08:00','2026-10-29T21:30:00+08:00','daily'],
    ['weekly','周报','2026-10-21','2026-10-21T00:00:00+08:00','2026-10-28T00:00:00+08:00','weekly'],
    ['monthly','月报','2024-02','2024-02-01T00:00:00+08:00','2024-03-01T00:00:00+08:00','monthly'],
  ]) {
    if(width<=920 && await page.locator('#feedView').evaluate(node=>node.classList.contains('mobile-reader-active')))
      await page.locator('#mobileReaderBackBtn').click();
    await page.locator('#createReportBtn').click();
    await page.getByRole('combobox',{name:'报告类型',exact:true}).click();
    await page.getByRole('option',{name:label,exact:true}).click();
    await expect(page.locator('#reportStart, #reportEnd')).toHaveCount(0);
    await expect(page.locator('#reportSelection')).toHaveAttribute('type','text');
    await page.locator('#reportSelection').fill(selection);
    if(kind==='weekly') await expect(page.locator('#reportCreationPeriod')).toContainText('2026-10-21 — 2026-10-27 · 共 7 天');
    if(kind==='monthly') await expect(page.locator('#reportCreationPeriod')).toContainText('2024-02 · 整月');
    for(const [theme,colorScheme] of [['light','light'],['dark','dark'],['system','light'],['system','dark'],['eink','light']]) {
      await page.keyboard.press('Escape');
      await page.emulateMedia({colorScheme});await chooseTheme(page,theme);
      await page.locator('#createReportBtn').click();
      expect(await page.locator('#reportCreationPopover').evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
      expect(await page.locator('#reportScrollArea').evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
      await expect(page.locator('#reportPeriodFields input').first()).toHaveAttribute('type','text');
      await expect(page.locator('#reportPeriodFields input').first()).toHaveCSS('color',await page.locator('#reportCreationKind').evaluate(node=>getComputedStyle(node).color));
    }
    await page.keyboard.press('Escape');await chooseTheme(page,'light');
    await page.locator('#createReportBtn').click();
    if(kind==='weekly'||kind==='monthly') await page.screenshot({path:`output/report-optimization-check/report-${kind}-selection-${width}.png`});
    const submitted=page.waitForResponse(response=>response.url().endsWith('/api/reports/manual') && response.request().method()==='POST');
    await page.getByRole('button',{name:'开始生成',exact:true}).click();
    const response=await submitted, report=(await response.json()).report;
    expect(response.status()).toBe(202);expect(report.kind).toBe(kind);
    expect(report.period_start).toBe(start);expect(report.period_end).toBe(end);
    await expect(page.locator(`[data-report-kind="${tab}"]`)).toHaveAttribute('aria-pressed','true');
    await expect(page.locator('#reportContent')).toContainText('等待生成');
  }
});

test('尚未结束的日报周报和月报不能提前生成',async({page})=>{
  const writes=[];page.on('request',request=>{if(request.url().endsWith('/api/reports/manual'))writes.push(request);});
  await page.goto('/');await openReports(page);await page.locator('#createReportBtn').click();
  for(const [label,selection,max] of [['晚报','2026-10-30','2026-10-30'],['周报','2026-10-24','2026-10-23'],['月报','2026-10','2026-09']]) {
    await page.getByRole('combobox',{name:'报告类型',exact:true}).click();await page.getByRole('option',{name:label,exact:true}).click();
    await expect(page.locator('#reportSelection')).toHaveAttribute('max',max);
    await page.locator('#reportSelection').fill(selection);await page.getByRole('button',{name:'开始生成',exact:true}).click();
    expect(await page.locator('#reportSelection').evaluate(input=>input.validationMessage)).toContain('时间范围尚未结束');
  }
  expect(writes).toHaveLength(0);
});

test('按覆盖月份和年份归档，默认展开当期，历史分组分页且轮询保留收折',async({page,request})=>{
  await request.post('/__test/reports',{data:{archive:true}});
  await page.goto('/');await openReports(page);
  const october = page.locator('[data-report-group="2026-10"]'), september = page.locator('[data-report-group="2026-09"]');
  await expect(october).toHaveAttribute('aria-expanded','true');
  await expect(september).toHaveAttribute('aria-expanded','false');
  await expect(september).toContainText('35份');
  await expect(page.locator('[data-report-id]:visible')).toHaveCount(3);
  await september.click();
  await expect(page.locator('#report-group-daily-2026-09 [data-report-id]')).toHaveCount(30);
  await page.locator('[data-report-more="2026-09"]').click();
  await expect(page.locator('#report-group-daily-2026-09 [data-report-id]')).toHaveCount(35);
  await expect(page.locator('[data-report-more]')).toHaveCount(0);
  await september.click();
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await expect(september).toHaveAttribute('aria-expanded','false');
  await expect(page.locator('[data-report-id]:visible')).toHaveCount(3);
  await page.locator('[data-report-kind="weekly"]').click();
  await expect(october).toHaveAttribute('aria-expanded','true');
  await expect(september).toHaveAttribute('aria-expanded','false');
  await september.click();
  await expect(page.locator('#report-group-weekly-2026-09 [data-report-id="archive-weekly"]')).toBeVisible();
  await page.locator('[data-report-kind="monthly"]').click();
  await expect(page.locator('[data-report-group="2026"]')).toHaveAttribute('aria-expanded','true');
  await expect(page.locator('[data-report-group="2025"]')).toHaveAttribute('aria-expanded','false');
  await page.locator('[data-report-group="2025"]').click();
  await expect(page.locator('#report-group-monthly-2025 [data-report-id="archive-monthly"]')).toBeVisible();
});

test('浮层保留阅读位置，日历支持七天范围和月份，Esc分层关闭且外部首次点击不穿透',async({page})=>{
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/');await openReports(page);
  await page.locator('.report-sources summary').first().click();
  await page.locator('#reportScrollArea').evaluate(node=>node.scrollTop=150);
  const scroll=await page.locator('#reportScrollArea').evaluate(node=>node.scrollTop);
  await page.locator('#createReportBtn').click();
  await expect(page.locator('#reportCreationPopover')).toBeVisible();
  await expect(page.locator('#reportContent #reportCreationForm')).toHaveCount(0);
  await page.getByRole('combobox',{name:'报告类型',exact:true}).click();
  await page.getByRole('option',{name:'周报',exact:true}).click();
  await page.locator('[data-calendar-day="2026-10-21"]').click();
  await expect(page.locator('#reportSelection')).toHaveValue('2026-10-21');
  await expect(page.locator('#reportCalendar .calendar-in-range')).toHaveCount(6);
  await expect(page.locator('[data-calendar-day="2026-10-24"]')).toBeDisabled();
  await page.locator('[data-calendar-day="2026-10-21"]').press('ArrowLeft');
  await expect(page.locator('[data-calendar-day="2026-10-20"]')).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('#reportSelection')).toHaveValue('2026-10-20');
  await page.getByRole('combobox',{name:'报告类型',exact:true}).click();
  await page.getByRole('option',{name:'月报',exact:true}).click();
  await expect(page.locator('#reportCalendar [role="gridcell"]')).toHaveCount(12);
  await page.locator('#reportCalendar [data-calendar-month="-1"]').click();
  await page.locator('[data-calendar-value="2025-12"]').click();
  await expect(page.locator('#reportSelection')).toHaveValue('2025-12');
  await page.getByRole('combobox',{name:'报告类型',exact:true}).click();
  await page.keyboard.press('Escape');
  await expect(page.locator('#reportCreationPopover')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('#createReportBtn')).toBeFocused();
  await expect(page.locator('#reportCreationPopover')).toBeHidden();
  await expect(page.locator('#reportContent h1')).toHaveText('合成晚报');
  await expect(page.locator('.report-sources[open]')).toHaveCount(1);
  expect(await page.locator('#reportScrollArea').evaluate(node=>node.scrollTop)).toBe(scroll);
  await page.locator('#createReportBtn').click();
  await expect(page.locator('#reportSelection')).toHaveValue('2025-12');
  await page.locator('[data-report-kind="weekly"]').click();
  await expect(page.locator('#reportCreationPopover')).toBeHidden();
  await expect(page.locator('[data-report-kind="daily"]')).toHaveAttribute('aria-pressed','true');
  await page.locator('[data-report-kind="weekly"]').click();
  await expect(page.locator('[data-report-kind="weekly"]')).toHaveAttribute('aria-pressed','true');
  expect(errors).toEqual([]);
});

for(const width of [320,390,1440]) test('新增浮层不占阅读栏且短屏所有控件可到达 '+width,async({page})=>{
  await page.setViewportSize({width,height:width===320?568:900});
  await page.goto('/');await openReports(page);
  await expect(page.locator('#createReportBtn svg')).toBeVisible();
  await expect(page.locator('#createReportBtn')).toHaveAttribute('aria-label','新增日报');
  await page.locator('#createReportBtn').click();
  await expect(page.locator('#reportCalendar')).toBeVisible();
  await expect(page.getByRole('button',{name:'关闭新增日报',exact:true})).toHaveCount(0);
  await expect(page.locator('#reportCreationPopover .popover-heading button')).toHaveCount(0);
  await expect(page.locator('#reportCreationModel')).toContainText(/Pi ·|Codex ·/);
  for (const kind of ['早报','午报','晚报']) {
    await choosePicker(page,'报告类型',kind);
    await expect(page.locator('#reportSelection')).toHaveValue('2026-10-30');
    await expect(page.locator('[data-calendar-day="2026-10-30"]')).toBeEnabled();
    await expect(page.locator('[data-calendar-day="2026-10-30"]')).toHaveAttribute('aria-selected','true');
  }
  await expect(page.locator('#reportCreationError')).toContainText('时间范围尚未结束');
  await page.screenshot({path:`output/report-creation-check/today-${width}.png`});
  if(width<921) await expect(page.locator('#feedView')).not.toHaveClass(/mobile-reader-active/);
  const box=await page.locator('#reportCreationPopover').boundingBox();
  expect(box.x).toBeGreaterThanOrEqual(0);expect(box.x+box.width).toBeLessThanOrEqual(width);
  expect(box.y+box.height).toBeLessThanOrEqual(width===320?568:900);
  await page.getByRole('button',{name:'开始生成',exact:true}).scrollIntoViewIfNeeded();
  await expect(page.getByRole('button',{name:'开始生成',exact:true})).toBeInViewport();
  await page.getByRole('combobox',{name:'报告类型',exact:true}).click();
  await page.getByRole('option',{name:'月报',exact:true}).click();
  await expect(page.locator('#reportSelection')).toBeVisible();
  expect(await page.locator('#reportCreationPopover').evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
  await page.getByRole('button',{name:'取消',exact:true}).click();
  await expect(page.locator('#reportCreationPopover')).toBeHidden();
});


test('内嵌日历不叠加原生日历，手输日期与月份仍校验有效范围',async({page})=>{
  const writes=[];page.on('request',request=>{if(request.url().endsWith('/api/reports/manual'))writes.push(request);});
  await page.goto('/');await openReports(page);await page.locator('#createReportBtn').click();
  for(const [label,invalid,valid] of [['周报','2026-02-30','2026-10-19'],['月报','2026-13','2024-02']]) {
    await page.getByRole('combobox',{name:'报告类型',exact:true}).click();
    await page.getByRole('option',{name:label,exact:true}).click();
    await expect(page.locator('#reportPeriodFields input[type="date"], #reportPeriodFields input[type="month"]')).toHaveCount(0);
    const input=page.locator('#reportSelection');
    await input.fill(invalid);
    await page.getByRole('button',{name:'开始生成',exact:true}).click();
    expect(await input.evaluate(node=>node.validationMessage)).toContain('请输入有效');
    await input.fill('0000-01'+(label==='周报'?'-01':''));
    expect(await input.evaluate(node=>node.validity.valid)).toBe(false);
    await input.fill(valid);
    expect(await input.evaluate(node=>node.validity.valid)).toBe(true);
    await expect(page.locator('#reportCalendar [aria-selected="true"]')).toHaveAttribute(label==='周报'?'data-calendar-day':'data-calendar-value',valid);
  }
  expect(writes).toHaveLength(0);
});

test('归档折叠标题悬停保持透明且无阴影，键盘焦点仍可见',async({page})=>{
  await page.goto('/');await openReports(page);
  for(const [theme,colorScheme] of [['light','light'],['dark','dark'],['system','light'],['system','dark'],['eink','light']]) {
    await page.emulateMedia({colorScheme});await chooseTheme(page,theme);
    for(const kind of ['daily','monthly']) {
      await page.locator(`[data-report-kind="${kind}"]`).click();
      const group=page.locator('[data-report-group]').first();
      if(await group.getAttribute('aria-expanded')==='true') await group.click();
      await group.hover();
      await expect(group).toHaveCSS('background-color','rgba(0, 0, 0, 0)');
      await expect(group).toHaveCSS('box-shadow','none');
      await group.press('Tab');await page.keyboard.press('Shift+Tab');
      await expect(group).toBeFocused();
      await expect(group).toHaveCSS('outline-style','solid');
    }
  }
});
