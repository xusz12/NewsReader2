import { test, expect } from "@playwright/test";
import { chooseTheme, answerConfirm } from "./helpers.js";

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
  await page.locator("#personalAssistantBtn").click();
  await expect(page.locator("#personalAssistantName")).toHaveText(name);
  await expect(page.locator("#personalAssistantStatus")).not.toContainText("正在读取");
}
async function send(page, text) {
  await page.locator("#personalAssistantInput").fill(text);
  await page.locator("#personalAssistantSend").click();
  await expect(page.locator("#personalAssistantMessages")).toContainText("已查询 NR 库内");
  await expect(page.locator("#personalAssistantStatus")).toContainText("本轮已完成");
}
async function settings(page) {
  await page.locator("#personalAssistantSettingsBtn").click();
  await expect(page.locator("#settingsDetailTitle")).toHaveText("个人助手");
  await expect(page.locator('[data-personal-field="name"]')).toHaveValue("测试助手");
}

for (const width of [390,1440]) test(`首次名称留空，由用户命名后开始讨论 ${width}`, async ({page,request}) => {
  await request.post("/__test/personal-assistant", {data:{reset:true}});
  await page.setViewportSize({width,height:900});await ready(page);await open(page,"个人助手");
  await expect(page.locator("#personalAssistantInput")).toBeDisabled();
  await expect(page.locator("#personalAssistantSend")).toBeDisabled();
  await page.screenshot({path:`.ui-artifacts/personal-unnamed-${width}.png`});
  await page.getByRole("button",{name:"为助手命名",exact:true}).click();
  const name=page.locator('[data-personal-field="name"]');
  await expect(name).toHaveValue("");
  await expect(page.locator("#personalSettingsStatus")).not.toContainText("名字不能为空");
  await name.fill("我取的名字");
  await page.getByRole("button",{name:"保存个人助手",exact:true}).click();
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
  await page.locator("#personalAssistantNew").click();
  await expect(page.locator("#personalAssistantMessages")).not.toContainText("而不是只看");
  await send(page,"查看最近的新闻");
  await page.locator("#personalAssistantClose").click();
  await page.locator("#feedCardList .news-card-title").nth(1).click();
  await page.locator("#personalAssistantBtn").click();
  await expect(page.locator("#personalAssistantMessages")).toContainText("查看最近的新闻");
  await page.reload();await open(page);
  await expect(page.locator("#personalAssistantMessages")).toContainText("查看最近的新闻");
  await page.locator('#personalThreadPicker [role="combobox"]').click();
  await page.getByRole("option", {name:first.title,exact:true}).click();
  await expect(page.locator("#personalAssistantMessages")).toContainText("而不是只看当天价格");
  await settings(page);
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
  expect((await (await request.get("/api/personal-assistant/threads/"+first.id)).json()).messages).toHaveLength(2);
  expect(errors).toEqual([]);
});

test("名字与 runtime 独立保存，原有模型设置不被改动", async ({page,request}) => {
  const original=(await (await request.get("/api/settings")).json()).agent;
  await ready(page);await open(page);await settings(page);
  await page.locator('[data-personal-field="name"]').fill("望远");
  await page.getByRole("combobox",{name:"Runtime",exact:true}).click();
  await page.getByRole("option",{name:"Codex",exact:true}).click();
  await page.getByRole("button",{name:"保存个人助手",exact:true}).click();
  await expect(page.locator("#personalSettingsStatus")).toHaveText("个人助手已保存");
  await page.locator("#personalAssistantBtn").click();
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
  await page.locator("#personalAssistantClose").click();
  await page.locator("#personalAssistantBtn").click();
  await expect(page.locator("#personalAssistantInput")).toHaveValue("下一条讨论的草稿");
  await page.getByRole("button",{name:"停止回复",exact:true}).click();
  await expect(page.locator("#personalAssistantStatus")).toContainText("已停止");
  await request.post("/__test/personal-assistant", {data:{mode:"fail"}});
  await page.locator("#personalAssistantSend").click();
  await expect(page.locator("#personalAssistantStatus")).toContainText("本轮未完成");
  await request.post("/__test/personal-assistant", {data:{mode:"success"}});
  await send(page,"继续讨论");
});

for (const width of [320,390,920,921,1279,1280,1440]) test(`个人助手在主题与屏宽 ${width} 下可操作`,async({page})=>{
  await page.setViewportSize({width,height:900});await ready(page);
  for(const [theme,colorScheme] of [["light","light"],["dark","dark"],["system","light"],["system","dark"],["eink","light"]]) {
    await page.emulateMedia({colorScheme});await chooseTheme(page,theme);await open(page);
    const panel=page.locator("#personalAssistantPanel");
    const bounds=await panel.boundingBox();
    expect(bounds.x).toBeGreaterThanOrEqual(0);expect(bounds.x+bounds.width).toBeLessThanOrEqual(width+1);
    expect(bounds.y+bounds.height).toBeLessThanOrEqual(901);
    expect(await panel.evaluate(el=>el.scrollWidth<=el.clientWidth+1)).toBe(true);
    await expect(page.locator("#personalAssistantSend")).toBeVisible();
    await expect(page.locator("#personalAssistantInput")).toBeVisible();
    if(width===390||width===1440) await page.screenshot({path:`.ui-artifacts/personal-${width}-${theme}-${colorScheme}.png`});
    await page.locator("#personalAssistantClose").click();
    await expect(page.locator("#personalAssistantBtn")).toBeFocused();
  }
  await open(page);await settings(page);
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
  await page.locator("#personalAssistantClose").click();
  await page.getByRole("combobox",{name:"阅读字号",exact:true}).click();await page.getByRole("option",{name:"特大（130%）",exact:true}).click();
  await open(page);await send(page,"展示正文阅读字号");
  expect(await page.locator("#personalAssistantMessages .agent-bubble").first().evaluate(el=>parseFloat(getComputedStyle(el).fontSize))).toBeGreaterThan(14);
});
