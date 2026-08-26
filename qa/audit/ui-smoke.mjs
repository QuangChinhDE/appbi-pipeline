// Drives the real UI through the whole journey and captures screenshots.
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
const OUT = process.env.SHOT_DIR;
fs.mkdirSync(OUT, { recursive: true });

const problems = [];
const seen = new Set();

async function shot(page, name) {
  // Wait for the loading skeletons to clear so screenshots show real data.
  try {
    await page.waitForFunction(
      () => document.querySelectorAll('.skeleton').length === 0,
      { timeout: 15000 },
    );
  } catch { /* some screens legitimately have none */ }
  await page.waitForTimeout(700);
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
  console.log(`  captured ${name}.png`);
}

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
const page = await context.newPage();

page.on('console', (message) => {
  if (message.type() === 'error') {
    const text = message.text();
    if (!seen.has(text)) { seen.add(text); problems.push(`console: ${text}`); }
  }
});
page.on('pageerror', (error) => problems.push(`pageerror: ${error.message}`));
page.on('response', (response) => {
  const url = response.url();
  if (url.includes('/api/') && response.status() >= 400 && response.status() !== 401) {
    problems.push(`http ${response.status()} ${url.replace(BASE, '')}`);
  }
});

console.log('== login ==');
await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
await shot(page, '01-login');
await page.fill('#email', 'admin@appbi.local');
await page.fill('#password', 'Admin@12345');
await page.click('button[type=submit]');
await page.waitForURL('**/overview', { timeout: 20000 });
await page.waitForLoadState('networkidle');
await shot(page, '02-overview');

const routes = [
  ['/sources', '03-sources'],
  ['/destinations', '04-destinations'],
  ['/pipelines', '05-pipelines'],
  ['/runs', '06-runs'],
  ['/monitoring', '07-monitoring'],
  ['/alerts', '08-alerts'],
  ['/connectors', '09-connectors'],
  ['/audit', '10-audit'],
  ['/settings/workspace', '11-settings-workspace'],
  ['/settings/access', '12-settings-access'],
  ['/settings/engine', '13-settings-engine'],
  ['/sources/new', '14-source-wizard'],
  ['/pipelines/new', '15-pipeline-wizard'],
];

for (const [route, name] of routes) {
  console.log(`== ${route} ==`);
  await page.goto(`${BASE}${route}`, { waitUntil: 'networkidle' });
  await shot(page, name);
}

// Drill into the first pipeline and its latest run.
console.log('== pipeline detail ==');
await page.goto(`${BASE}/pipelines`, { waitUntil: 'networkidle' });
const pipelineLink = page.locator('tbody tr a[href^="/pipelines/"]').first();
if (await pipelineLink.count()) {
  await pipelineLink.click();
  await page.waitForLoadState('networkidle');
  await shot(page, '16-pipeline-detail');
  for (const tab of ['Dữ liệu & cấu trúc', 'Lần chạy', 'Cài đặt']) {
    const button = page.getByRole('tab', { name: tab });
    if (await button.count()) {
      await button.click();
      await shot(page, `17-pipeline-${tab.split(' ')[0].toLowerCase()}`);
    }
  }
}

console.log('== run detail ==');
await page.goto(`${BASE}/runs`, { waitUntil: 'networkidle' });
const runLink = page.locator('tbody tr a[href^="/runs/"]').first();
if (await runLink.count()) {
  await runLink.click();
  await page.waitForLoadState('networkidle');
  await shot(page, '18-run-detail');
  const logsTab = page.getByRole('tab', { name: /Nhật ký/ });
  if (await logsTab.count()) {
    await logsTab.click();
    await page.waitForTimeout(2500);
    await shot(page, '19-run-logs');
  }
}

console.log('== source detail ==');
await page.goto(`${BASE}/sources`, { waitUntil: 'networkidle' });
const sourceLink = page.locator('tbody tr a[href^="/sources/"]').first();
if (await sourceLink.count()) {
  await sourceLink.click();
  await page.waitForLoadState('networkidle');
  await shot(page, '20-source-detail');
  const configTab = page.getByRole('tab', { name: 'Cấu hình' });
  if (await configTab.count()) {
    await configTab.click();
    await shot(page, '21-source-config');
  }
}

console.log('== mobile ==');
const mobile = await browser.newContext({ viewport: { width: 390, height: 844 } });
const mobilePage = await mobile.newPage();
await mobilePage.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
await mobilePage.fill('#email', 'admin@appbi.local');
await mobilePage.fill('#password', 'Admin@12345');
await mobilePage.click('button[type=submit]');
await mobilePage.waitForURL('**/overview', { timeout: 20000 });
await mobilePage.waitForLoadState('networkidle');
await mobilePage.screenshot({ path: `${OUT}/22-mobile-overview.png`, fullPage: true });
console.log('  captured 22-mobile-overview.png');

await browser.close();

console.log('\n=== issues ===');
if (problems.length === 0) console.log('none');
else problems.slice(0, 30).forEach((issue) => console.log(' -', issue));
