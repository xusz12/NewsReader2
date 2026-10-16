import { chooseTheme } from "./helpers.js";
import { test, expect } from '@playwright/test';
import fs from 'node:fs';

const tokens = JSON.parse(fs.readFileSync(new URL('../../static/design-tokens.json', import.meta.url), 'utf8'));
const cssName = name => `--${name.replace(/[A-Z]/g, c => `-${c.toLowerCase()}`)}`;

async function start(page, width = 1440) {
  await page.setViewportSize({ width, height: 900 });
  await page.goto('/');
  await expect(page.locator('#feedCardList .news-card').first()).toBeVisible();
}
test.beforeEach(async ({ request }) => {
  await request.post('/__test/reset-reading');
  for (const id of ['ui-064', 'ui-063']) await request.patch(`/api/news/${id}/state`, { data: { favorite: false } });
});

for (const [theme, scheme, palette] of [['light', 'light', 'light'], ['dark', 'light', 'dark'], ['system', 'dark', 'dark'], ['system', 'light', 'light']]) {
  test(`v004 ${theme}/${scheme}: palette, typography, semantic states and shadows`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme });
    await start(page);
    await chooseTheme(page, theme);
    const actual = await page.evaluate(() => {
      const css = getComputedStyle(document.documentElement);
      return Object.fromEntries([...css].filter(key => key.startsWith('--')).map(key => [key, css.getPropertyValue(key).trim()]));
    });
    for (const [key, token] of Object.entries(tokens.color[palette])) expect(actual[cssName(key)]).toBe(token.value);
    await expect(page.locator('.topbar')).toHaveCSS('height', '52px');
    await expect(page.locator('#leftNavDrawer')).toHaveCSS('width', '248px');
    await expect(page.locator('#feedColFeed')).toHaveCSS('width', '430px');
    await expect(page.locator('#articleMainTitle')).toHaveCSS('font-size', '34px');
    await expect(page.locator('#articleBodyContent')).toHaveCSS('font-size', '14px');
    await expect(page.locator('#articleBodyContent p').first()).toHaveCSS('line-height', '23.8px');
    await expect(page.locator('.news-card-title').first()).toHaveCSS('font-size', '15px');
    await expect(page.locator('.news-card-snippet').first()).toHaveCSS('font-size', '13px');
    await expect(page.locator('.news-card').first()).toHaveCSS('border-radius', '8px');
    await expect(page.locator('#btnToggleStar')).toHaveCSS('height', '28px');
    await expect(page.locator('#btnToggleStar')).toHaveCSS('border-radius', '6px');
    await page.locator('#articleMainTitle').click();
    await page.keyboard.press('j');
    await expect(page.locator('.news-card.is-active')).toHaveCSS('border-left-width', '1px');
    await expect(page.locator('#agentDock')).toHaveCSS('height', '42px');
    const shadow = await page.locator('#agentDock').evaluate(node => getComputedStyle(node).boxShadow);
    expect(shadow).not.toBe('none');
    if (palette === 'dark') expect(shadow).toContain('rgba(0, 0, 0, 0.6)');
    await page.locator('#btnToggleStar').click();
    const selected = page.locator('.news-card.is-active [data-state="favorite"]');
    await expect(selected).toHaveAttribute('aria-pressed', 'true');
    await expect.poll(async () => (await selected.evaluate(node => getComputedStyle(node).color)) === (await page.locator('#btnToggleStar').evaluate(node => getComputedStyle(node).color))).toBe(true);
    await page.screenshot({ path: `.ui-artifacts/v004-${theme}-${scheme}.png`, animations: 'disabled' });
    await page.route('**/api/news/*/agent/session', route => route.fulfill({ json: {
      ok: true, context_available: true, session: { id: 'visual-fixture', runtime: 'codex', model: 'test-model' }, messages: [], jobs: [],
    } }));
    await page.locator('#agentDockToggleBtn').click();
    await expect(page.locator('#agentDock')).toHaveCSS('height', '520px');
    await expect(page.locator('#archiveAgentBtn')).toHaveText('');
    await expect(page.locator('#archiveAgentBtn')).toHaveAccessibleName('提炼结论追加到想法');
    expect(await page.locator('.agent-header').evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
    await page.screenshot({ path: `.ui-artifacts/v004-agent-${theme}-${scheme}.png`, animations: 'disabled' });
  });
}

for (const width of [920, 921, 1279, 1280]) {
  test(`v004 breakpoint ${width}: reading, drawer and settings agree`, async ({ page }) => {
    await start(page, width);
    const mobile = width <= 920, drawer = width < 1280;
    await expect(page.locator('#leftNavDrawer')).toHaveJSProperty('inert', drawer);
    if (mobile) await expect(page.locator('#detailPanelFeed')).toBeHidden();
    else {
      await expect(page.locator('#detailPanelFeed')).toBeVisible();
      await expect(page.locator('#feedColFeed')).toHaveCSS('width', '430px');
    }
    await page.locator('#feedCardList .news-card-title').first().click();
    await expect(page.locator('#articleBodyContent')).toBeVisible();
    if (mobile) {
      await expect(page.locator('#feedColFeed')).toHaveJSProperty('inert', true);
      await page.locator('#mobileReaderBackBtn').click();
      await page.locator('#mobNavSettings').click();
      await expect(page.locator('#detailPanelSettings')).toBeHidden();
      await page.locator('[data-settings-tab="models"]').click();
    } else {
      if (drawer) await page.locator('#openNavBtn').click();
      await page.locator('#leftNavSettingsBtn').click();
      await expect(page.locator('#feedColSettings')).toHaveCSS('width', '430px');
    }
    await expect(page.locator('#modelSettings')).toBeVisible();
    expect(await page.evaluate(() => [document.documentElement, ...document.querySelectorAll('.feed-column.active,.detail-panel.active')].filter(n => n.getClientRects().length).every(n => n.scrollWidth <= n.clientWidth + 1))).toBe(true);
    await page.screenshot({ path: `.ui-artifacts/v004-settings-${width}.png`, animations: 'disabled' });
    if (drawer && !mobile) {
      await page.keyboard.press('Escape');
      await expect(page.locator('#openNavBtn')).toBeFocused();
    }
  });
}

test('v004 resize restores desktop selection and unlocks navigation', async ({ page }) => {
  await start(page, 920);
  await expect(page.locator('#detailPanelFeed')).toBeHidden();
  await page.setViewportSize({ width: 921, height: 900 });
  await expect(page.locator('#articleBodyContent')).toBeVisible();
  await page.locator('#openNavBtn').click();
  await page.setViewportSize({ width: 1280, height: 900 });
  await expect(page.locator('#leftNavDrawer')).toHaveJSProperty('inert', false);
  await expect(page.locator('#navDrawerBackdrop')).not.toHaveClass(/active/);
  await page.locator('#leftNavSettingsBtn').click();
  await expect(page.locator('#modelSettings')).toBeVisible();
});

test('v004 e-ink: opaque grayscale, hard shadow and no motion', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await start(page);
  await chooseTheme(page, 'eink');
  await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(255, 255, 255)');
  await expect(page.locator('body')).toHaveCSS('color', 'rgb(0, 0, 0)');
  await expect(page.locator('#agentDock')).toHaveCSS('box-shadow', 'rgb(0, 0, 0) 0px 4px 0px 0px');
  await expect(page.locator('.news-card').first()).toHaveCSS('box-shadow', 'none');
  await expect(page.locator('.news-card').first()).toHaveCSS('transition-duration', '0s');
  await expect(page.locator('#agentDock')).toHaveCSS('transition-duration', '0s');
  await page.screenshot({ path: '.ui-artifacts/v004-eink.png', animations: 'disabled' });
});
