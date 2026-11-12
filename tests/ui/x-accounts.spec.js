import { test, expect } from '@playwright/test';
import { openSettings, chooseTheme, contrast } from './helpers.js';

const account = (page, handle) => page.locator(`[data-x-account="${handle}"]`);
async function start(page) {
  await page.goto('/'); await openSettings(page);
  await page.locator('[data-settings-tab="x"]').click();
  await expect(page.locator('#showXAccountFormBtn')).toBeVisible();
  await expect(page.locator('#xAccountForm')).toBeHidden();
}
async function add(page, handle) {
  if (!await page.locator('#xAccountForm').isVisible()) await page.locator('#showXAccountFormBtn').click();
  await page.locator('#xAccountInput').fill(handle);
  await page.locator('#addXAccountBtn').click();
  await expect(page.locator('#addXAccountBtn')).toHaveText('添加');
}
async function saved(request) { return (await request.get('/api/settings')).json(); }

test.beforeEach(async ({page,request}) => {
  await page.route('**/api/settings/x-accounts/test', route => route.fulfill({json:{
    ok:true, handle:route.request().postDataJSON().handle, count:3,
  }}));
  await request.post('/__test/x-fixture', {data:{clear:true}});
  const data = await saved(request);
  for (const a of data.x_accounts.filter(a => a.handle.startsWith('uixtest'))) {
    const preview = await (await request.get('/api/settings/x-accounts/' + a.handle)).json();
    await request.delete('/api/settings/x-accounts/' + a.handle, {data:{delete_history:true,
      x_accounts_revision:preview.x_accounts_revision, confirmation_token:preview.source.confirmation_token}});
  }
});
test.afterEach(async ({request}) => { await request.post('/__test/x-fixture', {data:{clear:true}}); });

test('add, normalize, independent switches, draft navigation, save, undo and duplicate rejection', async ({page,request}) => {
  const errors=[]; page.on('pageerror', e => errors.push(e.message));
  await start(page);
  const form = page.locator('#xAccountForm'), show = page.locator('#showXAccountFormBtn');
  await expect(page.locator('#settingsSaveBtn')).toBeHidden();
  await expect(page.locator('#settingsResetBtn')).toBeHidden();
  await show.click();
  await expect(show).toHaveAttribute('aria-expanded','true');
  await expect(page.locator('#xAccountInput')).toBeFocused();
  await page.locator('#xAccountInput').fill('uixtestcancel');
  await expect(page.locator('#settingsSaveBtn')).toBeHidden();
  await page.keyboard.press('Escape');
  await expect(form).toBeHidden(); await expect(show).toBeFocused();
  await show.click();
  await expect(page.locator('#xAccountInput')).toHaveValue('');
  await page.locator('#cancelXAccountBtn').click();
  await expect(form).toBeHidden(); await expect(show).toHaveAttribute('aria-expanded','false');
  await add(page, 'https://x.com/UIXTestOne');
  await expect(form).toBeVisible(); await expect(page.locator('#xAccountInput')).toHaveValue('');
  await expect(page.locator('#xAccountTestResult')).toHaveAttribute('data-status','success');
  await expect(page.locator('#settingsSaveBtn')).toBeVisible();
  await expect(page.locator('#settingsResetBtn')).toBeVisible();
  const row = account(page,'uixtestone');
  await expect(row.getByRole('switch')).toHaveCount(2);
  for (const toggle of await row.getByRole('switch').all()) await expect(toggle).not.toBeChecked();
  await row.getByRole('switch', {name:'自动翻译 @uixtestone', exact:true}).check();
  await expect(row.getByRole('switch', {name:'自动抓取全文 @uixtestone', exact:true})).not.toBeChecked();
  await add(page, '@UIXTESTONE');
  await expect(form).toBeVisible();
  expect(await page.locator('#xAccountInput').evaluate(el => el.validationMessage)).toContain('已经');
  await add(page, 'uixtesttwo');
  await account(page,'uixtesttwo').getByRole('switch', {name:'自动抓取全文 @uixtesttwo', exact:true}).check();
  await page.locator('[data-settings-tab="pipeline"]').click();
  await page.locator('[data-settings-tab="x"]').click();
  await expect(row.getByRole('switch', {name:'自动翻译 @uixtestone', exact:true})).toBeChecked();
  await page.locator('#settingsSaveBtn').click();
  await expect(page.locator('#settingsStatus')).toHaveText('设置已保存');
  const data = await saved(request);
  expect(data.x_accounts.find(a=>a.handle==='uixtestone')).toMatchObject({auto_translate:true,auto_fulltext:false});
  expect(data.x_accounts.find(a=>a.handle==='uixtesttwo')).toMatchObject({auto_translate:false,auto_fulltext:true});
  await row.getByRole('switch', {name:'自动抓取全文 @uixtestone', exact:true}).check();
  await page.locator('#settingsResetBtn').click();
  await expect(row.getByRole('switch', {name:'自动抓取全文 @uixtestone', exact:true})).not.toBeChecked();
  await page.reload(); await openSettings(page); await page.locator('[data-settings-tab="x"]').click();
  await expect(row.getByRole('switch', {name:'自动翻译 @uixtestone', exact:true})).toBeChecked();
  expect(errors).toEqual([]);
});

test('adding waits for content verification and only successful tests expose save and undo', async ({page,request}) => {
  let release, calls=0;
  const gate = new Promise(resolve=>{release=resolve;});
  await page.route('**/api/settings/x-accounts/test', async route => {
    calls++;
    if (calls === 1) {
      await gate;
      await route.fulfill({status:422,json:{ok:false,error:'x_account_empty'}});
    } else await route.fulfill({json:{ok:true,handle:'uixtestprobe',count:2}});
  });
  await start(page); await page.locator('#showXAccountFormBtn').click();
  const input=page.locator('#xAccountInput'), button=page.locator('#addXAccountBtn'), result=page.locator('#xAccountTestResult');
  await input.fill('@UIXTestProbe');
  await expect(page.locator('#settingsSaveBtn')).toBeHidden();
  await button.click();
  try {
    await expect(button).toHaveText('测试中...'); await expect(button).toBeDisabled();
    await expect(button).toHaveAttribute('aria-busy','true'); await expect(input).toBeDisabled();
    await expect(result).toHaveAttribute('data-status','running');
    await expect(account(page,'uixtestprobe')).toHaveCount(0);
    await expect(page.locator('#settingsSaveBtn')).toBeHidden();
    await expect(page.locator('#settingsResetBtn')).toBeHidden();
    await page.locator('[data-settings-tab="pipeline"]').click();
    await page.locator('[data-settings-tab="x"]').click();
    await expect(button).toHaveText('测试中...');
    await button.evaluate(el=>el.click());
    expect(calls).toBe(1);
  } finally { release(); }
  await expect(result).toHaveAttribute('data-status','failed');
  await expect(result).toContainText('未获取到有效推文');
  await expect(input).toHaveValue('@UIXTestProbe'); await expect(input).toBeEnabled();
  await expect(button).toHaveText('添加'); await expect(button).toHaveAttribute('aria-busy','false');
  await expect(page.locator('#settingsSaveBtn')).toBeHidden();
  await expect(account(page,'uixtestprobe')).toHaveCount(0);
  await button.click();
  await expect(result).toHaveAttribute('data-status','success'); await expect(result).toContainText('2 条内容');
  await expect(account(page,'uixtestprobe')).toBeVisible();
  await expect(page.locator('#settingsSaveBtn')).toBeVisible();
  await expect(page.locator('#settingsResetBtn')).toBeVisible();
  expect((await saved(request)).x_accounts.some(a=>a.handle==='uixtestprobe')).toBe(false);
  await page.locator('#settingsResetBtn').click();
  await expect(account(page,'uixtestprobe')).toHaveCount(0); await expect(result).toBeHidden();
  await expect(page.locator('#settingsSaveBtn')).toBeHidden();
});

for (const action of ['cancel','undo']) {
  test(`${action} discards a late account test without restoring an abandoned draft`, async ({page}) => {
    let release, completed;
    const gate=new Promise(resolve=>{release=resolve;}), finished=new Promise(resolve=>{completed=resolve;});
    await page.route('**/api/settings/x-accounts/test', async route => {
      const handle=route.request().postDataJSON().handle;
      if (handle === 'uixtestlate') await gate;
      try { await route.fulfill({json:{ok:true,handle,count:3}}); }
      finally { if (handle === 'uixtestlate') completed(); }
    });
    await start(page);
    if (action === 'undo') await account(page,'ilyasut').getByRole('switch',{name:'自动翻译 @ilyasut',exact:true}).check();
    await page.locator('#showXAccountFormBtn').click(); await page.locator('#xAccountInput').fill('uixtestlate');
    await page.locator('#addXAccountBtn').click();
    try {
      await expect(page.locator('#addXAccountBtn')).toHaveText('测试中...');
      await page.locator(action === 'cancel' ? '#cancelXAccountBtn' : '#settingsResetBtn').click();
      await expect(page.locator('#xAccountForm')).toBeHidden();
      await expect(page.locator('#settingsSaveBtn')).toBeHidden();
      await add(page,'uixtestlatest');
      await expect(account(page,'uixtestlatest')).toBeVisible();
    } finally { release(); }
    await finished;
    await expect(account(page,'uixtestlate')).toHaveCount(0);
    await expect(page.locator('#xAccountTestResult')).toContainText('@uixtestlatest');
    await expect(page.locator('#xAccountTestResult')).toHaveAttribute('data-status','success');
  });
}

test('an invalid success response cannot add an account', async ({page}) => {
  await page.route('**/api/settings/x-accounts/test', route => route.fulfill({json:{ok:true,handle:'uixtestwrong',count:0}}));
  await start(page); await add(page,'uixtestinvalid');
  await expect(page.locator('#xAccountTestResult')).toHaveAttribute('data-status','failed');
  await expect(account(page,'uixtestinvalid')).toHaveCount(0);
  await expect(page.locator('#settingsSaveBtn')).toBeHidden();
});

for (const removeHistory of [false,true]) {
  test(`removal offers history choice and preserves unrelated drafts (delete: ${removeHistory})`, async ({page,request}) => {
    await request.post('/__test/x-fixture');
    await start(page);
    await add(page,'uixtestdraft');
    const remove = account(page,'uixtestcache').getByRole('button', {name:'移除账号 @uixtestcache'});
    await remove.click();
    const dialog = page.locator('#appConfirmDialog');
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole('radio', {name:'保留历史数据', exact:true})).toBeChecked();
    await expect(page.locator('#appConfirmCancel')).toBeFocused();
    await expect(page.locator('#appConfirmMessage')).toContainText('1 条新闻');
    await expect(page.locator('#appConfirmMessage')).toContainText('1 条想法');
    await page.keyboard.press('Escape');
    await expect(account(page,'uixtestcache')).toBeVisible();
    await remove.click();
    if (removeHistory) await dialog.getByRole('radio', {name:'同时删除历史数据'}).check();
    await expect(page.locator('#appConfirmAccept')).toHaveText(removeHistory ? '移除并删除数据' : '移除并保留数据');
    await page.locator('#appConfirmAccept').click();
    await expect(account(page,'uixtestcache')).toHaveCount(0);
    await expect(account(page,'uixtestdraft')).toBeVisible();
    expect((await saved(request)).x_accounts.some(a=>a.handle==='uixtestdraft')).toBe(false);
    const response = await request.get('/api/news/ui-x-prefetch/detail');
    expect(response.status()).toBe(removeHistory ? 404 : 200);
    await page.locator('#settingsSaveBtn').click();
    await expect(page.locator('#settingsStatus')).toHaveText('设置已保存');
    expect((await saved(request)).x_accounts.some(a=>a.handle==='uixtestcache')).toBe(false);
  });
}

test('prefetched full text stays outside read later until explicitly added', async ({page,request}) => {
  await request.post('/__test/x-fixture');
  await page.goto('/');
  const row = page.locator('#feedCardList [data-id="ui-x-prefetch"]');
  await expect(row).toBeVisible();
  const action = row.locator('[data-state="read_later"]');
  await expect(action).toHaveText('稍后再看'); await expect(action).toHaveAttribute('aria-pressed','false');
  const before = (await (await request.get('/api/news/ui-x-prefetch/detail')).json());
  expect(before.detail.content).toContain('自动预取'); expect(before.read_at).toBeNull(); expect(before.read_later_at).toBeNull();
  await action.click();
  await expect(action).toHaveText('全文待看');
  await expect(action).toHaveAttribute('aria-busy','false');
  expect((await (await request.get('/api/news/ui-x-prefetch/detail')).json()).read_later_at).toBeTruthy();
});

test('failed saves keep drafts and a delayed save cannot race account removal', async ({page,request}) => {
  await request.post('/__test/x-fixture'); await start(page);
  await add(page,'uixtestfailure');
  await page.route('**/api/settings', route => route.request().method() === 'PUT'
    ? route.fulfill({status:500,json:{error:'保存失败'}}) : route.continue());
  await page.locator('#settingsSaveBtn').click();
  await expect(page.locator('#settingsStatus')).toContainText('保存失败');
  await expect(account(page,'uixtestfailure')).toBeVisible();
  expect((await saved(request)).x_accounts.some(a=>a.handle==='uixtestfailure')).toBe(false);
  await page.unroute('**/api/settings');
  let release;
  const gate = new Promise(resolve=>{release=resolve;});
  await page.route('**/api/settings', async route => {
    if (route.request().method() === 'PUT') await gate;
    await route.continue();
  });
  await page.locator('#settingsSaveBtn').click();
  try {
    await expect(page.locator('#settingsSaveBtn')).toBeDisabled();
    await account(page,'uixtestcache').getByRole('button').click();
    await expect(page.locator('#toast')).toContainText('正在保存');
    await expect(page.locator('#appConfirmDialog')).toHaveCount(0);
    await account(page,'uixtestfailure').getByRole('switch',{name:'自动翻译 @uixtestfailure',exact:true}).check();
  } finally { release(); }
  await expect(page.locator('#settingsSaveBtn')).toBeEnabled();
  await expect(account(page,'uixtestfailure').getByRole('switch',{name:'自动翻译 @uixtestfailure',exact:true})).toBeChecked();
  expect((await saved(request)).x_accounts.find(a=>a.handle==='uixtestfailure').auto_translate).toBe(false);
  await page.locator('#settingsSaveBtn').click();
  await expect(page.locator('#settingsStatus')).toHaveText('设置已保存');
  expect((await saved(request)).x_accounts.find(a=>a.handle==='uixtestfailure').auto_translate).toBe(true);
});

test('editing a draft while removal statistics load leaves account actions usable', async ({page,request}) => {
  await request.post('/__test/x-fixture'); await start(page);
  let release;
  const gate = new Promise(resolve=>{release=resolve;});
  await page.route('**/api/settings/x-accounts/uixtestcache', async route => {
    await gate; await route.continue();
  });
  const response = page.waitForResponse('**/api/settings/x-accounts/uixtestcache');
  await account(page,'uixtestcache').getByRole('button').click();
  try { await add(page,'uixtestdraft'); }
  finally { release(); }
  await response;
  const remove = account(page,'uixtestcache').getByRole('button');
  await expect(remove).toBeEnabled();
  await expect(page.locator('#appConfirmDialog')).toHaveCount(0);
  await page.unroute('**/api/settings/x-accounts/uixtestcache');
  await remove.click();
  await expect(page.locator('#appConfirmDialog')).toBeVisible();
  await page.locator('#appConfirmCancel').click();
  await expect(account(page,'uixtestdraft')).toBeVisible();
});

for (const width of [1440,390]) for (const theme of ['light','dark','system','system-light','eink']) {
  test(`shared settings layout and removal dialog ${width} ${theme}`, async ({page,request}) => {
    await page.setViewportSize({width,height:850});
    await page.emulateMedia({colorScheme:theme === 'system-light' ? 'light' : 'dark'});
    await request.post('/__test/x-fixture');
    await start(page); await chooseTheme(page, theme === 'system-light' ? 'system' : theme);
    const toggle=account(page,'ilyasut').getByRole('switch',{name:'自动翻译 @ilyasut',exact:true});
    const size=await toggle.evaluate(el=>getComputedStyle(el.closest('label')).fontSize);
    await page.evaluate(()=>document.documentElement.style.setProperty('--reading-scale','1.3'));
    expect(await toggle.evaluate(el=>getComputedStyle(el.closest('label')).fontSize)).toBe(size);
    await toggle.focus(); await page.keyboard.press('Space'); await expect(toggle).toBeChecked();
    expect(await page.locator('#xAccountSettings').evaluate(el=>el.scrollWidth<=el.clientWidth)).toBe(true);
    const cards = await page.locator('[data-x-account]').evaluateAll(nodes => nodes.slice(0,2).map(node=>{
      const r=node.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height};
    }));
    if (width > 920) {
      expect(cards[0].y).toBe(cards[1].y); expect(cards[1].x).toBeGreaterThan(cards[0].x);
    } else {
      expect(cards[0].x).toBe(cards[1].x); expect(cards[1].y).toBeGreaterThan(cards[0].y);
    }
    expect(cards[0].height).toBeLessThan(190);
    await page.screenshot({path:`.ui-artifacts/x-accounts-${width}-${theme}.png`});
    await account(page,'uixtestcache').getByRole('button').click();
    await expect(page.locator('#appConfirmAccept')).toBeInViewport();
    expect(await page.locator('#appConfirmDialog').evaluate(el=>el.scrollWidth<=el.clientWidth)).toBe(true);
    await page.screenshot({path:`.ui-artifacts/x-remove-${width}-${theme}.png`});
    await page.locator('#appConfirmCancel').click();
    await expect(account(page,'uixtestcache').getByRole('button')).toBeFocused();
    await add(page,'uixtesttheme');
    const result=page.locator('#xAccountTestResult');
    await expect(result).toHaveAttribute('data-status','success');
    expect(await contrast(result)).toBeGreaterThanOrEqual(4.5);
    expect(await page.locator('#xAccountSettings').evaluate(el=>el.scrollWidth<=el.clientWidth)).toBe(true);
    await page.screenshot({path:`.ui-artifacts/x-test-success-${width}-${theme}.png`});
    await page.route('**/api/settings/x-accounts/test', route=>route.fulfill({status:422,json:{ok:false,error:'AUTH_REQUIRED'}}));
    await add(page,'uixtestdenied');
    await expect(result).toHaveAttribute('data-status','failed');
    expect(await contrast(result)).toBeGreaterThanOrEqual(4.5);
    await page.screenshot({path:`.ui-artifacts/x-test-failed-${width}-${theme}.png`});
  });
}

test('short phone removal choices stay reachable with keyboard and touch', async ({page,request}) => {
  await page.setViewportSize({width:320,height:568});
  await request.post('/__test/x-fixture');
  await start(page);
  await account(page,'uixtestcache').getByRole('button').click();
  const dialog=page.locator('#appConfirmDialog');
  await expect(page.locator('#appConfirmAccept')).toBeInViewport();
  const keep=dialog.getByRole('radio',{name:'保留历史数据',exact:true});
  await keep.focus(); await page.keyboard.press('ArrowDown');
  await expect(dialog.getByRole('radio',{name:'同时删除历史数据'})).toBeChecked();
  await expect(page.locator('#appConfirmAccept')).toHaveText('移除并删除数据');
  await page.keyboard.press('Tab');
  await expect(page.locator('#appConfirmCancel')).toBeFocused();
  expect(await dialog.evaluate(el=>el.scrollWidth<=el.clientWidth)).toBe(true);
  await page.screenshot({path:'.ui-artifacts/x-remove-short-phone.png'});
  await page.keyboard.press('Escape');
  await expect(account(page,'uixtestcache').getByRole('button')).toBeFocused();
});
