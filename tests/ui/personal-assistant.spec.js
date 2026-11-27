import { test, expect } from "@playwright/test";
import { chooseTheme, answerConfirm, openSettings, openSettingsItem } from "./helpers.js";

test.beforeEach(async ({request}) => {
  await request.post("/__test/personal-assistant", {data:{reset:true}});
  const {profile}=await (await request.get("/api/personal-assistant/profile")).json();
  await request.put("/api/personal-assistant/profile", {data:{...profile,name:"测试助手"}});
});

async function ready(page) {
  await page.goto("/");
  await expect(page.locator("#feedCardList .news-card").first()).toBeVisible();
}
async function open(page, name="测试助手") {
  const trigger = page.locator("#personalAssistantBtn");
  if (await trigger.getAttribute("aria-expanded") !== "true") await trigger.click();
  await expect(page.locator("#personalAssistantPanel")).toBeVisible();
  await expect(page.locator("#personalAssistantName")).toHaveText(name);
  await expect(page.locator("#personalAssistantStatus")).not.toContainText("正在读取");
}
async function send(page, text) {
  await page.locator("#personalAssistantInput").fill(text);
  await page.locator("#personalAssistantSend").click();
  await expect(page.locator("#personalAssistantMessages")).toContainText("已查询 NR 库内");
  await expect(page.locator("#personalAssistantStatus")).toContainText("本轮已完成");
}
async function openAssistantSettings(page, name="测试助手") {
  if (await page.locator("#feedView").evaluate(view => view.inert)) await page.locator("#personalAssistantBtn").click();
  await openSettings(page);
  await openSettingsItem(page, "personal-assistant");
  await expect(page.locator("#settingsDetailTitle")).toHaveText("研读与助手");
  await expect(page.locator('[data-personal-field="name"]')).toHaveValue(name);
}

test("Control+Q 连续开合，切换新闻保留讨论和草稿", async ({page}) => {
  await ready(page);
  const panel = page.locator("#personalAssistantPanel");
  const input = page.locator("#personalAssistantInput");
  const trigger = page.locator("#personalAssistantBtn");
  await expect(trigger).toHaveAttribute("aria-keyshortcuts", "Control+q");
  await page.keyboard.press("Control+q");
  await expect(input).toBeFocused();
  await send(page, "对照阅读两篇新闻");
  await input.fill("正在写的下一句");
  await input.press("Control+q");
  await expect(panel).toBeHidden();
  await expect(trigger).toBeFocused();
  await page.keyboard.press("Control+q");
  await expect(input).toHaveValue("正在写的下一句");
  await expect(page.locator("#personalAssistantMessages")).toContainText("对照阅读两篇新闻");
  const cards = page.locator("#feedCardList .news-card");
  await cards.nth(1).locator(".news-card-title").click();
  await expect(cards.nth(1)).toHaveAttribute("aria-current", "true");
  await expect(panel).toBeVisible();
  await expect(input).toHaveValue("正在写的下一句");
  await cards.nth(1).press("q");
  await expect(page.locator("#agentDock")).not.toHaveClass(/minimized/);
  await expect(panel).toBeVisible();
  await trigger.click();
  await expect(panel).toBeHidden();
  await expect(page.locator("#agentDock")).not.toHaveClass(/minimized/);
});

test("收起期间的已完成回复点亮顶栏，刷新后仍可读", async ({page,request,browserName}) => {
  const trigger = page.locator("#personalAssistantBtn"), dot = page.locator("#personalAssistantDot");
  const status = page.locator("#personalAssistantStatus");
  await page.setViewportSize({width:390,height:844});
  await request.post("/__test/personal-assistant", {data:{mode:"compacting"}});
  await ready(page);
  await open(page);
  await page.locator("#personalAssistantInput").fill("收起后提醒我");
  await page.locator("#personalAssistantSend").click();
  await expect(status).toHaveText("正在整理上下文");
  await trigger.click();
  await expect(dot).toBeHidden();
  await request.post("/__test/personal-assistant", {data:{mode:"success"}});
  await expect(dot).toBeVisible({timeout:10000});
  await expect(trigger).toHaveAccessibleName("个人助手，有新回复");
  await page.locator(".topbar").screenshot({path:`.ui-artifacts/assistant-unread-${browserName}.png`});
  await page.reload();
  await expect(dot).toBeVisible();
  await trigger.click();
  await expect(dot).toBeHidden();
  await expect(status).toContainText("本轮已完成");
  const notice = async () => (await (await request.get("/api/personal-assistant/reply-notice")).json()).unread_job_id;
  await expect.poll(notice).toBeNull();
  await page.keyboard.press("Control+q");
  await expect(dot).toBeHidden();
  await page.reload();
  await expect(trigger).toHaveAccessibleName("个人助手");
});

for (const width of [390,1440]) test(`翻看旧对话时保留位置，浮动按钮回到最新回复 ${width}`, async ({page,request,browserName}) => {
  await page.setViewportSize({width,height:700});
  await ready(page);
  await open(page);
  const stream = page.locator("#personalAssistantMessages"), jump = page.getByRole("button",{name:"回到底部"});
  await expect(jump).toBeHidden();
  await send(page, Array.from({length:40}, (_, i) => `资料第 ${i+1} 段。`).join("\n\n"));
  await expect(jump).toBeHidden();
  await request.post("/__test/personal-assistant",{data:{mode:"compacting"}});
  await page.locator("#personalAssistantInput").fill("再核对一下");
  await page.locator("#personalAssistantSend").click();
  await expect(page.locator("#personalAssistantStatus")).toHaveText("正在整理上下文");
  await stream.evaluate(el => {el.scrollTop = 0;});
  await expect(jump).toBeVisible();
  await request.post("/__test/personal-assistant",{data:{mode:"success"}});
  await expect(page.locator("#personalAssistantStatus")).toContainText("本轮已完成");
  expect(await stream.evaluate(el => el.scrollTop)).toBe(0);
  const notice = async () => (await (await request.get("/api/personal-assistant/reply-notice")).json()).unread_job_id;
  expect(await notice()).toBeTruthy();
  await expect(page.locator("#personalAssistantDot")).toBeHidden();
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await expect(page.locator("#personalAssistantStatus")).toContainText("本轮已完成");
  await expect(jump).toBeVisible();
  expect(await stream.evaluate(el => el.scrollTop)).toBe(0);
  await page.locator("#personalAssistantPanel").screenshot({path:`.ui-artifacts/assistant-jump-${width}-${browserName}.png`});
  await jump.click();
  await expect(jump).toBeHidden();
  await expect(stream).toBeFocused();
  expect(await stream.evaluate(el => el.scrollHeight-el.clientHeight-el.scrollTop)).toBeLessThanOrEqual(2);
  await expect.poll(notice).toBeNull();
});

test("长回复仍在屏内时也能跳到末尾", async ({page}) => {
  const answer = Array.from({length:80}, (_, i) => `讨论第 ${i+1} 段。`).join("\n\n");
  await page.route("**/api/personal-assistant/threads/main", route => route.fulfill({json:{
    ok:true, thread:{id:"long",is_main:true}, has_more:false, job:{id:"turn",status:"done"},
    messages:[{id:1,job_id:"turn",role:"user",status:"done",content:"展开说说。"},
      {id:2,job_id:"turn",role:"assistant",status:"done",content:answer}],
  }}));
  await ready(page);
  await open(page);
  const stream = page.locator("#personalAssistantMessages"), jump = page.getByRole("button",{name:"回到底部"});
  await expect(jump).toBeHidden();
  await stream.evaluate(el => {el.scrollTop = el.scrollHeight / 2;});
  await expect(jump).toBeVisible();
  await jump.press("Enter");
  await expect(jump).toBeHidden();
  await expect(stream).toBeFocused();
  expect(await stream.evaluate(el => el.scrollHeight-el.clientHeight-el.scrollTop)).toBeLessThanOrEqual(2);
});

test("工作区按剩余宽度折叠导航和列表，单栏可返回另选新闻", async ({page}) => {
  await page.setViewportSize({width:1720,height:900});
  await ready(page);
  const first = page.locator('#feedCardList .news-card').first();
  await first.locator('.news-card-title').click();
  await open(page);
  const menu = page.locator("#openNavBtn"), back = page.locator("#mobileReaderBackBtn");
  await expect(menu).toBeHidden();
  await page.setViewportSize({width:1719,height:900});
  await expect(menu).toBeVisible();
  await menu.click();
  await expect(page.locator("#leftNavDrawer")).toHaveClass(/drawer-open/);
  await page.keyboard.press("Escape");
  await expect(page.locator("#personalAssistantPanel")).toBeVisible();
  await page.setViewportSize({width:1361,height:900});
  await expect(back).toBeHidden();
  await expect(page.locator("#feedColFeed")).toHaveJSProperty("inert",false);
  await page.setViewportSize({width:1360,height:900});
  await expect(back).toBeVisible();
  await expect(page.locator("#feedColFeed")).toHaveJSProperty("inert",true);
  await back.click();
  const next = page.locator("#feedCardList .news-card").nth(1);
  await next.locator(".news-card-title").click();
  await expect(next).toHaveAttribute("aria-current","true");
  await expect(back).toBeVisible();
  await expect(page.locator("#personalAssistantPanel")).toBeVisible();
  await page.keyboard.press("Control+q");
  await expect(menu).toBeHidden();
  await expect(back).toBeHidden();
  await expect(next).toHaveAttribute("aria-current","true");
});

test("Esc 逐层关闭左侧设置和文章问答，返回单栏列表时保留助手与草稿", async ({page}) => {
  await page.setViewportSize({width:1280,height:900});
  await ready(page);
  await page.locator("#feedCardList .news-card-title").first().click();
  await open(page);
  const workspace = page.locator("#feedView"), panel = page.locator("#personalAssistantPanel");
  const input = page.locator("#personalAssistantInput"), articleChat = page.locator("#agentDockToggleBtn");
  await expect(workspace).toHaveClass(/mobile-reader-active/);
  await input.fill("对照阅读的草稿");

  await openAssistantSettings(page);
  await expect(panel).toBeVisible();
  await input.press("Escape");
  await expect(workspace).toHaveClass(/settings-active/);
  await page.locator('[data-personal-field="name"]').press("Escape");
  await expect(workspace).not.toHaveClass(/settings-active/);
  await expect(workspace).toHaveClass(/mobile-reader-active/);
  await expect(panel).toBeVisible();

  await articleChat.click();
  await expect(articleChat).toHaveAttribute("aria-expanded","true");
  await page.locator("#agentPromptInput").press("Escape");
  await expect(articleChat).toHaveAttribute("aria-expanded","false");
  await expect(articleChat).toBeFocused();
  await expect(workspace).toHaveClass(/mobile-reader-active/);
  await expect(panel).toBeVisible();

  await articleChat.press("Escape");
  await expect(workspace).not.toHaveClass(/mobile-reader-active/);
  await expect(page.locator("#feedColFeed")).toHaveJSProperty("inert",false);
  await expect(panel).toBeVisible();
  await expect(input).toHaveValue("对照阅读的草稿");
});

test("手机收起助手后返回同一篇文章和滚动位置", async ({page}) => {
  await page.setViewportSize({width:390,height:700});
  await ready(page);
  await page.locator('[data-id="ui-064"] .news-card-title').click();
  await expect(page.locator("#articleBodyContent > p").first()).toBeVisible();
  const reader = page.locator("#detailScrollArea");
  const previous = await reader.evaluate(el => { el.scrollTop = 240; return el.scrollTop; });
  expect(previous).toBeGreaterThan(0);
  await page.locator("#personalAssistantBtn").click();
  await expect(page.locator("#personalAssistantInput")).not.toBeFocused();
  await expect(page.locator("#feedView")).toHaveJSProperty("inert",true);
  await expect(page.locator("#mobileBottomNav")).toHaveJSProperty("inert",true);
  await page.keyboard.press("Escape");
  await expect(page.locator("#personalAssistantPanel")).toBeVisible();
  await expect(page.locator("#feedView")).toHaveClass(/mobile-reader-active/);
  await page.keyboard.press("Control+q");
  await expect(page.locator("#personalAssistantPanel")).toBeHidden();
  await expect(page.locator("#feedView")).toHaveClass(/mobile-reader-active/);
  await expect(page.locator("#feedView")).toHaveJSProperty("inert",false);
  await expect(page.locator("#mobileBottomNav")).toHaveJSProperty("inert",false);
  await expect(reader).toHaveJSProperty("scrollTop", previous);
});

for (const width of [390,1440]) test(`首次未命名即可开始讨论，之后可以取名 ${width}`, async ({page,request}) => {
  await request.post("/__test/personal-assistant", {data:{reset:true}});
  await page.setViewportSize({width,height:900});await ready(page);await open(page,"个人助手");
  await expect(page.locator("#personalAssistantInput")).toBeEnabled();
  await expect(page.locator("#personalAssistantSend")).toBeDisabled();
  await page.screenshot({path:`.ui-artifacts/personal-unnamed-${width}.png`});
  await send(page,"未命名时的第一轮");
  await openAssistantSettings(page,"");
  const name=page.locator('[data-personal-field="name"]');
  await expect(name).toHaveValue("");
  await expect(page.locator("#personalSettingsStatus")).not.toContainText("名字不能为空");
  await name.fill("我取的名字");
  await page.getByRole("button",{name:"保存本页",exact:true}).click();
  await expect(page.locator("#personalSettingsStatus")).toHaveText("个人助手已保存");
  await open(page,"我取的名字");
  await expect(page.locator("#personalAssistantInput")).toBeEnabled();
  await send(page,"从这段讨论开始");
  await page.reload();await open(page,"我取的名字");
  await expect(page.locator("#personalAssistantMessages")).toContainText("从这段讨论开始");
});

test("独立讨论跨新闻、重载保留，自动记忆可查看修改删除", async ({page,request}) => {
  const errors=[];page.on("pageerror",error=>errors.push(error.message));
  await ready(page);await open(page);
  await send(page,"记住：我关注政策的长期影响，而不是只看当天价格");
  const listing=await (await request.get("/api/personal-assistant/threads")).json();
  const first=listing.threads[0];
  await expect(page.locator("#personalAssistantNew, #personalThreadPicker")).toHaveCount(0);
  await send(page,"查看最近的新闻");
  await page.locator("#personalAssistantBtn").click();
  await page.locator("#feedCardList .news-card-title").nth(1).click();
  await page.locator("#personalAssistantBtn").click();
  await expect(page.locator("#personalAssistantMessages")).toContainText("查看最近的新闻");
  await page.reload();await open(page);
  await expect(page.locator("#personalAssistantMessages")).toContainText("查看最近的新闻");
  await expect(page.locator("#personalAssistantMessages")).toContainText("而不是只看当天价格");
  await openAssistantSettings(page);
  await page.getByRole("combobox",{name:"记忆文件",exact:true}).click();
  await page.getByRole("option",{name:"关注方向",exact:true}).click();
  const editor=page.getByRole("textbox",{name:"记忆内容"});
  await expect(editor).toContainText("消息 #");
  await editor.fill("我的观点有修正：关注政策传导的时间差。");
  await page.getByRole("button",{name:"保存记忆",exact:true}).click();
  await expect(page.locator("#personalMemoryEditor")).toContainText("记忆已保存");
  await expect((await request.get("/api/personal-assistant/memory/notes/%E5%85%B3%E6%B3%A8%E6%96%B9%E5%90%91.md")).ok()).toBeTruthy();
  await page.getByRole("button",{name:"删除记忆",exact:true}).click();await answerConfirm(page);
  await expect(page.locator("#personalMemoryEditor")).toContainText("记忆已删除");
  const memories=await (await request.get("/api/personal-assistant/memory")).json();
  expect(memories.files.map(f=>f.path)).not.toContain("notes/关注方向.md");
  expect((await (await request.get("/api/personal-assistant/threads/"+first.id)).json()).messages).toHaveLength(4);
  expect(errors).toEqual([]);
});

test("名字与 runtime 独立保存，原有模型设置不被改动", async ({page,request}) => {
  const original=(await (await request.get("/api/settings")).json()).agent;
  await ready(page);await open(page);await openAssistantSettings(page);
  await page.locator('[data-personal-field="name"]').fill("望远");
  await page.locator("#personalAssistantSettings").getByRole("combobox",{name:"Runtime",exact:true}).click();
  await page.getByRole("option",{name:"Codex",exact:true}).click();
  await expect(page.locator('[data-personal-field="codex.profile"], [data-personal-field="codex.provider"], input[name="personal.codex.model"]')).toHaveCount(0);
  await page.getByRole("button",{name:"保存本页",exact:true}).click();
  await expect(page.locator("#personalSettingsStatus")).toHaveText("个人助手已保存");
  await expect(page.locator("#personalAssistantPanel")).toBeVisible();
  await expect(page.locator("#personalAssistantName")).toHaveText("望远");
  await expect(page.locator("#personalAssistantRuntime")).toHaveText("Codex");
  const profile=(await (await request.get("/api/personal-assistant/profile")).json()).profile;
  expect(profile.name).toBe("望远");expect(profile.runtime).toBe("codex");
  expect((await (await request.get("/api/settings")).json()).agent).toEqual(original);
});

test("停止与失败有明确状态，关窗继续运行，草稿不会丢失", async ({page,request}) => {
  await request.post("/__test/personal-assistant", {data:{mode:"wait"}});
  await ready(page);await open(page);
  await page.locator("#personalAssistantInput").fill("慢慢整理资料");
  await page.locator("#personalAssistantSend").click();
  await expect(page.getByRole("button",{name:"停止回复",exact:true})).toBeEnabled();
  await page.locator("#personalAssistantInput").fill("下一条讨论的草稿");
  await expect(page.locator("#personalAssistantMessages")).toContainText("慢慢整理资料");
  await page.locator("#personalAssistantBtn").click();
  await page.locator("#personalAssistantBtn").click();
  await expect(page.locator("#personalAssistantInput")).toHaveValue("下一条讨论的草稿");
  await page.getByRole("button",{name:"停止回复",exact:true}).click();
  await expect(page.locator("#personalAssistantStatus")).toContainText("已停止");
  await expect(page.locator("#personalAssistantMessages .assistant .agent-bubble").last()).toHaveText("本轮没有生成回复");
  await request.post("/__test/personal-assistant", {data:{mode:"fail"}});
  await page.locator("#personalAssistantSend").click();
  await expect(page.locator("#personalAssistantStatus")).toContainText("本轮未完成");
  await expect(page.locator("#personalAssistantMessages .assistant .agent-bubble").last()).toHaveText("本轮没有生成回复");
  await request.post("/__test/personal-assistant", {data:{mode:"success"}});
  await send(page,"继续讨论");
});

test("生成期间只请求阶段，墨水屏气泡在完成时一次显示全文", async ({page,request}) => {
  const setMode = mode => request.post("/__test/personal-assistant", {data:{mode}});
  await setMode("web_search");
  await ready(page);
  await chooseTheme(page, "eink");
  await open(page);
  const gets = [];
  page.on("request", req => {
    const url = new URL(req.url());
    if (req.method() === "GET" && /\/api\/personal-assistant\/(profile|threads\/[^/]+)$/.test(url.pathname)) gets.push(url);
  });
  const status = page.locator("#personalAssistantStatus"), stream = page.locator("#personalAssistantMessages");
  await page.locator("#personalAssistantInput").fill("查询后再给我完整结论");
  await page.locator("#personalAssistantSend").click();
  const pending = stream.locator(".assistant").last();
  await expect(pending.locator(".agent-bubble")).toHaveText("正在整理资料…");
  const replyId = Number(await pending.getAttribute("data-message-id"));
  await stream.evaluate(el => {
    window.bubbleUpdates = [];
    new MutationObserver(() => window.bubbleUpdates.push(el.querySelector(".assistant .agent-bubble").innerText))
      .observe(el, {childList:true, subtree:true, characterData:true});
  });
  await expect(status).toHaveText("正在搜索网页…");
  await setMode("web_fetch");await expect(status).toHaveText("正在抓取来源网页…");
  await setMode("tool");await expect(status).toHaveText("正在查阅新闻库与记忆…");
  await setMode("writing");await expect(status).toHaveText("正在回复…");
  await status.evaluate(el => {
    window.stageChanges = 0;
    new MutationObserver(records => {window.stageChanges += records.length;})
      .observe(el, {childList:true, subtree:true, characterData:true});
  });
  const polls = gets.length;
  await page.evaluate(() => {
    document.dispatchEvent(new Event("visibilitychange"));
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect.poll(() => gets.length).toBeGreaterThanOrEqual(polls + 2);
  expect(gets.every(url => url.search === "?status_only=1")).toBe(true);
  expect(await page.evaluate(() => window.bubbleUpdates)).toEqual([]);
  expect(await page.evaluate(() => window.stageChanges)).toBe(0);
  await setMode("success");
  await expect(status).toContainText("本轮已完成");
  const paragraphs = pending.locator(".agent-bubble p");
  await expect(paragraphs.first()).toHaveText(/已查询 NR 库内 \d+ 条新闻。/);
  await expect(paragraphs.last()).toHaveText("你的关注方向会保存在独立工作区，讨论可以随时继续。");
  expect(await page.evaluate(() => window.bubbleUpdates)).toEqual([await pending.locator(".agent-bubble").innerText()]);
  expect(gets.filter(url => url.search !== "?status_only=1").map(url => url.search)).toEqual(["?after=" + replyId]);
  const beforeResume = gets.length;
  const resumed = page.waitForResponse(res => new URL(res.url()).search === "?after=" + (replyId + 1));
  await page.evaluate(() => {
    document.dispatchEvent(new Event("visibilitychange"));
    document.dispatchEvent(new Event("visibilitychange"));
  });
  expect((await (await resumed).json()).messages).toEqual([]);
  expect(gets.slice(beforeResume).map(url => url.search)).toEqual(["?after=" + (replyId + 1)]);
  await expect(stream.locator(".assistant")).toHaveCount(1);
});

test("历史每页 30 条，前台增量保留已载入的旧消息和阅读位置", async ({page,request}) => {
  test.setTimeout(60000);
  const base = "/api/personal-assistant/threads/main";
  async function anotherTurn(text) {
    const response = await request.post(base + "/messages", {data:{text, request_id:crypto.randomUUID()}});
    expect(response.ok()).toBe(true);
    await expect.poll(async () => (await (await request.get(base + "?status_only=1")).json()).job.status,
      {intervals:[50,100]}).toBe("done");
  }
  for (let n = 1; n <= 17; n++) await anotherTurn("历史问题 " + n);
  await ready(page);await open(page);
  const stream = page.locator("#personalAssistantMessages"), rows = stream.locator("[data-message-id]");
  const jump = page.getByRole("button",{name:"回到底部"});
  await expect(rows).toHaveCount(30);
  await stream.evaluate(el => {el.scrollTop=0;});
  await expect(jump).toBeVisible();
  const firstId = await rows.first().getAttribute("data-message-id");
  const anchor = stream.locator(`[data-message-id="${firstId}"]`);
  const position = () => anchor.evaluate(el => el.getBoundingClientRect().top);
  const top = await position();
  const older = page.waitForResponse(res => new URL(res.url()).search === "?before=" + firstId);
  await stream.getByRole("button",{name:"载入更早消息"}).click();
  expect((await (await older).json()).messages).toHaveLength(4);
  await expect(rows).toHaveCount(34);
  await expect.poll(async () => Math.abs(await position() - top)).toBeLessThanOrEqual(2);
  await anotherTurn("另一个窗口提交的消息");
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await expect(rows).toHaveCount(36);
  await expect(stream.locator(".user").last()).toContainText("另一个窗口提交的消息");
  expect(await rows.evaluateAll(nodes => new Set(nodes.map(node => node.dataset.messageId)).size)).toBe(36);
  await expect(rows.first()).toContainText("历史问题 1");
  expect(Math.abs(await position() - top)).toBeLessThanOrEqual(2);
  await expect(jump).toBeVisible();
  const unread = async () => (await (await request.get("/api/personal-assistant/reply-notice")).json()).unread_job_id;
  expect(await unread()).toBeTruthy();
  await jump.click();
  await expect(jump).toBeHidden();
  await expect.poll(unread).toBeNull();
  const lastId = Number(await rows.last().getAttribute("data-message-id"));
  await page.locator("#personalAssistantBtn").click();
  for (let n = 1; n <= 16; n++) await anotherTurn("离开期间的问题 " + n);
  const reset = page.waitForResponse(res => new URL(res.url()).search === "?after=" + (lastId + 1));
  await open(page);
  const latest = await (await reset).json();
  expect(latest.reset).toBe(true);
  expect(latest.messages).toHaveLength(30);
  await expect(rows).toHaveCount(30);
  await expect(rows.first()).toHaveAttribute("data-message-id", String(latest.messages[0].id));
  await expect(stream.getByRole("button",{name:"载入更早消息"})).toBeAttached();
});

test("另一窗口原轮重试后，最终增量请求可从断线恢复", async ({page,request}) => {
  await request.post("/__test/personal-assistant", {data:{mode:"fail"}});
  await ready(page);await open(page);
  const stream = page.locator("#personalAssistantMessages"), status = page.locator("#personalAssistantStatus");
  await page.locator("#personalAssistantInput").fill("重新核对来源");
  await page.locator("#personalAssistantSend").click();
  await expect(status).toContainText("本轮未完成");
  const base = "/api/personal-assistant/threads/main";
  const failed = await (await request.get(base)).json();
  const [user, reply] = failed.messages;
  await request.post("/__test/personal-assistant", {data:{mode:"writing"}});
  const newJob = crypto.randomUUID();
  const sent = await request.post(base + "/messages", {data:{retry_job:failed.job.id,request_id:newJob}});
  expect(sent.ok()).toBe(true);
  const retry = page.waitForResponse(res => new URL(res.url()).search === "?after=" + user.id);
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  expect((await (await retry).json()).messages.map(m => [m.id, m.job_id])).toEqual([[user.id,newJob],[reply.id,newJob]]);
  await expect(status).toHaveText("正在回复…");
  await expect(stream.locator(".assistant .agent-bubble")).toHaveText("正在整理资料…");
  let attempts = 0;
  await page.route(url => url.pathname.endsWith("/threads/" + failed.thread.id) && url.search === "?after=" + reply.id,
    route => ++attempts === 1 ? route.abort("connectionreset") : route.continue());
  await request.post("/__test/personal-assistant", {data:{mode:"success"}});
  await expect(status).toContainText("本轮已完成", {timeout:10000});
  expect(attempts).toBe(2);
  await expect(stream.locator("[data-message-id]")).toHaveCount(2);
  await expect(stream.locator(".assistant")).toHaveAttribute("data-message-id", String(reply.id));
  await expect(stream.locator(".assistant")).toContainText("讨论可以随时继续");
});

for (const width of [390,1440]) test(`联网进度和停止后的重试保留草稿 ${width}`, async ({page,request}) => {
  await request.post("/__test/personal-assistant",{data:{mode:"web_search"}});
  await page.setViewportSize({width,height:900});await ready(page);await open(page);
  const input=page.locator("#personalAssistantInput"), status=page.locator("#personalAssistantStatus");
  await input.fill("上次那条新闻后来怎么样了？");await page.locator("#personalAssistantSend").click();
  await expect(status).toContainText("正在搜索网页");
  await request.post("/__test/personal-assistant",{data:{mode:"web_fetch"}});
  await expect(status).toContainText("正在抓取来源网页");
  await input.fill("下一条问题的草稿");
  await page.getByRole("button",{name:"停止回复",exact:true}).click();await expect(status).toContainText("已停止");
  await request.post("/__test/personal-assistant",{data:{mode:"success"}});
  await page.locator("#personalAssistantRetry").click();await expect(status).toContainText("本轮已完成");
  await expect(input).toHaveValue("下一条问题的草稿");
  await expect(page.locator("#personalAssistantMessages .user")).toHaveCount(1);
});

for (const width of [390,1440]) test(`已停止的空回复不会在下一轮重新显示为生成中 ${width}`, async ({page,request}) => {
  await request.post("/__test/personal-assistant", {data:{mode:"wait_empty"}});
  await page.setViewportSize({width,height:900});await ready(page);await open(page);
  await page.locator("#personalAssistantInput").fill("第一轮");
  await page.locator("#personalAssistantSend").click();
  await expect(page.locator("#personalAssistantMessages")).toContainText("正在整理资料");
  await page.getByRole("button",{name:"停止回复",exact:true}).click();
  await expect(page.locator("#personalAssistantStatus")).toContainText("已停止");
  await expect(page.locator("#personalAssistantMessages .assistant").first()).toContainText("本轮没有生成回复");
  await page.locator("#personalAssistantInput").fill("第二轮");
  await page.locator("#personalAssistantSend").click();
  await expect(page.getByRole("button",{name:"停止回复",exact:true})).toBeEnabled();
  await expect(page.locator("#personalAssistantMessages .assistant").first()).toContainText("本轮没有生成回复");
  await expect(page.locator("#personalAssistantMessages .assistant").last()).toContainText("正在整理资料");
  await page.getByRole("button",{name:"停止回复",exact:true}).click();
  await expect(page.locator("#personalAssistantStatus")).toContainText("已停止");
});

for (const width of [320,390,920,921,1279,1280,1440]) test(`个人助手在主题与屏宽 ${width} 下可操作`,async({page})=>{
  await page.setViewportSize({width,height:900});await ready(page);
  for(const [theme,colorScheme] of [["light","light"],["dark","dark"],["system","light"],["system","dark"],["eink","light"]]) {
    await page.emulateMedia({colorScheme});await chooseTheme(page,theme);await open(page);
    const panel=page.locator("#personalAssistantPanel");
    const bounds=await panel.boundingBox();
    expect(bounds.x + bounds.width).toBe(width);
    expect(bounds.width).toBe(width <= 920 ? width : 440);
    expect(bounds.y).toBe(await page.locator(".topbar").evaluate(el => el.getBoundingClientRect().bottom));
    expect(bounds.y + bounds.height).toBe(900);
    expect(await page.locator(".topbar-right").evaluate(el => el.lastElementChild.id)).toBe("personalAssistantBtn");
    expect(await panel.evaluate(el=>el.scrollWidth<=el.clientWidth+1)).toBe(true);
    await expect(page.locator("#personalAssistantSend")).toBeVisible();
    await expect(page.locator("#personalAssistantInput")).toBeVisible();
    const input=page.locator("#personalAssistantInput");
    await input.fill("正在整理的草稿");
    await expect(panel.locator(".agent-header button")).toHaveCount(0);
    if (width > 920) {
      expect(await page.locator(".workspace-view").evaluate(el=>el.getBoundingClientRect().right)).toBe(bounds.x);
    } else {
      expect(await page.locator("#feedView").evaluate(el=>el.inert)).toBe(true);
    }
    await expect(input).toHaveValue("正在整理的草稿");
    if(width===390||width===1440) await page.screenshot({path:`.ui-artifacts/personal-${width}-${theme}-${colorScheme}.png`});
    await page.locator("#personalAssistantBtn").click();
    await expect(page.locator("#personalAssistantBtn")).toBeFocused();
  }
  await open(page);await openAssistantSettings(page);
  await page.locator('[data-personal-field="name"]').fill("等待保存的名字");
  expect(await page.locator("#personalAssistantSettings").evaluate(el=>el.scrollWidth<=el.clientWidth+1)).toBe(true);
  await page.screenshot({path:`.ui-artifacts/personal-settings-${width}.png`});
});

test("大字号、手机键盘缩小视口与输入法不触发误发送", async({page,request})=>{
  await page.setViewportSize({width:390,height:844});await ready(page);await open(page);
  await page.locator("#personalAssistantInput").fill("一段很长的想法\n".repeat(10));
  await page.locator("#personalAssistantInput").dispatchEvent("keydown",{key:"Enter",code:"Enter",isComposing:true});
  expect((await (await request.get("/api/personal-assistant/threads")).json()).threads).toHaveLength(0);
  for(const height of [460,390,844]) {
    await page.setViewportSize({width:390,height});
    await expect.poll(async()=>{const box=await page.locator("#personalAssistantSend").boundingBox();return box.y+box.height;}).toBeLessThanOrEqual(height);
  }
  await page.locator("#personalAssistantBtn").click();
  await page.getByRole("combobox",{name:"阅读字号",exact:true}).click();await page.getByRole("option",{name:"特大（130%）",exact:true}).click();
  await open(page);await send(page,"展示正文阅读字号");
  expect(await page.locator("#personalAssistantMessages .agent-bubble").first().evaluate(el=>parseFloat(getComputedStyle(el).fontSize))).toBeGreaterThan(14);
});

test("整理上下文期间可继续写草稿并停止",async({page,request})=>{
  await request.post("/__test/personal-assistant",{data:{mode:"compacting"}});
  await ready(page);await open(page);
  const input=page.locator("#personalAssistantInput");
  await input.fill("继续讨论");await page.locator("#personalAssistantSend").click();
  await expect(page.locator("#personalAssistantStatus")).toHaveText("正在整理上下文");
  await input.fill("下一条草稿");await page.getByRole("button",{name:"停止回复"}).click();
  await expect(page.locator("#personalAssistantStatus")).toContainText("已停止");
  await expect(input).toHaveValue("下一条草稿");
});
