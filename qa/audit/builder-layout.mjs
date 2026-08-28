// The Builder's new shape: a rail, one section at a time, and a picker that
// inserts a user input into a template field.
//
//   SHOT_DIR=.shots DEMO_PASSWORD=... node qa/audit/builder-layout.mjs
//
// Photographs each rail destination and drives the user-input picker end to
// end, because "the icon renders" and "picking inserts the right template" are
// different claims and only the second one matters.

import { createRequire } from 'node:module';
const require = createRequire(new URL('../../frontend/package.json', import.meta.url));
const { chromium } = require('playwright');
import fs from 'node:fs';

const BASE = process.env.BASE_URL || 'http://localhost:8080';
const OUT = process.env.SHOT_DIR || '.shots';
fs.mkdirSync(OUT, { recursive: true });

const problems = [];
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1680, height: 1000 } });
page.on('console', (m) => {
  if (m.type() === 'error') problems.push(`console: ${m.text().slice(0, 180)}`);
});
page.on('pageerror', (e) => problems.push(`pageerror: ${String(e).slice(0, 180)}`));
page.on('response', (r) => {
  if (r.status() >= 400 && !r.url().includes('favicon')) {
    problems.push(`http ${r.status()} ${r.url().replace(BASE, '')}`);
  }
});

const shot = async (name) => {
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${OUT}/${name}.png` });
  console.log(`  shot: ${name}`);
};

await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
await page.fill('input[type="email"]', process.env.DEMO_EMAIL || 'admin@appbi.local');
await page.fill('input[type="password"]', process.env.DEMO_PASSWORD || '');
await page.click('button[type="submit"]');
await page.waitForURL((u) => !u.pathname.includes('/login'), { timeout: 30000 });

await page.goto(`${BASE}/builder`, { waitUntil: 'networkidle' });
await page.getByRole('button', { name: /tạo connector/i }).first().click();
await page.waitForTimeout(600);
await page.locator('input[placeholder*="Shopify"]').first()
  .fill(`Layout ${new Date().toISOString().slice(11, 19).replace(/:/g, '')}`);
await page.getByRole('button', { name: /tạo và mở/i }).click();
await page.waitForURL(/\/builder\/[0-9a-f-]{36}/, { timeout: 30000 });
const projectId = page.url().split('/').pop();
console.log(`  project ${projectId}`);

// ── the rail ───────────────────────────────────────────────────────────────
const rail = page.getByRole('navigation', { name: /phần của connector/i });
const railItems = await rail.locator('button').evaluateAll(
  (nodes) => nodes.map((n) => n.textContent.trim().replace(/\s+/g, ' ')));
console.log(`  rail: ${JSON.stringify(railItems)}`);
if (railItems.length < 3) problems.push('rail khong co du muc');
await shot('layout-01-cau-hinh-chung');

// ── one section at a time ──────────────────────────────────────────────────
const visibleSections = () => page.evaluate(() => [...document.querySelectorAll('section, div')]
  .filter((el) => el.querySelector(':scope > header, :scope > div > h2, :scope > h2'))
  .length);

for (const [label, name] of [[/tham số người dùng/i, 'layout-02-tham-so'],
                             [/^posts$/i, 'layout-03-stream']]) {
  const item = rail.locator('button').filter({ hasText: label }).first();
  if (!(await item.count())) { problems.push(`rail thieu muc ${label}`); continue; }
  await item.click();
  await page.waitForTimeout(600);
  await shot(name);
}

// ── the picker: does it insert the right template? ─────────────────────────
console.log('\n  == Bộ chọn tham số người dùng ==');
await rail.locator('button').filter({ hasText: /^posts$/i }).first().click();
await page.waitForTimeout(500);

const pathField = page.locator('#stream-path');
await pathField.fill('/service/');
await pathField.click();
const picker = page.locator('#stream-path').locator('..')
  .getByRole('button', { name: /chèn tham số/i }).first();
if (!(await picker.count())) {
  problems.push('khong co nut chen tham so ben canh Duong dan');
} else {
  await picker.click();
  await page.waitForTimeout(400);
  const options = await page.locator('[role="menu"] button, [role="menuitem"]')
    .evaluateAll((nodes) => nodes.map((n) => n.textContent.trim()));
  console.log(`     menu khi chua co tham so: ${JSON.stringify(options)}`);
  await shot('layout-04-picker-rong');
  // With no inputs yet, the only entry creates one -- and lands on the inputs
  // section with a blank row, which is the point of offering it here.
  await page.locator('[role="menu"] button').first().click();
  await page.waitForTimeout(700);
  const onInputs = await page.getByLabel(/^Khóa 1$/i).count()
    || await page.locator('input[aria-label^="Khóa"]').count();
  console.log(`     sau khi bam "tao moi": o nhap khoa tham so = ${onInputs}`);
  if (!onInputs) problems.push('bam "tao tham so moi" khong mo o nhap khoa');
  await shot('layout-05-tao-tham-so');

  // Name it, go back, pick it: the only claim that matters is what lands in
  // the field. `{{ config['x'] }}` typed by hand is one misplaced quote away
  // from a connector that authenticates against a literal string.
  await page.locator('input[aria-label^="Khóa"]').first().fill('access_token');
  await page.waitForTimeout(300);
  await rail.locator('button').filter({ hasText: /^posts$/i }).first().click();
  await page.waitForTimeout(500);
  await page.locator('#stream-path').fill('/service/');
  await page.locator('#stream-path').click();
  await page.locator('#stream-path').locator('..')
    .getByRole('button', { name: /chèn tham số/i }).first().click();
  await page.waitForTimeout(400);
  const listed = await page.locator('[role="menu"] button')
    .evaluateAll((nodes) => nodes.map((n) => n.textContent.trim()));
  console.log(`     menu khi da co tham so: ${JSON.stringify(listed)}`);
  await page.locator('[role="menu"] button').first().click();
  await page.waitForTimeout(500);
  const inserted = await page.locator('#stream-path').inputValue();
  console.log(`     duong dan sau khi chen: ${JSON.stringify(inserted)}`);
  const expected = "/service/{{ config['access_token'] }}";
  if (inserted !== expected) {
    problems.push(`chen sai: mong doi ${expected}, nhan duoc ${inserted}`);
  }
  await shot('layout-06-da-chen');
}

console.log(`\n  == Vấn đề (${problems.length}) ==`);
for (const p of [...new Set(problems)]) console.log(`     ${p}`);
console.log(`  project_id=${projectId}`);
await browser.close();
