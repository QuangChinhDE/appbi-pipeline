/**
 * Behavioural audit.
 *
 * The structural audit asks "does this screen render correctly". This one asks
 * "does this control actually do what it says". It drives real interactions and
 * asserts the observable consequence: a filter must change the rows, a tab must
 * change the panel, a pause must survive a reload, a cancel must stop the run.
 *
 *   node scripts/audit-behaviour.mjs
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
const OUT = process.env.SHOT_DIR ?? './behaviour-shots';
fs.mkdirSync(OUT, { recursive: true });

const findings = [];
const passes = [];
function fail(area, detail) { findings.push({ area, detail }); console.log(`  [FAIL] ${area}: ${detail}`); }
function pass(area, detail) { passes.push({ area, detail }); console.log(`  [ok]   ${area}: ${detail}`); }

/** Assert, but never let one broken screen abort the rest of the audit. */
async function step(area, fn) {
  try { await fn(); } catch (e) { fail(area, `threw: ${String(e.message).slice(0, 160)}`); }
}

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();

page.on('pageerror', (e) => fail('runtime', `pageerror: ${e.message.slice(0, 140)}`));
page.on('response', (r) => {
  if (r.url().includes('/api/') && r.status() >= 500) {
    fail('api', `${r.status()} ${r.url().replace(BASE, '')}`);
  }
});

async function settle() {
  try {
    await page.waitForFunction(() => document.querySelectorAll('.skeleton').length === 0,
      { timeout: 12000 });
  } catch { /* screens without skeletons */ }
  await page.waitForTimeout(700);
}

async function go(route) {
  await page.goto(`${BASE}${route}`, { waitUntil: 'load' });
  await settle();
}

/** Row count of the first data table on screen. */
const rowCount = () => page.locator('main tbody tr').count();

// ── login ──────────────────────────────────────────────────────────────────
await page.goto(`${BASE}/login`, { waitUntil: 'load' });
await page.waitForSelector('#email');
await page.waitForTimeout(1200);
await page.fill('#email', 'admin@appbi.local');
await page.fill('#password', 'Admin@12345');
await page.click('button[type=submit]');
await page.waitForURL('**/overview', { timeout: 45000 });
console.log('logged in\n');

// ── 1. list filters must actually filter ───────────────────────────────────
console.log('== 1. filters change the result set ==');

await step('Pipelines filter', async () => {
  await go('/pipelines');
  const all = await rowCount();
  if (all === 0) return fail('Pipelines filter', 'no pipelines exist — cannot test filtering');

  // A filter nothing matches must empty the table, not silently ignore itself.
  const neverRun = page.locator('main button', { hasText: /Chưa chạy|Never run/ }).first();
  if (await neverRun.count()) {
    await neverRun.click();
    await settle();
    const filtered = await rowCount();
    if (filtered > all) fail('Pipelines filter', `"never run" returned MORE rows (${filtered} > ${all})`);
    else pass('Pipelines filter', `${all} → ${filtered} rows`);

    // aria-pressed must reflect the active filter, or a screen reader cannot tell.
    if (await neverRun.getAttribute('aria-pressed') !== 'true') {
      fail('Pipelines filter', 'active quick filter does not set aria-pressed=true');
    } else pass('Pipelines filter', 'active filter exposes aria-pressed');
  }
});

await step('Pipelines search', async () => {
  await go('/pipelines');
  const all = await rowCount();
  const box = page.locator('main input[type=search], main input[placeholder]').first();
  await box.fill('zzz-no-such-pipeline-zzz');
  await page.waitForTimeout(1400);            // debounce is 300ms + request
  await settle();
  const after = await rowCount();
  if (after !== 0) fail('Pipelines search', `nonsense query still returned ${after} rows`);
  else pass('Pipelines search', 'nonsense query empties the table');

  // And the empty state must say "no results", not "you have no pipelines".
  const text = await page.locator('main').innerText();
  if (all > 0 && /chưa có pipeline nào|no pipelines yet/i.test(text)) {
    fail('Pipelines search', 'a filtered-empty table shows the first-run empty state');
  } else pass('Pipelines search', 'filtered-empty state is distinct from first-run');

  await box.fill('');
  await page.waitForTimeout(1400);
  await settle();
  if (await rowCount() !== all) fail('Pipelines search', 'clearing the search does not restore the rows');
  else pass('Pipelines search', 'clearing restores the full list');
});

await step('Runs filter', async () => {
  await go('/runs');
  const all = await rowCount();
  const select = page.locator('main select').first();
  if (await select.count()) {
    const values = await select.locator('option').evaluateAll((o) => o.map((x) => x.value).filter(Boolean));
    if (values.length) {
      await select.selectOption(values[0]);
      await page.waitForTimeout(900);
      await settle();
      const after = await rowCount();
      if (after > all) fail('Runs filter', `filter widened the result set (${after} > ${all})`);
      else pass('Runs filter', `status filter ${all} → ${after} rows`);
    }
  }
});

// ── 2. deep links and back navigation ──────────────────────────────────────
console.log('\n== 2. navigation is reversible ==');

await step('Pipeline detail', async () => {
  await go('/pipelines');
  if (await rowCount() === 0) return;
  const name = (await page.locator('main tbody tr').first().locator('a').first().innerText()).trim();
  await page.locator('main tbody tr').first().locator('a').first().click();
  await page.waitForURL('**/pipelines/**', { timeout: 15000 });
  await settle();

  const heading = (await page.locator('main h1').first().innerText()).trim();
  if (!heading.includes(name.split('\n')[0])) {
    fail('Pipeline detail', `opened row "${name.split('\n')[0]}" but the heading reads "${heading}"`);
  } else pass('Pipeline detail', 'row opens the pipeline it names');

  // A detail URL must survive a reload — that is what people paste to each other.
  const url = page.url();
  await page.reload({ waitUntil: 'load' });
  await settle();
  if (page.url() !== url) fail('Pipeline detail', 'reload redirected away from the deep link');
  else if (!(await page.locator('main h1').first().innerText()).trim()) {
    fail('Pipeline detail', 'reload produced an empty heading');
  } else pass('Pipeline detail', 'deep link survives a reload');

  await page.goBack();
  await settle();
  if (!page.url().endsWith('/pipelines')) fail('Pipeline detail', 'back did not return to the list');
  else pass('Pipeline detail', 'back returns to the list');
});

await step('Detail tabs', async () => {
  await go('/pipelines');
  if (await rowCount() === 0) return;
  await page.locator('main tbody tr').first().locator('a').first().click();
  await page.waitForURL('**/pipelines/**', { timeout: 15000 });
  await settle();

  const tabs = page.locator('main [role=tab], main nav button');
  const count = Math.min(await tabs.count(), 6);
  const seen = new Set();
  for (let i = 0; i < count; i += 1) {
    const tab = tabs.nth(i);
    const label = (await tab.innerText()).trim();
    if (!label) continue;
    await tab.click();
    await page.waitForTimeout(700);
    const body = (await page.locator('main').innerText()).slice(0, 4000);
    if (seen.has(body)) fail('Detail tabs', `"${label}" renders the same content as another tab`);
    seen.add(body);
  }
  if (seen.size > 1) pass('Detail tabs', `${seen.size} tabs render distinct panels`);
});

// ── 3. state changes persist ───────────────────────────────────────────────
console.log('\n== 3. actions persist across a reload ==');

await step('Pause/resume', async () => {
  await go('/pipelines');
  if (await rowCount() === 0) return;
  const TOGGLE = /^(Tạm dừng|Tiếp tục|Pause|Resume)/;
  const toggleIn = (root) => root.getByRole('button', { name: TOGGLE }).first();
  const nameOf = (locator) => locator.getAttribute('aria-label');

  const toggle = toggleIn(page.locator('main tbody tr').first());
  if (!(await toggle.count())) {
    return fail('Pause/resume', 'no pause/resume control with an accessible name on the first row');
  }

  const before = await nameOf(toggle);
  await toggle.click();
  await page.waitForTimeout(2500);
  await settle();

  await page.reload({ waitUntil: 'load' });
  await settle();
  const after = await nameOf(toggleIn(page.locator('main tbody tr').first()));
  if (after === before) fail('Pause/resume', `state did not change after the toggle (still "${before}")`);
  else pass('Pause/resume', `"${before}" → "${after}" and it survives a reload`);

  // Put it back so a later run starts from the same place.
  await toggleIn(page.locator('main tbody tr').first()).click();
  await page.waitForTimeout(2000);
});

await step('Language toggle', async () => {
  await go('/pipelines');
  const before = (await page.locator('main h1').first().innerText()).trim();
  // The switch lives inside the collapsed account menu in the sidebar.
  const menu = page.locator('[aria-haspopup="menu"]').first();
  if (!(await menu.count())) return fail('Language toggle', 'no account menu trigger found');
  await menu.click();
  await page.waitForTimeout(500);
  const toggle = page.getByRole('button', { name: 'English', exact: true }).first();
  if (!(await toggle.count())) return fail('Language toggle', 'no language control found');
  if (await toggle.getAttribute('aria-pressed') === null) {
    fail('Language toggle', 'locale buttons do not expose aria-pressed');
  } else pass('Language toggle', 'locale buttons expose aria-pressed');
  await toggle.click();
  await page.waitForTimeout(900);
  const after = (await page.locator('main h1').first().innerText()).trim();
  if (after === before) fail('Language toggle', `heading unchanged ("${before}") after switching locale`);
  else pass('Language toggle', `"${before}" → "${after}"`);

  // The choice must outlive a reload, or every navigation resets the language.
  await page.reload({ waitUntil: 'load' });
  await settle();
  const persisted = (await page.locator('main h1').first().innerText()).trim();
  if (persisted !== after) fail('Language toggle', 'locale resets on reload');
  else pass('Language toggle', 'locale persists across a reload');

  await page.locator('[aria-haspopup="menu"]').first().click();
  await page.waitForTimeout(500);
  await page.getByRole('button', { name: 'Tiếng Việt', exact: true }).first().click();
  await page.waitForTimeout(600);
});

// ── 4. forms reject bad input before the server has to ─────────────────────
console.log('\n== 4. forms validate before submitting ==');

await step('Source wizard validation', async () => {
  await go('/sources/new');
  const card = page.locator('main button, main [role=button]', { hasText: /PostgreSQL/ }).first();
  if (!(await card.count())) return fail('Source wizard', 'no PostgreSQL connector card on the picker');
  await card.click();
  await page.waitForTimeout(1200);

  const next = page.locator('main button', { hasText: /Tiếp tục|Continue|Kiểm tra|Test/ }).last();
  if (!(await next.count())) return fail('Source wizard', 'no advance button after picking a connector');

  await next.click();
  await page.waitForTimeout(1500);

  // An empty required form must not reach the network at all.
  const text = await page.locator('main').innerText();
  if (!/bắt buộc|required|không được để trống/i.test(text)) {
    fail('Source wizard', 'submitting an empty required form shows no field-level error');
  } else pass('Source wizard', 'empty required fields are caught client-side');
});

// ── 5. an unknown id is a 404 screen, not a crash or a blank page ──────────
console.log('\n== 5. unknown ids degrade gracefully ==');

for (const [route, label] of [
  ['/pipelines/00000000-0000-0000-0000-000000000000', 'Pipeline'],
  ['/runs/00000000-0000-0000-0000-000000000000', 'Run'],
  ['/sources/00000000-0000-0000-0000-000000000000', 'Source'],
]) {
  await step(`${label} 404`, async () => {
    await go(route);
    const text = (await page.locator('main').innerText()).trim();
    if (!text) fail(`${label} 404`, 'blank page for an unknown id');
    else if (!/không tìm thấy|not found|không tồn tại|does not exist|lỗi|error/i.test(text)) {
      fail(`${label} 404`, `no not-found message; screen reads "${text.slice(0, 90)}"`);
    } else pass(`${label} 404`, 'shows a not-found state');
  });
}

// ── 6. a read-only role cannot mutate, even by URL ─────────────────────────
console.log('\n== 6. read-only role is enforced in the UI ==');

await step('Analyst', async () => {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const p2 = await ctx.newPage();
  await p2.goto(`${BASE}/login`, { waitUntil: 'load' });
  await p2.waitForSelector('#email');
  await p2.waitForTimeout(1200);
  await p2.fill('#email', 'analyst@appbi.local');
  await p2.fill('#password', 'Admin@12345');
  await p2.click('button[type=submit]');
  await p2.waitForURL('**/overview', { timeout: 45000 });

  for (const route of ['/sources/new', '/destinations/new', '/pipelines/new']) {
    await p2.goto(`${BASE}${route}`, { waitUntil: 'load' });
    await p2.waitForTimeout(1500);
    const inputs = await p2.locator('main input, main select').count();
    const text = await p2.locator('main').innerText();
    if (inputs > 0) fail('Analyst', `${route} still renders ${inputs} form fields`);
    else if (!/không có quyền|permission|cannot create/i.test(text)) {
      fail('Analyst', `${route} renders no form but explains nothing`);
    } else pass('Analyst', `${route} is guarded with an explanation`);
  }

  await p2.goto(`${BASE}/pipelines`, { waitUntil: 'load' });
  await p2.waitForTimeout(1800);
  // Scope to the table body: quick-filter chips above it share labels with
  // actions ("Tạm dừng" is both a filter and a verb), and a chip is not a mutation.
  const mutating = await p2.evaluate(() => [...document.querySelectorAll('main tbody button, main tbody a[href]')]
    .filter((b) => !b.disabled && b.getAttribute('aria-disabled') !== 'true')
    // Icon-only controls carry their name in aria-label, so text alone would
    // let a real RBAC leak slip through unnoticed.
    .map((b) => (b.getAttribute('aria-label') || b.textContent || '').trim())
    .filter((label) => /Đồng bộ ngay|Tạm dừng|Tiếp tục|Xóa|Run now|Pause|Resume|Delete/.test(label)));
  if (mutating.length) fail('Analyst', `enabled mutating controls on /pipelines: ${mutating.join(', ')}`);
  else pass('Analyst', 'no enabled mutating controls on the pipeline list');

  await ctx.close();
});

// ── report ─────────────────────────────────────────────────────────────────
await page.screenshot({ path: `${OUT}/final.png`, fullPage: true });
fs.writeFileSync(`${OUT}/findings.json`, JSON.stringify({ findings, passes }, null, 2));

console.log(`\n=== ${passes.length} checks passed, ${findings.length} findings ===`);
for (const f of findings) console.log(`  ${f.area}: ${f.detail}`);

await browser.close();
process.exit(findings.length ? 1 : 0);
