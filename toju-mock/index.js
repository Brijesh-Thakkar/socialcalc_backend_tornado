'use strict';
/*
 * toju-mock — MOCK-BACKED local stand-in for the toju.network (Storacha-Solana-Sdk) server.
 * =========================================================================================
 *
 * THIS IS A MOCK. It is NOT the real toju backend and does NO real IPFS pinning,
 * NO real Solana RPC, and NO real payment. It exists so the Tornado <-> toju-sidecar
 * integration can be built and tested with NO hosted backend, NO accounts and NO money,
 * per option C in MENTOR_FEEDBACK.md.
 *
 * It implements EXACTLY the HTTP endpoints that @toju.network/sol@1.0.0 calls, derived
 * from the SDK + server source with file:line cites (see ../toju-mock/CONTRACT.md).
 *
 * Mocked endpoints (SDK-driven):
 *   POST /upload/deposit            (multipart) -> { message, cid, instructions[], ... }
 *   POST /upload/confirm            (json)      -> { verified, message, deposit, url }
 *   GET  /pricing/quote             ?size&duration -> { quote:{...}, success }
 *   GET  /pricing/sol                            -> { price, timestamp }
 *   GET  /upload/history            ?userAddress&page&limit -> { data:[], next, page, limit, total }
 *   GET  /storage/renewal-cost      ?cid&duration -> { costInLamports, costInSOL, ... }
 *   POST /storage/renew             (json)      -> { instructions[] }
 *   POST /storage/confirm-renewal   (json)      -> { url }
 * Mock-defined (NOT SDK surface; the SDK has no retrieve — retrieval is by gateway URL):
 *   GET  /ipfs/:cid[/:name]                      -> raw stored bytes (byte-identical)
 *   GET  /status/:cid                            -> { cid, active, expiresAt, txHash, mocked }
 *   GET  /health                                 -> { status, mode }
 *
 * Every response carries "mocked": true where there is room for it, and the banner below
 * is logged on startup, so nobody mistakes this for the real service.
 */
const express = require('express');
const crypto = require('crypto');
const multer = require('multer');

const PORT = parseInt(process.env.TOJU_MOCK_PORT || '5057', 10);
// A fixed, syntactically-valid base58 program id so the SDK's `new PublicKey(programId)`
// never throws. This is the Solana System Program; the mock never submits anything.
const MOCK_PROGRAM_ID = '11111111111111111111111111111111';
// Mock economics (clearly fake, round numbers): 1000 lamports per byte per day.
const RATE_PER_BYTE_PER_DAY = 1000;
const MIN_DURATION_DAYS = 1;
const MOCK_SOL_USD = 150;
const DAY = 86400;

const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 50 * 1024 * 1024 } });
const app = express();
app.use(express.json({ limit: '50mb' }));

// In-memory store: cid -> { bytes, contentType, name, publicKey, durationDays, expiresAt, txHash, active }
const store = new Map();

function mockCid(bytes) {
  // Deterministic, content-addressed-ish, but UNMISTAKABLY a mock ("bafkmock" prefix).
  const hex = crypto.createHash('sha256').update(bytes).digest('hex');
  return 'bafkmock' + hex.slice(0, 52);
}

function depositInstruction(publicKey) {
  // Shape mirrors server/src/controllers/solana.controller.ts:51-61 (programId, keys[], base64 data).
  // The only required signer is the payer, so the sidecar's ephemeral keypair can sign & serialize().
  return {
    programId: MOCK_PROGRAM_ID,
    keys: [{ pubkey: publicKey, isSigner: true, isWritable: true }],
    data: Buffer.from('MOCK-DEPOSIT').toString('base64'),
  };
}

// ── POST /upload/deposit ──────────────────────────────────────────────────
// SDK: packages/sol/src/payment.ts:36-47 sends multipart {file[], duration, publicKey, userEmail?, directoryName?}
// Server: server/src/controllers/upload.controller.ts:162-315
app.post('/upload/deposit', upload.array('file'), (req, res) => {
  const files = req.files || [];
  const { publicKey, duration } = req.body;
  if (!publicKey) return res.status(400).json({ message: 'Invalid Solana public key', mocked: true });
  if (!files.length) return res.status(400).json({ message: 'No files selected', mocked: true });
  const durationSeconds = parseInt(duration, 10);
  if (Number.isNaN(durationSeconds) || durationSeconds < MIN_DURATION_DAYS * DAY) {
    return res.status(400).json({ message: 'Duration must be at least 1 day', mocked: true });
  }
  // Concatenate all file bytes into one directory-ish blob and key it by a CID.
  const bytes = Buffer.concat(files.map((f) => f.buffer));
  const cid = mockCid(bytes);
  const durationDays = Math.floor(durationSeconds / DAY);
  const expiresAt = new Date(Date.now() + durationDays * DAY * 1000).toISOString();
  store.set(cid, {
    bytes,
    contentType: files[0].mimetype || 'application/octet-stream',
    name: files[0].originalname || 'upload',
    publicKey, durationDays, expiresAt, txHash: null, active: false,
  });
  console.log(`[toju-mock] deposit: cid=${cid} bytes=${bytes.length} days=${durationDays}`);
  res.status(200).json({
    message: 'MOCK deposit instruction ready — sign to finalize upload',
    cid,
    instructions: [depositInstruction(publicKey)],
    fileCount: files.length,
    totalSize: bytes.length,
    files: files.map((f) => ({ name: f.originalname, size: f.size, type: f.mimetype })),
    mocked: true,
  });
});

// ── POST /upload/confirm ──────────────────────────────────────────────────
// SDK: packages/sol/src/payment.ts:122-131 sends json {cid, transactionHash}
// Server: server/src/controllers/upload.controller.ts:581-635
app.post('/upload/confirm', (req, res) => {
  const { cid, transactionHash } = req.body || {};
  if (!cid || !transactionHash) {
    return res.status(400).json({ message: 'CID and transaction hash are required', mocked: true });
  }
  const rec = store.get(cid);
  if (!rec) return res.status(404).json({ message: 'No pending upload found for this CID', mocked: true });
  rec.txHash = transactionHash;
  rec.active = true;
  const gateway = `http://localhost:${PORT}/ipfs/${cid}`;
  console.log(`[toju-mock] confirm: cid=${cid} tx=${transactionHash}`);
  res.status(200).json({
    verified: true,
    message: 'MOCK upload confirmed successfully',
    deposit: { contentCid: cid, transactionHash, expiresAt: rec.expiresAt, deletionStatus: 'active' },
    url: gateway,
    mocked: true,
  });
});

// ── GET /pricing/quote ──────────────────────────────────────────────────────
// SDK: packages/sol/src/client.ts:143-150 reads { quote: { totalCost } }
// Server: server/src/controllers/pricing.controller.ts:13-37 ; shape server/src/types.ts QuoteOutput
app.get('/pricing/quote', (req, res) => {
  const size = parseInt(req.query.size, 10) || 0;
  const duration = parseInt(req.query.duration, 10) || 0;
  const effectiveDuration = Math.max(duration, MIN_DURATION_DAYS);
  const totalCost = size * effectiveDuration * RATE_PER_BYTE_PER_DAY; // lamports (SDK divides by 1e9)
  res.status(200).json({
    quote: { effectiveDuration, ratePerBytePerDay: RATE_PER_BYTE_PER_DAY, totalCost },
    success: true,
    mocked: true,
  });
});

// ── GET /pricing/sol ────────────────────────────────────────────────────────
// SDK: packages/sol/src/client.ts:240-243 reads { price }
// Server: server/src/controllers/pricing.controller.ts:39-51
app.get('/pricing/sol', (_req, res) => {
  res.status(200).json({ price: MOCK_SOL_USD, timestamp: Date.now(), mocked: true });
});

// ── GET /upload/history ─────────────────────────────────────────────────────
// SDK: packages/sol/src/upload-history.ts:343-368 requires Array.isArray(data.data)
// Server: server/src/routes/upload.route.ts:37
app.get('/upload/history', (req, res) => {
  const userAddress = req.query.userAddress;
  const page = parseInt(req.query.page, 10) || 1;
  const limit = parseInt(req.query.limit, 10) || 20;
  if (!userAddress) return res.status(400).json({ message: 'userAddress required', mocked: true });
  const all = [...store.entries()]
    .filter(([, r]) => r.publicKey === userAddress)
    .map(([cid, r]) => ({
      contentCid: cid, cid, fileName: r.name, fileSize: r.bytes.length,
      durationDays: r.durationDays, expiresAt: r.expiresAt, transactionHash: r.txHash,
      deletionStatus: r.active ? 'active' : 'pending',
    }));
  const start = (page - 1) * limit;
  const slice = all.slice(start, start + limit);
  res.status(200).json({
    data: slice, next: start + limit < all.length ? page + 1 : null,
    page, limit, total: all.length, mocked: true,
  });
});

// ── GET /storage/renewal-cost ────────────────────────────────────────────────
// SDK: packages/sol/src/payment.ts:193-208 ; Server: storage.controller.ts:92-99
app.get('/storage/renewal-cost', (req, res) => {
  const cid = req.query.cid;
  const days = parseInt(req.query.duration, 10) || 0;
  const rec = store.get(cid);
  if (!rec) return res.status(404).json({ message: 'deposit not found', mocked: true });
  const costInLamports = rec.bytes.length * days * RATE_PER_BYTE_PER_DAY;
  res.status(200).json({
    newExpirationDate: new Date(Date.now() + days * DAY * 1000).toISOString(),
    currentExpirationDate: rec.expiresAt, additionalDays: days,
    costInLamports, costInSOL: costInLamports / 1e9,
    fileDetails: { cid, size: rec.bytes.length }, mocked: true,
  });
});

// ── POST /storage/renew ──────────────────────────────────────────────────────
// SDK: packages/sol/src/payment.ts:240-255 reads { instructions }
app.post('/storage/renew', (req, res) => {
  const { cid, publicKey } = req.body || {};
  if (!cid || !publicKey) return res.status(400).json({ message: 'cid and publicKey required', mocked: true });
  res.status(200).json({ instructions: [depositInstruction(publicKey)], mocked: true });
});

// ── POST /storage/confirm-renewal ────────────────────────────────────────────
// SDK: packages/sol/src/payment.ts:278-289 reads { url }
app.post('/storage/confirm-renewal', (req, res) => {
  const { cid, duration } = req.body || {};
  const rec = store.get(cid);
  if (rec && duration) rec.expiresAt = new Date(Date.now() + parseInt(duration, 10) * DAY * 1000).toISOString();
  res.status(200).json({ url: `http://localhost:${PORT}/ipfs/${cid}`, message: 'MOCK renewed', mocked: true });
});

// ── Gateway retrieve (NOT an SDK endpoint — retrieval is by gateway URL) ──────
function serveCid(req, res) {
  const rec = store.get(req.params.cid);
  if (!rec) return res.status(404).json({ error: 'not found', mocked: true });
  res.setHeader('Content-Type', rec.contentType || 'application/octet-stream');
  res.setHeader('X-Toju-Mock', 'true');
  res.send(rec.bytes);
}
app.get('/ipfs/:cid', serveCid);
app.get('/ipfs/:cid/:name', serveCid);

// ── Status (mock-defined convenience) ─────────────────────────────────────────
app.get('/status/:cid', (req, res) => {
  const rec = store.get(req.params.cid);
  if (!rec) return res.status(404).json({ cid: req.params.cid, active: false, mocked: true });
  res.status(200).json({
    cid: req.params.cid, active: rec.active, expiresAt: rec.expiresAt,
    txHash: rec.txHash, size: rec.bytes.length, mocked: true,
  });
});

app.get('/health', (_req, res) => res.json({ status: 'ok', mode: 'MOCK-BACKED', mocked: true }));

if (require.main === module) {
  app.listen(PORT, '0.0.0.0', () => {
    console.log('==================================================================');
    console.log(' toju-mock  ***  MOCK-BACKED  ***  NOT the real toju.network server');
    console.log(` listening on :${PORT}  — no IPFS, no Solana, no payments, no money`);
    console.log('==================================================================');
  });
}

module.exports = { app, mockCid, store };
