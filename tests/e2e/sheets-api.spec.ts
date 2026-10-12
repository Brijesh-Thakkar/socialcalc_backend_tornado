import { test, expect, APIRequestContext } from '@playwright/test';
import { generateUniqueUser } from './helpers/app-helper';

/**
 * JSON Sheets API (/api/*, handlers/sheets_api.py).
 *
 * The CORS "allowed origin" test needs the server started with ALLOWED_ORIGINS
 * containing API_TEST_ALLOWED_ORIGIN (e.g. http://localhost:5173); it is skipped
 * when that variable is not set.
 */

const JSON_HEADERS = { 'Content-Type': 'application/json' };
const ALLOWED_ORIGIN = process.env.API_TEST_ALLOWED_ORIGIN;

const RAW_SHEET = JSON.stringify({
  numsheets: 1,
  currentid: 'sheet1',
  currentname: 'sheet1',
  sheetArr: { sheet1: { sheetstr: { savestr: 'version:1.5\ncell:A1:v:10\ncell:A2:vtf:n:20:SUM(A1\\cA1)\n' }, name: 'sheet1', hidden: '0' } },
  timestamp: 'Sun Oct 04 2026',
});

async function registerAndLogin(request: APIRequestContext) {
  const { email, password } = generateUniqueUser();
  const reg = await request.post('/register', { form: { email, password, repassword: password } });
  expect(reg.ok()).toBeTruthy();
  const login = await request.post('/api/login', { headers: JSON_HEADERS, data: { email, password } });
  expect(login.status()).toBe(200);
  expect(await login.json()).toEqual({ ok: true, user: email });
  return { email, password };
}

test.describe('JSON Sheets API', () => {
  test('requires the signed user cookie (401 JSON)', async ({ playwright, baseURL }) => {
    const anon = await playwright.request.newContext({ baseURL });
    for (const [method, path] of [['GET', '/api/sheets'], ['GET', '/api/sheets/x'], ['PUT', '/api/sheets/x'], ['DELETE', '/api/sheets/x']]) {
      const res = await anon.fetch(path, { method, headers: JSON_HEADERS, data: method === 'PUT' ? { data: 'x' } : undefined });
      expect(res.status(), `${method} ${path}`).toBe(401);
      expect(await res.json()).toHaveProperty('error');
    }
    await anon.dispose();
  });

  test('login rejects bad credentials and non-JSON bodies', async ({ request }) => {
    const bad = await request.post('/api/login', { headers: JSON_HEADERS, data: { email: 'nobody@example.com', password: 'wrong' } });
    expect(bad.status()).toBe(401);
    const form = await request.post('/api/login', { form: { email: 'a', password: 'b' } });
    expect(form.status()).toBe(415);
  });

  test('save / list / load / delete round trip stores the raw string', async ({ request }) => {
    await registerAndLogin(request);

    expect(await (await request.get('/api/sheets')).json()).toEqual([]);

    const name = `api_${Date.now()}`;
    const put = await request.put(`/api/sheets/${name}`, { headers: JSON_HEADERS, data: { data: RAW_SHEET } });
    expect(put.status()).toBe(201);
    const again = await request.put(`/api/sheets/${name}`, { headers: JSON_HEADERS, data: { data: RAW_SHEET } });
    expect(again.status()).toBe(200);

    const list = await (await request.get('/api/sheets')).json();
    expect(list).toHaveLength(1);
    expect(list[0].name).toBe(name);
    expect(list[0].size).toBeGreaterThan(RAW_SHEET.length);
    expect(list[0].modified).toMatch(/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/);

    const got = await (await request.get(`/api/sheets/${name}`)).json();
    expect(got).toEqual({ name, data: RAW_SHEET });

    expect((await request.delete(`/api/sheets/${name}`)).status()).toBe(204);
    expect((await request.get(`/api/sheets/${name}`)).status()).toBe(404);
    expect((await request.delete(`/api/sheets/${name}`)).status()).toBe(404);
    expect(await (await request.get('/api/sheets')).json()).toEqual([]);
  });

  test('sheets are shared with the legacy routes (same storage layout and cookie)', async ({ request }) => {
    await registerAndLogin(request);

    // API -> legacy
    const apiName = `from_api_${Date.now()}`;
    await request.put(`/api/sheets/${apiName}`, { headers: JSON_HEADERS, data: { data: RAW_SHEET } });
    const legacyList = await (await request.get('/save')).text();
    expect(legacyList).toContain(apiName);
    const insert = await request.post('/insert', { form: { filename: apiName } });
    expect(await insert.json()).toEqual({ data: RAW_SHEET, result: 'ok' });

    // legacy -> API
    const legacyName = `from_legacy_${Date.now()}`;
    const save = await request.post('/save', { form: { fname: legacyName, data: RAW_SHEET } });
    expect(await save.json()).toEqual({ data: 'Done' });
    const names = (await (await request.get('/api/sheets')).json()).map((s: any) => s.name);
    expect(names).toContain(legacyName);
    expect((await (await request.get(`/api/sheets/${legacyName}`)).json()).data).toBe(RAW_SHEET);
  });

  test('rejects path traversal and unsafe names (400)', async ({ request }) => {
    await registerAndLogin(request);
    // (a bare '..' segment is normalised away by HTTP clients; covered in tests/python)
    for (const enc of ['..%2Fx', '..%2F..%2Fetc%2Fpasswd', 'a%2Fb', 'a%5Cb', '.hidden', 'a%00b', 'x'.repeat(101), 'securestore', 'x%27y']) {
      for (const method of ['GET', 'PUT', 'DELETE']) {
        const res = await request.fetch(`/api/sheets/${enc}`, { method, headers: JSON_HEADERS, data: method === 'PUT' ? { data: 'x' } : undefined });
        expect(res.status(), `${method} ${enc}`).toBe(400);
      }
    }
  });

  test('mutating routes require application/json (415)', async ({ request }) => {
    await registerAndLogin(request);
    for (const contentType of ['text/plain', 'application/x-www-form-urlencoded']) {
      const res = await request.put('/api/sheets/ct_test', { headers: { 'Content-Type': contentType }, data: '{"data":"x"}' });
      expect(res.status(), contentType).toBe(415);
    }
    expect((await request.get('/api/sheets/ct_test')).status()).toBe(404);
  });

  test('rejects bad bodies (400) and data over 5 MB (413)', async ({ request }) => {
    await registerAndLogin(request);
    for (const body of ['{', '[]', '{"nodata":1}', '{"data":5}']) {
      const res = await request.put('/api/sheets/bad_body', { headers: JSON_HEADERS, data: body });
      expect(res.status(), body).toBe(400);
    }
    const big = await request.put('/api/sheets/too_big', { headers: JSON_HEADERS, data: { data: 'x'.repeat(5 * 1024 * 1024 + 1) } });
    expect(big.status()).toBe(413);
    // a realistically large save (200 KB) is fine
    const ok = await request.put('/api/sheets/large_ok', { headers: JSON_HEADERS, data: { data: 'x'.repeat(200 * 1024) } });
    expect(ok.status()).toBe(201);
  });

  test('users cannot see each other\'s sheets', async ({ playwright, baseURL }) => {
    const a = await playwright.request.newContext({ baseURL });
    const b = await playwright.request.newContext({ baseURL });
    await registerAndLogin(a);
    await registerAndLogin(b);
    await a.put('/api/sheets/private_a', { headers: JSON_HEADERS, data: { data: RAW_SHEET } });
    expect((await b.get('/api/sheets/private_a')).status()).toBe(404);
    expect(await (await b.get('/api/sheets')).json()).toEqual([]);
    await a.dispose();
    await b.dispose();
  });

  test('logout clears the session', async ({ request }) => {
    await registerAndLogin(request);
    expect((await request.get('/api/sheets')).status()).toBe(200);
    expect((await request.post('/api/logout', { headers: JSON_HEADERS, data: {} })).status()).toBe(200);
    expect((await request.get('/api/sheets')).status()).toBe(401);
  });

  test('CORS preflight: allowed origin is echoed with credentials', async ({ request }) => {
    test.skip(!ALLOWED_ORIGIN, 'set API_TEST_ALLOWED_ORIGIN (must be in the server\'s ALLOWED_ORIGINS)');
    const res = await request.fetch('/api/sheets/x', {
      method: 'OPTIONS',
      headers: { Origin: ALLOWED_ORIGIN!, 'Access-Control-Request-Method': 'PUT', 'Access-Control-Request-Headers': 'content-type' },
    });
    expect(res.status()).toBe(204);
    const h = res.headers();
    expect(h['access-control-allow-origin']).toBe(ALLOWED_ORIGIN);
    expect(h['access-control-allow-credentials']).toBe('true');
    expect(h['access-control-allow-methods']).toContain('PUT');
    expect(h['access-control-allow-headers']).toContain('Content-Type');
  });

  test('CORS preflight: disallowed origin gets no CORS headers, never "*"', async ({ request }) => {
    for (const origin of ['http://evil.example', 'null']) {
      const res = await request.fetch('/api/sheets/x', { method: 'OPTIONS', headers: { Origin: origin, 'Access-Control-Request-Method': 'PUT' } });
      const h = res.headers();
      expect(h['access-control-allow-origin'], origin).toBeUndefined();
      expect(h['access-control-allow-credentials'], origin).toBeUndefined();
    }
  });
});
