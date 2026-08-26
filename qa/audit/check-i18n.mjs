/**
 * i18n coverage gate (spec section 37).
 *
 * Two failure modes this catches:
 *  1. A user-visible Vietnamese string hardcoded in a component, which would
 *     leak through untranslated when the locale is EN.
 *  2. A key present in `vi` but missing from `en`.
 *
 * Run from the frontend directory: `npm run check:i18n`
 */
import fs from 'node:fs';
import path from 'node:path';

const SRC = path.resolve(process.argv[2] ?? 'src');
const CATALOG = path.join(SRC, 'lib', 'i18n.ts');

// Vietnamese-specific letters. Latin-only words are ambiguous, so the check
// keys on diacritics and đ/Đ, which never appear in English copy.
const VIETNAMESE = /[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđĐ]/;

// Punctuation and glyphs that are language-neutral separators, not copy.
const NEUTRAL = /^[\s·×•—–\-|/:,.()[\]{}]*$/;

function walk(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(full));
    else if (/\.tsx?$/.test(entry.name)) out.push(full);
  }
  return out;
}

const problems = [];

// Keys a component asks for that no catalogue answers.
//
// The two checks below compare the catalogues to each other, so a key present
// in neither passes both -- and renders on screen as its own dotted name.
// `pipelines.runQueued` shipped that way: `t('pipelines.runQueued')` was called
// on every manual run and the toast said "pipelines.runQueued".
//
// Template keys (`t(`a.b.${x}`)`) cannot be resolved statically and are listed
// as unresolved rather than failed, so the prefix can be eyeballed in review.
const usedKeys = new Map();
const templateKeys = new Set();

for (const file of walk(SRC)) {
  if (path.resolve(file) === CATALOG) continue;
  const relative = path.relative(SRC, file).replace(/\\/g, '/');
  const lines = fs.readFileSync(file, 'utf8').split('\n');

  const whole = lines.join('\n');
  for (const match of whole.matchAll(/\bt\(\s*'([\w.]+)'/g)) {
    if (!usedKeys.has(match[1])) usedKeys.set(match[1], relative);
  }
  for (const match of whole.matchAll(/\bt\(\s*`([^`]+)`/g)) {
    templateKeys.add(`${relative}: ${match[1]}`);
  }

  lines.forEach((line, index) => {
    const trimmed = line.trim();
    // Comments are documentation, not UI copy.
    if (trimmed.startsWith('//') || trimmed.startsWith('*') || trimmed.startsWith('/*')) return;
    if (!VIETNAMESE.test(line)) return;

    // Strip everything the catalog already owns, then see if Vietnamese remains.
    const withoutKeys = line.replace(/t\(\s*['"`][^'"`]*['"`]/g, '');
    if (!VIETNAMESE.test(withoutKeys)) return;

    // Pull out the actual literal so a lone separator does not trip the gate.
    const literals = withoutKeys.match(/['"`]([^'"`]*)['"`]/g) ?? [];
    const jsxText = withoutKeys.replace(/<[^>]*>/g, '').replace(/\{[^}]*\}/g, '');
    const candidates = [...literals, jsxText];
    const offending = candidates.filter(
      (value) => VIETNAMESE.test(value) && !NEUTRAL.test(value.replace(/['"`]/g, '')),
    );
    if (offending.length === 0) return;

    problems.push(`${relative}:${index + 1}  ${trimmed.slice(0, 100)}`);
  });
}

// ── catalog parity ─────────────────────────────────────────────────────────
const catalogSource = fs.readFileSync(CATALOG, 'utf8');
function keysOf(name) {
  const start = catalogSource.indexOf(`const ${name}: Catalog = {`);
  if (start < 0) return new Set();
  // Scan to the matching close brace so nested objects cannot end it early.
  let depth = 0;
  let end = start;
  for (let i = catalogSource.indexOf('{', start); i < catalogSource.length; i += 1) {
    if (catalogSource[i] === '{') depth += 1;
    if (catalogSource[i] === '}') {
      depth -= 1;
      if (depth === 0) { end = i; break; }
    }
  }
  const body = catalogSource.slice(start, end);
  // Both quote styles. The catalogue was written single-quoted and this
  // pattern only matched that, so a batch of double-quoted entries was
  // invisible: the gate counted 836 keys, said "coverage OK", and had not
  // looked at a hundred of them. A key is a key however it is quoted.
  return new Set([...body.matchAll(/^\s*['"]([^'"]+)['"]\s*:/gm)].map((m) => m[1]));
}

const viKeys = keysOf('vi');
const enKeys = keysOf('en');
const missingInEn = [...viKeys].filter((key) => !enKeys.has(key));
const missingInVi = [...enKeys].filter((key) => !viKeys.has(key));

console.log(`catalog: ${viKeys.size} vi keys, ${enKeys.size} en keys`);

let failed = false;
if (problems.length) {
  failed = true;
  console.error(`\n${problems.length} hardcoded Vietnamese string(s) outside the catalog:`);
  problems.forEach((p) => console.error('  ' + p));
}
if (missingInEn.length) {
  failed = true;
  console.error(`\n${missingInEn.length} key(s) missing from en:`);
  missingInEn.forEach((k) => console.error('  ' + k));
}
if (missingInVi.length) {
  failed = true;
  console.error(`\n${missingInVi.length} key(s) missing from vi:`);
  missingInVi.forEach((k) => console.error('  ' + k));
}

const unknown = [...usedKeys]
  .filter(([key]) => !viKeys.has(key) && !enKeys.has(key))
  .map(([key, file]) => `${key}  (${file})`);
if (unknown.length) {
  failed = true;
  console.error(`
${unknown.length} key(s) used in code but in no catalogue:`);
  unknown.forEach((k) => console.error('  ' + k));
}
if (templateKeys.size) {
  console.log(`
${templateKeys.size} template key(s), not statically checkable:`);
  [...templateKeys].forEach((k) => console.log('  ' + k));
}

if (failed) process.exit(1);
console.log('i18n coverage OK');
