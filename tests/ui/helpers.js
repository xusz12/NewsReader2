export async function chooseTheme(page, value) {
  const label = await page.locator(`#themeSelect-native option[value="${value}"]`).textContent();
  await page.getByRole('combobox', { name: '外观主题', exact: true }).click();
  await page.getByRole('option', { name: label, exact: true }).click();
}

export async function openNavigation(page) {
  const menu = page.locator('#openNavBtn');
  if (await menu.isVisible() && !(await page.locator('#leftNavDrawer').evaluate(node => node.classList.contains('drawer-open')))) {
    await menu.click();
  }
}

export async function openSettings(page) {
  await openNavigation(page);
  await page.locator('#leftNavSettingsBtn').click();
}

export async function answerConfirm(page, accept = true) {
  const dialog = page.locator('#appConfirmDialog');
  await dialog.waitFor({ state: 'visible' });
  const message = await dialog.locator('#appConfirmMessage').innerText();
  await dialog.locator(accept ? '#appConfirmAccept' : '#appConfirmCancel').click();
  return message;
}

// Most UI fixtures describe a single batch. Explicit batch data can differ from
// library totals when testing the distinction between the two progress views.
export function withClassificationBatch(progress) {
  return {...progress, batch: progress.batch || Object.fromEntries(
    ['total','done','pending','running','failed'].map(key=>[key, progress[key] || 0]))};
}

export async function choosePicker(page, name, label) {
  await page.getByRole('combobox', {name, exact:true}).click();
  await page.getByRole('option', {name:label, exact:true}).click();
}
export async function openModelAdvanced(page) {
  const advanced = page.locator('#modelAdvanced');
  if (!await advanced.evaluate(node=>node.open)) await advanced.locator('summary').click();
}
