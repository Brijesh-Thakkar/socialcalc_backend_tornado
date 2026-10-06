import { test, expect } from '@playwright/test';
import { execSync } from 'child_process';

/**
 * Docker / Infrastructure Tests
 *
 * Verifies the Docker Compose stack health without depending on application logic.
 */

test.describe('Docker Compose Stack', () => {
  test('all four containers are running', () => {
    const out = execSync('docker compose ps', {
      cwd: process.cwd(),
      encoding: 'utf8',
    });
    expect(out).toContain('app1');
    expect(out).toContain('app2');
    expect(out).toContain('nginx');
    expect(out).toContain('memcache');

    // None should be in an exit/restart state
    expect(out).not.toMatch(/Exit\s+[^0]/);
    expect(out).not.toContain('Restarting');
  });

  test('memcached is reachable from app1', () => {
    const out = execSync(
      `docker exec tornado_version-app1-1 python3 -c "
import memcache, os
mc = memcache.Client([os.environ.get('MEMCACHE_HOST','127.0.0.1')])
mc.set('docker_test', 'ping')
print(mc.get('docker_test'))
"`,
      { encoding: 'utf8' }
    );
    expect(out.trim()).toBe('ping');
  });

  test('memcached is reachable from app2', () => {
    const out = execSync(
      `docker exec tornado_version-app2-1 python3 -c "
import memcache, os
mc = memcache.Client([os.environ.get('MEMCACHE_HOST','127.0.0.1')])
mc.set('docker_test2', 'pong')
print(mc.get('docker_test2'))
"`,
      { encoding: 'utf8' }
    );
    expect(out.trim()).toBe('pong');
  });

  test('shared memcached: app1 writes, app2 reads', () => {
    execSync(
      `docker exec tornado_version-app1-1 python3 -c "
import memcache, os
mc = memcache.Client([os.environ.get('MEMCACHE_HOST','127.0.0.1')])
mc.set('shared_probe', 'cross_instance_ok')
"`,
      { encoding: 'utf8' }
    );
    const out = execSync(
      `docker exec tornado_version-app2-1 python3 -c "
import memcache, os
mc = memcache.Client([os.environ.get('MEMCACHE_HOST','127.0.0.1')])
print(mc.get('shared_probe'))
"`,
      { encoding: 'utf8' }
    );
    expect(out.trim()).toBe('cross_instance_ok');
  });

  test('Docker DNS resolves app1, app2, memcache from nginx', () => {
    const out = execSync(
      'docker exec tornado_version-nginx-1 getent hosts app1 app2 memcache 2>&1',
      { encoding: 'utf8' }
    );
    expect(out).toContain('app1');
    expect(out).toContain('app2');
    expect(out).toContain('memcache');
  });

  test('app containers have no Python errors in logs', () => {
    const logs = execSync('docker compose logs app1 app2 2>&1', {
      cwd: process.cwd(),
      encoding: 'utf8',
    });
    // Should not have unhandled Python tracebacks
    expect(logs).not.toContain('Traceback (most recent call last)');
    expect(logs).not.toContain('SystemExit');
  });

  test('nginx config validates successfully', () => {
    const out = execSync('docker exec tornado_version-nginx-1 nginx -t 2>&1', {
      encoding: 'utf8',
    });
    expect(out).toContain('syntax is ok');
    expect(out).toContain('test is successful');
  });
});
