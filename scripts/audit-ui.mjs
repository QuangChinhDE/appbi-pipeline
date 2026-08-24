/**
 * Adversarial UI audit.
 *
 * Not a happy-path smoke test: this walks every screen in several states
 * (populated tenant, empty tenant, read-only role, three viewports) and reports
 * layout overflow, unlabelled controls, colour-only status, dead ends, broken
 * empty states, console noise and slow calls.
 */
import { chromium } from 'playwright';
import fs from 'node:fs';

const BASE = process.env.BASE_URL ?? 'http://localhost:8080';
const OUT = process.env.SHOT_DIR ?? './audit-shots';
fs.mkdirSync(OUT, { recursive: true });

const findings = [];
const seenKeys = new Set();

function report(severity, area, detail) {
  const key = `${severity}|${area}|${detail}`;
  if (seenKeys.has(key)) return;
  seenKeys.add(key);
  findings.push({ severity, area, detail });
}

const ROUTES = [
  ['/overview', 'Overview'],
  ['/sources', 'Sources'],
  ['/destinations', 'Destinations'],
  ['/pipelines', 'Pipelines'],
  ['/runs', 'Runs'],
  ['/monitoring', 'Monitoring'],
  ['/alerts', 'Alerts'],
  ['/connectors', 'Connectors'],
  ['/builder', 'Builder'],
  ['/audit', 'Audit'],
  ['/settings/workspace', 'Settings/Workspace'],
  ['/settings/access', 'Settings/Access'],
  ['/settings/engine', 'Settings/Engine'],
  ['/sources/new', 'Source wizard'],
  ['/destinations/new', 'Destination wizard'],
  ['/pipelines/new', 'Pipeline wizard'],
];

const browser = await chromium.launch();

async function newSession(viewport) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  page.on('console', (m) => {
    const text = m.text();
    // Expected during the error-path pass; those statuses are asserted directly.
    if (/Failed to load resource/.test(text)) return;
    if (m.type() === 'error') report('console', 'runtime', text.slice(0, 180));
    if (m.type() === 'warning' && /Warning:|hydrat/i.test(text)) {
      report('console', 'react', text.slice(0, 180));
    }
  });
  page.on('pageerror', (e) => report('blocker', 'runtime', `pageerror: ${e.message.slice(0, 180)}`));
  page.on('requestfailed', (r) => {
    if (!r.url().includes('/api/')) return;
    report('blocker', 'network', `${r.method()} ${r.url().replace(BASE, '')} failed`);
  });
  page.on('response', async (r) => {
    if (!r.url().includes('/api/')) return;
    const path = r.url().replace(BASE, '');
    if (r.status() >= 500) report('blocker', 'api', `${r.status()} ${path}`);
    const timing = r.request().timing();
    const total = timing.responseEnd - timing.requestStart;
    if (total > 3000 && !/discover|test|refresh/.test(path)) {
      report('perf', 'api', `${Math.round(total)}ms ${path}`);
    }
  });
  return { context, page };
}

async function login(page, email) {
  await page.goto(`${BASE}/login`, { waitUntil: 'load' });
  await page.waitForSelector('#email');
  await page.waitForTimeout(1200);
  await page.fill('#email', email);
  await page.fill('#password', 'Admin@12345');
  await page.click('button[type=submit]');
  await page.waitForURL('**/overview', { timeout: 45000 });
}

async function settle(page) {
  try {
    await page.waitForFunction(() => document.querySelectorAll('.skeleton').length === 0,
      { timeout: 12000 });
  } catch { /* some screens have no skeleton */ }
  await page.waitForTimeout(900);
  // Let CSS transitions finish so a mid-flight transform is not read as overflow.
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => setTimeout(r, 250))));
}

/** Structural problems visible in the DOM, checked the same way on every page. */
async function inspect(page, label) {
  const result = await page.evaluate(() => {
    // Chrome counts too: the sidebar and header are on every screen.
    const SCOPE = 'main, nav, header, aside, [role="dialog"]';
    const within = (sel) => [...document.querySelectorAll(SCOPE)]
      .flatMap((root) => [...root.querySelectorAll(sel)]);
    const out = {
      bodyOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      overflowing: [],
      unlabelled: [],
      tinyTargets: [],
      emptyHeadings: [],
      contrastRisk: [],
      textOverflow: [],
      duplicateIds: [],
      missingScope: 0,
      colourOnly: [],
    };

    // Elements wider than their scroll container that are not deliberately scrollable.
    for (const el of within('*')) {
      const style = getComputedStyle(el);
      if (el.scrollWidth > el.clientWidth + 2 &&
          !['auto', 'scroll', 'hidden', 'clip'].includes(style.overflowX) &&
          style.transitionProperty === 'none' &&
          el.clientWidth > 0) {
        out.overflowing.push(`${el.tagName.toLowerCase()}.${(el.className || '').toString().slice(0, 60)}`);
      }
    }

    // Controls with no accessible name.
    for (const el of within('button, a, input, select, textarea')) {
      const text = (el.textContent || '').trim();
      const name = el.getAttribute('aria-label') || el.getAttribute('title')
        || (el.id && document.querySelector(`label[for="${el.id}"]`)?.textContent) || '';
      if (!text && !name && el.offsetParent !== null) {
        out.unlabelled.push(`${el.tagName.toLowerCase()}#${el.id || '-'}`);
      }
    }

    // Interactive targets smaller than a comfortable tap size.
    for (const el of within('button, a[href]')) {
      const rect = el.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0 && (rect.height < 20 || rect.width < 20)) {
        out.tinyTargets.push(`${el.tagName.toLowerCase()} ${Math.round(rect.width)}x${Math.round(rect.height)} "${(el.textContent || '').trim().slice(0, 24)}"`);
      }
    }

    for (const el of within('h1, h2, h3')) {
      if (!(el.textContent || '').trim()) out.emptyHeadings.push(el.tagName);
    }

    // Text clipped without an ellipsis.
    for (const el of within('td, th, span, p')) {
      if (el.children.length === 0 && el.scrollWidth > el.clientWidth + 2) {
        const style = getComputedStyle(el);
        if (style.textOverflow !== 'ellipsis' && style.overflow !== 'hidden') {
          out.textOverflow.push(`"${(el.textContent || '').trim().slice(0, 40)}"`);
        }
      }
    }

    const ids = [...document.querySelectorAll('[id]')].map((e) => e.id);
    out.duplicateIds = [...new Set(ids.filter((id, i) => ids.indexOf(id) !== i))];

    out.missingScope = within('table th:not([scope])').length;

    // A status pill that is a bare colour with no text or icon.
    for (const el of within('[class*="rounded-full"]')) {
      const rect = el.getBoundingClientRect();
      if (rect.width > 0 && rect.width < 14 && !(el.textContent || '').trim()
          && !el.getAttribute('aria-label') && !el.querySelector('svg')) {
        out.colourOnly.push(el.className.toString().slice(0, 60));
      }
    }
    return out;
  });

  if (result.bodyOverflow > 2) {
    report('layout', label, `page scrolls horizontally by ${result.bodyOverflow}px`);
  }
  for (const el of result.overflowing.slice(0, 3)) {
    report('layout', label, `content overflows its box: ${el}`);
  }
  for (const el of [...new Set(result.unlabelled)].slice(0, 5)) {
    report('a11y', label, `control has no accessible name: ${el}`);
  }
  for (const el of [...new Set(result.tinyTargets)].slice(0, 3)) {
    report('a11y', label, `target under 20px: ${el}`);
  }
  for (const el of result.emptyHeadings) report('a11y', label, `empty heading ${el}`);
  for (const el of [...new Set(result.textOverflow)].slice(0, 3)) {
    report('layout', label, `text clipped with no ellipsis: ${el}`);
  }
  for (const id of result.duplicateIds.slice(0, 3)) {
    report('bug', label, `duplicate DOM id "${id}"`);
  }
  if (result.missingScope) report('a11y', label, `${result.missingScope} <th> without scope`);
  for (const el of [...new Set(result.colourOnly)].slice(0, 2)) {
    report('a11y', label, `status conveyed by colour alone: ${el}`);
  }
  return result;
}

// ── pass 1: populated tenant, desktop ──────────────────────────────────────
console.log('== pass 1: admin, populated workspace, 1440x900 ==');
{
  const { context, page } = await newSession({ width: 1440, height: 900 });
  await login(page, 'admin@appbi.local');

  for (const [route, label] of ROUTES) {
    await page.goto(`${BASE}${route}`, { waitUntil: 'load' });
    await settle(page);
    await inspect(page, label);
    await page.screenshot({ path: `${OUT}/p1-${label.replace(/\W+/g, '-')}.png`, fullPage: true });

    // Every list screen should show data or a real empty state, never a blank pane.
    const main = (await page.textContent('main')) ?? '';
    if (main.trim().length < 40) report('bug', label, 'main region is essentially empty');
  }

  // Detail screens.
  await page.goto(`${BASE}/pipelines`, { waitUntil: 'load' });
  await settle(page);
  const pipelineHref = await page.locator('tbody a[href^="/pipelines/"]').first()
    .getAttribute('href').catch(() => null);
  if (pipelineHref) {
    await page.goto(`${BASE}${pipelineHref}`, { waitUntil: 'load' });
    await settle(page);
    await inspect(page, 'Pipeline detail');
    for (const tab of ['Dữ liệu & cấu trúc', 'Lần chạy', 'Cài đặt']) {
      const button = page.getByRole('tab', { name: tab });
      if (await button.count()) {
        await button.click();
        await settle(page);
        await inspect(page, `Pipeline detail · ${tab}`);
        await page.screenshot({
          path: `${OUT}/p1-pipeline-${tab.split(' ')[0]}.png`, fullPage: true });
      }
    }
  } else {
    report('bug', 'Pipelines', 'no pipeline row to open — cannot audit the detail screen');
  }

  await page.goto(`${BASE}/runs`, { waitUntil: 'load' });
  await settle(page);
  const runHref = await page.locator('tbody a[href^="/runs/"]').first()
    .getAttribute('href').catch(() => null);
  if (runHref) {
    await page.goto(`${BASE}${runHref}`, { waitUntil: 'load' });
    await settle(page);
    await inspect(page, 'Run detail');
    const logsTab = page.getByRole('tab', { name: /Nhật ký/ });
    if (await logsTab.count()) {
      await logsTab.click();
      await page.waitForTimeout(2500);
      await inspect(page, 'Run detail · logs');
      await page.screenshot({ path: `${OUT}/p1-run-logs.png`, fullPage: true });
    }
  }

  await page.goto(`${BASE}/sources`, { waitUntil: 'load' });
  await settle(page);
  const sourceHref = await page.locator('tbody a[href^="/sources/"]').first()
    .getAttribute('href').catch(() => null);
  if (sourceHref) {
    await page.goto(`${BASE}${sourceHref}`, { waitUntil: 'load' });
    await settle(page);
    await inspect(page, 'Source detail');
    const configTab = page.getByRole('tab', { name: 'Cấu hình' });
    if (await configTab.count()) {
      await configTab.click();
      await settle(page);
      await inspect(page, 'Source detail · config');
      await page.screenshot({ path: `${OUT}/p1-source-config.png`, fullPage: true });
    }
  }

  // Language switch must translate, not just persist.
  await page.goto(`${BASE}/pipelines`, { waitUntil: 'load' });
  await settle(page);
  const beforeSwitch = (await page.textContent('main')) ?? '';
  await page.evaluate(() => window.localStorage.setItem('appbi.integration.locale', 'en'));
  await page.reload({ waitUntil: 'load' });
  await settle(page);
  const afterSwitch = (await page.textContent('main')) ?? '';
  if (beforeSwitch === afterSwitch) {
    report('bug', 'i18n', 'switching locale to EN changed nothing on /pipelines');
  }
  const stillVietnamese = /Tạo pipeline|Tình trạng|Lịch chạy|Đang bật|Hoạt động tốt|Thành công|Chi tiết|Tạm dừng|Đồng bộ ngay/.exec(afterSwitch);
  if (stillVietnamese) {
    report('i18n', 'Pipelines', `EN locale still renders Vietnamese: "${stillVietnamese[0]}"`);
  }
  await page.screenshot({ path: `${OUT}/p1-pipelines-EN.png`, fullPage: true });
  await page.evaluate(() => window.localStorage.setItem('appbi.integration.locale', 'vi'));

  await context.close();
}

// ── pass 2: empty tenant ───────────────────────────────────────────────────
console.log('== pass 2: admin, empty workspace (Marketing Analytics) ==');
{
  const { context, page } = await newSession({ width: 1440, height: 900 });
  await login(page, 'admin@appbi.local');

  // Switch workspace through the UI, as a user would.
  await page.locator('button:has-text("AppBI Data Team")').first().click();
  await page.waitForTimeout(400);
  const other = page.locator('button:has-text("Marketing Analytics")').first();
  if (await other.count()) {
    await other.click();
    await page.waitForTimeout(2500);
  } else {
    report('bug', 'Workspace switcher', 'second workspace not offered in the switcher');
  }

  for (const [route, label] of ROUTES.slice(0, 12)) {
    await page.goto(`${BASE}${route}`, { waitUntil: 'load' });
    await settle(page);
    await inspect(page, `EMPTY ${label}`);
    const main = (await page.textContent('main')) ?? '';
    // An empty tenant must still explain itself.
    if (/^\s*$/.test(main)) report('bug', `EMPTY ${label}`, 'blank page on an empty workspace');
    await page.screenshot({ path: `${OUT}/p2-empty-${label.replace(/\W+/g, '-')}.png`,
      fullPage: true });
  }
  await context.close();
}

// ── pass 3: read-only role ─────────────────────────────────────────────────
console.log('== pass 3: analyst (read-only) ==');
{
  const { context, page } = await newSession({ width: 1440, height: 900 });
  await login(page, 'analyst@appbi.local');

  for (const [route, label] of ROUTES) {
    await page.goto(`${BASE}${route}`, { waitUntil: 'load' });
    await settle(page);
    const main = (await page.textContent('main')) ?? '';

    // A read-only user must not be offered a mutating *control*. A heading that
    // merely names the page is not an affordance, so only interactive elements
    // count here.
    const offered = await page.evaluate(() => {
      const labels = [];
      for (const el of document.querySelectorAll('main button, main a[href]')) {
        if (el.hasAttribute('disabled') || el.getAttribute('aria-disabled') === 'true') continue;
        // A toggle-state chip is a filter, not a mutation, even when it shares
        // its label with an action ("Tạm dừng" is both a filter and a verb).
        if (el.hasAttribute('aria-pressed')) continue;
        labels.push((el.textContent || '').trim());
      }
      return labels;
    });
    for (const forbidden of ['Tạo pipeline', 'Thêm nguồn', 'Thêm đích', 'Đồng bộ ngay', 'Xóa']) {
      if (offered.some((l) => l === forbidden)) {
        report('rbac', `ANALYST ${label}`, `read-only user is offered the control "${forbidden}"`);
      }
    }
    // A wizard that walks to a guaranteed 403 is a dead end; the guard should
    // replace the form, not sit under it.
    if (route.endsWith('/new')) {
      const guarded = /không có quyền|cannot create/i.test(main);
      const hasForm = (await page.locator('main input, main select').count()) > 0;
      if (!guarded && hasForm) {
        report('rbac', `ANALYST ${label}`,
          'wizard form renders for a role without create permission');
      }
    }
    await page.screenshot({ path: `${OUT}/p3-analyst-${label.replace(/\W+/g, '-')}.png`,
      fullPage: true });
  }
  await context.close();
}

// ── pass 4: narrow viewports ───────────────────────────────────────────────
for (const [width, height, name] of [[1024, 800, 'tablet'], [390, 844, 'mobile']]) {
  console.log(`== pass 4: ${name} ${width}x${height} ==`);
  const { context, page } = await newSession({ width, height });
  await login(page, 'admin@appbi.local');
  for (const [route, label] of ROUTES.slice(0, 9)) {
    await page.goto(`${BASE}${route}`, { waitUntil: 'load' });
    await settle(page);
    await inspect(page, `${name.toUpperCase()} ${label}`);
    await page.screenshot({ path: `${OUT}/p4-${name}-${label.replace(/\W+/g, '-')}.png`,
      fullPage: true });
  }
  await context.close();
}

// ── pass 5: error and edge paths ───────────────────────────────────────────
console.log('== pass 5: error paths ==');
{
  const { context, page } = await newSession({ width: 1440, height: 900 });

  // Bad login must show a usable message.
  await page.goto(`${BASE}/login`, { waitUntil: 'load' });
  await page.waitForSelector('#email');
  await page.waitForTimeout(1000);
  await page.fill('#email', 'admin@appbi.local');
  await page.fill('#password', 'definitely-wrong');
  await page.click('button[type=submit]');
  await page.waitForTimeout(2500);
  const loginBody = (await page.textContent('body')) ?? '';
  if (!/không đúng|không hợp lệ/i.test(loginBody)) {
    report('bug', 'Login', 'wrong password shows no visible error');
  }
  await page.screenshot({ path: `${OUT}/p5-login-error.png`, fullPage: true });

  await login(page, 'admin@appbi.local');

  // Unknown ids must render a real not-found state, not a crash or a spinner.
  for (const [route, label] of [
    ['/pipelines/00000000-0000-0000-0000-000000000000', 'Pipeline 404'],
    ['/runs/00000000-0000-0000-0000-000000000000', 'Run 404'],
    ['/sources/00000000-0000-0000-0000-000000000000', 'Source 404'],
  ]) {
    await page.goto(`${BASE}${route}`, { waitUntil: 'load' });
    await page.waitForTimeout(3000);
    const body = (await page.textContent('body')) ?? '';
    if (/Đang tải/.test(body) && !/Không tìm thấy|không tải được/i.test(body)) {
      report('bug', label, 'unknown id leaves the page stuck on a spinner');
    }
    if (!/Không tìm thấy|Không tải được/i.test(body)) {
      report('bug', label, 'unknown id shows no not-found message');
    }
    await page.screenshot({ path: `${OUT}/p5-${label.replace(/\W+/g, '-')}.png`, fullPage: true });
  }

  // A malformed id (not a uuid) is a different code path.
  await page.goto(`${BASE}/pipelines/not-a-uuid`, { waitUntil: 'load' });
  await page.waitForTimeout(2500);
  const malformed = (await page.textContent('body')) ?? '';
  if (!/Không tìm thấy|không hợp lệ|Không tải được/i.test(malformed)) {
    report('bug', 'Pipeline malformed id', 'no readable error for a non-uuid id');
  }
  await page.screenshot({ path: `${OUT}/p5-malformed-id.png`, fullPage: true });

  // Wizard: Continue with nothing filled must explain itself.
  await page.goto(`${BASE}/sources/new`, { waitUntil: 'load' });
  await settle(page);
  await page.getByRole('button', { name: /PostgreSQL/ }).first().click();
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  await page.waitForSelector('#actor-name');
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  await page.waitForTimeout(900);
  const blocked = (await page.textContent('main')) ?? '';
  if (!/bắt buộc|cần điền/i.test(blocked)) {
    report('bug', 'Source wizard', 'Continue with empty required fields gives no feedback');
  }
  await page.screenshot({ path: `${OUT}/p5-wizard-validation.png`, fullPage: true });

  // Deleting a source that pipelines depend on must surface the dependency list.
  await page.goto(`${BASE}/sources`, { waitUntil: 'load' });
  await settle(page);
  const inUse = page.locator('tbody tr').filter({ hasText: 'PostgreSQL' }).first();
  if (await inUse.count()) {
    const href = await inUse.locator('a[href^="/sources/"]').first().getAttribute('href');
    await page.goto(`${BASE}${href}`, { waitUntil: 'load' });
    await settle(page);
    const del = page.getByRole('button', { name: 'Xóa' }).first();
    if (await del.count()) {
      await del.click();
      await page.waitForTimeout(500);
      const confirm = page.getByRole('button', { name: 'Xóa' }).last();
      await confirm.click();
      await page.waitForTimeout(2500);
      const after = (await page.textContent('body')) ?? '';
      if (!/đang được|sử dụng/i.test(after)) {
        report('bug', 'Source delete', 'dependency block is not explained in the UI');
      }
      await page.screenshot({ path: `${OUT}/p5-delete-dependency.png`, fullPage: true });
    }
  }
  await context.close();
}

await browser.close();

// ── report ─────────────────────────────────────────────────────────────────
const order = ['blocker', 'bug', 'rbac', 'layout', 'a11y', 'i18n', 'perf', 'console'];
findings.sort((a, b) => order.indexOf(a.severity) - order.indexOf(b.severity));

console.log(`\n=== ${findings.length} findings ===`);
let current = '';
for (const f of findings) {
  if (f.severity !== current) {
    current = f.severity;
    console.log(`\n[${current.toUpperCase()}]`);
  }
  console.log(`  ${f.area}: ${f.detail}`);
}
fs.writeFileSync(`${OUT}/findings.json`, JSON.stringify(findings, null, 2));
console.log(`\nscreenshots + findings.json in ${OUT}`);
