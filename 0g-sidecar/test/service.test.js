'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { createApp } = require('../service');

async function withServer(app, run) {
  const server = app.listen(0, '127.0.0.1');
  await new Promise(resolve => server.once('listening', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  try { await run(base); } finally { await new Promise(resolve => server.close(resolve)); }
}

test('mock mode archives and returns the same bytes with mock markers', async () => {
  const app = createApp({ mode: 'mock' });
  const sample = Buffer.from('non-sensitive sample sheet');
  await withServer(app, async base => {
    const health = await fetch(`${base}/health`).then(response => response.json());
    assert.equal(health.mock, true);
    const upload = await fetch(`${base}/archive`, {
      method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: sample,
    });
    const result = await upload.json();
    assert.equal(result.mock, true);
    assert.match(result.rootHash, /^0x[0-9a-f]{64}$/);
    const download = await fetch(`${base}/download/${result.rootHash}`);
    assert.equal(download.headers.get('x-zg-mock'), 'true');
    assert.deepEqual(Buffer.from(await download.arrayBuffer()), sample);
  });
});

test('live SDK upload encrypts to wallet and download verifies proof with the wallet key', async () => {
  const calls = {};
  class MemData {
    constructor(bytes) { this.bytes = bytes; }
    async merkleTree() { calls.merkle = true; return [{ rootHash: () => '0x' + 'a'.repeat(64) }, null]; }
  }
  class Indexer {
    constructor(url) { calls.indexerUrl = url; }
    async upload(file, rpc, signer, options) {
      calls.upload = { file, rpc, signer, options };
      return [{ rootHash: '0x' + 'a'.repeat(64), txHash: '0x' + 'b'.repeat(64) }, null];
    }
    async downloadToBlob(rootHash, options) {
      calls.download = { rootHash, options };
      return [new Blob(['verified content']), null];
    }
  }
  class Wallet {
    constructor(key) { calls.privateKey = key; this.signingKey = { publicKey: 'uncompressed-pubkey' }; }
  }
  class JsonRpcProvider {
    constructor(url) { calls.rpcUrl = url; }
    async getNetwork() { return { chainId: 16602n }; }
  }
  const sdk = {
    Indexer, MemData, ethers: {
      JsonRpcProvider, Wallet,
      SigningKey: { computePublicKey: (key, compressed) => `${key}:${compressed}` },
    },
  };
  const env = {
    ZG_PRIVATE_KEY: 'test-key',
    ZG_RPC_URL: 'https://evmrpc-testnet.0g.ai',
    ZG_INDEXER_URL: 'https://indexer-storage-testnet-turbo.0g.ai',
  };
  const app = createApp({ mode: 'live', sdk, env });
  await withServer(app, async base => {
    const upload = await fetch(`${base}/archive`, {
      method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: 'sample',
    });
    const result = await upload.json();
    assert.equal(result.mock, false);
    assert.equal(calls.merkle, true);
    assert.deepEqual(calls.upload.options.encryption, {
      type: 'ecies', recipientPubKey: 'uncompressed-pubkey:true',
    });
    const download = await fetch(`${base}/download/${result.rootHash}`);
    assert.equal(download.headers.get('x-zg-mock'), 'false');
    assert.equal(await download.text(), 'verified content');
    assert.deepEqual(calls.download.options, {
      proof: true, decryption: { privateKey: 'test-key' },
    });
  });
});

test('live mode fails clearly if the wallet key is missing', () => {
  assert.throws(() => createApp({ mode: 'live', env: {} }), /ZG_MODE=live requires ZG_PRIVATE_KEY/);
});

test('live mode rejects non-Galileo endpoints', () => {
  assert.throws(() => createApp({
    mode: 'live',
    env: { ZG_PRIVATE_KEY: 'test-key', ZG_RPC_URL: 'https://evmrpc.0g.ai' },
  }), /restricted to the Galileo testnet/);
});

test('live upload failures return the SDK error without leaking the configured key', async () => {
  class MemData { async merkleTree() { return [{ rootHash: () => '0x' + 'a'.repeat(64) }, null]; } }
  class Indexer {
    async upload() { return [null, new Error('actual SDK error with secret-test-key')]; }
  }
  class Wallet { constructor() { this.signingKey = { publicKey: 'pub' }; } }
  const sdk = {
    Indexer, MemData,
    ethers: {
      JsonRpcProvider: class { async getNetwork() { return { chainId: 16602n }; } }, Wallet,
      SigningKey: { computePublicKey: () => 'compressed-key' },
    },
  };
  const app = createApp({ mode: 'live', sdk, env: { ZG_PRIVATE_KEY: 'secret-test-key' } });
  await withServer(app, async base => {
    const response = await fetch(`${base}/archive`, {
      method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: 'sheet',
    });
    assert.equal(response.status, 502);
    const body = await response.json();
    assert.equal(body.error, 'actual SDK error with [redacted]');
    assert.equal(body.mock, false);
  });
});

test('mock mode rejects empty and oversized payloads', async () => {
  const app = createApp({ mode: 'mock', env: { ZG_MAX_PAYLOAD_BYTES: '4' } });
  await withServer(app, async base => {
    assert.equal((await fetch(`${base}/archive`, {
      method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: '',
    })).status, 400);
    assert.equal((await fetch(`${base}/archive`, {
      method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: '12345',
    })).status, 413);
  });
});
