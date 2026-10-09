import { test, expect } from './fixtures/auth.fixture';
import {
  openSpreadsheet,
  editSpreadsheetCell,
  getSpreadsheetCellValue,
  saveSpreadsheetAs,
  generateUniqueUser,
  registerUser,
  loginUser,
} from './helpers/app-helper';

test.describe('Spreadsheet Lifecycle', () => {
  test('open, edit, save as new name, and reload', async ({ authenticatedPage }) => {
    const ts = Date.now();
    const newSheetName = `sheet_${ts}`;
    const testCell = 'A1';
    const testValue = 'Playwright Test Value';

    await test.step('Open default spreadsheet', async () => {
      await openSpreadsheet(authenticatedPage, 'default');
    });

    await test.step('Edit cell A1', async () => {
      await editSpreadsheetCell(authenticatedPage, testCell, testValue);
      const val = await getSpreadsheetCellValue(authenticatedPage, testCell);
      expect(val).toBe(testValue);
    });

    await test.step('Save spreadsheet under new name', async () => {
      await saveSpreadsheetAs(authenticatedPage, newSheetName);
    });

    await test.step('Dashboard shows new sheet', async () => {
      await authenticatedPage.goto('/save');
      await expect(authenticatedPage.locator('tr', { hasText: newSheetName })).toBeVisible();
    });

    await test.step('Reload the saved sheet and verify cell value', async () => {
      await openSpreadsheet(authenticatedPage, newSheetName);
      const val = await getSpreadsheetCellValue(authenticatedPage, testCell);
      expect(val).toBe(testValue);
    });
  });

  test('delete a spreadsheet removes it from dashboard', async ({ authenticatedPage }) => {
    const sheetName = `todelete_${Date.now()}`;

    // Create the sheet first
    await openSpreadsheet(authenticatedPage, 'default');
    await saveSpreadsheetAs(authenticatedPage, sheetName);

    // Verify it's listed
    await authenticatedPage.goto('/save');
    await expect(authenticatedPage.locator('tr', { hasText: sheetName })).toBeVisible();

    // Delete it — click Delete button in the row
    const row = authenticatedPage.locator('tr', { hasText: sheetName });
    await row.locator('input[value="Delete"]').click();
    await authenticatedPage.waitForURL('**/save');

    // Verify it's gone from the dashboard
    await expect(authenticatedPage.locator('tr', { hasText: sheetName })).not.toBeVisible();
  });

  test('spreadsheet loads static JS and initialises SocialCalc', async ({ authenticatedPage }) => {
    await openSpreadsheet(authenticatedPage, 'default');
    const hasSpreadsheet = await authenticatedPage.evaluate(() => {
      return typeof (window as any).spreadsheet !== 'undefined' &&
             typeof (window as any).workbook !== 'undefined';
    });
    expect(hasSpreadsheet).toBe(true);
  });

  test('autosave filename holder element exists in spreadsheet page', async ({ authenticatedPage }) => {
    await openSpreadsheet(authenticatedPage, 'default');
    await expect(authenticatedPage.locator('#filenameholder')).toBeAttached();
  });

  test('default sheet auto-created on first login', async ({ page }) => {
    const credentials = generateUniqueUser();
    await registerUser(page, credentials.email, credentials.password);
    await loginUser(page, credentials.email, credentials.password);
    // SaveHandler auto-creates 'default' sheet if user has no files
    await expect(page.locator('tr', { hasText: 'default' })).toBeVisible();
  });
});
