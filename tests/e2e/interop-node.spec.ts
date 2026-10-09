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

// ───────────────────────────── auth + publish/open ─────────────────────────────

/** Run a shell snippet inside the node-interop container (curl is available there). */
function inNode(script: string, env: string[] = []): string {
  const args = ['compose', 'exec', '-T', ...env.flatMap((e) => ['-e', e]), 'node-interop', 'sh', '-c', script];
  const r = spawnSync('docker', args, { cwd: process.cwd(), encoding: 'utf8' });
  expect(r.status, r.stderr).toBe(0);
  return r.stdout;
}
const nodeSecret = () => inNode('printf %s "$NODE_INTEROP_SHARED_SECRET"').trim();

test.describe('M3 interop auth: X-Interop-Token on /v1/*', () => {
  const probe = (header: string) =>
    inNode(`curl -s -o /dev/null -w '%{http_code}' ${header} 'http://localhost:5055/v1/sheets/nothing?owner=nobody'`).trim();

  test('missing token -> 401, wrong token -> 401, right token -> served, /health stays open', () => {
    expect(probe('')).toBe('401');
    expect(probe("-H 'X-Interop-Token: wrong'")).toBe('401');
    expect(probe(`-H 'X-Interop-Token: ${nodeSecret()}'`)).toBe('404');
    expect(inNode("curl -s -o /dev/null -w '%{http_code}' http://localhost:5055/health").trim()).toBe('200');
  });

  test('NODE_INTEROP_SHARED_SECRET unset -> /v1/* answers 503 (fails closed)', () => {
    const out = inNode(
      "node app.js >/dev/null 2>&1 & P=$!; sleep 4; " +
      "curl -s -o /dev/null -w '%{http_code}' -H 'X-Interop-Token: anything' 'http://localhost:5099/v1/sheets/a?owner=u'; kill $P",
      ['PORT=5099', 'NODE_INTEROP_SHARED_SECRET=']).trim();
    expect(out).toBe('503');
  });
});

test.describe.serial('M3 separate Node user records (no shared hashes)', () => {
  test('registering the same email in Node does not touch the Tornado account', async ({ request, testUser, authenticatedPage }) => {
    // testUser is registered in Tornado by authenticatedPage (sha256_crypt record under ["home","users",email]).
    const reg = inNode(`curl -s -o /dev/null -w '%{http_code}' -d 'email=${testUser.email}&password=${testUser.password}' http://localhost:5055/register`).trim();
    expect(reg).toBe('200');
    const nodeLogin = inNode(`curl -s -o /dev/null -w '%{http_code}' -d 'email=${testUser.email}&password=${testUser.password}' http://localhost:5055/login`).trim();
    expect(nodeLogin).toBe('302');
    const nodeBadLogin = inNode(`curl -s -o /dev/null -w '%{http_code} %{redirect_url}' -d 'email=${testUser.email}&password=wrong-pass' http://localhost:5055/login`).trim();
    expect(nodeBadLogin).not.toContain('/save');
    // Tornado still logs the same user in with its own hash
    const t = await request.post('/login', { form: { email: testUser.email, password: testUser.password }, maxRedirects: 0 });
    expect(t.status()).toBe(302);
    expect(t.headers()['location']).toContain('/save');
  });
});

test.describe.serial('M3 publish / open (copy-by-name through the sidecar)', () => {
  const NAME = 'GST% ₹ हिंदी publish';

  test('anonymous callers are rejected', async ({ request }) => {
    expect((await request.post('/nodeinterop/publish', { form: { name: 'x' } })).status()).toBe(401);
    expect((await request.post('/nodeinterop/open', { form: { name: 'x' } })).status()).toBe(401);
  });

  for (const c of CASES) {
    test(`Tornado -> Node -> Tornado keeps the bytes: ${c.file}`, async ({ authenticatedPage: page, testUser }) => {
      const data = read(c.file);
      expect((await page.request.post('/save', { form: { fname: NAME, data } })).status()).toBe(200);
      const stored = (await (await page.request.post('/insert', { form: { filename: NAME } })).json()).data;   // Tornado's own copy

      const pub = await page.request.post('/nodeinterop/publish', { form: { name: NAME } });
      expect(pub.status(), await pub.text()).toBe(200);
      const pubBody = await pub.json();
      expect(pubBody).toMatchObject({ result: 'ok', name: NAME, size: Buffer.byteLength(stored, 'utf8') });
      expect(JSON.stringify(pubBody)).not.toContain(nodeSecret());

      // what Node holds, fetched with the token
      const viaNode = JSON.parse(inNode(
        `curl -s -H "X-Interop-Token: $NODE_INTEROP_SHARED_SECRET" "http://localhost:5055/v1/sheets/$(node -e 'console.log(encodeURIComponent(process.argv[1]))' '${NAME}')?owner=${encodeURIComponent(testUser.email)}"`));
      expect(viaNode.savestr).toBe(stored);

      // open it back under another name: byte-equal again
      const as = NAME + ' copy';
      const open = await page.request.post('/nodeinterop/open', { form: { name: NAME, as } });
      expect(open.status(), await open.text()).toBe(200);
      const back = await (await page.request.post('/insert', { form: { filename: as } })).json();
      expect(back.data).toBe(stored);
      expect(sha(back.data)).toBe(sha(stored));
      // and the opened sheet is also resolvable by Node's own (shared-bucket) storage code
      expect(node('raw', testUser.email, as).data).toBe(stored);
    });
  }

  test('open refuses to overwrite unless overwrite=yes; unknown sheets are 404', async ({ authenticatedPage: page }) => {
    await page.request.post('/save', { form: { fname: 'ow', data: read('invoice.xlsx.sc') } });
    expect((await page.request.post('/nodeinterop/publish', { form: { name: 'ow' } })).status()).toBe(200);
    await page.request.post('/save', { form: { fname: 'ow', data: read('customers.csv.sc') } });
    expect((await page.request.post('/nodeinterop/open', { form: { name: 'ow' } })).status()).toBe(409);
    expect((await page.request.post('/nodeinterop/open', { form: { name: 'ow', overwrite: 'yes' } })).status()).toBe(200);
    expect((await (await page.request.post('/insert', { form: { filename: 'ow' } })).json()).data).toBe(read('invoice.xlsx.sc').trim());
    expect((await page.request.post('/nodeinterop/open', { form: { name: 'never-published', as: 'x2' } })).status()).toBe(404);
    expect((await page.request.post('/nodeinterop/publish', { form: { name: 'no-such-sheet' } })).status()).toBe(404);
  });

  test('publish with the sidecar stopped -> 502, paused -> 504', async ({ authenticatedPage: page }) => {
    await page.request.post('/save', { form: { fname: 'down', data: read('invoice.xlsx.sc') } });
    sh('docker compose stop node-interop');
    try {
      expect((await page.request.post('/nodeinterop/publish', { form: { name: 'down' } })).status()).toBe(502);
    } finally { sh('docker compose start node-interop'); await waitHealthy(); }
    sh(`docker pause ${cid()}`);
    try {
      expect((await page.request.post('/nodeinterop/publish', { form: { name: 'down' }, timeout: 60000 })).status()).toBe(504);
    } finally { sh(`docker unpause ${cid()}`); await waitHealthy(); }
  });
});
