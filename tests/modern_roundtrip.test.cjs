const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { readFile } = require('node:fs/promises');
const path = require('node:path');
const { setTimeout: delay } = require('node:timers/promises');
const { test } = require('node:test');
const { chromium } = require('playwright');

const repoRoot = path.resolve(__dirname, '..');
const fixturePath = path.join(repoRoot, 'templates', 'Cupcake.msc');
const viteCli = path.join(repoRoot, 'modern-client', 'node_modules', 'vite', 'bin', 'vite.js');
const previewUrl = 'http://127.0.0.1:4173/modern/';

async function waitForPreview(server) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (server.exitCode !== null) throw new Error(`Vite preview exited with ${server.exitCode}`);
    try {
      const response = await fetch(previewUrl);
      if (response.ok) return;
    } catch {}
    await delay(100);
  }
  throw new Error('Vite preview did not become ready');
}

function sheetIds(workbook) {
  return Object.keys(workbook.sheetArr || {});
}

function cellCoordinates(saveString) {
  return [...saveString.matchAll(/^cell:([^:]+):/gm)].map((match) => match[1]).sort();
}

function cellRecords(saveString) {
  return new Map([...saveString.matchAll(/^(cell:[^\n]+)$/gm)].map((match) => [match[1].split(':')[1], match[1]]));
}

function sheetString(workbook, sheetId) {
  return workbook.sheetArr[sheetId].sheetstr.savestr;
}

test('Cupcake and app-generated workbooks survive modern load/save/reload', async (t) => {
  const fixtureText = await readFile(fixturePath, 'utf8');
  const fixture = JSON.parse(fixtureText);
  let savedData = null;
  const server = spawn(process.execPath, [viteCli, 'preview', '--host', '127.0.0.1', '--port', '4173', '--strictPort'], {
    cwd: path.join(repoRoot, 'modern-client'),
    stdio: 'ignore',
  });
  t.after(() => server.kill('SIGTERM'));
  await waitForPreview(server);

  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage();
  page.setDefaultTimeout(20000);
  await page.route('**/api/v1/modern/sheet*', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ fname: 'Cupcake', data: savedData || fixtureText }),
  }));
  await page.route('**/save', async (route) => {
    const body = new URLSearchParams(route.request().postData() || '');
    assert.equal(body.get('fname'), 'Cupcake');
    savedData = body.get('data');
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ data: 'Done' }) });
  });

  await page.goto(`${previewUrl}?fname=Cupcake`);
  await page.getByRole('status').filter({ hasText: 'Loaded “Cupcake”' }).waitFor();
  await page.locator('#te_griddiv').waitFor();
  await page.locator('ion-button').filter({ hasText: /^Save$/ }).click();
  await page.getByRole('status').filter({ hasText: 'Saved “Cupcake”' }).waitFor();

  const afterFixtureLoad = JSON.parse(savedData);
  assert.equal(afterFixtureLoad.numsheets, fixture.numsheets);
  assert.deepEqual(sheetIds(afterFixtureLoad), sheetIds(fixture));
  for (const sheetId of sheetIds(fixture)) {
    const sourceCells = cellCoordinates(sheetString(fixture, sheetId));
    const savedCells = cellCoordinates(sheetString(afterFixtureLoad, sheetId));
    assert.deepEqual(savedCells, sourceCells, `${sheetId} cell coordinates changed`);
    const sourceRecords = cellRecords(sheetString(fixture, sheetId));
    const savedRecords = cellRecords(sheetString(afterFixtureLoad, sheetId));
    const changed = [...sourceRecords.keys()].filter((coord) => sourceRecords.get(coord) !== savedRecords.get(coord));
    assert.equal(changed.length, 0, `${sheetId} cell contents or formatting changed at ${changed.slice(0, 8).join(', ')}`);
  }

  await page.reload();
  await page.getByRole('status').filter({ hasText: 'Loaded “Cupcake”' }).waitFor();
  await page.locator('#te_griddiv').waitFor();
  await page.locator('ion-button').filter({ hasText: /^Save$/ }).click();
  await page.getByRole('status').filter({ hasText: 'Saved “Cupcake”' }).waitFor();
  const afterReload = JSON.parse(savedData);
  assert.equal(afterReload.numsheets, afterFixtureLoad.numsheets);
  assert.deepEqual(sheetIds(afterReload), sheetIds(afterFixtureLoad));
  for (const sheetId of sheetIds(afterFixtureLoad)) {
    assert.deepEqual(
      [...cellRecords(sheetString(afterReload, sheetId)).entries()],
      [...cellRecords(sheetString(afterFixtureLoad, sheetId)).entries()],
      `${sheetId} cell contents or formatting changed after app-save reload`,
    );
    assert.deepEqual(
      cellCoordinates(sheetString(afterReload, sheetId)),
      cellCoordinates(sheetString(afterFixtureLoad, sheetId)),
      `${sheetId} cell coordinates changed after app-save reload`,
    );
  }
});
