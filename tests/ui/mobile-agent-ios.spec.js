import { test, expect } from '@playwright/test';

test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
test.beforeEach(async ({ page }) => {
  await page.route('**/api/news/*/agent/session', route => route.fulfill({ json: {
    ok: true, context_available: true, session: { id: 'ios-agent', runtime: 'codex', model: 'test-model' }, messages: [], jobs: [],
  } }));
  await page.goto('/');
  await page.locator('[data-id="ui-064"]').click();
  await page.locator('#agentDockToggleBtn').click();
  await expect(page.locator('#agentSendBtn')).toBeEnabled();
});

test('viewport events do not collapse a focused textarea during height measurement', async ({ page }) => {
  const input = page.locator('#agentPromptInput');
  await input.fill('聚焦时保持稳定的输入高度');
  await page.evaluate(() => {
    window.inputHeightWrites = [];
    const node = document.querySelector('#agentPromptInput');
    new MutationObserver(records => {
      for (const record of records) window.inputHeightWrites.push(record.oldValue || '');
    }).observe(node, { attributes: true, attributeFilter: ['style'], attributeOldValue: true });
    for (let n = 0; n < 4; n++) visualViewport.dispatchEvent(new Event('scroll'));
  });
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  expect(await page.evaluate(() => window.inputHeightWrites.some(value => /height:\s*(auto|0px)/.test(value)))).toBe(false);
  await expect(input).toBeFocused();
});

test('browser caret reveal does not scroll the sheet input out of its clipped ancestors', async ({ page, browserName }) => {
  const input = page.locator('#agentPromptInput');
  await input.fill('键盘打开后的完整草稿\n'.repeat(12));
  await page.evaluate(() => {
    Object.defineProperties(visualViewport, {
      height: { configurable: true, get: () => 380 },
      offsetTop: { configurable: true, get: () => 160 },
    });
    visualViewport.dispatchEvent(new Event('resize'));
  });
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await input.evaluate(node => node.scrollIntoView({ block: 'center' }));
  await expect.poll(() => input.evaluate(node => {
    const box = node.getBoundingClientRect(), send = document.querySelector('#agentSendBtn').getBoundingClientRect();
    return box.top >= 160 && send.bottom <= 540 &&
      document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2) === node;
  })).toBe(true);
  await expect(input).toBeFocused();
  await page.screenshot({ path: `.ui-artifacts/ios-agent-caret-viewport-${browserName}.png`, clip: { x: 0, y: 160, width: 390, height: 380 } });
});
