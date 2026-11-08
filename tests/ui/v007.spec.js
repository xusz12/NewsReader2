import {test,expect} from '@playwright/test';

test('opening the page reads the library without collecting', async({page})=>{
  const actions=[];
  page.on('request',request=>{if(request.method()==='POST' && /\/api\/(collection|reindex)$/.test(request.url())) actions.push(request.url());});
  await page.goto('/');
  await expect(page.locator('#feedCardList .news-card').first()).toBeVisible();
  expect(actions).toEqual([]);
});

test('collection request failure leaves existing news and scroll intact',async({page})=>{
  await page.goto('/');
  await expect(page.locator('#feedCardList .news-card').first()).toBeVisible();
  await page.evaluate(()=>{window.row=document.querySelector('#feedCardList .news-card');});
  await page.route('**/api/collection',route=>route.request().method()==='POST' ? route.fulfill({status:503,json:{error:'采集暂不可用'}}) : route.continue());
  await page.locator('#collectNewsBtn').click();
  await expect(page.locator('#toast')).toContainText('采集暂不可用');
  expect(await page.evaluate(()=>window.row===document.querySelector('#feedCardList .news-card'))).toBe(true);
});
