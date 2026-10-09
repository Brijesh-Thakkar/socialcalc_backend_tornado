import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

const FIXTURE_PATH = path.resolve(__dirname, '../../demos/tictactoe.msc');

// CID produced by the upload test; re-used by retrieve and list tests.
// Tests in this describe block run sequentially (workers: 1 in playwright.config.ts).
let uploadedCid = '';

test.describe('Meshkit IPFS storage', () => {
  test('POST /meshkit/upload returns 200 and a valid CID', async ({ request }) => {
    const bytes = fs.readFileSync(FIXTURE_PATH);
    const resp = await request.post('/meshkit/upload', {
      data: bytes,
      headers: { 'Content-Type': 'application/octet-stream' },
    });
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(typeof body.cid).toBe('string');
    expect(body.cid.length).toBeGreaterThan(0);
    expect(body.cid).toMatch(/^baf/);
    uploadedCid = body.cid;
  });

  test('GET /meshkit/retrieve/:cid returns byte-identical content', async ({ request }) => {
    const original = fs.readFileSync(FIXTURE_PATH);
    const resp = await request.get(`/meshkit/retrieve/${uploadedCid}`);
    expect(resp.status()).toBe(200);
    const retrieved = await resp.body();
    expect(Buffer.compare(original, retrieved)).toBe(0);
  });

  test('GET /meshkit/list contains the uploaded CID', async ({ request }) => {
    const resp = await request.get('/meshkit/list');
    expect(resp.status()).toBe(200);
    const items = await resp.json();
    expect(Array.isArray(items)).toBe(true);
    const entry = items.find((item: any) => item.cid === uploadedCid);
    expect(entry).toBeDefined();
  });

  test('GET /meshkit/retrieve with bogus CID returns 404', async ({ request }) => {
    const resp = await request.get(
      '/meshkit/retrieve/bafkreiaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
    );
    expect(resp.status()).toBe(404);
  });
});
