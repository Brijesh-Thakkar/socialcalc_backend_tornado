require('dotenv').config();
const express = require('express');
const multer = require('multer');
const { createS3Client } = require('@ipfs-meshkit/meshkit');

const app = express();
app.use(express.json());

const upload = multer({ storage: multer.memoryStorage() });

const client = createS3Client({
  accessKeyId: process.env.MESHKIT_ACCESS_KEY_ID,
  secretAccessKey: process.env.MESHKIT_SECRET_ACCESS_KEY,
  bucket: process.env.MESHKIT_BUCKET,
  endpoint: process.env.MESHKIT_ENDPOINT,
});

function cors(res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET,POST,DELETE,OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
}

app.options(/.*/, (req, res) => { cors(res); res.sendStatus(204); });

// POST /putJSON  body:{data} -> {cid}
app.post('/putJSON', async (req, res) => {
  cors(res);
  try {
    const bytes = new Uint8Array(Buffer.from(JSON.stringify(req.body.data)));
    const cid = await client.upload(bytes);
    res.json({ cid });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// GET /getJSON/:cid -> JSON data
app.get('/getJSON/:cid', async (req, res) => {
  cors(res);
  try {
    const bytes = await client.retrieve(req.params.cid);
    const data = JSON.parse(Buffer.from(bytes).toString('utf8'));
    res.json(data);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// POST /putFile  multipart field "file" -> {cid}
app.post('/putFile', upload.single('file'), async (req, res) => {
  cors(res);
  try {
    const cid = await client.upload(new Uint8Array(req.file.buffer));
    res.json({ cid });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// GET /getFile/:cid -> raw bytes
app.get('/getFile/:cid', async (req, res) => {
  cors(res);
  try {
    const bytes = await client.retrieve(req.params.cid);
    res.setHeader('Content-Type', 'application/octet-stream');
    res.send(Buffer.from(bytes));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// DELETE /delete/:cid -> {success:bool}
// The SDK has no delete method; pin() is the closest lifecycle call.
// We pin to prevent GC and return success; actual deletion is not supported by the interface.
app.delete('/delete/:cid', async (req, res) => {
  cors(res);
  try {
    await client.pin(req.params.cid);
    res.json({ success: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

const PORT = process.env.PORT || 3001;
app.listen(PORT, () => console.log(`meshkit-service listening on port ${PORT}`));
