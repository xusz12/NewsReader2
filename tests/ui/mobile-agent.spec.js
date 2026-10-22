import { test, expect } from '@playwright/test';
import { chooseTheme, openSettings } from './helpers.js';

test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
test.beforeEach(async ({ page, request }) => {
  await request.post('/__test/reset-reading');
  await page.route('**/api/news/*/agent/session', route => route.fulfill({ json: {
    ok: true, context_available: true, session: { id: 'mobile-agent', runtime: 'codex', model: 'test-model' }, messages: [], jobs: [],
  } }));
  await page.goto('/');
  await page.locator('[data-id="ui-064"]').click();
  await page.locator('#agentDockToggleBtn').click();
  await expect(page.locator('#agentSendBtn')).toBeEnabled();
});

async function viewport(page, height, offsetTop = 0) {
  await page.evaluate(({ height, offsetTop }) => {
    Object.defineProperties(visualViewport, {
      height: { configurable: true, get: () => height },
      offsetTop: { configurable: true, get: () => offsetTop },
    });
    visualViewport.dispatchEvent(new Event('resize'));
    visualViewport.dispatchEvent(new Event('scroll'));
  }, { height, offsetTop });
}

async function expectUsable(page, top, bottom) {
  await expect(page.locator('#agentDock')).not.toHaveClass(/minimized/);
  await expect(page.locator('#agentDockToggleBtn')).toHaveAttribute('aria-expanded', 'true');
  await expect(page.locator('.agent-body')).toHaveJSProperty('inert', false);
  await expect.poll(() => page.locator('#agentDock').evaluate((node, { top, bottom }) => {
    const box = node.getBoundingClientRect();
    return box.top >= top && box.bottom <= bottom + 1;
  }, { top, bottom })).toBe(true);
  for (const selector of ['#agentPromptInput', '#agentSendBtn']) {
    await expect.poll(() => page.locator(selector).evaluate((node, { top, bottom }) => {
      const box = node.getBoundingClientRect();
      return box.top >= top && box.bottom <= bottom + 1 &&
        node.contains(document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2));
    }, { top, bottom })).toBe(true);
  }
}

test('mobile starts at one line without opening the keyboard, grows and returns to one line', async ({ page, browserName }) => {
  const input = page.locator('#agentPromptInput');
  await expect(input).toHaveAttribute('rows', '1');
  await expect(input).not.toBeFocused();
  const line = (await input.boundingBox()).height;
  expect(line).toBeCloseTo(40, 0);
  await expect.poll(async () => (await page.locator('#agentDock').boundingBox()).height).toBeCloseTo(844 * .70, 0);
  await page.screenshot({ path: `.ui-artifacts/ios-agent-one-line-${browserName}.png` });
  await input.tap();
  await expect(input).toBeFocused();
  await input.fill('第一行'); await input.press('Enter'); await input.pressSequentially('第二行');
  await expect(input).toHaveValue('第一行\n第二行');
  await expect.poll(async () => (await input.boundingBox()).height).toBeGreaterThan(line);
  await expectUsable(page, 52, 788);
  await input.fill('');
  await expect.poll(async () => (await input.boundingBox()).height).toBeCloseTo(line, 0);
});

test('keyboard resizing, panning and focus scrolling keep the draft and send button visible', async ({ page }) => {
  const input = page.locator('#agentPromptInput');
  await input.tap();
  const draft = '键盘打开时能看到当前输入\n'.repeat(20);
  await input.fill(draft);
  for (const [height, offset] of [[410, 0], [320, 120], [410, 20]]) {
    await viewport(page, height, offset);
    await expectUsable(page, Math.max(52, offset + 8), height + offset);
    await expect(input).toBeFocused();
    await expect(input).toHaveValue(draft);
  }
  // Mobile browsers can scroll overflow:hidden ancestors while revealing focus.
  // Provide scrollable overflow so this exercises a real scroll offset.
  await page.evaluate(() => {
    const reader = document.querySelector('#detailPanelFeed');
    const overflow = document.createElement('div');
    overflow.id = 'focus-scroll-fixture';
    overflow.style.cssText = 'position:absolute;top:1200px;width:1px;height:1px;pointer-events:none';
    reader.append(overflow);
    reader.scrollTop = 100;
  });
  await expect(page.locator('#detailPanelFeed')).toHaveJSProperty('scrollTop', 100);
  await expectUsable(page, 52, 430);
  await expect(input).toBeFocused();
  await expect(input).toHaveValue(draft);
  await page.evaluate(() => { document.querySelector('#focus-scroll-fixture').remove(); document.querySelector('#detailPanelFeed').scrollTop = 0; });
  await viewport(page, 844);
  await expectUsable(page, 52, 788);
  await expect(input).toHaveValue(draft);
});

test('a resized layout viewport keeps the panel open and input reachable', async ({ page }) => {
  await page.locator('#agentPromptInput').tap();
  const draft = '保留草稿与焦点\n'.repeat(20);
  await page.locator('#agentPromptInput').fill(draft);
  for (const height of [450, 375, 844]) {
    await page.setViewportSize({ width: 390, height });
    await expectUsable(page, 52, height - 56);
    await expect(page.locator('#agentPromptInput')).toBeFocused();
    await expect(page.locator('#agentPromptInput')).toHaveValue(draft);
  }
});

test('the handle is removed and the existing collapse button preserves drafts', async ({ page }) => {
  const dock = page.locator('#agentDock'), input = page.locator('#agentPromptInput');
  await expect(page.locator('.mobile-sheet-handle-bar, .mobile-sheet-handle')).toHaveCount(0);
  await input.fill('收起再展开仍保留的问题');
  await page.locator('#agentDockToggleBtn').tap();
  await expect(dock).toHaveClass(/minimized/);
  await expect(input).not.toBeFocused();
  await expect(page.locator('#detailPanelFeed > #agentDock')).toHaveCount(1);
  await page.locator('#agentDockToggleBtn').tap();
  await expect(input).toHaveValue('收起再展开仍保留的问题');
  await expectUsable(page, 52, 788);
  await page.locator('#agentDockToggleBtn').focus();
  await page.keyboard.press('Enter');
  await expect(dock).toHaveClass(/minimized/);
  await expect(page.locator('#agentDockToggleBtn')).toBeFocused();
});

test('the mobile overlay follows reader/settings navigation and desktop resizing', async ({ page }) => {
  const input = page.locator('#agentPromptInput');
  await input.fill('切换页面不丢草稿');
  await expect(page.locator('body > #agentDock')).toHaveCount(1);
  await page.locator('#mobileReaderBackBtn').tap();
  await expect(page.locator('#agentDock')).toBeHidden();
  await expect(page.locator('#detailPanelFeed > #agentDock')).toHaveCount(1);
  await page.locator('[data-id="ui-064"]').click();
  await expect(page.locator('body > #agentDock')).toHaveCount(1);
  await expect(input).toHaveValue('切换页面不丢草稿');
  await openSettings(page);
  await expect(page.locator('#agentDock')).toBeHidden();
  await expect(page.locator('#detailPanelFeed > #agentDock')).toHaveCount(1);
  await page.keyboard.press('Escape');
  await expect(page.locator('body > #agentDock')).toHaveCount(1);
  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(page.locator('#detailPanelFeed > #agentDock')).toHaveCount(1);
  await expect(input).toHaveAttribute('rows', '2');
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('body > #agentDock')).toHaveCount(1);
  await expect(input).toHaveValue('切换页面不丢草稿');
  await expectUsable(page, 52, 788);
});

test('keyboard layout changes never animate in any theme', async ({ page }) => {
  const dock = page.locator('#agentDock');
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  for (const theme of ['light', 'dark', 'eink']) {
    await chooseTheme(page, theme);
    await expect(dock).toHaveCSS('transition-duration', '0s');
  }
  await viewport(page, 410);
  await expectUsable(page, 52, 410);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect(dock).toHaveCSS('transition-duration', '0s');
});
