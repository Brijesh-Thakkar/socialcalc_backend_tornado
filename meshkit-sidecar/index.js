'use strict';
require('dotenv').config();
const express = require('express');

const PORT = parseInt(process.env.MESHKIT_SIDECAR_PORT || '5050', 10);
const accessKeyId = process.env.MESHKIT_ACCESS_KEY;
const secretAccessKey = process.env.MESHKIT_SECRET_KEY;
const bucket = process.env.MESHKIT_BUCKET;
const endpoint = process.env.MESHKIT_ENDPOINT;

if (!accessKeyId || !secretAccessKey || !bucket) {
  console.error(
    'FATAL: MESHKIT_ACCESS_KEY, MESHKIT_SECRET_KEY, and MESHKIT_BUCKET are required'
  );
  process.exit(1);
}

let client;
if (endpoint) {
  const { createS3Client } = require('@ipfs-meshkit/meshkit');
  client = createS3Client({ accessKeyId, secretAccessKey, bucket, endpoint });
  console.log('meshkit-sidecar: using S3-compatible backend at', endpoint);
} else {
  const { createFilOneClient } = require('@ipfs-meshkit/meshkit');
  client = createFilOneClient({ accessKeyId, secretAccessKey, bucket });
  console.log('meshkit-sidecar: using default fil.one backend');
}

const app = express();

// GET /health — list() is the lightest round-trip the S3 client exposes
app.get('/health', async (req, res) => {
  try {
    await client.list();
    res.json({ status: 'ok' });
  } catch (err) {
    res.status(503).json({ error: err.message });
  }
});

// POST /upload — raw bytes (application/octet-stream)
app.post('/upload', express.raw({ type: '*/*', limit: '100mb' }), async (req, res) => {
  try {
    const cid = await client.upload(new Uint8Array(req.body));
    res.json({ cid });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// GET /retrieve/:cid — returns raw bytes
app.get('/retrieve/:cid', async (req, res) => {
  try {
    const bytes = await client.retrieve(req.params.cid);
    res.setHeader('Content-Type', 'application/octet-stream');
    res.send(Buffer.from(bytes));
  } catch (err) {
    // SDK message format: "S3 retrieve failed [<status>] for CID <cid>: ..."
    // Only map to 404 when S3 explicitly returned 404 (missing key).
    // Auth errors (403), server errors (5xx), and network failures all get 500
    // so callers see an accurate signal instead of a misleading "not found".
    const is404 = err.message != null && /\[404\]/.test(err.message);
    res.status(is404 ? 404 : 500).json({ error: err.message });
  }
});

// GET /list — returns [{cid, size, uploadedAt}, ...]
app.get('/list', async (req, res) => {
  try {
    const items = await client.list();
    res.json(items);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(`meshkit-sidecar listening on port ${PORT}`);
});
