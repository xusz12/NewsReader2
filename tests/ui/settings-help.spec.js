import { test, expect } from '@playwright/test';
import { openSettings, chooseTheme } from './helpers.js';

async function section(page, id) {
  if (await page.locator('#feedColSettings').evaluate(node => node.inert)) {
    await page.locator('#settingsCategoryBackBtn').click();
  }
  await page.locator(`[data-settings-tab="${id}"]`).click();
}
async function start(page) {
  await page.goto('/');
  await expect(page.locator('#feedCardList .news-card').first()).toBeVisible();
  await openSettings(page);
  await section(page, 'help');
}

for (const width of [1440, 390]) for (const appearance of ['light', 'dark', 'system-light', 'system-dark', 'eink']) {
  test(`help categories, expansion and layout ${width} ${appearance}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 850 });
    if (appearance.startsWith('system-')) await page.emulateMedia({ colorScheme: appearance.slice(7) });
    await start(page);
    await chooseTheme(page, appearance.startsWith('system-') ? 'system' : appearance);
    await expect(page.locator('#settingsDetailTitle')).toHaveText('帮助说明');
    await expect(page.locator('#settingsHelp h3')).toHaveText(['新闻采集', '阅读中的新新闻', '新闻流显示与数据清理', '阅读与本地数据']);
    await expect(page.locator('#settingsHelp [aria-expanded="true"]')).toHaveCount(0);
    const question = page.locator('#help-collection-source'), answer = page.locator('#help-collection-source-content');
    await question.click();
    await expect(question).toHaveAttribute('aria-expanded', 'true');
    await expect(answer).toBeVisible();
    await expect(answer).toContainText('不再读取 DailyNews 文件');
    await page.locator('#help-collection-schedule').click();
    await expect(answer).toBeVisible();
    await expect(page.locator('#help-collection-schedule-content')).toContainText('北京时间');
    await expect(page.locator('#settingsSaveBtn')).toBeHidden();
    await expect(page.locator('#settingsResetBtn')).toBeHidden();
    expect(await page.locator('#settingsHelp').evaluate(root => [root, ...root.querySelectorAll('.accordion-trigger,.accordion-content')]
      .filter(node => node.getClientRects().length).every(node => node.scrollWidth <= node.clientWidth))).toBe(true);
    const box = await question.boundingBox();
    expect(box.height).toBeGreaterThanOrEqual(44);
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(width);
    await question.focus();
    await page.keyboard.press('Tab');
    await expect(page.locator('#help-collection-dedup')).toBeFocused();
    await expect(page.locator('#help-collection-dedup')).toHaveCSS('outline-width', '2px');
    expect(await page.locator('#help-collection-dedup').evaluate(node => {
      const rect = node.getBoundingClientRect(), viewport = node.closest('.settings-main').getBoundingClientRect();
      return rect.top - 4 >= viewport.top && rect.bottom + 4 <= viewport.bottom;
    })).toBe(true);
    await page.screenshot({ path: `.ui-artifacts/settings-help-${width}-${appearance}.png` });
    await question.click();
    await expect(answer).toBeHidden();
    // The existing source settings still share the same section styling.
    const cardStyle = await page.locator('#settingsHelp > section').first().evaluate(node => {
      const style = getComputedStyle(node);
      return [style.padding, style.borderRadius, style.backgroundColor];
    });
    await section(page, 'pipeline');
    expect(await page.locator('[data-section="pipeline"]').first().evaluate(node => {
      const style = getComputedStyle(node);
      return [style.padding, style.borderRadius, style.backgroundColor];
    })).toEqual(cardStyle);
  });
}

test('accordion keyboard controls take priority over settings navigation and custom space shortcut', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('newsreader_workbench_shortcuts', JSON.stringify({ bindings: { next: ['Space'] } })));
  await start(page);
  const first = page.locator('#help-collection-source'), second = page.locator('#help-collection-dedup'), last = page.locator('#help-collection-translation');
  await first.focus();
  await page.keyboard.press('ArrowDown');
  await expect(second).toBeFocused();
  await page.keyboard.press('End');
  await expect(last).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await expect(first).toBeFocused();
  await page.keyboard.press('ArrowUp');
  await expect(last).toBeFocused();
  await page.keyboard.press('Home');
  await expect(first).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(first).toHaveAttribute('aria-expanded', 'true');
  await page.keyboard.press('Space');
  await expect(first).toHaveAttribute('aria-expanded', 'false');
  await expect(page.locator('[data-settings-tab="help"]')).toHaveAttribute('aria-current', 'true');
  await expect(page.locator('#settingsSaveBtn')).toBeHidden();
});

for (const width of [1440, 390]) {
  test(`reading help preserves other settings drafts and expansion across navigation and rerender ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 850 });
    await start(page);
    let writes = 0;
    page.on('request', request => { if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(request.method())) writes++; });
    await page.locator('#help-deletion-scope').click();
    await expect(page.locator('#help-deletion-scope-content')).toContainText('同一来源之后的新新闻继续收录');
    await section(page, 'classification');
    const input = page.locator('[name="classification.concurrency"]'), original = await input.inputValue();
    const changed = original === '1' ? '2' : '1';
    await input.fill(changed);
    await section(page, 'help');
    await expect(page.locator('#help-deletion-scope')).toHaveAttribute('aria-expanded', 'true');
    await expect(page.locator('#settingsSaveBtn')).toBeVisible();
    await page.keyboard.press('Escape');
    await openSettings(page);
    await section(page, 'help');
    await expect(page.locator('#help-deletion-scope')).toHaveAttribute('aria-expanded', 'true');
    await section(page, 'classification');
    await expect(input).toHaveValue(changed);
    await page.locator('#settingsResetBtn').click();
    await expect(input).toHaveValue(original);
    await section(page, 'help');
    await expect(page.locator('#help-deletion-scope')).toHaveAttribute('aria-expanded', 'true');
    await expect(page.locator('#settingsSaveBtn')).toBeHidden();
    expect(writes).toBe(0);
  });
}

test('short phone, large reading preference and reduced motion keep every answer reachable', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 568 });
  await page.addInitScript(() => localStorage.setItem('newsreader_workbench_font_size', 'extra-large'));
  await start(page);
  for (const trigger of await page.locator('#settingsHelp [data-accordion-trigger]').all()) await trigger.click();
  const last = page.locator('#help-local-reading-content');
  await last.locator('p').last().scrollIntoViewIfNeeded();
  await expect(last.locator('p').last()).toBeInViewport();
  await expect(last).toHaveCSS('font-size', '14px');
  await expect(page.locator('#help-local-reading')).toHaveCSS('font-size', '14px');
  await expect(page.locator('#help-local-reading .accordion-chevron')).toHaveCSS('transition-duration', '0s');
  expect(await page.locator('.settings-main').evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
  await page.screenshot({ path: '.ui-artifacts/settings-help-320.png' });
  await page.locator('#settingsCategoryBackBtn').click();
  await expect(page.locator('[data-settings-tab="help"]')).toBeFocused();
});

test('help stays readable when configuration cannot be loaded', async ({ page }) => {
  let fail = true;
  await page.route('**/api/settings', route => fail
    ? route.fulfill({ status: 500, json: { ok: false, error: 'settings_unavailable' } })
    : route.continue());
  await start(page);
  await page.locator('#help-collection-schedule').click();
  await expect(page.locator('#help-collection-schedule-content')).toBeVisible();
  await expect(page.locator('#settingsSaveBtn')).toBeHidden();
  await section(page, 'system');
  await expect(page.locator('#retrySettingsBtn')).toBeVisible();
  fail = false;
  await page.locator('#retrySettingsBtn').click();
  await expect(page.locator('#checkUpdateBtn')).toBeVisible();
  await section(page, 'help');
  await expect(page.locator('#help-collection-schedule')).toHaveAttribute('aria-expanded', 'true');
});
