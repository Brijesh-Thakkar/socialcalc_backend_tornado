'use strict';
require('dotenv').config();
const express = require('express');
const { Meshkit } = require('@ipfs-meshkit/meshkit');

const PORT = parseInt(process.env.MESHKIT_KUBO_SIDECAR_PORT || '5051', 10);
const IPFS_API_URL = process.env.IPFS_API_URL || 'http://127.0.0.1:5001';

async function start() {
  // Connect to the Kubo daemon — Meshkit.init() health-checks each node
  // and throws immediately if none are reachable, giving us fail-fast startup.
  let client;
  console.log(`meshkit-sidecar-kubo: connecting to Kubo at ${IPFS_API_URL} ...`);
  try {
    client = await Meshkit.init({ nodes: [IPFS_API_URL] });
    console.log('meshkit-sidecar-kubo: daemon reachable, active nodes:', client.activeNodes);
  } catch (err) {
    console.error(`FATAL: cannot reach Kubo daemon at ${IPFS_API_URL} — ${err.message}`);
    process.exit(1);
  }

  const app = express();

  // GET /health — healthCheck() exists in the type definitions but is not
  // implemented on Meshkit v1.2.0. listPins() makes a real round-trip to the
  // Kubo RPC API (ipfs.pin.ls), confirming the daemon is reachable.
  app.get('/health', async (req, res) => {
    try {
      await client.listPins();
      res.json({ status: 'ok', nodes: client.activeNodes });
    } catch (err) {
      res.status(503).json({ status: 'error', error: err.message, nodes: client.activeNodes });
    }
  });

  // POST /upload — raw bytes (application/octet-stream)
  // Pin immediately after upload so Kubo GC cannot reclaim the block.
  app.post('/upload', express.raw({ type: '*/*', limit: '100mb' }), async (req, res) => {
    try {
      const cid = await client.upload(new Uint8Array(req.body));
      await client.pin(cid);
      res.json({ cid });
    } catch (err) {
      res.status(500).json({ error: err.message });
    }
  });

  const RETRIEVE_TIMEOUT_MS = 5000;

  // GET /retrieve/:cid — returns raw bytes
  // Kubo hunts the IPFS network for unknown CIDs and can hang for 20+ seconds.
  // Promise.race caps the wait at 5 s; a timeout returns 504 immediately so
  // the caller gets a fast, deterministic error instead of a silent hang.
  // Note: Promise.race does not cancel the underlying Kubo RPC call — it will
  // eventually settle on its own after the timeout fires.
  app.get('/retrieve/:cid', async (req, res) => {
    const timeoutPromise = new Promise((_, reject) =>
      setTimeout(
        () => reject(new Error(`retrieve timed out after ${RETRIEVE_TIMEOUT_MS / 1000}s`)),
        RETRIEVE_TIMEOUT_MS
      )
    );
    try {
      const bytes = await Promise.race([client.retrieve(req.params.cid), timeoutPromise]);
      res.setHeader('Content-Type', 'application/octet-stream');
      res.send(Buffer.from(bytes));
    } catch (err) {
      const msg = err.message || '';
      if (/timed out/i.test(msg)) {
        return res.status(504).json({ error: msg });
      }
      const is404 = /not found|does not exist|no link named/i.test(msg);
      res.status(is404 ? 404 : 500).json({ error: msg });
    }
  });

  // GET /list — list() throws on Kubo; use listPins() instead.
  // Returns [{cid}] — size and uploadedAt are S3-only metadata, not available from Kubo.
  app.get('/list', async (req, res) => {
    try {
      const cids = await client.listPins();
      res.json(cids.map(cid => ({ cid })));
    } catch (err) {
      res.status(500).json({ error: err.message });
    }
  });

  app.listen(PORT, '0.0.0.0', () => {
    console.log(`meshkit-sidecar-kubo listening on port ${PORT}`);
  });
}

start();
