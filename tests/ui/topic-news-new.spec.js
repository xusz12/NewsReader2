import { test, expect } from '@playwright/test';
import { chooseTheme, openNavigation, openSettings } from './helpers.js';

const id = 'test-acquisition';
const path = '/api/topics/' + id;
const markers = page => page.locator('[data-topic-news-new]');
const isDetail = response => new URL(response.url()).pathname === path && response.request().method() === 'GET';
async function refresh(page) {
  const response = page.waitForResponse(isDetail);
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await response;
}
async function openTopic(page, width = 1440) {
  await page.setViewportSize({width, height:1000});
  await page.goto('/');
  await openNavigation(page);
  await page.locator('#navTopicsBtn').click();
  await page.locator('[data-topic-id="'+id+'"]').click();
  await expect(page.locator('#topicContent .article-title')).toHaveText('北辰科技收购远川数据');
  await expect(page.locator('.topic-news')).toHaveCount(2);
}
async function acknowledge(request, topicId = id) {
  const snapshot = await (await request.get('/api/topics/' + topicId)).json();
  await request.post('/api/topics/' + topicId + '/seen', {data:{observed_at:snapshot.observed_at}});
}
test.beforeEach(async ({page, request}) => {
  await request.post('/__test/reset-reading');
  await request.post('/__test/topics');
  const now = new Date();
  await page.clock.install({time:now});
  await page.clock.pauseAt(now);
});

for (const width of [390,1440]) test('来源旁的新新闻标记满六秒清除并持久化 '+width, async ({page,request}) => {
  const seen = [];
  page.on('request', r => { if (r.url().endsWith(path+'/seen')) seen.push(r); });
  await openTopic(page, width);
  await expect(markers(page)).toHaveCount(2);
  await expect(markers(page).first()).toHaveText('new');
  await expect(markers(page).first()).toHaveAttribute('aria-label','未读新闻');
  expect(await markers(page).first().evaluate(node => node.previousElementSibling.classList.contains('meta-source-name'))).toBe(true);
  await page.clock.runFor(5999);
  expect(seen).toHaveLength(0);
  await expect(markers(page)).toHaveCount(2);
  await page.clock.runFor(1);
  await expect(markers(page)).toHaveCount(0);
  await expect(page.locator('[data-topic-id="'+id+'"] .meta-time-row')).toHaveText('2 条新闻');
  expect(seen).toHaveLength(1);
  await page.clock.runFor(120); // The shared navigation counters use a 120 ms throttle.
  await expect(page.locator('#topicsNavBadge')).toHaveText('1');
  await expect(page.locator('#unreadBadge')).toHaveText('63');
  expect((await (await request.get(path)).json()).items.every(item=>!item.is_new)).toBe(true);
  await openTopic(page, width);
  await expect(markers(page)).toHaveCount(0);
  expect((await (await request.get('/api/topics/test-industry')).json()).items.every(item=>!item.is_new && item.read_at)).toBe(true);
  await openNavigation(page);await page.locator('#navFeedBtn').click();
  await page.locator('#feedColFeed [data-filter="all"]').click();
  await expect(page.locator('#feedCardList [data-id="ui-000"] .unread-dot')).toHaveCount(0);
  await expect(page.locator('#feedCardList [data-id="ui-001"]')).toHaveClass(/read/);
  await page.locator('#feedColFeed [data-filter="unread"]').click();
  const unread=(await (await request.get('/api/news?collection=feed&read_filter=unread&per=100')).json()).items;
  expect(unread.some(item=>['ui-000','ui-001'].includes(item.id))).toBe(false);
});

test('仅全局未读的新闻显示标记，普通轮询不重置六秒计时', async ({page,request}) => {
  await acknowledge(request);
  await request.patch('/api/news/ui-001/state',{data:{read:true}});
  await request.post('/__test/topic-update',{data:{news:true,item_id:'ui-000'}});
  await openTopic(page);
  await expect(markers(page)).toHaveCount(1);
  await expect(markers(page)).toHaveAttribute('data-topic-news-new','ui-000');
  await page.clock.runFor(5000);
  await refresh(page);
  await page.clock.runFor(999);
  await expect(markers(page)).toHaveCount(1);
  await page.clock.runFor(1);
  await expect(markers(page)).toHaveCount(0);
});

for (const destination of ['article','definition','filter','settings','hidden','mobile-list'])
test('不足六秒离开时保留标记，返回后重新计时 '+destination, async ({page,request}) => {
  await openTopic(page, destination==='mobile-list'?390:1440);
  await page.clock.runFor(5000);
  if (destination==='article') await page.locator('[data-topic-item]').first().click();
  if (destination==='definition') await page.locator('#topicDefinitionBtn').click();
  if (destination==='filter') await page.locator('.topic-node').first().click();
  if (destination==='settings') await openSettings(page);
  if (destination==='mobile-list') await page.locator('#mobileReaderBackBtn').click();
  if (destination==='hidden') await page.evaluate(() => {
    Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await page.clock.runFor(4000);
  expect((await (await request.get(path)).json()).items.every(item=>item.is_new)).toBe(true);
  if (destination==='article') await page.locator('#topicReaderBackBtn').click();
  if (destination==='definition') await page.locator('#topicDefinitionBtn').click();
  if (destination==='filter') await page.locator('[data-topic-action="all"]').click();
  if (destination==='settings') await page.locator('#navTopicsBtn').click();
  if (destination==='mobile-list') await page.locator('[data-topic-id="'+id+'"]').click();
  if (destination==='hidden') {
    await page.evaluate(() => Object.defineProperty(document,'hidden',{configurable:true,get:()=>false}));
    await refresh(page);
  }
  await expect(markers(page)).toHaveCount(2);
  await page.clock.runFor(5999);
  await expect(markers(page)).toHaveCount(2);
  await page.clock.runFor(1);
  await expect(markers(page)).toHaveCount(0);
});

test('旧确认请求完成时保留期间到达的新新闻，并给予新的六秒', async ({page,request}) => {
  let release;
  const gate = new Promise(resolve=>release=resolve);
  let calls = 0;
  await page.route('**/api/news/mark-read-by-ids',async route => {
    if (++calls===1) await gate;
    await route.continue();
  });
  await openTopic(page);
  const firstSent = page.waitForRequest(r=>r.url().endsWith('/api/news/mark-read-by-ids'));
  await page.clock.runFor(6000);await firstSent;
  await request.post('/__test/topic-update',{data:{news:true,item_id:'ui-002'}});
  await refresh(page);
  // Wait for the new snapshot to be rendered before releasing the old acknowledgment.
  const updated = (await (await request.get(path)).json()).items.find(item=>item.id==='ui-002');
  expect(updated.is_new).toBe(true);
  await page.evaluate(()=>new Promise(resolve=>queueMicrotask(resolve)));
  release();
  await expect(markers(page)).toHaveCount(1);
  await expect(markers(page)).toHaveAttribute('data-topic-news-new','ui-002');
  await page.clock.runFor(5999);
  await expect(markers(page)).toHaveCount(1);
  await page.clock.runFor(1);
  await expect(markers(page)).toHaveCount(0);
  expect(calls).toBe(2);
});

test('确认失败保留标记，下一次可见刷新可以重试', async ({page}) => {
  let calls=0;
  await page.route('**/api/news/mark-read-by-ids',route => ++calls===1
    ? route.fulfill({status:503,json:{ok:false,error:'unavailable'}}) : route.continue());
  await openTopic(page);
  const failed=page.waitForResponse(r=>r.url().endsWith('/api/news/mark-read-by-ids'));
  await page.clock.runFor(6000);expect((await failed).status()).toBe(503);
  await expect(markers(page)).toHaveCount(2);
  await refresh(page);
  await page.clock.runFor(6000);
  await expect(markers(page)).toHaveCount(0);
  expect(calls).toBe(2);
});

test('迟到的旧详情响应不会让已清除的标记重新出现', async ({page}) => {
  await openTopic(page);
  let release, captured;
  const gate=new Promise(resolve=>release=resolve), ready=new Promise(resolve=>captured=resolve);
  await page.route('**'+path+'?*',async route=>{
    const response=await route.fetch(), data=await response.json();
    captured();await gate;
    await route.fulfill({response,json:data});
  });
  const refreshing=refresh(page);await ready;
  await page.clock.runFor(6000);
  await expect(markers(page)).toHaveCount(0);
  release();await refreshing;
  await expect(markers(page)).toHaveCount(0);
  await page.clock.runFor(6000);
  await expect(markers(page)).toHaveCount(0);
});

test('分页沿用主题查看状态，已确认的后续页面不重复显示 new', async ({page,request}) => {
  await request.post('/__test/topics',{data:{many:true}});
  await page.goto('/');await openNavigation(page);await page.locator('#navTopicsBtn').click();
  await page.locator('[data-topic-id="'+id+'"]').click();
  await expect(markers(page)).toHaveCount(40);
  await page.clock.runFor(6000);await expect(markers(page)).toHaveCount(0);
  await page.locator('[data-topic-action="more"]').click();
  await expect(page.locator('.topic-news')).toHaveCount(65);
  await expect(markers(page)).toHaveCount(0);
  const unread=await (await request.get('/api/news?collection=feed&read_filter=unread&count_only=1')).json();
  expect(unread.total).toBe(0);
});

test('新闻流的标读和改为未读同步到所有主题', async ({page,request}) => {
  await page.goto('/');
  await page.locator('#feedColFeed [data-filter="all"]').click();
  await page.locator('#feedCardList [data-id="ui-000"] .news-card-title').click();
  await page.keyboard.press('m');
  await expect.poll(async()=> (await (await request.get('/api/news/ui-000/detail')).json()).read_at).toBeTruthy();
  await openNavigation(page);await page.locator('#navTopicsBtn').click();
  await page.locator('[data-topic-id="'+id+'"]').click();
  await expect(markers(page)).toHaveCount(1);
  await expect(markers(page)).toHaveAttribute('data-topic-news-new','ui-001');
  await page.locator('[data-topic-id="test-industry"]').click();
  await expect(markers(page)).toHaveCount(1);
  await page.locator('[data-topic-item="ui-000"]').click();
  await page.keyboard.press('m');
  await expect.poll(async()=> (await (await request.get('/api/news/ui-000/detail')).json()).read_at).toBeNull();
  await page.locator('#topicReaderBackBtn').click();
  await expect(markers(page)).toHaveCount(2);
  await page.locator('[data-topic-id="'+id+'"]').click();
  await expect(markers(page)).toHaveCount(2);
  await page.clock.runFor(6000);
  await expect(markers(page)).toHaveCount(0);
  await expect.poll(async()=> (await (await request.get('/api/topics/test-industry')).json()).unread_ids).toEqual([]);
});

for (const width of [390,1440]) test('标记复用卡片样式，长来源与主题字号不溢出 '+width, async ({page}) => {
  await page.route('**'+path+'?*',async route => {
    const response=await route.fetch(), data=await response.json();
    data.items.forEach(item=>item.source_name='国际新闻来源名称特别长的测试信源 International News');
    await route.fulfill({response,json:data});
  });
  await openTopic(page,width);
  for (const [theme,colorScheme] of [['light','light'],['dark','dark'],['system','light'],['system','dark'],['eink','light']]) {
    await page.emulateMedia({colorScheme});await chooseTheme(page,theme);
    for (const size of ['standard','extra-large']) {
      const label=await page.locator('#fontSizeSelect-native option[value="'+size+'"]').textContent();
      await page.getByRole('combobox',{name:'阅读字号',exact:true}).click();
      await page.getByRole('option',{name:label,exact:true}).click();
      const marker=markers(page).first();await expect(marker).toBeVisible();
      const layout=await marker.evaluate(node=>{
        const row=node.closest('.news-card-meta'), source=node.previousElementSibling;
        const n=node.getBoundingClientRect(), s=source.getBoundingClientRect(), r=row.getBoundingClientRect();
        return {fits:row.scrollWidth<=row.clientWidth, after:n.left>=s.right, inside:n.right<=r.right,
          sameLine:Math.abs((n.top+n.bottom)/2-(s.top+s.bottom)/2)<1,
          sourceFont:getComputedStyle(source).fontSize, badgeFont:getComputedStyle(node).fontSize};
      });
      expect(layout.fits && layout.after && layout.inside && layout.sameLine).toBe(true);
      expect(layout.sourceFont).toBe(size==='standard'?'11px':'14.3px');
      expect(layout.badgeFont).toBe('10.5px');
    }
    if(['light','dark','eink'].includes(theme)) {
      await page.locator('.topic-news').first().scrollIntoViewIfNeeded();
      await page.screenshot({path:'.ui-artifacts/topic-news-new-'+width+'-'+theme+'.png'});
    }
  }
});
