import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const [port = '8000', lang = 'zh'] = process.argv.slice(2);
const zh = lang === 'zh';
const prof = fs.mkdtempSync(path.join(os.tmpdir(), 'restore-'));
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
  await new Promise(r => setTimeout(r, 1200)); })()`;
const say = async text => { await ev(`(() => { const i = document.querySelector('#input'); i.value = ${JSON.stringify(text)}; i.closest('form').requestSubmit(); })()`); await ev(settle); };
const snapshot = `({
  chips: [...document.querySelectorAll('#condition-dock .chip')].map(c => c.textContent.trim()).filter(t => !/不限|any/i.test(t)),
  cards: [...document.querySelectorAll('.card')].map(c => c.dataset.id),
  pins: document.querySelectorAll('.pin').length,
  users: [...document.querySelectorAll('#chat .msg-user')].map(e => e.textContent),
  answers: document.querySelectorAll('#chat .msg-assistant').length,
  stage: document.getElementById('app').dataset.stage })`;
const reload = async () => { await send('Page.reload', {}); await sleep(3000); await ev(settle); };

await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
await send('Page.enable');
await send('Page.navigate', { url: `http://localhost:${port}/` });
await sleep(2000);
await ev(`localStorage.setItem('lang', ${JSON.stringify(lang)})`);
await send('Page.navigate', { url: `http://localhost:${port}/` });
await sleep(2500);

await say(zh ? '找 90 万以内的 3 房' : '3 bedroom under $900k');
await say(zh ? '要独立屋' : 'houses only');
const before = await ev(snapshot);
check('two turns before reload', before.users.length === 2 && before.answers === 2 && before.cards.length > 0, JSON.stringify(before.chips));

await reload();
const after = await ev(snapshot);
check('stage restored', after.stage === 'working');
check('chat turns restored', JSON.stringify(after.users) === JSON.stringify(before.users) && after.answers === 2, JSON.stringify(after.users));
check('filter chips restored', JSON.stringify(after.chips) === JSON.stringify(before.chips), JSON.stringify(after.chips));
check('same result cards restored', JSON.stringify(after.cards) === JSON.stringify(before.cards));
check('map pins restored', after.pins === before.pins && after.pins > 0, `${after.pins}`);
check('no resync search needed (server session alive)', !(await ev(`document.querySelector('#chat').textContent.includes(${JSON.stringify(zh ? '重新搜索' : 'rerun')})`)));

await say(zh ? '价格 ≤ $700,000' : 'price ≤ $700,000');
const third = await ev(snapshot);
const chat = await ev(`document.querySelector('#chat').textContent`);
check('follow-up after reload is applied, not refused', !/还不能确定这次要修改|Please specify which requirement/.test(chat.slice(-400)));
check('budget changed, other filters kept', third.chips.some(t => /700,000/.test(t)) && third.chips.some(t => /3\s*(房|bed)/.test(t)) && third.chips.some(t => /独立屋|House/i.test(t)), JSON.stringify(third.chips));

await ev(`document.querySelector('#btn-new').click()`);
await sleep(800);
await reload();
const fresh = await ev(snapshot);
check('"New chat" clears the saved conversation', fresh.stage === 'welcome' && fresh.users.length === 0, fresh.stage);

console.log(results.every(Boolean) ? `ALL PASSED (${results.length})` : `FAILED ${results.filter(x => !x).length}/${results.length}`);
ws.close(); chrome.kill();
process.exit(results.every(Boolean) ? 0 : 1);
