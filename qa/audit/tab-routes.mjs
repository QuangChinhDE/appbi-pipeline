// Verifies that every tabbed detail view is addressable and survives refresh.
// Usage: DEMO_PASSWORD=... node qa/audit/tab-routes.mjs

import { createRequire } from 'node:module';
import fs from 'node:fs';

const require = createRequire(new URL('../../frontend/package.json', import.meta.url));
const { chromium } = require('playwright');

const BASE = process.env.BASE_URL || 'http://localhost:8080';
const OUT = process.env.SHOT_DIR || '.shots/tab-routes';
const VIEWPORT = {
  width: Number(process.env.VIEWPORT_WIDTH || 1440),
  height: Number(process.env.VIEWPORT_HEIGHT || 900),
};
const password = process.env.DEMO_PASSWORD;
if (!password) throw new Error('DEMO_PASSWORD is required');
fs.mkdirSync(OUT, { recursive: true });

const failures = [];
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: VIEWPORT });

page.on('pageerror', (error) => failures.push(`pageerror: ${error.message}`));
page.on('response', (response) => {
  if (response.status() >= 500) failures.push(`http ${response.status()} ${response.url()}`);
});

await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
await page.fill('input[type="email"]', process.env.DEMO_EMAIL || 'admin@appbi.local');
await page.fill('input[type="password"]', password);
await page.click('button[type="submit"]');
await page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 30_000 });

async function firstDetail(listPath, prefix) {
  await page.goto(`${BASE}${listPath}`, { waitUntil: 'networkidle' });
  const locator = page.locator(`a[href^="${prefix}/"]:not([href="${prefix}/new"])`).first();
  if (await locator.count() === 0) return null;
  return locator.getAttribute('href');
}

async function walk(label, href, tabs) {
  if (!href) {
    console.log(`  skip ${label}: no item exists`);
    return;
  }
  await page.goto(`${BASE}${href}`, { waitUntil: 'networkidle' });
  await page.locator('[role="tablist"]').waitFor({ timeout: 20_000 });

  for (const tab of tabs) {
    const link = page.locator(`[role="tab"][href*="tab=${tab}"]`).first();
    if (await link.count() === 0) {
      failures.push(`${label}: missing link for tab=${tab}`);
      continue;
    }
    await link.click();
    await page.waitForURL((url) => url.searchParams.get('tab') === tab, { timeout: 10_000 });
    console.log(`  ok   ${label} tab=${tab}`);
  }

  const last = tabs.at(-1);
  await page.reload({ waitUntil: 'networkidle' });
  const active = page.locator(`[role="tab"][href*="tab=${last}"][aria-selected="true"]`);
  if (await active.count() === 0) failures.push(`${label}: refresh lost tab=${last}`);
  else console.log(`  ok   ${label} refresh keeps tab=${last}`);
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  if (overflow > 2) failures.push(`${label}: page overflows horizontally by ${overflow}px`);
  await page.screenshot({ path: `${OUT}/${label}.png`, fullPage: true });
}

await walk(
  'pipeline',
  await firstDetail('/pipelines', '/pipelines'),
  ['status', 'jobs', 'schema', 'settings'],
);
await walk('run', await firstDetail('/runs', '/runs'), ['summary', 'attempts', 'logs']);
await walk(
  'source',
  await firstDetail('/sources', '/sources'),
  ['overview', 'configuration', 'pipelines'],
);
await walk(
  'destination',
  await firstDetail('/destinations', '/destinations'),
  ['overview', 'configuration', 'pipelines'],
);
await walk('alerts', '/alerts', ['notifications', 'rules']);

console.log(`failures: ${failures.length}`);
for (const failure of failures) console.log(`  ${failure}`);
await browser.close();
process.exit(failures.length ? 1 : 0);
