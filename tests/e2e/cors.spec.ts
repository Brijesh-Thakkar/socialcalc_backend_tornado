import { test, expect } from '@playwright/test';

/**
 * CORS on /webapp and /htmltopdf (handlers/cors.py, driven by ALLOWED_ORIGINS).
 *
 * The stack's default allowlist is capacitor://localhost, http://localhost, https://localhost.
 * Set CORS_DEV_ORIGIN (e.g. http://localhost:5173) to also assert a dev-server origin that the
 * stack was started with via ALLOWED_ORIGINS; the check is skipped when it is not set.
 */

const ENDPOINTS = ['/webapp', '/htmltopdf'];
const APP_ORIGINS = ['capacitor://localhost', 'http://localhost', 'https://localhost'];
const DISALLOWED = ['https://evil.example', 'http://localhost.evil.example', 'null'];
const DEV_ORIGIN = process.env.CORS_DEV_ORIGIN;

function preflight(request: any, path: string, origin: string) {
  return request.fetch(path, {
    method: 'OPTIONS',
    headers: {
      Origin: origin,
      'Access-Control-Request-Method': 'POST',
      'Access-Control-Request-Headers': 'content-type',
    },
  });
}

test.describe('CORS allowlist', () => {
  for (const path of ENDPOINTS) {
    for (const origin of APP_ORIGINS) {
      test(`preflight ${path} from ${origin} is allowed`, async ({ request }) => {
        const res = await preflight(request, path, origin);
        expect(res.status()).toBe(204);
        const h = res.headers();
        expect(h['access-control-allow-origin']).toBe(origin);
        expect(h['access-control-allow-methods']).toContain('POST');
        expect(h['access-control-allow-headers']).toMatch(/content-type/i);
        expect(h['vary']).toMatch(/origin/i);
      });
    }

    for (const origin of DISALLOWED) {
      test(`preflight ${path} from ${origin} is rejected`, async ({ request }) => {
        const res = await preflight(request, path, origin);
        expect(res.status()).toBe(403);
        const h = res.headers();
        expect(h['access-control-allow-origin']).toBeUndefined();
        expect(h['access-control-allow-credentials']).toBeUndefined();
        expect(h['access-control-allow-methods']).toBeUndefined();
      });
    }

    test(`${path}: preflight without an Origin header is a plain 204`, async ({ request }) => {
      const res = await request.fetch(path, { method: 'OPTIONS' });
      expect(res.status()).toBe(204);
      expect(res.headers()['access-control-allow-origin']).toBeUndefined();
    });
  }

  test('/webapp actual request from an allowed origin: ACAO echoed + credentials', async ({ request }) => {
    const res = await request.get('/webapp?action=login', { headers: { Origin: 'capacitor://localhost' } });
    expect(res.status()).toBe(200);
    const h = res.headers();
    expect(h['access-control-allow-origin']).toBe('capacitor://localhost');
    expect(h['access-control-allow-credentials']).toBe('true');
    expect(h['vary']).toMatch(/origin/i);
  });

  test('/webapp actual request from a disallowed origin gets no CORS headers', async ({ request }) => {
    const res = await request.get('/webapp?action=login', { headers: { Origin: 'https://evil.example' } });
    const h = res.headers();
    expect(h['access-control-allow-origin']).toBeUndefined();
    expect(h['access-control-allow-credentials']).toBeUndefined();
    expect(h['vary']).toMatch(/origin/i);
  });

  test('/webapp error responses keep the CORS headers for an allowed origin', async ({ request }) => {
    // Missing required `action` argument -> Tornado 400
    const res = await request.get('/webapp', { headers: { Origin: 'http://localhost' } });
    expect(res.status()).toBe(400);
    expect(res.headers()['access-control-allow-origin']).toBe('http://localhost');
  });

  test('/htmltopdf is anonymous: allowed origin gets ACAO but no credentials', async ({ request }) => {
    const res = await request.post('/htmltopdf', {
      headers: { Origin: 'https://localhost' },
      form: { content: '<html><body><p>cors check</p></body></html>' },
    });
    expect(res.status()).toBe(200);
    const h = res.headers();
    expect(h['access-control-allow-origin']).toBe('https://localhost');
    expect(h['access-control-allow-credentials']).toBeUndefined();
  });

  test('wildcard is never combined with credentials', async ({ request }) => {
    for (const path of ['/webapp?action=login', '/htmltopdf?fname=nope']) {
      for (const origin of [...APP_ORIGINS, ...DISALLOWED]) {
        const res = await request.get(path, { headers: { Origin: origin } });
        const h = res.headers();
        if (h['access-control-allow-credentials'] === 'true') {
          expect(h['access-control-allow-origin']).not.toBe('*');
          expect(h['access-control-allow-origin']).toBe(origin);
        }
      }
    }
  });

  test('dev-server origin from ALLOWED_ORIGINS is allowed', async ({ request }) => {
    test.skip(!DEV_ORIGIN, 'CORS_DEV_ORIGIN not set');
    const res = await preflight(request, '/webapp', DEV_ORIGIN!);
    expect(res.status()).toBe(204);
    expect(res.headers()['access-control-allow-origin']).toBe(DEV_ORIGIN);
  });
});
