import http from 'node:http';
import fs from 'node:fs';
const fixture = JSON.parse(fs.readFileSync(process.env.RELATIVE_FIXTURE, 'utf8'));
const upstream = 'http://127.0.0.1:8520';
const sessions = new Map();
function browserChecks() {
  localStorage.setItem('lang', 'zh');
  const errors = [];
  window.addEventListener('error', e => errors.push(e.error?.stack || e.message));
  window.addEventListener('unhandledrejection', e => errors.push(String(e.reason)));
  window.addEventListener('DOMContentLoaded', async () => {
    const $ = s => document.querySelector(s), all = s => [...document.querySelectorAll(s)];
    const delay = ms => new Promise(r => setTimeout(r, ms));
    const wait = async pred => { for (let i=0;i<250;i++) { if (pred()) return; await delay(30); } throw Error('Timed out'); };
    const idle = () => !$('#composer .send').disabled;
    const ids = () => all('.card').map(c => c.dataset.id).join(',');
    const filters = () => $('#condition-dock').textContent;
    const start = text => { $('#input').value = text; $('#composer').requestSubmit(); };
    const send = async text => { start(text); await wait(idle); };
    const report = { viewport: [innerWidth,innerHeight], checks: [], failures: [], errors };
    const check = (ok, name) => { report.checks.push(name); if (!ok) report.failures.push(name); };
    try {
      await send('INITIAL'); await wait(() => !!$('.card'));
      const initialIds = ids(), initialFilters = filters();
      start('STEP'); await delay(120);
      check(!idle() && ids() === initialIds && filters() === initialFilters, 'Pending chat keeps committed filters and cards together');
      await wait(idle);
      check(ids() !== initialIds, 'Successful adjustment commits different real results');
      check(!!$('.chip-remove[aria-label^="删除 安静"]') && !!$('.chip-remove[aria-label^="删除 近公园"]'), 'Original quiet and green constraints remain');
      check($('.cond-adjustments')?.textContent.includes('热闹 ↑ 小幅调整'), 'Relative target is separate from score filters');
      check(!filters().includes('热闹 ≥'), 'Relative request never appears as an invented score threshold');
      check($('#result-sort').selectedOptions[0].textContent.includes('相对上一轮'), 'Sort explains the relative objective');
      check(all('.card .ml').some(n => n.textContent.includes('热闹')) && all('.card .ml').some(n => n.textContent.includes('安静')), 'Cards show both changing and retained attributes');
      check(all('.card-id').every(n => n.getBoundingClientRect().width >= 170), 'Score columns never squeeze the address and room details');
      check(!!$('.chip[title*="推断"]'), 'Inferred threshold origin remains accessible');
      const keptIds = ids(), keptFilters = filters();
      await send('IMPOSSIBLE');
      check(ids() === keptIds && filters() === keptFilters, 'No compatible result retains the current result');
      check($('#results-notice').textContent.includes('保留上一轮'), 'No-match notice explains retained results');
      await send('CLARIFY');
      check(ids() === keptIds && filters() === keptFilters, 'Clarification does not alter filters or cards');
      check(all('.msg-assistant').at(-1).textContent.includes('昼夜'), 'Clarification explains missing day/night evidence');
      await send('ERROR');
      check(ids() === keptIds && filters() === keptFilters && !!$('.msg-error'), 'Stream failure cannot commit staged results');
      $('.relative-goal .chip-remove').click(); await wait(idle);
      check(!$('.cond-adjustments') && !!$('.chip-remove[aria-label^="删除 安静"]'), 'Relative goal can be removed independently');
      await send('STEP');
      $('#result-sort').value = 'quiet'; $('#result-sort').dispatchEvent(new Event('change')); await wait(idle);
      check(!$('.cond-adjustments') && $('#result-sort').value === 'quiet', 'Explicit sorting exits relative mode');
      check(document.documentElement.scrollWidth <= innerWidth + 1, 'Layout fits viewport');
      check(errors.length === 0, 'No browser runtime errors');
    } catch (e) { report.failures.push(e.stack || String(e)); }
    document.title = report.failures.length ? 'FAIL relative UI' : 'PASS relative UI';
    const out = document.createElement('pre'); out.id = 'relative-report'; out.textContent = JSON.stringify(report,null,2); document.body.append(out);
    await fetch('/__result',{method:'POST',body:JSON.stringify(report)});
  });
}
http.createServer(async (req,res) => {
  try {
    const url = new URL(req.url,upstream);
    if (url.pathname === '/' && url.searchParams.has('width')) {
      const width = Math.max(320,Math.min(2560,Number(url.searchParams.get('width')) || 1440));
      const height = Math.max(480,Math.min(1440,Number(url.searchParams.get('height')) || 900));
      res.setHeader('content-type','text/html; charset=utf-8');
      res.end(`<style>body{margin:0}</style><iframe title="relative ${width}x${height}" src="/" style="width:${width}px;height:${height}px;border:0"></iframe>`); return;
    }
    if (url.pathname === '/__relative.js') { res.setHeader('content-type','text/javascript'); res.end(`(${browserChecks.toString()})();`); return; }
    if (req.method === 'POST') {
      let body = ''; for await (const part of req) body += part;
      if (url.pathname === '/__result') { console.log(body); res.end('ok'); return; }
      if (!['/api/chat','/api/refine'].includes(url.pathname)) { res.statusCode = 405; res.end('No live writes'); return; }
      const request = JSON.parse(body), message = request.message;
      const previous = sessions.get(request.thread_id) || fixture.initial;
      let next = structuredClone(message === 'STEP' ? fixture.relative : message === 'INITIAL' ? fixture.initial : previous);
      let notice = '', answer = '隔离测试：重放真实数据库响应。', intent = 'refine';
      if (message === 'IMPOSSIBLE') notice = '没有找到兼顾结果，已保留上一轮条件和房源。';
      if (message === 'CLARIFY') { notice = '没有昼夜噪声数据，是否改用整体安静度？'; intent = 'clarify'; }
      if (message === 'ERROR') next = structuredClone(fixture.initial);
      if (request.params) next.params = request.params;
      res.setHeader('content-type','text/event-stream'); res.setHeader('cache-control','no-store');
      const event = (name,data) => res.write(`event: ${name}\ndata: ${JSON.stringify(data)}\n\n`);
      if (!request.params) event('params',{params:next.params,intent});
      if (intent !== 'clarify') {
        event('ranking',{ranking:next.ranking,count:next.metrics.length});
        event('more',{metrics:next.metrics}); event('results',{metrics:next.metrics});
      }
      setTimeout(() => {
        if (message === 'ERROR') { event('error',{message:'Intentional failure after staged results'}); res.end(); return; }
        sessions.set(request.thread_id,next);
        event('answer',{answer:notice || answer});
        event('done',{params:next.params,intent,ranking:next.ranking,count:next.metrics.length,batch_offset:0,notice}); res.end();
      },400); return;
    }
    const response = await fetch(url); res.statusCode = response.status;
    res.setHeader('content-type',response.headers.get('content-type') || 'application/octet-stream'); res.setHeader('cache-control','no-store');
    if (url.pathname === '/') res.end((await response.text()).replace('</head>','<script src="/__relative.js"></script></head>').replace('<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/','<script crossorigin="anonymous" src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/'));
    else res.end(Buffer.from(await response.arrayBuffer()));
  } catch (error) { res.statusCode = 500; res.end(String(error)); }
}).listen(8524,'127.0.0.1',()=>console.log('Relative UI fixture http://127.0.0.1:8524/'));
