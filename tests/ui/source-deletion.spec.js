import { test, expect } from '@playwright/test';
import { openSettings, chooseTheme, answerConfirm } from './helpers.js';

const key = 'reuters/deletion-test';
const button = page => page.locator(`[data-delete-source="${key}"]`);
const checkbox = page => page.locator(`[data-setting-source="${key}"]`);
const dialog = page => page.locator('#appConfirmDialog');
async function start(page) {
  await page.goto('/');
  await expect(page.locator('#feedCardList .news-card').first()).toBeVisible();
  await openSettings(page);
  await page.locator('[data-settings-tab="pipeline"]').click();
  await expect(button(page)).toBeVisible();
}
test.beforeEach(async ({request}) => { await request.post('/__test/source-deletion'); });
test.afterEach(async ({request}) => { await request.post('/__test/source-deletion', {data:{clear:true}}); });

for (const width of [1440, 390]) for (const appearance of ['light', 'dark', 'system-light', 'system-dark', 'eink']) {
  test(`source deletion preview and cancel ${width} ${appearance}`, async ({page, request}) => {
    await page.setViewportSize({width, height:850});
    if (appearance.startsWith('system-')) await page.emulateMedia({colorScheme:appearance.slice(7)});
    await start(page);
    await chooseTheme(page, appearance.startsWith('system-') ? 'system' : appearance);
    const snapshot = (await (await request.get(`/api/settings/sources/${key}`)).json()).source;
    let writes = 0;
    page.on('request', req => { if (req.method() === 'DELETE') writes++; });
    await button(page).click();
    await expect(dialog(page)).toBeVisible();
    await expect(page.locator('#appConfirmCancel')).toBeFocused();
    await expect(page.locator('#appConfirmMessage')).toContainText('Reuters · Deletion Test');
    await expect(page.locator('#appConfirmMessage')).toContainText('新闻数目：3 条');
    await expect(page.locator('#appConfirmMessage')).toContainText(`${(snapshot.data_bytes / 1024).toFixed(1)} KiB`);
    await expect(page.locator('#appConfirmMessage')).toContainText('已删除的新闻不会重新导入');
    await expect(page.locator('#appConfirmMessage')).toContainText('该信源后续的新新闻仍会正常收录');
    expect(await dialog(page).evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
    const box = await dialog(page).boundingBox();
    expect(box.x).toBeGreaterThanOrEqual(0); expect(box.x+box.width).toBeLessThanOrEqual(width);
    for (let i=0;i<5;i++) {
      await page.keyboard.press('Tab');
      expect(await dialog(page).evaluate(node=>node.contains(document.activeElement))).toBe(true);
    }
    await page.screenshot({path:`.ui-artifacts/source-delete-${width}-${appearance}.png`});
    await page.keyboard.press('Escape');
    await expect(dialog(page)).toBeHidden();
    await expect(button(page)).toBeFocused();
    await expect(checkbox(page)).toBeChecked();
    expect(writes).toBe(0);
    await expect(button(page)).toBeEnabled();
    const row = button(page).locator('..');
    expect(await row.evaluate(node=>node.scrollWidth <= node.clientWidth)).toBe(true);
    await page.screenshot({path:`.ui-artifacts/source-rows-${width}-${appearance}.png`});
  });
}

test('delete clears the row and news immediately while retaining unrelated drafts', async ({page, request}) => {
  await start(page);
  const otherInput = page.locator(`[data-setting-source]:not([data-setting-source="${key}"])`).first();
  const otherKey = await otherInput.getAttribute('data-setting-source');
  const wasChecked = await otherInput.isChecked();
  await otherInput.setChecked(!wasChecked);
  await expect(page.locator('#settingsSaveBtn')).toBeVisible();
  let writes = 0;
  page.on('request', req => { if (req.method() === 'DELETE') writes++; });
  await button(page).click();
  await answerConfirm(page);
  await expect(button(page)).toHaveCount(0);
  await expect(page.locator('#toast')).toContainText('共 3 条新闻');
  expect(writes).toBe(1);
  await expect(page.locator(`[data-setting-source="${otherKey}"]`)).toBeChecked({checked:!wasChecked});
  await expect(page.locator('#settingsSaveBtn')).toBeVisible();
  const news = await (await request.get('/api/news?collection=feed&read_filter=all&q=删除测试')).json();
  expect(news.total).toBe(0);
  expect(await (await request.get('/api/news?collection=feed&read_filter=all')).json()).toMatchObject({total:65});
});

test('preview and deletion errors leave a retryable source and do not change its checkbox', async ({page}) => {
  await start(page);
  let failPreview = true, failDelete = true;
  await page.route('**/api/settings/sources/**', async route => {
    if (route.request().method() === 'GET' && failPreview) return route.fulfill({status:500,json:{ok:false,error:'source_delete_failed'}});
    if (route.request().method() === 'DELETE' && failDelete) return route.fulfill({status:409,json:{ok:false,error:'source_data_changed'}});
    return route.continue();
  });
  await button(page).click();
  await expect(page.locator('#settingsStatus')).toContainText('信源删除失败');
  await expect(dialog(page)).toHaveCount(0);
  await expect(button(page)).toBeEnabled();
  failPreview = false;
  await button(page).click(); await answerConfirm(page);
  await expect(page.locator('#settingsStatus')).toContainText('重新确认');
  await expect(button(page)).toBeEnabled(); await expect(checkbox(page)).toBeChecked();
  failDelete = false;
  await button(page).click(); await answerConfirm(page);
  await expect(button(page)).toHaveCount(0);
});

test('loading is guarded, leaving settings suppresses a late preview, and reading size preserves controls', async ({page}) => {
  await start(page);
  const baseline = await button(page).evaluate(node=>getComputedStyle(node).fontSize);
  await page.getByRole('combobox',{name:'阅读字号',exact:true}).click();
  await page.getByRole('option',{name:'特大（130%）',exact:true}).click();
  expect(await button(page).evaluate(node=>getComputedStyle(node).fontSize)).toBe(baseline);
  let release;
  const pending = new Promise(resolve=>{release=resolve;});
  let reads = 0;
  await page.route('**/api/settings/sources/**', async route => {
    reads++;
    const response = await route.fetch();
    await pending;
    await route.fulfill({response});
  });
  await button(page).click();
  await expect(button(page)).toBeDisabled();
  await expect(button(page)).toHaveText('统计中…');
  await page.locator('[data-settings-tab="system"]').click();
  release();
  await expect(button(page)).toBeEnabled();
  await expect(page.locator('#settingsDetailTitle')).toHaveText('系统与版本');
  await expect(dialog(page)).toHaveCount(0);
  expect(reads).toBe(1);
});

test('long source information remains readable with reachable actions on a short phone screen', async ({page}) => {
  await page.setViewportSize({width:320,height:568});
  await page.route('**/api/settings/sources/**', async route => {
    const response = await route.fetch(), data = await response.json();
    data.source.label = '国际新闻来源栏目与账号名称'.repeat(5);
    await route.fulfill({json:data});
  });
  await start(page); await button(page).click();
  await expect(dialog(page)).toBeVisible();
  const box = await dialog(page).boundingBox();
  expect(box.y).toBeGreaterThanOrEqual(0);
  expect(box.y+box.height).toBeLessThanOrEqual(568);
  await expect(page.locator('#appConfirmAccept')).toBeInViewport();
  expect(await dialog(page).evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true);
  await page.screenshot({path:'.ui-artifacts/source-delete-short-phone.png'});
  await answerConfirm(page,false);
  await expect(checkbox(page)).toBeChecked();
});
