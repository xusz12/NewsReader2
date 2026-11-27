import { openSettingsItem, choosePicker, chooseTheme } from './helpers.js';
import { test, expect } from '@playwright/test';

const path = '/api/news/ui-064';
async function ready(page) {
  await page.goto('/');
  await expect(page.locator('#articleBodyContent')).toBeVisible();
}
async function selection(page, selector) {
  const node = page.locator(selector);
  await node.scrollIntoViewIfNeeded();
  await node.evaluate(element => {
    const range = document.createRange(); range.selectNodeContents(element);
    const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
    element.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
  });
  await expect(page.locator('#selectionPopover')).toBeVisible();
}
test.beforeEach(async ({ request }) => {
  const settings = await (await request.get('/api/settings')).json();
  await request.put('/api/settings', { data: { llm: { ...settings.llm, pi_chat: { provider: 'test', model: 'test-model' } }, agent: { ...settings.agent, runtime: 'pi' } } });
  await request.post('/__test/reset-reading');
  await request.put(path + '/note', { data: { note: '' } });
  await request.delete(path + '/agent/session');
  const data = await (await request.get(path + '/highlights')).json();
  for (const surface of Object.values(data.highlight_surfaces)) {
    for (const entry of surface.highlights) await request.delete(path + `/highlights/${entry.id}`);
  }
});

test('persistent highlights in body, conclusion and points, including formatted Unicode and repeated text', async ({ page, request }) => {
  await ready(page);
  for (const [selector, kind] of [['#articleBodyContent > p:nth-child(2)', 'ai_zh'], ['.digest-lead', 'ai_conclusion_zh'], ['.digest-point-item:nth-child(2) > span:last-child', 'ai_points_zh']]) {
    await selection(page, selector);
    await page.getByRole('button', { name: '黄色高亮', exact: true }).click();
    await expect(page.locator(`${selector} mark`)).not.toHaveCount(0);
    const surface = (await (await request.get(path + '/highlights')).json()).highlight_surfaces[kind];
    expect(surface.highlights).toHaveLength(1);
    expect(surface.highlights[0].status).toBe('active');
  }
  await page.reload();
  await expect(page.locator('.digest-lead mark')).not.toHaveCount(0);
  await expect(page.locator('#articleBodyContent mark')).not.toHaveCount(0);
  await page.locator('[data-body-mode="bilingual"]').click();
  await selection(page, '#articleBodyContent [lang="en"] > p:nth-child(3)');
  await page.getByRole('button', { name: '黄色高亮', exact: true }).click();
  await expect(page.locator('#articleBodyContent [lang="en"] > p:nth-child(3) mark')).toContainText(['📌 ', '重复判断', ' 与 ', '引用', ' 和 ', 'code', '。']);
  await selection(page, '#articleBodyContent [lang="en"] > p:nth-child(4)');
  await page.getByRole('button', { name: '黄色高亮', exact: true }).click();
  const data = (await (await request.get(path + '/detail')).json());
  const raw = [...data.detail.content];
  const marks = data.highlight_surfaces.twitter_detail.highlights;
  expect(marks).toHaveLength(2);
  for (const mark of marks) expect(raw.slice(mark.start_offset, mark.end_offset).join('')).toBe(mark.selected_text);
  expect(marks[1].start_offset).toBeGreaterThan(marks[0].end_offset);
  await page.reload();
  await page.locator('[data-body-mode="bilingual"]').click();
  await expect(page.locator('#articleBodyContent [lang="en"] > p:nth-child(4) mark')).toHaveText('重复判断。');
  await page.locator('#articleBodyContent [lang="en"] > p:nth-child(4) mark').click();
  await page.getByRole('button', { name: '黄色高亮', exact: true }).click();
  await expect(page.locator('#articleBodyContent [lang="en"] > p:nth-child(4) mark')).toHaveCount(0);
});

test('restored highlights preserve article text when source edits change their resolved order', async ({ page }) => {
  const body = '先出现的判断与后出现的判断。';
  await page.route('**/api/news/ui-064/detail', async route => {
    const data = await (await route.fetch()).json();
    data.ai.body_zh = body;
    // Existing records retain their original order after the text has moved.
    data.highlight_surfaces.ai_zh = { body_ready: true, highlights: [
      { id: 901, status: 'active', resolved_start_offset: 7, resolved_end_offset: 13, color: 'yellow' },
      { id: 902, status: 'active', resolved_start_offset: 0, resolved_end_offset: 6, color: 'blue' },
    ] };
    await route.fulfill({ json: data });
  });
  await ready(page);
  await expect(page.locator('#articleBodyContent')).toHaveText(body);
  await expect(page.locator('#articleBodyContent mark')).toHaveText(['先出现的判断', '后出现的判断']);
});

test('digest text can be quoted to notes and Agent, with correct sources and no archive footer', async ({ page, request }) => {
  await ready(page);
  await selection(page, '.digest-lead');
  await page.locator('#quoteNoteBtn').click();
  await expect(page.locator('.draft-quote')).toContainText('引用精读结论');
  await page.locator('#researchNoteInput').fill('结论的补充判断');
  await page.locator('#publishResearchNoteBtn').click();
  await expect(page.locator('.saved-note .thought-quote')).toContainText('引用精读结论');
  await selection(page, '.digest-point-item:nth-child(2) > span:last-child');
  await page.locator('#quoteNoteBtn').click();
  await expect(page.locator('.draft-quote')).toContainText('引用精读要点第 2 条');
  await page.locator('#researchNoteInput').fill('要点的补充判断');
  await page.locator('#publishResearchNoteBtn').click();
  await expect(page.locator('.saved-note')).toHaveCount(2);
  const notes = (await (await request.get(path + '/detail')).json()).notes;
  expect(notes.map(note => note.quote_source)).toEqual(['conclusion', 'points']);
  await selection(page, '.digest-lead');
  await page.locator('#quoteAgentBtn').click();
  await expect(page.locator('#agentQuote')).toContainText('核心结论');
  await expect(page.locator('#agentPromptInput')).toBeEnabled();
  await expect(page.locator('#agentSendBtn')).toBeEnabled();
  expect((await (await request.get(path + '/agent/session')).json()).session).toBeTruthy();
  await expect(page.locator('.thought-origin')).toHaveCount(0);
});

test('empty thoughts are quiet and tweets without insights hide the notes box', async ({ page }) => {
  await ready(page);
  await expect(page.locator('.research-notes')).toHaveClass(/notes-empty/);
  await expect(page.locator('.research-notes h3, .thoughts-count')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '写想法', exact: true })).toBeVisible();
  await page.route('**/api/news/ui-064/detail', async route => {
    const data = await (await route.fetch()).json();
    data.ai = null; data.ai_status = 'none';
    await route.fulfill({ json: data });
  });
  await page.reload();
  await expect(page.locator('#articleBodyContent')).toBeVisible();
  await expect(page.locator('.notes-only')).toBeHidden();
  await page.locator('#writeArticleThoughtBtn').click();
  await expect(page.locator('#researchNoteInput')).toBeVisible();
  await page.getByRole('button', { name: '取消', exact: true }).click();
  await expect(page.locator('.notes-only')).toBeHidden();
  await expect(page.locator('#writeArticleThoughtBtn')).toBeFocused();
  await selection(page, '#articleBodyContent > p:first-child');
  await page.locator('#quoteNoteBtn').click();
  await page.locator('#researchNoteInput').fill('推文想法');
  await page.locator('#publishResearchNoteBtn').click();
  await expect(page.locator('.notes-only .saved-note')).toContainText('推文想法');
  await page.locator('.saved-note').hover();
  await page.getByRole('button', { name: '删除想法' }).click();
  await expect(page.locator('.notes-only')).toBeHidden();
});

test('expanding an untouched article prepares one session and reuses it on reopen', async ({ page, request }) => {
  await request.delete('/api/news/ui-063/agent/session');
  const posts = [];
  page.on('request', req => { if (req.url().endsWith('/agent/session') && req.method() === 'POST') posts.push(req); });
  await ready(page);
  expect(posts).toHaveLength(0);
  await page.locator('#agentDockToggleBtn').click();
  await expect(page.locator('#agentPromptInput')).toBeEnabled();
  await expect(page.locator('#agentSendBtn')).toBeEnabled();
  await expect.poll(() => posts.length).toBe(1);
  const first = (await (await request.get(path + '/agent/session')).json()).session.id;
  await page.locator('#agentDockToggleBtn').click();
  await page.locator('#agentDockToggleBtn').click();
  await expect(page.locator('#agentPromptInput')).toBeEnabled();
  await expect(page.locator('#agentSendBtn')).toBeEnabled();
  await expect.poll(() => posts.length).toBe(2);
  expect((await (await request.get(path + '/agent/session')).json()).session.id).toBe(first);
  await page.locator('[data-id="ui-063"] .news-card-title').click();
  await expect(page.locator('#agentDock')).toHaveClass(/minimized/);
  expect(posts).toHaveLength(2);
  await page.locator('[data-id="ui-063"] .news-card-title').click();
  await page.locator('#agentDockToggleBtn').click();
  await expect.poll(() => posts.length).toBe(3);
  expect(posts[2].url()).toContain('ui-063');
  expect(posts.every(req => !req.postDataJSON().new_session)).toBe(true);
});

for (const entry of ['toggle', 'quote']) {
  for (const runtime of ['pi', 'codex']) {
    test(`${entry} prepares an unused session with the saved ${runtime} runtime and model`, async ({ page, request }) => {
      const saved = await (await request.get('/api/settings')).json();
      const previous = await request.put('/api/settings', { data: {
        llm: { ...saved.llm, pi_chat: { provider: 'test', model: 'previous-model' } },
        agent: { ...saved.agent, runtime: runtime === 'pi' ? 'codex' : 'pi', codex: { ...saved.agent.codex, model: '', reasoning_effort: '' } },
      } });
      expect(previous.ok()).toBe(true);
      const original = (await (await request.post(path + '/agent/session', { data: {} })).json()).session;
      await ready(page);
      // Settings can change after article detail has already cached the empty session.
      const changed = await request.put('/api/settings', { data: {
        llm: { ...saved.llm, pi_chat: { provider: 'test', model: 'saved-pi-model' } },
        agent: { ...saved.agent, runtime, codex: { ...saved.agent.codex, model: 'ui-catalog-a', reasoning_effort: '' } },
      } });
      expect(changed.ok()).toBe(true);
      let release;
      const gate = new Promise(resolve => { release = resolve; });
      await page.route('**/api/news/ui-064/agent/session', async route => {
        if (route.request().method() === 'POST') await gate;
        await route.continue();
      });
      if (entry === 'quote') {
        await selection(page, '.digest-lead');
        await page.locator('#quoteAgentBtn').click();
        await expect(page.locator('#agentQuote')).toContainText('核心结论');
      } else await page.locator('#agentDockToggleBtn').click();
      await expect(page.locator('#agentStatus')).toHaveText('正在准备会话…');
      await expect(page.locator('#agentSendBtn')).toBeDisabled();
      await expect(page.locator('#agentPromptInput')).toBeEditable();
      await page.locator('#agentPromptInput').fill('解释这条判断');
      release();
      await expect(page.locator('#agentSendBtn')).toBeEnabled();
      await expect(page.locator('#agentPromptInput')).toHaveValue('解释这条判断');
      const selectedModel = runtime === 'codex' ? 'ui-catalog-a' : 'saved-pi-model';
      await expect(page.locator('#agentRuntimeBadge')).toContainText(selectedModel);
      const prepared = (await (await request.get(path + '/agent/session')).json()).session;
      expect(prepared.id).toBe(original.id);
      expect(prepared.provider).toBe(runtime);
      expect(prepared.model).toBe(selectedModel);
      const submitted = page.waitForResponse(r => r.url().endsWith('/agent/jobs') && r.request().method() === 'POST');
      await page.locator('#agentSendBtn').click();
      const response = await submitted;
      expect(response.status()).toBe(202);
      const data = await response.json();
      expect(data.job.provider).toBe(runtime);
      expect(data.job.model).toBe(selectedModel);
      expect(data.job.quote_text.length > 0).toBe(entry === 'quote');
    });
  }
}

test('saving settings updates the model of an already expanded unused Agent', async ({ page, request }) => {
  const saved = await (await request.get('/api/settings')).json();
  const configured = await request.put('/api/settings', { data: { llm: saved.llm, agent: { ...saved.agent, runtime: 'codex', codex: { ...saved.agent.codex, model: '', reasoning_effort: '' } } } });
  expect(configured.ok()).toBe(true);
  await ready(page);
  await page.locator('#agentDockToggleBtn').click();
  await expect(page.locator('#agentPromptInput')).toBeEnabled();
  await expect(page.locator('#agentSendBtn')).toBeEnabled();
  const original = (await (await request.get(path + '/agent/session')).json()).session.id;
  // Keyboard navigation leaves the Agent expanded while settings are saved.
  await page.locator('#leftNavSettingsBtn').press('Enter');
  await openSettingsItem(page, 'models');
  await page.locator('#refreshModelsBtn').click();
  await choosePicker(page,'模型','ui-catalog-b','chat');
  await page.locator('#settingsSaveBtn').click();
  await expect(page.locator('#agentRuntimeBadge')).toContainText('ui-catalog-b');
  await page.locator('#closeSettingsBtn').click();
  await expect(page.locator('#agentPromptInput')).toBeEnabled();
  const data = await (await request.get(path + '/agent/session')).json();
  expect(data.session.id).toBe(original);
  expect(data.session.model).toBe('ui-catalog-b');
  expect(data.messages).toEqual([]);
});

test('sidebar counts follow the collection and ignore late responses from the previous page', async ({ page, request }) => {
  await request.patch(path + '/state', { data: { read: true, read_later: true, favorite: true } });
  await ready(page);
  async function counts(collection, filter) {
    const sources = (await (await request.get(`/api/sources?collection=${collection}&read_filter=${filter}`)).json()).sources;
    const total = sources.reduce((sum, source) => sum + source.count, 0);
    for (const source of [...sources, { key: 'all', count: total }]) {
      const badge = page.locator(`[data-source="${source.key}"] .nav-badge`);
      if (source.count > 0) await expect(badge).toHaveText(String(source.count));
      else await expect(badge).toHaveCount(0);
    }
  }
  await counts('feed', 'unread');
  await page.locator('#navReadLaterBtn').click();
  await counts('read_later', 'unread');
  await page.locator('#navFavoritesBtn').click();
  await counts('favorites', 'all');
  const summary = (await (await request.get('/api/nav-summary')).json()).summary;
  await expect(page.locator('#starredNavBadge')).toHaveText(String(summary.favorites));
  await page.locator('#navImportantBtn').click();
  await expect(page.locator('#sourceFilters .nav-badge')).toHaveCount(0);
  await page.locator('#navNotesBtn').click();
  await expect(page.locator('#sourceFilters .nav-badge')).toHaveCount(0);
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const sourceRequest = (url, collection, filter) => {
    const parsed = new URL(url);
    return parsed.pathname === '/api/sources' && parsed.searchParams.get('collection') === collection && parsed.searchParams.get('read_filter') === filter;
  };
  const delayedSource = url => sourceRequest(url, 'read_later', 'unread');
  await page.route(delayedSource, async route => {
    await gate;
    await route.fulfill({ json: { ok: true, sources: [{ key: 'x', label: 'X', count: 9999 }] } });
  });
  try {
    await Promise.all([
      page.waitForRequest(req => delayedSource(req.url())),
      page.locator('#navReadLaterBtn').click(),
    ]);
    const [favorites] = await Promise.all([
      page.waitForResponse(res => sourceRequest(res.url(), 'favorites', 'all')),
      page.locator('#navFavoritesBtn').click(),
    ]);
    await favorites.finished();
    await counts('favorites', 'all');
    const delayed = page.waitForResponse(res => delayedSource(res.url()));
    release();
    await (await delayed).finished();
  } finally { release(); }
  await counts('favorites', 'all');
  await expect(page.locator('#sourceFilters')).not.toContainText('9999');
});

test('selection menu stays within a phone viewport and can save and quote a digest highlight', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  await page.goto('/');
  await page.locator('#feedCardList .news-card-title').first().click();
  await expect(page.locator('.digest-lead')).toBeVisible();
  await selection(page, '.digest-point-item:first-child > span:last-child');
  const box = await page.locator('#selectionPopover').boundingBox();
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(390);
  await page.screenshot({ path: '.ui-artifacts/reader-selection-phone.png' });
  await page.getByRole('button', { name: '黄色高亮', exact: true }).click();
  await expect(page.locator('.digest-point-item:first-child mark')).not.toHaveCount(0);
  await page.locator('.digest-point-item:first-child mark').first().focus();
  await page.keyboard.press('Enter');
  await page.locator('#quoteNoteBtn').click();
  await expect(page.locator('#researchNoteInput')).toBeFocused();
  await expect(page.locator('.draft-quote')).toContainText('引用精读要点第 1 条');
});

for (const width of [320, 335, 336, 390, 1440]) test(`个人助手引文与选区工具栏在各主题可用 ${width}`, async ({page,request}) => {
  await request.post('/__test/personal-assistant',{data:{reset:true}});
  await page.setViewportSize({width,height:900});
  await page.goto('/');
  await page.locator('[data-id="ui-064"] .news-card-title').click();
  const paragraph = '#articleBodyContent > p:nth-child(2)';
  for (const [theme,colorScheme] of [['light','light'],['dark','dark'],['system','light'],['system','dark'],['eink','light']]) {
    await page.emulateMedia({colorScheme}); await chooseTheme(page,theme);
    await selection(page,paragraph);
    const bar = page.locator('#selectionPopover');
    await expect(bar.getByRole('button')).toHaveCount(7);
    if (width < 921) {
      const box = await bar.boundingBox();
      expect(box.width).toBe(320); expect(box.height).toBe(54);
      expect(box.x).toBeCloseTo((width-320)/2, 1);
      const targets=await bar.getByRole('button').evaluateAll(buttons=>buttons.map(button=>[button.offsetWidth,button.offsetHeight]));
      expect(targets.every(([w,h])=>w>=44 && h>=44)).toBe(true);
    }
    await bar.getByRole('button',{name:'递给个人助手'}).click();
    await expect(page.locator('#personalAssistantQuote')).toContainText('引用译文');
    if(width<921) await expect(page.locator('#personalAssistantInput')).not.toBeFocused();
    else await expect(page.locator('#personalAssistantInput')).toBeFocused();
    await page.locator('#personalAssistantInput').fill('还在写的问题');
    await page.locator('#personalAssistantClose').click();
    await selection(page,'.digest-point-item:nth-child(2) > span:last-child');
    await bar.getByRole('button',{name:'递给个人助手'}).click();
    await expect(page.locator('#personalAssistantQuote')).toContainText('精读要点第 2 条');
    await expect(page.locator('#personalAssistantInput')).toHaveValue('还在写的问题');
    await page.getByRole('button',{name:'移除引文'}).click();
    await expect(page.locator('#personalAssistantQuote')).toBeHidden();
    await expect(page.locator('#personalAssistantInput')).toHaveValue('还在写的问题');
    await page.locator('#personalAssistantClose').click();
  }
  expect((await (await request.get('/api/personal-assistant/threads/main')).json()).thread).toBeNull();
  if(width===320) {
    await selection(page,paragraph);
    await page.locator('#selectionPopover').evaluate(el=>{el.style.setProperty('--selection-safe-left','10px');window.dispatchEvent(new Event('resize'));});
    const box=await page.locator('#selectionPopover').boundingBox();
    expect(box.x).toBeGreaterThanOrEqual(10);expect(box.height).toBeGreaterThan(54);
    expect(box.x+box.width).toBeLessThanOrEqual(320);
    await page.locator('#selectionPopover').evaluate(el => {
      el.style.removeProperty('--selection-safe-left');
      for (const button of el.querySelectorAll('button')) button.style.fontSize = '24px';
      window.dispatchEvent(new Event('resize'));
    });
    const groups = await page.locator('#selectionPopover .selection-action-group').evaluateAll(nodes =>
      nodes.map(group => [...group.querySelectorAll('button')].map(button => button.getBoundingClientRect().top)));
    expect(groups.every(row => new Set(row).size === 1)).toBe(true);
    expect(groups[1][0]).toBeGreaterThan(groups[0][0]);
  }
});

test('从跨段高亮中递引文，保留两段原话和起始段号', async ({page}) => {
  await page.goto('/');
  await page.locator('[data-id="ui-064"] .news-card-title').click();
  const body = page.locator('#articleBodyContent');
  const paragraphs = body.locator('p');
  const original = await paragraphs.evaluateAll(nodes => nodes.slice(0, 2).map(node => node.textContent).join('\n'));
  const firstParagraph = await paragraphs.first().getAttribute('data-quote-paragraph');
  await body.evaluate(element => {
    const paragraphs = element.querySelectorAll('p');
    const range = document.createRange();
    range.setStart(paragraphs[0], 0);
    range.setEnd(paragraphs[1], paragraphs[1].childNodes.length);
    const selection = getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    element.dispatchEvent(new MouseEvent('mouseup', {bubbles:true}));
  });
  await page.getByRole('button', {name:'黄色高亮',exact:true}).click();
  await paragraphs.nth(1).locator('mark').first().click();
  await page.getByRole('button', {name:'递给个人助手'}).click();
  const card = page.locator('#personalAssistantQuote');
  await expect(card).toContainText(`第 ${firstParagraph} 段`);
  expect(await card.locator('p').textContent()).toBe(original);
});

test('递引文断线重发复用请求号，换引文新建请求，生成中能准备下一轮', async({page,request})=>{
  await request.post('/__test/personal-assistant',{data:{reset:true}});
  await ready(page);
  await selection(page,'.digest-lead'); await page.locator('#quotePersonalBtn').click();
  const input=page.locator('#personalAssistantInput'), send=page.locator('#personalAssistantSend');
  const ids=[];
  await page.route('**/threads/main/messages',async route=>{
    ids.push(route.request().postDataJSON().request_id);
    if(ids.length===1) { await route.fetch(); await route.abort(); }
    else if(ids.length===3) await route.abort();
    else await route.continue();
  });
  await input.fill('这是什么意思？');await send.click();
  await expect(page.locator('#personalAssistantReload')).toBeVisible();
  await expect(input).toHaveValue('这是什么意思？');await expect(page.locator('#personalAssistantQuote')).toContainText('精读结论');
  await send.click();await expect(page.locator('#personalAssistantStatus')).toContainText('本轮已完成');
  expect(ids[1]).toBe(ids[0]);
  await page.locator('#personalAssistantClose').click();
  await selection(page,'.digest-lead');await page.locator('#quotePersonalBtn').click();
  await input.fill('这是什么意思？');await send.click();await expect(page.locator('#personalAssistantReload')).toBeVisible();
  await page.locator('#personalAssistantClose').click();
  await page.locator('[data-body-mode="bilingual"]').click();
  await selection(page,'#articleBodyContent [lang="en"] > p:nth-child(3)');await page.locator('#quotePersonalBtn').click();
  await request.post('/__test/personal-assistant',{data:{mode:'wait_empty'}});
  await send.click();await expect(send).toHaveAttribute('aria-label','停止回复');
  expect(ids[3]).not.toBe(ids[2]);
  await input.fill('下一轮草稿');await page.locator('#personalAssistantClose').click();
  await selection(page,'.digest-lead');await page.locator('#quotePersonalBtn').click();
  await expect(input).toHaveValue('下一轮草稿');await expect(page.locator('#personalAssistantQuote')).toContainText('精读结论');
  await send.click();await expect(page.locator('#personalAssistantStatus')).toContainText('已停止');
  const main=(await (await request.get('/api/personal-assistant/threads/main')).json());
  expect(main.messages).toHaveLength(4);
  expect(main.messages.filter(m=>m.role==='user').map(m=>m.quote.quote_source)).toEqual(['conclusion','original']);
});
