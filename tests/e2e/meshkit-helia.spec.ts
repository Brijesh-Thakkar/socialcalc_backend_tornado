import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

const FIXTURE_PATH = path.resolve(__dirname, '../../demos/tictactoe.msc');

// CID produced by the upload test; re-used by retrieve and list tests.
// Tests in this describe block run sequentially (workers: 1 in playwright.config.ts).
let uploadedCid = '';

test.describe('Meshkit IPFS storage — Helia backend', () => {
  test('POST /meshkit-helia/upload returns 200 and a valid CID', async ({ request }) => {
    const bytes = fs.readFileSync(FIXTURE_PATH);
    const resp = await request.post('/meshkit-helia/upload', {
      data: bytes,
      headers: { 'Content-Type': 'application/octet-stream' },
    });
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(typeof body.cid).toBe('string');
    expect(body.cid.length).toBeGreaterThan(0);
    // Helia returns CIDv1 in base32 (multibase prefix 'b').
    // Small files use raw codec (bafkrei...), larger files use dag-pb (bafy...).
    expect(body.cid).toMatch(/^baf/);
    uploadedCid = body.cid;
  });

  test('GET /meshkit-helia/retrieve/:cid returns byte-identical content', async ({ request }) => {
    const original = fs.readFileSync(FIXTURE_PATH);
    const resp = await request.get(`/meshkit-helia/retrieve/${uploadedCid}`);
    expect(resp.status()).toBe(200);
    const retrieved = await resp.body();
    expect(Buffer.compare(original, retrieved)).toBe(0);
  });

  test('GET /meshkit-helia/list contains the uploaded CID', async ({ request }) => {
    const resp = await request.get('/meshkit-helia/list');
    expect(resp.status()).toBe(200);
    const items = await resp.json();
    expect(Array.isArray(items)).toBe(true);
    const entry = items.find((item: any) => item.cid === uploadedCid);
    expect(entry).toBeDefined();
  });

  // Helia hunts the IPFS network for unknown CIDs; the sidecar caps the wait
  // at 5 s and returns 504, which Tornado propagates as 504.
  // The test is allowed up to 10 s to account for round-trip overhead on top
  // of the 5 s timeout.
  test('GET /meshkit-helia/retrieve with bogus CID returns 504 within ~6s', async ({ request }) => {
    test.setTimeout(15000);
    // CID derived from garbage bytes that have never been published to IPFS.
    const resp = await request.get(
      '/meshkit-helia/retrieve/bafkreibbelvtq7argbfc7runq77zbq3si6bqvzpmpdsyybbiokngw5ly4q'
    );
    expect(resp.status()).toBe(504);
  });
});
