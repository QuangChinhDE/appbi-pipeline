/**
 * Journey A from section 9.1, driven through the real UI.
 *
 * Creates a source and a destination through the wizards, builds a pipeline,
 * runs the first sync and watches it reach a terminal state — clicking the same
 * buttons a user would, with no direct API calls.
 */
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

const BASE = process.env.BASE_URL ?? 'http://localhost:8080';
const OUT = process.env.SHOT_DIR ?? './journey-shots';
const SUFFIX = String(Date.now()).slice(-5);
fs.mkdirSync(OUT, { recursive: true });

const problems = [];
let step = 0;

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
const page = await context.newPage();

page.on('pageerror', (error) => problems.push(`pageerror: ${error.message}`));
page.on('response', (response) => {
  const url = response.url();
  if (url.includes('/api/') && response.status() >= 500) {
    problems.push(`http ${response.status()} ${url.replace(BASE, '')}`);
  }
});

async function shot(name) {
  step += 1;
  const file = `${OUT}/j${String(step).padStart(2, '0')}-${name}.png`;
  await page.screenshot({ path: file, fullPage: true });
  console.log(`   shot ${file.split('/').pop()}`);
}

function log(message) {
  console.log(message);
}

// ── login ──────────────────────────────────────────────────────────────────
log('0. waiting for the app to answer');
for (let attempt = 0; attempt < 60; attempt += 1) {
  try {
    const response = await page.request.get(`${BASE}/login`, { timeout: 5000 });
    if (response.ok()) break;
  } catch { /* the container may still be booting */ }
  await page.waitForTimeout(2000);
}

log('1. login');
await page.goto(`${BASE}/login`, { waitUntil: 'load' });
await page.waitForSelector('#email', { timeout: 60000 });
// The form is a client component; clicking before hydration does nothing.
await page.waitForTimeout(2000);
await page.fill('#email', 'admin@appbi.local');
await page.fill('#password', 'Admin@12345');
await page.click('button[type=submit]');
try {
  await page.waitForURL('**/overview', { timeout: 60000 });
} catch (error) {
  await page.screenshot({ path: `${OUT}/login-failure.png`, fullPage: true });
  console.error('login did not navigate:', (await page.textContent('body'))?.slice(0, 500));
  throw error;
}

// ── source wizard ──────────────────────────────────────────────────────────
log('2. create source through the wizard');
await page.goto(`${BASE}/sources/new`, { waitUntil: 'domcontentloaded' });
await page.getByRole('button', { name: /Sample Data/ }).click();
await page.getByRole('button', { name: 'Tiếp tục' }).click();
await page.waitForSelector('#actor-name');
await page.fill('#actor-name', `UI Faker ${SUFFIX}`);
await page.fill('#cfg-count', '150');
await shot('source-configure');
await page.getByRole('button', { name: 'Tiếp tục' }).click();

log('   waiting for the connector check to run...');
await page.waitForSelector('text=Kết nối thành công', { timeout: 180000 });
await shot('source-tested');
await page.getByRole('button', { name: 'Lưu' }).click();
await page.waitForURL(/\/sources\/[0-9a-f-]{36}$/, { timeout: 240000 });
await page.waitForLoadState('domcontentloaded');
await page.waitForTimeout(1200);
await shot('source-created');
log('   source created');

// ── destination wizard ─────────────────────────────────────────────────────
log('3. create destination through the wizard');
await page.goto(`${BASE}/destinations/new`, { waitUntil: 'domcontentloaded' });
await page.getByRole('button', { name: /PostgreSQL/ }).first().click();
await page.getByRole('button', { name: 'Tiếp tục' }).click();
await page.waitForSelector('#actor-name');
await page.fill('#actor-name', `UI Warehouse ${SUFFIX}`);
await page.fill('#cfg-host', 'postgres');
await page.fill('#cfg-port', '5432');
await page.fill('#cfg-database', 'demo_warehouse');
await page.fill('#cfg-schema', `ui_${SUFFIX}`);
await page.fill('#cfg-username', 'demo_writer');
await page.fill('#cfg-password', 'demo_writer_pw');
await shot('destination-configure');
await page.getByRole('button', { name: 'Tiếp tục' }).click();

log('   waiting for the connector check to run...');
await page.waitForSelector('text=Kết nối thành công', { timeout: 180000 });
await page.getByRole('button', { name: 'Lưu' }).click();
await page.waitForURL(/\/destinations\/[0-9a-f-]{36}$/, { timeout: 240000 });
await page.waitForLoadState('domcontentloaded');
await page.waitForTimeout(1200);
await shot('destination-created');
log('   destination created');

// ── pipeline wizard ────────────────────────────────────────────────────────
log('4. build the pipeline through the wizard');
await page.goto(`${BASE}/pipelines/new`, { waitUntil: 'domcontentloaded' });
await page.fill('#pl-name', `UI Pipeline ${SUFFIX}`);
await page.selectOption('#pl-source', { label: `UI Faker ${SUFFIX} (Sample Data (Faker))` });
await page.selectOption('#pl-destination', { label: `UI Warehouse ${SUFFIX} (PostgreSQL)` });
await shot('pipeline-basics');
await page.getByRole('button', { name: 'Tiếp tục' }).click();

log('   waiting for schema discovery...');
await page.waitForSelector('text=/Chọn dữ liệu \\(\\d+ stream\\)/', { timeout: 180000 });
await page.waitForTimeout(700);

// Select the first two streams the way a user would.
const checkboxes = page.locator('tbody input[type=checkbox]');
await checkboxes.nth(0).check();
await checkboxes.nth(1).check();
await shot('pipeline-streams');
await page.getByRole('button', { name: 'Tiếp tục' }).click();

log('   schedule step');
await page.getByRole('button', { name: /^Theo chu kỳ/ }).click();
await page.waitForSelector('#sched-interval');
await page.selectOption('#sched-interval', '3600');
await page.waitForSelector('text=3 lần chạy tiếp theo');
await shot('pipeline-schedule');
await page.getByRole('button', { name: 'Tiếp tục' }).click();

await page.waitForSelector('text=Xác nhận');
await shot('pipeline-review');
await page.getByRole('button', { name: 'Tạo pipeline' }).click();
await page.waitForURL(/\/pipelines\/[0-9a-f-]{36}$/, { timeout: 240000 });
await page.waitForLoadState('domcontentloaded');
await page.waitForTimeout(1200);
const pipelineUrl = page.url();
log(`   pipeline created: ${pipelineUrl}`);
await shot('pipeline-created');

// ── watch the first sync ───────────────────────────────────────────────────
log('5. watching the first sync reach a terminal state');
let finalStatus = 'unknown';
let recordsShown = null;
for (let attempt = 0; attempt < 60; attempt += 1) {
  await page.reload({ waitUntil: 'load' });
  // Wait for the client query to resolve rather than guessing at a delay.
  try {
    await page.waitForSelector('text=/Thành công|Thất bại|Đang chạy|Chưa chạy lần nào/',
      { timeout: 15000 });
  } catch { /* still loading; try again */ }
  const body = (await page.textContent('body')) ?? '';
  if (/Hoạt động tốt/.test(body)) { finalStatus = 'HEALTHY'; break; }
  if (/Thất bại|Cần xử lý/.test(body)) { finalStatus = 'FAILED'; break; }
  await page.waitForTimeout(5000);
}
const recordCell = page.locator('text=BẢN GHI 30 NGÀY').locator('xpath=..');
if (await recordCell.count()) {
  recordsShown = (await recordCell.first().textContent())?.replace(/\D+/g, '') || null;
}
await shot('pipeline-after-sync');
log(`   pipeline health: ${finalStatus}, records on page: ${recordsShown ?? 'n/a'}`);

// ── run detail from the UI ─────────────────────────────────────────────────
log('6. open the run from the pipeline page');
const runLink = page.locator('a[href^="/runs/"]').first();
if (await runLink.count()) {
  await runLink.click();
  await page.waitForLoadState('domcontentloaded');
await page.waitForTimeout(1200);
  await shot('run-detail');
  const runBody = (await page.textContent('body')) ?? '';
  log(`   run page reports success: ${/Thành công/.test(runBody)}`);
}

await browser.close();

console.log('\n=== journey result ===');
console.log(`pipeline health after first sync: ${finalStatus}`);
console.log(`records visible in the UI: ${recordsShown ?? 'n/a'}`);
console.log(problems.length ? `issues:\n - ${problems.join('\n - ')}` : 'no server errors or page errors');
if (finalStatus !== 'HEALTHY') process.exitCode = 1;
