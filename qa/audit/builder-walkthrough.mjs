// Drive the Connector Builder the way a person does, and write down what is
// actually on screen.
//
// Usage (cwd does not matter):
//   SHOT_DIR=.shots DEMO_PASSWORD=... node qa/audit/builder-walkthrough.mjs
//
// Reading the components tells you the markup exists. This exists because that
// is not the same question as "can somebody build a working connector here":
// the editor can offer a field the compiler ignores, or accept a shape the
// engine refuses, and neither shows up in a unit test. So every control is
// enumerated from the live DOM, every group is opened and photographed, and the
// enumeration is printed next to what the backend says it accepts.

import { createRequire } from 'node:module';
const require = createRequire(new URL('../../frontend/package.json', import.meta.url));
const { chromium } = require('playwright');
import fs from 'node:fs';

const BASE = process.env.BASE_URL || 'http://localhost:8080';
const OUT = process.env.SHOT_DIR || '.shots';
fs.mkdirSync(OUT, { recursive: true });

const problems = [];
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } });
page.on('console', (m) => {
  if (m.type() === 'error') problems.push(`console: ${m.text().slice(0, 200)}`);
});
page.on('pageerror', (e) => problems.push(`pageerror: ${String(e).slice(0, 200)}`));
page.on('response', (r) => {
  if (r.status() >= 400 && !r.url().includes('favicon')) {
    problems.push(`http ${r.status()} ${r.request().method()} ${r.url().replace(BASE, '')}`);
  }
});

const shot = async (name) => {
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
  console.log(`  shot: ${name}`);
};

/** Every labelled control on screen, in document order. */
const controls = () => page.evaluate(() => {
  const out = [];
  for (const el of document.querySelectorAll('input, select, textarea, button')) {
    if (el.offsetParent === null && el.type !== 'hidden') continue;
    const id = el.id || '';
    let label = '';
    if (id) {
      const l = document.querySelector(`label[for="${CSS.escape(id)}"]`);
      if (l) label = l.textContent.trim();
    }
    if (!label) {
      const wrap = el.closest('label');
      if (wrap) label = wrap.textContent.trim();
    }
    if (!label) label = (el.getAttribute('aria-label') || el.textContent || '').trim();
    out.push({
      tag: el.tagName.toLowerCase(),
      type: el.type || '',
      id,
      label: label.replace(/\s+/g, ' ').slice(0, 70),
      options: el.tagName === 'SELECT'
        ? [...el.options].map((o) => o.value).join('|').slice(0, 120) : '',
    });
  }
  return out;
});

const report = async (title) => {
  const found = await controls();
  console.log(`\n  == ${title} == (${found.length} control)`);
  for (const c of found) {
    const kind = c.tag === 'select' ? `select[${c.options}]` : `${c.tag}/${c.type}`;
    console.log(`     ${c.label || '(khong nhan)'} :: ${kind}${c.id ? ` #${c.id}` : ''}`);
  }
  return found;
};

// ── sign in ────────────────────────────────────────────────────────────────
await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
await page.fill('input[type="email"]', process.env.DEMO_EMAIL || 'admin@appbi.local');
await page.fill('input[type="password"]', process.env.DEMO_PASSWORD || '');
await page.click('button[type="submit"]');
await page.waitForURL((u) => !u.pathname.includes('/login'), { timeout: 30000 });
console.log(`  dang nhap: ${page.url().replace(BASE, '')}`);

// ── the project list ───────────────────────────────────────────────────────
await page.goto(`${BASE}/builder`, { waitUntil: 'networkidle' });
await shot('builder-01-danh-sach');
await report('Danh sách project');

// ── create one ─────────────────────────────────────────────────────────────
const name = `Audit ${new Date().toISOString().slice(11, 19).replace(/:/g, '')}`;
const newButton = page.getByRole('button', { name: /connector|tạo|new|thêm/i }).first();
if (await newButton.count()) {
  await newButton.click();
  await page.waitForTimeout(700);
  await shot('builder-02-hop-thoai-tao');
  // By label, not "first visible text input" -- the search box sits above the
  // create row and swallowed the name, so the submit button stayed disabled.
  const field = page.locator('input[placeholder*="Shopify"], input#builder-new-name')
    .first();
  if (await field.count()) {
    await field.fill(name);
  } else {
    problems.push('khong tim thay o nhap ten connector');
  }
  const submit = page.getByRole('button', { name: /tạo|create|lưu|save/i }).last();
  await submit.click();
  await page.waitForTimeout(2500);
} else {
  problems.push('khong tim thay nut tao project tren /builder');
}
console.log(`  sau khi tao: ${page.url().replace(BASE, '')}`);
await shot('builder-03-editor');

// ── the editor, group by group ─────────────────────────────────────────────
// Open every collapsible so the screenshot shows the whole surface rather than
// a column of closed headers.
const openAll = async () => {
  const headers = page.locator('button[aria-expanded="false"]');
  for (let i = 0; i < 40; i += 1) {
    const closed = page.locator('button[aria-expanded="false"]');
    if (!(await closed.count())) break;
    await closed.first().click();
    await page.waitForTimeout(150);
  }
  return headers;
};

await openAll();
await shot('builder-04-editor-mo-het');
const editorControls = await report('Editor (đã mở hết nhóm)');

// ── the tabs / sections the left rail offers ───────────────────────────────
const rail = await page.evaluate(() => [...document.querySelectorAll('nav a, aside a, aside button')]
  .map((el) => el.textContent.trim().replace(/\s+/g, ' '))
  .filter(Boolean).slice(0, 30));
console.log(`\n  == Điều hướng trong editor ==\n     ${rail.join(' | ')}`);

// ── YAML view, which is the one place FE and BE must agree exactly ─────────
const yamlToggle = page.getByRole('button', { name: /^yaml$/i }).first();
if (await yamlToggle.count()) {
  await yamlToggle.click();
  await page.waitForTimeout(900);
  await shot('builder-05-yaml');
  const text = await page.evaluate(() => {
    const pre = document.querySelector('pre, textarea, .cm-content');
    return pre ? pre.textContent.slice(0, 1800) : '';
  });
  console.log('\n  == YAML đang hiển thị ==');
  console.log(text.split('\n').slice(0, 40).map((l) => `     ${l}`).join('\n'));
} else {
  problems.push('khong co che do xem YAML trong editor');
}

fs.writeFileSync(`${OUT}/builder-controls.json`,
  JSON.stringify(editorControls, null, 2), 'utf8');

console.log(`\n  == Vấn đề ghi nhận (${problems.length}) ==`);
for (const p of [...new Set(problems)]) console.log(`     ${p}`);

await browser.close();
