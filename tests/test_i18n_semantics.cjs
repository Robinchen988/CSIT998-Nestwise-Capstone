const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const sandbox = { window: {} };
vm.runInNewContext(fs.readFileSync(path.join(root, 'app/web/i18n.js'), 'utf8'), sandbox);
const I = sandbox.window.I18N;
const appJs = fs.readFileSync(path.join(root, 'app/web/app.js'), 'utf8');
const pipe = fs.readFileSync(path.join(root, 'pipeline/join_annual_rent.py'), 'utf8');
const failures = [], log = [];
const check = (id, cond, msg) => { log.push({ id, pass: !!cond, msg }); console.log((cond ? 'ok   ' : 'FAIL ') + `[${id}] ` + msg); if (!cond) failures.push(id); };

const emitted = new Set();
for (const m of pipe.matchAll(/source = "(\w+)"/g)) emitted.add(m[1]);
for (const m of pipe.matchAll(/rent_sources\.append\("(\w+)"\)/g)) emitted.add(m[1]);
emitted.delete('unavailable');
check('PIPE.rent_source', emitted.size === 5, 'pipeline emits 5 renderable rent_source values: ' + [...emitted].join(', '));

const cm = appJs.match(/payRow\(T\.rowRent,\s*money\(m\.annual_rent\),\s*(\[[\s\S]*?\]\s*\.filter\(Boolean\)\.join\(' · '\))\)\);/);
check('UI.compose.extract', !!cm, 'app.js still composes the rent row as payRow(T.rowRent, money(annual_rent), [yield note, rentScope[rent_source]].filter(Boolean).join(" · ")) — extracted for execution');
const pct = x => (x * 100).toFixed(1) + '%';
const money = x => '$' + Math.round(x).toLocaleString('en-US');
const composeNote = (T, m) => new Function('T', 'm', 'pct', 'return ' + cm[1])(T, m, pct);

const ASKING = /asking|guide price|vendor|指导价|挂牌|在售/i;
const UNIVERSAL_BENCH = /median|中位|benchmark|基准|DFFH/i;
const rows = {
  precinct_exact_sheet: { annual_rent: 400 * 52, price: 800000, rent_source: 'precinct_exact_sheet' },
  precinct_all_properties: { annual_rent: 350 * 52, price: 800000, rent_source: 'precinct_all_properties' },
  region_exact_sheet: { annual_rent: 380 * 52, price: 700000, rent_source: 'region_exact_sheet' },
  region_all_properties: { annual_rent: 360 * 52, price: 700000, rent_source: 'region_all_properties' },
  assumed_yield: { annual_rent: 26650, price: 1000000, rent_source: 'assumed_yield' },
};
for (const r of Object.values(rows)) r.gross_yield = r.annual_rent / r.price;
check('UNIT.weekly_to_annual', rows.precinct_exact_sheet.annual_rent === 20800 && Math.abs(rows.precinct_exact_sheet.gross_yield - 0.026) < 1e-9,
      'synthetic weekly $400 x 52 = $20,800 annual; yield 2.6% of $800,000');

for (const lang of ['zh', 'en']) {
  const T = I[lang];
  check(`UI.${lang}.rowRent`, !UNIVERSAL_BENCH.test(T.rowRent), `rowRent is provenance-neutral (applies to benchmark AND assumed rows) -> ${T.rowRent}`);
  check(`UI.${lang}.annualRentSub`, !UNIVERSAL_BENCH.test(T.annualRentSub('$1')), `annualRentSub is provenance-neutral -> ${T.annualRentSub('$1')}`);
  check(`UI.${lang}.rentCaveat`, /DFFH/.test(T.rentCaveat) && /×\s*52/.test(T.rentCaveat) && /(假设回报率|assumed yield)/.test(T.rentCaveat) && /(不是|not)/.test(T.rentCaveat),
        'rentCaveat names BOTH branches (DFFH weekly x52 and sale price x assumed yield) and denies "actual rent"');

  const out = {};
  for (const [src, m] of Object.entries(rows)) out[src] = composeNote(T, m);
  for (const src of emitted) {
    check(`UI.${lang}.rentScope.${src}`, typeof T.rentScope[src] === 'string' && out[src].includes(T.rentScope[src]) && out[src].includes(pct(rows[src].gross_yield)),
          `row note for ${src} = yield note + branch label`);
  }
  for (const src of ['precinct_exact_sheet', 'precinct_all_properties', 'region_exact_sheet', 'region_all_properties']) {
    check(`UI.${lang}.rentScope.${src}.x52`, /×\s*52/.test(out[src]), `${src}: note states weekly x 52`);
  }
  for (const src of ['region_exact_sheet', 'region_all_properties']) {
    check(`UI.${lang}.rentScope.${src}.weighted`, /(登记数|registration)/i.test(out[src]) && /(不是大区整体中位数|not a pooled regional median)/i.test(out[src]),
          `${src}: says count = rental-bond registrations and NOT a pooled regional median`);
    check(`UI.${lang}.rentScope.${src}.nomedianclaim`, !/(所在大区.*中位租金|regional median rent)/i.test(out[src].replace(/不是大区整体中位数|not a pooled regional median/i, '')),
          `${src}: does not present itself as the regional median`);
  }
  check(`UI.${lang}.rentScope.precinct_exact_sheet.clip`, /2[–-]4/.test(out.precinct_exact_sheet) && /1[–-]3/.test(out.precinct_exact_sheet), 'precinct_exact_sheet discloses bedroom clipping (2-4 / 1-3)');
  const a = out.assumed_yield;
  check(`UI.${lang}.rentScope.assumed_yield.branch`, /(不是租金基准|Not a rent benchmark)/.test(a) && !/(DFFH|片区.*中位租金)/.test(T.rentScope.assumed_yield), 'assumed_yield: says it is not a benchmark and does not claim DFFH/area-median provenance');
  check(`UI.${lang}.rentScope.assumed_yield.formula`, /(成交价×假设回报率|sale price × an assumed yield)/.test(a) && /(中位数|median recorded sale price)/.test(a) && /(登记数|registration)/.test(a),
        'assumed_yield formula: sale price x (same-type count-weighted benchmark annual rent / same-type MEDIAN sale price)');
  check(`UI.${lang}.rentScope.assumed_yield.nomatch`, /(不代表该地区没有租金数据|does not mean the area has no rent data)/.test(a), 'assumed_yield: "no match" is about this lookup, not "no rent data"');
  check(`UI.${lang}.rentScope.unknown`, composeNote(T, { annual_rent: 1, gross_yield: null, rent_source: 'unexpected' }) === '', 'unknown rent_source + null yield renders no invented provenance text');

  const pt = { askingPrice: T.askingPrice, valGap: T.valGap, gapAbove: T.gapAbove('5.0%'), gapBelow: T.gapBelow('5.0%'),
    verdictWithin: T.verdictWithin('80%'), verdictBelowRange: T.verdictBelowRange('12.0%', '80%'), verdictAboveRange: T.verdictAboveRange('12.0%', '80%') };
  for (const [k, v] of Object.entries(pt)) check(`UI.${lang}.${k}`, !ASKING.test(v), `${k} does not call the 2016-2018 sale price an asking/guide price`);
  check(`UI.${lang}.rowPriceNote`, /2016/.test(T.rowPriceNote) && /(不是|not)/.test(T.rowPriceNote), 'rowPriceNote gives the 2016-2018 period and denies "current asking price"');
  check(`UI.${lang}.askingPrice.sale`, /(成交|sale)/i.test(T.askingPrice) && /(成交|sold)/i.test(T.fav.price), 'price label and favourites label both say sale');
  check(`UI.${lang}.rank`, !/listing/i.test(T.rank(90)), `score-rank sentence does not call records "listings" -> ${T.rank(90)}`);
}
const dir = process.env.CAPSTONE_AUDIT_DIR;
if (dir) { try { fs.mkdirSync(dir, { recursive: true }); fs.writeFileSync(path.join(dir, 'static_contract_assertions.json'), JSON.stringify({ kind: 'static_contract_only', assertions: log }, null, 1)); } catch (e) {  } }
console.log(failures.length ? `FAILED: ${failures.length}` : `ALL PASSED (${log.length} static contract assertions)`);
process.exit(failures.length ? 1 : 0);
