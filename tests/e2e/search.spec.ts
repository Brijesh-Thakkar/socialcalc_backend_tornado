import { test, expect } from '@playwright/test';
import {
  openSpreadsheet,
  saveSpreadsheetAs,
  generateUniqueUser,
  registerUser,
  loginUser,
} from './helpers/app-helper';

test.describe('Dashboard Search functionality', () => {
  test('should filter spreadsheets by case-insensitive query and prefill search input', async ({ page }) => {
    const credentials = generateUniqueUser();
    
    // Step 1: Register and login a fresh user
    await registerUser(page, credentials.email, credentials.password);
    await loginUser(page, credentials.email, credentials.password);

    // Step 2: Create additional sheets
    await openSpreadsheet(page, 'default');
    await saveSpreadsheetAs(page, 'Finance_2026');
    
    await openSpreadsheet(page, 'default');
    await saveSpreadsheetAs(page, 'Budget_2026');
    
    await openSpreadsheet(page, 'default');
    await saveSpreadsheetAs(page, 'Tax_Report');

    // Step 3: Go to dashboard and check all are visible
    await page.goto('/save');
    await expect(page.locator('tr', { hasText: 'default' })).toBeVisible();
    await expect(page.locator('tr', { hasText: 'Finance_2026' })).toBeVisible();
    await expect(page.locator('tr', { hasText: 'Budget_2026' })).toBeVisible();
    await expect(page.locator('tr', { hasText: 'Tax_Report' })).toBeVisible();

    // Step 4: Perform search for '2026'
    await page.fill('input[name="q"]', '2026');
    await page.click('input[type="submit"][value="Search"]');

    // Step 5: Verify URL redirects/submits to /search?q=2026
    await page.waitForURL('**/search?q=2026');

    // Step 6: Verify filtering results
    await expect(page.locator('tr', { hasText: 'Finance_2026' })).toBeVisible();
    await expect(page.locator('tr', { hasText: 'Budget_2026' })).toBeVisible();
    await expect(page.locator('tr', { hasText: 'default' })).not.toBeVisible();
    await expect(page.locator('tr', { hasText: 'Tax_Report' })).not.toBeVisible();

    // Step 7: Verify prefilled query input
    await expect(page.locator('input[name="q"]')).toHaveValue('2026');

    // Step 8: Verify case-insensitive search with 'finance'
    await page.fill('input[name="q"]', 'finance');
    await page.click('input[type="submit"][value="Search"]');
    await page.waitForURL('**/search?q=finance');
    
    await expect(page.locator('tr', { hasText: 'Finance_2026' })).toBeVisible();
    await expect(page.locator('tr', { hasText: 'Budget_2026' })).not.toBeVisible();
    await expect(page.locator('tr', { hasText: 'default' })).not.toBeVisible();
    await expect(page.locator('tr', { hasText: 'Tax_Report' })).not.toBeVisible();
    await expect(page.locator('input[name="q"]')).toHaveValue('finance');
  });
});
