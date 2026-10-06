import { test, expect } from './fixtures/auth.fixture';
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import { execSync, spawnSync } from 'child_process';

/**
 * M3: Tornado <-> Node.js (node-interop sidecar), shared S3 storage.
 *
 * Both implementations store a sheet under the same S3 object (key = Python json.dumps of
 * ["home", <email>, <name>]); each keeps its own users, so no login is shared. The Node side is
 * driven with tests/helpers/interop-cli.js (the real Node routes/storage, session replaced by a
 * fixed user) through `docker compose exec`. Failure injection stops/pauses the sidecar and always
 * restores it.
 */

const FX = path.resolve(__dirname, 'fixtures/interop');
const read = (name: string) => fs.readFileSync(path.join(FX, name), 'utf8');
const sha = (s: string) => crypto.createHash('sha256').update(s, 'utf8').digest('hex');
const sh = (cmd: string) => execSync(cmd, { cwd: process.cwd(), encoding: 'utf8' });
const cid = () => sh('docker compose ps -q node-interop').trim();

function node(cmd: string, email: string, fname: string, stdin?: string): any {
  const r = spawnSync('docker', ['compose', 'exec', '-T', 'node-interop', 'node', 'tests/helpers/interop-cli.js', cmd, email, fname],
    { cwd: process.cwd(), input: stdin, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
  expect(r.status, r.stderr).toBe(0);
  return JSON.parse(r.stdout.trim().split('\n').pop()!);
}

async function waitHealthy() {
  for (let i = 0; i < 40; i++) {
    const out = sh(`docker inspect -f '{{.State.Health.Status}} {{.State.Paused}}' ${cid()}`).trim();
    if (out === 'healthy false') return;
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error('node-interop did not become healthy');
}

// Real fixtures; the sheet names carry "GST%", the rupee sign and Hindi so the S3 key needs escaping.
const CASES = [
  { file: 'native_BusinessInvoices.sc', name: 'GST% ₹ हिंदी native' },
  { file: 'invoice.xlsx.sc', name: 'GST% ₹ invoice' },
  { file: 'customers.csv.sc', name: 'ग्राहक customers' },
];

test.describe('M3 fixtures and registration', () => {
  test('SHA256SUMS matches the real files', () => {
    for (const line of fs.readFileSync(path.join(FX, 'SHA256SUMS'), 'utf8').trim().split('\n')) {
      const [digest, name] = line.split(/\s+/);
      expect(crypto.createHash('sha256').update(fs.readFileSync(path.join(FX, name))).digest('hex'), name).toBe(digest);
    }
  });

  test('cloudmain.py AND cloudmain-dev.py register the node-interop routes', () => {
    for (const f of ['cloudmain.py', 'cloudmain-dev.py']) {
      const src = fs.readFileSync(path.resolve(__dirname, '../../', f), 'utf8');
      expect(src, f).toContain('node_interop');
      expect(src, f).toContain('*node_interop.ROUTES');
    }
  });

  test('fixtures really contain rupee, Devanagari and GST text', () => {
    const all = read('invoice.xlsx.sc') + read('customers.csv.sc');
    expect(all).toContain('\\u20b9');       // ₹ (escaped inside the JSON save string)
    expect(all).toContain('\\u0930');       // Devanagari
    expect(all).toContain('GST %');
  });
});

test.describe.serial('M3 shared storage, both directions', () => {
  for (const c of CASES) {
    test(`Node saves -> Tornado opens, byte-equal: ${c.file}`, async ({ authenticatedPage: page, testUser }) => {
      const data = read(c.file);
      const saved = node('save', testUser.email, c.name, data);
      expect(saved).toEqual({ status: 200, body: { data: 'Done' } });

      const r = await page.request.post('/insert', { form: { filename: c.name } });
      expect(r.status()).toBe(200);
      const body = await r.json();
      expect(body.result).toBe('ok');
      expect(body.data).toBe(data);
      expect(sha(body.data)).toBe(sha(data));

      // and Tornado's editor page for the sheet opens
      const ed = await page.request.post('/usersheet', { form: { pagename: c.name, delete: 'no' } });
      expect(ed.status()).toBe(200);
      expect(await ed.text()).toContain('SocialCalc');
    });

    test(`Tornado saves -> Node opens, byte-equal: ${c.file}`, async ({ authenticatedPage: page, testUser }) => {
      const data = read(c.file);
      const s = await page.request.post('/save', { form: { fname: c.name, data } });
      expect(s.status()).toBe(200);

      // Tornado's SaveHandler reads `data` with get_argument(), which strips leading/trailing
      // whitespace (the native template ends in "\n"); that is existing Tornado behaviour, so the
      // expected bytes are the fixture trimmed. Node must hold exactly what Tornado holds.
      const expected = data.trim();
      const raw = node('raw', testUser.email, c.name);
      expect(raw.found).toBe(true);
      expect(raw.data).toBe(expected);
      expect(sha(raw.data)).toBe(sha(expected));
      const viaTornado = await (await page.request.post('/insert', { form: { filename: c.name } })).json();
      expect(raw.data).toBe(viaTornado.data);

      // Node's own /usersheet route returns the same sheet (parsed JSON, so compared semantically)
      const us = node('usersheet', testUser.email, c.name);
      expect(us.status).toBe(200);
      expect(us.body).toEqual(JSON.parse(expected));
    });
  }

  test('overwrite from the other side wins and stays one object', async ({ authenticatedPage: page, testUser }) => {
    const name = 'GST% ₹ हिंदी overwrite';
    expect(node('save', testUser.email, name, read('invoice.xlsx.sc')).status).toBe(200);
    const second = read('customers.csv.sc');
    expect((await page.request.post('/save', { form: { fname: name, data: second } })).status()).toBe(200);
    expect(node('raw', testUser.email, name).data).toBe(second);
    const dir = await page.request.get('/save');
    expect((await dir.text()).split(name).length - 1).toBeGreaterThanOrEqual(1);
  });
});

test.describe('M3 Node write failures are not reported as success', () => {
  test('POST /save returns 500 when the write fails (bucket unavailable)', async ({ testUser }) => {
    const r = spawnSync('docker', ['compose', 'exec', '-T', '-e', 'S3_BUCKET_NAME=no-such-bucket-m3', 'node-interop',
      'node', 'tests/helpers/interop-cli.js', 'save', testUser.email, 'failing'],
      { cwd: process.cwd(), input: 'x', encoding: 'utf8' });
    expect(r.status).toBe(0);
    const out = JSON.parse(r.stdout.trim().split('\n').pop()!);
    expect(out.status).toBe(500);
    expect(out.body.data).toBeUndefined();
  });
});

test.describe.serial('M3 /nodeinterop/health and failure mapping', () => {
  test('anonymous callers are rejected, logged-in callers see the sidecar', async ({ request, authenticatedPage: page }) => {
    expect((await request.get('/nodeinterop/health')).status()).toBe(401);
    const h = await page.request.get('/nodeinterop/health');
    expect(h.status()).toBe(200);
    const b = await h.json();
    expect(b.status).toBe('ok');
    expect(b.sidecar).toEqual({ status: 'ok', storage: 's3' });
  });

  test('sidecar stopped -> 502', async ({ authenticatedPage: page }) => {
    sh('docker compose stop node-interop');
    try {
      const r = await page.request.get('/nodeinterop/health');
      expect(r.status()).toBe(502);
      expect((await r.json()).error).toBe('sidecar_unreachable');
    } finally {
      sh('docker compose start node-interop');
      await waitHealthy();
    }
  });

  test('sidecar paused (connection accepted, no answer) -> 504 after the explicit timeout', async ({ authenticatedPage: page }) => {
    sh(`docker pause ${cid()}`);
    try {
      const t0 = Date.now();
      const r = await page.request.get('/nodeinterop/health', { timeout: 30000 });
      expect(r.status()).toBe(504);
      expect((await r.json()).error).toBe('sidecar_timeout');
      expect(Date.now() - t0).toBeLessThan(15000);
    } finally {
      sh(`docker unpause ${cid()}`);
      await waitHealthy();
    }
  });

  test('sidecar recovered -> 200 again', async ({ authenticatedPage: page }) => {
    expect((await page.request.get('/nodeinterop/health')).status()).toBe(200);
  });
});
