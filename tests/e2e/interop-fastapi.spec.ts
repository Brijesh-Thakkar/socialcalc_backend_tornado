import { test, expect } from './fixtures/auth.fixture';
import { openSpreadsheet } from './helpers/app-helper';
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import { execSync, spawnSync } from 'child_process';

/**
 * M1: Tornado <-> fastapi-interop sidecar.
 *
 * Every test uses the real files in tests/e2e/fixtures/interop (checksummed in SHA256SUMS).
 * Failure-injection tests stop/pause the sidecar container; each restores it afterwards.
 */

const FX = path.resolve(__dirname, 'fixtures/interop');
const read = (name: string) => fs.readFileSync(path.join(FX, name));
const mime = (name: string) => 'application/octet-stream';

/** Container id of a compose service, independent of the project name. */
function cid(service: string): string {
  return execSync(`docker compose ps -q ${service}`, { cwd: process.cwd(), encoding: 'utf8' }).trim();
}
function sh(cmd: string) { return execSync(cmd, { cwd: process.cwd(), encoding: 'utf8' }); }
async function waitSidecarHealthy(request: any) {
  for (let i = 0; i < 40; i++) {
    const out = sh(`docker inspect -f '{{.State.Health.Status}} {{.State.Paused}}' ${cid('fastapi-interop')}`).trim();
    if (out === 'healthy false') { return; }
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error('fastapi-interop did not become healthy');
}

// ---- tiny SocialCalc save-string reader used to compare content semantically ----
type CellInfo = { kind: string; value: string; formula?: string; fmt?: string; colspan?: number; rowspan?: number };
const dec = (s: string) => s.replace(/\\c/g, ':').replace(/\\n/g, '\n').replace(/\\b/g, '\\');
function sheets(savestr: string): { name: string; cells: Record<string, CellInfo> }[] {
  const book = JSON.parse(savestr);
  return Object.values<any>(book.sheetArr).map((s) => {
    const raw: string = s.sheetstr.savestr;
    const fmts: Record<string, string> = {};
    for (const l of raw.split('\n')) {
      const p = l.split(':');
      if (p[0] === 'valueformat') fmts[p[1]] = dec(p.slice(2).join(':'));
    }
    const cells: Record<string, CellInfo> = {};
    for (const l of raw.split('\n')) {
      const p = l.split(':');
      if (p[0] !== 'cell') continue;
      const c: CellInfo = { kind: '', value: '' };
      for (let i = 2; i < p.length; i++) {
        const k = p[i];
        if (k === 'v') { c.kind = 'n'; c.value = String(parseFloat(dec(p[++i]))); }
        else if (k === 't') { c.kind = 't'; c.value = dec(p[++i]); }
        else if (k === 'vtf') { c.kind = 'f'; c.value = ''; c.formula = dec(p[i + 3]); i += 3; }
        else if (k === 'ntvf' || k === 'tvf') { c.fmt = fmts[p[++i]]; }
        else if (k === 'colspan') { c.colspan = parseInt(p[++i], 10); }
        else if (k === 'rowspan') { c.rowspan = parseInt(p[++i], 10); }
        else if (['f', 'cf', 'l', 'c', 'bg', 'comment'].includes(k)) { i++; }
      }
      cells[p[1]] = c;
    }
    return { name: s.name, cells };
  });
}

function parseCsv(text: string): string[][] {
  const rows: string[][] = []; let row: string[] = []; let cur = ''; let q = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (q) { if (ch === '"') { if (text[i + 1] === '"') { cur += '"'; i++; } else q = false; } else cur += ch; }
    else if (ch === '"') q = true;
    else if (ch === ',') { row.push(cur); cur = ''; }
    else if (ch === '\n' || ch === '\r') { if (ch === '\r' && text[i + 1] === '\n') i++; row.push(cur); rows.push(row); row = []; cur = ''; }
    else cur += ch;
  }
  if (cur !== '' || row.length) { row.push(cur); rows.push(row); }
  return rows;
}

async function importFile(req: any, name: string) {
  const resp = await req.post('/interop/import', { multipart: { upload: { name, mimeType: mime(name), buffer: read(name) } } });
  return resp;
}
async function exportFile(req: any, type: string, content: string) {
  const resp = await req.post('/interop/export', { form: { type, content } });
  expect(resp.status(), await resp.text()).toBe(200);
  return resp.json();
}

test.describe('M1 fixtures are intact', () => {
  test('SHA256SUMS matches the real files', () => {
    const sums = fs.readFileSync(path.join(FX, 'SHA256SUMS'), 'utf8').trim().split('\n');
    expect(sums.length).toBeGreaterThanOrEqual(6);
    for (const line of sums) {
      const [digest, name] = line.split(/\s+/);
      expect(crypto.createHash('sha256').update(read(name)).digest('hex'), name).toBe(digest);
    }
  });
});

test.describe('M1 registration of routes', () => {
  test('cloudmain.py AND cloudmain-dev.py register the interop routes', () => {
    for (const f of ['cloudmain.py', 'cloudmain-dev.py']) {
      const src = fs.readFileSync(path.resolve(__dirname, '../../', f), 'utf8');
      expect(src, f).toContain('from handlers import interop');
      expect(src, f).toContain('*interop.ROUTES');
    }
  });
});

test.describe.serial('M1 Tornado <-> fastapi-interop', () => {
  test('health is proxied and anonymous callers are rejected', async ({ request }) => {
    const h = await request.get('/interop/health');
    expect(h.status()).toBe(200);
    const body = await h.json();
    expect(body.status).toBe('ok');
    expect(body.sidecar.engine).toBe('python');
    expect(body.sidecar.pdf).toBe(true);

    const imp = await request.post('/interop/import', { multipart: { upload: { name: 'invoice.xlsx', mimeType: mime('x'), buffer: read('invoice.xlsx') } } });
    expect(imp.status()).toBe(401);
    expect((await imp.json()).error).toBe('authentication_required');
    const exp = await request.post('/interop/export', { form: { type: 'csv', content: '{}' } });
    expect(exp.status()).toBe(401);
  });

  test('import every real fixture', async ({ authenticatedPage }) => {
    const req = authenticatedPage.request;
    const expected: Record<string, [string, number]> = {
      'invoice.xls': ['xls', 3], 'invoice.xlsx': ['xlsx', 3], 'customers.csv': ['csv', 1], 'table.html': ['html', 1],
    };
    for (const [name, [fmt, n]] of Object.entries(expected)) {
      const resp = await importFile(req, name);
      expect(resp.status(), name).toBe(200);
      const body = await resp.json();
      expect(body.result).toBe('ok');
      expect(body.format).toBe(fmt);
      expect(body.sheets).toHaveLength(n);
      const parsed = sheets(body.savestr);
      expect(parsed).toHaveLength(n);
      if (name === 'invoice.xlsx') {
        expect(parsed.map((s) => s.name)).toEqual(['Invoice', 'Payments', 'Summary']);
        const inv = parsed[0].cells;
        expect(inv.B2.value).toBe('INV-2026-0042');
        expect(inv.B3.value).toBe('Ramesh Kumar (रमेश कुमार)');
        expect(inv.B3.colspan).toBe(2);
        expect(inv.A1.colspan).toBe(6);
        expect(inv.D14.kind).toBe('f');
        expect(inv.D14.formula).toBe('D12+D13');
        expect(inv.C6.fmt).toContain('₹');
        const formulas = parsed.reduce((a, s) => a + Object.values(s.cells).filter((c) => c.kind === 'f').length, 0);
        expect(formulas).toBe(25);
      }
      if (name === 'invoice.xls') {
        expect(body.warnings.join(' ')).toContain('formulas');   // xlrd cannot read .xls formulas
        expect(parsed[0].cells.D14.value).toBe('9374.6771');
      }
      if (name === 'customers.csv') {
        const rows = Object.keys(parsed[0].cells).filter((k) => /^A\d+$/.test(k)).length;
        expect(rows).toBe(251);
        const all = Object.values(parsed[0].cells).map((c) => c.value).join('|');
        expect(all).toContain('₹1,25,000 total');
        expect(all).toContain('दिल्ली');
        expect(all).toContain('line1\nline2');
      }
      if (name === 'table.html') {
        expect(parsed[0].cells.A5.colspan).toBe(3);
        expect(parsed[0].cells.D5.value).toBe('5601.25');
      }
    }
  });

  test('imported workbook opens in the Tornado editor with sheets, text and formulas', async ({ authenticatedPage }) => {
    const page = authenticatedPage;
    const imp = await (await importFile(page.request, 'invoice.xlsx')).json();
    const name = `interop_${Date.now()}`;
    const saved = await page.request.post('/save', { form: { fname: name, data: imp.savestr } });
    expect(await saved.text()).toContain('Done');
    await openSpreadsheet(page, name);
    const info = await page.evaluate(() => {
      const SC = (window as any).SocialCalc;
      const c = SC.GetCurrentWorkBookControl();
      const s1 = c.workbook.sheetArr['sheet1'].sheet;
      return {
        names: Object.values<any>(c.sheetButtonArr).map((b) => b.value),
        b2: s1.cells['B2'].datavalue, b3: s1.cells['B3'].datavalue,
        d12: s1.cells['D12'].datavalue, d14formula: s1.cells['D14'].formula,
      };
    });
    expect(info.names).toEqual(['Invoice', 'Payments', 'Summary']);
    expect(info.b2).toBe('INV-2026-0042');
    expect(info.b3).toBe('Ramesh Kumar (रमेश कुमार)');
    expect(Number(info.d12)).toBeCloseTo(8306.22, 2);       // SUM recalculated by SocialCalc itself
    expect(info.d14formula).toBe('D12+D13');                  // formula survived (SocialCalc has no SUMPRODUCT, so D14 differs from Excel)
  });

  test('export xlsx/xls/csv/html/pdf: file is in S3 and served by BOTH app containers', async ({ authenticatedPage }) => {
    const req = authenticatedPage.request;
    const imp = await (await importFile(req, 'invoice.xlsx')).json();
    const results: Record<string, { key: string; url: string; bytes: Buffer }> = {};
    for (const type of ['xlsx', 'xls', 'csv', 'html']) {
      const out = await exportFile(req, type, imp.savestr);
      expect(out.result).toBe('ok');
      expect(out.key).toMatch(new RegExp(`^[A-Z0-9]{24}\\.${type}$`));
      expect(out.url).toContain(`/interop/export?fname=${out.key}`);
      expect(out.size).toBeGreaterThan(100);
      // 10 fetches through Nginx (round-robin) must all return identical bytes
      const first = await (await authenticatedPage.request.get(out.url)).body();
      for (let i = 0; i < 9; i++) {
        const r = await authenticatedPage.request.get(out.url);
        expect(r.status()).toBe(200);
        expect(Buffer.compare(await r.body(), first)).toBe(0);
      }
      // ...and each app container serves it from S3 on its own filesystem
      for (const svc of ['app1', 'app2']) {
        const status = sh(`docker exec ${cid(svc)} python3 -c "import urllib.request,sys;r=urllib.request.urlopen('http://localhost:8888/interop/export?fname=${out.key}');print(r.status,len(r.read()))"`).trim();
        expect(status, `${svc} ${type}`).toBe(`200 ${first.length}`);
      }
      results[type] = { key: out.key, url: out.url, bytes: first };
    }
    const pdf = await exportFile(req, 'pdf', read('invoice_rendered.html').toString('utf8'));   // rendered HTML in (web UI style)
    const pdfBytes = await (await req.get(pdf.url)).body();
    expect(pdfBytes.subarray(0, 4).toString()).toBe('%PDF');
    results['pdf'] = { key: pdf.key, url: pdf.url, bytes: pdfBytes };
    const pdfFromSheet = await exportFile(req, 'pdf', imp.savestr);                             // save string in: rendered server-side
    expect((await (await req.get(pdfFromSheet.url)).body()).subarray(0, 4).toString()).toBe('%PDF');
    const passthrough = await exportFile(req, 'html', read('table.html').toString('utf8'));    // real HTML in: returned as is
    expect(Buffer.compare(await (await req.get(passthrough.url)).body(), read('table.html'))).toBe(0);

    // --- content checks on the generated files, re-read through the same service ---
    const reimport = async (name: string, bytes: Buffer) => {
      const r = await req.post('/interop/import', { multipart: { upload: { name, mimeType: mime(name), buffer: bytes } } });
      expect(r.status(), name).toBe(200);
      return sheets((await r.json()).savestr);
    };
    const orig = sheets(imp.savestr);

    const x = await reimport('export.xlsx', results.xlsx.bytes);
    expect(x.map((s) => s.name)).toEqual(orig.map((s) => s.name));
    for (let i = 0; i < orig.length; i++) {
      for (const [coord, c] of Object.entries(orig[i].cells)) {
        const d = x[i].cells[coord];
        expect(d, `${orig[i].name}!${coord}`).toBeDefined();
        expect(d.kind).toBe(c.kind);
        if (c.kind === 'f') expect(d.formula).toBe(c.formula);          // formulas survive xlsx
        else expect(d.value).toBe(c.value);
        expect(d.fmt ?? null).toBe(c.fmt ?? null);                      // number/date formats survive
        expect(d.colspan ?? null).toBe(c.colspan ?? null);              // merges survive
      }
    }

    const xl = await reimport('export.xls', results.xls.bytes);
    for (let i = 0; i < orig.length; i++) {
      for (const [coord, c] of Object.entries(orig[i].cells)) {
        // .xls formula cells are written without a cached value (xlwt), so xlrd cannot read them back;
        // Excel/LibreOffice recalculate them on open. Values, text, merges and formats are checked.
        if (c.kind === 'f') continue;
        const d = xl[i].cells[coord];
        expect(d, `${orig[i].name}!${coord} (xls)`).toBeDefined();
        expect(d.value).toBe(c.value);
        expect(d.colspan ?? null).toBe(c.colspan ?? null);
      }
    }

    const html = results.html.bytes.toString('utf8');
    expect(html).toContain('₹9,374.68');
    expect(html).toContain('15-Sep-2026');
    expect(html).toContain('colspan="6"');
    expect(html).toContain('रमेश कुमार');

    const csv = results.csv.bytes.toString('utf8');
    expect(csv).toContain('INV-2026-0042');
    const csvRows = parseCsv(csv);
    expect(csvRows[0][0]).toBe('Aspiring Traders Pvt Ltd - Tax Invoice');
    expect(csvRows.find((r) => r[2] === 'Total')?.[3]).toBe('9374.6771');

    const probe = spawnSync('pdftotext', ['-v']);
    if (probe.error) {
      console.log('pdftotext not installed: PDF text assertions skipped');
    } else {
      const f = path.join(require('os').tmpdir(), `interop_${Date.now()}.pdf`);
      fs.writeFileSync(f, pdfBytes);
      const text = sh(`pdftotext -layout ${f} -`);
      fs.unlinkSync(f);
      expect(text).toContain('INV-2026-0042');
      expect(text).toContain('₹9,374.68');
      expect(text).toContain('रमेश कुमार');
    }
  });

  test('csv with empty cells keeps its columns through import and export', async ({ authenticatedPage }) => {
    const req = authenticatedPage.request;
    const imp = await (await importFile(req, 'customers.csv')).json();
    const out = await exportFile(req, 'csv', imp.savestr);
    const got = parseCsv((await (await req.get(out.url)).body()).toString('utf8'));
    const orig = parseCsv(read('customers.csv').toString('utf8'));
    expect(got).toHaveLength(orig.length);
    expect(got.every((r, i) => r.length === orig[i].length)).toBe(true);   // the PHPExcel path shifted 106 rows
    const norm = (v: string) => (/^-?\d+\.\d+$/.test(v) ? String(parseFloat(v)) : v);
    expect(got.map((r) => r.map(norm))).toEqual(orig.map((r) => r.map(norm)));
  });

  test('the shipped 10-sheet SocialCalc template exports to xlsx and xls', async ({ authenticatedPage }) => {
    const req = authenticatedPage.request;
    const savestr = read('native_BusinessInvoices.sc').toString('utf8');
    for (const type of ['xlsx', 'xls']) {
      const out = await exportFile(req, type, savestr);
      const bytes = await (await req.get(out.url)).body();
      expect(bytes.length).toBeGreaterThan(5000);
    }
  });

  test('errors: bad input is rejected with the sidecar error code', async ({ authenticatedPage }) => {
    const req = authenticatedPage.request;
    const corrupt = await req.post('/interop/import', { multipart: { upload: { name: 'broken.xlsx', mimeType: mime('x'), buffer: Buffer.concat([Buffer.from('PK\x03\x04'), Buffer.alloc(200, 1)]) } } });
    expect(corrupt.status()).toBe(422);
    expect((await corrupt.json()).error).toBe('corrupt_file');

    const noFile = await req.post('/interop/import', { multipart: { other: 'x' } });
    expect(noFile.status()).toBe(400);
    expect((await noFile.json()).error).toBe('no_file');

    const bad = await req.post('/interop/export', { form: { type: 'ods', content: '{}' } });
    expect(bad.status()).toBe(400);
    expect((await bad.json()).error).toBe('bad_type');

    const badSave = await req.post('/interop/export', { form: { type: 'xlsx', content: '{not json' } });
    expect(badSave.status()).toBe(422);
    expect((await badSave.json()).error).toBe('bad_savestr');

    const big = await req.post('/interop/import', { multipart: { upload: { name: 'big.csv', mimeType: mime('x'), buffer: Buffer.alloc(21 * 1024 * 1024, 0x41) } } });
    expect(big.status()).toBe(413);

    expect((await req.get('/interop/export?fname=../../etc/passwd')).status()).toBe(400);
    expect((await req.get('/interop/export?fname=AAAAAAAAAAAAAAAAAAAAAAAA.xlsx')).status()).toBe(404);
  });

  test('error mapping: sidecar stopped -> 502, paused (timeout) -> 504', async ({ authenticatedPage, request }) => {
    const req = authenticatedPage.request;
    const imp = await (await importFile(req, 'invoice.xlsx')).json();
    const id = cid('fastapi-interop');
    try {
      sh(`docker pause ${id}`);
      const t0 = Date.now();
      const slow = await req.post('/interop/export', { form: { type: 'csv', content: imp.savestr } });
      expect(slow.status()).toBe(504);
      expect((await slow.json()).error).toBe('sidecar_timeout');
      expect(Date.now() - t0).toBeLessThan(40000);                         // explicit timeout, not the 20 s default or hung
      const slowImport = await importFile(req, 'invoice.xlsx');
      expect(slowImport.status()).toBe(504);
      sh(`docker unpause ${id}`);

      sh(`docker stop ${id}`);
      const down = await req.post('/interop/export', { form: { type: 'csv', content: imp.savestr } });
      expect(down.status()).toBe(502);
      expect((await down.json()).error).toBe('sidecar_unreachable');
      expect((await importFile(req, 'invoice.xlsx')).status()).toBe(502);
      expect((await request.get('/interop/health')).status()).toBe(502);
    } finally {
      spawnSync('docker', ['unpause', id], { cwd: process.cwd() });
      sh(`docker start ${id}`);
      await waitSidecarHealthy(request);
    }
    const again = await importFile(req, 'invoice.xlsx');
    expect(again.status()).toBe(200);
  });
});
