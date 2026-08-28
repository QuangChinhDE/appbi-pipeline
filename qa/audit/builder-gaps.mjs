// The two flows the fidelity audit does not reach: the manifest view, and what
// happens when a person adds a stream.
//
//   SHOT_DIR=.shots DEMO_PASSWORD=... node qa/audit/builder-gaps.mjs <project_id>

import { createRequire } from 'node:module';
const require = createRequire(new URL('../../frontend/package.json', import.meta.url));
const { chromium } = require('playwright');
import fs from 'node:fs';

const BASE = process.env.BASE_URL || 'http://localhost:8080';
const OUT = process.env.SHOT_DIR || '.shots';
const PROJECT = process.argv[2];
fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } });
const problems = [];
page.on('pageerror', (e) => problems.push(`pageerror: ${String(e).slice(0, 160)}`));
page.on('response', (r) => {
  if (r.status() >= 400 && !r.url().includes('favicon')) {
    problems.push(`http ${r.status()} ${r.url().replace(BASE, '')}`);
  }
});

await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
await page.fill('input[type="email"]', process.env.DEMO_EMAIL || 'admin@appbi.local');
await page.fill('input[type="password"]', process.env.DEMO_PASSWORD || '');
await page.click('button[type="submit"]');
await page.waitForURL((u) => !u.pathname.includes('/login'), { timeout: 30000 });

await page.goto(`${BASE}/builder/${PROJECT}`, { waitUntil: 'networkidle' });
await page.waitForTimeout(1200);

// ── what "Xem manifest" actually gives you ─────────────────────────────────
console.log('\n  == "Xem manifest" ==');
const manifestButton = page.getByRole('button', { name: /xem manifest/i }).first();
if (!(await manifestButton.count())) {
  console.log('     khong co nut');
} else {
  await manifestButton.click();
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${OUT}/gaps-01-manifest.png`, fullPage: true });
  const info = await page.evaluate(() => {
    const dialog = document.querySelector('[role="dialog"]') || document.body;
    const editable = [...dialog.querySelectorAll('textarea, [contenteditable="true"]')].length;
    const buttons = [...dialog.querySelectorAll('button')]
      .map((b) => b.textContent.trim()).filter(Boolean);
    const body = dialog.textContent || '';
    return { editable, buttons: buttons.slice(0, 10), chars: body.length,
             head: body.slice(0, 300).replace(/\s+/g, ' ') };
  });
  console.log(`     o soan sua duoc: ${info.editable}`);
  console.log(`     nut trong hop thoai: ${JSON.stringify(info.buttons)}`);
  console.log(`     noi dung (${info.chars} ky tu): ${info.head.slice(0, 240)}`);
  if (info.editable === 0) {
    problems.push('manifest chi xem duoc, khong dan/sua duoc -> khong co duong nhap YAML');
  }
  await page.keyboard.press('Escape');
  await page.waitForTimeout(500);
}

// ── adding a stream ────────────────────────────────────────────────────────
console.log('\n  == Thêm stream ==');
const beforeNames = await page.evaluate(() =>
  [...document.querySelectorAll('button')].map((b) => b.textContent.trim())
    .filter((t) => /^[a-z0-9_]+$/i.test(t) && t.length < 20));
await page.getByRole('button', { name: /thêm stream/i }).first().click();
await page.waitForTimeout(1000);
await page.screenshot({ path: `${OUT}/gaps-02-them-stream.png`, fullPage: true });
const dialog = await page.evaluate(() => {
  const d = document.querySelector('[role="dialog"]');
  return d ? { has: true, text: d.textContent.replace(/\s+/g, ' ').slice(0, 200) } : { has: false };
});
console.log(`     co hop thoai hoi ten/duong dan: ${dialog.has}`);
if (!dialog.has) {
  const now = await page.evaluate(() => ({
    name: document.querySelector('#stream-name')?.value,
    path: document.querySelector('#stream-path')?.value,
  }));
  console.log(`     stream moi duoc tao thang: ten="${now.name}" duong dan="${now.path}"`);
  problems.push(
    `them stream khong hoi gi: tao san ten "${now.name}" va duong dan "${now.path}", `
    + 'nguoi dung phai tu tim ra ma sua');
}

// ── how the stream switcher scales ─────────────────────────────────────────
const pills = await page.evaluate(() => {
  const row = document.querySelector('#stream-name')?.closest('section, div[class*="rounded"]')
    ?.parentElement;
  if (!row) return null;
  const buttons = [...row.querySelectorAll('button')]
    .map((b) => b.textContent.trim())
    .filter((t) => /^[a-z0-9_]+$/i.test(t) && t.length < 24);
  return buttons;
});
console.log(`     bo chuyen stream: ${JSON.stringify(pills)} (kieu "pill", khong cay cha-con)`);

console.log(`\n  == Vấn đề (${problems.length}) ==`);
for (const p of [...new Set(problems)]) console.log(`     ${p}`);
await browser.close();
