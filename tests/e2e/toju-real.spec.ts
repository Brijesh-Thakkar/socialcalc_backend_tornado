import { test, expect } from './fixtures/auth.fixture';

/**
 * toju storage - REAL mode (TOJU_MODE=real), verified on Solana DEVNET.
 *
 *   browser -> Tornado /toju/* -> toju-sidecar (real) -> @toju.network/sol -> toju server
 *            -> real devnet deposit tx + real IPFS pin
 *
 * Runs ONLY when both are true:
 *   1. TOJU_REAL_E2E=1 is set (it spends a few lamports of devnet SOL from the payer key), and
 *   2. the stack's sidecar reports mock=false (brought up with docker-compose.toju-real.yml).
 * CI never sets TOJU_REAL_E2E and runs the mock stack, so this spec is skipped there.
 */
const RPC = process.env.SOLANA_RPC || 'https://api.devnet.solana.com';

test.describe('toju storage (REAL, devnet)', () => {
  test.beforeEach(async ({ request }) => {
    test.skip(process.env.TOJU_REAL_E2E !== '1', 'set TOJU_REAL_E2E=1 to run (spends devnet SOL)');
    const h = await request.get('/toju/health');
    const body = h.ok() ? await h.json() : {};
    test.skip(body?.sidecar?.mock !== false, 'stack is in MOCK mode');
  });

  test('save the rupee/Hindi/GST% invoice: real CID + devnet tx, MOCK badge hidden, tx link shown, byte-equal retrieve', async ({ authenticatedPage }) => {
    const page = authenticatedPage;
    // The toju server rejects an already-stored file (409), so every run needs unique content.
    const sheet =
      'version:1.5\n' +
      'cell:A1:t:चालान ₹ GST%\n' +
      'cell:A2:t:कुल\n' +
      'cell:B2:v:9374.68\n' +
      `cell:A3:t:run ${Date.now()}-${Math.floor(Math.random() * 1e6)}\n`;

    await page.goto('/static/toju-demo.html');
    // Banner reflects REAL mode (not the mock notice).
    await expect(page.locator('[data-testid="toju-banner"]')).toContainText('REAL');
    await expect(page.locator('[data-testid="toju-banner"]')).toContainText('devnet');

    await page.fill('#content', sheet);
    await page.click('text=Save to toju');

    // Tx link appears; MOCK badge stays hidden.
    const link = page.locator('[data-testid="toju-tx-link"]');
    await expect(link).toBeVisible({ timeout: 60000 });
    await expect(page.locator('[data-testid="toju-mock-badge"]')).toBeHidden();
    const href = (await link.getAttribute('href')) || '';
    expect(href).toMatch(/^https:\/\/explorer\.solana\.com\/tx\/[1-9A-HJ-NP-Za-km-z]{60,100}\?cluster=devnet$/);
    const sig = href.split('/tx/')[1].split('?')[0];

    // Real, non-mock ids.
    const cid = await page.inputValue('#cid');
    expect(cid.startsWith('mock-')).toBe(false);
    expect(cid).toMatch(/^(bafy|bafk|Qm)[A-Za-z0-9]+$/);
    expect(sig.startsWith('mock-tx-')).toBe(false);

    // The transaction really exists on devnet and ran the toju CreateDeposit instruction.
    const rpc = await page.request.post(RPC, {
      data: { jsonrpc: '2.0', id: 1, method: 'getTransaction',
              params: [sig, { commitment: 'confirmed', maxSupportedTransactionVersion: 0, encoding: 'json' }] },
    });
    const tx = (await rpc.json()).result;
    expect(tx, `devnet getTransaction(${sig})`).toBeTruthy();
    expect(tx.meta.err).toBeNull();
    expect((tx.meta.logMessages || []).join('\n')).toContain('Instruction: CreateDeposit');

    // Retrieve: byte-identical via the API...
    const r = await page.request.get(`/toju/retrieve/${cid}`);
    expect(r.status()).toBe(200);
    expect(Buffer.compare(await r.body(), Buffer.from(sheet, 'utf-8'))).toBe(0);
    // ...and through the UI button.
    await page.click('text=Retrieve');
    await expect(page.locator('#out')).toContainText('चालान ₹ GST%');

    // Status view: active, tx link still shown, no MOCK badge.
    await page.click('text=Status');
    await expect(page.locator('#out')).toContainText('"active": true');
    await expect(page.locator('[data-testid="toju-mock-badge-status"]')).toBeHidden();

    console.log(`REAL toju tx: ${href}\nREAL toju CID: ${cid}`);
  });
});
