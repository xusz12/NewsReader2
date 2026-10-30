import { test, expect } from '@playwright/test';
import { chooseTheme, openSettings } from './helpers.js';

const storageKey = 'newsreader_workbench_shortcuts';
async function section(page, id = 'shortcuts') {
  if (await page.locator('#feedColSettings').evaluate(node => node.inert)) await page.locator('#settingsCategoryBackBtn').click();
  await page.locator(`[data-settings-tab="${id}"]`).click();
}
async function start(page) {
  await page.goto('/');
  await expect(page.locator('#feedCardList .news-card').first()).toBeVisible();
  await openSettings(page);
  await section(page);
}
async function record(page, action, key, slot = 0) {
  await page.locator(`#shortcut-${action}-${slot}`).press(key);
}
async function save(page) {
  await page.locator('#settingsSaveBtn').click();
  await expect(page.locator('#settingsStatus')).toHaveText('设置已保存');
  await expect(page.locator('#settingsSaveBtn')).toBeHidden();
  await expect(page.locator('#settingsResetBtn')).toBeHidden();
}
const saved = page => page.evaluate(key => JSON.parse(localStorage.getItem(key)), storageKey);
test.beforeEach(async ({ request }) => {
  await request.post('/__test/reset-reading');
  await request.patch('/api/news/ui-064/state', { data: { favorite: false, important: false } });
});

test('custom keys save locally, replace defaults, update hints and work after reload', async ({ page }) => {
  await start(page);
  let serverWrites = 0;
  page.on('request', request => { if (request.method() === 'PUT' && request.url().endsWith('/api/settings')) serverWrites++; });
  await record(page, 'favorite', 'g');
  await record(page, 'important', 'Control+Shift+u');
  await record(page, 'next', 'n');
  await record(page, 'previous', 'p');
  await save(page);
  expect(serverWrites).toBe(0);
  expect((await saved(page)).bindings.favorite).toEqual(['g']);
  await page.keyboard.press('Escape');
  const first = page.locator('#feedCardList [data-id="ui-064"]');
  const favorite = first.locator('[data-state="favorite"]');
  await page.keyboard.press('f');
  await expect(favorite).toHaveAttribute('aria-pressed', 'false');
  await page.keyboard.press('g');
  await expect(favorite).toHaveAttribute('aria-pressed', 'true');
  await page.keyboard.press('Control+Shift+u');
  await expect(first.locator('[data-state="important"]')).toHaveAttribute('aria-pressed', 'true');
  await page.keyboard.press('n');
  await expect(first).not.toHaveAttribute('aria-current', 'true');
  await page.keyboard.press('p');
  await expect(first).toHaveAttribute('aria-current', 'true');
  await expect(page.locator('[data-shortcut-hint="favorite"]')).toHaveText('g');
  await page.keyboard.press('?');
  await expect(page.locator('#shortcutsModal')).toContainText('Ctrl+Shift+u');
  await page.keyboard.press('Escape');
  await page.reload();
  await expect(page.locator('[data-shortcut-hint="favorite"]')).toHaveText('g');
  await expect(page.locator('#keyboardBarKeys')).toContainText('Ctrl+Shift+u');
});

test('recording rejects conflicts and invalid input without overwriting bindings; clear and reset work', async ({ page }) => {
  await start(page);
  await record(page, 'favorite', 'a');
  await expect(page.locator('#shortcutValidation')).toContainText('已用于「标记重要」');
  await expect(page.locator('#shortcut-favorite-0')).toHaveValue('f');
  await expect(page.locator('#settingsSaveBtn')).toBeHidden();
  for (const key of ['Enter', 'Control+Enter']) {
    await record(page, 'favorite', key);
    await expect(page.locator('#shortcutValidation')).toContainText('保留');
    await expect(page.locator('#shortcut-favorite-0')).toHaveAttribute('aria-invalid', 'true');
    await expect(page.locator('#shortcut-favorite-0')).toHaveValue('f');
  }
  await record(page, 'favorite', 'Control');
  await page.keyboard.insertText('not-a-key、g');
  await expect(page.locator('#shortcut-favorite-0')).toHaveValue('f');
  await record(page, 'favorite', 'Escape');
  await expect(page.locator('#shortcut-favorite-0')).not.toBeFocused();
  await expect(page.locator('#detailPanelSettings')).toBeVisible();
  await record(page, 'favorite', 'Tab');
  await expect(page.locator('#shortcut-favorite-0')).not.toBeFocused();
  expect(await saved(page)).toBeNull();
  await page.getByRole('button', { name: '清除收藏快捷键 1', exact: true }).click();
  await save(page);
  expect((await saved(page)).bindings.favorite).toEqual([]);
  await page.keyboard.press('Escape');
  await page.keyboard.press('f');
  await expect(page.locator('#feedCardList [data-id="ui-064"] [data-state="favorite"]')).toHaveAttribute('aria-pressed', 'false');
  await openSettings(page);
  await section(page);
  await page.locator('#resetShortcutsBtn').click();
  await save(page);
  expect((await saved(page)).bindings.favorite).toEqual(['f']);
});

test('two independent slots persist and activate both keys, including an empty first slot', async ({ page }) => {
  await start(page);
  await expect(page.locator('.shortcut-setting-row')).toHaveCount(10);
  for (const row of await page.locator('.shortcut-setting-row').all()) await expect(row.locator('input')).toHaveCount(2);
  await record(page, 'favorite', 'g');
  await record(page, 'favorite', 'h', 1);
  await record(page, 'favorite', 'g', 1);
  await expect(page.locator('#shortcutValidation')).toContainText('已用于「收藏」');
  await expect(page.locator('#shortcut-favorite-1')).toHaveValue('h');
  await save(page);
  expect((await saved(page)).bindings.favorite).toEqual(['g', 'h']);
  await page.keyboard.press('Escape');
  const favorite = page.locator('#feedCardList [data-id="ui-064"] [data-state="favorite"]');
  await page.keyboard.press('g');
  await expect(favorite).toHaveAttribute('aria-pressed', 'true');
  await page.keyboard.press('h');
  await expect(favorite).toHaveAttribute('aria-pressed', 'false');
  await openSettings(page);
  await section(page);
  await page.getByRole('button', { name: '清除收藏快捷键 1', exact: true }).click();
  await expect(page.locator('#shortcut-favorite-1')).toHaveValue('h');
  await save(page);
  await page.reload();
  await openSettings(page);
  await section(page);
  await expect(page.locator('#shortcut-favorite-0')).toHaveValue('');
  await expect(page.locator('#shortcut-favorite-1')).toHaveValue('h');
  await expect(page.locator('[data-shortcut-hint="favorite"]')).toHaveText('h');
  await page.keyboard.press('Escape');
  await page.keyboard.press('g');
  await expect(favorite).toHaveAttribute('aria-pressed', 'false');
  await page.keyboard.press('h');
  await expect(favorite).toHaveAttribute('aria-pressed', 'true');
});

test('older preferences keep their first two bindings and the normalizer rejects more', async ({ page }) => {
  await page.addInitScript(key => localStorage.setItem(key, JSON.stringify({ show_bar: false, bindings: { previous: ['w', 'ArrowUp', 'k'], next: ['s', 'ArrowDown', 'j'], favorite: ['g', 'h', 'x'] } })), storageKey);
  await start(page);
  await expect(page.locator('#shortcut-favorite-0')).toHaveValue('g');
  await expect(page.locator('#shortcut-favorite-1')).toHaveValue('h');
  await expect(page.getByRole('switch', { name: '显示底部快捷键栏' })).not.toBeChecked();
  const error = await page.evaluate(async () => {
    const { normalizeShortcuts } = await import('/static/keyboard-shortcuts.js');
    try { normalizeShortcuts({ bindings: { favorite: ['g', 'h', 'x'] } }); } catch (error) { return error.message; }
  });
  expect(error).toContain('最多设置两个');
});

test('save and undo appear only for actual changes across settings and browser preferences', async ({ page }) => {
  await start(page);
  const saveButton = page.locator('#settingsSaveBtn');
  const undoButton = page.locator('#settingsResetBtn');
  await expect(saveButton).toBeHidden();
  await expect(undoButton).toBeHidden();
  await page.locator('#resetShortcutsBtn').click();
  await expect(saveButton).toBeHidden();
  const toggle = page.getByRole('switch', { name: '显示底部快捷键栏' });
  await toggle.uncheck();
  await expect(saveButton).toBeVisible();
  await toggle.check();
  await expect(saveButton).toBeHidden();
  await record(page, 'favorite', 'g');
  await expect(undoButton).toBeVisible();
  await record(page, 'favorite', 'f');
  await expect(saveButton).toBeHidden();
  await section(page, 'classification');
  const concurrency = page.locator('[name="classification.concurrency"]');
  const original = await concurrency.inputValue();
  await concurrency.fill(original === '1' ? '2' : '1');
  await expect(saveButton).toBeVisible();
  await concurrency.fill(original);
  await expect(saveButton).toBeHidden();
  const enabled = page.getByRole('switch', { name: '启用自动分类', exact: true });
  await enabled.click();
  await expect(undoButton).toBeVisible();
  await undoButton.click();
  await expect(saveButton).toBeHidden();
  await expect(undoButton).toBeHidden();
});

test('custom settings, search and help keys respect inputs, composition and dialogs', async ({ page }) => {
  await start(page);
  await record(page, 'settings', 'Control+.');
  await record(page, 'search', 'Control+k');
  await record(page, 'help', 'h');
  await save(page);
  await page.keyboard.press('Escape');
  await page.keyboard.press(',');
  await expect(page.locator('#feedColSettings')).toBeHidden();
  await page.keyboard.press('Control+.');
  await expect(page.locator('#feedColSettings')).toBeVisible();
  await page.keyboard.press('Control+.');
  await expect(page.locator('#feedColSettings')).toBeHidden();
  await page.keyboard.press('Control+k');
  await expect(page.locator('#feedSearchInput')).toBeFocused();
  await page.keyboard.press('h');
  await expect(page.locator('#shortcutsModal')).not.toHaveClass(/show/);
  await page.locator('#feedSearchInput').fill('');
  await page.locator('#feedSearchInput').blur();
  await page.evaluate(() => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'h', isComposing: true, bubbles: true })));
  await expect(page.locator('#shortcutsModal')).not.toHaveClass(/show/);
  await page.keyboard.press('h');
  await expect(page.locator('#shortcutsModal')).toHaveClass(/show/);
  await page.keyboard.press('Control+.');
  await expect(page.locator('#feedColSettings')).toBeHidden();
  await page.keyboard.press('Escape');
});

test('footer close persists and the settings switch restores it without changing mobile navigation', async ({ page }) => {
  await page.goto('/');
  await page.locator('#closeKeyboardBarBtn').click();
  await expect(page.locator('#keyboardBar')).toBeHidden();
  await page.reload();
  await expect(page.locator('#keyboardBar')).toBeHidden();
  await openSettings(page);
  await section(page);
  await expect(page.getByRole('switch', { name: '显示底部快捷键栏' })).not.toBeChecked();
  await page.getByRole('switch', { name: '显示底部快捷键栏' }).check();
  await save(page);
  await expect(page.locator('#keyboardBar')).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('#keyboardBar')).toBeHidden();
  await expect(page.locator('#mobileBottomNav button')).toHaveCount(3);
});

test('drafts survive section navigation, undo restores saved keys, and storage errors stay explicit', async ({ page }) => {
  await start(page);
  await record(page, 'favorite', 'g');
  await section(page, 'system');
  await section(page);
  await expect(page.locator('#shortcut-favorite-0')).toHaveValue('g');
  await page.locator('#settingsResetBtn').click();
  await expect(page.locator('#shortcut-favorite-0')).toHaveValue('f');
  await record(page, 'favorite', 'g');
  await page.evaluate(() => { Storage.prototype.setItem = () => { throw new Error('blocked'); }; });
  await page.locator('#settingsSaveBtn').click();
  await expect(page.locator('#settingsStatus')).toContainText('浏览器无法保存');
  await expect(page.locator('#settingsStatus')).toHaveClass(/dirty-state/);
  await expect(page.locator('[data-shortcut-hint="favorite"]')).toHaveText('f');
});

for (const width of [320, 390, 921, 1440]) {
  test(`compact settings list and shortcut fields fit themes at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/');
    for (const theme of ['light', 'dark', 'eink']) {
      await chooseTheme(page, theme);
      await openSettings(page);
      await expect(page.locator('[data-settings-tab]').first()).toHaveAttribute('data-settings-tab', 'system');
      await expect(page.locator('.settings-category-card:visible')).toHaveCount(9);
      await expect(page.locator('#feedColSettings, #detailPanelSettings').locator('#settingsSearchInput, .settings-eyebrow, .settings-category-meta, .settings-category-description, .settings-list-footer')).toHaveCount(0);
      const heights = await page.locator('.settings-category-card').evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().height));
      expect(Math.max(...heights)).toBeLessThanOrEqual(40);
      await page.screenshot({ path: `.ui-artifacts/settings-compact-${width}-${theme}.png` });
      await section(page);
      await expect(page.locator('#shortcutSettings input[readonly]')).toHaveCount(20);
      const toggle = page.getByRole('switch', { name: '显示底部快捷键栏' });
      expect(await toggle.evaluate(node => getComputedStyle(node).appearance)).toBe('none');
      await page.screenshot({ path: `.ui-artifacts/shortcuts-top-${width}-${theme}.png` });
      expect(await page.locator('#shortcutSettings').evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
      await page.locator('#resetShortcutsBtn').scrollIntoViewIfNeeded();
      await expect(page.locator('#resetShortcutsBtn')).toBeInViewport();
      await page.screenshot({ path: `.ui-artifacts/shortcuts-${width}-${theme}.png` });
      await record(page, 'favorite', 'g');
      for (const category of ['classification', 'catchup']) {
        await section(page, category);
        const title = await page.locator('#settingsDetailTitle').boundingBox();
        for (const id of ['settingsSaveBtn', 'settingsResetBtn']) {
          const button = await page.locator(`#${id}`).boundingBox();
          expect(Math.abs(button.y + button.height / 2 - title.y - title.height / 2)).toBeLessThan(1);
          expect(button.x).toBeGreaterThanOrEqual(0);
          expect(button.x + button.width).toBeLessThanOrEqual(width);
        }
        await expect(page.locator('#settingsDetailTitle')).toHaveAttribute('title', await page.locator('#settingsDetailTitle').textContent());
        await page.screenshot({ path: `.ui-artifacts/settings-inline-actions-${category}-${width}-${theme}.png` });
      }
      await page.locator('#settingsResetBtn').click();
      await page.keyboard.press('Escape');
    }
  });
}
