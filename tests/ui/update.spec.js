import { test, expect } from '@playwright/test';

const current = { ok: true, version: 'v0.0.6', commit: 'a'.repeat(40), repository: 'https://github.com/xusz12/NewsReader2.git' };
const latest = { tag: 'v0.0.7', version: 'v0.0.7', commit: 'b'.repeat(40) };
const available = { ok: true, enabled: true, current_version: current.version, current_commit: current.commit,
  repository: current.repository, latest, available: true, can_update: true, managed_launcher: true,
  check_token: 'checked-target', blockers: [], checked_at: 1792152000, status: 'update_available' };

async function setup(page, checked = available) {
  await page.route('**/api/version', route => route.fulfill({ json: current }));
  await page.route('**/api/update/status', route => route.fulfill({ json: { ok: true, status: 'idle' } }));
  await page.route('**/api/update/check?force=1', route => route.fulfill({ json: checked }));
  await page.goto('/');
  await expect(page.locator('#feedCardList .news-card').first()).toBeVisible();
  await openSystem(page);
}
async function openSystem(page) {
  const small = page.viewportSize().width <= 920;
  await page.locator(small ? '#mobNavSettings' : '#leftNavSettingsBtn').click();
  await page.locator('[data-settings-tab="system"]').click();
  await expect(page.locator('#updateRepository')).toHaveText(current.repository);
}

for (const [status, text, remote] of [
  ['no_releases', '尚未发布稳定版本', null],
  ['up_to_date', '已是最新稳定版本', { ...latest, version: 'v0.0.6' }],
  ['ahead', '高于已发布的 v0.0.5', { ...latest, version: 'v0.0.5' }],
]) {
  test(`update distinguishes ${status}`, async ({ page }) => {
    await setup(page, { ...available, available: false, status, latest: remote, check_token: undefined });
    await page.locator('#checkUpdateBtn').click();
    await expect(page.locator('#updateResult')).toContainText(text);
    await expect(page.locator('#applyUpdateBtn')).toBeHidden();
  });
}

test('update blockers explain local changes and launcher requirements', async ({ page }) => {
  await setup(page, { ...available, can_update: false, blockers: ['dirty_worktree', 'managed_launcher_required'] });
  await page.locator('#checkUpdateBtn').click();
  await expect(page.locator('#updateResult')).toContainText('未提交的修改');
  await expect(page.locator('#updateResult')).toContainText('启动NewsReader.command');
  await expect(page.locator('#applyUpdateBtn')).toBeHidden();
});

test('failed recheck clears the previous update action and can be retried', async ({ page }) => {
  await setup(page);
  await page.locator('#checkUpdateBtn').click();
  await expect(page.locator('#applyUpdateBtn')).toBeVisible();
  await page.route('**/api/update/check?force=1', route => route.fulfill({ status: 502, json: { ok: false, error: 'update_check_failed' } }));
  await page.locator('#checkUpdateBtn').click();
  await expect(page.locator('#updateResult')).toContainText('无法读取 GitHub');
  await expect(page.locator('#applyUpdateBtn')).toBeHidden();
  await expect(page.locator('#checkUpdateBtn')).toBeEnabled();
});

test('unsaved settings prevent applying an update', async ({ page }) => {
  await setup(page);
  await page.locator('[data-settings-tab="deepseek"]').click();
  await page.locator('input[name="llm.translation.model"]').fill('unsaved-test-model');
  await page.locator('[data-settings-tab="system"]').click();
  await page.locator('#checkUpdateBtn').click();
  let submitted = false;
  await page.route('**/api/update/apply', route => { submitted = true; return route.abort(); });
  await page.locator('#applyUpdateBtn').click();
  await expect(page.locator('#updateResult')).toContainText('请先保存或重置');
  expect(submitted).toBe(false);
});

test('rollback progress survives navigating away and is shown in Chinese', async ({ page }) => {
  await setup(page);
  let applied = 0, complete = false;
  await page.route('**/api/update/apply', async route => {
    expect(route.request().postDataJSON()).toEqual({ check_token: 'checked-target', ...latest });
    applied++;
    await route.fulfill({ status: 202, json: { ok: true, run_id: 'rollback-run' } });
  });
  await page.route('**/api/update/status', route => route.fulfill({ json: applied ? {
    ok: true, run_id: 'rollback-run', status: complete ? 'rolled_back' : 'fetching', error: complete ? 'health_check_failed' : undefined,
  } : { ok: true, status: 'idle' } }));
  await page.locator('#checkUpdateBtn').click();
  page.once('dialog', dialog => dialog.accept());
  await page.locator('#applyUpdateBtn').click();
  await expect(page.locator('#updateResult')).toContainText('正在下载目标版本');
  await expect(page.locator('#checkUpdateBtn')).toBeDisabled();
  await page.locator('#closeSettingsBtn').click();
  await openSystem(page);
  await expect(page.locator('#checkUpdateBtn')).toBeDisabled();
  complete = true;
  await expect(page.locator('#updateResult')).toContainText('已恢复旧版本');
  await expect(page.locator('#checkUpdateBtn')).toBeEnabled();
  await expect(page.locator('#refreshUpdateBtn')).toBeHidden();
  expect(applied).toBe(1);
});

for (const width of [360, 1440]) {
  test(`successful update resumes after reload and verifies the running version at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await setup(page);
    let applied = false, complete = false, servingTarget = false;
    await page.route('**/api/update/apply', route => {
      applied = true;
      return route.fulfill({ status: 202, json: { ok: true, run_id: 'success-run' } });
    });
    await page.route('**/api/update/status', route => route.fulfill({ json: applied ? {
      ok: true, run_id: 'success-run', status: complete ? 'healthy' : 'fetching', ...latest,
    } : { ok: true, status: 'idle' } }));
    await page.route('**/api/version', route => route.fulfill({ json: servingTarget ? { ...current, ...latest } : current }));
    await page.locator('#checkUpdateBtn').click();
    page.once('dialog', dialog => dialog.accept());
    await page.locator('#applyUpdateBtn').click();
    await expect(page.locator('#updateResult')).toContainText('正在下载目标版本');
    await page.reload();
    await expect(page.locator('#feedCardList .news-card').first()).toBeVisible();
    await openSystem(page);
    await expect(page.locator('#checkUpdateBtn')).toBeDisabled();
    complete = true;
    await expect(page.locator('#updateResult')).toContainText('等待目标版本');
    await expect(page.locator('#refreshUpdateBtn')).toBeHidden();
    servingTarget = true;
    await expect(page.locator('#updateResult')).toContainText('更新完成');
    await expect(page.locator('#updateCurrent')).toHaveText('News Reader v0.0.7');
    await expect(page.locator('#refreshUpdateBtn')).toBeVisible();
    expect(await page.evaluate(() => localStorage.getItem('newsreader:update-run'))).toBe('');
    expect(await page.locator('[data-section="system"]').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    await page.screenshot({ path: `.ui-artifacts/v006-update-success-${width}.png`, animations: 'disabled' });
  });
}
