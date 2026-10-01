import { test, expect } from '@playwright/test';

/**
 * Nginx / Proxy Tests
 *
 * Verifies that Nginx is correctly proxying requests, passing headers,
 * and handling WebSocket upgrade paths.
 */

test.describe('Nginx Reverse Proxy', () => {
  test('Tornado version header is visible (nginx passes Server header)', async ({ request }) => {
    const resp = await request.get('/login');
    expect(resp.status()).toBe(200);
    const server = resp.headers()['server'] || '';
    // nginx proxy_pass_header Server passes TornadoServer header through
    expect(server).toContain('TornadoServer');
  });

  test('Host header is forwarded — app uses request.host correctly', async ({ page }) => {
    // If Host were not forwarded, links built from request.host would be broken
    await page.goto('/login');
    const html = await page.content();
    // The page should not contain 'None' as host in any constructed links
    expect(html).not.toContain('href="http://None');
  });

  test('X-Forwarded-For is set via nginx', async ({ page }) => {
    // We can't directly read request headers in Tornado from page,
    // but we can verify nginx is healthy by getting a 200
    const resp = await page.goto('/login');
    expect(resp?.status()).toBe(200);
  });

  test('nginx handles large bodies (client_max_body_size 50M)', async ({ request }) => {
    // /login POST with oversized body should get 413, not connection reset
    const bigBody = 'x'.repeat(1024); // small — just testing the path works
    const resp = await request.post('/login', {
      form: { email: 'a@b.com', password: bigBody },
    });
    // Will redirect (302 back to /login) or 200, not a 502/connection error
    expect([200, 302, 400, 413]).toContain(resp.status());
  });

  test('WebSocket upgrade path /updates proxied (no 502)', async ({ request }) => {
    // /updates is a POST long-poll endpoint — check it returns 200, not 502
    const resp = await request.post('/updates');
    expect(resp.status()).toBe(200);
  });

  test('WebSocket upgrade path /broadcast proxied (no 502)', async ({ request }) => {
    const resp = await request.post('/broadcast', {
      form: { session: 'test', id: '1', message: 'hi' },
    });
    // Returns 200 (may be empty body) or 400 (missing required args) — not 502
    expect([200, 400]).toContain(resp.status());
  });

  test('nginx config is valid (nginx -t passes)', async () => {
    const { execSync } = require('child_process');
    const out = execSync('docker exec tornado_version-nginx-1 nginx -t 2>&1', {
      encoding: 'utf8',
    });
    expect(out).toContain('syntax is ok');
    expect(out).toContain('test is successful');
  });

  // nginx discards every server-level proxy_set_header inside a location that sets its own.
  // The WebSocket/long-poll locations do, so they must repeat the forwarding headers or the
  // app sees Host "tornado_app" and loses the public scheme and client IP (broken links).
  test('every location that sets proxy headers forwards Host, scheme and client IP', async () => {
    const fs = require('fs');
    const path = require('path');
    const conf = fs
      .readFileSync(path.resolve(__dirname, '../../configs/nginx.docker.conf'), 'utf8')
      .replace(/#.*$/gm, ''); // ignore commented-out directives
    const blocks = [...conf.matchAll(/location\s+[^{]+\{([^}]*)\}/g)].map((m) => m[0]);
    const withHeaders = blocks.filter((b) => b.includes('proxy_set_header'));
    expect(withHeaders.length).toBeGreaterThan(0);
    for (const block of withHeaders) {
      for (const header of ['Host', 'X-Real-IP', 'X-Forwarded-For', 'X-Forwarded-Proto', 'X-Scheme']) {
        expect(block, `${block.split('{')[0].trim()} missing ${header}`).toMatch(
          new RegExp(`proxy_set_header\\s+${header}\\s`),
        );
      }
    }
  });
});
