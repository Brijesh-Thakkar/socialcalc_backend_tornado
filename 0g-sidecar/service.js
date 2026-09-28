'use strict';

const express = require('express');
const crypto = require('node:crypto');

const MAX_PAYLOAD_BYTES = Number(process.env.ZG_MAX_PAYLOAD_BYTES || 1048576);
const GALILEO_RPC_URL = 'https://evmrpc-testnet.0g.ai';
const GALILEO_INDEXERS = new Set([
  'https://indexer-storage-testnet-turbo.0g.ai',
  'https://indexer-storage-testnet-standard.0g.ai',
]);

function createApp({ mode, sdk, env = process.env }) {
  if (!['mock', 'live'].includes(mode)) {
    throw new Error('ZG_MODE must be explicitly set to mock or live');
  }
  if (mode === 'live' && !env.ZG_PRIVATE_KEY) {
    throw new Error('ZG_MODE=live requires ZG_PRIVATE_KEY');
  }

  const app = express();
  const maxPayloadBytes = Number(env.ZG_MAX_PAYLOAD_BYTES || MAX_PAYLOAD_BYTES);
  const mockFiles = new Map();
  const rpcUrl = env.ZG_RPC_URL || GALILEO_RPC_URL;
  const indexerUrl = env.ZG_INDEXER_URL || 'https://indexer-storage-testnet-turbo.0g.ai';
  if (mode === 'live' && (rpcUrl !== GALILEO_RPC_URL || !GALILEO_INDEXERS.has(indexerUrl))) {
    throw new Error('Live 0G mode is restricted to the Galileo testnet RPC and indexer');
  }
  let clients;

  function getClients() {
    if (!clients) {
      const { Indexer, MemData } = sdk || require('@0gfoundation/0g-storage-ts-sdk');
      const ethers = sdk?.ethers || require('ethers');
      const provider = new ethers.JsonRpcProvider(rpcUrl);
      const signer = new ethers.Wallet(env.ZG_PRIVATE_KEY, provider);
      clients = { indexer: new Indexer(indexerUrl), MemData, ethers, signer, provider };
    }
    return clients;
  }

  app.get('/health', (_req, res) => res.json({ status: 'ok', mode, mock: mode === 'mock' }));

  const errorPayload = message => ({ error: message, mock: mode === 'mock' });
  const errorText = error => {
    const message = error?.message || '0G request failed';
    return env.ZG_PRIVATE_KEY ? message.split(env.ZG_PRIVATE_KEY).join('[redacted]') : message;
  };

  app.post('/archive', express.raw({ type: '*/*', limit: maxPayloadBytes }), async (req, res) => {
    if (!req.body?.length) return res.status(400).json(errorPayload('Archive payload is required'));
    if (req.body.length > maxPayloadBytes) return res.status(413).json(errorPayload('Archive payload is too large'));
    if (mode === 'mock') {
      const rootHash = `0x${crypto.createHash('sha256').update(req.body).digest('hex')}`;
      mockFiles.set(rootHash, Buffer.from(req.body));
      return res.json({ rootHash, txHash: `mock-${rootHash.slice(2, 18)}`, mock: true });
    }

    try {
      const { indexer, MemData, ethers, signer, provider } = getClients();
      const network = await provider.getNetwork();
      if (BigInt(network.chainId) !== 16602n) {
        throw new Error(`Configured 0G RPC returned chain ID ${network.chainId}; expected Galileo chain ID 16602`);
      }
      const data = new Uint8Array(req.body);
      const file = new MemData(data);
      const [tree, treeError] = await file.merkleTree();
      if (treeError) throw treeError;
      if (!tree?.rootHash()) throw new Error('SDK did not produce a Merkle root hash');

      const recipientPubKey = ethers.SigningKey.computePublicKey(signer.signingKey.publicKey, true);
      const [tx, uploadError] = await indexer.upload(file, rpcUrl, signer, {
        encryption: { type: 'ecies', recipientPubKey },
      });
      if (uploadError) throw uploadError;
      if (!tx?.rootHash || !tx?.txHash) throw new Error('SDK upload returned no rootHash or txHash');
      return res.json({ rootHash: tx.rootHash, txHash: tx.txHash, mock: false });
    } catch (error) {
      console.error('0G archive failed:', errorText(error));
      return res.status(502).json(errorPayload(errorText(error)));
    }
  });

  app.get('/download/:rootHash', async (req, res) => {
    const { rootHash } = req.params;
    if (mode === 'mock') {
      const data = mockFiles.get(rootHash);
      if (!data) return res.status(404).json(errorPayload('Archive not found'));
      res.set('Content-Type', 'application/octet-stream');
      res.set('X-ZG-Mock', 'true');
      return res.send(data);
    }
    try {
      const { indexer } = getClients();
      const [blob, downloadError] = await indexer.downloadToBlob(rootHash, {
        proof: true,
        decryption: { privateKey: env.ZG_PRIVATE_KEY },
      });
      if (downloadError) throw downloadError;
      res.set('Content-Type', 'application/octet-stream');
      res.set('X-ZG-Mock', 'false');
      return res.send(Buffer.from(await blob.arrayBuffer()));
    } catch (error) {
      console.error('0G download failed:', errorText(error));
      return res.status(502).json(errorPayload(errorText(error)));
    }
  });

  app.use((error, _req, res, _next) => {
    const status = error.status === 413 ? 413 : 400;
    res.status(status).json(errorPayload(status === 413 ? 'Archive payload is too large' : 'Invalid archive payload'));
  });

  return app;
}

module.exports = { createApp };
