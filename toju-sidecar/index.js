'use strict';
/*
 * toju-sidecar — thin HTTP wrapper around @toju.network/sol (the Storacha-Solana-Sdk).
 * ===================================================================================
 *
 * ***  MOCK-BACKED by default  ***
 *
 * Tornado never imports the SDK; it calls this sidecar over HTTP (handlers/toju.py),
 * exactly like the meshkit / fastapi-interop sidecars. This sidecar DOES import the real
 * published SDK (@toju.network/sol, pinned) and drives its real HTTP calls against whatever
 * backend TOJU_API_URL points at — in this integration, the local toju-mock.
 *
 * Why "mock-backed": the real toju.network API is suspended and the on-chain program is not
 * on devnet (see AUDIT_2.md §1), so there is no live backend to sign real transactions
 * against. In MOCK mode this sidecar supplies a fake Solana Connection + an ephemeral
 * keypair signer so the SDK's real upload/confirm/quote/history code paths execute end to
 * end with NO real RPC, NO real money and NO accounts. Flip TOJU_MODE=real (plus a real
 * TOJU_API_URL, SOLANA_RPC and SIDECAR_KEYPAIR) to talk to a real backend later — the HTTP
 * contract this sidecar exercises does not change.
 *
 * Endpoints Tornado calls (see handlers/toju.py):
 *   POST /upload           { sheet, fname?, durationDays? }  -> { cid, url, signature, success, mocked }
 *   GET  /retrieve/:cid                                      -> raw bytes (byte-identical to what was saved)
 *   GET  /status/:cid                                        -> { cid, active, expiresAt, ... }
 *   GET  /health                                             -> { status, mode, backend }
 *
 * Every request must carry  X-Toju-Token: <TOJU_SHARED_SECRET>  (shared secret, like interop).
 */
require('dotenv').config();
const express = require('express');
const { Keypair, PublicKey } = require('@solana/web3.js');
// Real SDK — pinned in package.json to @toju.network/sol@1.0.0.
const { Client, Environment, createDepositTxn, getUserUploadHistory } = require('@toju.network/sol');

const PORT = parseInt(process.env.TOJU_SIDECAR_PORT || '5056', 10);
const TOJU_API_URL = (process.env.TOJU_API_URL || 'http://localhost:5057').replace(/\/$/, '');
const SHARED_SECRET = process.env.TOJU_SHARED_SECRET || '';
// TOJU_MODE must be set EXPLICITLY — there is NO silent default to mock, so a misconfigured
// deployment can never quietly pretend to be real (or quietly be a mock). Allowed: mock | real.
const MODE = (process.env.TOJU_MODE || '').toLowerCase();
if (MODE !== 'mock' && MODE !== 'real') {
  console.error(
    'FATAL: TOJU_MODE must be set explicitly to "mock" or "real" (got %j). Refusing to start.',
    process.env.TOJU_MODE || ''
  );
  process.exit(1);
}
const SOLANA_NETWORK = (process.env.SOLANA_NETWORK || 'devnet').toLowerCase();
const ALLOW_MAINNET = process.env.ALLOW_MAINNET === 'true';
const DEFAULT_DURATION_DAYS = parseInt(process.env.TOJU_DEFAULT_DURATION_DAYS || '1', 10);

// ── Safety rail: NEVER mainnet unless ALLOW_MAINNET=true (never set anywhere) ──
if ((SOLANA_NETWORK.includes('mainnet') || /mainnet/.test(process.env.SOLANA_RPC || '')) && !ALLOW_MAINNET) {
  console.error('FATAL: SOLANA_NETWORK/SOLANA_RPC targets mainnet and ALLOW_MAINNET is not "true". Refusing to start.');
  process.exit(1);
}

// Ephemeral, in-memory, throwaway keypair. In MOCK mode nothing is ever broadcast, so this
// key signs only the locally-assembled (never-sent) transaction the SDK builds.
const keypair = Keypair.generate();

// A fake Connection: the SDK's createDepositTxn only ever calls these three methods on it.
// This is what keeps MOCK mode fully offline (no real Solana RPC).
function mockConnection() {
  return {
    async getLatestBlockhash() {
      return { blockhash: Keypair.generate().publicKey.toBase58(), lastValidBlockHeight: 1 };
    },
    async sendRawTransaction() {
      // "mock-tx-" prefix: never mistakable for a real Solana signature (base58, no hyphens).
      return 'mock-tx-' + Keypair.generate().publicKey.toBase58();
    },
    async confirmTransaction() {
      return { value: { err: null } };
    },
  };
}

function signTransaction(tx) {
  tx.partialSign(keypair);  // keypair.publicKey === the payer we pass in, so serialize() succeeds
  return tx;
}

const app = express();
app.use(express.json({ limit: '50mb' }));

// Shared-secret gate (like interop's X-Interop-Token). Missing/wrong -> 401.
app.use((req, res, next) => {
  if (req.path === '/health') return next();
  const tok = req.get('X-Toju-Token') || '';
  if (!SHARED_SECRET || tok !== SHARED_SECRET) {
    return res.status(401).json({ error: 'unauthorized', message: 'missing or invalid X-Toju-Token', mocked: MODE === 'mock' });
  }
  next();
});

function newClient() {
  // endpoint override forces all SDK HTTP at TOJU_API_URL (the mock), regardless of RPC mapping.
  return new Client({ environment: Environment.devnet, endpoint: TOJU_API_URL });
}

// ── POST /upload ──────────────────────────────────────────────────────────
// Drives the SDK's real deposit flow: GET /pricing/quote -> POST /upload/deposit
// -> (mock chain sign/confirm) -> POST /upload/confirm.
app.post('/upload', async (req, res) => {
  try {
    const { sheet, fname, durationDays } = req.body || {};
    if (typeof sheet !== 'string' || !sheet.length) {
      return res.status(400).json({ error: 'bad_request', message: 'field "sheet" (string) required', mocked: MODE === 'mock' });
    }
    const days = parseInt(durationDays, 10) || DEFAULT_DURATION_DAYS;
    const name = (fname || 'sheet.msc').toString();
    // Build a File the SDK can send (Node 20+ has global File/Blob/FormData/fetch).
    const file = new File([Buffer.from(sheet, 'utf-8')], name, { type: 'text/plain; charset=utf-8' });
    const client = newClient();

    // Exercise the SDK's pricing call (contract-covered), best-effort.
    let estimate = null;
    try { estimate = await client.estimateStorageCost([file], days * 86400); } catch (_) { /* non-fatal */ }

    // Exercise the SDK's real deposit flow with a mock connection + signer (MOCK mode).
    const connection = MODE === 'real' ? realConnection() : mockConnection();
    const result = await createDepositTxn(
      {
        file: [file],
        duration: days * 86400,
        payer: keypair.publicKey,     // matches the signer, so serialize() works
        connection,
        signTransaction,
        directoryName: name,
      },
      TOJU_API_URL
    );
    if (!result.success) {
      return res.status(502).json({ error: 'sdk_failed', message: result.error || 'deposit failed', mocked: MODE === 'mock' });
    }
    res.json({
      cid: result.cid, url: result.url, signature: result.signature,
      success: true, estimate, mode: MODE, mocked: MODE === 'mock',
    });
  } catch (err) {
    console.error('[toju-sidecar] upload error:', err);
    res.status(500).json({ error: 'sidecar_error', message: String(err && err.message || err), mocked: MODE === 'mock' });
  }
});

// ── GET /retrieve/:cid ──────────────────────────────────────────────────────
// The SDK has NO retrieve; retrieval is by gateway URL. We fetch the gateway bytes and
// stream them back byte-for-byte.
app.get('/retrieve/:cid', async (req, res) => {
  try {
    const url = `${TOJU_API_URL}/ipfs/${encodeURIComponent(req.params.cid)}`;
    const r = await fetch(url);
    if (r.status === 404) return res.status(404).json({ error: 'not_found', mocked: MODE === 'mock' });
    if (!r.ok) return res.status(502).json({ error: 'gateway_error', message: 'HTTP ' + r.status, mocked: MODE === 'mock' });
    const buf = Buffer.from(await r.arrayBuffer());
    res.setHeader('Content-Type', r.headers.get('content-type') || 'application/octet-stream');
    res.send(buf);
  } catch (err) {
    res.status(502).json({ error: 'gateway_unreachable', message: String(err && err.message || err), mocked: MODE === 'mock' });
  }
});

// ── GET /status/:cid ──────────────────────────────────────────────────────
// Uses the SDK's getUserUploadHistory (GET /upload/history) to find the CID.
app.get('/status/:cid', async (req, res) => {
  try {
    const hist = await getUserUploadHistory(keypair.publicKey.toBase58(), TOJU_API_URL, { page: 1, limit: 100 });
    const entry = (hist.data || []).find((e) => (e.contentCid || e.cid) === req.params.cid);
    if (!entry) return res.status(404).json({ cid: req.params.cid, active: false, mocked: MODE === 'mock' });
    res.json({
      cid: req.params.cid, active: entry.deletionStatus === 'active',
      expiresAt: entry.expiresAt, txHash: entry.transactionHash || null,
      size: entry.fileSize, mode: MODE, mocked: MODE === 'mock',
    });
  } catch (err) {
    res.status(502).json({ error: 'backend_unreachable', message: String(err && err.message || err), mocked: MODE === 'mock' });
  }
});

app.get('/health', (_req, res) => res.json({ status: 'ok', mode: MODE.toUpperCase() + '-BACKED', backend: TOJU_API_URL, network: SOLANA_NETWORK }));

// Real Connection is only wired when TOJU_MODE=real (future). Kept lazy so MOCK never needs it.
function realConnection() {
  const { Connection } = require('@solana/web3.js');
  const rpc = process.env.SOLANA_RPC || 'https://api.devnet.solana.com';
  return new Connection(rpc, { commitment: 'confirmed', wsEndpoint: '' });
}

if (require.main === module) {
  app.listen(PORT, '0.0.0.0', () => {
    console.log('==================================================================');
    console.log(` toju-sidecar  ***  ${MODE.toUpperCase()}-BACKED  ***`);
    console.log(` listening on :${PORT}  backend=${TOJU_API_URL}  network=${SOLANA_NETWORK}`);
    if (MODE === 'mock') console.log(' MODE=mock: no real Solana RPC, no real IPFS, no money.');
    console.log('==================================================================');
  });
}

module.exports = { app };
