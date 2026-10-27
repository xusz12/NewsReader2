import { test, expect } from '@playwright/test';
import { answerConfirm, chooseTheme, openNavigation } from './helpers.js';

const fontBox = page => page.getByRole('combobox', { name: '阅读字号', exact: true });
async function chooseFont(page, value) {
  const label = await page.locator(`#fontSizeSelect-native option[value="${value}"]`).textContent();
  if (await page.locator('#agentDockToggleBtn').getAttribute('aria-expanded') === 'true') {
    await fontBox(page).click();
    await expect(page.locator('#agentDockToggleBtn')).toHaveAttribute('aria-expanded', 'false');
  }
  await fontBox(page).click();
  await page.getByRole('option', { name: label, exact: true }).click();
}

test('mobile badges follow real read and read-later changes and disappear at zero', async ({ page, request }) => {
  await request.post('/__test/reset-reading');
  await request.post('/api/news/clear-read-later', { data: {} });
  await request.patch('/api/news/ui-064/state', { data: { read_later: true } });
  // Deliberately restore unread so the queue action is available in the unread feed.
  await request.patch('/api/news/ui-064/state', { data: { read: false } });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  const feed = page.locator('#mobUnreadBadge'), later = page.locator('#mobReadLaterBadge');
  await expect(feed).toHaveText('65');
  await expect(later).toHaveText('1');
  const action = page.locator('#feedCardList [data-id="ui-064"] [data-state="read_later"]');
  await action.click();
  await expect(later).toBeHidden();
  await expect(feed).toHaveText('65');
  await expect(page.locator('#mobileBottomNav [data-collection="read_later"]')).toHaveAccessibleName('稍后再看');
  await action.click();
  await expect(later).toBeVisible();
  await expect(later).toHaveText('1');
  await expect(feed).toHaveText('64'); // Joining read later also marks the feed item read.
  await page.locator('#markAllReadBtn').click();
  await answerConfirm(page);
  await expect(feed).toBeHidden();
  await expect(later).toHaveText('1'); // Feed reads and full-text completion are separate states.
  await expect(page.locator('#mobNavFeed')).toHaveAccessibleName('新闻流');
  await request.patch('/api/news/ui-064/state', { data: { read: false } });
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(feed).toBeVisible();
  await expect(feed).toHaveText('1');
  await expect(page.locator('#mobNavFeed')).toHaveAccessibleName('新闻流，1 条未读');
});

test('badges start hidden while loading and keep the full count accessible above 99', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  let release;
  const held = new Promise(resolve => { release = resolve; });
  await page.route('**/api/nav-summary', async route => {
    await held;
    await route.fulfill({ json: { summary: { feed_unread: 120, read_later_unread: 245, favorites: 0 } } });
  });
  try {
    await page.goto('/');
    await expect(page.locator('#mobUnreadBadge')).toBeHidden();
    await expect(page.locator('#mobReadLaterBadge')).toBeHidden();
  } finally { release(); }
  await expect(page.locator('#mobUnreadBadge')).toHaveText('99+');
  await expect(page.locator('#mobReadLaterBadge')).toHaveText('99+');
  await expect(page.locator('#mobNavFeed')).toHaveAccessibleName('新闻流，120 条未读');
  await expect(page.locator('#mobileBottomNav [data-collection="read_later"]')).toHaveAccessibleName('稍后再看，245 条未读');
});

for (const width of [390, 1440]) {
  test(`reading sizes change content, preserve controls and persist at ${width}px`, async ({ page, request }) => {
    await request.post('/__test/reset-reading');
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/');
    await expect(page.locator('#feedCardList .news-card').first()).toBeVisible();
    await page.locator('#feedCardList .news-card-title').first().click();
    await expect(page.locator('#articleBodyContent p').first()).toBeVisible();
    const title = page.locator('#feedCardList .news-card-title').first();
    const body = page.locator('#articleBodyContent');
    for (const [value, factor] of [['small', .9], ['large', 1.15], ['extra-large', 1.3], ['standard', 1]]) {
      await chooseFont(page, value);
      await expect(fontBox(page)).toBeFocused();
      await expect(body).toHaveCSS('font-size', `${Number((14 * factor).toFixed(2))}px`);
      await expect(title).toHaveCSS('font-size', `${Number(((width <= 920 ? 15 : 14.5) * factor).toFixed(4))}px`);
      await expect(page.locator('.topbar')).toHaveCSS('height', width <= 920 ? '50px' : '52px');
      await expect(fontBox(page)).toHaveCSS('height', width <= 920 ? '36px' : '32px');
      await expect(page.locator('#themeSelect')).toHaveCSS('height', width <= 920 ? '36px' : '32px');
    }
    await chooseFont(page, 'extra-large');
    await chooseTheme(page, 'dark');
    await page.reload();
    await expect(fontBox(page)).toHaveAttribute('aria-description', '特大（130%）');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await page.locator('#feedCardList .news-card-title').first().click();
    await expect(body).toHaveCSS('font-size', '18.2px');
    await expect(title).toHaveCSS('font-size', width <= 920 ? '19.5px' : '18.85px');
    if (width <= 920) await expect(page.locator('#agentPromptInput')).toHaveCSS('font-size', '16px');

    const idea = (await (await request.post('/api/standalone-ideas', { data: { title: '字号检查', note: '正文应跟随阅读字号。' } })).json()).idea;
    try {
      await openNavigation(page);
      await page.locator('#navNotesBtn').click();
      await page.locator(`[data-idea-id="${idea.idea_id}"]`).click();
      await expect(page.locator('#ideaNote')).toHaveCSS('font-size', '20.8px');
      await chooseFont(page, 'standard');
      await expect(page.locator('#ideaNote')).toHaveCSS('font-size', '16px');
    } finally { await request.delete(`/api/standalone-ideas/${idea.standalone_id}`); }
  });
}

test('invalid or unavailable local storage keeps the default and leaves font selection usable', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('newsreader_workbench_font_size', 'invalid'));
  await page.goto('/');
  await expect(fontBox(page)).toHaveAttribute('aria-description', '标准（100%）');
  await page.addInitScript(() => {
    Storage.prototype.getItem = () => { throw new Error('blocked'); };
    Storage.prototype.setItem = () => { throw new Error('blocked'); };
  });
  await page.reload();
  await chooseFont(page, 'large');
  await expect(page.locator('#articleBodyContent')).toHaveCSS('font-size', '16.1px');
});

test('saved thoughts, Agent replies and generated summaries follow the same reading preference', async ({ page, request }) => {
  await request.post('/__test/reset-agent');
  await request.post('/__test/reset-summaries');
  const saved = (await (await request.get('/api/settings')).json());
  await request.put('/api/settings', { data: {
    agent: { ...saved.agent, runtime: 'codex', codex: { ...saved.agent.codex, model: 'ui-summary-model', reasoning_effort: '' } },
    news_summary: { max_days: 20, max_items: 2 },
  } });
  try {
    const notice = await (await request.post('/__test/agent-complete', { data: { item_id: 'ui-000', answer: '字号变化后仍可继续阅读的回复。' } })).json();
    await page.goto('/');
    await chooseFont(page, 'extra-large');
    await page.locator('#agentNotificationsBtn').click();
    await page.locator(`[data-open-notice="${notice.job_id}"]`).click();
    const reply = page.locator('.agent-msg.assistant .agent-bubble').filter({ hasText: '字号变化后仍可继续阅读的回复。' });
    await expect(reply).toBeVisible();
    await expect(reply).toHaveCSS('font-size', '16.9px');
    await expect(page.locator('.saved-note-content').first()).toHaveCSS('font-size', '18.2px');
    await chooseFont(page, 'standard');
    await expect(reply).toHaveCSS('font-size', '13px');
    await page.locator('#agentDockToggleBtn').click();
    await chooseFont(page, 'extra-large');
    await page.locator('#navSummariesBtn').click();
    await page.locator('#catchupStartBtn').click();
    await page.locator('#confirmSummaryBtn').click();
    await expect(page.locator('#summaryCardList .news-card')).toHaveCount(1);
    await expect(page.locator('.summary-prose')).toHaveCSS('font-size', '18.2px');
    await expect(page.locator('.summary-prose p').first()).toHaveCSS('font-size', '18.2px');
    await chooseFont(page, 'standard');
    await expect(page.locator('.summary-prose')).toHaveCSS('font-size', '14px');
  } finally {
    await request.post('/__test/reset-agent');
    await request.post('/__test/reset-summaries');
    await request.put('/api/settings', { data: { agent: saved.agent, news_summary: saved.news_summary } });
  }
});

test('font menu and topbar fit small screens and responsive boundaries in every theme', async ({ page }) => {
  test.setTimeout(90000);
  await page.goto('/');
  await expect(page.locator('#feedCardList .news-card').first()).toBeVisible();
  for (const width of [320, 360, 390, 920, 921, 1100, 1101, 1279, 1280, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    for (const [theme, scheme] of [['light', 'light'], ['dark', 'light'], ['system', 'dark'], ['system', 'light'], ['eink', 'light']]) {
      await page.emulateMedia({ colorScheme: scheme });
      await chooseTheme(page, theme);
      await chooseFont(page, 'extra-large');
      await page.locator('#feedCardList .news-card-title').first().click();
      const boxes = await page.locator('.topbar-left, .topbar-center, .topbar-right').evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().toJSON()));
      expect(boxes[0].right).toBeLessThanOrEqual(boxes[1].left);
      expect(boxes[1].right).toBeLessThanOrEqual(boxes[2].left);
      expect(boxes[2].right).toBeLessThanOrEqual(width);
      if (width > 920) {
        expect(await fontBox(page).locator('.select-value').evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
        const status = await page.locator('#upstreamErrorsBtn').boundingBox();
        expect(status.x + status.width / 2).toBeCloseTo(width / 2, 0);
      }
      await fontBox(page).click();
      const menu = page.getByRole('listbox');
      await expect(menu.getByRole('option')).toHaveCount(4);
      const bounds = await menu.boundingBox();
      expect(bounds.x).toBeGreaterThanOrEqual(0);
      expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
      await page.keyboard.press('Home');
      await page.keyboard.press('Enter');
      await expect(fontBox(page)).toHaveAttribute('aria-description', '小（90%）');
      if (width <= 920) await page.locator('#mobileReaderBackBtn').click();
    }
  }
});
