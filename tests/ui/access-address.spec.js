import { test, expect } from "@playwright/test";
import { openSettings, openSettingsItem } from "./helpers.js";

const privateAddress = {kind:"serve", url:"https://desktop-newsreader.private-tailnet.ts.net:8443/"};
const readingAddress = {kind:"tailscale", url:"http://100.64.1.2:18080/"};
const endpoint = "**/api/settings/access-address";

test("访问地址可搜索和复制，重查失败也保留阅读偏好草稿", async ({page}) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {value:{
      writeText: async text => { window.copiedAddress = text; },
    }});
  });
  let result = privateAddress;
  let failed = false;
  let checking;
  await page.route(endpoint, async route => {
    if (checking) await checking;
    await route.fulfill(failed ? {status:503, json:{ok:false}} : {json:{ok:true, address:result}});
  });
  await page.goto("/");
  await openSettings(page);
  await openSettingsItem(page, "appearance");
  await page.getByRole("combobox", {name:"阅读主题"}).click();
  await page.getByRole("option", {name:"墨水屏 (E-Ink)", exact:true}).click();
  const save = page.locator("#settingsSaveBtn");
  await expect(save).toBeVisible();
  const search = page.getByRole("searchbox", {name:"搜索全部设置"});
  await search.fill("手机网址");
  await search.press("Enter");
  const card = page.getByRole("region", {name:"访问地址"});
  const copy = card.getByRole("button", {name:"复制", exact:true});
  const refresh = card.getByRole("button", {name:"重新检测"});
  await expect(card).toBeFocused();
  await expect(card.getByText(privateAddress.url)).toBeVisible();
  await copy.click();
  expect(await page.evaluate(() => window.copiedAddress)).toBe(privateAddress.url);
  await expect(card.getByRole("status")).toHaveText("已复制访问地址。");

  let finish;
  checking = new Promise(resolve => { finish = resolve; });
  failed = true;
  await refresh.click();
  await expect(card.getByRole("status")).toContainText("正在检测");
  await expect(copy).toBeHidden();
  finish();
  await expect(card.getByRole("status")).toContainText("检测失败");
  await expect(card.getByText(privateAddress.url)).toBeHidden();
  await expect(save).toBeVisible();
  await expect(page.getByRole("combobox", {name:"阅读主题"})).toHaveText("墨水屏 (E-Ink)");

  failed = false;
  result = null;
  await refresh.click();
  await expect(card.getByRole("status")).toContainText("本机入口");
  await expect(copy).toBeHidden();
  result = readingAddress;
  await refresh.click();
  await expect(card.getByText(readingAddress.url)).toBeVisible();
  await expect(card.getByRole("status")).toContainText("阅读");
});

test("从阅读偏好滚到地址卡时自动检测，并持续复核可见地址", async ({page}) => {
  await page.setViewportSize({width:390, height:560});
  await page.clock.install();
  let result = privateAddress;
  let checks = 0;
  await page.route(endpoint, async route => {
    checks++;
    await route.fulfill({json:{ok:true, address:result}});
  });
  await page.goto("/");
  await openSettings(page);
  await openSettingsItem(page, "appearance");
  const card = page.getByRole("region", {name:"访问地址"});
  await card.scrollIntoViewIfNeeded();
  await expect(card.getByText(privateAddress.url)).toBeVisible();
  const initialChecks = checks;
  await page.clock.fastForward(31_000);
  await expect.poll(() => checks).toBeGreaterThan(initialChecks);
  await expect(card.getByRole("button", {name:"复制", exact:true})).toBeVisible();

  result = readingAddress;
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect(card.getByText(readingAddress.url)).toBeVisible();
  result = null;
  await page.clock.fastForward(31_000);
  await expect(card.getByRole("status")).toContainText("本机入口");
  await expect(card.getByRole("button", {name:"复制", exact:true})).toBeHidden();
  result = privateAddress;
  await page.clock.fastForward(31_000);
  await expect(card.getByText(privateAddress.url)).toBeVisible();
  await expect(card.getByRole("button", {name:"复制", exact:true})).toBeVisible();

  await page.locator("#settingsCategoryBackBtn").click();
  const closedChecks = checks;
  await page.clock.fastForward(31_000);
  expect(checks).toBe(closedChecks);
  await openSettingsItem(page, "access-address");
  await expect(card.getByText(privateAddress.url)).toBeVisible();
});

test("手机普通 HTTP 页面使用共享复制回退并可反馈失败", async ({page, baseURL}) => {
  await page.setViewportSize({width:390, height:844});
  await page.addInitScript(() => {
    document.execCommand = command => {
      if (command !== "copy" || window.failCopy) return false;
      const input = document.activeElement;
      window.copiedAddress = input.value.slice(input.selectionStart, input.selectionEnd);
      return true;
    };
  });
  await page.route(/^http:\/\/nr-access\.test\//, async route => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/settings/access-address")
      return route.fulfill({json:{ok:true, address:readingAddress}});
    const response = await route.fetch({url:new URL(url.pathname + url.search, baseURL).href});
    await route.fulfill({response});
  });
  await page.goto("http://nr-access.test/");
  expect(await page.evaluate(() => window.isSecureContext)).toBe(false);
  await openSettings(page);
  await openSettingsItem(page, "access-address");
  const card = page.getByRole("region", {name:"访问地址"});
  const copy = card.getByRole("button", {name:"复制", exact:true});
  await copy.click();
  expect(await page.evaluate(() => window.copiedAddress)).toBe(readingAddress.url);
  await expect(card.getByRole("status")).toHaveText("已复制访问地址。");
  await page.evaluate(() => { window.failCopy = true; });
  await copy.click();
  await expect(card.getByRole("status")).toContainText("复制失败");
});

test("不同主题和屏宽都完整显示长网址并保留紧凑复制控件", async ({page}) => {
  const address = {...privateAddress, url:"https://" + "device-".repeat(7) + "mac." + "tailnet-".repeat(7) + "name.ts.net:8443/"};
  await page.route(endpoint, route => route.fulfill({json:{ok:true, address}}));
  for (const width of [1440, 390]) {
    await page.setViewportSize({width, height:844});
    await page.goto("/");
    await openSettings(page);
    await openSettingsItem(page, "access-address");
    const card = page.getByRole("region", {name:"访问地址"});
    const url = card.getByText(address.url);
    const copy = card.getByRole("button", {name:"复制", exact:true});
    for (const [theme, colorScheme] of [["light", "light"], ["dark", "dark"], ["eink", "light"], ["system", "light"], ["system", "dark"]]) {
      await page.emulateMedia({colorScheme});
      await page.locator("html").evaluate((html, theme) => {
        html.dataset.theme = theme;
        html.dataset.fontSize = "large";
      }, theme);
      await expect(url).toBeVisible();
      expect(await url.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
      if (width === 390) expect((await copy.boundingBox()).height).toBeLessThanOrEqual(27);
    }
  }
});
