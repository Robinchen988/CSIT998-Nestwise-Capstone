import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const [port = '8000', lang = 'zh'] = process.argv.slice(2);
const prof = fs.mkdtempSync(path.join(os.tmpdir(), 'dsort-'));
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

const settle = `(async () => { await new Promise(r => setTimeout(r, 800));
  for (let k = 0; k < 120; k++) { const s = document.querySelector('#result-sort'); if (s && !s.disabled) break; await new Promise(r => setTimeout(r, 500)); }
  await new Promise(r => setTimeout(r, 800)); })()`;
const cardMetres = `[...document.querySelectorAll('.card')].map(c => {
  const t = [...c.querySelectorAll('*')].map(e => e.childElementCount ? '' : e.textContent).join(' ');
  const m = t.match(/(?:火车站|Train station)\\s*([\\d.]+)\\s*(km|m)/i);
  return m ? (m[2].toLowerCase() === 'km' ? parseFloat(m[1]) * 1000 : parseFloat(m[1])) : null; })`;
const options = `[...document.querySelectorAll('#result-sort option')].map(o => [o.value, o.textContent])`;
const choose = v => ev(`(() => { const s = document.querySelector('#result-sort'); s.value = ${JSON.stringify(v)}; s.dispatchEvent(new Event('change')); })()`);

await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
await send('Page.enable');
await send('Page.navigate', { url: `http://localhost:${port}/` });
await sleep(2000);
await ev(`localStorage.setItem('lang', ${JSON.stringify(lang)})`);
await send('Page.navigate', { url: `http://localhost:${port}/` });
await sleep(2500);
await ev(`(() => { const i = document.querySelector('#input'); i.value = '3 bedroom house under $1.2M within 1.5 km of a train station'; i.closest('form').requestSubmit(); })()`);
await ev(`(async () => { for (let k = 0; k < 120 && document.querySelectorAll('.card').length < 3; k++) await new Promise(r => setTimeout(r, 1000)); })()`);
await ev(settle);

const opts = await ev(options);
const keys = opts.map(o => o[0]);
check('menu offers distance-to-train-station sort', keys.includes('nearest:train_station'), JSON.stringify(opts.find(o => o[0] === 'nearest:train_station')));
check('no unexplained "named place" option without a named place', !keys.includes('near_place_distance'));

await choose('nearest:train_station');
await ev(settle);
let d = await ev(cardMetres);
const asc = a => a.every(v => v != null) && a.every((v, i) => i === 0 || a[i - 1] <= v);
check('cards ordered by the station distance they display', asc(d), JSON.stringify(d));
check('menu keeps the chosen sort', (await ev(`document.querySelector('#result-sort').value`)) === 'nearest:train_station');
const note = await ev(`document.querySelector('#res-notes')?.textContent || ''`);
check('ranking note names the station', /火车站|train station/i.test(note), note.slice(0, 120));

const more = await ev(`(() => { const b = [...document.querySelectorAll('button')].find(x => /换一批|Next batch/.test(x.textContent)); if (b) { b.click(); return true; } return false; })()`);
if (more) {
  await ev(settle);
  const d2 = await ev(cardMetres);
  check('next batch continues ascending', asc(d2) && d2[0] >= d[d.length - 1], JSON.stringify(d2));
}

await ev(`(() => { const chip = [...document.querySelectorAll('.chip')].find(c => /火车站|Train station/i.test(c.textContent)); chip?.querySelector('.chip-remove')?.click(); })()`);
await ev(settle);
const keys2 = (await ev(options)).map(o => o[0]);
check('removing the station filter removes the option', !keys2.includes('nearest:train_station'), JSON.stringify(keys2.slice(0, 3)));
check('sort falls back to default', (await ev(`document.querySelector('#result-sort').value`)) === '');

console.log(results.every(Boolean) ? `ALL PASSED (${results.length})` : `FAILED ${results.filter(x => !x).length}/${results.length}`);
ws.close(); chrome.kill();
process.exit(results.every(Boolean) ? 0 : 1);
