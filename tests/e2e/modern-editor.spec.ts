import { readFileSync } from 'node:fs';
import path from 'node:path';
import { test, expect } from './fixtures/auth.fixture';

const cupcakeSave = readFileSync(path.resolve(__dirname, '../../templates/Cupcake.msc'), 'utf8');
const cupcake = JSON.parse(cupcakeSave);

function cellRecords(saveString: string): Map<string, string> {
  return new Map(
    [...saveString.matchAll(/^(cell:[^\n]+)$/gm)].map((match) => [match[1].split(':')[1], match[1]]),
  );
}

function sheetString(workbook: any, sheetId: string): string {
  return workbook.sheetArr[sheetId].sheetstr.savestr;
}

test('modern editor edits a cell and preserves the workbook through save and reload', async ({ authenticatedPage }) => {
  const fname = `modern_Cupcake_${Date.now()}`;
  const createResponse = await authenticatedPage.evaluate(async ({ fname, data }) => {
    const response = await fetch('/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8' },
      body: new URLSearchParams({ fname, data }),
    });
    return { status: response.status, body: await response.json() };
  }, { fname, data: cupcakeSave });
  expect(createResponse.status).toBe(200);
  expect(createResponse.body.data).toBe('Done');

  await authenticatedPage.goto(`/modern/?fname=${encodeURIComponent(fname)}`);
  await expect(authenticatedPage.getByRole('status')).toContainText(`Loaded “${fname}”`);
  await authenticatedPage.locator('#te_griddiv').waitFor();

  // Editing starts in the selected cell, with no modal or separate editor page.
  await authenticatedPage.locator('#cell_B2').click();
  const inlineEditor = authenticatedPage.locator('.sc-inline-cell-editor');
  await expect(inlineEditor).toBeVisible();
  await inlineEditor.fill('Modern editor edit');
  await inlineEditor.press('Enter');
  await expect.poll(() => authenticatedPage.evaluate(() => (window as any).SocialCalc.WorkBookControlSaveSheet().includes('Modern editor edit'))).toBe(true);

  await authenticatedPage.locator('.modern-actions input[value="Save"]').click();
  await expect(authenticatedPage.getByRole('status')).toContainText(`Saved “${fname}”`);

  const firstModernSave = await authenticatedPage.evaluate(() => (window as any).SocialCalc.WorkBookControlSaveSheet());
  const first = JSON.parse(firstModernSave);
  expect(first.numsheets).toBe(cupcake.numsheets);
  expect(Object.keys(first.sheetArr)).toEqual(Object.keys(cupcake.sheetArr));
  for (const sheetId of Object.keys(cupcake.sheetArr)) {
    const actual = cellRecords(sheetString(first, sheetId));
    const original = cellRecords(sheetString(cupcake, sheetId));
    if (sheetId === 'sheet1') {
      expect(actual.get('B2')).toContain('Modern editor edit');
      actual.delete('B2');
      original.delete('B2');
    }
    expect([...actual.entries()]).toEqual([...original.entries()]);
  }

  await authenticatedPage.reload();
  await expect(authenticatedPage.getByRole('status')).toContainText(`Loaded “${fname}”`);
  await authenticatedPage.locator('#te_griddiv').waitFor();
  const reloaded = await authenticatedPage.evaluate(() => (window as any).SocialCalc.WorkBookControlSaveSheet());
  const second = JSON.parse(reloaded);
  expect(second.numsheets).toBe(first.numsheets);
  expect(Object.keys(second.sheetArr)).toEqual(Object.keys(first.sheetArr));
  for (const sheetId of Object.keys(first.sheetArr)) {
    expect([...cellRecords(sheetString(second, sheetId)).entries()]).toEqual(
      [...cellRecords(sheetString(first, sheetId)).entries()],
    );
  }
});
