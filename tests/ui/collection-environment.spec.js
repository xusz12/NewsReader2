import { test, expect } from '@playwright/test';
import { openSettings, chooseTheme, contrast } from './helpers.js';

const checked_at = '2026-11-10T03:00:00+00:00';
const environment = {ok:true, checked_at, checks:[
  {id:'runtime',status:'success',message:'opencli 2.0.3；使用 NewsReader 锁定的项目内版本。'},
  {id:'adapters',status:'success',message:'12 项必需命令与内置 adapter 校验通过；不加载个人 adapter 或插件。'},
  {id:'chrome',status:'success',message:'已通过浏览器连接检查；采集期间请保持 Chrome 运行。'},
  {id:'bridge',status:'success',message:'OpenCLI 浏览器扩展 1.0.27 已连接。'},
]};
const x = {ok:true, checked_at, checks:[{id:'x',status:'success',message:'已验证当前浏览器中的 X 登录会话。'}]};
const mixed = {...environment, checks: environment.checks.map(row => row.id === 'bridge'
  ? {...row,status:'failed',message:'浏览器扩展未连接。'} : row.id === 'chrome'
    ? {...row,status:'unknown',message:'浏览器连接尚未确认。'} : row)};

test.beforeEach(async ({page}) => {
  await page.route('**/api/collection/environment', route => route.fulfill({json:{ok:true,checks:[]}}));
});

async function start(page) {
  await page.goto('/');
  await expect(page.locator('#feedCardList .news-card').first()).toBeVisible();
  await openSettings(page);
  await page.locator('[data-settings-tab="pipeline"]').click();
  await expect(page.locator('#collectionServicesTitle')).toHaveText('采集服务配置');
}

test('checks are explicit, retain settings drafts and invalidate account results after browser checks', async ({page}) => {
  let calls = 0;
  await page.route('**/api/collection/environment/check', async route => {
    calls++;
    await route.fulfill({json:route.request().postDataJSON().target === 'x' ? x : environment});
  });
  await start(page);
  expect(calls).toBe(0);
  await expect(page.locator('[data-environment-check="x"]')).toContainText('未检查');
  await page.locator('[name="collection.daily"]').fill('07:00、19:00');
  await page.locator('#checkCollectionXBtn').click();
  await expect(page.locator('[data-environment-check="x"]')).toContainText('通过');
  await page.locator('#checkCollectionEnvironmentBtn').click();
  await expect(page.locator('[data-environment-check="bridge"]')).toContainText('已连接');
  await expect(page.locator('[data-environment-check="x"]')).toContainText('待复查（上次通过）');
  await expect(page.locator('[data-environment-check="x"] .status-dot')).toHaveAttribute('data-status','unchecked');
  await expect(page.locator('[data-environment-check="x"]')).toContainText('检查于');
  await expect(page.locator('[name="collection.daily"]')).toHaveValue('07:00、19:00');
  await expect(page.locator('#settingsSaveBtn')).toBeVisible();
  await page.locator('#collectionApiSettingsBtn').click();
  await expect(page.locator('#settingsDetailTitle')).toHaveText('API 管理');
  await expect(page.locator('#settingsDetailTitle')).toBeFocused();
  await page.locator('[data-settings-tab="pipeline"]').click();
  await expect(page.locator('[name="collection.daily"]')).toHaveValue('07:00、19:00');
  await expect(page.locator('[data-environment-check="runtime"]')).toContainText('2.0.3');
  expect(calls).toBe(2);
});

test('failed check retains timestamped results and never claims current readiness', async ({page}) => {
  let fail = false;
  await page.route('**/api/collection/environment/check', route => route.fulfill(fail
    ? {status:503,json:{ok:false,error:'private failure'}} : {json:environment}));
  await start(page);
  await page.locator('#checkCollectionEnvironmentBtn').click();
  await expect(page.locator('[data-environment-check="runtime"]')).toContainText('检查于');
  fail = true;
  await page.locator('#checkCollectionEnvironmentBtn').click();
  await expect(page.locator('#collectionEnvironmentNotice')).toContainText('已有结果保留');
  await expect(page.locator('#collectionEnvironmentNotice')).not.toContainText('private');
  await expect(page.locator('[data-environment-check="runtime"]')).toContainText('2.0.3');
  await expect(page.locator('#checkCollectionEnvironmentBtn')).toBeEnabled();
  await expect(page.locator('#checkCollectionXBtn')).toBeEnabled();
});

test('pending checks disable duplicates and survive navigation without saving drafts', async ({page}) => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  await page.route('**/api/collection/environment/check', async route => {
    await pending;
    await route.fulfill({json:environment});
  });
  try {
    await start(page);
    await page.locator('[name="collection.translation_concurrency"]').fill('7');
    await page.locator('#checkCollectionEnvironmentBtn').click();
    await expect(page.locator('#checkCollectionXBtn')).toBeDisabled();
    await expect(page.locator('#checkCollectionEnvironmentBtn')).toHaveAttribute('aria-busy','true');
    await page.locator('#collectionApiSettingsBtn').click();
    release();
    await page.locator('[data-settings-tab="pipeline"]').click();
    await expect(page.locator('[data-environment-check="runtime"]')).toContainText('2.0.3');
    await expect(page.locator('[name="collection.translation_concurrency"]')).toHaveValue('7');
    await expect(page.locator('#checkCollectionEnvironmentBtn')).toBeEnabled();
  } finally { release(); }
});

test('services show saved routing, distinguish configured from tested and reuse configuration pages', async ({page}) => {
  await start(page);
  await expect(page.locator('[data-collection-service="deepseek"]')).toContainText('缺少 API 密钥');
  await expect(page.locator('#collectionServices')).toContainText('配置齐全不代表连接成功');
  await page.locator('#collectionApiSettingsBtn').click();
  await page.locator('[name="llm.translation.provider"][value="opencode-go"]').check();
  await page.locator('[data-settings-tab="pipeline"]').click();
  await expect(page.locator('[data-collection-service]').first()).toHaveAttribute('data-collection-service','deepseek');
  await expect(page.locator('#settingsSaveBtn')).toBeVisible();
  await page.locator('#collectionModelSettingsBtn').click();
  await expect(page.locator('#settingsDetailTitle')).toHaveText('模型与 Agent');
  await expect(page.locator('#settingsDetailTitle')).toBeFocused();
});

test('the latest check results and timestamps restore after reloading without running diagnostics', async ({page}) => {
  let saved = [], checks = 0;
  await page.route('**/api/collection/environment', route => route.fulfill({json:{ok:true,checks:saved}}));
  await page.route('**/api/collection/environment/check', async route => {
    checks++;
    saved = mixed.checks.map(row => ({...row,checked_at,stale:false}));
    await route.fulfill({json:{...mixed,results:saved}});
  });
  await start(page);
  await page.locator('#checkCollectionEnvironmentBtn').click();
  await expect(page.locator('[data-environment-check="bridge"]')).toContainText('未通过');
  await page.reload();
  await openSettings(page);
  await page.locator('[data-settings-tab="pipeline"]').click();
  await expect(page.locator('[data-environment-check="runtime"]')).toContainText('通过');
  await expect(page.locator('[data-environment-check="bridge"]')).toContainText('未通过');
  await expect(page.locator('[data-environment-check="bridge"]')).toContainText('2026/11/10 11:00:00');
  expect(checks).toBe(1);
});

test('a delayed history response cannot replace a newer manual check', async ({page}) => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  await page.route('**/api/collection/environment', async route => {
    await pending;
    await route.fulfill({json:{ok:true,checks:mixed.checks.map(row=>({...row,checked_at}))}});
  });
  await page.route('**/api/collection/environment/check', route => route.fulfill({json:environment}));
  try {
    await start(page);
    await page.locator('#checkCollectionEnvironmentBtn').click();
    await expect(page.locator('[data-environment-check="bridge"]')).toContainText('已连接');
    const restored = page.waitForResponse(response=>response.url().endsWith('/api/collection/environment'));
    release(); await restored;
    await expect(page.locator('[data-environment-check="bridge"]')).toContainText('已连接');
  } finally { release(); }
});

for (const width of [1440,390]) for (const appearance of ['light','dark','system-light','system-dark','eink']) {
  test.describe(`${width} ${appearance}`, () => {
    test.use({hasTouch:width === 390});
    test('environment and service cards reuse shared layout and stay stable across reading sizes', async ({page}) => {
      await page.setViewportSize({width,height:900});
      if (appearance.startsWith('system-')) await page.emulateMedia({colorScheme:appearance.slice(7)});
      await page.route('**/api/collection/environment/check', route => route.fulfill({json:mixed}));
      await start(page);
      await chooseTheme(page,appearance.startsWith('system-') ? 'system' : appearance);
      await page.locator('#checkCollectionEnvironmentBtn').click();
      await expect(page.locator('[data-environment-check="bridge"]')).toContainText('未通过');
      const success = page.locator('[data-environment-check="runtime"] .status-text');
      const failure = page.locator('[data-environment-check="bridge"] .status-text');
      await expect(success).toHaveAttribute('data-status','success');
      await expect(failure).toHaveAttribute('data-status','failed');
      expect(await contrast(success)).toBeGreaterThanOrEqual(4.5);
      expect(await contrast(failure)).toBeGreaterThanOrEqual(4.5);
      const colors = await page.evaluate(() => {
        const sample = document.createElement('span');document.body.append(sample);
        sample.style.color='color-mix(in oklab, var(--status-success) 60%, var(--fg))';
        const success=getComputedStyle(sample).color;
        sample.style.color='color-mix(in oklab, var(--status-danger) 60%, var(--fg))';
        const failure=getComputedStyle(sample).color;sample.remove();return {success,failure};
      });
      await expect(success).toHaveCSS('color',colors.success);
      await expect(failure).toHaveCSS('color',colors.failure);
      const dots = {success:page.locator('[data-environment-check="runtime"] .status-dot'),
        failed:page.locator('[data-environment-check="bridge"] .status-dot'),
        unknown:page.locator('[data-environment-check="chrome"] .status-dot'),
        unchecked:page.locator('[data-environment-check="x"] .status-dot')};
      const dotColors = await page.evaluate(() => {
        const sample=document.createElement('span');document.body.append(sample);
        const colors=Object.fromEntries(Object.entries({success:'--status-success',failed:'--status-danger',unknown:'--status-danger',unchecked:'--muted'}).map(([status,token])=>{
          sample.style.backgroundColor=`var(${token})`;return [status,getComputedStyle(sample).backgroundColor];
        }));sample.remove();return colors;
      });
      await expect(page.locator('#collectionEnvironmentResults .status-dot')).toHaveCount(5);
      for(const [status,dot] of Object.entries(dots)) {
        await expect(dot).toHaveAttribute('data-status',status);
        await expect(dot).toHaveAttribute('aria-hidden','true');
        await expect(dot).toHaveCSS('background-color',dotColors[status]);
        expect(await dot.evaluate(node=>node === node.parentElement.firstElementChild)).toBe(true);
      }
      for (const scale of [.9,1,1.15,1.3]) {
        await page.evaluate(scale => document.documentElement.style.setProperty('--reading-scale',String(scale)),scale);
        const styles = await page.evaluate(() => {
          const first = document.querySelector('#collectionEnvironment'), services = document.querySelector('#collectionServices');
          const original = document.querySelector('[name="collection.daily"]').closest('.settings-section-card');
          const style = node => { const s=getComputedStyle(node); return [s.padding,s.backgroundColor,s.borderRadius,s.fontSize]; };
          return {first:style(first),services:style(services),original:style(original),overflow:[first,services].some(n=>n.scrollWidth>n.clientWidth),
            labels:[first,services,original].map(n=>getComputedStyle(n.querySelector('.field-label')).fontSize)};
        });
        expect(styles.first).toEqual(styles.original);
        expect(styles.services).toEqual(styles.original);
        expect(styles.overflow).toBe(false);
        expect(new Set(styles.labels).size).toBe(1);
      }
      await page.locator('#checkCollectionEnvironmentBtn').focus();
      await page.keyboard.press('Tab');
      await expect(page.locator('#checkCollectionXBtn')).toBeFocused();
      await expect(page.locator('#checkCollectionXBtn')).toHaveCSS('outline-width','2px');
      await page.screenshot({path:`.ui-artifacts/collection-environment-${width}-${appearance}.png`});
      await page.locator('#collectionServicesTitle').scrollIntoViewIfNeeded();
      await page.screenshot({path:`.ui-artifacts/collection-services-${width}-${appearance}.png`});
    });
  });
}
