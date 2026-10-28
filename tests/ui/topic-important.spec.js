import { test, expect } from '@playwright/test';
import { chooseTheme, openNavigation } from './helpers.js';

const buttonFor = page => page.locator('[data-topic-important="ui-001"]');
const detail = async request => (await request.get('/api/news/ui-001/detail')).json();
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
};
async function openTopic(page) {
  await page.goto('/');
  await openNavigation(page);
  await page.locator('#navTopicsBtn').click();
  await page.locator('[data-topic-id="test-acquisition"]').click();
  await expect(buttonFor(page)).toBeAttached();
}
async function expectPlain(button) {
  await expect(button).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
  await expect(button).toHaveCSS('border-top-width', '0px');
  await expect(button).toHaveCSS('box-shadow', 'none');
}
test.beforeEach(async ({ request }) => {
  await request.post('/__test/topics');
  await request.post('/__test/reset-reading');
  await request.patch('/api/news/ui-001/state', {data:{important:false, read_later:false, read_later_done:true}});
});

test('时间线重要与新闻流、其他主题和阅读器共享，点击保持位置与阅读状态', async ({page,request}) => {
  await openTopic(page);
  const button=buttonFor(page), card=button.locator('xpath=ancestor::article');
  await card.hover();
  const before=await detail(request);
  const scroll=await page.locator('#topicScrollArea').evaluate(n=>n.scrollTop);
  await button.evaluate(n=>{ window.importantButton=n; });
  await button.click();
  await expect(button).toHaveAttribute('aria-pressed','true');
  await expect(button).toHaveAttribute('aria-busy','false');
  await expect(button).toBeFocused();
  expect(await button.evaluate(n=>n===window.importantButton)).toBe(true);
  expect(await page.locator('#topicScrollArea').evaluate(n=>n.scrollTop)).toBe(scroll);
  await expect(page.locator('#topicContent')).toBeVisible();
  const after=await detail(request);
  expect(after.important_at).toBeTruthy();
  for(const key of ['read_at','read_later_at','read_later_done_at','detail_status']) expect(after[key]).toBe(before[key]);
  await page.locator('[data-topic-id="test-industry"]').click();
  await expect(button).toHaveAttribute('aria-pressed','true');
  await page.locator('[data-topic-item="ui-001"]').click();
  await expect(page.locator('#detailPanelFeed .detail-toolbar [data-state="important"]')).toHaveAttribute('aria-pressed','true');
  await page.locator('#topicReaderBackBtn').click();
  await openNavigation(page);
  await page.locator('#navImportantBtn').click();
  const feedButton=page.locator('#feedCardList [data-id="ui-001"] [data-state="important"]');
  await expect(feedButton).toHaveAttribute('aria-pressed','true');
  await feedButton.click();
  await expect(feedButton).toHaveAttribute('aria-busy','false');
  expect((await detail(request)).important_at).toBeNull();
  await openTopic(page);
  await expect(button).toHaveAttribute('aria-pressed','false');
});

test('重要保存失败恢复原状态，按钮与正文入口独立', async ({page,request}) => {
  await openTopic(page);
  await page.route('**/api/news/ui-001/state',route=>route.fulfill({status:500,json:{error:'save_failed'}}));
  const button=buttonFor(page);
  await button.focus();await page.keyboard.press('Enter');
  await expect(button).toHaveAttribute('aria-busy','false');
  await expect(button).toHaveAttribute('aria-pressed','false');
  await expect(button).toBeFocused();
  await expect(page.locator('#topicContent')).toBeVisible();
  expect((await detail(request)).important_at).toBeNull();
});

test('重要快速连点保留最后意图且不阻塞稍后再看', async ({page,request}) => {
  await openTopic(page);
  const gate=deferred(), bodies=[];
  await page.route('**/api/news/ui-001/state',async route=>{
    bodies.push(route.request().postDataJSON());
    if(bodies.length===1) await gate.promise;
    await route.continue();
  });
  const button=buttonFor(page);
  await button.locator('xpath=ancestor::article').hover();await button.click();
  await expect(button).toHaveAttribute('aria-busy','true');
  await button.click();
  await expect(button).toHaveAttribute('aria-pressed','false');
  await page.locator('[data-topic-read-later="ui-001"]').click();
  gate.resolve();
  await expect(button).toHaveAttribute('aria-busy','false');
  await expect(page.locator('[data-topic-read-later="ui-001"]')).toHaveAttribute('aria-busy','false');
  expect(bodies).toEqual([{important:true},{important:false,read:true,read_later:true}]);
  const saved=await detail(request);
  expect(saved.important_at).toBeNull();expect(saved.read_later_at).toBeTruthy();
});

test('迟到的主题详情不能覆盖刚保存的重要标记', async ({page}) => {
  await openTopic(page);
  const gate=deferred(), captured=deferred();
  await page.route('**/api/topics/test-acquisition?*',async route=>{
    const response=await route.fetch();captured.resolve();
    await gate.promise;await route.fulfill({response});
  });
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await captured.promise;
  const button=buttonFor(page);
  await button.locator('xpath=ancestor::article').hover();await button.click();
  await expect(button).toHaveAttribute('aria-busy','false');
  const response=page.waitForResponse(r=>r.url().includes('/api/topics/test-acquisition?'));
  gate.resolve();await response;
  await expect(button).toHaveAttribute('aria-pressed','true');
});

for (const mobile of [false,true]) test.describe(mobile?'手机':'桌面',()=>{
  test.use({viewport:mobile?{width:390,height:844}:{width:1440,height:900},isMobile:mobile,hasTouch:mobile});
  test('重要与稍后再看共用纯图标样式，所有主题和字号下保持可操作',async({page,request})=>{
    await request.patch('/api/news/ui-064/state',{data:{important:true}});
    await page.route('**/api/topics/test-acquisition?*',async route=>{
      const response=await route.fetch(), data=await response.json();
      data.items.forEach(item=>item.source_name='特别长的国际新闻来源 International News');
      await route.fulfill({response,json:data});
    });
    await openTopic(page);
    const button=buttonFor(page), card=button.locator('xpath=ancestor::article');
    for(const [theme,colorScheme] of [['light','light'],['dark','dark'],['system','light'],['system','dark'],['eink','light']]) {
      await page.emulateMedia({colorScheme});await chooseTheme(page,theme);
      for(const size of ['standard','extra-large']) {
        const label=await page.locator('#fontSizeSelect-native option[value="'+size+'"]').textContent();
        await page.getByRole('combobox',{name:'阅读字号',exact:true}).click();
        await page.getByRole('option',{name:label,exact:true}).click();
        await card.scrollIntoViewIfNeeded();await page.mouse.move(0,0);
        await expect(button).toHaveCSS('opacity',mobile?'1':'0');
        await button.focus();await page.keyboard.press('Tab');await page.keyboard.press('Shift+Tab');
        await expect(button).toBeFocused();await expect(button).toHaveCSS('opacity','1');
        await expect(button).toHaveCSS('outline-style','solid');
        await expectPlain(button);
        await page.keyboard.press('Enter');
        await expect(button).toHaveAttribute('aria-pressed','true');
        await expect(button).toHaveAttribute('aria-busy','false');
        await button.blur();await page.mouse.move(0,0);
        await expect(button).toHaveCSS('opacity','1');
        await expectPlain(button);
        const layout=await button.evaluate(n=>{
          const row=n.closest('.news-card-meta'), later=n.nextElementSibling;
          const a=n.getBoundingClientRect(), b=later.getBoundingClientRect();
          return {fits:row.scrollWidth<=row.clientWidth,width:a.width,height:a.height,before:a.right<b.left,
            sameLine:Math.abs((a.top+a.bottom)/2-(b.top+b.bottom)/2)<1,
            distinct:n.querySelector('svg').innerHTML!==later.querySelector('svg').innerHTML,
            color:getComputedStyle(n).color};
        });
        expect(layout.fits && layout.before && layout.sameLine && layout.distinct).toBe(true);
        expect(layout.width).toBeGreaterThanOrEqual(mobile?32:26);
        expect(layout.height).toBeGreaterThanOrEqual(mobile?32:26);
        expect(layout.color).toBe(await page.locator('#feedCardList [data-state="important"].active').first().evaluate(n=>getComputedStyle(n).color));
        if(size==='extra-large' && theme!=='system') await page.screenshot({path:'.ui-artifacts/topic-important-'+(mobile?'mobile':'desktop')+'-'+theme+'.png'});
        await button.click();await expect(button).toHaveAttribute('aria-pressed','false');
        await expect(button).toHaveAttribute('aria-busy','false');await button.blur();
      }
    }
  });
});
