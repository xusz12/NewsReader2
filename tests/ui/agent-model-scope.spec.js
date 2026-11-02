import {test, expect} from '@playwright/test';
import {answerConfirm, choosePicker} from './helpers.js';

async function openNews(page, id) {
  if (!await page.locator('#agentDock').evaluate(node => node.classList.contains('minimized')))
    await page.locator('#agentDockToggleBtn').click();
  if (await page.locator('#feedView').evaluate(node => node.classList.contains('mobile-reader-active')))
    await page.locator('#mobileReaderBackBtn').click();
  await page.locator(`#feedCardList [data-id="${id}"] .news-card-title`).click();
  await page.locator('#agentDockToggleBtn').click();
  await expect(page.locator('#agentSendBtn')).toBeEnabled();
}

test.beforeEach(async ({request}) => {
  await request.post('/__test/reset-reading');
  await request.post('/__test/reset-agent');
  const saved = await (await request.get('/api/settings')).json();
  await request.put('/api/settings', {data: {
    llm: {...saved.llm, pi_chat: {provider:'test', model:'test-model', reasoning_effort:'low'}},
    agent: {...saved.agent, runtime:'pi', codex:{...saved.agent.codex, model:'ui-catalog-b', reasoning_effort:'medium'}},
  }});
});

for (const width of [390, 1440]) for (const runtime of ['pi', 'codex'])
  test(`临时模型仅用于当前会话，新会话与其他新闻沿用默认 ${width} ${runtime}`, async ({page, request}) => {
    await page.setViewportSize({width, height:900});
    const original = await (await request.get('/api/settings')).json();
    const base = '/api/news/ui-000/agent';
    const model = runtime === 'pi' ? 'comparison-model' : 'ui-catalog-a';
    await page.goto('/');
    await page.locator('#feedColFeed [data-filter="all"]').click();
    await openNews(page, 'ui-000');
    await page.locator('#agentModelTrigger').click();
    await expect(page.locator('#agentModelTitle')).toHaveText('当前会话模型');
    if (runtime === 'codex') await choosePicker(page, 'Agent Runtime', 'Codex');
    await expect(page.getByRole('combobox', {name:'Agent 模型', exact:true})).toBeEnabled();
    await choosePicker(page, 'Agent 模型', runtime === 'pi' ? model + ' · test' : model);
    await expect(page.getByRole('combobox', {name:'Agent 推理程度', exact:true})).toBeEnabled();
    await choosePicker(page, 'Agent 推理程度', 'high');
    await expect(page.locator('#agentModelTrigger')).toHaveAttribute('title', `当前会话模型：${model} · high`);
    await page.locator('[data-agent-model-close]').click();
    await page.locator('#agentPromptInput').fill('当前新闻的临时模型问题');
    await page.locator('#agentSendBtn').click();
    await expect(page.locator('#agentChatStream .user')).toContainText('当前新闻的临时模型问题');
    const temporary = await (await request.get(base + '/session')).json();
    expect(temporary.jobs[0].model).toBe(model);
    await page.locator('#agentSendBtn').click();
    await expect(page.locator('#agentNewBtn')).toBeEnabled();

    await openNews(page, 'ui-001');
    await expect(page.locator('#agentModelTrigger')).toHaveAttribute('title', '当前会话模型：test-model · low');
    await page.locator('#agentModelTrigger').click();
    await expect(page.getByRole('combobox', {name:'Agent Runtime', exact:true})).toHaveText('Pi');
    await expect(page.getByRole('combobox', {name:'Agent 模型', exact:true})).toHaveText('test-model · test');
    await expect(page.getByRole('combobox', {name:'Agent 推理程度', exact:true})).toHaveText('low');
    await page.locator('[data-agent-model-close]').click();
    const other = (await (await request.get('/api/news/ui-001/agent/session')).json()).session;
    expect(other.model).toBe('test-model');
    expect(other.model_override).toBeFalsy();

    await openNews(page, 'ui-000');
    await expect(page.locator('#agentModelTrigger')).toHaveAttribute('title', `当前会话模型：${model} · high`);
    await expect(page.locator('#agentChatStream .user')).toContainText('当前新闻的临时模型问题');
    await page.locator('#agentNewBtn').click();
    await answerConfirm(page);
    await expect(page.locator('#agentModelTrigger')).toHaveAttribute('title', '当前会话模型：test-model · low');
    await expect(page.locator('#agentChatStream .user')).toHaveCount(0);
    const fresh = (await (await request.get(base + '/session')).json()).session;
    expect(fresh.id).not.toBe(temporary.session.id);
    expect(fresh.provider).toBe('pi');
    expect(fresh.model).toBe('test-model');
    expect(fresh.reasoning_effort).toBe('low');
    expect(fresh.model_override).toBeFalsy();
    const settings = await (await request.get('/api/settings')).json();
    expect(settings.agent).toEqual(original.agent);
    expect(settings.llm.pi_chat).toEqual(original.llm.pi_chat);
    expect(settings.feature_models).toEqual(original.feature_models);
  });
