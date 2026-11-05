import {test,expect} from '@playwright/test';
import {chooseTheme,openNavigation} from './helpers.js';

async function openSources(page,request) {
  await request.post('/__test/reports',{data:{linked_sources:true}});
  await page.route('**/api/reports/report-evening',async route=>{
    const response=await route.fetch(), data=await response.json(), report=data.report;
    report.content.sources.forEach(source=>source.title='合成短标题');
    report.content.sources[0].published_at='2026-11-03T10:00:00+08:00';
    report.content.sources[1].published_at='2026-11-03T03:00:00Z';
    delete report.source_items.N2;
    report.content.sources.push({...report.content.sources[0],ref:'N3',published_at:'2026-11-02T23:00:00+08:00',title:'合成测试：长标题与 English words 混排，验证在较大阅读字号下能够正确换行且不发生居中或超出来源列表。'});
    report.source_items.N3={...report.source_items.N1};
    report.content.sections[0].paragraphs[0].refs.push('N3');
    await route.fulfill({response,json:data});
  });
  await openNavigation(page);await page.locator('#navReportsBtn').click();
  await page.locator('[data-report-id="report-evening"]').click();
  const sources=page.locator('.report-sources').first();
  await sources.locator('summary').click();
  return sources;
}

async function sourceGeometry(sources) {
  return sources.locator('.reader-source-list li').evaluateAll(rows=>rows.map(row=>{
    const title=row.querySelector('.reader-source-open'), range=document.createRange();
    range.selectNodeContents(title);
    const box=title.getBoundingClientRect(), line=range.getClientRects()[0], bounds=row.getBoundingClientRect();
    return {type:title.tagName, direction:getComputedStyle(title).flexDirection,
      leftOffset:line.left-bounds.left, insetTop:line.top-box.top, height:box.height,
      overflow:Math.max(...[...range.getClientRects()].map(rect=>rect.right-bounds.right)),
      gap:row.querySelector('.news-card-meta').getBoundingClientRect().top-box.bottom};
  }));
}

for (const touch of [true,false]) test.describe(touch?'触屏来源':'桌面来源',()=>{
  test.use({hasTouch:touch,isMobile:touch,viewport:{width:touch?390:1440,height:900}});
  for (const theme of ['light','dark','system-light','system-dark','eink']) test('标题对齐、行距与操作 '+theme,async({page,request},testInfo)=>{
    await page.emulateMedia({colorScheme:theme==='system-dark'?'dark':'light'});
    await page.goto('/');await chooseTheme(page,theme.startsWith('system')?'system':theme);
    const sources=await openSources(page,request);
    expect(await page.evaluate(()=>matchMedia('(pointer: coarse)').matches)).toBe(touch);
    for (const large of [false,true]) {
      if (large) {
        await page.getByRole('combobox',{name:'阅读字号',exact:true}).click();
        await page.getByRole('option',{name:/特大/}).click();
      }
      const geometry=await sourceGeometry(sources);
      await testInfo.attach('source-geometry-'+large,{body:JSON.stringify(geometry),contentType:'application/json'});
      expect(geometry.map(row=>row.type)).toEqual(['BUTTON','BUTTON','A']);
      await expect(sources.locator('.reader-source-meta')).toHaveText([/11[-/]02 23:00/,/11[-/]03 10:00/,/11[-/]03 11:00/]);
      for (const row of geometry) {
        expect(Math.abs(row.leftOffset)).toBeLessThan(1);
        expect(row.overflow).toBeLessThanOrEqual(1);
        if (touch) {expect(row.height).toBeGreaterThanOrEqual(44);expect(Math.abs(row.gap)).toBeLessThan(1);}
      }
      if (touch) expect(geometry[2].insetTop).toBeCloseTo(geometry[1].insetTop,1);
      await expect(sources.locator('li').nth(2).locator('[data-source-state]')).toHaveCount(0);
      await expect(sources.locator('li').nth(1).locator('[data-source-state]')).toHaveCount(2);
      await expect(sources.locator('[data-source-state="important"]').first()).toBeVisible();
    }
    await page.screenshot({path:`.ui-artifacts/source-layout-${touch?'touch':'desktop'}-${theme}.png`});
    await sources.locator('[data-source-item]').first().click();
    await expect(page.locator('#detailPanelFeed')).toHaveClass(/active/);
    await page.locator(touch?'#mobileReaderBackBtn':'#topicReaderBackBtn').click();
    await expect(sources).toHaveJSProperty('open',true);
  });
});
