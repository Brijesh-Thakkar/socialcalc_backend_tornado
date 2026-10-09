import { test, expect } from './fixtures/auth.fixture';
import { execSync, spawnSync } from 'child_process';

/**
 * MOCK-BACKED toju (Storacha-Solana-Sdk) sheet storage — E2E through the full stack:
 *   browser/API -> Tornado /toju/* (handlers/toju.py) -> toju-sidecar -> @toju.network/sol -> toju-mock
 *
 * There is NO real IPFS, NO real Solana and NO money here: toju-sidecar runs TOJU_MODE=mock and
 * drives the real SDK against the local toju-mock. See toju-mock/CONTRACT.md.
 *
 * Covered: save a ₹/Hindi/GST% invoice -> CID -> retrieve (byte-identical); status; anonymous
 * rejection; shared-secret token missing/wrong (checked at the sidecar, from inside the network);
 * and the 502 (sidecar stopped) / 504 (sidecar paused) error mapping.
 */

// A SocialCalc-style save string with rupee, Devanagari and a literal GST% — the exact
// non-ASCII payload that must survive the round trip byte-for-byte.
const SHEET =
  'version:1.5\n' +
  'cell:A1:t:चालान ₹ GST%\n' +
  'cell:A2:t:कुल\n' +
  'cell:B2:v:9374.68\n';

function cid(service: string): string {
  return execSync(`docker compose ps -q ${service}`, { cwd: process.cwd(), encoding: 'utf8' }).trim();
}
function sh(cmd: string) { return execSync(cmd, { cwd: process.cwd(), encoding: 'utf8' }); }

async function waitSidecarUp(request: any) {
  for (let i = 0; i < 40; i++) {
    const h = await request.get('/toju/health').catch(() => null);
    if (h && h.status() === 200) return;
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error('toju-sidecar did not come back up');
}

test.describe('toju storage (MOCK-BACKED)', () => {
  let savedCid = '';

  test('health is proxied and anonymous callers are rejected', async ({ request }) => {
    const h = await request.get('/toju/health');
    expect(h.status()).toBe(200);
    const body = await h.json();
    expect(body.sidecar.mode).toContain('MOCK');
    // Anonymous (no login cookie) must be refused by the authed routes.
    const save = await request.post('/toju/save', { form: { content: SHEET } });
    expect(save.status()).toBe(401);
    const ret = await request.get('/toju/retrieve/bafkmockX');
    expect(ret.status()).toBe(401);
  });

  test('save the ₹/Hindi/GST% invoice returns a CID', async ({ authenticatedPage }) => {
    const r = await authenticatedPage.request.post('/toju/save', {
      form: { content: SHEET, fname: 'invoice.msc', durationDays: '2' },
    });
    expect(r.status()).toBe(200);
    const j = await r.json();
    expect(j.result).toBe('ok');
    expect(typeof j.cid).toBe('string');
    expect(j.cid.length).toBeGreaterThan(0);
    expect(j.mocked).toBe(true);           // never let this masquerade as the real backend
    savedCid = j.cid;
  });

  test('retrieve by CID is byte-identical to what was saved', async ({ authenticatedPage }) => {
    expect(savedCid).not.toBe('');
    const r = await authenticatedPage.request.get(`/toju/retrieve/${savedCid}`);
    expect(r.status()).toBe(200);
    const got = await r.body();
    expect(Buffer.compare(got, Buffer.from(SHEET, 'utf-8'))).toBe(0);
  });

  test('status reports the CID active', async ({ authenticatedPage }) => {
    const r = await authenticatedPage.request.get(`/toju/status/${savedCid}`);
    expect(r.status()).toBe(200);
    const j = await r.json();
    expect(j.cid).toBe(savedCid);
    expect(j.active).toBe(true);
  });

  test('shared-secret token: the sidecar rejects missing/wrong tokens', async ({}) => {
    // Hit the sidecar directly from inside the network (it is not exposed to the host).
    // Tornado always sends the right token; here we prove the sidecar enforces it.
    const app = cid('app1');
    const curl = (hdr: string) =>
      sh(`docker exec ${app} python3 -c "import urllib.request,urllib.error;` +
         `req=urllib.request.Request('http://toju-sidecar:5056/upload',data=b'{}',method='POST',` +
         `headers={'Content-Type':'application/json'${hdr}});` +
         `\nimport sys\ntry:\n r=urllib.request.urlopen(req);print(r.status)\nexcept urllib.error.HTTPError as e:print(e.code)"`).trim();
    expect(curl('')).toBe('401');                                   // missing token
    expect(curl(",'X-Toju-Token':'definitely-wrong'")).toBe('401'); // wrong token
  });

  test('error mapping: sidecar stopped -> 502, paused (timeout) -> 504', async ({ authenticatedPage, request }) => {
    const req = authenticatedPage.request;
    const id = cid('toju-sidecar');
    try {
      // Paused: connection established but no response -> Tornado request timeout -> 504.
      sh(`docker pause ${id}`);
      const slow = await req.get(`/toju/status/${savedCid || 'bafkmockX'}`);
      expect(slow.status()).toBe(504);
      sh(`docker unpause ${id}`);

      // Stopped: connection refused -> 502.
      sh(`docker stop ${id}`);
      const down = await req.post('/toju/save', { form: { content: SHEET } });
      expect(down.status()).toBe(502);
      const downHealth = await request.get('/toju/health');
      expect(downHealth.status()).toBe(502);
    } finally {
      spawnSync('docker', ['unpause', id], { cwd: process.cwd() });
      sh(`docker start ${id}`);
      await waitSidecarUp(request);
    }
  });
});
