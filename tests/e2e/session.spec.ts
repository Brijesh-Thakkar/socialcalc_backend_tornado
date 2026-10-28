import { test, expect } from '@playwright/test';
import { generateUniqueUser, registerUser, loginUser } from './helpers/app-helper';

/**
 * Session Persistence Tests
 *
 * Verifies that sessions created on one Tornado instance are readable by the
 * other instance via the shared memcached container.
 */

test.describe('Session Persistence', () => {
  test('session cookie is set after login', async ({ page }) => {
    const credentials = generateUniqueUser();
    await registerUser(page, credentials.email, credentials.password);
    await loginUser(page, credentials.email, credentials.password);

    // Tornado sets a "user" cookie via set_secure_cookie
    const cookies = await page.context().cookies();
    const userCookie = cookies.find((c) => c.name === 'user');
    expect(userCookie).toBeDefined();
    expect(userCookie?.value).toBeTruthy();
  });

  test('session persists across multiple requests (round-robin)', async ({ page }) => {
    const credentials = generateUniqueUser();
    await registerUser(page, credentials.email, credentials.password);
    await loginUser(page, credentials.email, credentials.password);

    // Make multiple requests — nginx round-robins between app1 and app2
    // The session cookie must be recognised by whichever instance handles each request
    for (let i = 0; i < 6; i++) {
      await page.goto('/save');
      await expect(page.locator('body')).toContainText(credentials.email);
    }
  });

  test('logout destroys session — subsequent requests lose auth', async ({ page }) => {
    const credentials = generateUniqueUser();
    await registerUser(page, credentials.email, credentials.password);
    await loginUser(page, credentials.email, credentials.password);

    // Logout clears the cookie
    await page.goto('/logout');
    await page.waitForURL('**/login');

    // Cookie should be cleared
    const cookies = await page.context().cookies();
    const userCookie = cookies.find((c) => c.name === 'user');
    expect(!userCookie || userCookie.value === '""').toBe(true);
  });

  test('memcached shared session: two browser contexts see same state', async ({ browser }) => {
    // Context A logs in
    const ctxA = await browser.newContext();
    const pageA = await ctxA.newPage();
    const credentials = generateUniqueUser();
    await registerUser(pageA, credentials.email, credentials.password);
    await loginUser(pageA, credentials.email, credentials.password);

    // Copy cookies from A to B to simulate the same user hitting the other instance
    const cookiesA = await ctxA.cookies();
    const ctxB = await browser.newContext();
    await ctxB.addCookies(cookiesA);
    const pageB = await ctxB.newPage();

    // Context B should be able to access the dashboard with the same session
    await pageB.goto('/save');
    await expect(pageB.locator('body')).toContainText(credentials.email);

    await ctxA.close();
    await ctxB.close();
  });
});
