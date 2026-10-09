'use strict';
/*
 * Contract test — fails LOUDLY if @toju.network/sol's HTTP requests stop matching the mock.
 * ==========================================================================================
 *
 * This drives the REAL SDK (@toju.network/sol, pinned) exactly the way toju-sidecar does, against
 * a throwaway recording server that captures every request. It then asserts the SDK sent EXACTLY
 * the endpoints + fields that toju-mock implements (and that CONTRACT.md documents). If a future
 * SDK bump changes a path, verb, query param or body field, this test throws and names the drift,
 * so the mock + CONTRACT.md can be updated deliberately instead of silently diverging.
 *
 * Run:  cd toju-sidecar && npm test        (no docker, no network, no money)
 */
const http = require('http');
const assert = require('assert');
const { Keypair } = require('@solana/web3.js');
const { Client, Environment, createDepositTxn, getUserUploadHistory } = require('@toju.network/sol');

const seen = [];                       // every request the SDK made: {method, path, query, contentType, raw}
const keypair = Keypair.generate();
const PUBKEY = keypair.publicKey.toBase58();

function readBody(req) {
  return new Promise((resolve) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => resolve(Buffer.concat(chunks)));
  });
}

// Recording server: records, then returns the minimal valid shape the SDK consumes.
const server = http.createServer(async (req, res) => {
  const u = new URL(req.url, 'http://x');
  const raw = await readBody(req);
  seen.push({
    method: req.method, path: u.pathname,
    query: Object.fromEntries(u.searchParams.entries()),
    contentType: req.headers['content-type'] || '', raw: raw.toString('utf-8'),
  });
  res.setHeader('Content-Type', 'application/json');
  if (u.pathname === '/pricing/quote') {
    return res.end(JSON.stringify({ quote: { effectiveDuration: 1, ratePerBytePerDay: 1000, totalCost: 1000 }, success: true }));
  }
  if (u.pathname === '/upload/deposit') {
    // Echo the payer pubkey from the multipart body so createDepositTxn can build/sign a tx.
    const m = raw.toString('utf-8').match(/name="publicKey"\r\n\r\n([^\r\n]+)/);
    const payer = m ? m[1] : PUBKEY;
    return res.end(JSON.stringify({
      message: 'ok', cid: 'bafkmockCONTRACT',
      instructions: [{ programId: '11111111111111111111111111111111',
        keys: [{ pubkey: payer, isSigner: true, isWritable: true }],
        data: Buffer.from('MOCK').toString('base64') }],
    }));
  }
  if (u.pathname === '/upload/confirm') {
    return res.end(JSON.stringify({ verified: true, url: 'http://x/ipfs/bafkmockCONTRACT', message: 'ok' }));
  }
  if (u.pathname === '/upload/history') {
    return res.end(JSON.stringify({ data: [{ contentCid: 'bafkmockCONTRACT', deletionStatus: 'active' }], next: null, page: 1, limit: 100, total: 1 }));
  }
  res.statusCode = 404;
  res.end(JSON.stringify({ error: 'unexpected endpoint: ' + u.pathname }));
});

function mockConnection() {
  return {
    async getLatestBlockhash() { return { blockhash: Keypair.generate().publicKey.toBase58(), lastValidBlockHeight: 1 }; },
    async sendRawTransaction() { return 'MOCKSIG'; },
    async confirmTransaction() { return { value: { err: null } }; },
  };
}

// The contract we assert the SDK still satisfies. Edit DELIBERATELY when the SDK changes.
const EXPECTED = [
  { method: 'GET', path: '/pricing/quote', query: ['size', 'duration'] },
  { method: 'POST', path: '/upload/deposit', multipartFields: ['file', 'duration', 'publicKey'] },
  { method: 'POST', path: '/upload/confirm', jsonFields: ['cid', 'transactionHash'] },
  { method: 'GET', path: '/upload/history', query: ['userAddress', 'page', 'limit'] },
];

function findReq(exp) {
  return seen.find((r) => r.method === exp.method && r.path === exp.path);
}

async function main() {
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${server.address().port}`;

  // 1) estimate -> GET /pricing/quote
  const client = new Client({ environment: Environment.devnet, endpoint: base });
  const file = new File([Buffer.from('version:1.5\ncell:A1:t:₹ GST%\n', 'utf-8')], 'inv.msc', { type: 'text/plain' });
  await client.estimateStorageCost([file], 2 * 86400);

  // 2) deposit flow -> POST /upload/deposit + POST /upload/confirm (mock chain)
  const result = await createDepositTxn(
    { file: [file], duration: 2 * 86400, payer: keypair.publicKey, connection: mockConnection(),
      signTransaction: (tx) => { tx.partialSign(keypair); return tx; }, directoryName: 'inv.msc' },
    base
  );
  assert.strictEqual(result.success, true, 'createDepositTxn should succeed against the recording server');

  // 3) history -> GET /upload/history
  await getUserUploadHistory(PUBKEY, base, { page: 1, limit: 100 });

  server.close();

  // ── Assert the contract ───────────────────────────────────────────────
  const failures = [];
  for (const exp of EXPECTED) {
    const req = findReq(exp);
    if (!req) { failures.push(`MISSING: SDK never sent ${exp.method} ${exp.path}`); continue; }
    for (const q of exp.query || []) {
      if (!(q in req.query)) failures.push(`${exp.method} ${exp.path}: missing query param "${q}" (saw: ${Object.keys(req.query).join(',') || 'none'})`);
    }
    for (const f of exp.multipartFields || []) {
      if (!req.contentType.startsWith('multipart/form-data')) failures.push(`${exp.path}: expected multipart, got "${req.contentType}"`);
      if (!req.raw.includes(`name="${f}"`)) failures.push(`${exp.path}: missing multipart field "${f}"`);
    }
    for (const f of exp.jsonFields || []) {
      let body = {};
      try { body = JSON.parse(req.raw); } catch (_) { failures.push(`${exp.path}: body is not JSON`); }
      if (!(f in body)) failures.push(`${exp.path}: missing JSON field "${f}" (saw: ${Object.keys(body).join(',') || 'none'})`);
    }
  }
  // Also flag any request to an endpoint the mock/CONTRACT.md does NOT know about.
  const known = new Set(EXPECTED.map((e) => e.path));
  for (const r of seen) {
    if (!known.has(r.path)) failures.push(`UNKNOWN: SDK sent ${r.method} ${r.path} which the mock does not implement`);
  }

  if (failures.length) {
    console.error('\n❌ SDK ↔ mock CONTRACT DRIFT DETECTED:\n  - ' + failures.join('\n  - '));
    console.error('\nUpdate toju-mock/index.js and toju-mock/CONTRACT.md to match the new SDK, then re-run.\n');
    process.exit(1);
  }
  console.log('✓ contract OK — SDK requests match the mock:');
  for (const e of EXPECTED) console.log(`    ${e.method} ${e.path}`);
  process.exit(0);
}

main().catch((e) => { console.error('contract test crashed:', e); process.exit(1); });
