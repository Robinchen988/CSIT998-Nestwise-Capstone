import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const [port = '8000', lang = 'zh'] = process.argv.slice(2);
const prof = fs.mkdtempSync(path.join(os.tmpdir(), 'cmp-'));
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
const rect = sel => ev(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) return null; const r = e.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; })()`);
async function click(sel) {
  const p = await rect(sel); if (!p) return false;
  for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased'])
    await send('Input.dispatchMouseEvent', { type, x: p[0], y: p[1], button: 'left', clickCount: 1 });
  await sleep(400); return true;
}
async function drag(sel, dx, dy) {
  const p = await rect(sel);
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: p[0], y: p[1] });
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: p[0], y: p[1], button: 'left', clickCount: 1 });
  for (let k = 1; k <= 5; k++) await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: p[0] + dx * k / 5, y: p[1] + dy * k / 5, button: 'left', buttons: 1 });
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: p[0] + dx, y: p[1] + dy, button: 'left', clickCount: 1 });
  await sleep(300);
}
const results = [];
const check = (name, ok, extra = '') => { results.push(ok); console.log((ok ? 'PASS ' : 'FAIL ') + name + (extra ? '  ' + extra : '')); };

await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
await send('Page.enable');
await send('Page.navigate', { url: `http://localhost:${port}/` });
await sleep(2500);
await ev(`localStorage.setItem('lang', ${JSON.stringify(lang)})`);
await send('Page.navigate', { url: `http://localhost:${port}/` });
await sleep(2500);
console.log(await ev(`(async () => { const i=document.querySelector('#input'); i.value='3-bed house under $1.2M near a train station'; i.closest('form').requestSubmit();
  for (let k=0;k<90 && document.querySelectorAll('.card').length<3;k++) await new Promise(r=>setTimeout(r,1000));
  await new Promise(r=>setTimeout(r,2500)); return document.querySelectorAll('.card').length + ' cards'; })()`));

await click('.card');
await sleep(800);
check('detail opens from a normal card', await ev(`document.querySelector('#detail').classList.contains('open')`));
check('compare button present in detail bar', !!(await rect('#detail .d-bar .cmp-add')));

const before = await ev(`getComputedStyle(document.querySelector('#detail')).transform`);
await drag('#detail .d-bar .d-bar-title, #detail .d-bar', -40, 30);
const after = await ev(`getComputedStyle(document.querySelector('#detail')).transform`);
check('title bar still drags the panel', before !== after, `${before} -> ${after}`);

await click('#detail .d-bar .cmp-add');
check('clicking "+ compare" opens the picker', !!(await rect('.cmp-picker')));
const items = await ev(`document.querySelectorAll('.cmp-pick-item').length`);
check('picker lists other properties', items > 0, `${items} items`);
await click('.cmp-pick-item');
await sleep(800);
check('picking one opens side-by-side (2 columns)', await ev(`document.querySelector('#detail').classList.contains('compare') && document.querySelectorAll('.cmp-colhead').length === 2`));

await click('#detail .cmp-diff');
check('"differences only" toggles', await ev(`!!document.querySelector('#detail .cmp-diff.on') && !!document.querySelector('.cmp-grid.diff-only')`));
await click('#detail .cmp-diff');
check('"differences only" toggles back', await ev(`!document.querySelector('.cmp-grid.diff-only')`));

await click('#detail .cmp-bar .cmp-add');
check('"+ add another" opens the picker in compare view', !!(await rect('.cmp-picker')));
await click('.cmp-pick-item');
await sleep(800);
check('third column added', await ev(`document.querySelectorAll('.cmp-colhead').length === 3`));
check('no add button at 3 columns', !(await rect('#detail .cmp-bar .cmp-add')));

await click('.cmp-remove');
await sleep(400);
check('remove a column', await ev(`document.querySelectorAll('.cmp-colhead').length === 2`));
await click('#detail .d-close');
await sleep(400);
check('close button closes', await ev(`!document.querySelector('#detail').classList.contains('open')`));
console.log(results.every(Boolean) ? `ALL PASSED (${results.length})` : `FAILED ${results.filter(x => !x).length}/${results.length}`);
ws.close(); chrome.kill();
process.exit(results.every(Boolean) ? 0 : 1);
