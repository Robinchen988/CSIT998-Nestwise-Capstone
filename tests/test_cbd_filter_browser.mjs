import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const [port = '8000', lang = 'zh'] = process.argv.slice(2);
const zh = lang === 'zh';
const prof = fs.mkdtempSync(path.join(os.tmpdir(), 'cbd-'));
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

const settle = `(async () => { await new Promise(r => setTimeout(r, 1000));
  for (let k = 0; k < 150; k++) { const s = document.querySelector('#result-sort'); const busy = document.querySelector('#app')?.classList.contains('running');
    if (s && !s.disabled && !busy) break; await new Promise(r => setTimeout(r, 500)); }
  await new Promise(r => setTimeout(r, 1000)); })()`;
const say = async text => { await ev(`(() => { const i = document.querySelector('#input'); i.value = ${JSON.stringify(text)}; i.closest('form').requestSubmit(); })()`); await ev(settle); };
const chipText = `[...document.querySelectorAll('.chip')].map(c => c.textContent.trim())`;
const cbdChip = `[...document.querySelectorAll('.chip')].find(c => /CBD/.test(c.textContent))`;
const detailKms = `(async () => { const out = [];
  for (const card of document.querySelectorAll('.card')) {
    card.click(); await new Promise(r => setTimeout(r, 700));
    const t = document.querySelector('#detail')?.textContent || '';
    const m = t.match(/(?:距\\s*CBD\\s*([\\d.]+)\\s*km)|(?:([\\d.]+)\\s*km\\s*(?:to|from)\\s*(?:the\\s*)?CBD)/i);
    out.push(m ? parseFloat(m[1] || m[2]) : null);
  }
  document.querySelector('#detail .d-close')?.click(); return out; })()`;

await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
await send('Page.enable');
await send('Page.navigate', { url: `http://localhost:${port}/` });
await sleep(2000);
await ev(`localStorage.setItem('lang', ${JSON.stringify(lang)})`);
await send('Page.navigate', { url: `http://localhost:${port}/` });
await sleep(2500);

await say(zh ? '找 120 万以内的 3 房独立屋' : '3 bedroom house under $1.2M');
check('first search returns results', (await ev(`document.querySelectorAll('.card').length`)) > 0);
await say(zh ? '只保留距离 Melbourne CBD 20 公里以内的房源' : 'only keep properties within 20 km of the Melbourne CBD');
const chips = await ev(chipText);
check('follow-up adds a CBD ≤ 20 km chip', chips.some(t => /CBD\s*≤\s*20/.test(t)), JSON.stringify(chips));
check('earlier conditions are kept', chips.some(t => /1,200,000/.test(t)) && chips.some(t => /3\s*(房|bed)/.test(t)));
const chat = await ev(`document.querySelector('#chat').textContent`);
check('no "no such data" message for the CBD filter', !/没有这类数据[^。]*CBD|CBD[^.]*(no data|not filterable)/i.test(chat));
let kms = await ev(detailKms);
check('every result is within 20 km of the CBD', kms.length > 0 && kms.every(v => v != null && v <= 20), JSON.stringify(kms));

await ev(`(() => { ${cbdChip}.querySelector('.chip-main, button')?.click(); })()`);
await sleep(500);
await ev(`(() => { const inp = document.querySelector('#popover input[type=number]'); inp.value = '10'; inp.dispatchEvent(new Event('input'));
  document.querySelector('#popover').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); })()`);
await ev(settle);
const chips10 = await ev(chipText);
check('editing the chip to 10 km', chips10.some(t => /CBD\s*≤\s*10/.test(t)), JSON.stringify(chips10.filter(t => /CBD/.test(t))));
kms = await ev(detailKms);
check('every result is within 10 km after the edit', kms.length > 0 && kms.every(v => v != null && v <= 10), JSON.stringify(kms));

await ev(`(() => { ${cbdChip}.querySelector('.chip-remove')?.click(); })()`);
await ev(settle);
check('removing the chip removes the filter', !(await ev(chipText)).some(t => /CBD\s*≤/.test(t)));

await ev(`document.querySelector('#btn-new')?.click()`);
await sleep(800);
await say(zh ? '80 万以内、4 房、步行 5 分钟到 CBD' : 'under $800k, 4 bedrooms, 5 minutes walk to the CBD');
const chips3 = await ev(chipText);
const unsup = await ev(`[...document.querySelectorAll('.cond-unsupported')].map(e => e.textContent).join(' | ')`);
check('walking-time search still runs with its other filters', chips3.some(t => /800,000/.test(t)) && chips3.some(t => /4\s*(房|bed)/.test(t)), JSON.stringify(chips3));
check('walking time is not turned into a km filter', !chips3.some(t => /CBD\s*≤/.test(t)));
check('walking time is listed as unsupported', /步行|walk/i.test(unsup), unsup.slice(0, 120));

console.log(results.every(Boolean) ? `ALL PASSED (${results.length})` : `FAILED ${results.filter(x => !x).length}/${results.length}`);
ws.close(); chrome.kill();
process.exit(results.every(Boolean) ? 0 : 1);
