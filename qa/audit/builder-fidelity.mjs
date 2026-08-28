// Does the Builder's form and its backend describe the same connector?
//
// Usage (cwd does not matter):
//   SHOT_DIR=.shots DEMO_PASSWORD=... node qa/audit/builder-fidelity.mjs
//
// Three things are compared, and a disagreement between any two is a bug the
// unit tests cannot see:
//
//   1. what the form shows   -- enumerated from the live DOM, after driving each
//                               mode, because most controls are conditional and
//                               a default-state screenshot proves nothing
//   2. what the form sends   -- the PATCH body, captured off the wire
//   3. what the backend kept -- the stored definition and the compiled manifest
//
// A field the form offers and the payload drops is dead UI. A field the payload
// carries and the manifest ignores is a promise the engine never sees.

import { createRequire } from 'node:module';
const require = createRequire(new URL('../../frontend/package.json', import.meta.url));
const { chromium } = require('playwright');
import fs from 'node:fs';

const BASE = process.env.BASE_URL || 'http://localhost:8080';
const OUT = process.env.SHOT_DIR || '.shots';
fs.mkdirSync(OUT, { recursive: true });

const problems = [];
const saves = [];
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } });
page.on('console', (m) => {
  if (m.type() === 'error') problems.push(`console: ${m.text().slice(0, 200)}`);
});
page.on('pageerror', (e) => problems.push(`pageerror: ${String(e).slice(0, 200)}`));
page.on('response', (r) => {
  if (r.status() >= 400 && !r.url().includes('favicon')) {
    problems.push(`http ${r.status()} ${r.request().method()} ${r.url().replace(BASE, '')}`);
  }
});
page.on('request', (r) => {
  if (r.method() === 'PATCH' && r.url().includes('/builder/projects/')) {
    try { saves.push(JSON.parse(r.postData() || '{}')); } catch { /* not json */ }
  }
});

const shot = async (name) => {
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
};

/** Labelled, visible controls inside one named group. */
const groupControls = (heading) => page.evaluate((title) => {
  const header = [...document.querySelectorAll('button')]
    .find((b) => b.textContent.trim().toLowerCase().includes(title.toLowerCase()));
  if (!header) return null;
  const panel = header.closest('div')?.parentElement || header.parentElement;
  const out = [];
  for (const el of panel.querySelectorAll('input, select, textarea')) {
    if (el.offsetParent === null) continue;
    let label = '';
    if (el.id) label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`)?.textContent || '';
    if (!label) label = el.closest('label')?.textContent || '';
    out.push({
      label: (label || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim().slice(0, 60),
      id: el.id, type: el.tagName === 'SELECT' ? 'select' : el.type,
      options: el.tagName === 'SELECT' ? [...el.options].map((o) => o.value) : undefined,
    });
  }
  return out;
}, heading);

const show = (title, found) => {
  if (found === null) { problems.push(`khong tim thay nhom "${title}"`); return; }
  console.log(`\n  -- ${title} (${found.length} o) --`);
  for (const c of found) {
    console.log(`     ${(c.label || '(khong nhan)').padEnd(34)} ${c.type}`
      + `${c.options ? ` [${c.options.join('|')}]` : ''}${c.id ? `  #${c.id}` : ''}`);
  }
};

const setSelect = async (id, value) => {
  const el = page.locator(`#${id}`);
  if (!(await el.count())) { problems.push(`thieu select #${id}`); return false; }
  await el.selectOption(value);
  await page.waitForTimeout(400);
  return true;
};

const setText = async (id, value) => {
  const el = page.locator(`#${id}`);
  if (!(await el.count())) { problems.push(`thieu input #${id}`); return false; }
  await el.fill(value);
  return true;
};

// ── sign in and create a project ───────────────────────────────────────────
await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
await page.fill('input[type="email"]', process.env.DEMO_EMAIL || 'admin@appbi.local');
await page.fill('input[type="password"]', process.env.DEMO_PASSWORD || '');
await page.click('button[type="submit"]');
await page.waitForURL((u) => !u.pathname.includes('/login'), { timeout: 30000 });

await page.goto(`${BASE}/builder`, { waitUntil: 'networkidle' });
await page.getByRole('button', { name: /tạo connector/i }).first().click();
await page.waitForTimeout(600);
const label = `Fidelity ${new Date().toISOString().slice(11, 19).replace(/:/g, '')}`;
await page.locator('input[placeholder*="Shopify"]').first().fill(label);
await page.getByRole('button', { name: /tạo và mở/i }).click();
await page.waitForURL(/\/builder\/[0-9a-f-]{36}/, { timeout: 30000 });
const projectId = page.url().split('/').pop();
console.log(`  project ${projectId}`);

// Open every collapsed group.
for (let i = 0; i < 40; i += 1) {
  const closed = page.locator('button[aria-expanded="false"]');
  if (!(await closed.count())) break;
  await closed.first().click();
  await page.waitForTimeout(120);
}

// ── a second stream, so "choose a parent" has something to choose ──────────
console.log('\n  == 1. Cần hai stream để chọn được cha ==');
const before = await page.locator('button[aria-expanded]').count();
await page.getByRole('button', { name: /thêm stream/i }).first().click();
await page.waitForTimeout(600);
// The button now opens a small form asking for the name and the path, instead
// of silently creating `stream_2` at `/`.
if (await page.locator('#new-stream-name').count()) {
  await page.fill('#new-stream-name', 'lead');
  await page.fill('#new-stream-path', '/lead/list');
  await page.getByRole('button', { name: /^thêm$/i }).first().click();
  await page.waitForTimeout(900);
} else {
  problems.push('nut "Them stream" khong hoi ten/duong dan');
}
const streamPills = await page.evaluate(() => [...document.querySelectorAll('button')]
  .map((b) => b.textContent.trim())
  .filter((t) => t && t.length < 24 && !/thêm|lưu|chạy|phát|xóa|xem/i.test(t)));
console.log(`     pill stream tren man hinh: ${JSON.stringify(streamPills.slice(0, 8))}`);
await shot('fidelity-01-hai-stream');

// ── 2. pagination, revealed ────────────────────────────────────────────────
console.log('\n  == 2. Phân trang: bật "page" rồi xem lộ ra gì ==');
await setSelect('stream-pagination', 'page');
show('Phân trang sau khi chọn "page"', await groupControls('Phân trang'));

// ── 3. incremental, both filter modes ──────────────────────────────────────
console.log('\n  == 3. Incremental ==');
const inc = page.locator('input[type="checkbox"]').first();
await inc.check();
await page.waitForTimeout(400);
show('Incremental, chế độ mặc định', await groupControls('Đồng bộ incremental'));
if (await page.locator('#cursor-filter-mode').count()) {
  await setSelect('cursor-filter-mode', 'client');
  show('Incremental, chế độ "API không lọc"', await groupControls('Đồng bộ incremental'));
  await setSelect('cursor-filter-mode', 'server');
} else {
  problems.push('KHONG co o chon "ai loc theo thoi gian" (#cursor-filter-mode)');
}

// ── 4. partition by parent ─────────────────────────────────────────────────
console.log('\n  == 4. Phân mảnh theo stream cha ==');
await setSelect('partition-mode', 'parent');
show('Phân mảnh theo cha', await groupControls('Phân mảnh'));
const parentOptions = await page.evaluate(() => {
  const el = document.querySelector('#parent-stream');
  return el ? [...el.options].map((o) => o.value) : null;
});
console.log(`     lua chon stream cha: ${JSON.stringify(parentOptions)}`);
if (parentOptions && parentOptions.filter(Boolean).length === 0) {
  problems.push('select stream cha khong co lua chon nao du da co 2 stream');
}
await shot('fidelity-02-cha-con');

// ── 5. fill it in the way it has to be filled to work ─────────────────────
console.log('\n  == 5. Điền cho chạy được rồi lưu ==');
await setText('stream-selector', 'leads');
await setText('stream-pk', 'id');
if (await page.locator('#page-param').count()) await setText('page-param', 'page');
if (await page.locator('#page-inject').count()) await setSelect('page-inject', 'body_data');
if (await page.locator('#cursor-field').count()) await setText('cursor-field', 'last_update');
if (await page.locator('#cursor-param').count()) await setText('cursor-param', 'start_time');
if (await page.locator('#cursor-inject').count()) await setSelect('cursor-inject', 'body_data');
if (parentOptions && parentOptions.filter(Boolean).length) {
  await setSelect('parent-stream', parentOptions.filter(Boolean)[0]);
}
if (await page.locator('#parent-param').count()) await setText('parent-param', 'service_id');
if (await page.locator('#parent-inject').count()) await setSelect('parent-inject', 'body_data');
await shot('fidelity-03-da-dien');

await page.getByRole('button', { name: /^lưu$/i }).first().click();
await page.waitForTimeout(2500);
await shot('fidelity-04-sau-khi-luu');

// ── 6. what went on the wire ───────────────────────────────────────────────
console.log('\n  == 6. Payload FE gửi lên ==');
if (!saves.length) {
  problems.push('khong bat duoc request PATCH nao khi bam Luu');
} else {
  const last = saves[saves.length - 1];
  const streams = last.definition?.streams || [];
  console.log(`     ${saves.length} lan PATCH, lan cuoi co ${streams.length} stream`);
  for (const s of streams) {
    console.log(`     stream "${s.name}":`);
    for (const key of ['pagination', 'partition']) {
      console.log(`        ${key}: ${JSON.stringify(s[key])}`);
    }
    const cursorKeys = Object.keys(s).filter((k) => k.startsWith('cursor') || k === 'incremental');
    console.log(`        cursor: ${JSON.stringify(
      Object.fromEntries(cursorKeys.map((k) => [k, s[k]])))}`);
  }
  fs.writeFileSync(`${OUT}/builder-payload.json`, JSON.stringify(last, null, 2), 'utf8');
}

console.log(`\n  == Vấn đề (${problems.length}) ==`);
for (const p of [...new Set(problems)]) console.log(`     ${p}`);
console.log(`\n  project_id=${projectId}`);

await browser.close();
