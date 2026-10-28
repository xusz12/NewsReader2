import { test, expect } from '@playwright/test';
import { chooseTheme, openNavigation } from './helpers.js';

const original = 'Markets **rose** today.\n\n📌 Analysts expect [more growth](https://example.com).\n\n> Demand remains strong.';
const translated = '市场今日**上涨**。\n\n📌 分析师预期[增长持续](https://example.com)。\n\n> 需求依然强劲。';
const refresh = page => page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
async function openArticle(page) {
  await page.goto('/');
  await page.locator('[data-id="ui-063"] .news-card-title').click();
  await page.getByRole('button', {name:'中英双语',exact:true}).click();
}
async function selectText(locator) {
  await locator.scrollIntoViewIfNeeded();
  await locator.evaluate(node => {
    const range=document.createRange();range.selectNodeContents(node);
    const selection=getSelection();selection.removeAllRanges();selection.addRange(range);
    node.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));
  });
}
test.beforeEach(async ({request}) => {
  await request.post('/__test/reset-reading');
  await request.post('/__test/topics');
  await request.post('/__test/bilingual',{data:{original,translated}});
});
test.afterEach(async ({request}) => { await request.post('/__test/bilingual'); });

for (const width of [390,1440]) test('主题入口数量覆盖新进展和新闻，仅查看具体主题后清除 '+width, async({page,request})=>{
  await page.setViewportSize({width,height:900});await page.goto('/');
  await openNavigation(page);
  await expect(page.locator('#topicsNavBadge')).toHaveText('2');
  await expect(page.locator('#navTopicsBtn')).toHaveAttribute('aria-label','主题追踪，2 个主题有更新');
  await page.locator('#navTopicsBtn').click();
  if(width===390) await expect(page.locator('#topicsNavBadge')).toHaveText('2');
  if(width===390) await page.locator('[data-topic-id="test-acquisition"]').click();
  await expect(page.locator('.topic-card.is-active')).toHaveCount(1);
  const id=await page.locator('.topic-card.is-active').getAttribute('data-topic-id');
  const remaining=id==='test-acquisition'?'test-industry':'test-acquisition';
  await expect(page.locator('#topicsNavBadge')).toHaveText('1',{timeout:8000});
  // A detail reader hides the topic; its polling must not acknowledge new progress.
  await page.locator('.topic-news [data-topic-item]').first().click();
  await request.post('/__test/topic-update',{data:{id}});await refresh(page);
  await expect(page.locator('#topicsNavBadge')).toHaveText('2');
  await page.locator(width===390?'#mobileReaderBackBtn':'#topicReaderBackBtn').click();
  await expect(page.locator('#topicsNavBadge')).toHaveText('1',{timeout:8000});
  if(width===390) await page.locator('#mobileReaderBackBtn').click();
  await page.locator('[data-topic-id="'+remaining+'"]').click();
  await expect(page.locator('#topicsNavBadge')).toBeHidden({timeout:8000});
  await openNavigation(page);await page.locator('#navFeedBtn').click();
  await request.post('/__test/topic-update',{data:{news:true,item_id:'ui-002'}});await refresh(page);
  await expect(page.locator('#topicsNavBadge')).toHaveText('1');
  await page.reload();await openNavigation(page);
  await expect(page.locator('#topicsNavBadge')).toHaveText('1');
  await page.screenshot({path:'.ui-artifacts/topic-update-badge-'+width+'.png'});
});

test('双语逐段交替，保留两种语言的原始偏移、高亮和引用',async({page,request})=>{
  await openArticle(page);
  const pairs=page.locator('.bilingual-pair');await expect(pairs).toHaveCount(3);
  expect(await pairs.locator('[lang]').evaluateAll(nodes=>nodes.map(n=>n.lang))).toEqual(['en','zh-Hans','en','zh-Hans','en','zh-Hans']);
  for(const [lang,kind] of [['en','original'],['zh-Hans','ai_zh']]) {
    const paragraph=pairs.nth(1).locator('[lang="'+lang+'"]');
    await selectText(paragraph);await page.getByRole('button',{name:'高亮',exact:true}).click();
    await expect(paragraph.locator('mark')).not.toHaveCount(0);
    const data=(await (await request.get('/api/news/ui-063/detail')).json());
    const surface=data.highlight_surfaces[kind];
    expect(surface.highlights).toHaveLength(1);
    const row=surface.highlights[0],raw=Array.from(kind==='original'?original:translated);
    expect(raw.slice(row.start_offset,row.end_offset).join('')).toBe(row.selected_text);
  }
  await selectText(pairs.nth(1).locator('[lang="zh-Hans"]'));
  await page.locator('#quoteNoteBtn').click();
  await expect(page.locator('.draft-quote')).toContainText('引用译文第 2 段');
  await page.locator('[data-body-mode="translated"]').click();
  await expect(page.locator('[data-body-mode="translated"]')).toBeFocused();
  await expect(page.locator('#articleBodyContent mark')).not.toHaveCount(0);
  await page.locator('[data-body-mode="translated"]').press('Tab');
  await page.locator('[data-body-mode="bilingual"]').press('Enter');
  await expect(page.locator('[data-body-mode="bilingual"]')).toBeFocused();
  await expect(page.locator('.bilingual-pair [lang="en"] mark')).not.toHaveCount(0);
  await page.reload();await page.locator('[data-id="ui-063"] .news-card-title').click();
  await page.locator('[data-body-mode="bilingual"]').click();
  await expect(page.locator('.bilingual-pair [lang="zh-Hans"] mark')).not.toHaveCount(0);
});

test('旧的跨段译文高亮在双语模式中仍可完整引用',async({page,request})=>{
  const selected=translated.split('\n\n').slice(0,2).join('\n\n');
  const response=await request.post('/api/news/ui-063/highlights',{data:{body_kind:'ai_zh',start_offset:0,
    end_offset:Array.from(selected).length,selected_text:selected,color:'yellow'}});
  expect(response.ok()).toBeTruthy();
  await openArticle(page);
  await page.locator('.bilingual-pair [lang="zh-Hans"] mark').first().click();
  await page.locator('#quoteNoteBtn').click();
  await expect(page.locator('.draft-quote')).toContainText('市场今日上涨');
  await expect(page.locator('.draft-quote')).toContainText('分析师预期增长持续');
});

test('旧译文分段不匹配和翻译缺失时保留全文，只有译文也能切换',async({page,request})=>{
  await request.post('/__test/bilingual',{data:{original,translated:'完整中文译文。',body_only:true}});
  await openArticle(page);
  await expect(page.locator('#detailContent [role="status"]')).toContainText('分段与原文不一致');
  await expect(page.locator('#articleBodyContent [lang="en"]')).toContainText('Demand remains strong.');
  await expect(page.locator('#articleBodyContent [lang="zh-Hans"]')).toHaveText('完整中文译文。');
  await request.post('/__test/bilingual',{data:{original,translated:'',body_only:true}});
  await openArticle(page);
  await expect(page.locator('#detailContent [role="status"]')).toContainText('中文译文尚未就绪');
  await request.post('/__test/bilingual',{data:{original:'',translated,body_only:true}});
  await openArticle(page);
  await expect(page.locator('#detailContent [role="status"]')).toContainText('英文原文尚未就绪');
  await expect(page.locator('#articleBodyContent')).toContainText('需求依然强劲');
});

for(const width of [390,920,921,1279,1280,1440]) test('双语复用正文排版、主题与阅读字号 '+width,async({page})=>{
  await page.setViewportSize({width,height:1000});await openArticle(page);
  for(const [theme,colorScheme] of [['light','light'],['dark','dark'],['system','light'],['system','dark'],['eink','light']]) {
    await page.emulateMedia({colorScheme});await chooseTheme(page,theme);
    for(const size of ['standard','extra-large']) {
      const label=await page.locator('#fontSizeSelect-native option[value="'+size+'"]').textContent();
      await page.getByRole('combobox',{name:'阅读字号',exact:true}).click();
      await page.getByRole('option',{name:label,exact:true}).click();
      await expect(page.locator('#articleBodyContent')).toHaveCSS('font-size',size==='standard'?'14px':'18.2px');
      const metrics=await page.locator('.bilingual-pair').first().evaluate(pair=>{
        const nodes=[pair.closest('.article-body'),...pair.querySelectorAll('p')];
        return nodes.map(node=>{const c=getComputedStyle(node);return [c.fontFamily,c.fontSize,c.lineHeight,c.color];});
      });
      expect(metrics[1]).toEqual(metrics[0]);expect(metrics[2]).toEqual(metrics[0]);
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
    }
    await page.locator('.bilingual-pair').first().scrollIntoViewIfNeeded();
    if([390,1440].includes(width)) await page.screenshot({path:'.ui-artifacts/bilingual-'+width+'-'+theme+'-'+colorScheme+'.png'});
  }
});
