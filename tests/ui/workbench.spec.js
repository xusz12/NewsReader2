import { chooseTheme } from "./helpers.js";
import { test, expect } from "@playwright/test";
import fs from "node:fs";
test.beforeEach(async ({ request }) => {
  const settings = await (await request.get("/api/settings")).json();
  await request.put("/api/settings", {
    data: {
      llm: settings.llm,
      agent: settings.agent,
      feed: { hidden_source_subkeys: [] },
    },
  });
});
async function ready(page) {
  await page.goto("/");
  await expect(page.locator("#feedCardList .news-card").first()).toBeVisible();
  await page.locator('#feedColFeed [data-filter="all"]').click();
  await page.locator("#sortNewsBtn").click();
  await expect(page.locator('[data-id="ui-000"]')).toBeVisible();
  await expect(page.locator("#articleBodyContent")).toBeVisible();
}
async function select(page, id = "ui-000") {
  if (!await page.locator(`[data-id="${id}"]`).count()) {
    await page.locator('#feedColFeed [data-filter="all"]').click();
    if ((await page.locator("#sortNewsBtn").getAttribute("aria-label")).includes("旧到新")) await page.locator("#sortNewsBtn").click();
    await expect(page.locator(`[data-id="${id}"]`)).toBeVisible();
  }
  await page.locator(`[data-id="${id}"] .news-card-title`).click();
  await expect(page.locator("#articleBodyContent")).toBeVisible();
}

test("independent release shows its version and disabled update source", async ({
  page,
}) => {
  await page.route("**/api/update/check?*", route => route.fulfill({ json: {
    ok: true, enabled: false, current_version: "v0.0.11", available: false,
    message: "独立项目尚未配置更新仓库，在线更新未启用。",
  } }));
  await ready(page);
  await expect(page).toHaveTitle("News Reader v0.0.11");
  await expect(page.locator(".brand-version")).toHaveText("v0.0.11");
  await page.keyboard.press(",");
  await page.locator('[data-settings-tab="system"]').click();
  await page.locator("#checkUpdateBtn").click();
  await expect(page.locator("#updateResult")).toContainText(
    "独立项目尚未配置更新仓库",
  );
  await expect(page.locator("#applyUpdateBtn")).toBeHidden();
  await page.locator("#settingsContent summary").click();
  await expect(page.locator("#releaseNotes")).toContainText("v0.0.1");
  await expect(page.locator("#releaseNotes")).toContainText(
    "作为独立项目开始版本管理",
  );
});

test("three-column shell, real data and retired feature removal", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await ready(page);
  await expect(page.locator("#leftNavDrawer")).toBeVisible();
  await expect(page.locator("#feedColFeed")).toHaveCSS("width", "430px");
  await expect(page.locator("#leftNavDrawer")).toHaveCSS("width", "248px");
  await expect(page.locator("#navFeedBtn")).toHaveAttribute(
    "aria-current",
    "page",
  );
  await expect(
    page.getByRole("button", { name: /^(日报|提醒|复盘|跟踪|板块)$/ }),
  ).toHaveCount(0);
  expect(await page.locator('script[src*="news_summaries"]').count()).toBe(0);
  await select(page);
  await expect(page.locator("#articleMainTitle")).toContainText("美联储");
  expect(errors).toEqual([]);
});
test("read flags persist and card/reader stay synchronized", async ({
  page,
  request,
}) => {
  await request.patch("/api/news/ui-000/state", {
    data: { favorite: false, read: false },
  });
  await ready(page);
  await select(page);
  await page.locator("#btnToggleStar").click();
  await expect(
    page.locator('[data-id="ui-000"] [data-state="favorite"]'),
  ).toHaveAttribute("aria-pressed", "true");
  await page.keyboard.press("m");
  await expect(page.locator('[data-id="ui-000"]')).toHaveClass(/read/);
  // The card updates optimistically; persistence completes asynchronously.
  await expect.poll(async () => (await (await request.get("/api/news/ui-000/detail")).json()).read_at).toBeTruthy();
  const detail = await (await request.get("/api/news/ui-000/detail")).json();
  expect(detail.favorite_at).toBeTruthy();
  expect(detail.read_at).toBeTruthy();
  await page.reload();
  await select(page);
  await expect(page.locator("#btnToggleStar")).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await page.locator("#navFavoritesBtn").click();
  await expect(page.locator('[data-id="ui-000"]')).toBeVisible();
});
test("failed state mutation reports error and preserves state", async ({
  page,
  request,
}) => {
  await request.patch("/api/news/ui-000/state", { data: { favorite: false } });
  await ready(page);
  await select(page);
  await page.route("**/api/news/ui-000/state", (route) =>
    route.fulfill({
      status: 500,
      json: { ok: false, error: "database_unavailable" },
    }),
  );
  await page.locator("#btnToggleStar").click();
  await expect(page.locator("#toast")).toHaveClass(/show/);
  await expect(page.locator("#btnToggleStar")).toHaveAttribute(
    "aria-pressed",
    "false",
  );
});
test("search, source filters, pagination and unread cursor", async ({
  page,
}) => {
  await ready(page);
  await page.locator("#feedSearchInput").fill("台积电");
  await expect(page.locator("#feedCardList .news-card").first()).toContainText(
    "台积电",
  );
  expect(await page.locator("#feedCardList .news-card").count()).toBe(13);
  await page.locator("#feedSearchInput").fill("不存在的关键词__");
  await expect(page.locator("#feedCardList")).toContainText("没有符合条件");
  await page.locator("#feedSearchInput").fill("");
  await expect(page.locator("#feedCardList .news-card").first()).toBeVisible();
  await page.locator('[data-source="reuters"]').click();
  await expect(page.locator("#feedCardList .news-card").first()).toContainText(
    "Reuters",
  );
  await page.locator('[data-source="all"]').click();
  await page.locator('[data-filter="unread"]').click();
  await page.locator("#loadMoreBtn").click();
  await expect
    .poll(() => page.locator("#feedCardList .news-card").count())
    .toBeGreaterThan(30);
  const ids = await page
    .locator("#feedCardList .news-card")
    .evaluateAll((nodes) => nodes.map((n) => n.dataset.id));
  expect(new Set(ids).size).toBe(ids.length);
});
test("multi-select controls are removed", async ({ page }) => {
  await ready(page);
  await expect(page.locator('[data-select], #batchActionBar')).toHaveCount(0);
});
test("saved article notes remain available without the removed editor", async ({
  page,
  request,
}) => {
  await request.post('/api/news/ui-000/notes', { data: { note: '重构后的阅读笔记 <script>alert(1)</script>' } });
  await ready(page);
  await select(page);
  await expect(page.locator('#articleNote, #saveArticleNoteBtn, .article-note-editor')).toHaveCount(0);
  await select(page, "ui-001");
  await select(page);
  await expect(page.locator('.saved-note').last()).toContainText('重构后的阅读笔记 <script>alert(1)</script>');
  expect(
    (await (await request.get("/api/news/ui-000/detail")).json()).note.note,
  ).toContain("<script>");
  await page.reload();
  await select(page);
  await expect(page.locator('#articleNote, #saveArticleNoteBtn')).toHaveCount(0);
  await expect(page.locator(".saved-note").last()).toContainText("<script>");
});
test("independent ideas create, edit, search and delete", async ({ page }) => {
  await ready(page);
  await page.locator("#navNotesBtn").click();
  await page.locator("#newIdeaBtn").click();
  await page.locator("#ideaNote").fill("独立测试想法：记录跨日变化");
  await page.locator("#saveIdeaBtn").click();
  await expect(page.locator("#ideasCardList")).toContainText("独立测试想法");
  await page.locator("#ideasSearchInput").fill("独立测试想法");
  await page.locator("#ideasCardList .news-card").click();
  await page.locator("#ideaNote").fill("独立测试想法：补充核心证据");
  await page.locator("#saveIdeaBtn").click();
  await expect(page.locator("#ideasCardList")).toContainText("补充核心证据");
  page.once("dialog", (dialog) => dialog.accept());
  await page.locator("#deleteIdeaBtn").click();
  await expect(page.locator("#ideasCardList")).not.toContainText(
    "独立测试想法",
  );
});
test("settings draft, runtime, connection test and save use real contracts", async ({
  page,
  request,
}) => {
  await ready(page);
  await page.keyboard.press(",");
  await expect(page.locator("#feedColSettings")).toBeVisible();
  await expect(page.locator("#detailPanelSettings")).toBeVisible();
  await page.locator('[data-runtime="codex"]').click();
  await page.locator('[name="agent.codex.provider"]').fill("fixture-provider");
  await page.locator('[name="agent.codex.profile"]').fill("test-profile");
  await page.route("**/api/settings/models", (route) =>
    route.fulfill({
      json: {
        ok: true,
        catalog: {
          options: [
            {
              value: "model-a",
              label: "Model A",
              reasoning_efforts: ["low", "high"],
            },
          ],
          notice: "Fixture catalog",
        },
      },
    }),
  );
  await page.locator("#refreshModelsBtn").click();
  await page.getByRole('combobox', { name: '模型选择 (Model)', exact: true }).click();
  await page.getByRole('option', { name: 'Model A', exact: true }).click();
  await expect(
    page.locator('select[name="agent.codex.reasoning_effort"] option'),
  ).toHaveCount(3);
  let tested;
  await page.route("**/api/settings/connection-test", (route) => {
    tested = route.request().postDataJSON();
    return route.fulfill({
      json: {
        ok: true,
        status: "success",
        message: "连接成功",
        elapsed_ms: 12,
        checked_at: "2026-10-15",
      },
    });
  });
  await page.locator("#testConnectionBtn").click();
  await expect(page.locator("#connTestTerminal")).toContainText("[SUCCESS]");
  expect(tested.codex.provider).toBe("fixture-provider");
  expect(tested.codex.model).toBe("model-a");
  await page.keyboard.press("Escape");
  await page.keyboard.press(",");
  await expect(page.locator('[name="agent.codex.profile"]')).toHaveValue(
    "test-profile",
  );
  await page.locator("#settingsSaveBtn").click();
  await expect(page.locator("#settingsStatus")).toHaveText("设置已保存");
  const saved = await (await request.get("/api/settings")).json();
  expect(saved.agent.codex.model).toBe("model-a");
  expect(saved.agent.runtime).toBe("codex");
});
test("sidebar source filters have no toggles", async ({ page }) => {
  await ready(page);
  await expect(page.locator('#sourceFilters [role="switch"], #sourceFilters input')).toHaveCount(0);
  await page.locator('[data-source="reuters"]').click();
  await expect(page.locator('#feedCardList .news-card').first()).toContainText("Reuters");
});
test("Agent session, streamed result, stop/retry and archive", async ({
  page,
}) => {
  const session = {
    id: "session-ui",
    runtime: "codex",
    model: "fixture-model",
  };
  const job = { id: "job-ui", status: "running", answer_text: "" };
  const payload = {
    session,
    context_available: true,
    messages: [
      { role: "user", content: "解释核心证据", job_id: job.id },
      { role: "assistant", content: "", job_id: job.id },
    ],
    jobs: [job],
  };
  await page.route("**/api/news/*/agent/session", (route) =>
    route.fulfill({
      json: {
        ok: true,
        session,
        context_available: true,
        messages: [],
        jobs: [],
      },
    }),
  );
  await page.route("**/api/news/*/agent/jobs", (route) =>
    route.fulfill({ json: { ok: true, ...payload } }),
  );
  await page.route("**/api/news/*/agent/jobs/job-ui/stream", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: `data: ${JSON.stringify({ ok: true, job: { ...job, answer_text: "已验证的测试回答" } })}\n\n`,
    }),
  );
  await page.route("**/api/news/*/agent/jobs/job-ui", (route) =>
    route.fulfill({
      json: { ok: true, job: { ...job, answer_text: "已验证的测试回答" } },
    }),
  );
  await page.route("**/api/news/*/agent/jobs/job-ui/stop", (route) =>
    route.fulfill({
      json: {
        ok: true,
        job: { ...job, status: "stopped", answer_text: "已验证的测试回答" },
      },
    }),
  );
  await ready(page);
  await select(page);
  await page.keyboard.press("q");
  await expect(page.locator("#agentPromptInput")).toBeEnabled();
  await page.locator("#agentPromptInput").fill("解释核心证据");
  await page.locator("#agentSendBtn").click();
  await expect(page.locator("#agentChatStream")).toContainText(
    "已验证的测试回答",
  );
  await expect(page.getByRole("button", { name: "停止研究" })).toBeVisible();
  await page.route("**/api/news/*/agent/session", (route) =>
    route.fulfill({
      json: {
        ok: true,
        ...payload,
        jobs: [{ ...job, status: "stopped", answer_text: "已验证的测试回答" }],
        messages: [
          payload.messages[0],
          { ...payload.messages[1], content: "已验证的测试回答" },
        ],
      },
    }),
  );
  await page.getByRole("button", { name: "停止研究" }).click();
  await expect(page.locator('[data-retry-job="job-ui"]')).toBeVisible();
  let archived;
  await page.route("**/api/news/*/chat/archive", (route) => {
    archived = route.request().postDataJSON();
    return route.fulfill({
      json: {
        ok: true,
        has_note: 1,
        note: { note: "归档结论" },
        note_preview: "归档结论",
      },
    });
  });
  await page.locator("#archiveAgentBtn").click();
  await expect(page.locator("#toast")).toContainText("结论已追加");
  expect(archived.agent_session_id).toBe("session-ui");
  await expect(page.locator(".saved-note").last()).toContainText("归档结论");
});
test("stale detail response cannot overwrite the selected article", async ({
  page,
}) => {
  await ready(page);
  await page.route("**/api/news/ui-000/detail", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 500));
    await route.continue();
  });
  await page.locator('[data-id="ui-000"] .news-card-title').click();
  await page.locator('[data-id="ui-001"] .news-card-title').click();
  await expect(page.locator("#articleMainTitle")).toContainText("台积电");
  await page.waitForTimeout(700);
  await expect(page.locator("#articleMainTitle")).toContainText("台积电");
});
test("theme persistence, keyboard shortcuts and modal focus", async ({
  page,
}) => {
  await ready(page);
  await expect(page.locator('#openShortcutsBtn')).toHaveCount(0);
  await chooseTheme(page, "dark");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.keyboard.press("/");
  await expect(page.locator("#feedSearchInput")).toBeFocused();
  await page.locator("#feedSearchInput").blur();
  await page.keyboard.press("?");
  await expect(page.locator("#shortcutsModal")).toHaveClass(/show/);
  await page.keyboard.press("Escape");
  await expect(page.locator("#shortcutsModal")).not.toHaveClass(/show/);
  await chooseTheme(page, "eink");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "eink");
});
const viewports = [
  [360, 800],
  [390, 844],
  [430, 932],
  [600, 960],
  [820, 1180],
  [1024, 768],
  [1366, 768],
  [1440, 900],
  [1920, 1080],
];
for (const [width, height] of viewports)
  test(`responsive ${width}×${height}: feed, reader, settings, no overflow`, async ({
    page,
  }) => {
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.setViewportSize({ width, height });
    await page.goto("/");
    await expect(
      page.locator("#feedCardList .news-card").first(),
    ).toBeVisible();
    if (width <= 920)
      await expect(page.locator("#detailPanelFeed")).not.toBeVisible();
    const overflow = () =>
      page.evaluate(() => {
        const nodes = [
          document.documentElement,
          ...document.querySelectorAll(
            ".feed-column.active,.detail-panel.active",
          ),
        ];
        return nodes
          .filter((n) => n.getClientRects().length)
          .filter((n) => n.scrollWidth > n.clientWidth + 1)
          .map((n) => ({
            id: n.id,
            width: n.clientWidth,
            scroll: n.scrollWidth,
          }));
      });
    expect(await overflow()).toEqual([]);
    fs.mkdirSync(".ui-artifacts", { recursive: true });
    await page.screenshot({
      animations: "disabled",
      path: `.ui-artifacts/feed-${width}.png`,
    });
    await page.locator("#feedCardList .news-card-title").first().click();
    await expect(page.locator("#articleMainTitle")).toBeVisible();
    expect(await overflow()).toEqual([]);
    await page.screenshot({
      animations: "disabled",
      path: `.ui-artifacts/reader-${width}.png`,
    });
    if (width <= 920) {
      await page.locator("#agentDockToggleBtn").click();
      await expect(page.locator("#agentDock")).toHaveClass(/mobile-sheet-open/);
      await page.locator("#agentDockToggleBtn").click();
      await page.locator("#mobileReaderBackBtn").click();
      await expect(page.locator("#feedColFeed")).toBeVisible();
      await page.locator("#mobNavSettings").click();
    } else {
      if (width < 1280) await page.locator("#openNavBtn").click();
      await page.locator("#leftNavSettingsBtn").click();
    }
    if (width <= 920) {
      await expect(page.locator("#feedColSettings")).toBeVisible();
      await expect(page.locator("#detailPanelSettings")).not.toBeVisible();
      await page.screenshot({
        animations: "disabled",
        path: `.ui-artifacts/settings-categories-${width}.png`,
      });
      await page.locator('[data-settings-tab="models"]').click();
    } else {
      if (width >= 1280) await expect(page.locator("#leftNavDrawer")).toBeVisible();
      await expect(page.locator("#feedColSettings")).toBeVisible();
    }
    await expect(page.locator("#modelSettings")).toBeVisible();
    expect(await overflow()).toEqual([]);
    await page.screenshot({
      animations: "disabled",
      path: `.ui-artifacts/settings-${width}.png`,
    });
    expect(errors).toEqual([]);
  });

test("full-text search includes cached article body", async ({ page }) => {
  await ready(page);
  await page
    .locator("#feedSearchInput")
    .fill("isolated interface test fixture");
  await expect(page.locator("#feedCardList .news-card")).toHaveCount(65);
  await expect(page.locator('#feedColFeed [data-filter="all"]')).toContainText(
    "65",
  );
});
test("late catalog and connection results cannot replace a changed draft", async ({
  page,
}) => {
  await ready(page);
  await page.keyboard.press(",");
  await page.locator('[data-runtime="codex"]').click();
  let releaseCatalog;
  const catalogGate = new Promise((resolve) => (releaseCatalog = resolve));
  await page.route("**/api/settings/models", async (route) => {
    await catalogGate;
    await route.fulfill({
      json: {
        ok: true,
        catalog: { options: [{ value: "stale-model", label: "Stale model" }] },
      },
    });
  });
  await page.locator("#refreshModelsBtn").click();
  await page.locator('[name="agent.codex.profile"]').fill("new-profile");
  releaseCatalog();
  await expect(
    page.locator(
      'select[name="agent.codex.model"] option[value="stale-model"]',
    ),
  ).toHaveCount(0);
  let releaseTest;
  const testGate = new Promise((resolve) => (releaseTest = resolve));
  await page.route("**/api/settings/connection-test", async (route) => {
    await testGate;
    await route.fulfill({
      json: {
        ok: true,
        status: "success",
        message: "STALE_SUCCESS",
        elapsed_ms: 3,
      },
    });
  });
  await page.locator("#testConnectionBtn").click();
  await page.locator('[name="agent.codex.profile"]').fill("latest-profile");
  releaseTest();
  await expect(page.locator("#testConnectionBtn")).toBeEnabled();
  await expect(page.locator("#connTestTerminal")).not.toContainText(
    "STALE_SUCCESS",
  );
  await expect(page.locator('[name="agent.codex.profile"]')).toHaveValue(
    "latest-profile",
  );
});
test("database picker draft survives redraw and is submitted unchanged", async ({
  page,
  request,
}) => {
  const saved = await (await request.get("/api/settings")).json();
  saved.database = {
    ...saved.database,
    environment_override: false,
    native_picker_available: true,
    configured_path: "",
  };
  await page.route("**/api/settings", (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ json: saved })
      : route.fulfill({
          json: {
            ...saved,
            ...route.request().postDataJSON(),
            database: {
              ...saved.database,
              configured_path: route.request().postDataJSON().database.path,
            },
          },
        }),
  );
  await page.route("**/api/settings/database/pick", (route) =>
    route.fulfill({
      json: { ok: true, path: "/tmp/newsreader selected.sqlite3" },
    }),
  );
  await ready(page);
  await page.keyboard.press(",");
  await page.locator('[data-settings-tab="storage"]').click();
  await page.locator("#pickDatabaseBtn").click();
  await expect(page.locator('[name="database.configured_path"]')).toHaveValue(
    "/tmp/newsreader selected.sqlite3",
  );
  await page.keyboard.press("Escape");
  await page.keyboard.press(",");
  await expect(page.locator('[name="database.configured_path"]')).toHaveValue(
    "/tmp/newsreader selected.sqlite3",
  );
  const saving = page.waitForRequest(
    (request) =>
      request.url().endsWith("/api/settings") && request.method() === "PUT",
  );
  await page.locator("#settingsSaveBtn").click();
  expect((await saving).postDataJSON().database.path).toBe(
    "/tmp/newsreader selected.sqlite3",
  );
  await expect(page.locator("#settingsStatus")).toHaveText("设置已保存");
});
test("failed settings save keeps draft and allows retry", async ({ page }) => {
  await ready(page);
  await page.keyboard.press(",");
  await page.locator('[data-runtime="codex"]').click();
  await page.locator('[name="agent.codex.profile"]').fill("unsaved-draft");
  await page.route("**/api/settings", (route) =>
    route.request().method() === "PUT"
      ? route.fulfill({
          status: 500,
          json: { ok: false, error: "settings_save_failed" },
        })
      : route.continue(),
  );
  await page.locator("#settingsSaveBtn").click();
  await expect(page.locator("#settingsStatus")).toHaveClass(/error-state/);
  await expect(page.locator('[name="agent.codex.profile"]')).toHaveValue(
    "unsaved-draft",
  );
  await expect(page.locator("#settingsSaveBtn")).toBeEnabled();
});
test("unsafe article markup and links remain inert text", async ({ page }) => {
  await page.route("**/api/news/ui-000/detail", async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    data.ai.body_zh =
      '<img src=x onerror="window.injected=true">\n\n[unsafe](javascript:alert(1))\n\n[safe](https://example.com)';
    await route.fulfill({ json: data });
  });
  await ready(page);
  await select(page);
  await expect(page.locator("#articleBodyContent")).toContainText("<img");
  await expect(page.locator("#articleBodyContent img")).toHaveCount(0);
  await expect(page.locator("#articleBodyContent a")).toHaveCount(1);
  expect(await page.evaluate(() => window.injected)).toBeUndefined();
});
test("mobile list scroll position survives opening and closing reader", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.locator("#feedCardList .news-card").first()).toBeVisible();
  await page
    .locator("#feedCardList")
    .evaluate((node) => (node.scrollTop = 450));
  const position = await page
    .locator("#feedCardList")
    .evaluate((node) => node.scrollTop);
  const card = page.locator("#feedCardList .news-card").nth(3);
  await card.locator(".news-card-title").click();
  await expect(page.locator("#articleMainTitle")).toBeVisible();
  await page.locator("#mobileReaderBackBtn").click();
  expect(
    await page.locator("#feedCardList").evaluate((node) => node.scrollTop),
  ).toBe(position);
  await page.locator("#openNavBtn").click();
  await expect(page.locator("#leftNavDrawer")).not.toHaveAttribute("inert", "");
  await page.locator("#navFavoritesBtn").click();
  await expect(page.locator("#leftNavDrawer")).toHaveAttribute("inert", "");
});

test("settings is an inline three-column workspace with searchable categories", async ({
  page,
}) => {
  await ready(page);
  await select(page);
  await page.locator("#leftNavSettingsBtn").click();
  await expect(
    page.locator("#settingsRightDrawer, #settingsDrawerBackdrop"),
  ).toHaveCount(0);
  await expect(page.locator("#feedColFeed")).not.toBeVisible();
  await expect(page.locator("#detailPanelFeed")).not.toBeVisible();
  await expect(page.locator("#agentDock")).not.toBeVisible();
  const navigation = await page.locator("#leftNavDrawer").boundingBox();
  const categories = await page.locator("#feedColSettings").boundingBox();
  const detail = await page.locator("#detailPanelSettings").boundingBox();
  expect(navigation.width).toBe(248);
  expect(categories.width).toBe(430);
  expect(categories.x).toBe(navigation.x + navigation.width);
  expect(detail.x).toBe(categories.x + categories.width);
  expect(detail.y).toBe(categories.y);
  await expect(page.locator("#leftNavSettingsBtn")).toHaveAttribute(
    "aria-current",
    "page",
  );
  await expect(page.locator("#navFeedBtn")).not.toHaveAttribute(
    "aria-current",
    "page",
  );
  await page.keyboard.press("/");
  await expect(page.locator("#settingsSearchInput")).toBeFocused();
  await page.locator("#settingsSearchInput").fill("SQLite");
  await expect(page.locator(".settings-category-card:visible")).toHaveCount(1);
  await page.locator('[data-settings-tab="storage"]').click();
  await expect(page.locator("#settingsDetailTitle")).toHaveText("数据库与存储");
  await expect(page.locator('[data-section="models"]')).not.toBeVisible();
  await expect(page.locator('[data-section="storage"]')).toBeVisible();
  await page.locator("#settingsSearchInput").fill("没有这个分类");
  await expect(page.locator("#settingsSearchEmpty")).toBeVisible();
  await page.locator("#clearSettingsSearchBtn").click();
  await expect(page.locator(".settings-category-card:visible")).toHaveCount(6);
  for (const category of ["deepseek", "pipeline", "system", "models"]) {
    await page.locator(`[data-settings-tab="${category}"]`).click();
    await expect(
      page.locator(`#settingsContent > [data-section="${category}"]`),
    ).toBeVisible();
    await expect(
      page.locator("#settingsContent > [data-section]:visible"),
    ).toHaveCount(1);
  }
  await chooseTheme(page, "dark");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.screenshot({
    path: ".ui-artifacts/settings-workspace-dark.png",
    animations: "disabled",
  });
  await page.locator("#navFavoritesBtn").click();
  await expect(page.locator("#feedColSettings")).not.toBeVisible();
  await expect(page.locator("#navFavoritesBtn")).toHaveAttribute(
    "aria-current",
    "page",
  );
});

test("settings navigation preserves unsaved fields and the reading position", async ({
  page,
}) => {
  await ready(page);
  await select(page);
  await page.locator("#detailScrollArea").evaluate((node) => {
    node.scrollTop = 280;
  });
  const position = await page
    .locator("#detailScrollArea")
    .evaluate((node) => node.scrollTop);
  await page.locator("#leftNavSettingsBtn").click();
  await page.locator('[data-runtime="codex"]').click();
  await page.locator('[name="agent.codex.profile"]').fill("three-column-draft");
  await page.locator('[data-settings-tab="deepseek"]').click();
  await page
    .locator('input[name="llm.translation.model"]')
    .fill("translation-draft");
  await page.locator('[data-settings-tab="models"]').click();
  await expect(page.locator('[name="agent.codex.profile"]')).toHaveValue(
    "three-column-draft",
  );
  await page.keyboard.press(",");
  await expect(page.locator("#feedColSettings")).not.toBeVisible();
  await expect(page.locator("#articleBodyContent")).toBeVisible();
  expect(
    await page.locator("#detailScrollArea").evaluate((node) => node.scrollTop),
  ).toBe(position);
  await page.keyboard.press(",");
  await expect(page.locator('[name="agent.codex.profile"]')).toHaveValue(
    "three-column-draft",
  );
  await page.locator('[data-settings-tab="deepseek"]').click();
  await expect(page.locator('input[name="llm.translation.model"]')).toHaveValue(
    "translation-draft",
  );
  await page.locator("#settingsResetBtn").click();
  await expect(page.locator("#settingsStatus")).toHaveText(
    "已恢复已保存的配置",
  );
  await expect(
    page.locator('input[name="llm.translation.model"]'),
  ).not.toHaveValue("translation-draft");
  await expect(page.locator("#settingsResetBtn")).toBeDisabled();
});

test("mobile settings return to categories and keep the original reader", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await select(page);
  const title = await page.locator("#articleMainTitle").textContent();
  await page.locator("#mobNavSettings").click();
  await expect(page.locator("#feedColSettings")).toBeVisible();
  await expect(page.locator("#detailPanelSettings")).not.toBeVisible();
  await page.locator('[data-settings-tab="storage"]').click();
  await expect(page.locator('[data-section="storage"]')).toBeVisible();
  await expect(page.locator("#feedColSettings")).toHaveJSProperty("inert", true);
  await page.locator("#settingsCategoryBackBtn").click();
  await expect(page.locator("#feedColSettings")).toBeVisible();
  await page.locator('[data-settings-tab="deepseek"]').click();
  await page
    .locator('input[name="llm.translation.model"]')
    .fill("mobile-draft");
  await page.locator("#settingsCategoryBackBtn").click();
  await page.locator('[data-settings-tab="deepseek"]').click();
  await expect(page.locator('input[name="llm.translation.model"]')).toHaveValue(
    "mobile-draft",
  );
  await page.keyboard.press("Escape");
  await expect(page.locator("#articleMainTitle")).toHaveText(title);
  await expect(page.locator("#detailPanelFeed")).toBeVisible();
  await expect(page.locator("#mobNavSettings")).not.toHaveClass(/active/);
});
