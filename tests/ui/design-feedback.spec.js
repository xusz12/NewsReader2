import { test, expect } from '@playwright/test';
import { chooseTheme, openSettings } from './helpers.js';

test.beforeEach(async ({ request }) => {
  await request.post('/__test/reset-reading');
});

for (const width of [1440, 390, 320]) {
  test(`theme menu is custom, aligned and keyboard accessible at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/');
    const theme = page.getByRole('combobox', { name: '外观主题', exact: true });
    await expect(theme).toBeVisible();
    await expect(page.locator('#themeSelect-native')).toBeHidden();
    if (width <= 920) {
      await expect(theme.locator('.theme-symbol')).toBeVisible();
      await expect(theme.locator('.select-value')).toBeHidden();
      await expect(theme).toHaveAccessibleDescription('跟随系统');
      const rect = await theme.boundingBox();
      expect(rect.width).toBe(36);
      expect(rect.x + rect.width).toBeLessThanOrEqual(width);
    } else {
      await expect(theme.locator('.theme-symbol')).toBeHidden();
      await expect(theme.locator('.select-value')).toBeVisible();
    }
    const heights = await page.locator('#refreshIndexBtn, #themeSelect').evaluateAll(nodes => nodes.filter(n => n.getClientRects().length).map(n => n.getBoundingClientRect().height));
    expect(new Set(heights).size).toBe(1);
    expect(heights[0]).toBe(width <= 920 ? 36 : 32);
    if (width <= 920) {
      await page.locator('#feedCardList .news-card-title').first().click();
      await expect(page.locator('#mobileReaderBackBtn')).toBeVisible();
      await expect(page.locator('#openNavBtn')).toBeVisible();
      const controls = await page.locator('.topbar-left, .topbar-right').evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().toJSON()));
      expect(controls[0].right).toBeLessThanOrEqual(controls[1].left);
      expect(controls[1].right).toBeLessThanOrEqual(width - 8);
      await page.screenshot({ path: `.ui-artifacts/mobile-theme-reader-${width}.png`, animations: 'disabled' });
      await page.locator('#mobileReaderBackBtn').click();
    }
    await theme.click();
    const menu = page.getByRole('listbox', { name: '外观主题', exact: true });
    await expect(menu).toBeVisible();
    const rect = await menu.evaluate(n => n.getBoundingClientRect().toJSON());
    expect(rect.left).toBeGreaterThanOrEqual(8);
    expect(rect.right).toBeLessThanOrEqual(width - 8);
    await page.screenshot({ path: `.ui-artifacts/feedback-theme-${width}.png`, animations: 'disabled' });
    await page.keyboard.press('End');
    await page.keyboard.press('Escape');
    await expect(theme).toBeFocused();
    await expect(theme).toHaveText('跟随系统');
    await theme.press('Enter');
    await page.keyboard.press('End');
    await page.keyboard.press('Enter');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'eink');
    await page.reload();
    await expect(theme).toHaveText('墨水屏 (E-Ink)');
    await expect(theme).toHaveAccessibleDescription('墨水屏 (E-Ink)');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'eink');
    await openSettings(page);
    await page.locator('[data-settings-tab="models"]').click();
    await expect(page.locator('#modelSettings')).toBeVisible();
    expect(await page.locator('select').evaluateAll(nodes => nodes.every(n => n.hidden && n.nextElementSibling?.getAttribute('role') === 'combobox'))).toBe(true);
    await expect(page.locator('[role="listbox"]')).toHaveCount(1);
  });
}

test('selected cards, read-later and Agent archive match the reviewed appearance', async ({ page, request }) => {
  await request.patch('/api/news/ui-064/state', { data: { read_later: true } });
  // Keep this queued fixture in the unread feed so it is the selected card.
  await request.patch('/api/news/ui-064/state', { data: { read: false } });
  await page.goto('/');
  const selected = page.locator('.news-card.is-active');
  await expect(selected).toBeVisible();
  await expect(selected).toHaveAttribute('data-id', 'ui-064');
  for (const theme of ['light', 'dark', 'eink']) {
    await chooseTheme(page, theme);
    const colors = await selected.evaluate(node => {
      const probe = document.createElement('span');
      probe.style.cssText = 'color:var(--accent);background:var(--surface);border-color:var(--fg)';
      node.append(probe);
      const ref = getComputedStyle(probe);
      const result = {
        accent: ref.color, surface: ref.backgroundColor, fg: ref.borderTopColor,
      };
      probe.remove();
      return result;
    });
    for (const side of ['top', 'right', 'bottom', 'left']) {
      await expect(selected).toHaveCSS(`border-${side}-width`, '1px');
      await expect(selected).toHaveCSS(`border-${side}-color`, colors.accent);
    }
    await expect(selected).toHaveCSS('background-color', colors.surface);
    await expect(selected).toHaveCSS('box-shadow', `${colors.accent} 0px 0px 0px 1px`);
    await expect(selected.locator('[data-state="read_later"]')).toHaveCSS('background-color', colors.fg);
    await expect(selected.locator('[data-state="read_later"]')).toHaveCSS('color', colors.surface);
  }
  await chooseTheme(page, 'light');
  await page.route('**/api/news/*/agent/session', route => route.fulfill({ json: {
    ok: true, context_available: true, session: { id: 'visual-fixture', runtime: 'codex', model: 'test-model' }, messages: [], jobs: [],
  } }));
  await page.locator('#agentDockToggleBtn').click();
  const archive = page.locator('#archiveAgentBtn');
  await expect(archive).toBeVisible();
  await expect(archive).toHaveText('');
  await expect(archive).toHaveAccessibleName('提炼结论追加到想法');
  await expect(archive).toHaveCSS('width', '28px');
  await page.screenshot({ path: '.ui-artifacts/feedback-cards-agent.png', animations: 'disabled' });
  await request.patch('/api/news/ui-064/state', { data: { read_later: false } });
});

test('hidden vertical scrollbars preserve wheel scrolling in the feed and reader', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#articleBodyContent')).toBeVisible();
  for (const selector of ['#feedCardList', '#detailScrollArea']) {
    const area = page.locator(selector);
    await expect(area).toHaveCSS('scrollbar-width', 'none');
    const before = await area.evaluate(n => n.scrollTop);
    await area.hover();
    await page.mouse.wheel(0, 240);
    await expect.poll(() => area.evaluate(n => n.scrollTop)).toBeGreaterThan(before);
  }
  expect(await page.locator('*').evaluateAll(nodes => nodes.filter(n => /auto|scroll/.test(getComputedStyle(n).overflowY)).every(n => getComputedStyle(n).scrollbarWidth === 'none'))).toBe(true);
});
