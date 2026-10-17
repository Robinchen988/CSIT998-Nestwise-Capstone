import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const [port = '8000', lang = 'zh'] = process.argv.slice(2);
const prof = fs.mkdtempSync(path.join(os.tmpdir(), 'sync-'));
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
const centre = sel => ev(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) return null; const r = e.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; })()`);
const move = async p => { await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: p[0], y: p[1] }); await sleep(250); };
const click = async p => { for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased']) await send('Input.dispatchMouseEvent', { type, x: p[0], y: p[1], button: 'left', clickCount: 1 }); await sleep(900); };

await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
await send('Page.enable');
await send('Page.navigate', { url: `http://localhost:${port}/` });
await sleep(2000);
await ev(`localStorage.setItem('lang', ${JSON.stringify(lang)})`);
await send('Page.navigate', { url: `http://localhost:${port}/` });
await sleep(2500);
await ev(`(() => { const i = document.querySelector('#input'); i.value = '2 bedroom apartment under $700k near a train station'; i.closest('form').requestSubmit(); })()`);
await ev(`(async () => { for (let k = 0; k < 120 && document.querySelectorAll('.pin').length < 5; k++) await new Promise(r => setTimeout(r, 1000)); await new Promise(r => setTimeout(r, 3000)); })()`);

const pins = await ev(`[...document.querySelectorAll('.pin-badge')].map(b => b.textContent.trim())`);
const last = String(Math.max(...pins.map(Number)));
const pinAt = n => ev(`(() => { const b = [...document.querySelectorAll('.pin-badge')].find(x => x.textContent.trim() === ${JSON.stringify(n)}); if (!b) return null; const r = b.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; })()`);
const cardVisible = n => ev(`(() => { const c = document.querySelector('.card[data-no="${n}"]'), col = c.closest('.col-results'); const a = c.getBoundingClientRect(), b = col.getBoundingClientRect();
  return a.top >= b.top - 1 && a.bottom <= b.bottom + 1; })()`);

await move(await centre('.card[data-no="2"]'));
check('hover card 2 lights up pin 2', await ev(`[...document.querySelectorAll('.pin.hl .pin-badge')].map(b => b.textContent.trim()).join() === '2'`));
await move([700, 30]);
check('leaving the card clears the pin', await ev(`!document.querySelector('.pin.hl')`));
await move(await pinAt('3'));
check('hover pin 3 outlines card 3', await ev(`[...document.querySelectorAll('.card.hl')].map(c => c.dataset.no).join() === '3'`));
await move([700, 30]);
check('leaving the pin clears the card', await ev(`!document.querySelector('.card.hl')`));

await ev(`document.querySelector('.col-results').scrollTop = 0`);
await sleep(300);
const scrollable = await ev(`(() => { const c = document.querySelector('.col-results'); return c.scrollHeight > c.clientHeight + 1; })()`);
const before = await cardVisible(last);
await click(await pinAt(last));
check(`clicking pin ${last} opens its detail`, await ev(`document.querySelector('#detail').classList.contains('open') && document.querySelector('.card.selected')?.dataset.no === ${JSON.stringify(last)}`));
check(`card ${last} scrolled into view`, await cardVisible(last), `list scrollable=${scrollable}, visible before=${before}`);
check(`card ${last} flashes`, await ev(`document.querySelector('.card[data-no="${last}"]').classList.contains('flash')`));

console.log(results.every(Boolean) ? `ALL PASSED (${results.length})` : `FAILED ${results.filter(x => !x).length}/${results.length}`);
ws.close(); chrome.kill();
process.exit(results.every(Boolean) ? 0 : 1);
