'use strict';
// dotenv must load before any env-var reads; ESM side-effect import does this.
import 'dotenv/config';
import express from 'express';
import { createHelia } from 'helia';
import { unixfs } from '@helia/unixfs';
import { FsBlockstore } from 'blockstore-fs';
import { FsDatastore } from 'datastore-fs';
import { CID } from 'multiformats/cid';
import { promises as fsp } from 'fs';
import path from 'path';

const PORT             = parseInt(process.env.MESHKIT_HELIA_SIDECAR_PORT || '5052', 10);
const BLOCKSTORE_PATH  = process.env.HELIA_BLOCKSTORE_PATH || '/data/helia/blocks';
const DATASTORE_PATH   = process.env.HELIA_DATASTORE_PATH  || '/data/helia/datastore';
const CIDS_PATH        = process.env.HELIA_CIDS_PATH       || '/data/helia/cids.json';

const RETRIEVE_TIMEOUT_MS = 5000;

// ── Persistent CID index ─────────────────────────────────────────────────────
// IPFS has no "list everything I've added" primitive, so we maintain a JSON
// file alongside the blockstore that tracks upload CIDs across restarts.

async function loadCids() {
  try {
    const raw = await fsp.readFile(CIDS_PATH, 'utf8');
    return new Set(JSON.parse(raw));
  } catch {
    return new Set();
  }
}

async function saveCids(cidSet) {
  await fsp.mkdir(path.dirname(CIDS_PATH), { recursive: true });
  await fsp.writeFile(CIDS_PATH, JSON.stringify([...cidSet]), 'utf8');
}

// ── Main ─────────────────────────────────────────────────────────────────────

async function start() {
  await fsp.mkdir(BLOCKSTORE_PATH, { recursive: true });
  await fsp.mkdir(DATASTORE_PATH,  { recursive: true });

  const blockstore = new FsBlockstore(BLOCKSTORE_PATH);
  const datastore  = new FsDatastore(DATASTORE_PATH);

  let helia;
  try {
    helia = await createHelia({ blockstore, datastore });
    console.log(
      'meshkit-sidecar-helia: Helia node started, peer id:',
      helia.libp2p.peerId.toString()
    );
  } catch (err) {
    console.error('FATAL: failed to start Helia node —', err.message);
    process.exit(1);
  }

  const fs   = unixfs(helia);
  const cids = await loadCids();

  const app = express();

  // GET /health — verify the in-process Helia node is live; no external ping needed.
  app.get('/health', (req, res) => {
    const status = helia?.libp2p?.status;
    if (status === 'started') {
      res.json({ status: 'ok', peerId: helia.libp2p.peerId.toString() });
    } else {
      res.status(503).json({ status: 'error', error: `Helia libp2p status: ${status}` });
    }
  });

  // POST /upload — raw bytes → add to Helia, persist CID, return { cid }
  app.post('/upload', express.raw({ type: '*/*', limit: '100mb' }), async (req, res) => {
    try {
      const cid    = await fs.addBytes(new Uint8Array(req.body));
      const cidStr = cid.toString();
      cids.add(cidStr);
      await saveCids(cids);
      res.json({ cid: cidStr });
    } catch (err) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /retrieve/:cid — returns raw bytes
  // Helia hunts the IPFS network for unknown CIDs and can hang indefinitely.
  // Promise.race caps the wait at 5 s and returns 504 on timeout.
  // The AbortController fires 500 ms later to cancel the background cat so the
  // async iterator is cleaned up rather than leaked indefinitely.
  app.get('/retrieve/:cid', async (req, res) => {
    const ac = new AbortController();

    const timeoutPromise = new Promise((_, reject) =>
      setTimeout(() => {
        ac.abort();
        reject(new Error(`retrieve timed out after ${RETRIEVE_TIMEOUT_MS / 1000}s`));
      }, RETRIEVE_TIMEOUT_MS)
    );

    async function doRetrieve() {
      const cid    = CID.parse(req.params.cid);
      const chunks = [];
      for await (const chunk of fs.cat(cid, { signal: ac.signal })) {
        chunks.push(chunk);
      }
      return Buffer.concat(chunks);
    }

    // Attach a no-op catch so that when the timeout fires first and aborts the
    // cat, the resulting AbortError from doRetrieve does not become an
    // unhandled rejection.
    const retrieveOp = doRetrieve();
    retrieveOp.catch(() => {});

    try {
      const bytes = await Promise.race([retrieveOp, timeoutPromise]);
      res.setHeader('Content-Type', 'application/octet-stream');
      res.send(bytes);
    } catch (err) {
      const msg = err.message || '';
      if (/timed out/i.test(msg)) {
        return res.status(504).json({ error: msg });
      }
      const is404 = /not found|does not exist|no block/i.test(msg);
      res.status(is404 ? 404 : 500).json({ error: msg });
    }
  });

  // GET /list — return CIDs tracked since this sidecar first started
  app.get('/list', (req, res) => {
    res.json([...cids].map(cid => ({ cid })));
  });

  app.listen(PORT, '0.0.0.0', () => {
    console.log(`meshkit-sidecar-helia listening on port ${PORT}`);
  });
}

start();
