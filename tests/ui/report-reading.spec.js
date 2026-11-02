import {test,expect} from '@playwright/test';
import {openNavigation,chooseTheme,choosePicker} from './helpers.js';

async function openReports(page) {
  await openNavigation(page); await page.locator('#navReportsBtn').click();
  await page.locator('[data-report-id="report-evening"]').click();
  await expect(page.locator('#reportProse')).toBeVisible();
}
async function selectReportText(page) {
  const paragraph = page.locator('#reportProse [data-quote-paragraph]').first();
  await paragraph.scrollIntoViewIfNeeded();
  return paragraph.evaluate(node=>{
    const range=document.createRange(); range.selectNodeContents(node);
    getSelection().removeAllRanges(); getSelection().addRange(range);
    node.dispatchEvent(new MouseEvent('mouseup',{bubbles:true})); return node.textContent;
  });
}
async function openAgentModels(page) {
  await page.locator('#agentModelTrigger').click();
  await expect(page.locator('#agentModelPopover')).toBeVisible();
}
test.beforeEach(async({request})=>{
  await request.post('/__test/reset-reading');
  await request.post('/__test/reports',{data:{linked_sources:true,briefs:true}});
  await request.delete('/api/news/report:report-evening/agent/session');
  const reader=(await (await request.get('/api/reports/report-evening')).json()).report.reader;
  for(const row of reader.highlight_surfaces.original.highlights) await request.delete('/api/news/report:report-evening/highlights/'+row.id);
  for(const row of reader.notes) await request.delete('/api/news/report:report-evening/notes/'+row.id);
});

for(const width of [390,1440]) test('日报保留高亮与引用 Agent、独立草稿，不提供写想法入口 '+width,async({page,request})=>{
  await page.setViewportSize({width,height:900});
  await page.goto('/'); await openReports(page);
  const text=await selectReportText(page);
  await page.locator('#highlightSelectionBtn').click();
  await expect(page.locator('#reportProse mark')).not.toHaveCount(0);
  const saved=(await (await request.get('/api/reports/report-evening')).json()).report.reader;
  expect(saved.highlight_surfaces.original.highlights[0].selected_text).toBe(text);
  await page.reload(); await openReports(page);
  await expect(page.locator('#reportProse mark')).not.toHaveCount(0);
  await selectReportText(page);
  await expect(page.locator('#quoteNoteBtn')).toBeHidden();
  await expect(page.locator('#reportContent .research-notes, #reportNotesMount')).toHaveCount(0);
  await selectReportText(page); await page.locator('#quoteAgentBtn').click();
  await expect(page.locator('#agentQuote')).toContainText(text);
  await expect(page.locator('#archiveAgentBtn')).toBeHidden();
  await expect(page.locator('#agentSendBtn')).toBeEnabled();
  await page.locator('#agentPromptInput').fill('日报草稿');
  await page.locator('#agentDockToggleBtn').click();
  await page.locator('.report-sources summary').first().click();
  await page.locator('[data-source-item="ui-000"]').first().click();
  await expect(page.locator('#detailPanelFeed')).toHaveClass(/active/);
  await expect(page.locator('#quoteNoteBtn')).not.toHaveAttribute('hidden');
  await page.locator('#agentDockToggleBtn').click();
  await expect(page.locator('#archiveAgentBtn')).toBeVisible();
  await expect(page.locator('#agentPromptInput')).toHaveValue('');
  await page.locator('#agentDockToggleBtn').click();
  await page.locator(width === 390 ? '#mobileReaderBackBtn' : '#topicReaderBackBtn').click();
  await expect(page.locator('.report-sources').first()).toHaveJSProperty('open',true);
  await page.locator('#agentDockToggleBtn').click();
  await expect(page.locator('#archiveAgentBtn')).toBeHidden();
  await expect(page.locator('#agentPromptInput')).toHaveValue('日报草稿');
  await expect(page.locator('#agentQuote')).toContainText(text);
  await page.locator('#agentSendBtn').click();
  await expect(page.locator('#agentChatStream .user')).toContainText('日报草稿');
  const session=(await (await request.get('/api/news/report:report-evening/agent/session')).json());
  expect(session.messages[0].quote_text).toBe(text);
  await request.post('/api/news/report:report-evening/agent/jobs/'+session.jobs[0].id+'/stop');
});

test('日报来源状态同步、失败恢复、连续点击及返回位置',async({page,request})=>{
  await request.patch('/api/news/ui-000/state',{data:{important:false,read_later:false,read_later_done:true}});
  const before=(await (await request.get('/api/news/ui-000/detail')).json());
  await page.goto('/'); await openReports(page);
  const details=page.locator('.report-sources').first(); await details.locator('summary').click();
  const important=details.locator('[data-source-state="important"]').first();
  await page.route('**/api/news/ui-000/state', route=>route.fulfill({status:500,json:{ok:false,error:'test_failure'}}));
  await important.click(); await expect(important).toHaveAttribute('aria-pressed','false');
  await expect(page.locator('#toast')).toBeVisible(); await page.unroute('**/api/news/ui-000/state');
  expect((await (await request.get('/api/news/ui-000/detail')).json()).read_at).toBe(before.read_at);
  await important.click(); await expect(important).toHaveAttribute('aria-busy','false');
  await expect(important).toHaveAttribute('aria-pressed','true');
  const acknowledged=(await (await request.get('/api/news/ui-000/detail')).json());
  expect(acknowledged.read_at).toBeTruthy();
  expect(acknowledged.read_later_done_at).toBe(before.read_later_done_at);
  const scroll=await page.locator('#reportScrollArea').evaluate(node=>node.scrollTop);
  await details.locator('[data-source-item="ui-000"]').click();
  await expect(page.locator('#detailPanelFeed [data-state="important"]')).toHaveAttribute('aria-pressed','true');
  await page.keyboard.press('m');
  expect((await (await request.get('/api/news/ui-000/detail')).json()).read_at).toBe(acknowledged.read_at);
  await page.locator('#detailPanelFeed [data-state="important"]').click();
  await page.locator('#topicReaderBackBtn').click();
  await expect(important).toHaveAttribute('aria-pressed','false');
  expect(await page.locator('#reportScrollArea').evaluate(node=>node.scrollTop)).toBe(scroll);
  expect((await (await request.get('/api/news/ui-000/detail')).json()).read_at).toBe(acknowledged.read_at);
  await important.click({clickCount:3}); await expect(important).toHaveAttribute('aria-busy','false');
  const state=(await (await request.get('/api/news/ui-000/detail')).json());
  await expect(important).toHaveAttribute('aria-pressed',String(!!state.important_at));
  const later=details.locator('[data-source-state="read_later"]').first();
  await later.click(); await expect(later).toHaveAttribute('aria-busy','false');
  const changed=(await (await request.get('/api/news/ui-000/detail')).json());
  expect(changed.read_at).toBeTruthy(); expect(changed.read_later_at).toBeTruthy();
  // News cards and reports use the same two flags in both directions, including reload.
  if (await important.getAttribute('aria-pressed') !== 'true') await important.click();
  await expect(important).toHaveAttribute('aria-busy','false');
  await page.locator('#navFeedBtn').click();
  await page.locator('#feedColFeed [data-filter="all"]').click();
  await page.locator('#feedSearchInput').fill('美联储');
  const card=page.locator('#feedCardList [data-id="ui-000"]');
  await expect(card.locator('[data-state="important"]')).toHaveAttribute('aria-pressed','true');
  await expect(card.locator('[data-state="read_later"]')).toHaveAttribute('aria-pressed','true');
  await card.locator('[data-state="important"]').click();
  await card.locator('[data-state="read_later"]').click();
  await expect(card.locator('[aria-busy="true"]')).toHaveCount(0);
  await openReports(page); await page.locator('.report-sources summary').first().click();
  await expect(important).toHaveAttribute('aria-pressed','false');
  await expect(later).toHaveAttribute('aria-pressed','false');
  await page.reload(); await openReports(page); await page.locator('.report-sources summary').first().click();
  await expect(important).toHaveAttribute('aria-pressed','false');
  await expect(later).toHaveAttribute('aria-pressed','false');
});

for(const width of [390,1440]) test('Agent 面板直接切换模型与推理，保留历史且不修改默认设置 '+width,async({page,request})=>{
  await page.setViewportSize({width,height:900});
  const original=await (await request.get('/api/settings')).json();
  await page.goto('/'); await openReports(page); await page.locator('#agentDockToggleBtn').click();
  await expect(page.locator('#agentDock [role="combobox"]')).toHaveCount(0);
  await openAgentModels(page);
  await expect(page.getByRole('combobox',{name:'Agent 模型',exact:true})).toBeEnabled();
  await choosePicker(page,'Agent 模型','comparison-model · test');
  await expect(page.locator('#agentRuntimeBadge')).toContainText('comparison-model');
  await expect(page.getByRole('combobox',{name:'Agent 推理程度',exact:true})).toBeEnabled();
  await choosePicker(page,'Agent 推理程度','high');
  await expect.poll(async()=> (await (await request.get('/api/news/report:report-evening/agent/session')).json()).session.reasoning_effort).toBe('high');
  await page.locator('[data-agent-model-close]').click();
  await expect(page.locator('#agentModelTrigger')).toHaveAttribute('title','当前会话模型：comparison-model · high');
  await expect(page.locator('#agentModelTrigger')).toHaveText('');
  await page.locator('#agentPromptInput').fill('选择后的问题'); await page.locator('#agentSendBtn').click();
  await expect(page.locator('#agentChatStream .user')).toContainText('选择后的问题');
  await expect(page.locator('#agentModelTrigger')).toBeDisabled();
  const session=(await (await request.get('/api/news/report:report-evening/agent/session')).json());
  expect(session.jobs[0].model).toBe('comparison-model');
  await page.locator('#agentSendBtn').click();
  await openAgentModels(page);
  await expect(page.getByRole('combobox',{name:'Agent Runtime',exact:true})).toBeEnabled();
  await choosePicker(page,'Agent Runtime','Codex');
  await expect(page.getByRole('combobox',{name:'Agent 模型',exact:true})).toBeEnabled();
  await choosePicker(page,'Agent 模型','ui-catalog-a');
  await expect(page.locator('#agentRuntimeBadge')).toContainText('ui-catalog-a');
  await expect(page.locator('#agentChatStream .user')).toContainText('选择后的问题');
  const settings=await (await request.get('/api/settings')).json();
  expect(settings.agent).toEqual(original.agent);
  expect(settings.llm.pi_chat).toEqual(original.llm.pi_chat);
  expect(settings.feature_models).toEqual(original.feature_models);
  // Nested menus, Escape and outside presses close only the relevant surface.
  await page.getByRole('combobox',{name:'Agent 模型',exact:true}).click();
  await page.keyboard.press('Escape');
  await expect(page.locator('#agentModelPopover')).toBeVisible();
  await expect(page.getByRole('listbox')).toBeHidden();
  await page.keyboard.press('Escape');
  await expect(page.locator('#agentModelPopover')).toBeHidden();
  await expect(page.locator('#agentModelTrigger')).toBeFocused();
  await openAgentModels(page);
  await page.locator('#agentPromptInput').click();
  await expect(page.locator('#agentModelPopover')).toBeHidden();
  await expect(page.locator('#agentDock')).not.toHaveClass(/minimized/);
  await expect(page.locator('#agentPromptInput')).not.toBeFocused();
  await page.locator('#agentPromptInput').click();
  await expect(page.locator('#agentPromptInput')).toBeFocused();
});

for(const width of [390,1440]) for(const theme of ['light','dark','system-light','system-dark','eink']) test('日报来源与 Agent 主题字号 '+width+' '+theme,async({page})=>{
  await page.setViewportSize({width,height:900}); await page.emulateMedia({colorScheme:theme==='system-dark'?'dark':'light'});
  await page.goto('/'); await chooseTheme(page,theme.startsWith('system')?'system':theme); await openReports(page);
  await page.locator('.report-sources summary').first().click();
  await page.getByRole('combobox',{name:'阅读字号',exact:true}).click(); await page.getByRole('option',{name:/特大/}).click();
  await expect(page.locator('.report-sources[open] [data-source-state="important"]').first()).toBeVisible();
  const source=page.locator('.report-sources[open] .reader-source-open').first();
  await expect(source).toHaveCSS('text-decoration-line','none');
  await source.hover(); await expect(source).toHaveCSS('text-decoration-line','none');
  expect(await page.locator('#reportContent').evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
  await page.screenshot({path:'.ui-artifacts/v046-sources-'+width+'-'+theme+'.png'});
  await page.locator('#agentDockToggleBtn').click();
  await expect(page.locator('#agentModelTrigger')).toBeEnabled();
  await expect(page.locator('#agentDock [role="combobox"]')).toHaveCount(0);
  await expect(page.locator('#agentPromptInput')).toHaveAttribute('rows','1');
  await expect(page.locator('#agentModelTrigger')).toHaveText('');
  const input=await page.locator('#agentPromptInput').boundingBox(), send=await page.locator('#agentSendBtn').boundingBox(), model=await page.locator('#agentModelTrigger').boundingBox();
  expect(send.height).toBeCloseTo(input.height,0);
  expect(model.width).toBeLessThanOrEqual(32);
  expect(model.x).toBeGreaterThan(input.x+input.width);
  expect(model.x+model.width).toBeLessThan(send.x);
  expect(Math.abs(model.y+model.height/2-send.y-send.height/2)).toBeLessThan(1);
  expect(await page.locator('#agentModelControls').evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
  await page.screenshot({path:'.ui-artifacts/v046-'+width+'-'+theme+'.png'});
  await openAgentModels(page);
  await expect(page.getByRole('combobox',{name:'Agent 模型',exact:true})).toBeEnabled();
  await expect(page.locator('[data-agent-model-close]')).toBeInViewport();
  expect(await page.locator('#agentModelPopover').evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
  await page.screenshot({path:'.ui-artifacts/v046-models-'+width+'-'+theme+'.png'});
});

test('周报回复通知从新闻流进入正确报告并定位回复',async({page,request})=>{
  await request.post('/__test/reset-agent');
  const completed=await request.post('/__test/agent-complete',{data:{item_id:'report:report-weekly',answer:'周报中的独立回复'}});
  expect(completed.ok()).toBe(true);
  const job=(await completed.json()).job_id;
  await page.goto('/');
  await page.locator('#agentNotificationsBtn').click();
  await page.locator(`[data-open-notice="${job}"]`).click();
  await expect(page.locator('#detailPanelReports')).toHaveClass(/active/);
  await expect(page.locator('[data-report-kind="weekly"]')).toHaveAttribute('aria-pressed','true');
  await expect(page.locator('#agentChatStream')).toContainText('周报中的独立回复');
  await expect.poll(async()=>(await (await request.get('/api/agent/notifications')).json()).notifications.length).toBe(0);
});

test('模型目录失败可重试，切换阅读目标恢复可用目录',async({page})=>{
  let fail=true;
  await page.route(/\/api\/news\/report(?:%3A|:)report-evening\/agent\/model-options/,async route=>{
    if(fail) await route.fulfill({status:503,json:{ok:false,error:'report_model_catalog_failed'}});
    else await route.continue();
  });
  await page.goto('/'); await openReports(page); await page.locator('#agentDockToggleBtn').click();
  await openAgentModels(page);
  await expect(page.locator('#agentModelPopover')).toContainText('模型目录读取失败');
  fail=false; await page.locator('[data-agent-model-retry]').click();
  await expect(page.getByRole('combobox',{name:'Agent 模型',exact:true})).toBeEnabled();
  await page.locator('[data-agent-model-close]').click();
  await page.locator('#agentDockToggleBtn').click();
  await page.locator('.report-sources summary').first().click();
  await page.locator('[data-source-item="ui-000"]').first().click();
  await page.locator('#agentDockToggleBtn').click();
  await openAgentModels(page);
  await expect(page.getByRole('combobox',{name:'Agent 模型',exact:true})).toBeEnabled();
  await expect(page.locator('#agentModelPopover')).not.toContainText('模型目录读取失败');
});

test('切换 Runtime 等待目录时阻止旧模型发送，完成后使用新选择',async({page})=>{
  let release, entered;
  const gate=new Promise(resolve=>release=resolve), entry=new Promise(resolve=>entered=resolve);
  await page.route(/\/agent\/model-options\?.*runtime=codex/,async route=>{entered(); await gate; await route.continue();});
  await page.goto('/'); await openReports(page); await page.locator('#agentDockToggleBtn').click();
  await page.locator('#agentPromptInput').fill('切换后再发送');
  await openAgentModels(page);
  await expect(page.getByRole('combobox',{name:'Agent Runtime',exact:true})).toBeEnabled();
  await choosePicker(page,'Agent Runtime','Codex'); await entry;
  await expect(page.locator('#agentSendBtn')).toBeDisabled();
  release();
  await expect(page.locator('#agentRuntimeBadge')).toContainText('codex');
  await expect(page.locator('#agentSendBtn')).toBeEnabled();
  await expect(page.locator('#agentPromptInput')).toHaveValue('切换后再发送');
});
