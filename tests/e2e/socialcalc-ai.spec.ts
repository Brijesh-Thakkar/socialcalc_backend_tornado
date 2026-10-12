import { test, expect } from '@playwright/test';
import { registerUser, loginUser, generateUniqueUser } from './helpers/app-helper';
import * as fs from 'fs';
import * as path from 'path';
import { execSync } from 'child_process';

/**
 * socialcalc-ai (React/Ionic UI, docker-compose service `socialcalc-ai`, host port 8081) <-> Tornado.
 *
 * The UI container serves the app and proxies /api/* to Tornado's JSON Sheets API (handlers/sheets_api.py),
 * so these tests drive the built production image, not a dev server. The invoice is the real
 * tests/e2e/fixtures/interop/invoice.xlsx (₹ currency, Devanagari text, a "GST %" row) imported through
 * /interop/import. Tornado's `user` cookie is not scoped by port, so a login on :8081 also works on :8080.
 */

const AI = (process.env.SOCIALCALC_AI_URL || 'http://localhost:8081').replace(/\/+$/, '');
const FX = path.resolve(__dirname, 'fixtures/interop');
const sh = (cmd: string) => execSync(cmd, { cwd: process.cwd(), encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
const cid = (service: string) => sh(`docker compose ps -q ${service}`).trim();
const dockerState = (service: string) => sh(`docker inspect -f '{{.State.Health.Status}} {{.State.Paused}} {{.State.Running}}' ${cid(service)}`).trim();

async function waitFor(fn: () => Promise<boolean> | boolean, what: string, tries = 60) {
  for (let i = 0; i < tries; i++) {
    try { if (await fn()) return; } catch { /* keep polling */ }
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error(`timed out waiting for ${what}`);
}


/** Cells of every sheet keyed "SheetName!A1", with SocialCalc's renumbered ids resolved and floats normalised. */
function semantic(savestr: string): Record<string, string> {
  const dec = (v: string) => v.replace(/\\c/g, ':').replace(/\\n/g, '\n').replace(/\\b/g, '\\');
  const out: Record<string, string> = {};
  for (const s of Object.values<any>(JSON.parse(savestr).sheetArr)) {
    const raw: string[] = String(s.sheetstr.savestr).split('\n');
    const defs: Record<string, string> = {};
    for (const l of raw) { const p = l.split(':'); if (['valueformat', 'font', 'color', 'border', 'layout', 'cellformat'].includes(p[0])) defs[`${p[0]}${p[1]}`] = p.slice(2).join(':'); }
    const attrKind: Record<string, string> = { ntvf: 'valueformat', tvf: 'valueformat', f: 'font', cf: 'cellformat', c: 'color', bg: 'color', l: 'layout', b: 'border' };
    for (const l of raw) {
      const p = l.split(':');
      if (p[0] !== 'cell') continue;
      const parts: string[] = [];
      for (let i = 2; i < p.length; i++) {
        const k = p[i];
        if (k in attrKind) { parts.push(`${k}=${defs[attrKind[k] + p[++i]] ?? '?' + p[i]}`); }
        else if (k === 'v') { parts.push(`v=${Number(dec(p[++i])).toPrecision(12)}`); }
        else if (k === 't') { parts.push(`t=${dec(p[++i])}`); }
        else if (k === 'vtf') { const t = p[++i]; const val = dec(p[++i]); parts.push(`vtf=${t}:${t === 'n' ? Number(val).toPrecision(12) : val}:${dec(p[++i])}`); }
        else { parts.push(k); }
      }
      out[`${s.name}!${p[1]}`] = parts.join('|');
    }
  }
  return out;
}

test.describe.configure({ mode: 'serial' });

test.describe('socialcalc-ai container', () => {
  test('healthcheck: container is healthy and /healthz answers without involving Tornado', async ({ request }) => {
    await waitFor(() => dockerState('socialcalc-ai').startsWith('healthy'), 'socialcalc-ai healthy');
    const res = await request.get(`${AI}/healthz`);
    expect(res.status()).toBe(200);
    expect((await res.text()).trim()).toBe('ok');
  });

  test('serves the app (SPA fallback) and proxies /api to Tornado', async ({ request }) => {
    const index = await request.get(`${AI}/`);
    expect(index.status()).toBe(200);
    expect(index.headers()['content-type']).toContain('text/html');
    const deep = await request.get(`${AI}/some/deep/link`);
    expect(deep.status()).toBe(200);
    expect(await deep.text()).toBe(await index.text());
    // reaches Tornado's sheets API: JSON 401 produced by handlers/sheets_api.py, not by the UI's nginx
    const api = await request.get(`${AI}/api/sheets`);
    expect(api.status()).toBe(401);
    expect(await api.json()).toEqual({ error: 'authentication required' });
  });

  test('open UI -> load a sheet via /api -> edit -> save -> reopen in the legacy Tornado UI (₹ / Hindi / GST % intact)', async ({ browser, baseURL }) => {
    test.setTimeout(180_000);
    const user = generateUniqueUser();
    const sheet = `ai_invoice_${Date.now()}`;
    const ctx = await browser.newContext({ baseURL, viewport: { width: 1400, height: 900 } });
    const legacy = await ctx.newPage();
    legacy.on('dialog', (d) => d.accept());

    // --- seed: import the real invoice.xlsx, store it through the UI's own /api proxy (:8081) ---
    await registerUser(legacy, user.email, user.password);
    await loginUser(legacy, user.email, user.password);
    const imp = await ctx.request.post('/interop/import', {
      multipart: { upload: { name: 'invoice.xlsx', mimeType: 'application/octet-stream', buffer: fs.readFileSync(path.join(FX, 'invoice.xlsx')) } },
    });
    expect(imp.status()).toBe(200);
    const s0: string = (await imp.json()).savestr;
    for (const needle of ['₹', 'रमेश', 'GST %']) expect(s0, `fixture contains ${needle}`).toContain(needle);
    const put = await ctx.request.put(`${AI}/api/sheets/${encodeURIComponent(sheet)}`, { data: { data: s0 } });
    expect(put.status()).toBe(201);
    // transport is byte-exact: the proxy and Tornado return the stored invoice unchanged (₹ / Devanagari / GST % survive UTF-8)
    expect((await (await ctx.request.get(`${AI}/api/sheets/${encodeURIComponent(sheet)}`)).json()).data).toBe(s0);
    expect((await (await ctx.request.post('/insert', { form: { filename: sheet } })).json()).data).toBe(s0);
    await ctx.clearCookies();                 // the UI must log in by itself

    // --- socialcalc-ai UI ---
    const page = await ctx.newPage();
    const pageErrors: string[] = [];
    page.on('pageerror', (e) => pageErrors.push(e.message));
    await page.goto(AI);
    await expect(page.getByTestId('storage-user')).toHaveText('Not logged in', { timeout: 30_000 });
    await page.getByTestId('btn-login').click();
    await page.getByTestId('login-email').fill(user.email);
    await page.getByTestId('login-password').fill(user.password);
    await page.getByTestId('login-submit').click();
    await expect(page.getByTestId('storage-user')).toContainText('Logged in', { timeout: 15_000 });

    await page.getByTestId('btn-open').click();
    await expect(page.getByTestId(`sheet-row-${sheet}`)).toBeVisible({ timeout: 10_000 });
    await page.getByTestId(`open-${sheet}`).click();
    await page.getByText(`Loaded ${sheet}`).first().waitFor({ timeout: 10_000 });
    await expect(page.getByTestId('current-sheet-name')).toHaveText(sheet);
    await page.waitForTimeout(1500);

    const cells = () => page.evaluate(() => {
      const c = (window as any).SocialCalc.GetCurrentWorkBookControl().workbook.sheetArr['sheet1'].sheet.cells;
      return { b2: c.B2 && c.B2.datavalue, b3: c.B3 && c.B3.datavalue };
    });
    expect(await cells()).toEqual({ b2: 'INV-2026-0042', b3: 'Ramesh Kumar (रमेश कुमार)' });

    // edit B2 through the real Edit Cell modal
    const pos = await page.evaluate(() => {
      const r = document.getElementById('cell_B2')!.getBoundingClientRect();
      return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
    });
    await page.mouse.click(pos.x, pos.y);
    await page.waitForSelector('.sc-cell-input');
    await page.fill('.sc-cell-input', 'INV-2026-0043');
    await page.click('.sc-cell-btn-apply');
    await page.waitForTimeout(800);
    expect((await cells()).b2).toBe('INV-2026-0043');

    await page.getByTestId('btn-save').click();
    await page.getByText(`Saved "${sheet}"`).first().waitFor({ timeout: 10_000 });

    // --- what Tornado stored ---
    const got = await ctx.request.get(`${AI}/api/sheets/${encodeURIComponent(sheet)}`);
    expect(got.status()).toBe(200);
    const s1: string = (await got.json()).data;
    // The UI re-serialises the workbook (renumbers sheet/format ids, rewrites floats, adds EditableCells/timestamp),
    // so a UI-saved string is not byte-equal to the imported one. Compare the CONTENT with ids resolved: the only
    // difference must be the cell we edited; every ₹ / Devanagari / GST % cell is untouched.
    const a = semantic(s0), b = semantic(s1);
    expect(Object.keys(b).sort()).toEqual(Object.keys(a).sort());
    const changed = Object.keys(a).filter((k) => a[k] !== b[k]);
    // D13/D14 depend on SUMPRODUCT, which SocialCalc's formula engine does not have (same limitation as in
    // interop-fastapi.spec.ts): SocialCalc recalculates them on load and caches an error value, but the formula
    // TEXT must survive. That is the engine, not the storage path, so it is the only other allowed difference.
    expect(changed).toEqual(['Invoice!B2', 'Invoice!D13', 'Invoice!D14']);
    expect(b['Invoice!D13']).toContain('SUMPRODUCT(D6:D10,E6:E10)/100');
    expect(b['Invoice!D13']).toContain('Unknown function SUMPRODUCT');
    expect(b['Invoice!D14']).toContain('D12+D13');
    expect(b['Invoice!B2']).toBe('t=INV-2026-0043');
    expect(b['Invoice!B3']).toContain('Ramesh Kumar (रमेश कुमार)');
    expect(b['Invoice!E5']).toBe(a['Invoice!E5']);
    expect(b['Invoice!E5']).toContain('GST %');
    expect(Object.values(b).some((v) => v.includes('₹'))).toBe(true);
    expect(s1).toContain('GST %');
    expect(s1).toContain('Ramesh Kumar (रमेश कुमार)');
    expect(s1).toContain('₹');

    // --- reopen in the legacy Tornado UI ---
    await legacy.goto('/login');
    await legacy.fill('input[name="email"]', user.email);
    await legacy.fill('input[name="password"]', user.password);
    await legacy.click('input[type="submit"][value="Login"]');
    await legacy.waitForURL('**/save');
    await legacy.locator('tr', { hasText: sheet }).locator('input[value="Edit"]').click();
    await legacy.waitForFunction(() => { const sp = (window as any).spreadsheet; return sp && sp.editor && sp.editor.busy === false && sp.editor.state === 'start'; }, null, { timeout: 30_000 });
    await legacy.waitForTimeout(1000);
    const leg = await legacy.evaluate(() => {
      const SC = (window as any).SocialCalc;
      const wb = SC.GetCurrentWorkBookControl();
      const bySheetName: Record<string, any> = {};
      for (const sid of Object.keys(wb.workbook.sheetArr)) {
        const sh = wb.workbook.sheetArr[sid].sheet;
        const name = Object.values<any>(wb.sheetButtonArr).find((x: any) => x.id === sid)?.value ?? sid;
        bySheetName[name] = sh;
      }
      const inv = bySheetName['Invoice'];
      const c = inv.cells;
      return {
        names: Object.keys(bySheetName),
        b2: c.B2.datavalue, b3: c.B3.datavalue, e5: c.E5.datavalue, d6: c.D6.datavalue,
        c5: c.C5.datavalue,
        fmt: (() => { const sum = bySheetName['Summary'].cells.B2; return bySheetName['Summary'].valueformats[sum.nontextvalueformat]; })(),
      };
    });
    expect(leg.names).toEqual(['Invoice', 'Payments', 'Summary']);
    expect(leg.b2).toBe('INV-2026-0043');
    expect(leg.b3).toBe('Ramesh Kumar (रमेश कुमार)');
    expect(leg.e5).toBe('GST %');
    expect(leg.c5).toBe('Rate (₹)');
    expect(Number(leg.d6)).toBeCloseTo(1255, 6);
    expect(leg.fmt).toContain('₹');

    // Byte-equal where it is real: the legacy UI is served the exact bytes the UI saved (and /api returns).
    const ins = await ctx.request.post('/insert', { form: { filename: sheet } });
    const insBody = await ins.json();
    expect(insBody.result).toBe('ok');
    expect(insBody.data).toBe(s1);
    expect(pageErrors).toEqual([]);
  });

  test('Tornado stopped -> 502, paused -> 504 (JSON), UI health unaffected, recovers', async ({ request, browser, baseURL }) => {
    test.setTimeout(120_000);
    const up = async () => (await request.get(`${AI}/api/sheets`)).status() === 401;
    const tornado = cid('nginx');               // the Tornado front door the UI proxies to (TORNADO_API_URL=http://nginx:80)
    try {
      // stopped -> connection refused / name not resolvable -> 502
      sh(`docker stop ${tornado}`);
      await new Promise((r) => setTimeout(r, 3_000));   // > the UI proxy's 2 s DNS cache, so the name no longer resolves
      const down = await request.get(`${AI}/api/sheets`);
      expect(down.status()).toBe(502);
      expect(await down.json()).toEqual({ error: 'backend unreachable' });
      expect((await request.get(`${AI}/healthz`)).status()).toBe(200);   // the UI container itself stays healthy
      sh(`docker start ${tornado}`);
      await waitFor(up, 'Tornado back after stop');

      // paused -> accepts the connection but never answers -> 504 after TORNADO_READ_TIMEOUT
      sh(`docker pause ${tornado}`);
      const t0 = Date.now();
      const slow = await request.get(`${AI}/api/sheets`, { timeout: 60_000 });
      const waited = Date.now() - t0;
      expect(slow.status()).toBe(504);
      expect(await slow.json()).toEqual({ error: 'backend timed out' });
      expect(waited).toBeGreaterThanOrEqual(3_000);                       // waited for the (short, CI-configured) read timeout
      expect(waited).toBeLessThan(40_000);
      expect((await request.get(`${AI}/healthz`)).status()).toBe(200);
      sh(`docker unpause ${tornado}`);
      await waitFor(up, 'Tornado back after pause');

      // the app reports it: with the backend down a save shows the HTTP status instead of failing silently
      const ctx = await browser.newContext({ baseURL, viewport: { width: 1400, height: 900 } });
      const page = await ctx.newPage();
      await page.goto(AI);
      await expect(page.getByTestId('storage-user')).toHaveText('Not logged in', { timeout: 30_000 });
      sh(`docker stop ${tornado}`);
      await new Promise((r) => setTimeout(r, 3_000));   // let the proxy's 2 s DNS cache expire
      await page.getByTestId('btn-open').click();
      await expect(page.getByText(/HTTP 502/).first()).toBeVisible({ timeout: 10_000 });
      await ctx.close();
    } finally {
      try { sh(`docker unpause ${tornado}`); } catch { /* not paused */ }
      try { sh(`docker start ${tornado}`); } catch { /* already running */ }
      await waitFor(up, 'Tornado restored');
    }
  });
});
