import { test, expect } from '@playwright/test';

test.beforeEach(async ({ request }) => {
  await request.post('/__test/reset-reading');
});

test('all-read sources remain selectable with no unread badges after reload', async ({ page, request }) => {
  await request.post('/api/news/mark-all-read', { data: { collection: 'feed' } });
  for (let visit = 0; visit < 2; visit++) {
    await page.goto('/');
    await expect(page.locator('#feedCardList')).toContainText('没有符合条件的新闻');
    for (const key of ['techcrunch', 'ars']) await expect(page.locator(`[data-source="${key}"]`)).toBeVisible();
    await expect(page.locator('#sourceFilters .nav-badge')).toHaveCount(0);
    await page.locator('#feedColFeed [data-filter="all"]').click();
    for (const [key, label] of [['techcrunch', 'TechCrunch'], ['ars', 'Ars Technica']]) {
      await page.locator(`[data-source="${key}"]`).click();
      await expect(page.locator('#feedCardList .news-card').first()).toBeVisible();
      await expect(page.locator('#feedCardList .meta-source-box > span:last-child').first()).toHaveText(label);
      expect((await page.locator('#feedCardList .meta-source-box > span:last-child').allTextContents()).every(text => text === label)).toBe(true);
      await expect(page.locator(`[data-source="${key}"] .nav-badge`)).toHaveCount(0);
    }
  }
});

test('unconfigured translation settings ignore stale default candidates and preserve a saved model', async ({ page, request }) => {
  const saved = await (await request.get('/api/settings')).json();
  saved.api_status.deepseek = { configured: false };
  saved.llm.translation.model = '';
  saved.model_catalogs.translation = { options: [
    { value: 'deepseek-v4-flash', source: 'fallback' },
    { value: 'deepseek-v4-pro', source: 'fallback' },
  ] };
  await page.route('**/api/settings', route => route.fulfill({ json: saved }));
  await page.goto('/');
  await page.locator('#leftNavSettingsBtn').click();
  await page.locator('[data-settings-tab="deepseek"]').click();
  const dropdown = page.locator('select[name="llm.translation.model"]');
  await expect(dropdown.locator('option')).toHaveCount(1);
  await expect(page.getByRole('combobox', { name: '翻译模型', exact: true })).toHaveText('请先配置 API 密钥');
  await expect(page.locator('#translationModelsBtn')).toBeHidden();
  await expect(page.locator('#translationTestBtn')).toBeDisabled();
  saved.llm.translation.model = 'my-saved-model';
  await page.reload();
  await page.locator('#leftNavSettingsBtn').click();
  await page.locator('[data-settings-tab="deepseek"]').click();
  await expect(dropdown).toHaveValue('my-saved-model');
  await expect(dropdown.locator('option')).toHaveCount(2);
  await expect(page.locator('input[name="llm.translation.model"]')).toHaveValue('my-saved-model');
});

test('reader displays a concrete Chinese fetch error without diagnosis', async ({ page }) => {
  await page.route('**/api/news/ui-064/detail', async route => {
    const data = await (await route.fetch()).json();
    data.detail_status = 'failed';
    data.job = { ...(data.job || {}), last_error: 'BROWSER_CONNECT: Browser Bridge extension not connected' };
    await route.fulfill({ json: data });
  });
  await page.goto('/');
  await expect(page.locator('#articleBodyContent')).toBeVisible();
  await expect(page.locator('#detailContent .inline-error')).toHaveText('浏览器连接失败。 浏览器桥接扩展未连接。');
  await page.locator('#agentDockToggleBtn').click();
  await expect(page.locator('.agent-suggestions, .suggestion-chip')).toHaveCount(0);
  await expect(page.locator('#agentPromptInput')).toBeVisible();
});

test.describe('touch selection toolbar', () => {
  test.use({ hasTouch: true, viewport: { width: 390, height: 844 } });
  test('stays above navigation, saves, dismisses and quotes at narrow widths', async ({ page, request }) => {
    const highlights = (await (await request.get('/api/news/ui-064/highlights')).json()).highlight_surfaces;
    for (const surface of Object.values(highlights)) for (const entry of surface.highlights) await request.delete(`/api/news/ui-064/highlights/${entry.id}`);
    await page.goto('/');
    await page.locator('#feedCardList .news-card-title').first().tap();
    const selectText = async () => {
      await page.locator('.digest-lead').evaluate(element => {
        const range = document.createRange(); range.selectNodeContents(element);
        getSelection().removeAllRanges(); getSelection().addRange(range);
      });
      await expect(page.locator('#selectionPopover')).toBeVisible();
    };
    await selectText();
    const toolbar = page.locator('#selectionPopover');
    const box = await toolbar.boundingBox(), nav = await page.locator('.mobile-bottom-nav').boundingBox();
    expect(box.y + box.height).toBeCloseTo(nav.y - 8, 0);
    expect(box.y).toBeGreaterThan((await page.locator('.digest-lead').boundingBox()).y);
    await page.locator('#highlightSelectionBtn').tap();
    await expect(page.locator('.digest-lead mark')).not.toHaveCount(0);
    await page.locator('.digest-lead mark').first().tap();
    await expect(page.locator('#highlightSelectionBtn')).toHaveText('取消高亮');
    await page.locator('#highlightSelectionBtn').tap();
    await expect(page.locator('.digest-lead mark')).toHaveCount(0);
    await selectText();
    await page.locator('#dismissSelectionBtn').tap();
    await expect(toolbar).toBeHidden();
    await selectText();
    await page.setViewportSize({ width: 320, height: 568 });
    const small = await toolbar.boundingBox(), smallNav = await page.locator('.mobile-bottom-nav').boundingBox();
    expect(small.x).toBeGreaterThanOrEqual(8);
    expect(small.x + small.width).toBeLessThanOrEqual(312);
    expect(small.y + small.height).toBeLessThanOrEqual(smallNav.y - 7);
    await page.screenshot({ path: '.ui-artifacts/v010-touch-selection.png' });
    await page.locator('#quoteNoteBtn').tap();
    await expect(page.locator('.draft-quote')).toContainText('引用精读结论');
    await expect(page.locator('#researchNoteInput')).toBeFocused();
  });
});
