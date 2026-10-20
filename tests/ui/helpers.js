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
