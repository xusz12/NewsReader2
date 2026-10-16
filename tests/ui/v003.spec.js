import { test, expect } from '@playwright/test';

async function sidebarCounts(page, count) {
  await page.route('**/api/nav-summary', route => route.fulfill({ json: {
    ok: true, summary: { feed_unread: count, read_later_unread: count, favorites: count },
  } }));
  await page.route('**/api/sources?*', route => route.fulfill({ json: {
    ok: true, sources: [
      { key: 'reuters', label: 'Reuters', count },
      { key: 'bloomberg', label: 'Bloomberg', count: 0 },
      { key: 'x', label: 'X', count: 0 },
    ],
  } }));
  await page.goto('/');
  await expect(page.locator('[data-source="reuters"]')).toBeVisible();
}

test('zero counts hide badges without hiding sidebar entries', async ({ page }) => {
  await sidebarCounts(page, 0);
  for (const id of ['unreadBadge', 'readLaterNavBadge', 'starredNavBadge']) {
    await expect(page.locator(`#${id}`)).toBeHidden();
  }
  await expect(page.locator('#sourceFilters .nav-badge')).toHaveCount(0);
  for (const source of ['all', 'reuters', 'bloomberg', 'x']) {
    await expect(page.locator(`[data-source="${source}"]`)).toBeVisible();
  }
});

test('only selected collection and source badges use the accent background', async ({ page }) => {
  await sidebarCounts(page, 3);
  const background = badge => badge.evaluate(node => getComputedStyle(node).backgroundColor);
  const accent = await background(page.locator('#unreadBadge'));
  const badges = ['#unreadBadge', '#readLaterNavBadge', '#starredNavBadge'];
  for (const [button, selected] of [
    ['#navReadLaterBtn', '#readLaterNavBadge'],
    ['#navFavoritesBtn', '#starredNavBadge'],
    ['#navFeedBtn', '#unreadBadge'],
  ]) {
    await page.locator(button).click();
    for (const badge of badges) {
      await expect(page.locator(badge)).toHaveText('3');
      if (badge === selected) await expect(page.locator(badge)).toHaveCSS('background-color', accent);
      else expect(await background(page.locator(badge))).not.toBe(accent);
    }
  }
  const all = page.locator('[data-source="all"] .nav-badge');
  const reuters = page.locator('[data-source="reuters"] .nav-badge');
  await expect(all).toHaveCSS('background-color', accent);
  expect(await background(reuters)).not.toBe(accent);
  await page.locator('[data-source="reuters"]').click();
  await expect(reuters).toHaveCSS('background-color', accent);
  expect(await background(all)).not.toBe(accent);
  await expect(page.locator('[data-source="bloomberg"] .nav-badge')).toHaveCount(0);
  await page.screenshot({ path: '.ui-artifacts/v003-sidebar.png' });
});

for (const width of [1920, 2560]) {
  test(`wide reader ${width}px scrolls from both margins and keeps its scrollbar at the panel edge`, async ({ page, request }) => {
    await request.post('/__test/reset-reading');
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/');
    await expect(page.locator('#articleBodyContent')).toBeVisible();
    const panel = await page.locator('#detailPanelFeed').boundingBox();
    const scroll = page.locator('#detailScrollArea');
    const area = await scroll.boundingBox();
    const article = await page.locator('#articleMainTitle').boundingBox();
    expect(Math.abs(area.x - panel.x)).toBeLessThanOrEqual(1);
    expect(Math.abs(area.x + area.width - panel.x - panel.width)).toBeLessThanOrEqual(1);
    expect(article.width).toBeLessThanOrEqual(760);
    expect(article.x - area.x).toBeGreaterThan(24);
    for (const x of [area.x + 10, area.x + area.width - 30]) {
      await scroll.evaluate(node => { node.scrollTop = 0; });
      await page.mouse.move(x, area.y + 150);
      await page.mouse.wheel(0, 300);
      await expect.poll(() => scroll.evaluate(node => node.scrollTop)).toBeGreaterThan(0);
    }
    await scroll.evaluate(node => { node.scrollTop = 0; });
    await page.screenshot({ path: `.ui-artifacts/v003-reader-${width}.png` });
  });
}
