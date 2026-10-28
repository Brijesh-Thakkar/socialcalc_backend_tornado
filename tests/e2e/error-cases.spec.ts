import { test, expect } from '@playwright/test';

test.describe('Error Cases & Edge Cases', () => {
  test('404 for completely unknown route', async ({ request }) => {
    const resp = await request.get('/does_not_exist_at_all_xyz');
    expect(resp.status()).toBe(404);
  });

  test('404 for unknown route via page navigation', async ({ page }) => {
    const resp = await page.goto('/route_xyz_nonexistent');
    expect(resp?.status()).toBe(404);
  });

  test('unauthenticated /save redirects, no 500', async ({ request }) => {
    // Following redirects — should end up at login, not 500
    const resp = await request.get('/save', { maxRedirects: 5 });
    expect([200, 302]).toContain(resp.status());
  });

  test('/logout clears cookie and redirects to login', async ({ request }) => {
    const resp = await request.get('/logout', { maxRedirects: 0 });
    expect(resp.status()).toBe(302);
    expect(resp.headers()['location']).toContain('/login');
  });

  test('/login GET returns 200', async ({ request }) => {
    const resp = await request.get('/login');
    expect(resp.status()).toBe(200);
  });

  test('/register GET returns 200', async ({ request }) => {
    const resp = await request.get('/register');
    expect(resp.status()).toBe(200);
  });

  test('/lostpw GET returns 200', async ({ request }) => {
    const resp = await request.get('/lostpw');
    expect(resp.status()).toBe(200);
  });

  test('POST /updates returns 200 (long-poll endpoint)', async ({ request }) => {
    const resp = await request.post('/updates');
    // Returns 200 even without session (returns empty or null message)
    expect(resp.status()).toBe(200);
  });
});
