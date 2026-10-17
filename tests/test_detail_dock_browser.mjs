import { spawn } from 'node:child_process'; import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';
const sleep = ms => new Promise(r => setTimeout(r, ms));
const port = process.argv[2] || '8000';
const results = [];
const check = (name, ok, extra = '') => { results.push(ok); console.log((ok ? 'PASS ' : 'FAIL ') + name + (extra ? '  ' + extra : '')); };
for (const [W, H] of [[1280, 800], [1440, 900], [1920, 1080]]) {
  const prof = fs.mkdtempSync(path.join(os.tmpdir(), 'lp-')); const dbg = 9600 + Math.floor(Math.random() * 300);
  const chrome = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', ['--headless=new', `--remote-debugging-port=${dbg}`, `--user-data-dir=${prof}`, '--no-first-run', `--window-size=${W},${H}`, 'about:blank'], { stdio: 'ignore' });
  let ws; for (let i = 0; i < 50; i++) { try { const t = await (await fetch(`http://127.0.0.1:${dbg}/json`)).json(); const pg = t.find(x => x.type === 'page'); if (pg) { ws = new WebSocket(pg.webSocketDebuggerUrl); break; } } catch {} await sleep(200); }
  await new Promise(r => ws.addEventListener('open', r, { once: true }));
  let id = 0; const pend = new Map(); ws.addEventListener('message', e => { const m = JSON.parse(e.data); if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); } });
  const send = (method, params = {}) => new Promise(r => { const i = ++id; pend.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
  const ev = async expr => (await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value;
  await send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: `http://localhost:${port}/` }); await sleep(2500);
  await ev(`(() => { const i = document.querySelector('#input'); i.value = '2 bedroom apartment under $700k near a train station'; i.closest('form').requestSubmit(); })()`);
  await ev(`(async () => { for (let k = 0; k < 120 && document.querySelectorAll('.pin').length < 5; k++) await new Promise(r => setTimeout(r, 1000)); await new Promise(r => setTimeout(r, 2500)); document.querySelector('.card').click(); await new Promise(r => setTimeout(r, 1200)); })()`);
  const measure = `(() => { const ov = (a, b) => Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left)) / a.width;
    const map = document.querySelector('.map-wrap').getBoundingClientRect(), res = document.querySelector('.col-results').getBoundingClientRect(), d = document.querySelector('#detail').getBoundingClientRect();
    return { detail: [Math.round(d.left), Math.round(d.width)], mapCovered: Math.round(ov(map, d) * 100) + '%', listCovered: Math.round(ov(res, d) * 100) + '%' }; })()`;
  const one = await ev(measure);
  check(`${W}x${H} single detail leaves map and list uncovered`, one.mapCovered === '0%' && one.listCovered === '0%', JSON.stringify(one));
  for (const n of [2, 3]) {
    await ev(`(async () => { document.querySelector('#detail .cmp-add').dispatchEvent(new MouseEvent('click', { bubbles: true })); await new Promise(r => setTimeout(r, 400)); document.querySelector('.cmp-pick-item')?.click(); await new Promise(r => setTimeout(r, 1200)); })()`);
    const m = await ev(measure), cols = await ev(`document.querySelectorAll('.cmp-colhead').length`);
    if (n === 2) check(`${W}x${H} 2-column compare leaves the map uncovered`, cols === 2 && m.mapCovered === '0%', JSON.stringify(m));
    else console.log(`info ${W}x${H} 3-column compare`, JSON.stringify(m));
  }
  ws.close(); chrome.kill();
}
console.log(results.every(Boolean) ? `ALL PASSED (${results.length})` : `FAILED ${results.filter(x => !x).length}/${results.length}`);
process.exit(results.every(Boolean) ? 0 : 1);
