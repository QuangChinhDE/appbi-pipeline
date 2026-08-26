// Walk the source-creation wizard the way a user does, and report what is there.
//
//   cd frontend && SHOT_DIR=../.shots DEMO_PASSWORD=... node ../qa/audit/connector-form.mjs
//
// Counts console errors and failed requests, reads back the labels and help
// text actually rendered, and checks the mobile layout for sideways overflow.
// Ten missing connector icons showed up here as 404s and nowhere else.
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
const OUT = process.env.SHOT_DIR || 'shots';
fs.mkdirSync(OUT, { recursive: true });

const errors = [];

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
page.on('console', (m) => { if (m.type() === 'error') errors.push(`console: ${m.text().slice(0, 160)}`); });
page.on('pageerror', (e) => errors.push(`pageerror: ${String(e).slice(0, 160)}`));
page.on('response', (r) => {
  if (r.status() >= 400) errors.push(`http ${r.status()} ${r.url().replace(BASE, '')}`);
});

async function shot(name) {
  await page.waitForTimeout(900);
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
  console.log(`  shot ${name}`);
}

// ── sign in ──────────────────────────────────────────────────────────────────
await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
await page.fill('#email', 'admin@appbi.local');
await page.fill('#password', process.env.DEMO_PASSWORD);
await page.click('button[type=submit]');
await page.waitForURL('**/overview', { timeout: 30000 });
console.log('signed in');

// ── the source wizard ────────────────────────────────────────────────────────
await page.goto(`${BASE}/sources/new`, { waitUntil: 'networkidle' });
await shot('01-pick-connector');

const cards = await page.locator('button,[role=button]').filter({ hasText: /Base|Postgres|BigQuery/ }).count();
console.log(`  connector cards visible: ${cards}`);

// Choose Base HRM.
const hrm = page.getByText('Base HRM', { exact: false }).first();
if (await hrm.count()) {
  await hrm.click();
  await page.waitForTimeout(800);
  console.log('  picked Base HRM');
} else {
  console.log('  !! could not find a Base HRM card');
}
await shot('02-picked');

// Continue to configuration.
const next = page.getByRole('button', { name: /Tiếp tục|Continue/i }).first();
if (await next.count()) { await next.click(); await page.waitForTimeout(1500); }
await shot('03-configure');

// What does the configuration step actually show?
const labels = await page.locator('label').allInnerTexts();
const inputs = await page.locator('input:visible').count();
const helps = await page.locator('p,span').filter({ hasText: /Epoch|token|domain|installation/i }).allInnerTexts();
console.log(`\n  configure step: ${inputs} visible input(s)`);
console.log('  labels:', JSON.stringify(labels.map((l) => l.trim()).filter(Boolean)));
console.log('  help text:', JSON.stringify(helps.slice(0, 4).map((h) => h.trim().slice(0, 110))));

// Is there anything resembling documentation on this screen?
const body = (await page.locator('body').innerText()).toLowerCase();
for (const marker of ['documentation', 'tài liệu', 'setup guide', 'hướng dẫn', 'docs']) {
  if (body.includes(marker)) console.log(`  found doc marker: ${marker}`);
}

// Mobile: docs first, and nothing may overflow sideways.
await page.setViewportSize({ width: 390, height: 844 });
await page.waitForTimeout(1000);
await shot('04-mobile');
const overflow = await page.evaluate(
  () => document.documentElement.scrollWidth - document.documentElement.clientWidth);
console.log(`  mobile horizontal overflow: ${overflow}px`);

console.log(`\nerrors seen: ${errors.length}`);
for (const e of [...new Set(errors)].slice(0, 12)) console.log(`  ${e}`);

await browser.close();
