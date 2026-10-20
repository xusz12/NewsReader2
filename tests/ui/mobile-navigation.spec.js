import { openNavigation, openSettings } from "./helpers.js";
import { test, expect } from '@playwright/test';

test.use({ viewport: { width: 360, height: 800 }, isMobile: true, hasTouch: true });
test.beforeEach(async ({ request }) => { await request.post('/__test/reset-reading'); });

test('bottom navigation stays limited to reading views while other workspaces use the drawer', async ({ page }) => {
  await page.goto('/');
  const bottom = page.locator('#mobileBottomNav');
  await expect(bottom.locator('button > span')).toHaveText(['新闻流', '稍后再看', '收藏']);
  for (const [collection, title] of [['favorites', '收藏'], ['read_later', '稍后再看'], ['feed', '新闻流']]) {
    const loaded = page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === '/api/news' && url.searchParams.get('collection') === collection;
    });
    await bottom.getByRole('button', { name: title }).click();
    await loaded;
    await expect(bottom.locator('[aria-current="page"]')).toHaveAttribute('data-collection', collection);
    await expect(page.locator(`#leftNavDrawer [data-collection="${collection}"]`)).toHaveAttribute('aria-current', 'page');
  }
  for (const collection of ['notes', 'summaries']) {
    await openNavigation(page);
    await page.locator(`#leftNavDrawer [data-collection="${collection}"]`).click();
    await expect(bottom.locator('[aria-current="page"]')).toHaveCount(0);
    await expect(page.locator('#leftNavDrawer')).toHaveJSProperty('inert', true);
  }
  await openSettings(page);
  await expect(page.locator('#feedColSettings')).toBeVisible();
  await expect(bottom.locator('button > span')).toHaveText(['新闻流', '稍后再看', '收藏']);
  await bottom.getByRole('button', { name: '收藏', exact: true }).click();
  await expect(page.locator('#feedColSettings')).toBeHidden();
  await expect(bottom.getByRole('button', { name: '收藏', exact: true })).toHaveAttribute('aria-current', 'page');
});

async function openReader(page) {
  await page.goto('/');
  await page.locator('#feedCardList .news-card-title').first().click();
  await expect(page.locator('#articleBodyContent')).toBeVisible();
  await expect(page.locator('#detailPanelFeed')).toHaveCSS('transform', 'matrix(1, 0, 0, 1, 0, 0)');
}

async function swipe(page, { x = 20, dx = 110, dy = 4, cancel = false, multi = false, finish = true, target = '#detailScrollArea' } = {}) {
  return page.evaluate(({ x, dx, dy, cancel, multi, finish, target }) => {
    const element = document.querySelector(target);
    const touch = (clientX, clientY, identifier = 1) => ({ identifier, target: element, clientX, clientY });
    const dispatch = (type, touches, changedTouches) => {
      // WebKit does not expose a constructible Touch. Supply the same touch
      // payload on a synthetic event so gesture tests run in both engines.
      const event = new Event(type, { bubbles: true, cancelable: true });
      Object.defineProperties(event, { touches: { value: touches }, changedTouches: { value: changedTouches } });
      element.dispatchEvent(event);
      return event.defaultPrevented;
    };
    const start = touch(x, 220), end = touch(x + dx, 220 + dy);
    dispatch('touchstart', [start], [start]);
    const consumed = dispatch('touchmove', multi ? [end, touch(x + 20, 230, 2)] : [end], [end]);
    if (finish) dispatch(cancel ? 'touchcancel' : 'touchend', [], [end]);
    return consumed;
  }, { x, dx, dy, cancel, multi, finish, target });
}

test('edge swipe returns to the news list without resetting its scroll position', async ({ page }) => {
  await page.goto('/');
  const list = page.locator('#feedCardList');
  await list.locator('.news-card').first().waitFor();
  await list.evaluate(node => { node.scrollTop = 80; });
  const scroll = await list.evaluate(node => node.scrollTop);
  await list.locator('.news-card-title').nth(1).click();
  await expect(page.locator('#articleBodyContent')).toBeVisible();
  expect(await swipe(page)).toBe(true);
  await expect(page.locator('#detailPanelFeed')).toBeHidden();
  await expect(list).toBeVisible();
  expect(await list.evaluate(node => node.scrollTop)).toBe(scroll);
  await expect(page.locator('#mobileReaderBackBtn')).toBeHidden();
  await expect(page.locator('#openNavBtn')).toBeVisible();
});

test('short, non-edge, vertical, canceled and multi-touch gestures keep the reader open', async ({ page }) => {
  await openReader(page);
  for (const gesture of [{ dx: 50 }, { x: 60 }, { dx: 12, dy: 100 }, { cancel: true }, { multi: true }]) {
    const consumed = await swipe(page, gesture);
    if (gesture.x || gesture.dy || gesture.multi) expect(consumed).toBe(false);
    await expect(page.locator('#articleBodyContent')).toBeVisible();
    await expect(page.locator('#detailPanelFeed')).toHaveCSS('transform', 'matrix(1, 0, 0, 1, 0, 0)');
    await expect(page.locator('#agentDock')).toHaveCSS('transform', 'none');
  }
});

test('selected text and an open Agent do not trigger swipe navigation', async ({ page }) => {
  await page.route('**/api/news/*/agent/session', route => route.fulfill({ json: {
    ok: true, context_available: true, session: { id: 'mobile-test', runtime: 'codex', model: 'test-model' }, messages: [], jobs: [],
  } }));
  await openReader(page);
  await page.locator('#articleBodyContent p').first().evaluate(node => {
    const range = document.createRange();
    range.selectNodeContents(node);
    getSelection().removeAllRanges();
    getSelection().addRange(range);
  });
  expect(await swipe(page)).toBe(false);
  await expect(page.locator('#articleBodyContent')).toBeVisible();
  await page.evaluate(() => getSelection().removeAllRanges());
  await page.locator('#agentDockToggleBtn').click();
  await expect(page.locator('#agentPromptInput')).toBeEnabled();
  await page.locator('#agentPromptInput').fill('保留输入草稿');
  expect(await swipe(page, { target: '#agentPromptInput' })).toBe(false);
  expect(await swipe(page)).toBe(false);
  await expect(page.locator('#agentPromptInput')).toHaveValue('保留输入草稿');
  await expect(page.locator('#articleBodyContent')).toBeVisible();
});

test('idea detail supports edge return while editing gestures preserve the draft', async ({ page }) => {
  await page.goto('/');
  await openNavigation(page);
  await page.locator('#navNotesBtn').click();
  await page.locator('#newIdeaBtn').click();
  await page.locator('#ideaNote').fill('返回后保留想法草稿');
  expect(await swipe(page, { target: '#ideaNote' })).toBe(false);
  await expect(page.locator('#mobileReaderBackBtn')).toHaveAccessibleName('返回想法列表');
  expect(await swipe(page, { target: '#ideaDetailBody .article-header' })).toBe(true);
  await expect(page.locator('#detailPanelIdeas')).toBeHidden();
  await expect(page.locator('#ideasCardList')).toBeVisible();
  await page.locator('#newIdeaBtn').click();
  await expect(page.locator('#ideaNote')).toHaveValue('返回后保留想法草稿');
});

test('the underlying list does not auto-mark news read until the reader is closed', async ({ page, request }) => {
  await page.clock.install();
  await page.route('**/api/news?*', async route => {
    const data = await (await route.fetch()).json();
    await route.fulfill({ json: { ...data, ...(data.items ? { items: data.items.slice(0, 1) } : {}), total: 1, has_more: false, next_cursor: null } });
  });
  await openReader(page);
  await page.clock.fastForward(6000);
  expect((await (await request.get('/api/news/ui-064/detail')).json()).read_at).toBeFalsy();
  await swipe(page);
  await page.clock.runFor(50);
  await page.clock.fastForward(6000);
  await expect.poll(async () => (await (await request.get('/api/news/ui-064/detail')).json()).read_at).toBeTruthy();
});

test('every settings subpage swipes back to its category and preserves the original reader', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 450 });
  await openReader(page);
  const title = await page.locator('#articleMainTitle').textContent();
  await openSettings(page);
  const list = page.locator('.settings-category-list');
  for (const section of ['models', 'deepseek', 'pipeline', 'storage', 'system']) {
    const category = page.locator(`[data-settings-tab="${section}"]`);
    await category.click();
    await expect(page.locator(`#settingsContent > [data-section="${section}"]`)).toBeVisible();
    await expect(page.locator('#settingsDetailTitle')).toBeFocused();
    const scroll = await list.evaluate(node => node.scrollTop);
    await expect(page.locator('#feedColSettings')).toBeVisible();
    await expect(page.locator('#feedColSettings')).toHaveJSProperty('inert', true);
    expect(await swipe(page, { target: '.settings-main' })).toBe(true);
    await expect(page.locator('#detailPanelSettings')).toBeHidden();
    await expect(page.locator('#detailPanelSettings')).toHaveJSProperty('inert', true);
    await expect(page.locator('#feedColSettings')).toHaveJSProperty('inert', false);
    await expect(category).toBeFocused();
    expect(await list.evaluate(node => node.scrollTop)).toBe(scroll);
    await expect(page.locator('#feedView')).toHaveClass(/settings-active/);
    await expect(page.locator('#detailPanelFeed')).toBeHidden();
  }
  await page.keyboard.press('Escape');
  await expect(page.locator('#detailPanelFeed')).toBeVisible();
  await expect(page.locator('#articleMainTitle')).toHaveText(title);
});

test('settings swipe preserves the draft and search while ignoring editing and canceled gestures', async ({ page }) => {
  await page.goto('/');
  await openSettings(page);
  await page.locator('#settingsSearchInput').fill('翻译');
  await page.locator('[data-settings-tab="deepseek"]').click();
  const input = page.locator('input[name="llm.translation.model"]');
  await input.fill('settings-swipe-draft');
  for (const gesture of [{ target: 'input[name="llm.translation.model"]' }, { dx: 50 }, { x: 60 }, { dx: 12, dy: 100 }, { cancel: true }, { multi: true }]) {
    const consumed = await swipe(page, { target: '.settings-main', ...gesture });
    if (gesture.target || gesture.x || gesture.dy || gesture.multi) expect(consumed).toBe(false);
    await expect(page.locator('#detailPanelSettings')).toHaveCSS('transform', 'matrix(1, 0, 0, 1, 0, 0)');
    await expect(input).toHaveValue('settings-swipe-draft');
  }
  await swipe(page, { target: '.settings-main' });
  await expect(page.locator('#detailPanelSettings')).toBeHidden();
  await expect(page.locator('#settingsSearchInput')).toHaveValue('翻译');
  await expect(page.locator('.settings-category-card:visible')).toHaveCount(1);
  await page.locator('[data-settings-tab="deepseek"]').click();
  await expect(input).toHaveValue('settings-swipe-draft');
  await expect(page.locator('#settingsStatus')).toHaveClass(/dirty-state/);
});

test.describe('original sliding transitions', () => {
  test.use({ reducedMotion: 'no-preference' });

  test('entering every settings category keeps the workspace in place throughout the animation', async ({ page }) => {
    await page.setViewportSize({ width: 402, height: 874 });
    await page.goto('/');
    await openSettings(page);
    await expect(page.locator('#modelSettings')).toBeAttached();
    const panel = page.locator('#detailPanelSettings');
    for (const section of ['models', 'pipeline', 'deepseek', 'catchup', 'storage', 'system']) {
      await page.evaluate(() => {
        window.settingsFrames = [];
        window.samplingSettings = true;
        const containers = [document.querySelector('#feedView'), document.querySelector('.workspace-view'), document.querySelector('.app-root')];
        const sample = () => {
          window.settingsFrames.push(containers.map(node => node.scrollLeft));
          if (window.samplingSettings) requestAnimationFrame(sample);
        };
        requestAnimationFrame(sample);
      });
      await page.locator(`[data-settings-tab="${section}"]`).tap();
      await expect(page.locator('#settingsDetailTitle')).toBeFocused();
      await expect(panel).toHaveCSS('transform', 'matrix(1, 0, 0, 1, 0, 0)');
      const scrollOffsets = await page.evaluate(() => {
        window.samplingSettings = false;
        return window.settingsFrames.flat();
      });
      expect(scrollOffsets.length).toBeGreaterThan(0);
      expect(Math.max(...scrollOffsets.map(Math.abs))).toBe(0);
      const rect = await panel.boundingBox();
      expect(rect.x).toBe(0);
      expect(rect.width).toBe(402);
      await expect(page.locator(`#settingsContent > [data-section="${section}"]`)).toBeVisible();
      await page.locator('#settingsCategoryBackBtn').tap();
      await expect(panel).toBeHidden();
    }
  });

  test('settings subpage follows the finger over categories and rebounds or exits in 200ms', async ({ page }) => {
    await page.goto('/');
    await openSettings(page);
    await page.locator('[data-settings-tab="deepseek"]').click();
    const panel = page.locator('#detailPanelSettings');
    await expect(panel).toHaveCSS('transform', 'matrix(1, 0, 0, 1, 0, 0)');
    for (const dx of [50, 110]) {
      await swipe(page, { target: '.settings-main', dx, finish: false });
      expect((await panel.boundingBox()).x).toBeCloseTo(dx, 1);
      await expect(page.locator('#feedColSettings')).toBeVisible();
      await expect(page.locator('#feedColSettings')).toHaveJSProperty('inert', true);
      expect(await page.evaluate(() => !!document.elementFromPoint(10, 200)?.closest('#detailPanelSettings'))).toBe(false);
      await page.screenshot({ path: `.ui-artifacts/v007-settings-swipe-${dx}.png` });
      const middle = await panel.evaluate(node => {
        node.dispatchEvent(new Event('touchend', { bubbles: true }));
        const animation = node.getAnimations().find(item => item.transitionProperty === 'transform');
        if (!animation) throw new Error('Missing settings transform transition');
        animation.pause();
        animation.currentTime = 100;
        const result = { duration: animation.effect.getTiming().duration, x: node.getBoundingClientRect().x, visible: getComputedStyle(node).visibility };
        for (const item of node.getAnimations()) item.finish();
        return result;
      });
      expect(middle.duration).toBe(200);
      expect(middle.visible).toBe('visible');
      if (dx < 72) {
        expect(middle.x).toBeGreaterThan(0);
        expect(middle.x).toBeLessThan(dx);
        await expect(panel).toHaveCSS('transform', 'matrix(1, 0, 0, 1, 0, 0)');
      } else {
        expect(middle.x).toBeGreaterThan(dx);
        expect(middle.x).toBeLessThan(360);
        await expect(panel).toBeHidden();
        await expect(page.locator('#feedColSettings')).toHaveJSProperty('inert', false);
      }
    }
  });

  test('reader follows the finger over the list, then rebounds or exits in 200ms with its Agent', async ({ page }) => {
    await openReader(page);
    const panel = page.locator('#detailPanelFeed');
    const dock = page.locator('#agentDock');
    const before = await dock.boundingBox();
    await expect(page.locator('#feedColFeed')).toHaveJSProperty('inert', true);
    await expect(page.locator('#feedColFeed')).toBeVisible();
    for (const dx of [50, 110]) {
      await swipe(page, { dx, finish: false });
      const rect = await panel.boundingBox(), agent = await dock.boundingBox();
      expect(rect.x).toBeCloseTo(dx, 1);
      expect(agent.x - before.x).toBeCloseTo(dx, 1);
      expect(agent.y).toBeCloseTo(before.y, 1);
      expect(await page.evaluate(() => !!document.elementFromPoint(10, 200)?.closest('#detailPanelFeed'))).toBe(false);
      await page.screenshot({ path: `.ui-artifacts/v007-original-swipe-${dx}.png` });
      const middle = await panel.evaluate(node => {
        node.dispatchEvent(new Event('touchend', { bubbles: true }));
        const animation = node.getAnimations().find(item => item.transitionProperty === 'transform');
        if (!animation) throw new Error('Missing transform transition');
        animation.pause();
        animation.currentTime = 100;
        const result = {
          duration: animation.effect.getTiming().duration,
          x: node.getBoundingClientRect().x,
          visible: getComputedStyle(node).visibility,
          agentY: document.querySelector('#agentDock').getBoundingClientRect().y,
        };
        for (const item of node.getAnimations()) item.finish();
        return result;
      });
      expect(middle.duration).toBe(200);
      expect(middle.visible).toBe('visible');
      expect(middle.agentY).toBeCloseTo(before.y, 1);
      if (dx < 72) {
        expect(middle.x).toBeGreaterThan(0);
        expect(middle.x).toBeLessThan(dx);
        await expect(panel).toHaveCSS('transform', 'matrix(1, 0, 0, 1, 0, 0)');
      } else {
        expect(middle.x).toBeGreaterThan(dx);
        expect(middle.x).toBeLessThan(360);
        await expect(panel).toBeHidden();
        await expect(page.locator('#feedColFeed')).toHaveJSProperty('inert', false);
      }
    }
  });

  test('Agent input stays above the bottom tabs in portrait, landscape and a short viewport', async ({ page }) => {
    await page.route('**/api/news/*/agent/session', route => route.fulfill({ json: {
      ok: true, context_available: true, session: { id: 'mobile-test', runtime: 'codex', model: 'test-model' }, messages: [], jobs: [],
    } }));
    await openReader(page);
    await page.locator('#agentDockToggleBtn').click();
    await expect(page.locator('#agentPromptInput')).toBeEnabled();
    await page.locator('#agentPromptInput').fill('定位检查');
    for (const [width, height] of [[360, 800], [390, 844], [812, 375], [360, 450]]) {
      await page.setViewportSize({ width, height });
      await expect.poll(async () => (await page.locator('#agentDock').boundingBox()).height).toBeCloseTo(Math.min(height * 0.78, height - 52 - 56), 0);
      const rects = await page.locator('#agentPromptInput').evaluate(node => {
        const input = node.getBoundingClientRect();
        const dock = document.querySelector('#agentDock').getBoundingClientRect();
        const tabs = document.querySelector('.mobile-bottom-nav').getBoundingClientRect();
        const header = document.querySelector('.topbar').getBoundingClientRect();
        return { inputBottom: input.bottom, dockBottom: dock.bottom, dockTop: dock.top, tabsTop: tabs.top, headerBottom: header.bottom,
          reachable: document.elementFromPoint(input.x + input.width / 2, input.y + input.height / 2) === node };
      });
      expect(rects.inputBottom).toBeLessThanOrEqual(rects.tabsTop);
      expect(rects.dockBottom).toBeCloseTo(rects.tabsTop, 1);
      expect(rects.dockTop).toBeGreaterThanOrEqual(rects.headerBottom);
      expect(rects.reachable).toBe(true);
      await page.screenshot({ path: `.ui-artifacts/v007-original-agent-${width}-${height}.png` });
    }
  });
});
