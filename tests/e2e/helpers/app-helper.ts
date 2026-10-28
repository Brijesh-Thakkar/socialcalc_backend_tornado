import { Page, expect } from '@playwright/test';

export function generateUniqueUser() {
  const randomId = Date.now() + Math.floor(Math.random() * 10000);
  return {
    email: `testuser_${randomId}@example.com`,
    password: `TestPassword123!`,
  };
}

export async function registerUser(page: Page, email: string, password: string) {
  await page.goto('/register');
  await page.fill('input[name="email"]', email);
  await page.fill('input[name="password"]', password);
  await page.fill('input[name="repassword"]', password);
  await page.click('input[type="submit"][value="Register"]');
  await expect(page.locator('h2:has-text("Registration Complete")')).toBeVisible();
}

export async function loginUser(page: Page, email: string, password: string) {
  await page.goto('/login');
  await page.fill('input[name="email"]', email);
  await page.fill('input[name="password"]', password);
  await page.click('input[type="submit"][value="Login"]');
  await page.waitForURL('**/save');
  await expect(page.locator('h2:has-text("Aspiring Investments")')).toBeVisible();
}

export async function logoutUser(page: Page) {
  const logoutLink = page.locator('a:has-text("logout")');
  if (await logoutLink.isVisible()) {
    await logoutLink.click();
  } else {
    await page.goto('/logout');
  }
  await page.waitForURL('**/login');
  await expect(page.locator('input[value="Login"]')).toBeVisible();
}

export async function openSpreadsheet(page: Page, filename: string) {
  await page.goto('/save');
  const row = page.locator('tr', { hasText: filename });
  await expect(row).toBeVisible();
  await row.locator('input[value="Edit"]').click();
  await waitForSpreadsheetReady(page);
}

/**
 * Waits until the SocialCalc editor is initialised and idle.
 * Polls editor.busy === false and editor.state === 'start'.
 */
export async function waitForSpreadsheetReady(page: Page) {
  await page.waitForSelector('#sheetdata', { state: 'attached' });
  await page.waitForFunction(() => {
    const sc = (window as any).spreadsheet;
    return (
      sc !== undefined &&
      sc.editor !== undefined &&
      sc.editor.busy === false &&
      sc.editor.state === 'start'
    );
  }, { timeout: 30000 });
}

export async function editSpreadsheetCell(page: Page, cell: string, value: string) {
  await page.evaluate(
    ({ cell, value }) => {
      (window as any).spreadsheet.editor.EditorScheduleSheetCommands(
        `set ${cell} text t ${value}`,
        true,
        false
      );
    },
    { cell, value }
  );
  await page.waitForFunction(
    () => (window as any).spreadsheet.editor.busy === false,
    { timeout: 10000 }
  );
}

export async function getSpreadsheetCellValue(page: Page, cell: string): Promise<string | null> {
  return await page.evaluate((cellName) => {
    const sc = (window as any).spreadsheet;
    if (!sc || !sc.sheet) return null;
    const c = sc.sheet.cells[cellName];
    return c ? (c.datavalue !== undefined ? String(c.datavalue) : null) : null;
  }, cell);
}

/**
 * Saves the current spreadsheet via the Save tab.
 * SocialCalc assigns id = "SocialCalc-saveastab" to the Save tab element.
 */
export async function saveSpreadsheetAs(page: Page, filename: string) {
  // The Save tab td has id ending in "saveastab" (pattern: %id.saveastab)
  await page.locator('td[id$="saveastab"]').click();
  await page.waitForSelector('#saveasentry', { state: 'visible' });
  await page.fill('#saveasentry', filename);

  // savecheck() calls $.postJSON("/save", ...) and alerts "Done"
  const dialogPromise = page.waitForEvent('dialog');
  await page.evaluate(() => (window as any).savecheck());
  const dialog = await dialogPromise;
  expect(dialog.message()).toContain('Done');
  await dialog.accept();
}
