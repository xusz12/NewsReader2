import { test, expect } from '@playwright/test';
import { openSettings } from './helpers.js';

test.beforeEach(async ({request}) => { await request.post('/__test/reset-collection'); });
test.afterEach(async ({request}) => { await request.post('/__test/reset-collection'); });

async function start(page) {
  await page.goto('/');
  await expect(page.locator('#feedCardList .news-card').first()).toBeVisible();
  await expect(page.locator('#pipelineStatus')).not.toContainText('正在连接');
}

test('collection only queues work and arrivals preserve rows, scroll and pagination', async ({page,request}) => {
  const errors=[]; page.on('pageerror', error => errors.push(error.message));
  await start(page);
  await page.evaluate(() => {window.firstCard=document.querySelector('#feedCardList .news-card');});
  await openSettings(page);
  await page.locator('[data-settings-tab="pipeline"]').click();
  await page.locator('#settingsCollectBtn').click();
  await expect(page.locator('#settingsCollectBtn')).toBeDisabled();
  await page.keyboard.press('Escape');
  await expect(page.locator('#pipelineStatus')).toContainText('正在采集', {timeout:12000});
  await request.post('/__test/new-news',{data:{published_at:'2026-10-01 01:00:00'}});
  await expect(page.locator('#newNewsNotice')).toBeVisible({timeout:12000});
  await expect(page.locator('#newNewsCount')).toHaveText('有 1 条新新闻');
  expect(await page.evaluate(() => window.firstCard === document.querySelector('#feedCardList .news-card'))).toBe(true);
  expect(await page.locator('#feedCardList').evaluate(node=>node.scrollTop)).toBe(0);
  await expect(page.locator('[data-id="ui-late-arrival"]')).toHaveCount(0);
  for(let i=0;i<3;i++) {
    await page.locator('#feedCardList').evaluate(node=>{node.scrollTop=node.scrollHeight;});
    await page.waitForTimeout(400);
  }
  await expect(page.locator('[data-id="ui-late-arrival"]')).toHaveCount(0);
  await page.locator('#viewNewNewsBtn').click();
  await expect(page.locator('#feedCardList .news-card').first()).toHaveAttribute('data-id','ui-late-arrival');
  expect(errors).toEqual([]);
});

test('five second end timer cannot mark a news item published during countdown', async ({page,request}) => {
  await start(page);
  for(let i=0;i<3;i++) {
    await page.locator('#feedCardList').evaluate(node=>{node.scrollTop=node.scrollHeight;});
    await page.waitForTimeout(400);
  }
  await expect(page.locator('.feed-end')).toBeInViewport();
  await request.post('/__test/new-news');
  await expect.poll(async()=> (await (await request.get('/api/news/ui-064/detail')).json()).read_at, {timeout:10000}).toBeTruthy();
  const late = await (await request.get('/api/news/ui-late-arrival/detail')).json();
  expect(late.read_at).toBeNull();
  await expect(page.locator('#newNewsNotice')).toBeVisible();
});

test('bulk confirmation preserves its original set when news arrives', async ({page,request}) => {
  await start(page);
  await page.locator('#markAllReadBtn').click();
  await expect(page.locator('#appConfirmDialog')).toBeVisible();
  await request.post('/__test/new-news');
  await page.locator('#appConfirmDialog').getByRole('button',{name:'全部标为已读',exact:true}).click();
  await expect.poll(async()=> (await (await request.get('/api/news?read_filter=unread&count_only=1')).json()).total).toBe(1);
  expect((await (await request.get('/api/news/ui-late-arrival/detail')).json()).read_at).toBeNull();
});

test('translation retry stays visible until publication and never requests acquisition', async ({page}) => {
  let counts={pending:0,running:0,failed:2,done:0}, acquisitions=0;
  await page.route('**/api/collection',async route=>{
    if(route.request().method()==='POST') acquisitions++;
    const response=await route.fetch(), data=await response.json();
    await route.fulfill({json:{...data,counts}});
  });
  await page.route('**/api/collection/retry-translation',route=>{
    counts={pending:2,running:0,failed:0,done:0};
    return route.fulfill({status:202,json:{ok:true,retried:2}});
  });
  await start(page);
  await page.locator('#retryCollectionTranslationBtn').click();
  await expect(page.locator('#collectionTranslationNotice')).toBeVisible();
  await expect(page.locator('#collectionTranslationText')).toContainText('2 条新闻正在翻译');
  counts={pending:0,running:0,failed:0,ready:2,done:0};
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  await expect(page.locator('#collectionTranslationNotice')).toBeVisible();
  await expect(page.locator('#collectionTranslationText')).toContainText('2 条新闻已完成翻译');
  await expect(page.locator('#retryCollectionTranslationBtn')).toBeHidden();
  counts={pending:0,running:0,failed:0,done:2};
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  await expect(page.locator('#collectionTranslationNotice')).toBeHidden();
  expect(acquisitions).toBe(0);
});

test('collection schedule validates and saves without changing report time',async({page,request})=>{
  const original=await (await request.get('/api/settings')).json();
  try {
    await start(page);
    await openSettings(page);
    await page.locator('[data-settings-tab="pipeline"]').click();
    await page.locator('[name="collection.daily"]').fill('24:00');
    await page.locator('#settingsSaveBtn').click();
    await expect(page.locator('#toast')).toContainText('HH:MM');
    await page.locator('[name="collection.daily"]').fill('19:00、07:00、07:00');
    await page.locator('[name="collection.translation_concurrency"]').fill('33');
    await page.locator('#settingsSaveBtn').click();
    await expect(page.locator('#settingsStatus')).toContainText('1–32');
    await page.locator('[name="collection.translation_concurrency"]').fill('3');
    await page.locator('[name="collection.enabled"]').uncheck();
    await page.locator('#settingsSaveBtn').click();
    await expect(page.locator('#settingsSaveBtn')).toBeHidden();
    const saved=await (await request.get('/api/settings')).json();
    expect(saved.collection.daily).toEqual(['07:00','19:00']);
    expect(saved.collection.enabled).toBe(false);
    expect(saved.collection.translation_concurrency).toBe(3);
    expect(saved.report_schedule).toEqual(original.report_schedule);
  } finally {
    await request.put('/api/settings',{data:{llm:original.llm,collection:original.collection}});
  }
});

for (const width of [1440,390]) for(const theme of ['light','dark','system','eink']) {
  test.describe(`appearance ${width} ${theme}`, () => {
  test.use({hasTouch:width === 390});
  test(`collection settings and translation notice ${width} ${theme}`, async ({page}) => {
    await page.setViewportSize({width,height:850});
    await page.route('**/api/collection', async route => {
      if(route.request().method()!=='GET') return route.continue();
      const result=await route.fetch(), data=await result.json();
      data.counts.failed=2;
      await route.fulfill({json:data});
    });
    await start(page);
    await page.evaluate(theme=>{document.documentElement.dataset.theme=theme;},theme);
    await expect(page.locator('#collectionTranslationNotice')).toContainText('2 条新闻');
    await page.screenshot({path:`.ui-artifacts/collection-notice-${width}-${theme}.png`});
    await openSettings(page);
    await page.locator('[data-settings-tab="pipeline"]').click();
    await expect(page.locator('[name="collection.daily"]')).toHaveValue('00:01、04:00、08:00、12:00、18:00、21:00');
    await expect(page.locator('#settingsCollectionSources li')).toHaveCount(12);
    await expect(page.locator('[name="collection.translation_concurrency"]')).toHaveValue('4');
    await page.locator('[name="collection.translation_concurrency"]').fill('6');
    await page.locator('[name="collection.daily"]').fill('07:00、19:00');
    await expect(page.locator('#settingsSaveBtn')).toBeVisible();
    await page.screenshot({path:`.ui-artifacts/collection-${width}-${theme}.png`});
    expect(await page.locator('.settings-main').evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true);
  });
  });
}
