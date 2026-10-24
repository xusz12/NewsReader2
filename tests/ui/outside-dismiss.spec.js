import { test, expect } from '@playwright/test';
import { answerConfirm, chooseTheme } from './helpers.js';

async function start(page, request) {
  await request.post('/__test/reset-reading');
  await request.post('/__test/reset-agent');
  await request.patch('/api/news/ui-064/state', { data: { favorite: false } });
  await page.route('**/api/error-stats?*', route => route.fulfill({ json: {
    ok: true, day: '2026-10-24', today: '2026-10-24', days: [],
  } }));
  await page.goto('/');
  await page.locator('[data-id="ui-064"]').click();
}

async function observePress(page, selector) {
  await page.locator(selector).evaluate(node => {
    window.outsideEvents = [];
    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
      node.addEventListener(type, () => window.outsideEvents.push(type));
    }
  });
}

for (const width of [1440, 390]) {
  test.describe(`outside dismissal at ${width}`, () => {
    test.use({ viewport: { width, height: 844 }, isMobile: width === 390, hasTouch: width === 390 });
    const press = locator => width === 390 ? locator.tap() : locator.click();

    test('Agent consumes the first outside press, preserves drafts and allows the next action', async ({ page, request }) => {
      await start(page, request);
      const dock = page.locator('#agentDock'), toggle = page.locator('#agentDockToggleBtn');
      const star = page.locator('#btnToggleStar'), input = page.locator('#agentPromptInput');
      await press(toggle);
      await input.fill('外部点击后仍保留的问题');
      await observePress(page, '#btnToggleStar');
      await press(star);
      await expect(dock).toHaveClass(/minimized/);
      await expect(star).not.toHaveClass(/active/);
      await expect(star).not.toBeFocused();
      expect(await page.evaluate(() => window.outsideEvents)).toEqual([]);
      await press(star);
      await expect(star).toHaveClass(/active/);

      await press(toggle);
      const theme = page.getByRole('combobox', { name: '外观主题', exact: true });
      await press(theme);
      await expect(dock).toHaveClass(/minimized/);
      await expect(page.getByRole('listbox')).toBeHidden();
      await press(theme);
      await expect(page.getByRole('listbox')).toBeVisible();
      await theme.press('Escape');
      await press(toggle);
      await expect(input).toHaveValue('外部点击后仍保留的问题');

      if (width === 390) {
        await press(page.locator('#mobileReaderBackBtn'));
        await expect(dock).toHaveClass(/minimized/);
        await expect(page.locator('#feedView')).toHaveClass(/mobile-reader-active/);
        await press(page.locator('#mobileReaderBackBtn'));
        await expect(page.locator('#feedView')).not.toHaveClass(/mobile-reader-active/);
      } else {
        const title = await page.locator('.article-title').innerText();
        await press(page.locator('[data-id="ui-063"]'));
        await expect(dock).toHaveClass(/minimized/);
        await expect(page.locator('.article-title')).toHaveText(title);
        await press(page.locator('[data-id="ui-063"]'));
        await expect(page.locator('.article-title')).not.toHaveText(title);
      }
    });

    test('upstream panel and its calendar consume outside presses in each theme', async ({ page, request }) => {
      await start(page, request);
      const entry = page.locator('#upstreamErrorsBtn'), panel = page.locator('#upstreamErrorsPanel');
      const theme = page.getByRole('combobox', { name: '外观主题', exact: true });
      for (const value of ['light', 'dark', 'eink']) {
        await chooseTheme(page, value);
        await press(entry);
        const backdrop = page.locator('#upstreamErrorsBackdrop');
        await expect(backdrop).toBeVisible();
        await expect(backdrop).toHaveCSS('backdrop-filter', value === 'eink' ? 'none' : 'blur(4px)');
        expect(await backdrop.boundingBox()).toEqual({ x: 0, y: 0, width, height: 844 });
        await expect(panel).toHaveCSS('filter', 'none');
        await press(page.locator('#upstreamCalendarBtn'));
        await expect(page.locator('#upstreamCalendar')).toBeVisible();
        await page.screenshot({ path: `.ui-artifacts/outside-dismiss-${width}-${value}.png` });
        await observePress(page, '#themeSelect');
        await press(theme);
        await expect(panel).toBeHidden();
        await expect(backdrop).toBeHidden();
        await expect(page.locator('#upstreamCalendar')).toBeHidden();
        await expect(page.getByRole('listbox')).toBeHidden();
        await expect(theme).not.toBeFocused();
        expect(await page.evaluate(() => window.outsideEvents)).toEqual([]);
        await press(theme);
        await expect(page.getByRole('listbox')).toBeVisible();
        await theme.press('Escape');
      }
      await press(entry);
      await press(entry);
      await expect(panel).toBeHidden();
      await expect(page.locator('#upstreamErrorsBackdrop')).toBeHidden();
    });
  });
}

test('Agent confirmation above the panel remains operable', async ({ page, request }) => {
  await start(page, request);
  await request.post('/__test/agent-complete', { data: { item_id: 'ui-064' } });
  await page.locator('#agentDockToggleBtn').click();
  await expect(page.locator('#agentChatStream')).toContainText('通知样例');
  await page.locator('#agentNewBtn').click();
  await answerConfirm(page, false);
  await expect(page.locator('#agentDock')).not.toHaveClass(/minimized/);
  await expect(page.locator('#agentChatStream')).toContainText('通知样例');
  await page.locator('#agentNewBtn').click();
  await answerConfirm(page);
  await expect(page.locator('#agentDock')).not.toHaveClass(/minimized/);
  await expect(page.locator('#agentChatStream')).not.toContainText('通知样例');
});

test('a cancelled outside gesture does not swallow the next click or keyboard activation', async ({ page, request }) => {
  await start(page, request);
  await page.locator('#agentDockToggleBtn').click();
  const theme = page.getByRole('combobox', { name: '外观主题', exact: true });
  const box = await theme.boundingBox();
  await page.mouse.move(box.x + 8, box.y + 8);
  await page.mouse.down();
  await expect(page.locator('#agentDock')).toHaveClass(/minimized/);
  await page.mouse.move(1, 1);
  await page.mouse.up();
  await theme.click();
  await expect(page.getByRole('listbox')).toBeVisible();
  await theme.press('Escape');
  await page.locator('#upstreamErrorsBtn').click();
  await theme.click();
  await expect(page.getByRole('listbox')).toBeHidden();
  await theme.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('listbox')).toBeVisible();
});
