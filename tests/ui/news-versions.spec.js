import { test, expect } from '@playwright/test';
import { chooseTheme } from './helpers.js';

let ids;
test.beforeEach(async ({ request }) => {
  await request.post('/__test/reset-collection');
  const result = await request.post('/__test/news-versions');
  expect(result.ok()).toBeTruthy();
  ids = (await result.json()).ids;
});
test.afterEach(async ({ request }) => {
  await request.post('/__test/news-versions', { data: { clear: true } });
  await request.post('/__test/reset-collection');
});

async function start(page) {
  await page.goto('/');
  await page.locator('#feedColFeed [data-filter="all"]').click();
  if (await page.locator('#feedSearchToggleBtn').isVisible()) await page.locator('#feedSearchToggleBtn').click();
  await page.locator('#feedSearchInput').fill('版本验收');
  await expect(page.locator('#feedCardList .news-card')).toHaveCount(3);
}

test('updates remain unread, preserve old body and state, and link to each version', async ({ page, request }) => {
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  await start(page);
  const card = id => page.locator(`#feedCardList [data-id="${id}"]`);
  await expect(card(ids[0]).locator('[data-news-update]')).toHaveCount(0);
  await expect(card(ids[2]).locator('[data-news-update]')).toHaveText('更新');
  await expect(card(ids[2]).locator('.unread-dot')).toBeVisible();
  await card(ids[2]).click();
  await expect(page.locator('#articleMainTitle')).toHaveText('版本验收：最新进展');
  await expect(page.locator('#fetchArticleBtn')).toHaveText('抓取全文');
  await expect(page.locator('#articleBodyContent')).toHaveCount(0);
  await page.locator('[data-news-version="previous_version"]').click();
  await expect(page.locator('#articleMainTitle')).toHaveText('版本验收：第二次进展');
  await expect(page.getByText('此版本未保存历史正文，请查看最新版本。')).toBeVisible();
  await expect(page.locator('#fetchArticleBtn')).toHaveCount(0);
  await page.locator('[data-news-version="previous_version"]').click();
  await expect(page.locator('#articleMainTitle')).toHaveText('版本验收：原稿');
  await expect(page.locator('#articleBodyContent')).toHaveText('原稿的已缓存正文。');
  await expect(page.locator('#detailContent [data-news-update]')).toHaveCount(0);
  const original = await (await request.get(`/api/news/${ids[0]}/detail`)).json();
  expect(original.read_at).toBe('original-read'); expect(original.favorite_at).toBe('original-favorite');
  await page.locator('[data-news-version="latest_version"]').click();
  await expect(page.locator('#articleMainTitle')).toHaveText('版本验收：最新进展');
  expect(errors).toEqual([]);
});

for (const mobile of [false, true]) {
  for (const [theme, scheme] of [['light','light'],['dark','dark'],['system','light'],['system','dark'],['eink','light']]) {
    test(`version badges and reader controls ${mobile?'mobile':'desktop'} ${theme} ${scheme}`, async ({ page }) => {
      await page.setViewportSize(mobile ? {width:390,height:844} : {width:1440,height:900});
      await page.emulateMedia({colorScheme:scheme});
      await start(page);
      await chooseTheme(page,theme);
      for (const size of ['small','standard','large','extra-large']) {
        const option = await page.locator(`#fontSizeSelect-native option[value="${size}"]`).textContent();
        await page.getByRole('combobox',{name:'阅读字号',exact:true}).click();
        await page.getByRole('option',{name:option,exact:true}).click();
        const badge = page.locator(`#feedCardList [data-id="${ids[2]}"] [data-news-update]`);
        await expect(badge).toBeVisible();
        expect(await badge.evaluate(n=>n.scrollWidth <= n.clientWidth+1)).toBeTruthy();
      }
      await page.locator(`#feedCardList [data-id="${ids[2]}"]`).click();
      await expect(page.locator('#detailContent [data-news-update]')).toBeVisible();
      await expect(page.locator('[data-news-version="previous_version"]')).toBeVisible();
      expect(await page.locator('#detailContent').evaluate(n=>n.scrollWidth<=n.clientWidth+1)).toBeTruthy();
      await page.locator('[data-news-version="previous_version"]').click();
      await expect(page.getByText('此版本未保存历史正文，请查看最新版本。')).toBeVisible();
      await page.screenshot({path:`.ui-artifacts/news-versions-${mobile?'mobile':'desktop'}-${theme}-${scheme}.png`});
    });
  }
}

for (const width of [1024,1280]) test(`cached update metadata wraps within the shared reader at ${width}px`, async ({page,request}) => {
  const data = await (await request.post('/__test/news-versions',{data:{cache_middle:true}})).json();
  ids = data.ids;
  await page.setViewportSize({width,height:900});
  await start(page);
  await page.locator(`#feedCardList [data-id="${ids[1]}"]`).click();
  await expect(page.locator('#articleBodyContent')).toHaveText('Saved middle version.');
  await expect(page.locator('[data-news-version="previous_version"]')).toBeVisible();
  await expect(page.locator('[data-news-version="latest_version"]')).toBeVisible();
  expect(await page.locator('#detailContent').evaluate(n=>n.scrollWidth<=n.clientWidth+1)).toBeTruthy();
});
