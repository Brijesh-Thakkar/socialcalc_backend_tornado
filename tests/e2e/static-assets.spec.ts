import { test, expect } from '@playwright/test';

test.describe('Static Assets', () => {
  const cssFiles = [
    '/static/screen.css',
    '/static/fonts.css',
    '/static/socialcalc.css',
  ];

  const jsFiles = [
    '/static/autosave.js',
    '/static/socialcalcspreadsheetcontrol.js',
    '/static/formula1.js',
    '/static/formatnumber2.js',
    '/static/jquery.min.js',
    '/static/json2.js',
  ];

  const imageFiles = [
    '/static/checkmark.png',
  ];

  for (const file of cssFiles) {
    test(`CSS: ${file} returns 200 with text/css`, async ({ request }) => {
      const resp = await request.get(file);
      expect(resp.status()).toBe(200);
      expect(resp.headers()['content-type']).toContain('text/css');
    });
  }

  for (const file of jsFiles) {
    test(`JS: ${file} returns 200 with javascript content-type`, async ({ request }) => {
      const resp = await request.get(file);
      expect(resp.status()).toBe(200);
      const ct = resp.headers()['content-type'] || '';
      expect(ct.includes('javascript') || ct.includes('text/')).toBe(true);
    });
  }

  for (const file of imageFiles) {
    test(`Image: ${file} returns 200 with image content-type`, async ({ request }) => {
      const resp = await request.get(file);
      expect(resp.status()).toBe(200);
      expect(resp.headers()['content-type']).toContain('image/');
    });
  }

  test('Tornado serves static files with cache headers', async ({ request }) => {
    const resp = await request.get('/static/screen.css');
    expect(resp.status()).toBe(200);
    // Tornado static handler sets Etag or Last-Modified
    const etag = resp.headers()['etag'];
    const lastMod = resp.headers()['last-modified'];
    expect(etag || lastMod).toBeTruthy();
  });

  test('static URL versioning fingerprint in page HTML', async ({ page }) => {
    await page.goto('/login');
    const html = await page.content();
    // Tornado static_url() appends ?v=<hash> to fingerprint static files
    expect(html).toMatch(/static\/screen\.css\?v=/);
  });
});
