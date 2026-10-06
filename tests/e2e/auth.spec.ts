import { test, expect } from '@playwright/test';
import { generateUniqueUser, registerUser, loginUser, logoutUser } from './helpers/app-helper';

test.describe('Authentication Flow', () => {
  test('register, login, and logout a new user', async ({ page }) => {
    const credentials = generateUniqueUser();

    await test.step('Register new user', async () => {
      await registerUser(page, credentials.email, credentials.password);
    });

    await test.step('Log in with new user', async () => {
      await loginUser(page, credentials.email, credentials.password);
    });

    await test.step('Log out user', async () => {
      await logoutUser(page);
    });
  });

  test('reject invalid login credentials', async ({ page }) => {
    await page.goto('/login');
    await page.fill('input[name="email"]', 'nobody@nonexistent.invalid');
    await page.fill('input[name="password"]', 'wrongpassword');
    await page.click('input[type="submit"][value="Login"]');
    // Failed login redirects back to /login
    await page.waitForURL('**/login');
    await expect(page.locator('input[value="Login"]')).toBeVisible();
  });

  test('reject duplicate registration', async ({ page }) => {
    const credentials = generateUniqueUser();
    // First registration succeeds
    await registerUser(page, credentials.email, credentials.password);
    // Second registration with same email shows "exists" page
    await page.goto('/register');
    await page.fill('input[name="email"]', credentials.email);
    await page.fill('input[name="password"]', credentials.password);
    await page.fill('input[name="repassword"]', credentials.password);
    await page.click('input[type="submit"][value="Register"]');
    await expect(page.locator('body')).toContainText('exists');
  });

  test('register page renders correctly', async ({ page }) => {
    await page.goto('/register');
    await expect(page).toHaveTitle('Aspiring Investments');
    await expect(page.locator('input[name="email"]')).toBeVisible();
    await expect(page.locator('input[name="password"]')).toBeVisible();
    await expect(page.locator('input[name="repassword"]')).toBeVisible();
    await expect(page.locator('input[value="Register"]')).toBeVisible();
  });

  test('login page renders correctly', async ({ page }) => {
    await page.goto('/login');
    await expect(page).toHaveTitle('Aspiring Investments');
    await expect(page.locator('input[name="email"]')).toBeVisible();
    await expect(page.locator('input[name="password"]')).toBeVisible();
    await expect(page.locator('a[href="/register"]')).toBeVisible();
    await expect(page.locator('a[href="/lostpw"]')).toBeVisible();
  });

  test('logged-in user sees their email in dashboard', async ({ page }) => {
    const credentials = generateUniqueUser();
    await registerUser(page, credentials.email, credentials.password);
    await loginUser(page, credentials.email, credentials.password);
    await expect(page.locator('body')).toContainText(credentials.email);
    await expect(page.locator('a[href="/logout"]')).toBeVisible();
  });

  test('logout clears session and redirects to login', async ({ page }) => {
    const credentials = generateUniqueUser();
    await registerUser(page, credentials.email, credentials.password);
    await loginUser(page, credentials.email, credentials.password);
    await logoutUser(page);
    // After logout, attempting to visit dashboard redirects back to /dev then /login
    await page.goto('/save');
    await page.waitForURL(/\/(dev|login)/);
  });
});
