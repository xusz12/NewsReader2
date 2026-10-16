import { test, expect } from '@playwright/test';

test.beforeEach(async ({ request }) => { await request.post('/__test/reset-reading'); });
async function start(page) {
  await page.goto('/');
  await expect(page.locator('#feedCardList .news-card').first()).toBeVisible();
}

test('default unread ascending, collection filters and scrolling dates', async ({ page }) => {
  await start(page);
  await expect(page.locator('#feedColFeed [data-filter="unread"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#sortNewsBtn')).toHaveAttribute('aria-label', '排序：旧到新，点击切换');
  await expect(page.locator('#feedColFeed [data-filter]')).toHaveCount(2);
  await expect(page.locator('#feedCardList .news-card').first()).toHaveAttribute('data-id', 'ui-064');
  await page.locator('#feedCardList').evaluate(node => node.scrollTop = 180);
  const top = await page.locator('#feedCardList').evaluate(node => node.getBoundingClientRect().top);
  expect((await page.locator('.feed-date-heading').first().boundingBox()).y).toBeLessThan(top);
  const row = await page.locator('.feed-filter-row').boundingBox();
  for (const control of await page.locator('.feed-filter-row button').all()) { const box = await control.boundingBox(); expect(box.y).toBeCloseTo(row.y, 0); }
  await page.locator('#navImportantBtn').click();
  await expect(page.locator('#feedColFeed [data-filter]')).toHaveCount(2);
  await expect(page.locator('#feedColFeed [data-filter="notes"]')).toBeVisible();
  await expect(page.locator('#markAllReadBtn')).toBeHidden();
  await page.locator('#navFavoritesBtn').click();
  await expect(page.locator('#feedColFeed [data-filter]')).toHaveCount(1);
  await expect(page.locator('#sortNewsBtn')).toBeVisible();
  await expect(page.locator('#btnToggleRead, #btnToggleReadLater, #batchActionBar, .nav-settings-shortcut')).toHaveCount(0);
});

test('scroll marks only cards fully above toolbar and keeps unread rows stable', async ({ page, request }) => {
  await start(page);
  const first = page.locator('#feedCardList .news-card').first();
  const id = await first.getAttribute('data-id');
  await expect(first).not.toHaveClass(/\bread\b/);
  await page.locator('#feedCardList').evaluate(node => node.scrollTop = 80);
  expect((await (await request.get(`/api/news/${id}/detail`)).json()).read_at).toBeNull();
  const height = await first.evaluate(node => node.offsetTop + node.offsetHeight);
  await page.locator('#feedCardList').evaluate((node, y) => node.scrollTop = y + 5, height);
  await expect.poll(async () => (await (await request.get(`/api/news/${id}/detail`)).json()).read_at).toBeTruthy();
  await expect(page.locator(`[data-id="${id}"]`)).toHaveCount(1);
  // Off-screen cards not yet passed through the viewport stay unread.
  expect((await (await request.get('/api/news/ui-000/detail')).json()).read_at).toBeNull();
});

test('end-of-list delay marks only current search range', async ({ page, request }) => {
  await start(page);
  await page.locator('#feedSearchInput').fill('AgentUnmatched');
  await expect(page.locator('#feedCardList')).toContainText('没有符合条件');
  await page.locator('#feedSearchInput').fill('台积电');
  await expect(page.locator('.feed-end')).toHaveCount(1);
  const ids = await page.locator('#feedCardList .news-card').evaluateAll(nodes => nodes.map(n => n.dataset.id));
  await page.locator('#feedCardList').evaluate(node => node.scrollTop = node.scrollHeight);
  const lastId = ids.at(-1);
  expect((await (await request.get(`/api/news/${lastId}/detail`)).json()).read_at).toBeNull();
  await expect.poll(async () => (await (await request.get(`/api/news/${lastId}/detail`)).json()).read_at, { timeout: 8000 }).toBeTruthy();
  expect((await (await request.get('/api/news/ui-000/detail')).json()).read_at).toBeNull();
});

test('all-read respects source and read-later completion is independent', async ({ page, request }) => {
  await start(page);
  await page.locator('[data-source="reuters"]').click();
  const ids = (await (await request.get('/api/news?source_filter=reuters&per=100')).json()).items.map(i => i.id);
  await page.locator('#markAllReadBtn').click();
  await expect.poll(async () => (await (await request.get(`/api/news/${ids.at(-1)}/detail`)).json()).read_at).toBeTruthy();
  await request.post('/__test/reset-reading');
  await request.patch('/api/news/ui-000/state', { data: { read_later: true, read: false } });
  await page.locator('#navReadLaterBtn').click();
  await page.locator('[data-source="all"]').click();
  await expect(page.locator('#markAllReadBtn')).toHaveAttribute('aria-label', '全部看完');
  await page.locator('#markAllReadBtn').click();
  await expect.poll(async () => (await (await request.get('/api/news/ui-000/detail')).json()).read_later_at).toBeNull();
  const detail = await (await request.get('/api/news/ui-000/detail')).json();
  expect(detail.read_later_done_at).toBeTruthy();
  expect(detail.read_at).toBeNull();
});

for (const width of [1440, 390]) {
  test(`unfetched articles omit empty notes and body placeholders at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let fetched = false;
    await page.route('**/api/news/*/detail', async route => {
      const data = await (await route.fetch()).json();
      if (!fetched) Object.assign(data, { detail: null, ai: null, detail_status: 'missing', ai_status: 'missing', job: null, ai_job: null });
      await route.fulfill({ json: data });
    });
    await page.route('**/api/news/*/detail/retry', async route => {
      fetched = true;
      await route.fulfill({ json: { ok: true } });
    });
    await start(page);
    if (width < 768) await page.locator('#feedCardList .news-card-title').first().click();
    await expect(page.locator('#detailPanelFeed .cache-label')).toHaveText('尚未抓取正文');
    await expect(page.locator('#articleMainTitle')).toBeVisible();
    await expect(page.locator('#fetchArticleBtn')).toBeEnabled();
    await expect(page.locator('#articleSourceLink')).toBeVisible();
    await expect(page.locator('.digest-box, #articleBodyContent, #articleNote, #saveArticleNoteBtn')).toHaveCount(0);
    await expect(page.locator('#detailScrollArea')).not.toContainText('暂无正文');
    await expect(page.locator('#detailScrollArea')).not.toContainText('阅读笔记');
    await page.screenshot({ path: `.ui-artifacts/reader-unfetched-${width}.png`, animations: 'disabled' });
    await page.locator('#fetchArticleBtn').click();
    await expect(page.locator('#articleBodyContent')).not.toBeEmpty();
    await expect(page.locator('.digest-box')).toContainText('DeepSeek 结构化精读');
    await page.locator('[data-body-mode="original"]').click();
    await expect(page.locator('#articleBodyContent')).not.toBeEmpty();
    await expect(page.locator('#articleNote, #saveArticleNoteBtn')).toHaveCount(0);
    await expect(page.locator('#researchNoteComposer')).toBeHidden();
    expect(errors).toEqual([]);
  });
}

test('thoughts are integrated into the digest with counts and deletion', async ({ page, request }) => {
  await request.put('/api/news/ui-064/note', { data: { note: '原有评论' } });
  await request.post('/api/news/ui-064/notes', { data: { note: '新评论 <script>不会执行</script>' } });
  await start(page);
  await expect(page.locator('.digest-box .digest-thoughts .saved-note')).toHaveCount(2);
  await expect(page.locator('[data-id="ui-064"] .note-badge')).toHaveText('2');
  await page.screenshot({ path: '.ui-artifacts/v002-comments.png', animations: 'disabled' });
  await expect(page.locator('#articleNote, #saveArticleNoteBtn')).toHaveCount(0);
  await page.locator('.saved-note').first().hover();
  await page.locator('.saved-note').first().getByRole('button', { name: '删除' }).click();
  await expect(page.locator('.saved-note')).toHaveCount(1);
  await expect(page.locator('.saved-note')).toContainText('新评论');
  await expect(page.locator('[data-id="ui-064"] .note-badge')).toHaveText('1');
  await page.reload();
  await expect(page.locator('.saved-note')).toHaveCount(1);
});

test('pipeline distinguishes translation while original remains available', async ({ page }) => {
  await page.route('**/api/news/*/detail', async route => {
    const response = await route.fetch();
    const data = await response.json();
    data.detail_status = 'success'; data.ai_status = 'running'; data.ai = null;
    await route.fulfill({ json: data });
  });
  await start(page);
  await expect(page.locator('#detailPanelFeed .cache-label')).toContainText('正文翻译中');
  await expect(page.locator('#detailPanelFeed .cache-label')).not.toContainText('opencli');
  await expect(page.locator('#articleBodyContent')).not.toBeEmpty();
});

test('Agent UI submits ordinary and quoted questions to real backend', async ({ page, request }) => {
  const saved = await (await request.get('/api/settings')).json();
  await request.put('/api/settings', { data: { llm: { ...saved.llm, pi_chat: { provider: 'test', model: 'test-model' } }, agent: { ...saved.agent, runtime: 'pi' } } });
  await request.delete('/api/news/ui-064/agent/session');
  await start(page);
  await page.locator('#agentDockToggleBtn').click();
  await expect(page.locator('#agentHeader #agentNewBtn')).toBeVisible();
  await page.locator('#agentPromptInput').fill('解释背景');
  const response = page.waitForResponse(r => r.url().endsWith('/agent/jobs') && r.request().method() === 'POST');
  await page.locator('#agentSendBtn').click();
  expect((await response).status()).toBe(202);
  await expect(page.getByRole('button', { name: '停止研究' })).toBeVisible();
  await page.getByRole('button', { name: '停止研究' }).click();
  await expect(page.locator('#agentPromptInput')).toBeEnabled();
  await page.locator('#articleMainTitle').click();
  await expect(page.locator('#agentDock')).toHaveClass(/minimized/);
  await page.locator('#articleBodyContent').evaluate(node => {
    const range = document.createRange(); range.selectNodeContents(node);
    const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
    node.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
  });
  await page.locator('#quoteAgentBtn').click();
  await page.locator('#agentPromptInput').fill('解释引用');
  const quoted = page.waitForResponse(r => r.url().endsWith('/agent/jobs') && r.request().method() === 'POST');
  await page.locator('#agentSendBtn').click();
  const result = await quoted;
  expect(result.status()).toBe(202);
  expect(result.request().postDataJSON().quote_source).toBe('selection');
  await request.delete('/api/news/ui-064/agent/session');
});

test('key update/cancel, model discovery and custom selection preserve settings draft', async ({ page, request }) => {
  const saved = await (await request.get('/api/settings')).json();
  saved.api_status.deepseek = { configured: true, masked_key: 'sk-f••••••••1234' };
  saved.llm.translation.model = 'known';
  saved.model_catalogs.translation = { options: [{ value: 'known', source: 'official' }] };
  await page.route('**/api/settings', route => route.request().method() === 'GET' ? route.fulfill({ json: saved }) : route.continue());
  await page.route('**/api/settings/secrets/deepseek', route => route.fulfill({ json: { ...saved, api_status: { ...saved.api_status, deepseek: { configured: route.request().method() !== 'DELETE', masked_key: 'sk-n••••••••5678' } } } }));
  await page.route('**/api/settings/translation/models', route => route.fulfill({ json: { ok: true, catalog: { options: [{ value: 'known', source: 'official' }, { value: 'new-model', source: 'official' }] } } }));
  await page.route('**/api/settings/translation/test', route => route.fulfill({ json: { ok: true, message: '模型连接成功', model: route.request().postDataJSON().model, elapsed_ms: 15 } }));
  await start(page);
  await page.locator('#leftNavSettingsBtn').click();
  await page.locator('[data-settings-tab="deepseek"]').click();
  await expect(page.locator('#deepseekSecret')).toHaveCount(0);
  await expect(page.locator('.masked-secret')).toHaveText('sk-f••••••••1234');
  const custom = page.locator('input[name="llm.translation.model"]');
  const dropdown = page.locator('select[name="llm.translation.model"]');
  await expect(custom).toHaveValue('');
  await custom.fill('my-model');
  await expect(dropdown).toHaveValue('my-model');
  await page.locator('#editSecretBtn').click();
  await page.locator('#deepseekSecret').fill('draft-key');
  await page.locator('#cancelSecretBtn').click();
  await expect(page.locator('#deepseekSecret')).toHaveCount(0);
  await page.locator('#editSecretBtn').click();
  await page.locator('#deepseekSecret').fill('new-key');
  await page.locator('#saveSecretBtn').click();
  await expect(page.locator('.masked-secret')).toHaveText('sk-n••••••••5678');
  await expect(custom).toHaveValue('my-model');
  await page.locator('#translationModelsBtn').click();
  await page.getByRole('combobox', { name: '翻译模型', exact: true }).click();
  await page.getByRole('option', { name: 'new-model', exact: true }).click();
  await expect(custom).toHaveValue('');
  await page.locator('#translationTestBtn').click();
  await expect(page.locator('#translationTestTerminal')).toContainText('模型连接成功 · new-model');
  await page.screenshot({ path: '.ui-artifacts/v002-translation-settings.png', animations: 'disabled' });
  page.once('dialog', dialog => dialog.accept());
  await page.locator('#deleteSecretBtn').click();
  await expect(page.locator('#deepseekSecret')).toBeVisible();
  await expect(dropdown).toHaveValue('new-model');
});


test('source badges count unread news and update after marking read', async ({ page, request }) => {
  await start(page);
  const sources = (await (await request.get('/api/sources?collection=feed&read_filter=unread')).json()).sources;
  for (const source of sources) await expect(page.locator(`[data-source="${source.key}"] .nav-badge`)).toHaveText(String(source.count));
  await page.locator('[data-source="reuters"]').click();
  await page.locator('#markAllReadBtn').click();
  await expect(page.locator('[data-source="reuters"] .nav-badge')).toHaveText('0');
});
