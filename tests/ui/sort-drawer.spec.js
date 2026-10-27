import { test, expect } from '@playwright/test';
import { chooseTheme, openNavigation } from './helpers.js';

const filter = (page, value) => page.locator(`#feedColFeed [data-filter="${value}"]`);
async function expectOrder(page, direction, firstId) {
  await expect(page.locator('#sortNewsBtn')).toHaveAttribute('aria-label', `排序：${direction}，点击切换`);
  await expect(page.locator('#feedCardList .news-card').first()).toHaveAttribute('data-id', firstId);
}
async function navigate(page, id) {
  await openNavigation(page);
  await page.locator(id).click();
}

test.describe('news sorting', () => {
  test.afterEach(async ({ request }) => {
    for (const id of ['ui-012', 'ui-063', 'ui-064']) {
      await request.patch(`/api/news/${id}/state`, { data: { read_later: true } });
      await request.patch(`/api/news/${id}/state`, { data: { read_later: false, favorite: false, important: false } });
    }
  });

  for (const width of [390, 1050, 1440]) {
    test(`defaults, manual reversal and search agree across filters at ${width}px`, async ({ page, request }) => {
      await request.post('/__test/reset-reading');
      // Other suites may have completed every cached article; seed the pending row explicitly.
      await request.patch('/api/news/ui-063/state', { data: { read_later: true } });
      for (const id of ['ui-012', 'ui-064']) {
        await request.patch(`/api/news/${id}/state`, { data: { read_later: true, favorite: true, important: true } });
      }
      // Queuing marks news read; restore the complete unread sorting fixture.
      await request.post('/__test/reset-reading');
      await page.setViewportSize({ width, height: 844 });
      await page.goto('/');
      await expectOrder(page, '旧到新', 'ui-064');
      await page.locator('#sortNewsBtn').click();
      await expectOrder(page, '新到旧', 'ui-012');
      await filter(page, 'all').click();
      await expectOrder(page, '新到旧', 'ui-012');
      await page.locator('#sortNewsBtn').click();
      await expectOrder(page, '旧到新', 'ui-064');
      await filter(page, 'all').click();
      await expectOrder(page, '旧到新', 'ui-064');
      if (width <= 920) await page.locator('#feedSearchToggleBtn').click();
      await page.locator('#feedSearchInput').fill('isolated interface test fixture');
      await expect(page.locator('#feedCardList .news-card')).toHaveCount(65);
      await expectOrder(page, '旧到新', 'ui-064');
      await page.locator('#sortNewsBtn').click();
      await expectOrder(page, '新到旧', 'ui-012');
      await filter(page, 'unread').click();
      await expectOrder(page, '旧到新', 'ui-064');
      await filter(page, 'all').click();
      await expectOrder(page, '新到旧', 'ui-012');
      if (width <= 920) await page.locator('#feedSearchToggleBtn').click();
      else await page.locator('#feedSearchInput').fill('');
      await expectOrder(page, '新到旧', 'ui-012');

      for (const id of ['ui-012', 'ui-064']) {
        await request.patch(`/api/news/${id}/state`, { data: { read_later: false, read_later_done: true } });
      }
      await navigate(page, '#navReadLaterBtn');
      await expectOrder(page, '旧到新', 'ui-063');
      await filter(page, 'all').click();
      await expectOrder(page, '新到旧', 'ui-012');
      await filter(page, 'read').click();
      await expectOrder(page, '新到旧', 'ui-012');
      await page.locator('#sortNewsBtn').click();
      await expectOrder(page, '旧到新', 'ui-064');
      await filter(page, 'unread').click();
      await expectOrder(page, '旧到新', 'ui-063');
      await navigate(page, '#navFavoritesBtn');
      await expectOrder(page, '新到旧', 'ui-012');
      await navigate(page, '#navImportantBtn');
      await expectOrder(page, '新到旧', 'ui-012');
      await filter(page, 'notes').click();
      await expectOrder(page, '新到旧', 'ui-000');
    });
  }
});

for (const [theme, scheme] of [['light', 'light'], ['dark', 'dark'], ['system', 'light'], ['system', 'dark'], ['eink', 'light']]) {
  test(`drawer has no closed shadow and survives breakpoints in ${theme}/${scheme}`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'no-preference' });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/');
    await chooseTheme(page, theme);
    const drawer = page.locator('#leftNavDrawer');
    for (const width of [390, 920, 921, 1279, 1280, 921]) {
      await page.setViewportSize({ width, height: 844 });
      await expect(drawer).toHaveCSS('box-shadow', 'none');
      await expect(drawer).toHaveJSProperty('inert', width < 1280);
      if (width === 1280) {
        await expect(drawer).toBeInViewport();
        continue;
      }
      await expect(drawer).not.toBeInViewport();
      await page.locator('#openNavBtn').click();
      await expect(drawer).toHaveJSProperty('inert', false);
      await expect(drawer).toBeInViewport();
      await expect(drawer).not.toHaveCSS('box-shadow', 'none');
      if (theme === 'eink') {
        await expect(drawer).toHaveCSS('box-shadow', 'rgb(0, 0, 0) 0px 4px 0px 0px');
        await expect(drawer).toHaveCSS('transition-duration', '0s');
      }
      await page.locator('.drawer-close-btn').click();
      await expect(drawer).toHaveCSS('box-shadow', 'none');
      await expect(drawer).not.toBeInViewport();
      if (width === 390 || width === 921) {
        await page.screenshot({ path: `.ui-artifacts/drawer-closed-${width}-${theme}-${scheme}.png` });
      }
      await page.locator('#openNavBtn').click();
      await page.keyboard.press('Escape');
      await expect(drawer).toHaveCSS('box-shadow', 'none');
      await expect(page.locator('#openNavBtn')).toBeFocused();
    }
  });
}
