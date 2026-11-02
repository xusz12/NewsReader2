import { test, expect } from '@playwright/test';
import { chooseTheme, openNavigation, openSettings } from './helpers.js';

const storageKey = 'newsreader_workbench_shortcuts';
const state = async request => (await (await request.get('/api/news/ui-064/detail')).json());

test.beforeEach(async ({request}) => {
  await request.post('/__test/reset-reading');
  await request.patch('/api/news/ui-064/state', {data:{favorite:false,important:false}});
});

for (const width of [390, 1440]) test(`retired default and custom read shortcuts stay removed at ${width}px`, async ({page, request}) => {
  await page.setViewportSize({width,height:900});
  await page.addInitScript(key => localStorage.setItem(key, JSON.stringify({show_bar:true,bindings:{read:['m','g'],favorite:['v']}})), storageKey);
  await page.goto('/');
  await expect(page.locator('#feedCardList [data-id="ui-064"]')).toBeVisible();
  const before = (await state(request)).read_at;
  for (const key of ['m','g']) await page.keyboard.press(key);
  expect((await state(request)).read_at).toBe(before);
  const preferences = await page.evaluate(key => JSON.parse(localStorage.getItem(key)), storageKey);
  expect(preferences.bindings.read).toBeUndefined();
  expect(preferences.bindings.favorite).toEqual(['v']);
  await openSettings(page);
  await page.locator('[data-settings-tab="shortcuts"]').click();
  await expect(page.locator('#shortcutSettings .shortcut-setting-row')).toHaveCount(9);
  await expect(page.locator('[data-shortcut-action="read"]')).toHaveCount(0);
  for (const selector of ['#keyboardBarKeys', '#shortcutHelpRows']) await expect(page.locator(selector)).not.toContainText('标为已读');
  await page.locator('#resetShortcutsBtn').click();
  await page.locator('#settingsSaveBtn').click();
  await expect(page.locator('#settingsStatus')).toHaveText('设置已保存');
  expect((await page.evaluate(key => JSON.parse(localStorage.getItem(key)), storageKey)).bindings.read).toBeUndefined();
  await page.keyboard.press('Escape');
  await request.patch('/api/news/ui-064/state', {data:{read:true}});
  await openNavigation(page);await page.locator('#navFeedBtn').click();
  await page.locator('#feedColFeed [data-filter="all"]').click();
  await page.locator('#sortNewsBtn').click();
  await page.locator('#feedCardList [data-id="ui-064"] .news-card-title').click();
  const stamp = (await state(request)).read_at;
  for (const key of ['m','m','g']) await page.keyboard.press(key);
  expect((await state(request)).read_at).toBe(stamp);
});

test('retired shortcut is ignored even if local storage cannot be cleaned', async ({page,request}) => {
  await page.addInitScript(key => {
    localStorage.setItem(key,JSON.stringify({bindings:{read:['m','g'],favorite:['v']}}));
    Storage.prototype.setItem = () => { throw new Error('read-only'); };
  },storageKey);
  await page.goto('/');await expect(page.locator('#feedCardList [data-id="ui-064"]')).toBeVisible();
  await page.keyboard.press('m');await page.keyboard.press('g');
  expect((await state(request)).read_at).toBeNull();
  await expect(page.locator('[data-shortcut-hint="favorite"]')).toHaveText('v');
});

test('m no longer completes saved articles; the existing completion button still works', async ({page,request}) => {
  await request.patch('/api/news/ui-064/state', {data:{read_later:true}});
  await page.goto('/');await openNavigation(page);await page.locator('#navReadLaterBtn').click();
  const button = page.locator('#feedCardList [data-id="ui-064"] [data-state="read_later"]');
  await expect(button).toHaveText('全文待看');
  const before = await state(request);
  await page.keyboard.press('m');await page.keyboard.press('m');
  expect((await state(request)).read_later_done_at).toBeNull();
  await button.click();await expect(button).toHaveText('已看完');
  await expect(button).toHaveAttribute('aria-busy','false');
  expect((await state(request)).read_at).toBe(before.read_at);
  await button.click();await expect(button).toHaveText('全文待看');
  await expect(button).toHaveAttribute('aria-busy','false');
  expect((await state(request)).read_later_done_at).toBeNull();
});

for (const width of [390, 1440]) test(`important and favorite acknowledgements preserve full-text state across themes at ${width}px`, async ({page, request}) => {
  test.setTimeout(60000);
  await page.setViewportSize({width,height:900});
  for (const [theme, scheme] of [['light','light'],['dark','dark'],['system','light'],['system','dark'],['eink','light']]) {
    for (const flag of ['important','favorite']) {
      await request.patch('/api/news/ui-064/state', {data:{important:false,favorite:false,read_later:false,read_later_done:true}});
      await request.post('/__test/reset-reading');
      await page.emulateMedia({colorScheme:scheme});
      await page.goto('/');
      await chooseTheme(page,theme);
      const card = page.locator('#feedCardList [data-id="ui-064"]');
      await expect(card).toBeVisible();
      await page.getByRole('combobox',{name:'阅读字号',exact:true}).click();
      await page.getByRole('option',{name:'特大（130%）',exact:true}).click();
      const before = await state(request);
      const button = card.locator(`[data-state="${flag}"]`);
      await button.click();
      await expect(button).toHaveAttribute('aria-busy','false');
      await expect(card.locator('.unread-dot')).toHaveCount(0);
      await expect(page.locator('#unreadBadge')).toHaveText('64');
      const acknowledged = await state(request);
      expect(acknowledged.read_at).toBeTruthy();
      expect(acknowledged.read_later_done_at).toBe(before.read_later_done_at);
      expect(acknowledged.read_later_at).toBe(before.read_later_at);
      await button.click();await expect(button).toHaveAttribute('aria-busy','false');
      expect((await state(request)).read_at).toBe(acknowledged.read_at);
      await expect(page.locator('#unreadBadge')).toHaveText('64');
      if (flag === 'favorite') await page.screenshot({path:`.ui-artifacts/browsing-${width}-${theme}-${scheme}.png`});
    }
  }
});
