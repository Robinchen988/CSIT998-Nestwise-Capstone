import http from 'node:http';
import fs from 'node:fs';
import { historyChecks } from './test_result_history_browser.mjs';
const upstream = process.env.CONVERSATION_URL || 'http://127.0.0.1:8520';
const port = Number(process.env.CONVERSATION_TEST_PORT || 8523);
const sessions = new Map();
const historySessions = new Map();
const fixture = process.env.CONVERSATION_FIXTURE ? JSON.parse(fs.readFileSync(process.env.CONVERSATION_FIXTURE, 'utf8')) : null;
const initial = fixture?.initial.params || {
  max_price: 1000000, bedrooms: 3, property_type: 'house', suburb: null,
  abstract_needs: [{ attribute: 'quiet', min_score: 70 }, { attribute: 'green', min_score: 60 }],
  amenity_needs: [], planning_needs: [], unsupported_asks: [], sort_by: null,
};

function fixtureBrowser(historyChecks) {
  localStorage.setItem('lang', 'zh');
  const errors = [];
  const requests = [], nativeFetch = window.fetch.bind(window);
  let nextMode = '';
  const historyMode = new URLSearchParams(location.search).has('history');
  window.fetch = (url, options) => {
    if (url === '/api/refine' || (historyMode && options?.method === 'POST' && String(url).startsWith('/api/'))) {
      requests.push(JSON.parse(options.body));
      options = { ...options, headers: { ...options.headers, 'x-fixture-mode': nextMode, ...(historyMode ? { 'x-fixture-history': '1' } : {}) } }; nextMode = '';
    }
    return nativeFetch(url, options);
  };
  window.addEventListener('error', e => errors.push(e.error?.stack || e.message));
  window.addEventListener('unhandledrejection', e => errors.push(String(e.reason)));
  window.addEventListener('DOMContentLoaded', async () => {
    const $ = s => document.querySelector(s);
    const delay = ms => new Promise(r => setTimeout(r, ms));
    const wait = async pred => { for (let i = 0; i < 200; i++) { if (pred()) return; await delay(40); } throw new Error('UI response timed out'); };
    const send = async text => {
      $('#input').value = text; $('#composer').requestSubmit();
      await wait(() => !$('#composer .send').disabled && $('.msg-assistant:not(:empty)'));
    };
    await send('我想找 3 房的住宅，预算 100 万内，安静一点，最好附近有公园。');
    document.title = '对话栏界面测试 · 隔离响应重放';
    if (historyMode) {
      if (new URLSearchParams(location.search).get('history') !== 'preview') await historyChecks({ errors, requests, setMode: mode => { nextMode = mode; } });
      return;
    }
    if (!new URLSearchParams(location.search).has('checks')) return;
    const result = { viewport: [innerWidth, innerHeight], checks: [], failures: [], errors };
    const check = (ok, name) => { result.checks.push(name); if (!ok) result.failures.push(name); };
    const visible = el => !!el && el.getBoundingClientRect().height > 0;
    const rect = s => $(s).getBoundingClientRect();
    try {
      check($('.brand-name').textContent === '筑明' && $('.brand-ai').textContent === 'AI'
        && $('.brand-tagline').textContent === 'A BETTER HOME' && visible($('.brand-tagline')), 'New wordmark and home tagline visible');
      check(rect('.brand-tagline').top >= rect('.brand-wordmark').bottom && Math.abs(rect('.brand-tagline').left - rect('.brand-wordmark').left) <= 2, 'Wordmark and tagline form a left-aligned vertical lockup');
      check($('.msg-row-user .msg-time') && $('.msg-row-assistant .msg-time'), 'Both message roles retain timestamps');
      check($('#condition-dock .conditions') && !$('#chat .conditions'), 'Filters dock outside conversation history');
      check(!visible($('.chip.unset')) && visible($('#result-sort')), 'Unused filters collapsed while sorting stays visible');
      const quietChip = () => [...document.querySelectorAll('.chip-main')].find(b => b.textContent.includes('安静'));
      const originalScore = Number(quietChip().textContent.match(/\d+$/)[0]);
      for (const [operator, symbol] of [['gt', '>'], ['lte', '≤'], ['lt', '<'], ['eq', '='], ['gte', '≥']]) {
        quietChip().click();
        check($('#popover select').options.length === 5, 'All score operators available: ' + operator);
        $('#popover select').value = operator; $('#popover input').value = originalScore;
        $('#popover .actions .right button:last-child').click();
        await wait(() => !$('#composer .send').disabled);
        const need = requests.at(-1).params.abstract_needs.find(n => n.attribute === 'quiet');
        check(need.operator === operator && need.min_score === originalScore && quietChip().textContent.includes(symbol), 'Comparison saved in request and chip: ' + operator);
        quietChip().click();
        check($('#popover select').value === operator && Number($('#popover input').value) === originalScore, 'Comparison restored when reopened: ' + operator);
        $('#popover .actions .right button:first-child').click();
      }
      quietChip().click();
      const beforeInvalid = requests.length;
      for (const value of ['', '-1', '101', '60.5']) {
        $('#popover input').value = value;
        $('#popover .actions .right button:last-child').click();
        check($('#popover').classList.contains('is-open') && requests.length === beforeInvalid && !$('#popover input').checkValidity(), 'Invalid score stays editable without submitting: ' + value);
      }
      check($('#popover').getBoundingClientRect().left >= 0 && $('#popover').getBoundingClientRect().right <= innerWidth, 'Score editor fits viewport');
      $('#popover .actions .right button:first-child').click();
      if ($('#result-sort').value === 'quiet') {
        const ids = () => [...document.querySelectorAll('.card')].map(c => c.dataset.id).join(',');
        const oldIds = ids(), oldCount = requests.length;
        nextMode = 'delay'; $('.chip-remove[aria-label^="删除 安静"]').click();
        await wait(() => requests.length > oldCount);
        check(requests.at(-1).params.sort_by === null && requests.at(-1).removed_attributes.includes('quiet'), 'Delete quiet cancels its sort in the request');
        check(ids() === oldIds && !!$('.chip-remove[aria-label^="删除 安静"]'), 'Pending update keeps filters and cards on the same committed result');
        check($('#results-notice').textContent.includes('安静'), 'Pending removal explains what is changing');
        await wait(() => !$('#composer .send').disabled);
        check(!$('.chip-remove[aria-label^="删除 安静"]') && $('#result-sort').value === '', 'Deletion commits default relevance sorting');
        check(![...document.querySelectorAll('.card .ml')].some(el => el.textContent.includes('安静')), 'Cards no longer highlight the deleted quiet preference');
        check(!!$('.chip-remove[aria-label^="删除 教育配套"]'), 'Unrelated education preference remains');
        for (const mode of ['http-error', 'stream-error', 'truncated']) {
          const keptIds = ids(); nextMode = mode;
          $('#result-sort').value = 'price_asc'; $('#result-sort').dispatchEvent(new Event('change'));
          await wait(() => !$('#composer .send').disabled);
          check(ids() === keptIds && $('#result-sort').value === '' && !!$('.msg-error button'), mode + ' keeps old filters/cards and offers retry');
        }
        $('.msg-error:last-child button').click(); await wait(() => !$('#composer .send').disabled);
        check($('#result-sort').value === 'price_asc', 'Retry applies the intended sort');
        $('#result-sort').value = 'quiet'; $('#result-sort').dispatchEvent(new Event('change'));
        await wait(() => !$('#composer .send').disabled);
        check(!$('.chip-remove[aria-label^="删除 安静"]') && [...document.querySelectorAll('.card .ml')].some(el => el.textContent.includes('安静')), 'Explicit quiet sort works without restoring a removed threshold');
        $('#result-sort').dispatchEvent(new Event('change')); await wait(() => !$('#composer .send').disabled);
        check($('#results-notice').textContent.includes('未变化'), 'Same results are acknowledged after a fresh update');
      }
      check(!document.querySelector('button button'), 'No nested interactive buttons');
      check([...document.querySelectorAll('.chip-remove')].every(b => b.getAttribute('aria-label')), 'Every remove action has a keyboard-accessible label');
      $('#input').value = '保留我的草稿'; $('.follow-up').click();
      check($('#input').value.startsWith('保留我的草稿，'), 'Suggestions preserve unsent draft');
      $('.cond-add').click(); check(document.activeElement === $('#input'), 'Add filter focuses conversation input');
      $('#input').value = '';
      $('.cond-edit').click();
      check(visible($('.chip.unset')) && visible($('#result-sort')), 'Edit exposes unset filters without hiding sorting');
      const bed = [...document.querySelectorAll('.chip-main')].find(b => b.textContent === '3 房');
      bed.click(); const number = $('#popover input'); number.value = '4';
      $('#popover .actions .right button:last-child').click();
      await wait(() => !$('#composer .send').disabled);
      check([...document.querySelectorAll('.chip-main')].some(b => b.textContent === '4 房'), 'Editing a filter updates rendered state');
      document.querySelector('.chip-remove[aria-label="删除 4 房"]').click();
      await wait(() => !$('#composer .send').disabled);
      check(!document.querySelector('.chip-remove[aria-label="删除 4 房"]'), 'Remove is separate from edit and updates state');
      if ($('.cond-edit').getAttribute('aria-expanded') === 'true') $('.cond-edit').click();
      $('#btn-lang').click(); await wait(() => $('.cond-head').textContent.includes('Current filters'));
      await wait(() => !$('#composer .send').disabled);
      check($('#input').placeholder === 'Tell me more about your needs…', 'Working placeholder and filters translate');
      $('#btn-lang').click(); await wait(() => $('.cond-head').textContent.includes('当前筛选条件'));
      await wait(() => !$('#composer .send').disabled);
      const before = rect('#composer').top;
      for (let i = 0; i < 5; i++) await send('界面测试：' + '继续补充住房需求，检查多轮消息和长文字的显示。'.repeat(9));
      if (innerWidth >= 900) {
        check(Math.abs(before - rect('#composer').top) < 1, 'Long history cannot push input off screen');
        check($('#chat').scrollHeight > $('#chat').clientHeight && $('#chat').scrollTop > 0, 'Conversation history scrolls independently');
        check(rect('#chat').bottom <= rect('#condition-dock').top + 1, 'History never overlaps filters');
        check(rect('#composer').bottom <= innerHeight, 'Input remains in viewport');
        check(rect('.col-chat').width >= 380, 'Wider conversation column');
      }
      check(document.documentElement.scrollWidth <= innerWidth + 1, 'No horizontal page overflow');
      await send('LONG_FILTER_FIXTURE');
      check(!!document.querySelector('.chip-main[title*="新偏好"] svg'), 'Unknown preferences receive category fallback icon');
      check([...document.querySelectorAll('.chip')].every(c => c.scrollWidth <= c.clientWidth + 1), 'Long filter labels wrap inside chip');
      nextMode = 'delay'; $('#result-sort').value = 'price_asc'; $('#result-sort').dispatchEvent(new Event('change'));
      await wait(() => $('#composer .send').disabled);
      $('#btn-new').click();
      await delay(650);
      check($('#app').dataset.stage === 'welcome' && !$('#condition-dock').children.length, 'New conversation clears dock and restores homepage');
      check(!visible($('.brand-tagline')) && !visible($('#follow-ups')), 'Content-only controls stay hidden on homepage');
      await send('我想找 3 房的住宅，预算 100 万内，安静一点，最好附近有公园。');
      check(!!$('#condition-dock .conditions'), 'Filters remount after reset');
      check(errors.length === 0, 'No runtime errors');
    } catch (e) { result.failures.push(String(e)); }
    result.status = result.failures.length ? 'FAIL' : 'PASS';
    const report = document.createElement('pre'); report.id = 'conversation-test-result';
    report.style.cssText = 'position:fixed;right:8px;bottom:8px;z-index:9999;background:white;border:1px solid #ddd;padding:10px;max-width:42vw;max-height:24vh;overflow:auto;font:11px monospace';
    report.textContent = JSON.stringify(result, null, 2); document.body.append(report);
    await fetch('/__result', { method: 'POST', body: JSON.stringify(result) });
  });
}

http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, upstream);
    if (url.pathname === '/' && url.searchParams.has('width')) {
      const width = Math.max(320, Math.min(2560, Number(url.searchParams.get('width')) || 1280));
      const height = Math.max(480, Math.min(1440, Number(url.searchParams.get('height')) || 720));
      res.setHeader('content-type', 'text/html; charset=utf-8');
      const mode = url.searchParams.has('history') ? (url.searchParams.get('history') === 'preview' ? '?history=preview' : '?history=1') : url.searchParams.has('checks') ? '?checks=1' : '';
      res.end(`<title>Conversation responsive fixture</title><style>body{margin:0}</style><iframe title="${width} by ${height}" src="/${mode}" style="width:${width}px;height:${height}px;border:0"></iframe>`); return;
    }
    if (url.pathname === '/__fixture.js') { res.setHeader('content-type', 'text/javascript'); res.end(`(${fixtureBrowser.toString()})(${historyChecks.toString()});`); return; }
    if (req.method === 'POST') {
      let body = ''; for await (const chunk of req) body += chunk;
      if (url.pathname === '/__result') { console.log(body); res.end('ok'); return; }
      if (!['/api/chat', '/api/refine', '/api/rebatch'].includes(url.pathname)) { res.statusCode = 405; res.end('Fixture blocks live writes'); return; }
      const payload = JSON.parse(body);
      const params = structuredClone(payload.params || sessions.get(payload.thread_id) || initial);
      if (req.headers['x-fixture-history'] === '1' && payload.message === 'HISTORY_NEW') params.sort_by = 'quiet';
      const mode = req.headers['x-fixture-mode'];
      if (mode === 'http-error') { res.statusCode = 503; res.end('Intentional fixture failure'); return; }
      if (fixture && payload.removed_attributes?.includes('quiet')) params.semantic_query = fixture.removed.params.semantic_query;
      if (payload.message === 'LONG_FILTER_FIXTURE') {
        params.abstract_needs.push({ attribute: '新偏好'.repeat(14), min_score: 60 });
        params.near_place = { name: 'A very long place name near Melbourne University and the surrounding neighbourhood', max_distance_m: 2500 };
      }
      const sameHardFilters = ['bedrooms', 'max_price', 'property_type'].every(k => params[k] === initial[k]);
      let metrics = fixture && sameHardFilters ? structuredClone((params.sort_by === 'quiet' ? fixture.initial : fixture.removed).metrics) : [];
      if (params.sort_by === 'price_asc') metrics.sort((a, b) => a.price - b.price);
      let answer = payload.lang === 'en' ? 'UI test: replaying a saved response. No live search or LLM request is made from this page.' : '界面测试：正在重放保存的响应。此页面不会发送实时检索或模型请求，可点击条件验证更新。';
      const history = req.headers['x-fixture-history'] === '1';
      const about = history && payload.message === 'HISTORY_ABOUT';
      let offset = 0, pool = metrics, ranking = '';
      if (history) {
        if (!fixture) throw new Error('History checks require a real CONVERSATION_FIXTURE');
        if (about || url.pathname === '/api/rebatch') {
          const previous = historySessions.get(payload.thread_id);
          pool = previous.pool; offset = about ? previous.offset : payload.offset;
          metrics = pool.slice(offset, offset + 5); ranking = previous.ranking;
        } else {
          const seen = new Set(metrics.map(m => m.id));
          pool = metrics.length ? metrics.concat(structuredClone([...fixture.initial.metrics, ...fixture.removed.metrics].filter(m => !seen.has(m.id)))) : [];
          ranking = '界面测试排序：' + (params.sort_by || '默认推荐');
        }
        const no = offset + 1;
        answer += metrics.length ? (about ? ` 第 ${no} 套、前两套、后两套。` : payload.lang === 'en'
          ? `\n- Property ${no}: ${metrics[0].address}.\n- Properties ${no + 1} and ${no + 2}.\n- Properties ${no}-${no + 2}.\n- Property 99 (invalid fixture reference).`
          : `\n- 第 ${no} 套：${metrics[0].address}。\n- 第 ${no + 1}、${no + 2} 套；第 ${no}-${no + 2} 套；前两套；后两套。\n- 第 99 套（测试无效编号）。`) : ' 没有结果。';
      }
      res.setHeader('content-type', 'text/event-stream'); res.setHeader('cache-control', 'no-store');
      const event = (name, data) => res.write(`event: ${name}\ndata: ${JSON.stringify(data)}\n\n`);
      if (url.pathname === '/api/chat') event('params', { params, intent: about ? 'about_results' : 'new_search' });
      if (!about && url.pathname !== '/api/rebatch') {
        if (history) { event('ranking', { ranking, count: metrics.length }); event('more', { metrics: pool }); }
        event('results', { metrics });
      }
      event('token', { t: answer.slice(0, 10) });
      setTimeout(() => {
        if (mode === 'stream-error') { event('error', { message: 'Intentional fixture failure after partial results' }); res.end(); return; }
        if (mode === 'truncated') { res.end(); return; }
        sessions.set(payload.thread_id, params);
        if (history) historySessions.set(payload.thread_id, { metrics, pool, offset, ranking });
        event('token', { t: answer.slice(10) }); event('answer', { answer }); event('done', { params, batch_offset: offset, ...(history ? { ranking, count: metrics.length } : {}) }); res.end();
      }, mode === 'delay' ? 450 : 80);
      return;
    }
    const response = await fetch(url); res.statusCode = response.status;
    res.setHeader('content-type', response.headers.get('content-type') || 'application/octet-stream'); res.setHeader('cache-control', 'no-store');
    if (url.pathname === '/') res.end((await response.text()).replace('</head>', '<script src="/__fixture.js"></script></head>').replace('<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/', '<script crossorigin="anonymous" src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/'));
    else res.end(Buffer.from(await response.arrayBuffer()));
  } catch (error) { res.statusCode = 502; res.end(String(error)); }
}).listen(port, '127.0.0.1', () => console.log(`Conversation fixture: http://127.0.0.1:${port}/`));
