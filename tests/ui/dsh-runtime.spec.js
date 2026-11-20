import {test, expect} from '@playwright/test';
import {choosePicker, chooseTheme, openSettings, openSettingsItem} from './helpers.js';

const catalog = {options:[{provider:'fixture',value:'news-model',label:'news-model',reasoning_efforts:['low','high']}],error:'',notice:'已从 DeepSeek Harness 桌面版 0.2.2-rc.5 读取 1 个模型。'};

for (const width of [390, 1440]) test('DSH 目录、连接和安装指引 ' + width, async ({page}) => {
  await page.setViewportSize({width,height:900});
  let probes = 0, installed = false;
  await page.route('**/api/settings/models', async route => {
    if (route.request().postDataJSON()?.runtime !== 'dsh') return route.continue();
    probes++;
    await route.fulfill({json:{ok:true,catalog:installed ? catalog : {options:[],error:'oar_dsh_missing',notice:'可安装 DeepSeek Harness 桌面版或设置 OAR_DSH_BIN。'}}});
  });
  await page.route('**/api/settings/connection-test', async route => {
    const data = route.request().postDataJSON();
    expect(data.runtime).toBe('dsh');
    expect(data.dsh).toMatchObject({provider:'fixture',model:'news-model',reasoning_effort:'high'});
    await route.fulfill({json:{ok:true,status:'success',message:'本次模型请求成功。',elapsed_ms:123}});
  });
  await page.goto('/');
  await openSettings(page); await openSettingsItem(page, 'models');
  expect(probes).toBe(0);
  await choosePicker(page, 'Runtime', 'DSH', 'chat');
  await expect(page.locator('#modelsNotice')).toContainText('OAR_DSH_BIN');
  await expect(page.locator('#testConnectionBtn')).toBeDisabled();
  installed = true;
  await page.locator('#refreshModelsBtn').click();
  await expect(page.locator('#modelsNotice')).toContainText('DeepSeek Harness 桌面版 0.2.2-rc.5');
  await choosePicker(page, '服务商', 'fixture', 'chat');
  await choosePicker(page, '模型', 'news-model', 'chat');
  await choosePicker(page, '推理强度', 'high', 'chat');
  await page.locator('#testConnectionBtn').click();
  await expect(page.locator('#connTestTerminal')).toContainText('成功');
  for (const [theme,scheme] of [['light','light'],['dark','dark'],['system','dark'],['eink','light']]) {
    await page.emulateMedia({colorScheme:scheme});
    await chooseTheme(page,theme);
    const picker = page.locator('[data-model-feature="chat"]');
    await expect(picker.getByRole('combobox',{name:'Runtime',exact:true})).toHaveText('DSH');
    expect(await picker.evaluate(el=>el.scrollWidth<=el.clientWidth+1)).toBe(true);
  }
});
