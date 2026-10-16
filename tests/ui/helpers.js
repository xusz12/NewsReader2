export async function chooseTheme(page, value) {
  const label = await page.locator(`#themeSelect-native option[value="${value}"]`).textContent();
  await page.getByRole('combobox', { name: '外观主题', exact: true }).click();
  await page.getByRole('option', { name: label, exact: true }).click();
}
