'use strict';
/*
 * toju-sidecar — thin HTTP wrapper around @toju.network/sol (the Storacha-Solana-Sdk).
 * ===================================================================================
 *
 * Two modes, chosen by TOJU_MODE (REQUIRED, no default):
 *   mock  — supplies a fake Solana Connection + ephemeral signer so the SDK's real
 *           upload/confirm/quote/history HTTP calls run against a local toju-mock with
 *           NO real RPC, NO real money and NO accounts. Used by CI.
 *   real  — loads a real payer keypair (mounted, never committed), uses a real Solana
 *           Connection on SOLANA_CLUSTER, and the SDK broadcasts a real deposit transaction
 *           to a real toju backend (TOJU_API_URL). Used for the on-chain demo / production.
 *
 * Tornado never imports the SDK; it calls this sidecar over HTTP (handlers/toju.py), exactly
 * like the meshkit / fastapi-interop sidecars. The HTTP contract is identical in both modes.
 *
 * Endpoints Tornado calls (see handlers/toju.py):
 *   POST /upload           { sheet, fname?, durationDays? }  -> { cid, url, signature, cluster, explorer, mock, ... }
 *   GET  /retrieve/:cid                                      -> raw bytes (byte-identical to what was saved)
 *   GET  /status/:cid                                        -> { cid, active, expiresAt, ... }
 *   GET  /health                                             -> { status, mode, backend, cluster, payer, programId }
 *
 * Every request must carry  X-Toju-Token: <TOJU_SHARED_SECRET>  (shared secret, like interop).
 * Mainnet is always refused unless ALLOW_MAINNET=true (never set anywhere).
 */
require('dotenv').config();
const fs = require('node:fs');
const express = require('express');
const { Keypair, Connection } = require('@solana/web3.js');
// Real SDK — pinned in package.json to @toju.network/sol@1.0.0.
const { Client, Environment, createDepositTxn, getUserUploadHistory } = require('@toju.network/sol');

const PORT = parseInt(process.env.TOJU_SIDECAR_PORT || '5056', 10);
const TOJU_API_URL = (process.env.TOJU_API_URL || 'http://localhost:5057').replace(/\/$/, '');
const SHARED_SECRET = process.env.TOJU_SHARED_SECRET || '';

// TOJU_MODE must be set EXPLICITLY — there is NO silent default, so a misconfigured deployment
// can never quietly pretend to be real (or quietly be a mock). Allowed: mock | real.
const MODE = (process.env.TOJU_MODE || '').toLowerCase();
if (MODE !== 'mock' && MODE !== 'real') {
  console.error('FATAL: TOJU_MODE must be set explicitly to "mock" or "real" (got %j). Refusing to start.', process.env.TOJU_MODE || '');
  process.exit(1);
}
const IS_REAL = MODE === 'real';

// Solana cluster: SOLANA_CLUSTER (preferred) or legacy SOLANA_NETWORK; default devnet.
const CLUSTER = (process.env.SOLANA_CLUSTER || process.env.SOLANA_NETWORK || 'devnet').toLowerCase();
const ALLOW_MAINNET = process.env.ALLOW_MAINNET === 'true';
const DEFAULT_DURATION_DAYS = parseInt(process.env.TOJU_DEFAULT_DURATION_DAYS || '1', 10);
const PROGRAM_ID = process.env.TOJU_PROGRAM_ID || '';        // informational; validated against the server's instruction
const PAYER_KEYPAIR_PATH = process.env.TOJU_PAYER_KEYPAIR || '';
// IPFS gateway retrieval base (real server pins to IPFS; the mock serves /ipfs itself).
const GATEWAY_URL = (process.env.TOJU_GATEWAY_URL || TOJU_API_URL).replace(/\/$/, '');

const RPC_BY_CLUSTER = {
  devnet: 'https://api.devnet.solana.com',
  testnet: 'https://api.testnet.solana.com',
  'mainnet-beta': 'https://api.mainnet-beta.solana.com',
  mainnet: 'https://api.mainnet-beta.solana.com',
};
const SOLANA_RPC = process.env.SOLANA_RPC || RPC_BY_CLUSTER[CLUSTER] || RPC_BY_CLUSTER.devnet;
const ENV_BY_CLUSTER = {
  devnet: Environment.devnet, testnet: Environment.testnet,
  'mainnet-beta': Environment.mainnet, mainnet: Environment.mainnet,
};
const SDK_ENV = ENV_BY_CLUSTER[CLUSTER] || Environment.devnet;

// ── Safety rail: NEVER mainnet unless ALLOW_MAINNET=true (never set anywhere) ──
if ((/mainnet/.test(CLUSTER) || /mainnet/.test(SOLANA_RPC)) && !ALLOW_MAINNET) {
  console.error('FATAL: SOLANA_CLUSTER/SOLANA_RPC targets mainnet and ALLOW_MAINNET is not "true". Refusing to start.');
  process.exit(1);
}

// Payer keypair: mock uses a throwaway ephemeral key (nothing is ever broadcast); real loads
// a funded keypair from a mounted file (never committed) and signs/broadcasts for real.
let keypair;
if (IS_REAL) {
  if (!PAYER_KEYPAIR_PATH) {
    console.error('FATAL: TOJU_MODE=real requires TOJU_PAYER_KEYPAIR (path to a Solana keypair JSON). Refusing to start.');
    process.exit(1);
  }
  try {
    const secret = Uint8Array.from(JSON.parse(fs.readFileSync(PAYER_KEYPAIR_PATH, 'utf8')));
    keypair = Keypair.fromSecretKey(secret);
  } catch (err) {
    console.error('FATAL: could not load TOJU_PAYER_KEYPAIR from %s — %s', PAYER_KEYPAIR_PATH, err.message);
    process.exit(1);
  }
} else {
  keypair = Keypair.generate();
}

// A fake Connection (mock mode): the SDK's createDepositTxn only calls these three methods.
function mockConnection() {
  return {
    async getLatestBlockhash() { return { blockhash: Keypair.generate().publicKey.toBase58(), lastValidBlockHeight: 1 }; },
    // "mock-tx-" prefix: never mistakable for a real Solana signature (base58, no hyphens).
    async sendRawTransaction() { return 'mock-tx-' + Keypair.generate().publicKey.toBase58(); },
    async confirmTransaction() { return { value: { err: null } }; },
  };
}
let _realConn = null;
function realConnection() {
  if (!_realConn) _realConn = new Connection(SOLANA_RPC, { commitment: 'confirmed', wsEndpoint: '' });
  return _realConn;
}

function signTransaction(tx) {
  tx.partialSign(keypair);  // keypair.publicKey === the payer we pass in, so serialize() succeeds
  return tx;
}

function explorerUrl(sig) {
  if (!IS_REAL || !sig) return null;
  const q = CLUSTER === 'mainnet-beta' || CLUSTER === 'mainnet' ? '' : `?cluster=${CLUSTER}`;
  return `https://explorer.solana.com/tx/${sig}${q}`;
}

const app = express();
app.use(express.json({ limit: '50mb' }));

// Shared-secret gate (like interop's X-Interop-Token). Missing/wrong -> 401.
app.use((req, res, next) => {
  if (req.path === '/health') return next();
  const tok = req.get('X-Toju-Token') || '';
  if (!SHARED_SECRET || tok !== SHARED_SECRET) {
    return res.status(401).json({ error: 'unauthorized', message: 'missing or invalid X-Toju-Token', mock: !IS_REAL });
  }
  next();
});

function newClient() {
  // Cluster-derived Environment; endpoint override forces all SDK HTTP at TOJU_API_URL.
  return new Client({ environment: SDK_ENV, endpoint: TOJU_API_URL });
}

// ── POST /upload ──────────────────────────────────────────────────────────
// Drives the SDK's deposit flow: GET /pricing/quote -> POST /upload/deposit
// -> sign/confirm (mock: fake chain; real: broadcast on SOLANA_CLUSTER) -> POST /upload/confirm.
app.post('/upload', async (req, res) => {
  try {
    const { sheet, fname, durationDays } = req.body || {};
    if (typeof sheet !== 'string' || !sheet.length) {
      return res.status(400).json({ error: 'bad_request', message: 'field "sheet" (string) required', mock: !IS_REAL });
    }
    const days = parseInt(durationDays, 10) || DEFAULT_DURATION_DAYS;
    const name = (fname || 'sheet.msc').toString();
    const file = new File([Buffer.from(sheet, 'utf-8')], name, { type: 'text/plain; charset=utf-8' });
    const client = newClient();

    let estimate = null;
    try { estimate = await client.estimateStorageCost([file], days * 86400); } catch (_) { /* non-fatal */ }

    const connection = IS_REAL ? realConnection() : mockConnection();
    const result = await createDepositTxn(
      { file: [file], duration: days * 86400, payer: keypair.publicKey, connection, signTransaction, directoryName: name },
      TOJU_API_URL
    );
    if (!result.success) {
      return res.status(502).json({ error: 'sdk_failed', message: result.error || 'deposit failed', mock: !IS_REAL });
    }
    res.json({
      cid: result.cid, url: result.url, signature: result.signature,
      fileName: name, cluster: IS_REAL ? CLUSTER : null, explorer: explorerUrl(result.signature),
      success: true, estimate, mode: MODE, mock: !IS_REAL,
    });
  } catch (err) {
    console.error('[toju-sidecar] upload error:', err);
    res.status(500).json({ error: 'sidecar_error', message: String(err && err.message || err), mock: !IS_REAL });
  }
});

// ── GET /retrieve/:cid ──────────────────────────────────────────────────────
// The SDK has NO retrieve; retrieval is by IPFS gateway URL. The real toju server pins each
// upload as a directory, so the file lives at /ipfs/<cid>/<name>; we look the name up from
// history and fall back to /ipfs/<cid>. The mock serves the bytes directly at /ipfs/<cid>.
app.get('/retrieve/:cid', async (req, res) => {
  const cid = req.params.cid;
  try {
    const candidates = [];
    if (IS_REAL) {
      let name = null;
      try {
        const hist = await getUserUploadHistory(keypair.publicKey.toBase58(), TOJU_API_URL, { page: 1, limit: 100 });
        const entry = (hist.data || []).find((e) => (e.contentCid || e.cid) === cid);
        name = entry && entry.fileName;
      } catch (_) { /* fall through to bare cid */ }
      if (name) candidates.push(`${GATEWAY_URL}/ipfs/${encodeURIComponent(cid)}/${encodeURIComponent(name)}`);
    }
    candidates.push(`${GATEWAY_URL}/ipfs/${encodeURIComponent(cid)}`);

    let last = null;
    for (const url of candidates) {
      const r = await fetch(url);
      if (r.ok) {
        const buf = Buffer.from(await r.arrayBuffer());
        res.setHeader('Content-Type', r.headers.get('content-type') || 'application/octet-stream');
        return res.send(buf);
      }
      last = r.status;
    }
    if (last === 404) return res.status(404).json({ error: 'not_found', mock: !IS_REAL });
    return res.status(502).json({ error: 'gateway_error', message: 'HTTP ' + last, mock: !IS_REAL });
  } catch (err) {
    res.status(502).json({ error: 'gateway_unreachable', message: String(err && err.message || err), mock: !IS_REAL });
  }
});

// ── GET /status/:cid ──────────────────────────────────────────────────────
app.get('/status/:cid', async (req, res) => {
  try {
    const hist = await getUserUploadHistory(keypair.publicKey.toBase58(), TOJU_API_URL, { page: 1, limit: 100 });
    const entry = (hist.data || []).find((e) => (e.contentCid || e.cid) === req.params.cid);
    if (!entry) return res.status(404).json({ cid: req.params.cid, active: false, mock: !IS_REAL });
    res.json({
      cid: req.params.cid, active: entry.deletionStatus === 'active',
      expiresAt: entry.expiresAt, txHash: entry.transactionHash || null,
      explorer: explorerUrl(entry.transactionHash), cluster: IS_REAL ? CLUSTER : null,
      size: entry.fileSize, mode: MODE, mock: !IS_REAL,
    });
  } catch (err) {
    res.status(502).json({ error: 'backend_unreachable', message: String(err && err.message || err), mock: !IS_REAL });
  }
});

app.get('/health', (_req, res) => res.json({
  status: 'ok', mode: MODE.toUpperCase() + '-BACKED', backend: TOJU_API_URL,
  cluster: CLUSTER, rpc: IS_REAL ? SOLANA_RPC : null,
  payer: IS_REAL ? keypair.publicKey.toBase58() : null,
  programId: PROGRAM_ID || null, mock: !IS_REAL,
}));

if (require.main === module) {
  app.listen(PORT, '0.0.0.0', () => {
    console.log('==================================================================');
    console.log(` toju-sidecar  ***  ${MODE.toUpperCase()}-BACKED  ***`);
    console.log(` listening on :${PORT}  backend=${TOJU_API_URL}  cluster=${CLUSTER}`);
    if (IS_REAL) {
      console.log(` payer=${keypair.publicKey.toBase58()}  rpc=${SOLANA_RPC}`);
      console.log(' MODE=real: broadcasts REAL transactions and spends REAL funds on the cluster above.');
    } else {
      console.log(' MODE=mock: no real Solana RPC, no real IPFS, no money.');
    }
    console.log('==================================================================');
  });
}

module.exports = { app };
