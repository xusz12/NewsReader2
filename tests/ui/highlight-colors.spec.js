import { test, expect } from '@playwright/test';
import { chooseTheme } from './helpers.js';

const path = '/api/news/ui-064';
const colors = [['yellow', '黄'], ['red', '红'], ['green', '绿'], ['blue', '蓝']];
const paragraph = '#articleBodyContent > p:nth-child(2)';

async function openArticle(page) {
  await page.goto('/');
  await page.locator('#feedCardList .news-card-title').first().click();
  await expect(page.locator(paragraph)).toBeVisible();
}

async function selectParagraph(page, selector = paragraph) {
  const node = page.locator(selector);
  await node.scrollIntoViewIfNeeded();
  await node.evaluate(element => {
    const range = document.createRange(); range.selectNodeContents(element);
    getSelection().removeAllRanges(); getSelection().addRange(range);
    element.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
  });
  await expect(page.locator('#selectionPopover')).toBeVisible();
  await expect(page.getByRole('button', { name: /^(高亮|取消高亮)$/ })).toHaveCount(0);
}

// Composite translucent highlights over their actual ancestor backgrounds.
async function textContrast(locator) {
  return locator.evaluate(node => {
    const ctx = document.createElement('canvas').getContext('2d', { willReadFrequently: true });
    const ancestors = [];
    for (let current = node; current; current = current.parentElement) ancestors.unshift(current);
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, 1, 1);
    for (const current of ancestors) {
      ctx.fillStyle = getComputedStyle(current).backgroundColor; ctx.fillRect(0, 0, 1, 1);
    }
    const background = [...ctx.getImageData(0, 0, 1, 1).data].slice(0, 3);
    ctx.fillStyle = getComputedStyle(node).color; ctx.fillRect(0, 0, 1, 1);
    const foreground = [...ctx.getImageData(0, 0, 1, 1).data].slice(0, 3);
    const luminance = rgb => rgb.map(x => x / 255).map(x => x <= .04045 ? x / 12.92 : ((x + .055) / 1.055) ** 2.4)
      .reduce((total, value, index) => total + value * [.2126, .7152, .0722][index], 0);
    const values = [luminance(background), luminance(foreground)].sort((a, b) => a - b);
    return (values[1] + .05) / (values[0] + .05);
  });
}

test.beforeEach(async ({ request }) => {
  await request.post('/__test/reset-reading');
  const data = await (await request.get(path + '/highlights')).json();
  for (const surface of Object.values(data.highlight_surfaces)) {
    for (const row of surface.highlights) await request.delete(path + '/highlights/' + row.id);
  }
});

test('红绿蓝高亮保存、刷新恢复，键盘换色保留定位与批注', async ({ page, request }) => {
  await openArticle(page);
  for (const [selector, color, label] of [
    [paragraph, 'red', '红'], ['.digest-lead', 'green', '绿'], ['.digest-point-item:nth-child(2) > span:last-child', 'blue', '蓝'],
  ]) {
    await selectParagraph(page, selector);
    await page.getByRole('button', { name: label + '色高亮', exact: true }).click();
    await expect(page.locator(selector + ' mark').first()).toHaveClass(new RegExp('highlight-' + color));
  }
  const saved = (await (await request.get(path + '/highlights')).json()).highlight_surfaces.ai_zh.highlights[0];
  await request.put(path + '/highlights/' + saved.id + '/annotation', { data: { annotation_text: '保留原来的批注' } });
  await page.reload();
  await expect(page.locator(paragraph + ' mark').first()).toHaveClass(/highlight-red/);
  await expect(page.locator('.digest-lead mark').first()).toHaveClass(/highlight-green/);
  await expect(page.locator('.digest-point-item:nth-child(2) mark').first()).toHaveClass(/highlight-blue/);
  await page.locator(paragraph + ' mark').first().focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('button', { name: '红色高亮', exact: true })).toBeFocused();
  await expect(page.getByRole('button', { name: '红色高亮', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await page.keyboard.press('Tab');
  await page.keyboard.press('Enter');
  await expect(page.locator(paragraph + ' mark').first()).toHaveClass(/highlight-green/);
  const changed = (await (await request.get(path + '/highlights')).json()).highlight_surfaces.ai_zh.highlights;
  expect(changed).toHaveLength(1);
  expect(changed[0]).toMatchObject({ id: saved.id, start_offset: saved.start_offset, end_offset: saved.end_offset,
    selected_text: saved.selected_text, color: 'green', annotation_text: '保留原来的批注' });
  await page.locator(paragraph + ' mark').first().click();
  await page.getByRole('button', { name: '黄色高亮', exact: true }).click();
  await expect(page.locator(paragraph + ' mark').first()).toHaveClass(/highlight-yellow/);
  await page.locator(paragraph + ' mark').first().click();
  await expect(page.getByRole('button', { name: '黄色高亮', exact: true })).toHaveAttribute('title', '取消黄色高亮');
  await page.getByRole('button', { name: '黄色高亮', exact: true }).click();
  await expect(page.locator(paragraph + ' mark')).toHaveCount(0);
  expect((await (await request.get(path + '/highlights')).json()).highlight_surfaces.ai_zh.highlights).toHaveLength(0);
  await page.reload();
  await expect(page.locator(paragraph)).toBeVisible();
  await expect(page.locator(paragraph + ' mark')).toHaveCount(0);
});

test('高亮保存失败保留选区和颜色操作，可直接重试', async ({ page, request }) => {
  await openArticle(page);
  await selectParagraph(page);
  const selectedText = await page.evaluate(() => getSelection().toString());
  await page.route('**/api/news/ui-064/highlights', route => route.fulfill({ status: 503, json: { ok: false, error: 'save_failed' } }));
  await page.getByRole('button', { name: '蓝色高亮', exact: true }).click();
  await expect(page.locator('#toast')).toBeVisible();
  await expect(page.getByRole('button', { name: '蓝色高亮', exact: true })).toBeEnabled();
  expect(await page.evaluate(() => getSelection().toString())).toBe(selectedText);
  await expect(page.locator(paragraph + ' mark')).toHaveCount(0);
  await page.unroute('**/api/news/ui-064/highlights');
  await page.getByRole('button', { name: '蓝色高亮', exact: true }).click();
  await expect(page.locator(paragraph + ' mark').first()).toHaveClass(/highlight-blue/);
  expect((await (await request.get(path + '/highlights')).json()).highlight_surfaces.ai_zh.highlights).toHaveLength(1);
});

for (const width of [390, 1440]) for (const theme of ['light', 'dark', 'system-light', 'system-dark', 'eink']) {
  test(`高亮色板主题、字号与视口 ${width} ${theme}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ colorScheme: theme === 'system-dark' ? 'dark' : 'light' });
    await openArticle(page);
    await chooseTheme(page, theme.startsWith('system') ? 'system' : theme);
    for (const [color, label] of colors) {
      await selectParagraph(page);
      const button = page.getByRole('button', { name: label + '色高亮', exact: true });
      await expect(button).toBeInViewport();
      expect(await textContrast(button.locator('.highlight-color-swatch'))).toBeGreaterThanOrEqual(4.5);
      if (width === 390) {
        const box = await button.boundingBox();
        expect(box.width).toBeGreaterThanOrEqual(44); expect(box.height).toBeGreaterThanOrEqual(44);
      }
      await button.click();
      const mark = page.locator(paragraph + ' mark').first();
      await expect(mark).toHaveClass(new RegExp('highlight-' + color));
      expect(await textContrast(mark)).toBeGreaterThanOrEqual(4.5);
      if (theme === 'eink') {
        await expect(mark).toHaveCSS('background-color', 'rgb(229, 229, 229)');
        await expect(mark).toHaveCSS('text-decoration-line', 'underline');
      }
    }
    for (const size of ['small', 'standard', 'large', 'extra-large']) {
      await page.evaluate(size => document.documentElement.dataset.fontSize = size, size);
      await page.locator(paragraph).scrollIntoViewIfNeeded();
      await page.locator(paragraph + ' mark').first().click();
      const popover = page.locator('#selectionPopover');
      await expect(popover).toBeVisible();
      const box = await popover.boundingBox();
      expect(box.x).toBeGreaterThanOrEqual(0); expect(box.x + box.width).toBeLessThanOrEqual(width);
      expect(box.y).toBeGreaterThanOrEqual(0); expect(box.y + box.height).toBeLessThanOrEqual(900);
      expect(await popover.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
      await expect(page.getByRole('button', { name: '蓝色高亮', exact: true })).toHaveAttribute('aria-pressed', 'true');
      await expect(page.getByRole('button', { name: '蓝色高亮', exact: true })).toHaveCSS('box-shadow', 'none');
      await expect(page.getByRole('button', { name: '蓝色高亮', exact: true }).locator('.highlight-color-swatch')).toHaveText('✓');
      if (width === 390) expect(box.height).toBeLessThanOrEqual(54);
      await page.keyboard.press('Escape');
    }
    await page.locator(paragraph + ' mark').first().focus();
    await page.keyboard.press('Enter');
    await page.screenshot({ path: `.ui-artifacts/highlight-colors-${width}-${theme}.png` });
    await page.keyboard.press('Escape');
    await expect(page.locator('#selectionPopover')).toBeHidden();
    await expect(page.locator(paragraph + ' mark').first()).toBeFocused();
  });
}
