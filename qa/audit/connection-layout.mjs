// Walk the connection detail page the way a person does, and photograph it.
//
// Usage (cwd does not matter):
//   SHOT_DIR=.shots DEMO_PASSWORD=... node qa/audit/connection-layout.mjs
//
// Reading the components tells you the markup exists. Only driving the page
// tells you the tabs switch, the stream modal opens over real fields, the
// replication-state panel actually reaches the engine, and nothing throws on
// the way. Every console error and every 4xx/5xx is collected and printed.

// playwright lives in `frontend/node_modules`, and ESM resolves bare specifiers
// from the *importing file's* directory -- not the working directory.
import { createRequire } from 'node:module';
const require = createRequire(new URL('../../frontend/package.json', import.meta.url));
const { chromium } = require('playwright');
import fs from 'node:fs';

const BASE = process.env.BASE_URL || 'http://localhost:8080';
const OUT = process.env.SHOT_DIR || '.shots';
fs.mkdirSync(OUT, { recursive: true });

const errors = [];
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text().slice(0, 160)); });
page.on('pageerror', (e) => errors.push(`pageerror: ${String(e).slice(0, 160)}`));
page.on('response', (r) => {
  if (r.status() >= 400) errors.push(`http ${r.status()} ${r.url().replace(BASE, '')}`);
});

const shot = async (name) => {
  await page.waitForTimeout(700);
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
  console.log(`  shot: ${name}`);
};

// ── sign in ──────────────────────────────────────────────────────────────────
await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
await page.fill('input[type="email"]', process.env.DEMO_EMAIL || 'admin@appbi.local');
await page.fill('input[type="password"]', process.env.DEMO_PASSWORD);
await page.click('button[type="submit"]');
await page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 30_000 });

// ── open the first pipeline ──────────────────────────────────────────────────
await page.goto(`${BASE}/pipelines`, { waitUntil: 'networkidle' });
// `/pipelines/new` also matches a naive href prefix, and clicking it lands on
// the create wizard -- which then reports every assertion below as missing.
const first = page.locator('a[href^="/pipelines/"]:not([href="/pipelines/new"])').first();
if (await first.count() === 0) {
  console.log('  no pipeline to open — run the Base.vn e2e first');
  await browser.close();
  process.exit(1);
}
await first.click();
await page.waitForURL(/\/pipelines\/[0-9a-f-]{8,}/, { timeout: 20_000 });
await page.waitForLoadState('networkidle');
console.log(`  on: ${new URL(page.url()).pathname}`);

// networkidle fires while React is still hydrating, so every assertion below
// would report "missing" against a page that renders correctly a moment later.
// Wait for the tab strip -- it only exists once the pipeline query resolved.
await page.locator('[role="tablist"]').waitFor({ timeout: 20_000 });
await page.locator('table').first().waitFor({ timeout: 20_000 });

/** Tabs are real ARIA tabs, so address them as such rather than by loose text. */
const openTab = async (id) => {
  await page.locator(`[role="tab"][href*="tab=${id}"]`).first().click();
  await page.waitForURL((url) => url.searchParams.get('tab') === id, { timeout: 10_000 });
  await page.waitForTimeout(900);
};

const check = async (label, locator) => {
  const found = await locator.count() > 0;
  console.log(`  ${found ? 'ok  ' : 'MISS'} ${label}`);
  if (!found) errors.push(`missing: ${label}`);
  return found;
};

// ── Status ───────────────────────────────────────────────────────────────────
await check('header enabled switch', page.locator('button[role="switch"][aria-checked]').first());
await check('sync now', page.getByRole('button', { name: /sync now|đồng bộ ngay/i }).first());
await check('active streams table', page.locator('table').first());
await shot('connection-1-status');

// row menu
const kebab = page.locator('table tbody tr button[aria-haspopup="menu"]').first();
if (await kebab.count() > 0) {
  await kebab.click();
  await check('row menu open', page.locator('[role="menu"]'));
  await shot('connection-2-row-menu');
  await page.keyboard.press('Escape');
}

// ── Job History ──────────────────────────────────────────────────────────────
await openTab('jobs');
await page.reload({ waitUntil: 'networkidle' });
await check(
  'job tab survives refresh',
  page.locator('[role="tab"][href*="tab=jobs"][aria-selected="true"]'),
);
await shot('connection-3-jobs');

// ── Schema ───────────────────────────────────────────────────────────────────
await openTab('schema');
await check('sync mode select', page.locator('table select').first());
await shot('connection-4-schema');

// stream modal
//
// Read-only from here on. An earlier version of this walkthrough clicked the
// first button in a schema row -- which is the enable toggle -- and quietly
// turned a stream off on a real pipeline. An audit that changes the thing it
// is auditing reports on a state that only it created.
// The first button in a schema row is the enable toggle; the stream name is
// the one that opens the modal. Address it by its text, not its position.
const streamName = await page.locator('table tbody tr td:nth-child(3) button').first().innerText();
const streamLink = page.getByRole('button', { name: streamName, exact: true }).first();
if (await streamLink.count() > 0) {
  await streamLink.click();
  await page.waitForTimeout(700);
  await check('stream modal', page.locator('[role="dialog"]'));
  if (!new URL(page.url()).searchParams.get('stream')) errors.push('stream modal missing URL state');
  await shot('connection-5-stream-modal');
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => !new URL(window.location.href).searchParams.has('stream'));
}

// ── Settings ─────────────────────────────────────────────────────────────────
await openTab('settings');
await check('advanced disclosure', page.getByRole('button', { name: /advanced|nâng cao/i }).first());
const stateToggle = page.getByRole('button', { name: /connection state|trạng thái replication/i }).first();
if (await stateToggle.count() > 0) {
  await stateToggle.click();
  await page.waitForTimeout(1500);          // it fetches from the engine on open
  await check('replication state body', page.locator('pre, p.text-caption').first());
}
await shot('connection-6-settings');

// ── edit the cursor through the UI, then put it back ────────────────────────
//
// The panel exists to be edited, so an audit that only reads it proves the
// wrong thing. This types into the editor, saves through the confirm dialog,
// reads the value back, and restores the original -- if the restore fails the
// script says so loudly rather than leaving a real pipeline mis-marked.
const editor = page.locator('textarea[aria-label]').first();
if (process.env.MUTATE_STATE === '1' && await editor.count() > 0) {
  const original = await editor.inputValue();
  let parsed = null;
  try { parsed = JSON.parse(original); } catch { /* reported below */ }

  if (Array.isArray(parsed) && parsed.length > 0) {
    // Edit a real cursor value, not a synthetic key: Airbyte drops keys it
    // does not recognise inside a stream entry, so an alien marker never comes
    // back and the round-trip check fails against a save that worked.
    const probe = JSON.parse(original);
    const entry = probe.find((e) => e && typeof e.streamState === 'object'
      && e.streamState !== null && 'last_update' in e.streamState);
    if (!entry) {
      console.log('  --   no cursor value to edit, skipping the write path');
      await browser.close();
      process.exit(errors.length ? 1 : 0);
    }
    const originalCursor = entry.streamState.last_update;
    entry.streamState.last_update = '1234567890';
    await editor.fill(JSON.stringify(probe, null, 2));

    const saveButton = page.getByRole('button', { name: /save cursor|lưu con trỏ/i }).first();
    await check('save enabled after a valid edit', saveButton);
    if (await saveButton.isEnabled()) {
      await saveButton.click();
      await page.getByRole('button', { name: /save cursor|lưu con trỏ/i }).last().click();
      await page.waitForTimeout(2500);
      const saved = await editor.inputValue();
      const roundTripped = saved.includes('1234567890');
      console.log(`  ${roundTripped ? 'ok  ' : 'MISS'} cursor edit round-trips`);
      if (!roundTripped) errors.push('cursor edit did not round-trip');
      await shot('connection-7-state-edited');

      // restore
      await editor.fill(original);
      await page.getByRole('button', { name: /save cursor|lưu con trỏ/i }).first().click();
      await page.getByRole('button', { name: /save cursor|lưu con trỏ/i }).last().click();
      await page.waitForTimeout(2500);
      const restored = await editor.inputValue();
      const clean = !restored.includes('1234567890');
      console.log(`  ${clean ? 'ok  ' : 'MISS'} cursor restored`);
      if (!clean) errors.push(`LEFT A TEST CURSOR IN A REAL PIPELINE — set it back to ${originalCursor} by hand`);
    }
  } else {
    console.log('  --   cursor is empty, edit path not exercised');
  }
} else if (await editor.count() > 0) {
  console.log('  ok   cursor editor rendered (write path skipped; set MUTATE_STATE=1 to exercise it)');
}

console.log(`\nerrors: ${errors.length}`);
for (const line of errors.slice(0, 25)) console.log(`  ${line}`);
await browser.close();
process.exit(errors.length ? 1 : 0);
