import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const [port = '8000', lang = 'zh'] = process.argv.slice(2);
const zh = lang === 'zh';
const prof = fs.mkdtempSync(path.join(os.tmpdir(), 'addcond-'));
const dbg = 9500 + Math.floor(Math.random() * 300);
const chrome = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', [
  '--headless=new', `--remote-debugging-port=${dbg}`, `--user-data-dir=${prof}`, '--no-first-run',
  '--window-size=1440,900', 'about:blank'], { stdio: 'ignore' });
const sleep = ms => new Promise(r => setTimeout(r, ms));
let ws;
for (let i = 0; i < 50; i++) {
  try { const t = await (await fetch(`http://127.0.0.1:${dbg}/json`)).json(); const pg = t.find(x => x.type === 'page'); if (pg) { ws = new WebSocket(pg.webSocketDebuggerUrl); break; } } catch {}
  await sleep(200);
}
await new Promise(r => ws.addEventListener('open', r, { once: true }));
let id = 0; const pend = new Map();
ws.addEventListener('message', e => { const m = JSON.parse(e.data); if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); } });
const send = (method, params = {}) => new Promise(r => { const i = ++id; pend.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const ev = async expr => (await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value;
const results = [];
const check = (name, ok, extra = '') => { results.push(ok); console.log((ok ? 'PASS ' : 'FAIL ') + name + (extra ? '  ' + extra : '')); };
const settle = `(async () => { await new Promise(r => setTimeout(r, 1200));
  for (let k = 0; k < 160; k++) { const s = document.querySelector('#result-sort'); if (s && !s.disabled && !document.querySelector('.msg-status')) break; await new Promise(r => setTimeout(r, 500)); }
  await new Promise(r => setTimeout(r, 1000)); })()`;
const centre = sel => ev(`(() => { const e = ${sel}; if (!e) return null; const r = e.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; })()`);
const click = async sel => { const p = await centre(sel); if (!p) return false;
  for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased']) await send('Input.dispatchMouseEvent', { type, x: p[0], y: p[1], button: 'left', clickCount: 1 });
  await sleep(350); return true; };
const menuItems = `[...document.querySelectorAll('#popover .add-menu-item')].map(b => b.textContent)`;
const chips = `[...document.querySelectorAll('#condition-dock .chip')].map(c => c.textContent.trim())`;
const notes = `document.querySelector('#res-notes')?.textContent || ''`;
const openMenu = () => click(`document.querySelector('.cond-add')`);
const pick = label => click(`[...document.querySelectorAll('#popover .add-menu-item')].find(b => b.textContent === ${JSON.stringify(label)})`);
const fill = async values => {
  await ev(`(() => { const els = [...document.querySelectorAll('#popover select, #popover input')]; const vals = ${JSON.stringify(values)};
    vals.forEach((v, i) => { if (v == null) return; els[i].value = v; els[i].dispatchEvent(new Event('input')); els[i].dispatchEvent(new Event('change')); });
    document.querySelector('#popover').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); })()`);
  await ev(settle);
};

await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
await send('Page.enable');
await send('Page.navigate', { url: `http://localhost:${port}/` });
await sleep(2000);
await ev(`localStorage.setItem('lang', ${JSON.stringify(lang)})`);
await send('Page.navigate', { url: `http://localhost:${port}/` });
await sleep(2500);
await ev(`(() => { const i = document.querySelector('#input'); i.value = ${JSON.stringify(zh ? '找 120 万以内的 3 房独立屋' : '3 bedroom house under $1.2M')}; i.closest('form').requestSubmit(); })()`);
await ev(settle);

await openMenu();
let items = await ev(menuItems);
const T = await ev(`(() => { const L = window.I18N[${JSON.stringify(lang)}]; return { budgetMax: L.budgetMax, budgetMin: L.budgetMin, bedrooms: L.bedroomsLabel, type: L.typeLabel, cbd: L.cbdLabel, amenity: L.addAmenity, pref: L.addPreference, plan: L.addPlanning }; })()`);
check('menu opens on click', items.length > 0, JSON.stringify(items));
check('already-set filters are not offered', !items.includes(T.budgetMax) && !items.includes(T.bedrooms) && !items.includes(T.type));
check('unset filters are offered', [T.budgetMin, T.cbd, T.amenity, T.pref, T.plan].every(x => items.includes(x)));

await pick(T.amenity);
check('place-distance editor opens (type, metres, how strict)', await ev(`document.querySelectorAll('#popover select, #popover input').length === 3`));
await fill(['primary_school', '1000', 'required']);
check('primary-school distance chip added', (await ev(chips)).some(t => /(小学|Primary school).*1(\.0)?\s*km|1000\s*m/i.test(t)), JSON.stringify(await ev(chips)));
check('ranking note shows the new requirement', /小学|primary school/i.test(await ev(notes)));

await openMenu(); await pick(T.cbd); await fill(['15']);
check('CBD chip added', (await ev(chips)).some(t => /CBD\s*≤\s*15/.test(t)));
await openMenu();
check('CBD no longer offered once set', !(await ev(menuItems)).includes(T.cbd));
await pick(T.plan); await fill(['no_airport_noise']);
check('risk exclusion chip added', (await ev(chips)).some(t => /机场噪声|airport noise/i.test(t)));
await openMenu(); await pick(T.pref); await fill(['quiet', 'gte', '70']);
check('preference chip added', (await ev(chips)).some(t => /安静|Quiet/i.test(t) && /70/.test(t)), JSON.stringify(await ev(chips)));
check('all earlier filters kept', (await ev(chips)).some(t => /1,200,000/.test(t)) && (await ev(chips)).some(t => /CBD\s*≤\s*15/.test(t)));

await openMenu(); await click(`document.querySelector('#popover .add-menu-chat')`);
check('"describe it in the chat" focuses the input', await ev(`document.activeElement === document.querySelector('#input') && !document.querySelector('#popover').classList.contains('is-open')`));

console.log(results.every(Boolean) ? `ALL PASSED (${results.length})` : `FAILED ${results.filter(x => !x).length}/${results.length}`);
ws.close(); chrome.kill();
process.exit(results.every(Boolean) ? 0 : 1);
