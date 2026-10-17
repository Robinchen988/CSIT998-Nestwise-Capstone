import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const candidate = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const root = path.resolve(process.argv[2] || candidate);
const read = rel => {
  const selected = path.join(root, rel);
  return fs.readFileSync(fs.existsSync(selected) ? selected : path.join(candidate, rel), 'utf8');
};
const app = read('app/web/app.js');
const favorites = read('app/web/favorites.js');
const i18n = read('app/web/i18n.js');
let pass = 0;
let fail = 0;
function check(ok, name) {
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}`);
  if (ok) pass++; else fail++;
}

const match = app.match(/const area = (v => [^\n]*?);[^\n]*\n\s*const metaItems = (\[[\s\S]*?\]\.filter\(Boolean\));/);
check(Boolean(match), '[detail] found the real detail metadata expression');
if (match) {
  const render = new Function('m', 'T', 'typeZh', `const area = ${match[1]}; return ${match[2]}.join(' · ');`);
  for (const [lang, label] of [['zh', '车位'], ['en', 'car spaces']]) {
    const T = {
      beds: n => `${n} beds`, baths: n => `${n} baths`,
      carSpaces: n => `${n} ${label}`, toCbd: km => `${km} km to CBD`,
      landArea: v => `land ${v}`, buildingArea: v => `floor ${v}`,
    };
    for (const car of [0, null, 3]) {
      const meta = render({ bedrooms: 2, bathrooms: 1, property_type: 'house', car_spaces: car, distance_cbd: 4 }, T, () => 'house');
      check(!/车位|car spaces?/i.test(meta) && meta.includes('2 beds') && meta.includes('4 km to CBD'),
        `[detail ${lang}] car_spaces=${car} omitted while other metadata stays`);
    }
  }
}

check(/window\.nwOpenDetail\s*=\s*m\s*=>\s*openDetail\(m\)/.test(app)
  && /function openDetail\([^)]*\)[\s\S]*?renderDetailInto\(d, m\)/.test(app)
  && /window\.nwOpenDetail\s*&&\s*window\.nwOpenDetail\(metric\)/.test(favorites),
  '[favorites] saved property detail uses the same detail renderer');
check(!app.split(/\r?\n/).find(s => s.includes("'card-meta'"))?.includes('car_spaces'),
  '[card] result card does not show car spaces');
check(!favorites.split(/\r?\n/).find(s => s.includes('meta.textContent'))?.includes('car_spaces'),
  '[favorites] saved list row does not show car spaces');
check(!/\bcarSpaces\b/.test(i18n), '[i18n] Chinese and English carSpaces labels are absent');
console.log(`RESULT ${pass} passed, ${fail} failed`);
process.exitCode = fail ? 1 : 0;
