import { test, expect } from '@playwright/test';
import { execSync } from 'child_process';

/**
 * Load Balancing Tests
 *
 * These tests prove that Nginx distributes requests across both Tornado
 * containers using round-robin. They inspect the Nginx access log (which
 * includes the upstream= field) via docker exec.
 */

function getNginxUpstreamCounts(): { [ip: string]: number } {
  try {
    const raw = execSync(
      'docker compose logs nginx 2>&1 | grep "upstream=" | grep -oP "upstream=\\K[^\\s]+"',
      { cwd: process.cwd(), encoding: 'utf8' }
    );
    const counts: { [ip: string]: number } = {};
    for (const line of raw.trim().split('\n')) {
      if (!line) continue;
      counts[line] = (counts[line] || 0) + 1;
    }
    return counts;
  } catch {
    return {};
  }
}

test.describe('Load Balancing', () => {
  test('nginx distributes requests across both Tornado instances', async ({ request }) => {
    // Snapshot counts before
    const before = getNginxUpstreamCounts();

    // Send 20 requests to get a statistically reliable distribution
    for (let i = 0; i < 20; i++) {
      await request.get('/login');
    }

    // Snapshot counts after
    const after = getNginxUpstreamCounts();

    // Calculate how many new requests landed on each upstream
    const newHits: { [ip: string]: number } = {};
    for (const [ip, count] of Object.entries(after)) {
      newHits[ip] = count - (before[ip] || 0);
    }

    const upstreams = Object.keys(newHits).filter((ip) => newHits[ip] > 0);

    // Both app containers must have received at least one request
    expect(upstreams.length).toBeGreaterThanOrEqual(2);

    // Neither container should have received all requests (max 19 of 20)
    for (const ip of upstreams) {
      expect(newHits[ip]).toBeLessThan(20);
    }
  });

  test('both Tornado containers are running and reachable', async ({ request }) => {
    // Verify the whole stack is up before other tests run
    const resp = await request.get('/login');
    expect(resp.status()).toBe(200);

    // Check container health via docker ps
    const ps = execSync('docker compose ps --format json 2>/dev/null || docker compose ps', {
      cwd: process.cwd(),
      encoding: 'utf8',
    });
    expect(ps).toContain('app1');
    expect(ps).toContain('app2');
    expect(ps).toContain('nginx');
    expect(ps).toContain('memcache');
  });
});
