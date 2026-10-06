import { test, expect } from '@playwright/test';

test.describe('Routing & Navigation', () => {
  test('unauthenticated /dev redirects to /login', async ({ page }) => {
    await page.goto('/dev');
    await page.waitForURL('**/login');
    await expect(page).toHaveTitle('Aspiring Investments');
    await expect(page.locator('h2:has-text("Aspiring Investments")')).toBeVisible();
    await expect(page.locator('input[name="email"]')).toBeVisible();
    await expect(page.locator('input[name="password"]')).toBeVisible();
    await expect(page.locator('input[type="submit"][value="Login"]')).toBeVisible();
    await expect(page.locator('a[href="/register"]')).toBeVisible();
  });

  test('unauthenticated /save redirects away from dashboard', async ({ page }) => {
    await page.goto('/save');
    // SaveHandler redirects to /dev which redirects to /login
    await page.waitForURL(/\/(dev|login)/);
  });

  test('unknown route returns 404', async ({ page }) => {
    const response = await page.goto('/this_route_definitely_does_not_exist');
    expect(response?.status()).toBe(404);
  });

  test('login page is accessible at /login', async ({ page }) => {
    const response = await page.goto('/login');
    expect(response?.status()).toBe(200);
    await expect(page.locator('input[value="Login"]')).toBeVisible();
  });

  test('register page is accessible at /register', async ({ page }) => {
    const response = await page.goto('/register');
    expect(response?.status()).toBe(200);
    await expect(page.locator('input[value="Register"]')).toBeVisible();
  });
});
