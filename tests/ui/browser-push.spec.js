import { test, expect } from "@playwright/test";
import { collectionHeatmap, openSettings, openSettingsItem } from "./helpers.js";

const httpsOrigin = "https://nr-notifications.test";
const insecureOrigin = "http://nr-notifications.test";
const types = ["daily_report", "collection_error", "personal_reply", "article_reply"];

async function privatePage(page, baseURL, {mobile = false, standalone = true} = {}) {
  await page.addInitScript(({mobile, standalone}) => {
    const key = "synthetic-notification-subscription";
    const subscription = {
      toJSON: () => ({endpoint:"https://fcm.googleapis.com/fcm/send/browser-test", keys:{p256dh:"synthetic", auth:"synthetic"}}),
      async unsubscribe() { localStorage.removeItem(key); return true; },
    };
    const registration = {pushManager:{
      getSubscription: async () => localStorage.getItem(key) ? subscription : null,
      async subscribe(options) {
        window.subscriptionOptions = {visible:options.userVisibleOnly, keyLength:options.applicationServerKey.length};
        localStorage.setItem(key, "yes");
        return subscription;
      },
    }};
    const serviceWorker = Object.assign(new EventTarget(), {
      register: async () => registration, ready: Promise.resolve(registration), getRegistration: async () => registration,
    });
    Object.defineProperty(navigator, "serviceWorker", {value:serviceWorker});
    if ("Notification" in window)
      Object.defineProperty(Notification, "permission", {configurable:true, get:() => localStorage.getItem(key) ? "granted" : "default"});
    if (mobile) {
      Object.defineProperty(navigator, "userAgent", {value:"iPhone"});
      Object.defineProperty(navigator, "standalone", {value:standalone});
    }
  }, {mobile, standalone});
  await page.route(/^https?:\/\/nr-notifications\.test\//, async route => {
    const url = new URL(route.request().url());
    const response = await route.fetch({url: new URL(url.pathname + url.search, baseURL).href});
    return route.fulfill({response});
  });
}

test("本浏览器订阅、即时类型开关与取消在重新加载后生效", async ({page, baseURL}) => {
  await privatePage(page, baseURL);
  let choices = null;
  const methods = [];
  await page.route("**/api/browser-push/config", route => route.fulfill({json:{ok:true, available:true, public_key:"B" + "A".repeat(86)}}));
  await page.route("**/api/browser-push/device", route => {
    const method = route.request().method();
    methods.push(method);
    if (method === "PUT") choices = route.request().postDataJSON().types;
    if (method === "DELETE") choices = null;
    return route.fulfill({json:{ok:true, registered:choices !== null, types:choices || []}});
  });
  await page.goto(httpsOrigin);
  await openSettings(page);
  await openSettingsItem(page, "browser-push");
  const card = page.locator("#browserPushSettings");
  await expect(card.getByRole("status")).toHaveText("本浏览器尚未订阅。");
  expect(methods).toEqual([]);
  await card.getByRole("button", {name:"启用本浏览器通知"}).click();
  await expect(card.getByRole("status")).toHaveText("本浏览器已订阅。");
  expect(await page.evaluate(() => window.subscriptionOptions)).toEqual({visible:true, keyLength:65});
  expect(choices).toEqual(types);
  await card.getByRole("switch", {name:"日报完成"}).uncheck();
  await expect.poll(() => choices).toEqual(types.slice(1));
  await page.reload();
  await openSettings(page);
  await openSettingsItem(page, "browser-push");
  await expect(card.getByRole("switch", {name:"日报完成"})).not.toBeChecked();
  await expect(card.getByRole("switch", {name:"个人助手回复"})).toBeChecked();
  await card.getByRole("button", {name:"取消本浏览器订阅"}).click();
  await expect(card.getByRole("status")).toHaveText("本浏览器尚未订阅。");
  expect(choices).toBeNull();
  expect(await page.evaluate(() => localStorage.getItem("synthetic-notification-subscription"))).toBeNull();
});

test("手机浏览器显示主屏幕前提，普通 HTTP 不开放订阅", async ({page, baseURL}) => {
  await page.setViewportSize({width:390, height:844});
  await privatePage(page, baseURL, {mobile:true, standalone:false});
  await page.goto(httpsOrigin);
  await openSettings(page);
  await openSettingsItem(page, "browser-push");
  const card = page.locator("#browserPushSettings");
  await expect(card.getByRole("status")).toContainText("添加到主屏幕");
  await expect(card.getByRole("button", {name:"启用本浏览器通知"})).toHaveCount(0);
  await page.goto(insecureOrigin);
  await openSettings(page);
  await openSettingsItem(page, "browser-push");
  await expect(card.getByRole("status")).toContainText("私有 HTTPS 地址");
  await expect(card.getByRole("button", {name:"启用本浏览器通知"})).toHaveCount(0);
});

test("开发回环地址使用 Chromium 原生后台脚本和 PushManager", async ({page, context, baseURL}) => {
  await context.grantPermissions(["notifications"], {origin:baseURL});
  await page.route("**/api/browser-push/config", route => route.fulfill({json:{ok:true, available:true, public_key:"B" + "A".repeat(86)}}));
  await page.goto("/");
  await openSettings(page);
  await openSettingsItem(page, "browser-push");
  const native = await page.evaluate(async () => {
    const registration = await navigator.serviceWorker.ready;
    return {secure:window.isSecureContext, push:registration.pushManager instanceof PushManager,
      script:registration.active.scriptURL, permission:Notification.permission};
  });
  expect(native).toMatchObject({secure:true, push:true, script:`${baseURL}/push-sw.js`});
  if (native.permission === "denied") {
    await expect(page.locator("#browserPushSettings").getByRole("status")).toContainText("通知权限已关闭");
  } else {
    await expect(page.getByRole("button", {name:"启用本浏览器通知"})).toBeEnabled();
  }
});

test("采集通知展开指定日期的失败批次", async ({page}) => {
  const id = "a".repeat(32), day = "2026-11-09";
  await page.route("**/api/error-stats?*", route => route.fulfill({json:{ok:true, today:day, day, unresolved_count:1,
    records:[{run_id:id, source_id:"source-one", label:"测试来源", time:"09:00:00", date:day, status:"failed", error_count:1, error:"TIMEOUT"}],
    heatmap:collectionHeatmap(day, day, {[day]:1}), stats:{error_count:1},
  }}));
  await page.goto(`/?nr_push=collection_error&id=${id}&day=${day}`);
  const batch = page.locator(`[data-upstream-batch="${id}"]`);
  await expect(batch).toBeVisible();
  await expect(batch.locator("details")).toHaveAttribute("open", "");
  await expect(page.getByRole("button", {name:"仅错误与提示", exact:true})).toHaveAttribute("aria-pressed", "true");
});

test("日报通知打开对应报告", async ({page, request}) => {
  await request.post("/__test/reports");
  const id = "b".repeat(32);
  const data = await (await request.get("/api/reports/report-evening")).json();
  await page.route(`**/api/reports/${id}`, route => route.fulfill({json:{...data, report:{...data.report, id}}}));
  await page.goto(`/?nr_push=daily_report&id=${id}`);
  await expect(page.locator("#detailPanelReports")).toHaveClass(/active/);
  await expect(page.locator("#reportContent")).toContainText("合成晚报");
});

test("个人助手通知定位完整回复并回传本浏览器回执", async ({page, baseURL}) => {
  await privatePage(page, baseURL);
  const thread = "c".repeat(32), job = "d".repeat(8) + "-dddd-4ddd-8ddd-" + "d".repeat(12);
  const receipts = [];
  await page.addInitScript(() => localStorage.setItem("synthetic-notification-subscription", "yes"));
  await page.route("**/api/personal-assistant/profile", route => route.fulfill({json:{ok:true, profile:{name:"测试助手", runtime:"pi"}}}));
  await page.route("**/api/personal-assistant/threads", route => route.fulfill({json:{ok:true, threads:[{id:thread, title:"测试讨论", current_library:true}]}}));
  await page.route(`**/api/personal-assistant/threads/${thread}?message=42`, route => route.fulfill({json:{ok:true,
    thread:{id:thread,title:"测试讨论",current_library:true}, messages:[{id:42,job_id:job,role:"assistant",status:"done",content:"通知对应的完整回复。"}],
    job:{id:job,status:"done"},has_more:false,
  }}));
  await page.route("**/api/browser-push/seen", route => {
    receipts.push(...route.request().postDataJSON().replies);
    return route.fulfill({json:{ok:true}});
  });
  await page.goto(`${httpsOrigin}/?nr_push=personal_reply&id=${thread}&message=42`);
  await expect(page.locator("#personalAssistantMessages [data-message-id='42']")).toBeFocused();
  await expect.poll(() => receipts).toEqual([{kind:"personal_reply", id:job}]);
});

test("文章问答通知定位指定会话的最终回复", async ({page, request}) => {
  await request.post("/__test/reset-agent");
  const answer = "系统通知打开了指定会话的最终回复。";
  const response = await request.post("/__test/agent-complete", {data:{item_id:"ui-000", answer}});
  expect(response.ok()).toBe(true);
  const {job_id} = await response.json();
  await page.goto(`/?nr_push=article_reply&id=${job_id}`);
  const reply = page.locator(`#agentChatStream [data-job-id="${job_id}"][data-role="assistant"]`);
  await expect(reply).toHaveText(answer);
  await expect(reply).toBeFocused();
});

test("Chromium 后台脚本在 NR 页面离开后建立系统通知", async ({page, context, baseURL}) => {
  await page.goto("/");
  const workerCreated = context.waitForEvent("serviceworker");
  await page.evaluate(async () => {
    await navigator.serviceWorker.register("/push-sw.js");
    await navigator.serviceWorker.ready;
  });
  const worker = await workerCreated;
  await page.goto("/static/manifest.webmanifest");
  const id = "e".repeat(32), tag = "f".repeat(32);
  const result = await worker.evaluate(async ({id, tag}) => {
    const displays = [], work = [], opened = [];
    self.registration.showNotification = async (title, options) => displays.push({title, tag:options.tag, url:options.data.url});
    const event = new Event("push");
    event.data = {json:() => ({kind:"daily_report", tag, title:"日报已完成", body:"查看报告", target:{id}})};
    event.waitUntil = promise => work.push(promise);
    self.dispatchEvent(event);
    await Promise.all(work);
    self.clients.matchAll = async () => [];
    self.clients.openWindow = async url => opened.push(url);
    for (const url of [displays[0].url, "https://untrusted.example/"]) {
      const click = new Event("notificationclick");
      click.notification = {data:{url},close() {}};
      click.waitUntil = promise => work.push(promise);
      self.dispatchEvent(click);
    }
    await Promise.all(work);
    return {displays, opened};
  }, {id, tag});
  const url = `${baseURL}/?nr_push=daily_report&id=${id}`;
  expect(result).toEqual({displays:[{title:"日报已完成", tag, url}], opened:[url]});
});
