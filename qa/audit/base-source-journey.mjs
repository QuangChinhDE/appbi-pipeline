// Create a Base source the way a person does: click, type, test, save.
//
// Usage (cwd does not matter):
//   SHOT_DIR=.shots DEMO_PASSWORD=... node qa/audit/base-source-journey.mjs
//
// This is the check that a form is usable, as opposed to present. Reading the
// component tells you the fields exist; only driving it tells you the domain
// dropdown has the right options, the test button reaches Base, and the whole
// thing ends on a saved source.
// playwright lives in `frontend/node_modules`, and ESM resolves bare specifiers
// from the *importing file's* directory -- not the working directory. These
// scripts used to sit inside `frontend/`, where a bare import worked; they
// moved to `qa/audit/` and every one of them has been unrunnable since, while
// still carrying a "run from frontend/" comment that cannot help. `cd` does
// not change ESM resolution.
import { createRequire } from 'node:module';
const require = createRequire(new URL('../../frontend/package.json', import.meta.url));
const { chromium } = require('playwright');
import fs from 'node:fs';

const BASE = 'http://localhost:8080';
const OUT = process.env.SHOT_DIR || '.shots';
fs.mkdirSync(OUT, { recursive: true });
const TOKENS = JSON.parse(fs.readFileSync(new URL('../../secrets/base-tokens.json', import.meta.url), 'utf8'));

const errors = [];
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text().slice(0, 140)); });
page.on('pageerror', (e) => errors.push(String(e).slice(0, 140)));
page.on('response', (r) => {
  if (r.status() >= 400 && !r.url().includes('/test')) {
    errors.push(`http ${r.status()} ${r.url().replace(BASE, '')}`);
  }
});

const shot = async (n) => { await page.waitForTimeout(800); await page.screenshot({ path: `${OUT}/${n}.png`, fullPage: true }); };

await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
await page.fill('#email', 'admin@appbi.local');
await page.fill('#password', process.env.DEMO_PASSWORD);
await page.click('button[type=submit]');
await page.waitForURL('**/overview', { timeout: 30000 });

await page.goto(`${BASE}/sources/new`, { waitUntil: 'networkidle' });
await page.getByText('Base Workflow', { exact: false }).first().click();
await page.waitForTimeout(600);
await page.getByRole('button', { name: /Tiếp tục|Continue/i }).first().click();
await page.waitForTimeout(1500);

// Fill it in exactly as a user would: name, token, domain.
await page.locator('input#name, input[id*=name]').first().fill('Base Workflow — production');
const secret = page.locator('input[type=password]').first();
await secret.fill(TOKENS.workflow);
const domain = page.locator('select').first();
if (await domain.count()) {
  await domain.selectOption('base.com.vn');
  console.log('  domain set to base.com.vn from the dropdown');
}
await shot('10-filled');

await page.getByRole('button', { name: /Tiếp tục|Continue/i }).first().click();
console.log('  running the connection test…');
await page.waitForTimeout(3000);
// The check starts a container; give it room.
for (let i = 0; i < 40; i += 1) {
  const text = (await page.locator('body').innerText()).toLowerCase();
  if (text.includes('thành công') || text.includes('thất bại') || text.includes('lỗi')) break;
  await page.waitForTimeout(3000);
}
await shot('11-tested');

const body = await page.locator('body').innerText();
const ok = /thành công/i.test(body);
console.log(`  test result on screen: ${ok ? 'SUCCESS' : 'not successful'}`);
console.log('  ' + body.split('\n').filter((l) => /thành công|thất bại|lỗi|token/i.test(l)).slice(0, 3).join(' | ').slice(0, 200));

if (ok) {
  await page.getByRole('button', { name: /Lưu|Save/i }).first().click();
  await page.waitForTimeout(4000);
  await shot('12-saved');
  console.log(`  after save: ${page.url().replace(BASE, '')}`);
}

console.log(`\nerrors: ${errors.length}`);
for (const e of [...new Set(errors)].slice(0, 8)) console.log(`  ${e}`);
await browser.close();
