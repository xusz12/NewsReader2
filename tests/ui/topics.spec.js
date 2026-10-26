import { test, expect } from "@playwright/test";
import { chooseTheme, openSettings, answerConfirm } from "./helpers.js";

const styles = (locator, properties) => locator.evaluate((node, keys) => {
  const css = getComputedStyle(node);
  return Object.fromEntries(keys.map(key => [key, css.getPropertyValue(key)]));
}, properties);
const typography = ['font-family','font-size','font-weight','line-height','letter-spacing','color'];
const controlStyle = ['font-family','font-size','padding-top','padding-right','padding-bottom','padding-left','border-radius','border-top-width','background-color'];

async function openTopics(page) {
  if (await page.locator("#openNavBtn").isVisible()) await page.locator("#openNavBtn").click();
  await page.locator("#navTopicsBtn").click();
  await expect(page.locator("#feedColTopics")).toHaveClass(/active/);
}
async function openDefinitionEditor(page) {
  const details=page.locator('#topicDraftDetails');
  await expect(details).toBeVisible({timeout:15000});
  if(!await details.evaluate(n=>n.open))await details.locator('summary').click();
}
async function createDefinitionDraft(page,request) {
  await page.goto('/');await openTopics(page);
  await page.locator('#newTopicBtn').click();
  await page.locator('[name="query"]').fill('云杉芯片上市');
  await page.getByRole('button',{name:'搜索并生成定义'}).click();
  await expect(page.getByRole('log',{name:'主题定义对话'})).toBeVisible({timeout:15000});
  return (await (await request.get('/api/topics')).json()).topics.find(t=>t.status==='draft').id;
}
test.beforeEach(async ({request}) => {
  await request.post("/__test/topics");
});

test('主题概览精简重复信息，新闻数量随筛选显示且归档保留报道',async({page,request})=>{
  await page.goto('/');await openTopics(page);await page.locator('[data-topic-id="test-acquisition"]').click();
  const detail=await (await request.get('/api/topics/test-acquisition')).json();
  await expect(page.locator('#topicContent .status-pill')).toHaveText('持续跟踪中');
  await expect(page.locator('#topicContent > .section-stack > .settings-section-card')).toHaveCount(3);
  await expect(page.locator('#topicToolbar')).not.toContainText('持续跟踪');
  await expect(page.locator('[data-topic-stat]')).toHaveCount(0);
  await expect(page.locator('#topicHistoryTitle .section-title-label')).toHaveText('重要进展');
  await expect(page.locator('#topicHistoryTitle .reader-action-toolbar')).not.toContainText('重要进展');
  await expect(page.locator('#topicNewsTitle')).toHaveText('新闻时间线 '+detail.total+' 条');
  await expect(page.locator('#topicNewsTitle')).not.toContainText('最新在前');
  await expect(page.locator('#topicContent header')).toContainText(detail.topic.definition);
  await expect(page.locator('.timeline-flow .feed-date-heading')).toHaveCount(1);
  await page.locator('.timeline-flow').scrollIntoViewIfNeeded();
  await page.screenshot({path:'.ui-artifacts/topics-news-timeline.png'});
  await page.locator('.topic-node').first().click();
  await expect(page.locator('.topic-news')).toHaveCount(1);
  await expect(page.locator('#topicNewsTitle')).toContainText('相关报道 1 条');
  await page.locator('#topicArchiveBtn').click();
  await expect(page.locator('#topicContent .status-pill')).toHaveText('已归档');
  await page.locator('[data-topic-action="all"]').click();
  await expect(page.locator('#topicNewsTitle')).toHaveText('新闻时间线 '+detail.total+' 条');
});

for(const width of [390,1280]) test('确认定义支持多轮对话、刷新恢复及明确确认 '+width,async({page,request})=>{
  await page.setViewportSize({width,height:900});
  const id=await createDefinitionDraft(page,request), input=page.getByRole('textbox',{name:'讨论主题定义'});
  const log=page.getByRole('log',{name:'主题定义对话'}), confirm=page.getByRole('button',{name:'确认并整理历史'});
  await expect(log).toContainText('是否确认按这一定义继续');
  await expect(log.locator('.agent-msg')).toHaveCount(2);
  await input.fill('为什么不追踪股价？');await expect(confirm).toBeDisabled();
  // Composition and mobile Enter must not submit incomplete text.
  await input.dispatchEvent('keydown',{key:'Enter',isComposing:true});
  await expect(log.locator('.agent-msg')).toHaveCount(2);
  if(width===390){await input.press('Enter');await expect(input).toHaveValue('为什么不追踪股价？\n');}
  const sent=page.waitForResponse(r=>r.url().endsWith('/messages') && r.request().method()==='POST');
  await input.press(width===390?'Control+Enter':'Enter');await sent;
  await expect(confirm).toBeDisabled();
  await expect(log).toContainText('一般股价波动不属于上市审核进展',{timeout:15000});
  await expect(log.locator('.agent-msg')).toHaveCount(4);await expect(confirm).toBeEnabled();
  const original=await (await request.get('/api/topics/'+id)).json();
  expect(original.topic.status).toBe('draft');expect(original.nodes).toHaveLength(0);
  await input.fill('仅追踪审核');await page.locator('#topicChatForm button').click();
  await expect(log).toContainText('已缩小到审核进程',{timeout:15000});
  await expect(log.locator('.agent-msg')).toHaveCount(6);
  await openDefinitionEditor(page);
  await expect(page.locator('[name="definition"]')).toHaveValue('仅追踪云杉芯片的审核进程');
  await page.locator('#topicDraftDetails summary').click();
  await page.reload();await openTopics(page);await page.locator('[data-topic-id="'+id+'"]').click();
  await expect(log.locator('.agent-msg')).toHaveCount(6);
  expect((await (await request.get('/api/topics/'+id)).json()).topic.status).toBe('draft');
  await page.screenshot({path:'.ui-artifacts/topics-definition-chat-'+width+'.png'});
  await confirm.click();
  await expect(page.getByRole('heading',{name:'主题已创建'})).toBeVisible({timeout:15000});
  const complete=await (await request.get('/api/topics/'+id)).json();
  expect(complete.topic.definition).toBe('仅追踪云杉芯片的审核进程');expect(complete.items).toHaveLength(0);
});

test('定义回复失败可重试，轮询保留下一条输入与光标',async({page,request})=>{
  const id=await createDefinitionDraft(page,request),input=page.locator('#topicChatInput');
  await input.fill('修订失败');await page.locator('#topicChatForm button').click();
  await expect(page.locator('.topic-error')).toBeVisible({timeout:15000});
  await expect(page.getByRole('button',{name:'确认并整理历史'})).toBeDisabled();
  await expect(page.locator('#topicChatStream .agent-msg')).toHaveCount(3);
  await page.locator('[data-topic-action="retry"]').click();
  await input.fill('尚未发送的后续问题');
  // Resolve and move the caret in one browser task: polling can replace the
  // textarea between Playwright's locator resolution and evaluate invocation.
  await page.evaluate(()=>document.querySelector('#topicChatInput').setSelectionRange(4,4));
  const refreshed=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/topics/'+id);
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));await refreshed;
  await expect(page.locator('#topicChatStream .agent-msg')).toHaveCount(4,{timeout:15000});
  await expect(input).toHaveValue('尚未发送的后续问题');await expect(input).toBeFocused();
  expect(await input.evaluate(n=>n.selectionStart)).toBe(4);
  expect((await (await request.get('/api/topics/'+id)).json()).messages.filter(m=>m.content==='修订失败')).toHaveLength(1);
  await input.fill('');await expect(page.getByRole('button',{name:'确认并整理历史'})).toBeEnabled();
});

test('其他窗口修订定义时保留本地编辑并阻止确认旧稿',async({page,request})=>{
  const id=await createDefinitionDraft(page,request),path='/api/topics/'+id;
  const current=(await (await request.get(path)).json()).topic;
  await openDefinitionEditor(page);await page.locator('[name="definition"]').fill('本地未发送的修改');
  await request.post(path+'/messages',{data:{request_id:'remote',message:'仅追踪审核',rules_revision:current.rules_revision,definition:current}});
  await expect.poll(async()=>(await (await request.get(path)).json()).job.status,{timeout:15000}).toBe('done');
  const refreshed=page.waitForResponse(r=>new URL(r.url()).pathname===path);
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));await refreshed;
  await expect(page.locator('[name="definition"]')).toHaveValue('本地未发送的修改');
  await expect(page.getByRole('button',{name:'确认并整理历史'})).toBeDisabled();
  await page.getByRole('button',{name:'载入最新定义'}).click();
  await expect(page.locator('[name="definition"]')).toHaveValue('仅追踪云杉芯片的审核进程');
  await expect(page.getByRole('button',{name:'确认并整理历史'})).toBeEnabled();
});

for(const width of [390,1280]) test('定义对话沿用 Agent 样式与阅读字号 '+width,async({page,request})=>{
  await page.setViewportSize({width,height:900});await createDefinitionDraft(page,request);
  const input=page.locator('#topicChatInput');
  for(const [theme,scheme] of [['light','light'],['dark','dark'],['eink','light'],['system','light'],['system','dark']]) {
    await page.emulateMedia({colorScheme:scheme});await chooseTheme(page,theme);
    expect(await styles(input,controlStyle)).toEqual(await styles(page.locator('#agentPromptInput'),controlStyle));
    expect(await styles(page.locator('#topicChatForm button'),controlStyle)).toEqual(await styles(page.locator('#agentSendBtn'),controlStyle));
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  }
  const bubble=page.locator('#topicChatStream .assistant .agent-bubble').first();
  const base=parseFloat((await styles(bubble,['font-size']))['font-size']);
  const inputSize=(await styles(input,['font-size']))['font-size'];
  await page.evaluate(()=>document.documentElement.style.setProperty('--reading-scale','1.3'));
  expect(parseFloat((await styles(bubble,['font-size']))['font-size'])).toBeCloseTo(base*1.3,1);
  expect((await styles(input,['font-size']))['font-size']).toBe(inputSize);
  await page.screenshot({path:'.ui-artifacts/topics-chat-theme-'+width+'.png'});
});

test("主题搜索匹配名称与定义并叠加归档筛选",async({page,request})=>{
  await request.post('/api/topics/test-industry/archive',{data:{archived:true}});
  await page.goto('/');await openTopics(page);
  const input=page.getByRole('textbox',{name:'搜索主题',exact:true});
  const cards=page.locator('.topic-card');
  await expect(input).toBeVisible();
  await expect(page.locator('#topicSearchToggleBtn')).toBeHidden();
  await input.fill('北辰');await expect(cards).toHaveCount(1);
  await expect(cards.first()).toHaveAttribute('data-topic-id','test-acquisition');
  await input.fill('监管 审查');await expect(cards).toHaveCount(2);
  await page.locator('[data-topic-filter="tracking"]').click();await expect(cards).toHaveCount(1);
  await expect(cards.first()).toHaveAttribute('data-topic-id','test-acquisition');
  await page.locator('[data-topic-filter="archived"]').click();await expect(cards).toHaveCount(1);
  await expect(cards.first()).toHaveAttribute('data-topic-id','test-industry');
  await input.fill('北辰');await expect(cards).toHaveCount(0);
  await expect(page.locator('#topicCardList')).toContainText('没有匹配的主题');
  await input.fill('');await expect(cards).toHaveCount(1);
  await expect(page.locator('[data-topic-filter="archived"]')).toHaveAttribute('aria-pressed','true');
  await page.locator('#feedColTopics .feed-header').screenshot({path:'.ui-artifacts/topics-search-desktop.png'});
});

for (const width of [390,1280]) test('创建后编辑定义与横向规则，保留已有资料 '+width,async({page,request})=>{
  await page.setViewportSize({width,height:900});await page.goto('/');await openTopics(page);
  await page.locator('[data-topic-id="test-acquisition"]').click();
  const before=await (await request.get('/api/topics/test-acquisition')).json();
  await page.locator('#topicDefinitionBtn').click();
  await expect(page.locator('#topicCreationSteps')).toBeHidden();
  await expect(page.locator('[name="timeline_mode"],[name="start_date"]')).toHaveCount(0);
  await page.locator('[name="definition"]').fill('只追踪正式监管决定及交易完成');
  await page.locator('[name="horizontal_rule"]').fill('仅监管最终决定、交割或终止；重复报道不新增');
  const refreshed=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/topics/test-acquisition');
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));await refreshed;
  await expect(page.locator('[name="definition"]')).toHaveValue('只追踪正式监管决定及交易完成');
  await page.screenshot({path:'.ui-artifacts/topics-edit-definition-'+width+'.png'});
  await page.getByRole('button',{name:'保存修改',exact:true}).click();
  await expect(page.locator('#topicDefinitionForm')).toHaveCount(0);
  const after=await (await request.get('/api/topics/test-acquisition')).json();
  expect(after.topic.rules_revision).toBe(before.topic.rules_revision+1);
  expect(after.topic.definition).toBe('只追踪正式监管决定及交易完成');
  expect(after.nodes).toEqual(before.nodes);expect(after.items).toEqual(before.items);
  await page.locator('#topicDefinitionBtn').click();
  await expect(page.locator('[name="horizontal_rule"]')).toHaveValue('仅监管最终决定、交割或终止；重复报道不新增');
  await page.locator('[name="definition"]').fill('这次取消');
  await page.getByRole('button',{name:'取消',exact:true}).click();
  await page.locator('#topicDefinitionBtn').click();
  await expect(page.locator('[name="definition"]')).toHaveValue(after.topic.definition);
  await page.getByRole('button',{name:'取消',exact:true}).click();
});

test('定义并发修改不覆盖另一窗口，冲突保留输入',async({page,request})=>{
  await page.goto('/');await openTopics(page);await page.locator('[data-topic-id="test-acquisition"]').click();
  await page.locator('#topicDefinitionBtn').click();
  const current=await (await request.get('/api/topics/test-acquisition')).json();
  await request.patch('/api/topics/test-acquisition/definition',{data:{...current.topic,definition:'另一窗口保存的定义'}});
  await page.locator('[name="definition"]').fill('当前窗口尚未保存的输入');
  await page.getByRole('button',{name:'保存修改',exact:true}).click();
  await expect(page.locator('.topic-form-error')).toContainText('定义已在其他页面修改');
  await expect(page.locator('[name="definition"]')).toHaveValue('当前窗口尚未保存的输入');
  expect((await (await request.get('/api/topics/test-acquisition')).json()).topic.definition).toBe('另一窗口保存的定义');
  const refreshed=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/topics/test-acquisition');
  await page.getByRole('button',{name:'取消',exact:true}).click();await refreshed;
  await page.locator('#topicDefinitionBtn').click();
  await expect(page.locator('[name="definition"]')).toHaveValue('另一窗口保存的定义');
});

test("主题搜索与新建输入分离，刷新不覆盖右栏或草稿",async({page})=>{
  await page.goto('/');await openTopics(page);
  await page.locator('[data-topic-id="test-acquisition"]').click();
  const input=page.locator('#topicSearchInput');
  await input.fill('并购');
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await expect(page.locator('#topicContent h1')).toHaveText('北辰科技收购远川数据');
  await expect(page.locator('.topic-card')).toHaveCount(1);
  await page.locator('#newTopicBtn').click();
  await page.locator('[name="query"]').fill('保留主题创建草稿');
  await input.fill('没有此主题');
  const refreshed=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/topics');
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));await refreshed;
  await expect(page.locator('[name="query"]')).toHaveValue('保留主题创建草稿');
  await expect(input).toHaveValue('没有此主题');
  await input.fill('');await expect(page.locator('.topic-card')).toHaveCount(2);
  await expect(page.locator('[name="query"]')).toHaveValue('保留主题创建草稿');
});

for (const width of [320,390]) test('手机主题搜索沿用展开、快捷键与收起行为 '+width,async({page})=>{
  await page.setViewportSize({width,height:844});await page.goto('/');await openTopics(page);
  const input=page.locator('#topicSearchInput'), toggle=page.locator('#topicSearchToggleBtn');
  await expect(input).toBeHidden();
  await expect(page.locator('#topicSearchRow')).toHaveJSProperty('inert',true);
  await page.locator('[data-topic-filter="tracking"]').click();
  await toggle.click();await expect(input).toBeFocused();
  await input.fill('北辰');await expect(page.locator('.topic-card')).toHaveCount(1);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.locator('#feedColTopics .feed-header').screenshot({path:'.ui-artifacts/topics-search-mobile-'+width+'.png'});
  await page.locator('.topic-card').click();await page.locator('.topic-news-open').first().click();
  await page.keyboard.press('/');await expect(input).toBeFocused();
  await expect(page.locator('#feedColTopics')).toBeVisible();await expect(input).toHaveValue('北辰');
  await input.press('Escape');await expect(input).toBeHidden();await expect(toggle).toBeFocused();
  await expect(input).toHaveValue('');await expect(page.locator('.topic-card')).toHaveCount(2);
  await expect(page.locator('[data-topic-filter="tracking"]')).toHaveAttribute('aria-pressed','true');
  await page.keyboard.press('/');await expect(input).toBeFocused();
  await input.fill('并购');await toggle.click();
  await expect(input).toHaveValue('');await expect(page.locator('.topic-card')).toHaveCount(2);
});

test('主题搜索在920与921断点同步可见性和焦点',async({page})=>{
  await page.setViewportSize({width:921,height:900});await page.goto('/');await openTopics(page);
  const input=page.locator('#topicSearchInput'), toggle=page.locator('#topicSearchToggleBtn');
  await expect(input).toBeVisible();await expect(toggle).toBeHidden();await input.focus();
  await page.setViewportSize({width:920,height:900});await expect(input).toBeHidden();await expect(toggle).toBeFocused();
  await toggle.press('Enter');await expect(input).toBeFocused();
  await page.setViewportSize({width:921,height:900});await expect(input).toBeVisible();await expect(toggle).toBeHidden();
});

test("顶部筛选、归档及恢复跟踪保留历史",async({page,request})=>{
  await page.goto('/');await openTopics(page);
  const filters=page.locator('#topicFilters');
  await expect(filters.getByRole('button')).toHaveText(['全部','持续跟踪','已归档']);
  await expect(page.locator('#newTopicBtn')).toHaveText('');
  await expect(page.locator('#newTopicBtn')).toHaveAccessibleName('新建主题');
  await filters.getByRole('button',{name:'持续跟踪',exact:true}).click();
  await page.locator('[data-topic-id="test-acquisition"]').click();
  const before=await (await request.get('/api/topics/test-acquisition')).json();
  await page.getByRole('button',{name:'归档主题',exact:true}).click();
  await expect(page.locator('.topic-card')).toHaveCount(1);
  await expect(page.locator('#topicContent h1')).toHaveText('数据基础设施并购');
  await filters.getByRole('button',{name:'已归档',exact:true}).click();
  await expect(page.locator('.topic-card')).toHaveCount(1);
  await expect(page.locator('#topicContent h1')).toHaveText('北辰科技收购远川数据');
  await expect(page.locator('#topicContent')).toContainText('已归档，停止收录新新闻');
  const after=await (await request.get('/api/topics/test-acquisition')).json();
  expect(after.items).toEqual(before.items);expect(after.nodes).toEqual(before.nodes);
  await page.reload();await openTopics(page);
  await filters.getByRole('button',{name:'已归档',exact:true}).click();
  await expect(page.locator('.topic-card')).toHaveCount(1);
  await page.getByRole('button',{name:'恢复跟踪',exact:true}).click();
  await expect(page.locator('#topicCardList')).toHaveText('暂无已归档的主题');
  await filters.getByRole('button',{name:'持续跟踪',exact:true}).click();
  await expect(page.locator('.topic-card')).toHaveCount(2);
  await expect(filters.getByRole('button',{name:'持续跟踪',exact:true})).toHaveAttribute('aria-pressed','true');
  await filters.getByRole('button',{name:'持续跟踪',exact:true}).press('Home');
  await expect(filters.getByRole('button',{name:'全部',exact:true})).toBeFocused();
  await filters.getByRole('button',{name:'全部',exact:true}).press('Enter');
  await expect(filters.getByRole('button',{name:'全部',exact:true})).toHaveAttribute('aria-pressed','true');
  await page.screenshot({path:'.ui-artifacts/topics-filters-desktop.png'});
});

test("从归档筛选新建并切换筛选仍保留草稿",async({page})=>{
  await page.goto('/');await openTopics(page);
  await page.locator('[data-topic-filter="archived"]').click();
  await page.locator('#newTopicBtn').click();
  await expect(page.locator('[data-topic-filter="archived"]')).toHaveAttribute('aria-pressed','true');
  await page.locator('[name="query"]').fill('筛选切换保留输入');
  await page.locator('[data-topic-filter="tracking"]').click();
  await expect(page.locator('.topic-card')).toHaveCount(2);
  await page.locator('#newTopicBtn').click();
  await expect(page.locator('[name="query"]')).toHaveValue('筛选切换保留输入');
  await page.getByRole('button',{name:'搜索并生成定义'}).click();
  await openDefinitionEditor(page);
  await expect(page.locator('[name="definition"]')).toBeVisible({timeout:15000});
  await page.locator('[name="definition"]').fill('手动定义不会被筛选覆盖');
  await page.locator('[data-topic-filter="tracking"]').click();
  await expect(page.locator('.topic-card')).toHaveCount(2);
  await page.locator('[data-topic-filter="all"]').click();
  await expect(page.locator('.topic-card')).toHaveCount(3);
  await page.locator('.topic-card').filter({hasText:'待确认定义'}).click();
  await expect(page.locator('[name="definition"]')).toHaveValue('手动定义不会被筛选覆盖');
});

test("360px手机顶部筛选与新建同排且支持触屏",async({browser})=>{
  const context=await browser.newContext({viewport:{width:360,height:780},isMobile:true,hasTouch:true});
  const page=await context.newPage();
  try {
    await page.goto('http://127.0.0.1:8765/');await openTopics(page);
    const filters=page.locator('#topicFilters'), create=page.locator('#newTopicBtn');
    const left=await filters.boundingBox(), right=await create.boundingBox();
    expect(left.x+left.width).toBeLessThanOrEqual(right.x);
    expect(Math.abs(left.y+left.height/2-right.y-right.height/2)).toBeLessThan(2);
    expect(await filters.evaluate(n=>n.scrollWidth<=n.clientWidth)).toBe(true);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:'.ui-artifacts/topics-filters-mobile.png'});
    await filters.getByRole('button',{name:'持续跟踪',exact:true}).tap();
    await page.locator('[data-topic-id="test-acquisition"]').tap();
    await page.getByRole('button',{name:'归档主题',exact:true}).tap();
    await page.locator('#mobileReaderBackBtn').tap();
    await filters.getByRole('button',{name:'已归档',exact:true}).tap();
    await expect(page.locator('.topic-card')).toHaveCount(1);
    await page.locator('.topic-card').tap();
    await page.getByRole('button',{name:'恢复跟踪',exact:true}).tap();
    await page.locator('#mobileReaderBackBtn').tap();
    await expect(page.locator('#topicCardList')).toHaveText('暂无已归档的主题');
    await create.tap();
    await expect(page.locator('[name="query"]')).toBeVisible();
  } finally {await context.close();}
});

test("主题、多重归属、节点筛选及现有阅读器返回", async ({page}) => {
  const errors=[];page.on("pageerror",e=>errors.push(e.message));
  await page.goto("/");
  await openTopics(page);
  await page.locator('[data-topic-id="test-acquisition"]').click();
  await expect(page.locator("#topicContent h1")).toHaveText("北辰科技收购远川数据");
  await expect(page.locator(".topic-news")).toHaveCount(2);
  await page.locator('[data-topic-node="test-acquisition-node-1"]').click();
  await expect(page.locator(".topic-news")).toHaveCount(1);
  const node=await page.locator('.topic-node[aria-pressed="true"]').getAttribute("data-topic-node");
  await page.locator(".topic-news-open").click();
  await expect(page.locator("#detailPanelFeed")).toHaveClass(/active/);
  await page.locator("#topicReaderBackBtn").click();
  await expect(page.locator("#detailPanelTopics")).toHaveClass(/active/);
  await expect(page.locator('.topic-node[aria-pressed="true"]')).toHaveAttribute("data-topic-node",node);
  await page.locator('[data-topic-action="all"]').click();
  await page.locator('[data-related-topic="test-industry"]').first().click();
  await expect(page.locator("#topicContent h1")).toHaveText("数据基础设施并购");
  await expect(page.locator(".topic-news")).toHaveCount(2);
  expect(errors).toEqual([]);
});

test("创建、编辑定义、历史整理完成后新闻流为空", async ({page,request}) => {
  await request.post("/__test/topics",{data:{empty:true}});
  await page.goto("/");
  await openTopics(page);
  await page.locator("#newTopicBtn").click();
  await page.locator('[name="query"]').fill("持续追踪云杉芯片上市");
  await page.getByRole("button",{name:"搜索并生成定义"}).click();
  await expect(page.getByRole("heading",{name:"确认你的追踪范围"})).toBeVisible({timeout:15000});
  await openDefinitionEditor(page);
  await page.locator('[name="name"]').fill("云杉上市追踪");
  await expect(page.locator('[name="timeline_mode"]')).toHaveCount(0);
  await page.locator('[name="horizontal_rule"]').fill('正式上市及审核决定，排除预测');
  await page.screenshot({path:".ui-artifacts/topics-create-desktop.png"});
  await page.getByRole("button",{name:"确认并整理历史"}).click();
  await expect(page.getByRole("heading",{name:"主题已创建"})).toBeVisible({timeout:15000});
  await expect(page.locator(".topic-success")).toContainText("从现在起的新入库新闻");
  await expect(page.locator(".topic-success")).toContainText("重要进展");
  await page.getByRole("button",{name:"查看主题"}).click();
  await expect(page.locator("#topicContent h1")).toHaveText("云杉上市追踪");
  await expect(page.locator(".topic-node")).toHaveCount(2);
  await expect(page.locator(".topic-news")).toHaveCount(0);
  await expect(page.locator("#topicContent")).toContainText("等待新的相关新闻");
  const {topics}=await (await request.get("/api/topics")).json();
  expect(topics).toHaveLength(1);
  expect(topics[0].status).toBe("ready");
});

test("失败任务明确呈现并能重试", async ({page,request}) => {
  await request.post("/__test/topics",{data:{empty:true,mode:"fail"}});
  await page.goto("/");await openTopics(page);
  await page.locator("#newTopicBtn").click();
  await page.locator('[name="query"]').fill("测试事件");
  await page.getByRole("button",{name:"搜索并生成定义"}).click();
  await expect(page.locator(".topic-error")).toBeVisible({timeout:15000});
  await request.post("/__test/topic-mode",{data:{mode:"success"}});
  await page.locator('[data-topic-action="retry"]').click();
  await expect(page.getByRole("heading",{name:"确认你的追踪范围"})).toBeVisible({timeout:15000});
  expect((await (await request.get("/api/topics")).json()).topics).toHaveLength(1);
});

test("手机从主题到新闻逐级返回，创建草稿保留", async ({page}) => {
  await page.setViewportSize({width:390,height:844});
  await page.goto("/");await openTopics(page);
  await page.locator('[data-topic-id="test-acquisition"]').click();
  await expect(page.locator("#topicContent")).toBeVisible();
  await page.locator(".topic-news-open").first().click();
  await page.locator("#mobileReaderBackBtn").click();
  await expect(page.locator("#topicContent h1")).toHaveText("北辰科技收购远川数据");
  await page.locator("#mobileReaderBackBtn").click();
  await expect(page.locator("#feedColTopics")).toBeVisible();
  await page.locator("#newTopicBtn").click();
  await page.locator('[name="query"]').fill("保留这份草稿");
  await page.locator("#mobileReaderBackBtn").click();
  await page.locator("#openNavBtn").click();
  await page.locator("#navFeedBtn").click();
  await openTopics(page);
  await page.locator("#newTopicBtn").click();
  await expect(page.locator('[name="query"]')).toHaveValue("保留这份草稿");
  await expect(page.locator("#mobileBottomNav [data-collection]")).toHaveCount(3);
});

for (const [theme,width,scheme="light"] of [["light",1280],["dark",1279],["system",921,"light"],["system",1280,"dark"],["eink",920],["light",360]]) {
  test("主题与阅读器共享布局 "+theme+" "+width+" "+scheme,async ({page,request})=>{
    await request.post('/__test/reset-reading');
    await page.setViewportSize({width,height:960});
    await page.emulateMedia({colorScheme:scheme});
    await page.goto("/");await chooseTheme(page,theme);
    await page.locator('#feedCardList .news-card').first().click();
    await expect(page.locator('#detailContent .article-title')).toBeVisible();
    const reference = {
      toolbar: await styles(page.locator('#detailPanelFeed > .detail-toolbar'), ['height','padding-left','padding-right','border-bottom-width','background-color']),
      content: await styles(page.locator('#detailScrollArea'), ['padding-top','padding-right','padding-bottom','padding-left']),
      title: await styles(page.locator('#detailContent .article-title'), typography),
      button: await styles(page.locator('#btnToggleImportant'), controlStyle),
      cardTitle: await styles(page.locator('#feedCardList .news-card-title').first(), typography),
      cardSnippet: await styles(page.locator('#feedCardList .news-card-snippet').first(), typography),
      searchBox: await styles(page.locator('#feedSearchRow .feed-search-box'), controlStyle),
      searchInput: await styles(page.locator('#feedSearchInput'), typography),
    };
    await openTopics(page);
    await page.locator('[data-topic-id="test-acquisition"]').click();
    await expect(page.locator("#topicContent h1")).toHaveText("北辰科技收购远川数据");
    expect(await styles(page.locator('#topicToolbar'),Object.keys(reference.toolbar))).toEqual(reference.toolbar);
    expect(await styles(page.locator('#topicScrollArea'),Object.keys(reference.content))).toEqual(reference.content);
    for (const section of await page.locator('#topicContent .settings-section-card').all()) {
      expect(await styles(section,['padding-top','padding-right','padding-bottom','padding-left','row-gap']))
        .toEqual({'padding-top':'16px','padding-right':'16px','padding-bottom':'16px','padding-left':'16px','row-gap':'12px'});
    }
    expect(await styles(page.locator('#topicContent .article-title'),typography)).toEqual(reference.title);
    expect(await styles(page.locator('#topicDefinitionBtn'),controlStyle)).toEqual(reference.button);
    expect(await styles(page.locator('#topicCardList .news-card-title').first(),typography)).toEqual(reference.cardTitle);
    expect(await styles(page.locator('.topic-news .news-card-title').first(),typography)).toEqual(reference.cardTitle);
    expect(await styles(page.locator('.topic-news .news-card-snippet').first(),typography)).toEqual(reference.cardSnippet);
    expect(await styles(page.locator('#topicSearchRow .feed-search-box'),controlStyle)).toEqual(reference.searchBox);
    expect(await styles(page.locator('#topicSearchInput'),typography)).toEqual(reference.searchInput);
    await expect(page.locator('#topicContent .status-pill')).toHaveText('持续跟踪中');
    expect(await page.locator('#topicContent').evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:".ui-artifacts/topics-"+theme+"-"+width+"-"+scheme+".png"});
    const middle=await page.locator('#feedColTopics').boundingBox();
    const right=await page.locator('#detailPanelTopics').boundingBox();
    await expect(page.locator('#topicContent')).not.toContainText('阅读新闻');
    await page.locator('.topic-news-open').first().click();
    await expect(page.locator('#detailPanelFeed')).toHaveClass(/active/);
    await expect(page.locator('#detailContent .article-title')).toBeVisible();
    expect(await page.locator('#feedColTopics').boundingBox()).toEqual(middle);
    const reader=await page.locator('#detailPanelFeed').boundingBox();
    for(const key of ['x','y','width','height'])expect(reader[key]).toBeCloseTo(right[key],0);
    if(width>920)expect(reader.x).toBeGreaterThanOrEqual(middle.x+middle.width);
    await page.screenshot({path:'.ui-artifacts/topics-article-'+theme+'-'+width+'-'+scheme+'.png'});
    await page.locator(width>920?'#topicReaderBackBtn':'#mobileReaderBackBtn').click();
    await expect(page.locator('#detailPanelTopics')).toHaveClass(/active/);
    expect(await page.locator('#feedColTopics').boundingBox()).toEqual(middle);
  });
}

for (const width of [390,1280]) test('主题表单沿用设置控件、阅读字号同步 '+width,async({page,request})=>{
  await request.post('/__test/reset-reading');
  await page.setViewportSize({width,height:900});await page.goto('/');
  await openSettings(page);await page.locator('[data-settings-tab="classification"]').click();
  const inputStyle=await styles(page.locator('[name="classification.concurrency"]'),controlStyle);
  const labelStyle=await styles(page.locator('#classificationSettings .field-label'),typography);
  if (width<=920) await page.locator('#settingsCategoryBackBtn').click();
  await page.locator('#closeSettingsBtn').click();await openTopics(page);
  await page.locator('[data-topic-id="test-acquisition"]').click();
  const before = await styles(page.locator('#topicContent .article-title'),['font-size']);
  const font = page.getByRole('combobox',{name:'阅读字号',exact:true});
  await font.click();await page.getByRole('option',{name:'特大（130%）',exact:true}).click();
  expect(parseFloat((await styles(page.locator('#topicContent .article-title'),['font-size']))['font-size'])).toBeCloseTo(parseFloat(before['font-size'])*1.3,2);
  expect(await styles(page.locator('.topic-news .news-card-title').first(),typography)).toEqual(await styles(page.locator('#topicCardList .news-card-title').first(),typography));
  if (width<=920) await page.locator('#mobileReaderBackBtn').click();
  await page.locator('#newTopicBtn').click();
  expect(await styles(page.locator('[name="query"]'),controlStyle)).toEqual(inputStyle);
  expect(await styles(page.locator('#topicCreateForm .field-label').first(),typography)).toEqual(labelStyle);
  const form=await page.locator('#topicCreateForm').boundingBox(), content=await page.locator('#topicContent').boundingBox();
  expect(form.width).toBeCloseTo(content.width,0);
  await page.screenshot({path:'.ui-artifacts/topics-shared-form-'+width+'.png'});
});

test("删除主题使用确认框并保留原始新闻",async({page,request})=>{
  await page.goto('/');await openTopics(page);
  await page.locator('[data-topic-id="test-acquisition"]').click();
  await expect(page.locator('#topicToolbar [data-topic-action="delete"]')).toBeVisible();
  await page.locator('[data-topic-action="delete"]').click();
  await expect(page.locator('#appConfirmCancel')).toBeFocused();
  await page.locator('#appConfirmCancel').click();
  await expect(page.locator('[data-topic-id="test-acquisition"]')).toHaveCount(1);
  await page.locator('[data-topic-action="delete"]').click();
  await page.locator('#appConfirmAccept').click();
  await expect(page.locator('[data-topic-id="test-acquisition"]')).toHaveCount(0);
  expect((await request.get('/api/news/ui-000/detail')).status()).toBe(200);
});

test("创建时保留中栏，步骤与操作固定在右栏顶部",async({page,request})=>{
  await request.post('/__test/topics',{data:{many:true,many_topics:true}});
  await page.goto('/');await openTopics(page);
  await page.locator('[data-topic-id="test-acquisition"]').click();
  const list=page.locator('#topicListScroll');
  await list.evaluate(n=>{n.scrollTop=280;});
  const before=await page.locator('#feedColTopics').innerHTML();
  const position=await list.evaluate(n=>n.scrollTop);
  await page.locator('#newTopicBtn').click();
  expect(await page.locator('#feedColTopics').innerHTML()).toBe(before);
  expect(await list.evaluate(n=>n.scrollTop)).toBe(position);
  await expect(page.locator('#topicCreationSteps')).toBeVisible();
  await expect(page.locator('#topicCreationSteps [aria-current="step"]')).toContainText('输入主题');
  await expect(page.locator('#feedColTopics .topic-steps')).toHaveCount(0);
  await page.locator('[name="query"]').fill('云杉芯片上市');
  await page.getByRole('button',{name:'搜索并生成定义'}).click();
  await openDefinitionEditor(page);
  await expect(page.locator('[name="definition"]')).toBeVisible({timeout:15000});
  await expect(page.locator('#topicCreationSteps [aria-current="step"]')).toContainText('确认定义');
  await expect(page.locator('.topic-card')).toHaveCount(17);
  const toolbar=page.locator('#topicToolbar'), steps=page.locator('#topicCreationSteps');
  const initial=await toolbar.boundingBox(), stepPosition=await steps.boundingBox();
  await page.locator('[name="start_date"]').scrollIntoViewIfNeeded();
  expect((await toolbar.boundingBox()).y).toBe(initial.y);
  expect((await steps.boundingBox()).y).toBe(stepPosition.y);
  await page.screenshot({path:'.ui-artifacts/topics-create-toolbar.png'});
  await page.locator('[data-topic-id="test-acquisition"]').click();
  await expect(steps).toBeHidden();
  await expect(toolbar.getByRole('button')).toHaveText(['定义','归档主题','删除主题']);
  await expect(page.locator('#topicContent')).not.toContainText('当前进展');
  await toolbar.getByRole('button',{name:'定义',exact:true}).click();
  await expect(page.locator('#topicDefinitionForm [name="definition"]')).toBeVisible();
  await expect(page.locator('#topicDefinitionForm [name="horizontal_rule"]')).toBeVisible();
  await page.getByRole('button',{name:'取消',exact:true}).click();
  await page.locator('.topic-news-open').last().scrollIntoViewIfNeeded();
  expect((await toolbar.boundingBox()).y).toBe(initial.y);
});

test("重要进展使用底部滑块浏览，内容拖动不滚动且键盘仍可选择",async({page,request})=>{
  await request.post('/__test/topics',{data:{long_timeline:true}});
  await page.goto('/');await openTopics(page);
  await page.locator('[data-topic-id="test-acquisition"]').click();
  const timeline=page.locator('.topic-timeline');
  const slider=page.getByRole('slider',{name:'重要进展浏览位置'});
  const active=await page.locator('.topic-node[aria-pressed="true"]').getAttribute('data-topic-node');
  await expect(slider).toBeEnabled();
  await slider.focus();await slider.press('Home');
  await expect.poll(()=>timeline.evaluate(n=>n.scrollLeft)).toBe(0);
  const sliderBox=await slider.boundingBox();
  await page.mouse.click(sliderBox.x+sliderBox.width*.75,sliderBox.y+sliderBox.height/2);
  await expect.poll(()=>timeline.evaluate(n=>n.scrollLeft)).toBeGreaterThan(1000);
  await expect(page.locator('.topic-node[aria-pressed="true"]')).toHaveAttribute('data-topic-node',active);
  await expect(page.locator('.topic-news')).toHaveCount(2);
  await timeline.focus();await timeline.press('Home');
  await expect(page.locator('.topic-node').first()).toBeFocused();
  const box=await timeline.boundingBox();
  await page.mouse.move(box.x+230,box.y+10);await page.mouse.down();
  await page.mouse.move(box.x+30,box.y+10,{steps:12});await page.mouse.up();
  expect(await timeline.evaluate(n=>n.scrollLeft)).toBe(0);
  await expect(page.locator('.topic-node[aria-pressed="true"]')).toHaveAttribute('data-topic-node',active);
  await expect(page.locator('.topic-news')).toHaveCount(2);
  await timeline.focus();await timeline.press('End');
  await expect(page.locator('.topic-node').last()).toBeFocused();
  expect(await timeline.evaluate(n=>n.scrollLeft)).toBeGreaterThan(1000);
  await page.locator('.topic-node').last().press('Enter');
  await expect(page.locator('.topic-news')).toHaveCount(1);
  expect((await page.locator('.topic-history').boundingBox()).height).toBeGreaterThanOrEqual(300);
});

for(const width of [390,1280]) test('有报道节点筛选新闻，无报道节点保留原列表 '+width,async({page,request})=>{
  await request.post('/__test/topics',{data:{long_timeline:true}});
  await page.setViewportSize({width,height:900});
  await page.goto('/');await openTopics(page);await page.locator('[data-topic-id="test-acquisition"]').click();
  const empty=page.locator('[data-topic-node="test-acquisition-node-2"]');
  const reported=page.locator('[data-topic-node="test-acquisition-node-0"]');
  await expect(empty).toContainText('历史概述 · 暂无报道');
  await expect(reported).toContainText('查看 1 条报道');
  await empty.click();await expect(empty).toHaveAttribute('aria-pressed','true');
  await expect(page.locator('.topic-news')).toHaveCount(2);
  await expect(page.locator('#topicNewsTitle')).toContainText('新闻时间线');
  await reported.click();await expect(page.locator('.topic-news')).toHaveCount(1);
  await expect(page.locator('#topicNewsTitle')).toContainText('相关报道');
  const filtered=await page.locator('.topic-news-open').getAttribute('data-topic-item');
  await empty.click();await expect(empty).toHaveAttribute('aria-pressed','true');
  await expect(page.locator('.topic-news-open')).toHaveAttribute('data-topic-item',filtered);
  await expect(page.locator('#topicNewsTitle + p')).toHaveText('公布收购');
  await reported.click();await expect(reported).toHaveAttribute('aria-pressed','true');
  await page.locator('.topic-history').scrollIntoViewIfNeeded();
  await page.screenshot({path:'.ui-artifacts/topics-controls-'+width+'.png'});
  await page.locator('[data-topic-action="all"]').click();
  await expect(page.locator('.topic-news')).toHaveCount(2);
});

for (const action of ['button','external']) test("精简已有脉络后保留新闻与节点关联 "+action,async({page,request})=>{
  await request.post('/__test/topics',{data:{long_timeline:true}});
  await page.goto('/');await openTopics(page);
  await page.locator('[data-topic-id="test-acquisition"]').click();
  await page.locator('[data-topic-node="test-acquisition-node-1"]').click();
  await expect(page.locator('.topic-news')).toHaveCount(1);
  if (action==='button') {
    await page.getByRole('button',{name:'精简脉络',exact:true}).click();
    await answerConfirm(page,true);
  }
  else {
    await request.post('/api/topics/test-acquisition/refine');
    await expect.poll(async()=>(await (await request.get('/api/topics/test-acquisition')).json()).job.status).toBe('done');
    await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  }
  await expect(page.locator('.topic-node')).toHaveCount(1,{timeout:15000});
  await expect(page.locator('.topic-node')).toContainText('交易进入监管审查阶段');
  await expect(page.locator('.topic-news')).toHaveCount(2);
  await page.locator('.topic-node').click();
  await expect(page.locator('.topic-news')).toHaveCount(2);
  await expect(page.getByRole('button',{name:'精简脉络',exact:true})).toHaveCount(0);
});

for(const width of [390,1280]) test('精简前警告可取消，成功后一个月内隐藏入口 '+width,async({page,request})=>{
  await page.setViewportSize({width,height:900});
  let submitted=0;
  await page.route('**/api/topics/*/refine',route=>{submitted++;return route.continue();});
  await page.goto('/');await openTopics(page);await page.locator('[data-topic-id="test-acquisition"]').click();
  const button=page.getByRole('button',{name:'精简脉络',exact:true});
  await button.click();
  await expect(page.locator('#appConfirmMessage')).toContainText('不再单独显示');
  await expect(page.locator('#appConfirmMessage')).toContainText('成功后一个月内');
  await expect(page.locator('#appConfirmCancel')).toBeFocused();
  await page.screenshot({path:'.ui-artifacts/topic-refine-warning-'+width+'.png'});
  await answerConfirm(page,false);
  expect(submitted).toBe(0);
  await expect(button).toBeEnabled();
  await expect(page.locator('.topic-node')).toHaveCount(2);
  await request.post('/__test/topics',{data:{refined_recently:true}});
  await page.reload();await openTopics(page);await page.locator('[data-topic-id="test-acquisition"]').click();
  await expect(page.locator('.topic-node')).toHaveCount(2);
  await expect(button).toHaveCount(0);
  const blocked=await request.post('/api/topics/test-acquisition/refine');
  expect(blocked.status()).toBe(409);
  expect((await blocked.json()).error).toBe('topic_refine_cooldown');
});

test("手机时间线支持原生横向滑动且不触发边缘返回",async({browser,request})=>{
  await request.post('/__test/topics',{data:{long_timeline:true}});
  const context=await browser.newContext({viewport:{width:360,height:800},isMobile:true,hasTouch:true});
  const page=await context.newPage();
  try {
    await page.goto('http://127.0.0.1:8765/');await openTopics(page);
    await page.locator('[data-topic-id="test-acquisition"]').tap();
    const timeline=page.locator('.topic-timeline');
    await timeline.scrollIntoViewIfNeeded();
    await timeline.evaluate(n=>{n.scrollLeft=0;});
    const selected=await page.locator('.topic-node[aria-pressed="true"]').getAttribute('data-topic-node');
    const box=await timeline.boundingBox(), cdp=await context.newCDPSession(page);
    async function swipe(x1,y1,x2,y2) {
      await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:x1,y:y1}]});
      for(let i=1;i<=12;i++) await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:x1+(x2-x1)*i/12,y:y1+(y2-y1)*i/12}]});
      await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
    }
    await swipe(box.x+box.width-24,box.y+55,box.x+24,box.y+55);
    await expect.poll(()=>timeline.evaluate(n=>n.scrollLeft)).toBeGreaterThan(80);
    await expect(page.locator('.topic-node[aria-pressed="true"]')).toHaveAttribute('data-topic-node',selected);
    await timeline.evaluate(n=>{n.scrollLeft=800;});
    await swipe(box.x+8,box.y+110,box.x+box.width-24,box.y+110);
    await expect.poll(()=>timeline.evaluate(n=>n.scrollLeft)).toBeLessThan(750);
    await expect(page.locator('#feedView')).toHaveClass(/mobile-reader-active/);
    await page.locator('#mobileReaderBackBtn').tap();await page.locator('#newTopicBtn').tap();
    await expect(page.locator('#topicCreationSteps')).toBeVisible();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:'.ui-artifacts/topics-create-360.png'});
  } finally {await context.close();}
});

test("新闻分页刷新保留列表、焦点与阅读位置",async({page,request})=>{
  await request.post('/__test/topics',{data:{many:true,many_topics:true}});
  await page.goto('/');await openTopics(page);
  await page.locator('[data-topic-id="test-acquisition"]').click();
  await expect(page.locator('.topic-news')).toHaveCount(40);
  await page.locator('[data-topic-action="more"]').click();
  await expect(page.locator('.topic-news')).toHaveCount(65);
  const article=page.locator('.topic-news-open').last();
  await article.scrollIntoViewIfNeeded();
  await page.locator('#topicListScroll').evaluate(n=>n.scrollTop=120);
  const listScroll=await page.locator('#topicListScroll').evaluate(n=>n.scrollTop);
  const scroll=await page.locator('#topicScrollArea').evaluate(n=>n.scrollTop);
  await article.click();
  expect(await page.locator('#topicListScroll').evaluate(n=>n.scrollTop)).toBe(listScroll);
  await page.locator('#topicReaderBackBtn').click();
  expect(await page.locator('#topicListScroll').evaluate(n=>n.scrollTop)).toBe(listScroll);
  expect(await page.locator('#topicScrollArea').evaluate(n=>n.scrollTop)).toBeCloseTo(scroll,0);
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await expect(page.locator('.topic-news')).toHaveCount(65);
});

test("手机触屏确认定义布局与输入草稿",async({browser,request})=>{
  await request.post('/__test/topics',{data:{empty:true}});
  const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  const page=await context.newPage();
  try {
    await page.goto('http://127.0.0.1:8765/');await openTopics(page);
    await page.locator('#newTopicBtn').tap();
    await page.locator('[name="query"]').fill('云杉芯片上市');
    await page.getByRole('button',{name:'搜索并生成定义'}).tap();
    await openDefinitionEditor(page);
    await expect(page.locator('[name="name"]')).toBeVisible({timeout:15000});
    await page.locator('[name="definition"]').fill('保留手动编辑后的范围');
    expect(await page.locator('[name="definition"]').evaluate(n=>getComputedStyle(n).fontSize)).toBe('16px');
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:'.ui-artifacts/topics-create-mobile.png'});
    await page.locator('#mobileReaderBackBtn').tap();
    await page.locator('.topic-card').tap();
    await expect(page.locator('[name="definition"]')).toHaveValue('保留手动编辑后的范围');
    await page.getByRole('button',{name:'确认并整理历史'}).tap();
    await expect(page.getByRole('heading',{name:'主题已创建'})).toBeVisible({timeout:15000});
  } finally {await context.close();}
});
