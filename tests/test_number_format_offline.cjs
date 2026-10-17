const fs = require('fs'), path = require('path'), vm = require('vm');
const root = path.resolve(__dirname, '..');
const app = fs.readFileSync(path.join(root, 'app/web/app.js'), 'utf8');
const fav = fs.readFileSync(path.join(root, 'app/web/favorites.js'), 'utf8');
const sb = { window: {} };
vm.runInNewContext(fs.readFileSync(path.join(root, 'app/web/i18n.js'), 'utf8'), sb);
let fails = 0, checks = 0;
const check = (c, msg) => { checks++; console.log((c ? 'ok   ' : 'FAIL ') + msg); if (!c) fails++; };
const grab = (src, re, what) => { const m = src.match(re); check(!!m, `found ${what}`); return m ? m[0] : 'null'; };

const code = [
  grab(app, /^\s*const money = .*$/m, 'app.js money'), grab(app, /^\s*const pct = .*$/m, 'app.js pct'), grab(app, /^\s*const dist = .*$/m, 'app.js dist'),
  grab(app, /^\s*function fmtEvidence\(.*$/m, 'app.js fmtEvidence'),
  grab(app, /^\s*function rankText\(m, attr\) \{[\s\S]*?\n  \}/m, 'app.js rankText'),
].join('\n');
const ctx = { T: sb.window.I18N.zh };
vm.createContext(ctx);
vm.runInContext(code + '\nthis.money=money;this.pct=pct;this.dist=dist;this.fmtEvidence=fmtEvidence;this.rankText=rankText;', ctx);
const { money, pct, dist, fmtEvidence, rankText } = ctx;

const fctx = { Intl };
vm.createContext(fctx);
vm.runInContext(grab(fav, /^\s*const money = .*$/m, 'favorites.js money') + '\n' + grab(fav, /^\s*const short = .*$/m, 'favorites.js short') +
  '\nthis.fmoney=money;this.short=short;', fctx);

check(money(null) === '—' && money(undefined) === '—' && money(NaN) === '—' && money('abc') === '—', '[BOUNDARY] money: missing/NaN/non-numeric -> em dash, never $0 or NaN');
check(money(0) === '$0', '[BOUNDARY] money(0) = $0 (a real zero is shown, not hidden)');
check(money(1234567) === '$1,234,567' && money(799999.5) === '$800,000', `[BOUNDARY] money groups thousands and rounds to whole dollars (${money(799999.5)})`);
check(money(-5) === '$-5', `[CHARACTERIZATION] negative money renders as "${money(-5)}" (sign after $); reachable only via /api/recalc with negative rent — no bound in RecalcIn`);
check(pct(0.042) === '4.2%' && pct(0) === '0.0%' && pct(1) === '100.0%' && pct(-0.05) === '-5.0%', '[BOUNDARY] pct: fractions x100 with one decimal (0.042 -> 4.2%, 0 -> 0.0%)');
check(pct(null) === '—' && pct(NaN) === '—', '[BOUNDARY] pct(null/NaN) -> em dash');
check(pct(0.0123456, 2) === '1.23%', '[BOUNDARY] pct honours the digits argument');
check(dist(0) === '0 m' && dist(43) === '43 m' && dist(999) === '999 m' && dist(1000) === '1.0 km' && dist(2549) === '2.5 km', '[BOUNDARY] dist: meters < 1000 as m, else km with one decimal');
check(dist(null) === '—' && dist(NaN) === '—', '[BOUNDARY] dist(null/NaN) -> em dash');
check(dist(999.6) === '1.0 km', `[DEFECT] 999.6 m must not print as "1000 m" (got "${dist(999.6)}")`);
check(dist(999.4) === '999 m', `[BOUNDARY] 999.4 m -> "${dist(999.4)}"`);
check(fmtEvidence(650, '米') === '650 m' && fmtEvidence(1500, 'm') === '1.5 km', '[BOUNDARY] fmtEvidence: meter units go through dist()');
check(fmtEvidence(5, '家') === '5 家' && fmtEvidence(3.14159, '分') === '3.1 分' && fmtEvidence(7, '') === '7', '[BOUNDARY] fmtEvidence: integers as-is, floats one decimal, unit appended only when present');
check(fmtEvidence('x', '米') === '—' && fmtEvidence(null, '米') === '—' && fmtEvidence(undefined, 'm') === '—',
      `[BOUNDARY] non-numeric / missing value with a meter unit -> em dash via dist() (got "${fmtEvidence('x', '米')}"); callers also skip null evidence (app.js:793 guard)`);
const rt = v => rankText({ context_ranks: { quiet: v } }, 'quiet');
check(rt(null) === '' && rankText({}, 'quiet') === '', '[BOUNDARY] no rank -> no sentence (nothing invented)');
check(rt(100) === '高于全库 99% 的房源', `[BOUNDARY] rank 100 clamped to 99 ("${rt(100)}"): never says higher than 100%`);
check(rt(57) === '高于全库 57% 的房源', '[BOUNDARY] rank 57 -> 57% (percentile, not the 0-100 score)');
check(rt(0) === '高于全库 0% 的房源', `[DEFECT] zh: rank 0 must read 0%, not be clamped UP to 1% (got "${rt(0)}")`);
ctx.T = sb.window.I18N.en;
check(rt(0) === 'Higher than 0% of properties in the database', `[DEFECT] en: rank 0 must read 0% (got "${rt(0)}")`);
check(rt(57) === 'Higher than 57% of properties in the database', `[BOUNDARY] en: rank 57 -> 57% (got "${rt(57)}")`);
check(rt(100) === 'Higher than 99% of properties in the database', `[BOUNDARY] en: rank 100 -> clamped to 99%, never "100%" (got "${rt(100)}")`);
ctx.T = sb.window.I18N.zh;
const { short, fmoney } = fctx;
check(short(450000) === '$450k' && short(1500000) === '$1.5M' && short(2000000) === '$2M' && short(1050000) === '$1.05M', '[BOUNDARY] favourites short price: k below 1M, M with up to 2 decimals');
check(short(999600) === '$1M', `[DEFECT] 999,600 must read $1M, not "$1000k" (got "${short(999600)}")`);
check(short(999400) === '$999k', `[BOUNDARY] 999,400 -> ${short(999400)}`);
check(/1,234,568/.test(fmoney(1234567.6)), `[BOUNDARY] favourites money groups/rounds (${fmoney(1234567.6)}; currency symbol is ICU-dependent)`);

check(dist(999.49) === '999 m' && dist(999.5) === '1.0 km' && dist(1000.1) === '1.0 km',
      '[BOUNDARY] rounded-meter threshold: 999.49 / 999.5 / 1000.1');
check(short(0) === '$0k' && short(999499) === '$999k' && short(999500) === '$1M' && short(1000001) === '$1M',
      '[BOUNDARY] zero and rounded-k threshold: 999499 / 999500 / 1000001');
check(rt(1) === '高于全库 1% 的房源', '[BOUNDARY] valid rank 1 remains 1%');
const observations = [
  ['distance negative -1', dist(-1)], ['distance negative -0.4', dist(-0.4)],
  ['distance Infinity', dist(Infinity)], ['short null direct call', short(null)],
  ['short NaN direct call', short(NaN)], ['short negative -1000', short(-1000)],
  ['rank NaN', rt(NaN)], ['rank negative -1', rt(-1)]
];
console.log('[OUTSIDE-CONTRACT OBSERVATIONS] ' + JSON.stringify(observations));

console.log(fails ? `FAILED: ${fails}` : `ALL PASSED: ${checks} checks`);
process.exit(fails ? 1 : 0);
