import { test, expect } from '@playwright/test';
import { chooseTheme, openSettings, openNavigation, contrast } from './helpers.js';

for (const [theme, colorScheme] of [['light', 'light'], ['dark', 'light'], ['eink', 'light'], ['system', 'dark']]) {
  test(`design corrections: button contrast, terminals and heading ${theme}/${colorScheme}`, async ({ page }) => {
    await page.emulateMedia({ colorScheme });
    await page.goto('/');
    await chooseTheme(page, theme);
    await page.getByRole('button', { name: '写想法', exact: true }).click();
    await page.getByRole('textbox', { name: '写下研读想法' }).fill('仅检查样式，不发布');
    const publish = page.getByRole('button', { name: '发布想法', exact: true });
    await page.mouse.move(0, 0);
    await expect.poll(() => contrast(publish)).toBeGreaterThanOrEqual(4.5);
    await publish.hover();
    await expect.poll(() => contrast(publish)).toBeGreaterThanOrEqual(4.5);
    await openSettings(page);
    await page.locator('[data-settings-tab="classification"]').click();
    await page.getByRole('switch', { name: '启用自动分类', exact: true }).click();
    const save = page.locator('#settingsSaveBtn');
    await page.mouse.move(0, 0);
    await expect.poll(() => contrast(save)).toBeGreaterThanOrEqual(4.5);
    await save.hover();
    await expect.poll(() => contrast(save)).toBeGreaterThanOrEqual(4.5);
    for (const [section, terminal] of [['models', '#connTestTerminal'], ['deepseek', '#translationTestTerminal']]) {
      await page.locator(`[data-settings-tab="${section}"]`).click();
      await expect(page.locator('.settings-detail-heading h2')).toHaveCSS('font-weight', '600');
      if (section === 'models') await page.locator(terminal).evaluate(node=>{node.hidden=false;node.textContent='[SUCCESS] 本次模型请求成功。';});
      await expect(page.locator(terminal)).toBeVisible();
      if (theme === 'eink') {
        await expect(page.locator(terminal)).toHaveCSS('background-color', 'rgb(244, 244, 244)');
        await expect(page.locator(terminal)).toHaveCSS('color', 'rgb(0, 0, 0)');
        await expect(page.locator(terminal)).toHaveCSS('border-top', '1px solid rgb(0, 0, 0)');
      } else {
        await expect.poll(() => contrast(page.locator(terminal))).toBeGreaterThanOrEqual(4.5);
      }
    }
  });
}

for (const width of [1440, 390]) {
  test(`design corrections: editor keyboard focus is visible and unclipped at ${width}px`, async ({ page, request }) => {
    const idea = (await (await request.post('/api/standalone-ideas', { data: { title: '焦点样例', note: '只验证隔离编辑器的键盘焦点。' } })).json()).idea;
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/');
    await openNavigation(page);
    await page.locator('#navNotesBtn').click();
    await page.locator(`[data-idea-id="${idea.idea_id}"]`).click();
    const editor = page.locator('#ideaNote');
    await page.locator('#ideaTitle').focus();
    await page.keyboard.press('Tab');
    await expect(editor).toBeFocused();
    await expect(editor).toHaveCSS('outline-width', '2px');
    await expect(editor).toHaveCSS('outline-style', 'solid');
    await expect(editor).toHaveCSS('outline-offset', '2px');
    expect(await editor.evaluate(node => {
      const rect = node.getBoundingClientRect();
      for (let parent = node.parentElement; parent; parent = parent.parentElement) {
        const style = getComputedStyle(parent), box = parent.getBoundingClientRect();
        if (/(auto|scroll|hidden|clip)/.test(style.overflowX) && (rect.left - 4 < box.left || rect.right + 4 > box.right)) return false;
        if (/(auto|scroll|hidden|clip)/.test(style.overflowY) && (rect.top - 4 < box.top || rect.bottom + 4 > box.bottom)) return false;
      }
      return true;
    })).toBe(true);
    await page.locator('#ideaTitle').focus();
    await expect(editor).toHaveCSS('outline-style', 'none');
    await expect(editor).toHaveCSS('border-width', '0px');
    await request.delete(`/api/standalone-ideas/${idea.standalone_id}`);
  });
}
