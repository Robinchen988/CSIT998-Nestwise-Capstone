(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const h = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

  const state = {
    threadId: null, meta: null, params: null, intent: null,
    metrics: [], ranking: '', count: 0, answer: '', running: false,
    selectedId: null, map: null, mapRO: null, pins: [],
    pool: [], offset: 0, moreBuf: null,
    lang: 'en', requestSerial: 0, resultNotice: '', historyView: null,
    compare: [], detailMetric: null, detailToken: null, cmpDiff: false,
    measure: null,
    measureBounds: null,
    measureOff: null,
    focusTimer: null,
  };

  let T = window.I18N.zh;

  const answerSnapshots = new WeakMap(), referenceTargets = new WeakMap();
  const resultView = () => state.historyView || state;
  const resultKey = view => JSON.stringify([view.offset, view.metrics.map(m => String(m.id))]);
  function snapshotResults() {
    const snapshot = structuredClone({ metrics: state.metrics, params: state.params,
      offset: state.offset, ranking: state.ranking, count: state.count });
    snapshot.key = resultKey(snapshot); snapshot.createdAt = new Date();
    return snapshot;
  }
  function showResultSnapshot(snapshot) {
    if (state.running) return;
    const next = snapshot && snapshot.key !== resultKey(state) ? snapshot : null;
    if (state.historyView === next) return;
    closeSheet(); state.selectedId = null; state.historyView = next;
    renderResults();
  }
  function returnToLatest() { showResultSnapshot(null); }

  const money = v => (v == null || isNaN(v)) ? '—' : '$' + Math.round(v).toLocaleString('en-AU');
  const pct = (v, digits = 1) => (v == null || isNaN(v)) ? '—' : (v * 100).toFixed(digits) + '%';
  const dist = m => (m == null || isNaN(m)) ? '—' : (Math.round(m) >= 1000 ? (m / 1000).toFixed(1) + ' km' : Math.round(m) + ' m');
  const ptZh = t => (state.meta && state.meta.property_types[t]) || t || '';
  const UNIT_ADDR = /^\s*[A-Za-z]?\d+[A-Za-z]?\s*\//;
  const isUnitAddr = m => UNIT_ADDR.test(m && m.address || '');
  const typeZh = m => (m.property_type === 'house' && isUnitAddr(m)) ? T.semiDetached : ptZh(m.property_type);
  const attrZh = a => (state.meta && state.meta.attributes[a]) || a;
  const kindZh = k => (state.meta && state.meta.kinds[k]) || k;
  const DIAMOND = '<svg width="8" height="8" viewBox="0 0 8 8"><path d="M4 0l4 4-4 4-4-4z" fill="currentColor"></path></svg>';
  const DOT = '<svg width="7" height="7" viewBox="0 0 8 8"><circle cx="4" cy="4" r="3.5" fill="currentColor"></circle></svg>';
  const X = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M6 6l12 12"></path><path d="M18 6L6 18"></path></svg>';
  const PENCIL = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"></path><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"></path></svg>';
  const CATEGORY_PATHS = {
    budget: '<rect x="3" y="5" width="18" height="15" rx="3"/><path d="M3 9h18m-6 5h3"/>',
    rooms: '<path d="M3 18V8m18 10V8M3 15h18M3 11h18v4H3zm3 0V7h5v4m2 0V7h5v4"/>',
    home: '<path d="m3 10 9-7 9 7M5 9v12h14V9m-9 12v-8h4v8"/>',
    location: '<path d="M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 1 1 14 0Z"/><circle cx="12" cy="10" r="2.5"/>',
    tag: '<path d="M3 4h8l10 10-7 7L3 10Z"/><circle cx="7.5" cy="8" r="1"/>',
    person: '<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2Z"/>',
  };
  const categoryIcon = name => '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (CATEGORY_PATHS[name] || CATEGORY_PATHS.tag) + '</svg>';

  const twoColumn = () => matchMedia('(min-width: 900px)').matches;
  const detailIsSheet = () => !matchMedia('(min-width: 1280px)').matches;

  function newThread() {
    state.threadId = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random()).replace(/-/g, '');
    try { sessionStorage.setItem('threadId', state.threadId); sessionStorage.removeItem(SESSION_KEY); } catch (_) {  }
  }

  const SESSION_KEY = 'nw.session';
  const MAX_SAVED_TURNS = 20;
  function saveSession() {
    const turns = []; let user = null;
    for (const row of document.querySelectorAll('#chat .msg-row')) {
      const time = row.querySelector('time')?.dateTime;
      if (row.classList.contains('msg-row-user')) { user = { text: row.querySelector('.msg-user')?.textContent || '', time }; continue; }
      const el = row.querySelector('.msg-assistant');
      if (!el || el.dataset.raw == null) continue;
      const snap = answerSnapshots.get(el);
      turns.push({ user: user?.text ?? null, userTime: user?.time, answer: el.dataset.raw, time,
        snap: snap ? { metrics: snap.metrics, params: snap.params, offset: snap.offset, ranking: snap.ranking, count: snap.count } : null });
      user = null;
    }
    if (!turns.length) return;
    const current = { params: state.params, intent: state.intent, metrics: state.metrics, pool: state.pool,
      offset: state.offset, ranking: state.ranking, count: state.count, answer: state.answer };
    for (const keep of [MAX_SAVED_TURNS, 5, 1]) {
      try { sessionStorage.setItem(SESSION_KEY, JSON.stringify({ v: 1, threadId: state.threadId, turns: turns.slice(-keep), current })); return; }
      catch (err) { if (keep === 1) console.warn('session save skipped', err); }
    }
  }
  function savedSession() {
    try {
      const data = JSON.parse(sessionStorage.getItem(SESSION_KEY) || 'null');
      return data && data.v === 1 && data.threadId && data.turns?.length && data.current?.params ? data : null;
    } catch (_) { return null; }
  }
  function setMsgTime(body, iso) {
    const t = iso && body.closest('.msg-main')?.querySelector('time'); if (!t) return;
    t.dateTime = iso; t.textContent = new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
  }
  async function restoreSession(data) {
    let server = null;
    try { server = await fetch('/api/session/' + encodeURIComponent(data.threadId)).then(r => r.ok ? r.json() : null); } catch (_) {  }
    await metaReady;
    if (state.threadId !== data.threadId || state.running) return;
    setStage('working');
    for (const turn of data.turns) {
      if (turn.user != null) setMsgTime(addUser(turn.user), turn.userTime);
      if (turn.snap) Object.assign(state, turn.snap);
      const a = addAssistantMsg(); setMsgTime(a, turn.time); renderAnswer(a, turn.answer);
    }
    Object.assign(state, data.current, { moreBuf: null, selectedId: null, historyView: null, resultNotice: '' });
    renderConditions(); renderResults(); scrollChat(); saveSession();
    const ids = (data.current.metrics || []).map(m => m.id);
    const inSync = server && server.active && JSON.stringify(server.ids) === JSON.stringify(ids);
    if (!inSync) refine(server && server.active ? server.params : data.current.params, T.statusRestore);
  }
  function setStage(s) {
    $('#app').dataset.stage = s;
    $('#brand-home').disabled = s === 'welcome';
    $('#input').placeholder = s === 'working' ? T.workingPlaceholder : T.inputPlaceholder;
    $('#input').setAttribute('aria-label', $('#input').placeholder);
    if (s === 'welcome') renderDrift(); else stopDrift();
  }

  function resetAll() {
    clearTimeout(state.focusTimer); state.focusTimer = null;
    state.requestSerial++;
    state.resultNotice = ''; setRunning(false);
    newThread();
    Object.assign(state, { params: null, intent: null, metrics: [], ranking: '', count: 0,
                           answer: '', selectedId: null, pool: [], offset: 0, moreBuf: null, historyView: null });
    $('#chat').innerHTML = '';
    $('#condition-dock').replaceChildren(); condCard = null; editingConditions = false;
    $('#input').value = '';
    $('#results').innerHTML = '';
    closeSheet();
    $('#detail').innerHTML = '';
    setDetailPos(0, 0);
    if (state.map) { state.map.remove(); state.map = null; }
    if (state.mapRO) { state.mapRO.disconnect(); state.mapRO = null; }
    setStage('welcome');
  }

  async function streamPost(url, body, on) {
    const request = ++state.requestSerial, thread = state.threadId;
    const current = () => request === state.requestSerial && thread === state.threadId;
    let finished = false;
    const fail = payload => { if (current() && !finished) { finished = true; on.error?.(payload); } };
    try {
      const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      if (!res.ok || !res.body) { fail({ message: await res.text() }); return; }
      const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = '';
      for (;;) {
        const { value, done } = await reader.read();
        if (!current()) { await reader.cancel(); return; }
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const raw = buf.slice(0, idx); buf = buf.slice(idx + 2);
          let ev = 'message', data = '';
          for (const line of raw.split('\n')) {
            if (line.startsWith('event:')) ev = line.slice(6).trim();
            else if (line.startsWith('data:')) data += line.slice(5).trim();
          }
          let payload = {}; try { payload = JSON.parse(data || '{}'); } catch (_) {  }
          if (finished) continue;
          if (ev === 'error') { fail(payload); continue; }
          if (ev === 'done') finished = true;
          on[ev] && on[ev](payload);
        }
      }
      if (!finished) fail({ message: T.incompleteStream });
    } catch (error) { fail({ message: error.message || String(error) }); }
  }

  function addMessage(kind, text) {
    const row = h('article', 'msg-row msg-row-' + kind);
    const avatar = h('div', 'msg-avatar'); avatar.setAttribute('aria-hidden', 'true');
    if (kind === 'user') avatar.innerHTML = categoryIcon('person'); else avatar.textContent = 'AI';
    const main = h('div', 'msg-main'), body = h('div', 'msg-' + kind, text);
    const now = new Date(), time = h('time', 'msg-time', now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false }));
    time.dateTime = now.toISOString(); main.append(body, time); row.append(avatar, main);
    $('#chat').appendChild(row); return body;
  }
  function addUser(text) { const e = addMessage('user', text); scrollChat(); return e; }
  function addStatus() { const e = h('div', 'msg-status'); e.innerHTML = '<span class="dot"></span><span class="t"></span>'; e.querySelector('.t').textContent = T.statusInit; $('#chat').appendChild(e); scrollChat(); return e; }
  function scrollChat() { const c = $('#chat'); if (twoColumn()) c.scrollTop = c.scrollHeight; else c.lastElementChild?.scrollIntoView({ block: 'nearest' }); }

  let condCard = null;
  let editingConditions = false;
  function ensureCondCard() {
    if (!condCard) condCard = h('div', 'conditions');
    $('#condition-dock').appendChild(condCard);
    return condCard;
  }

  function runHandlers(statusEl, assistantEl) {
    let tokens = '', finalAnswer = '';
    const staged = {};
    return {
      node: ({ node }) => { const t = statusEl.querySelector('.t'); if (t) t.textContent = T.nodeStatus(T.nodes[node] || node); },
      params: ({ intent, params }) => {
        staged.intent = intent; staged.params = params;
      },
      ranking: ({ ranking, count }) => { staged.ranking = ranking || ''; staged.count = count || 0; },
      more: ({ metrics }) => { staged.pool = metrics || []; },
      results: ({ metrics }) => {
        staged.metrics = metrics || [];
      },
      token: ({ t }) => { hideSkeleton(); tokens += t; assistantEl.classList.add('streaming'); assistantEl.textContent = tokens; scrollChat(); },
      answer: ({ answer }) => { finalAnswer = answer || tokens; },
      done: ({ params, ranking, count, intent, notice, batch_offset }) => {
        params ??= staged.params; ranking ??= staged.ranking; count ??= staged.count; intent ??= staged.intent;
        if (params) state.params = params; if (ranking != null) state.ranking = ranking; if (count != null) state.count = count; if (intent) state.intent = intent;
        if (staged.metrics) {
          state.metrics = staged.metrics; state.pool = staged.pool || staged.metrics;
          state.offset = batch_offset || 0; state.moreBuf = null; state.selectedId = null; closeSheet();
        }
        state.resultNotice = notice || '';
        hideSkeleton(); statusEl.remove(); setRunning(false); renderConditions();
        if (staged.metrics) renderResults(); else renderResultsHead();
        state.answer = finalAnswer || tokens; assistantEl.classList.remove('streaming');
        renderAnswer(assistantEl, state.answer);
        if (state.intent === 'new_search' || state.intent === 'refine') ensureCondCard();
        scrollChat();
      },
      error: ({ message }) => { hideSkeleton(); statusEl.remove(); setRunning(false); const e = h('div', 'msg-error', T.errPrefix + message); $('#chat').appendChild(e); },
    };
  }

  function answerHost() {
    setStage('working');
    renderResultsHead();
    const a = addAssistantMsg();
    showSkeleton();
    scrollChat();
    return a;
  }

  function addAssistantMsg() {
    const a = addMessage('assistant');
    if (!twoColumn()) {
      const more = h('button', 'msg-more', T.expand); more.type = 'button';
      const toggle = () => {
        const on = a.classList.toggle('clamped');
        more.textContent = on ? T.expand : T.collapse;
      };
      a.classList.add('clamped');
      a.addEventListener('click', e => { if (!e.target.closest('button, a')) toggle(); });
      more.addEventListener('click', toggle);
      a.after(more);
    }
    return a;
  }

  function cleanLine(s) {
    return s.replace(/\*\*/g, '').replace(/^#{1,6}\s*/, '')
            .replace(/^\s*(结论|总结|建议)\s*[:：]\s*/, '')
            .trim();
  }

  function renderAnswer(el, text) {
    el.dataset.raw = String(text || '');
    queueMicrotask(saveSession);
    let snapshot = answerSnapshots.get(el);
    if (!snapshot) {
      snapshot = snapshotResults(); answerSnapshots.set(el, snapshot);
      if (snapshot.metrics.length) {
        const link = h('button', 'msg-results'); link.type = 'button';
        referenceTargets.set(link, { snapshot });
        link.addEventListener('click', () => {
          if (state.running) return;
          showResultSnapshot(snapshot);
          $('#results').scrollIntoView({ block: 'start' });
          const col = $('#results').closest('.col-results'); if (col) scrollTo(col, 0);
        });
        el.closest('.msg-main').appendChild(link);
      }
    }
    const lines = String(text || '').split('\n').map(s => cleanLine(s)).filter(Boolean);
    const bullets = lines.filter(l => /^[-•·]\s+/.test(l));
    if (!bullets.length) { el.textContent = ''; el.appendChild(withRefs(lines.join('\n'), snapshot)); refreshRefs(); return; }
    const lead = lines.find(l => !/^[-•·]\s+/.test(l)) || '';
    const rest = lines.filter(l => !/^[-•·]\s+/.test(l) && l !== lead);
    el.textContent = '';
    if (lead) { const d = h('div', 'msg-lead'); d.appendChild(withRefs(lead, snapshot)); el.appendChild(d); }
    const ul = h('div', 'msg-bullets');
    for (const b of bullets) {
      const d = h('div', 'msg-bullet'); d.appendChild(withRefs(b.replace(/^[-•·]\s+/, ''), snapshot)); ul.appendChild(d);
    }
    el.appendChild(ul);
    for (const r of rest) { const d = h('div', 'msg-extra'); d.appendChild(withRefs(r, snapshot)); el.appendChild(d); }
    refreshRefs();
  }

  const CN_NUM = { 一: 1, 两: 2, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10 };
  const NUM_SRC = '(?:\\d{1,2}|[一两二三四五六七八九十]{1,3})';
  const SEP_LIST = '[、,，和及与]';
  const SEP_RANGE = '(?:[-–—~～]|至|到)';
  const REF_RE = new RegExp(
    '第\\s*' + NUM_SRC + '(?:\\s*(?:' + SEP_LIST + '|' + SEP_RANGE + ')\\s*(?:第\\s*)?' + NUM_SRC + ')*\\s*套'
    + '|[前后]\\s*' + NUM_SRC + '\\s*套', 'g');
  const NUM_RE = new RegExp(NUM_SRC, 'g');
  const EN_N = '\\d{1,2}';
  const EN_REF_RE = new RegExp('\\bPropert(?:y|ies)\\s+' + EN_N
    + '(?:\\s*(?:,|and|&|-|–|—|to)\\s*(?:and\\s+)?' + EN_N + ')*', 'gi');
  const EN_NUM_RE = new RegExp(EN_N, 'g');
  const EN_RANGE_RE = new RegExp('^Propert(?:y|ies)\\s+(' + EN_N + ')\\s*(?:-|–|—|to)\\s*(' + EN_N + ')$', 'i');
  const REFS = {
    zh: () => ({ phrase: REF_RE, num: NUM_RE, range: RANGE_RE, headtail: HEADTAIL_RE }),
    en: () => ({ phrase: EN_REF_RE, num: EN_NUM_RE, range: EN_RANGE_RE, headtail: null }),
  };
  const refSet = () => (REFS[state.lang] || REFS.zh)();
  const RANGE_RE = new RegExp('^第\\s*(' + NUM_SRC + ')\\s*' + SEP_RANGE + '\\s*(?:第\\s*)?(' + NUM_SRC + ')\\s*套$');
  const HEADTAIL_RE = new RegExp('^([前后])\\s*(' + NUM_SRC + ')\\s*套$');
  function toNum(t) {
    if (/^\d+$/.test(t)) return parseInt(t, 10);
    if (CN_NUM[t]) return CN_NUM[t];
    let m = t.match(/^十([一两二三四五六七八九])$/);
    if (m) return 10 + CN_NUM[m[1]];
    m = t.match(/^([一两二三四五六七八九])十([一两二三四五六七八九])?$/);
    if (m) return CN_NUM[m[1]] * 10 + (m[2] ? CN_NUM[m[2]] : 0);
    return undefined;
  }
  const seq = (a, b) => Array.from({ length: b - a + 1 }, (_, i) => a + i);

  function withRefs(text, snapshot) {
    const frag = document.createDocumentFragment();
    const s = String(text || '');
    let last = 0, m;
    const RE = refSet().phrase;
    RE.lastIndex = 0;
    while ((m = RE.exec(s))) {
      if (m.index > last) frag.appendChild(document.createTextNode(s.slice(last, m.index)));
      frag.appendChild(refPhrase(m[0], snapshot));
      last = m.index + m[0].length;
    }
    if (last < s.length) frag.appendChild(document.createTextNode(s.slice(last)));
    return frag;
  }

  function refPhrase(phrase, snapshot) {
    const R = refSet();
    let m = R.headtail && phrase.match(R.headtail);
    if (m) {
      const k = toNum(m[2]);
      const lo = snapshot.offset + 1, hi = snapshot.offset + snapshot.metrics.length;
      if (k && hi >= lo) {
        const nums = m[1] === '前' ? seq(lo, Math.min(lo + k - 1, hi))
                                   : seq(Math.max(hi - k + 1, lo), hi);
        return groupRef(phrase, nums, snapshot);
      }
    }
    m = phrase.match(R.range);
    if (m) {
      const a = toNum(m[1]), b = toNum(m[2]);
      if (a && b && b >= a && b - a <= 20) return groupRef(phrase, seq(a, b), snapshot);
    }
    const wrap = h('span', 'ref-group');
    let last = 0, x;
    const NRE = R.num;
    NRE.lastIndex = 0;
    while ((x = NRE.exec(phrase))) {
      const n = toNum(x[0]);
      if (x.index > last) wrap.appendChild(document.createTextNode(phrase.slice(last, x.index)));
      wrap.appendChild(n ? refBtn(x[0], [n], T.jumpTo(n), snapshot)
                         : document.createTextNode(x[0]));
      last = x.index + x[0].length;
    }
    if (last < phrase.length) wrap.appendChild(document.createTextNode(phrase.slice(last)));
    return wrap;
  }

  function groupRef(phrase, nums, snapshot) {
    const wrap = h('span', 'ref-group');
    wrap.appendChild(refBtn(phrase, nums, T.jumpRange(nums[0], nums[nums.length - 1]), snapshot));
    return wrap;
  }

  function refBtn(label, nums, title, snapshot) {
    const b = h('button', 'ref', label); b.type = 'button'; b.title = title;
    const targets = nums.map(no => ({ no, id: snapshot.metrics[no - snapshot.offset - 1]?.id }));
    referenceTargets.set(b, { snapshot, targets, title });
    b.dataset.no = nums.join(',');
    b.dataset.propertyIds = targets.map(t => t.id ?? '').join(',');
    b.addEventListener('click', e => {
      e.stopPropagation();
      if (state.running || targets.some(t => t.id == null)) return;
      showResultSnapshot(snapshot);
      focusCards(targets.filter(t => document.querySelector('.card[data-no="' + t.no + '"]')?.dataset.id === String(t.id)).map(t => t.no));
    });
    return b;
  }

  const cardExists = n => !!document.querySelector('.card[data-no="' + n + '"]');

  function scrollTo(el, top, ms = 320) {
    const from = el.scrollTop;
    const max = el.scrollHeight - el.clientHeight;
    const to = Math.min(Math.max(top, 0), Math.max(max, 0));
    if (Math.abs(to - from) < 2) return;
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) { el.scrollTop = to; return; }

    let done = false;
    const t0 = performance.now();
    const ease = p => 1 - Math.pow(1 - p, 3);
    const step = now => {
      if (done) return;
      const p = Math.min((now - t0) / ms, 1);
      el.scrollTop = from + (to - from) * ease(p);
      if (p < 1) requestAnimationFrame(step); else done = true;
    };
    requestAnimationFrame(step);
    setTimeout(() => { if (!done) { done = true; el.scrollTop = to; } }, ms + 60);
  }

  function focusCards(nums) {
    const found = nums.filter(cardExists);
    if (!found.length) return;
    focusCard(found[0]);
    for (const n of found.slice(1)) {
      const c = document.querySelector('.card[data-no="' + n + '"]');
      c.classList.remove('flash'); void c.offsetWidth; c.classList.add('flash');
    }
  }

  function focusCard(no) {
    const card = document.querySelector('.card[data-no="' + no + '"]');
    if (!card) return;
    const col = card.closest('.col-results');
    if (col && col.scrollHeight > col.clientHeight + 1) {
      const cr = card.getBoundingClientRect(), colr = col.getBoundingClientRect();
      scrollTo(col, col.scrollTop + (cr.top - colr.top) - (col.clientHeight - cr.height) / 2);
    } else {
      card.scrollIntoView({ block: 'center' });
    }
    card.classList.remove('flash'); void card.offsetWidth; card.classList.add('flash');
  }

  function showSkeleton() {
    if ($('#skeleton')) return;
    const s = h('div', 'skeleton'); s.id = 'skeleton';
    for (let i = 0; i < 3; i++) s.appendChild(h('i'));
    $('#results').appendChild(s);
  }
  function hideSkeleton() { const s = $('#skeleton'); if (s) s.remove(); }

  async function send(text) {
    if (state.running) return;
    returnToLatest();
    setRunning(true);
    state.resultNotice = '';
    await metaReady;
    addUser(text);
    const status = addStatus();
    const answerEl = answerHost();
    await streamPost('/api/chat', { thread_id: state.threadId, message: text, lang: state.lang }, runHandlers(status, answerEl));
  }

  async function refine(params, statusText) {
    if (state.running) return;
    returnToLatest();
    const thread = state.threadId;
    setRunning(true);
    await metaReady;
    if (thread !== state.threadId) return;
    const active = new Set((params.abstract_needs || []).map(n => n.attribute));
    const removed = (state.params?.abstract_needs || []).map(n => n.attribute).filter(a => !active.has(a));
    params = JSON.parse(JSON.stringify(params));
    if (removed.includes(params.sort_by)) params.sort_by = null;
    const previousIds = state.metrics.map(m => m.id);
    state.resultNotice = statusText || T.statusRefine;
    renderResultsHead();
    const status = addStatus();
    status.querySelector('.t').textContent = statusText || T.statusRefine;
    const answerEl = answerHost();
    $('#results').setAttribute('aria-busy', 'true');
    const staged = { metrics: [], pool: [], tokens: '', answer: '', ranking: '' };
    await streamPost('/api/refine', { thread_id: state.threadId, params, removed_attributes: removed, lang: state.lang }, {
      node: ({ node }) => { status.querySelector('.t').textContent = T.nodeStatus(T.nodes[node] || node); },
      ranking: ({ ranking }) => { staged.ranking = ranking || ''; },
      more: ({ metrics }) => { staged.pool = metrics || []; },
      results: ({ metrics }) => { staged.metrics = metrics || []; },
      token: ({ t }) => { staged.tokens += t; },
      answer: ({ answer }) => { staged.answer = answer || ''; },
      done: data => {
        const same = previousIds.length === staged.metrics.length && previousIds.every((id, i) => id === staged.metrics[i].id);
        Object.assign(state, { params: data.params || params, intent: data.intent || 'refine',
          ranking: data.ranking ?? staged.ranking, count: data.count ?? staged.metrics.length,
          metrics: staged.metrics, pool: staged.pool.length ? staged.pool : staged.metrics,
          moreBuf: null, offset: data.batch_offset || 0, selectedId: null, answer: staged.answer || staged.tokens,
          resultNotice: data.notice || (same ? T.resultsUnchanged : T.resultsUpdated) });
        hideSkeleton(); status.remove(); setRunning(false); closeSheet();
        $('#results').removeAttribute('aria-busy');
        renderConditions(); renderResults(); renderAnswer(answerEl, state.answer); scrollChat();
      },
      error: ({ message, rollback_failed }) => {
        hideSkeleton(); status.remove(); answerEl.closest('.msg-row').remove(); setRunning(false);
        $('#results').removeAttribute('aria-busy');
        state.resultNotice = rollback_failed ? T.refineRecoveryFailed : T.refineFailed;
        renderConditions(); renderResultsHead();
        const e = h('div', 'msg-error'); e.appendChild(h('span', null, state.resultNotice));
        const retry = h('button', 'btn small', T.retry); retry.type = 'button';
        retry.addEventListener('click', () => { if (state.running) return; e.remove(); refine(params, statusText); });
        e.appendChild(retry); e.title = message || ''; $('#chat').appendChild(e); scrollChat();
      },
    });
  }

  function setRunning(v) {
    state.running = v; $('#composer .send').disabled = v;
    document.querySelectorAll('#condition-dock .chip button').forEach(el => { el.disabled = v; });
    const sort = $('#result-sort'); if (sort) sort.disabled = v || !!state.historyView;
    document.querySelectorAll('.more-batch button').forEach(el => { el.disabled = v; });
    refreshRefs();
  }

  function chip(label, opts = {}) {
    const c = h('div', 'chip' + (opts.cls ? ' ' + opts.cls : ''));
    const main = h(opts.onClick ? 'button' : 'span', 'chip-main');
    const icon = h('span', 'chip-icon'); icon.innerHTML = categoryIcon(opts.icon);
    main.append(icon, h('span', 'chip-text', label)); main.title = label;
    if (opts.onClick) { main.type = 'button'; main.disabled = state.running; main.addEventListener('click', opts.onClick); }
    c.appendChild(main);
    if (opts.onRemove) {
      const x = h('button', 'chip-remove'); x.type = 'button'; x.innerHTML = X;
      x.setAttribute('aria-label', T.remove + ' ' + label); x.title = T.remove; x.disabled = state.running;
      x.addEventListener('click', opts.onRemove); c.appendChild(x);
    }
    return c;
  }

  function renderConditions() {
    const p = state.params; if (!p) return;
    const card = ensureCondCard(); card.innerHTML = '';
    const meta = state.meta;
    const apply = (mut, statusText) => { if (state.running) return; const next = JSON.parse(JSON.stringify(state.params)); mut(next); refine(next, statusText); };

    card.classList.toggle('is-editing', editingConditions);
    const head = h('div', 'cond-head'); head.appendChild(h('strong', null, T.condHead));
    const edit = h('button', 'cond-edit', editingConditions ? T.closeConditions : T.editConditions); edit.type = 'button';
    edit.setAttribute('aria-expanded', String(editingConditions)); edit.setAttribute('aria-controls', 'condition-options');
    edit.addEventListener('click', () => { editingConditions = !editingConditions; renderConditions(); card.querySelector('.cond-edit').focus({ preventScroll: true }); });
    head.appendChild(edit); card.appendChild(head);
    const legend = h('div', 'cond-legend');
    legend.append(h('span', 'legend-required', T.requiredShort), h('span', 'legend-preferred', T.preferredShort));
    card.appendChild(legend);
    const options = h('div', 'condition-options'); options.id = 'condition-options'; card.appendChild(options);

    const g1 = h('div', 'cond-group cond-required'); g1.title = T.mustLabel;
    g1.appendChild(h('div', 'cond-label', T.requiredShort));
    const c1 = h('div', 'chips');
    const numChip = (key, label, fmt, unitHint) => {
      const v = p[key];
      const icon = key.includes('price') ? 'budget' : 'rooms';
      if (v == null) c1.appendChild(chip(T.unset(label), { icon, cls: 'unset', onClick: () => editNumber(label, v, unitHint, val => apply(n => { n[key] = val; })) }));
      else c1.appendChild(chip(fmt(v), { icon, cls: 'hard', onClick: () => editNumber(label, v, unitHint, val => apply(n => { n[key] = val; })), onRemove: () => apply(n => { n[key] = null; }) }));
    };
    numChip('max_price', T.budgetMax, v => '≤ ' + money(v), T.aud);
    if (p.min_price != null) numChip('min_price', T.budgetMin, v => '≥ ' + money(v), T.aud);
    numChip('bedrooms', T.bedroomsLabel, v => T.beds(v), T.roomsUnit);
    if (p.max_distance_cbd_km != null) c1.appendChild(chip(T.cbdChip(p.max_distance_cbd_km), { icon: 'location', cls: 'hard',
      onClick: () => editNumber(T.cbdLabel, p.max_distance_cbd_km, 'km', val => apply(n => { n.max_distance_cbd_km = val; })),
      onRemove: () => apply(n => { n.max_distance_cbd_km = null; }) }));
    if (p.bathrooms != null) numChip('bathrooms', T.bathroomsLabel, v => T.baths(v), T.roomsUnit);
    if (p.property_type) c1.appendChild(chip(ptZh(p.property_type), { icon: 'home', cls: 'hard', onClick: () => editSelect(T.typeLabel, p.property_type, meta.property_types, val => apply(n => { n.property_type = val; })), onRemove: () => apply(n => { n.property_type = null; }) }));
    else c1.appendChild(chip(T.typeAny, { icon: 'home', cls: 'unset', onClick: () => editSelect(T.typeLabel, '', meta.property_types, val => apply(n => { n.property_type = val || null; })) }));
    if (p.suburb) c1.appendChild(chip(p.suburb, { icon: 'location', cls: 'hard', onClick: () => editText(T.suburbLabel, p.suburb, val => apply(n => { n.suburb = val || null; })), onRemove: () => apply(n => { n.suburb = null; }) }));
    else c1.appendChild(chip(T.suburbAny, { icon: 'location', cls: 'unset', onClick: () => editText(T.suburbLabel, '', val => apply(n => { n.suburb = val || null; })) }));
    g1.classList.toggle('no-active', !c1.querySelector('.hard'));
    g1.appendChild(c1); options.appendChild(g1);

    const g2 = h('div', 'cond-group cond-preferred'); g2.title = T.wishLabel;
    g2.appendChild(h('div', 'cond-label', T.preferredShort));
    const c2 = h('div', 'chips'); let soft = 0;
    (p.abstract_needs || []).forEach((need, i) => {
      const required = need.strength === 'required'; if (!required) soft++;
      const score = chip(T.scoreChip(attrZh(need.attribute), need.min_score, scoreSymbol(need.operator)), {
        cls: required ? 'hard' : '',
        onClick: () => editScore(need, (op, score) => apply(n => {
          Object.assign(n.abstract_needs[i], { operator: op, min_score: score, value_source: 'explicit' });
          n.relative_preferences = (n.relative_preferences || []).filter(g => g.field !== need.attribute);
        })),
        onRemove: () => apply(n => { n.abstract_needs.splice(i, 1); }, T.removingPreference(attrZh(need.attribute))) });
      score.title = need.value_source === 'inferred' ? T.inferredScore : T.relativeScoreOrigin;
      (required ? c1 : c2).appendChild(score);
    });
    g1.classList.toggle('no-active', !c1.querySelector('.hard'));
    (p.amenity_needs || []).forEach((need, i) => { const req = need.strength === 'required'; if (!req) soft++; (req ? c1 : c2).appendChild(chip(T.distChip(kindZh(need.kind), dist(need.max_distance_m)), { icon: 'location', cls: req ? 'hard' : '',
      onClick: () => editNumber(T.distTitle(kindZh(need.kind)), need.max_distance_m, T.metres, val => apply(n => { n.amenity_needs[i].max_distance_m = val; })),
      onRemove: () => apply(n => { n.amenity_needs.splice(i, 1); }) })); });
    if (p.near_place) { soft++; const np = p.near_place; c2.appendChild(chip(T.nearPlaceChip(np.name, np.max_distance_m ? dist(np.max_distance_m) : ''), { icon: 'location',
      onClick: () => editNumber(T.nearPlaceTitle(np.name), np.max_distance_m, T.metres, val => apply(n => { n.near_place.max_distance_m = val || null; })),
      onRemove: () => apply(n => { n.near_place = null; if (n.sort_by === 'near_place_distance') n.sort_by = null; }) })); }
    if (p.school_zone) { soft++; c2.appendChild(chip(T.schoolZoneChip(p.school_zone.school), { icon: 'location', onRemove: () => apply(n => { n.school_zone = null; }) })); }
    (p.planning_needs || []).forEach((need, i) => { c1.appendChild(chip(meta.planning_needs[need] || need, { cls: 'hard', onRemove: () => apply(n => { n.planning_needs.splice(i, 1); }) })); });
    g1.classList.toggle('no-active', !c1.querySelector('.hard'));
    if (p.min_gross_yield != null) { soft++; c2.appendChild(chip(T.yieldChip(pct(p.min_gross_yield)), { icon: 'budget',
      onClick: () => editNumber(T.yieldTitle, p.min_gross_yield * 100, '%', val => apply(n => { n.min_gross_yield = val / 100; })),
      onRemove: () => apply(n => { n.min_gross_yield = null; }) })); }
    if (!soft) c2.appendChild(h('div', 'cond-unsupported', T.noSoft));
    g2.classList.toggle('no-active', !soft);
    g2.appendChild(c2); options.appendChild(g2);
    if (p.relative_preferences?.length) {
      const adjustments = h('div', 'cond-adjustments'); adjustments.appendChild(h('span', 'cond-adjustments-label', T.adjustmentLabel));
      const items = h('div', 'chips');
      p.relative_preferences.forEach((goal, i) => {
        const label = meta.attributes[goal.field] || (goal.field === 'price' ? T.relativePrice : meta.sort_labels[goal.field]) || goal.field;
        const item = chip(T.relativeGoal(label, goal.direction, goal.degree), { cls: 'relative-goal',
          onRemove: () => apply(n => { n.relative_preferences.splice(i, 1); }) });
        item.title = T.relativeGoalHint; items.appendChild(item);
      });
      adjustments.appendChild(items); options.appendChild(adjustments);
    }
    if (!c1.querySelector('.hard') && !soft) options.appendChild(h('div', 'cond-hint', T.noFilters));

    const add = h('button', 'cond-add'); add.type = 'button';
    const plus = h('span', null, '+'); plus.setAttribute('aria-hidden', 'true'); add.append(plus, document.createTextNode(T.addCondition));
    add.setAttribute('aria-haspopup', 'dialog');
    add.addEventListener('click', () => openAddMenu(p, apply)); card.appendChild(add);

    if (p.unsupported_asks && p.unsupported_asks.length) card.appendChild(h('div', 'cond-unsupported', T.unsupportedNote(p.unsupported_asks.map(a => (meta.unsupported_key || {})[a] || a))));

    if (state.count || state.metrics.length) {
      const st = h('div', 'cond-status');
      st.appendChild(h('span', null, state.metrics.length ? T.foundN(state.metrics.length) : T.foundNone));
      if (state.metrics.length) { const b = h('button', 'btn small', T.seeResults); b.type = 'button'; b.addEventListener('click', () => { returnToLatest(); $('#results').scrollIntoView({ behavior: 'smooth', block: 'start' }); }); st.appendChild(b); }
      card.appendChild(st);
    }
  }

  function openPopover(title, fields, onApply, hint, cssClass = '') {
    const pop = $('#popover'); pop.innerHTML = ''; pop.appendChild(h('h4', null, title));
    pop.classList.toggle('score-editor', cssClass === 'score-editor');
    pop.setAttribute('role', 'dialog'); pop.setAttribute('aria-modal', 'true'); pop.setAttribute('aria-label', title);
    const inputs = [];
    for (const f of fields) {
      const lab = h('label'); lab.appendChild(h('span', null, f.label));
      let inp;
      if (f.type === 'select') { inp = h('select'); for (const [k, v] of Object.entries(f.options)) { const o = h('option', null, v); o.value = k; if (k === f.value) o.selected = true; inp.appendChild(o); } if (f.allowEmpty) { const o = h('option', null, T.any); o.value = ''; if (!f.value) o.selected = true; inp.prepend(o); } }
      else { inp = h('input'); inp.type = f.type || 'text'; if (f.value != null) inp.value = f.value; if (f.type === 'number') { inp.inputMode = 'decimal'; inp.step = 'any'; } }
      for (const key of ['min', 'max', 'step', 'required']) if (f[key] != null) inp[key] = f[key];
      lab.appendChild(inp); pop.appendChild(lab); inputs.push(inp);
    }
    if (hint) pop.appendChild(h('div', 'hint', hint));
    const act = h('div', 'actions'); const left = h('div'); const right = h('div', 'right');
    const cancel = h('button', 'btn ghost small', T.cancel); cancel.type = 'button'; cancel.addEventListener('click', closePopover);
    const ok = h('button', 'btn small', T.apply); ok.type = 'button'; ok.addEventListener('click', () => {
      const invalid = inputs.find(i => !i.checkValidity());
      if (invalid) { invalid.reportValidity(); return; }
      closePopover(); onApply(inputs.map(i => i.value));
    });
    right.appendChild(cancel); right.appendChild(ok); act.appendChild(left); act.appendChild(right); pop.appendChild(act);
    pop.classList.add('is-open'); $('#backdrop').classList.add('is-open');
    $('#backdrop').onclick = closePopover;
    inputs[0] && inputs[0].focus();
    pop.onkeydown = ev => { if (ev.key === 'Enter') { ev.preventDefault(); ok.click(); } if (ev.key === 'Escape') closePopover(); };
  }
  function openAddMenu(p, apply) {
    const meta = state.meta, pop = $('#popover');
    pop.innerHTML = ''; pop.classList.remove('score-editor');
    pop.setAttribute('role', 'dialog'); pop.setAttribute('aria-modal', 'true'); pop.setAttribute('aria-label', T.addCondition);
    pop.appendChild(h('h4', null, T.addCondition));
    const list = h('div', 'add-menu');
    const item = (label, run) => {
      const b = h('button', 'add-menu-item', label); b.type = 'button';
      b.addEventListener('click', () => { closePopover(); run(); });
      list.appendChild(b);
    };
    const int = v => Math.max(1, Math.round(v));
    if (p.max_price == null) item(T.budgetMax, () => editNumber(T.budgetMax, null, T.aud, v => v && apply(n => { n.max_price = Math.round(v); })));
    if (p.min_price == null) item(T.budgetMin, () => editNumber(T.budgetMin, null, T.aud, v => v && apply(n => { n.min_price = Math.round(v); })));
    if (p.bedrooms == null) item(T.bedroomsLabel, () => editNumber(T.bedroomsLabel, null, T.roomsUnit, v => v && apply(n => { n.bedrooms = int(v); })));
    if (p.bathrooms == null) item(T.bathroomsLabel, () => editNumber(T.bathroomsLabel, null, T.roomsUnit, v => v && apply(n => { n.bathrooms = int(v); })));
    if (!p.property_type) item(T.typeLabel, () => editSelect(T.typeLabel, '', meta.property_types, v => v && apply(n => { n.property_type = v; })));
    if (!p.suburb) item(T.suburbLabel, () => editText(T.suburbLabel, '', v => v && apply(n => { n.suburb = v; })));
    if (p.max_distance_cbd_km == null) item(T.cbdLabel, () => editNumber(T.cbdLabel, null, 'km', v => v && apply(n => { n.max_distance_cbd_km = v; })));
    item(T.addAmenity, () => openPopover(T.addAmenity, [
      { label: T.addAmenityKind, type: 'select', value: 'train_station', options: meta.kinds },
      { label: T.addAmenityMeters, type: 'number', value: 1500, min: 100, max: 20000, step: 100, required: true },
      { label: T.addStrength, type: 'select', value: 'preferred', options: { preferred: T.preferredShort, required: T.requiredShort } },
    ], ([kind, m, strength]) => apply(n => {
      n.amenity_needs = [...(n.amenity_needs || []).filter(x => x.kind !== kind), { kind, max_distance_m: Math.round(Number(m)), strength }];
    })));
    const freeAttrs = Object.fromEntries(Object.entries(meta.attributes || {}).filter(([k]) => !(p.abstract_needs || []).some(x => x.attribute === k)));
    if (Object.keys(freeAttrs).length) item(T.addPreference, () => openPopover(T.addPreference, [
      { label: T.addPreferencePick, type: 'select', value: Object.keys(freeAttrs)[0], options: freeAttrs },
      { label: T.scoreComparison, type: 'select', value: 'gte', options: T.scoreOperators },
      { label: T.scoreValue, type: 'number', value: 60, min: 0, max: 100, step: 1, required: true },
    ], ([attribute, op, score]) => apply(n => {
      n.abstract_needs = [...(n.abstract_needs || []), { attribute, min_score: Number(score), operator: op, strength: 'preferred', value_source: 'explicit' }];
    }), T.scoreHint, 'score-editor'));
    const freePlan = Object.fromEntries(Object.entries(meta.planning_needs || {}).filter(([k]) => !(p.planning_needs || []).includes(k)));
    if (Object.keys(freePlan).length) item(T.addPlanning, () => openPopover(T.addPlanning, [
      { label: '', type: 'select', value: Object.keys(freePlan)[0], options: freePlan },
    ], ([k]) => apply(n => { n.planning_needs = [...(n.planning_needs || []), k]; })));
    pop.appendChild(list);
    const chat = h('button', 'link add-menu-chat', T.addByChat); chat.type = 'button';
    chat.addEventListener('click', () => { closePopover(); $('#input').focus(); });
    pop.appendChild(chat);
    pop.classList.add('is-open'); $('#backdrop').classList.add('is-open'); $('#backdrop').onclick = closePopover;
    pop.onkeydown = ev => { if (ev.key === 'Escape') closePopover(); };
    list.querySelector('button')?.focus();
  }
  function closePopover() { $('#popover').classList.remove('is-open'); if (!$('#detail').classList.contains('open')) $('#backdrop').classList.remove('is-open'); }
  function editNumber(title, value, unit, cb) { openPopover(title, [{ label: unit, type: 'number', value }], ([v]) => { const n = parseFloat(v); if (!isNaN(n) && n > 0) cb(n); else if (v === '') cb(null); }); }
  const scoreSymbol = op => ({ gte: '≥', gt: '>', lte: '≤', lt: '<', eq: '=' }[op] || '≥');
  function editScore(need, cb) {
    openPopover(T.scoreTitle(attrZh(need.attribute)), [
      { label: T.scoreComparison, type: 'select', value: need.operator || 'gte', options: T.scoreOperators },
      { label: T.scoreValue, type: 'number', value: need.min_score, min: 0, max: 100, step: 1, required: true },
    ], ([op, value]) => cb(op, Number(value)), T.scoreHint, 'score-editor');
  }
  function editText(title, value, cb) { openPopover(title, [{ label: '', type: 'text', value }], ([v]) => cb(v.trim())); }
  function editSelect(title, value, options, cb) { openPopover(title, [{ label: '', type: 'select', value, options, allowEmpty: true }], ([v]) => cb(v)); }

  function renderResultsHead() {
    const r = $('#results');
    const view = resultView();
    $('#results-history')?.remove();
    if (state.historyView) {
      const banner = h('div', 'results-history'); banner.id = 'results-history'; banner.setAttribute('role', 'status');
      const text = h('div');
      const time = view.createdAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
      text.append(h('strong', null, T.historyTitle(time)), h('div', 'history-note', T.historyNote));
      const back = h('button', 'btn small', T.returnLatest); back.type = 'button';
      back.addEventListener('click', returnToLatest); banner.append(text, back); r.prepend(banner);
    }
    let head = $('#res-head'); if (!head) { head = h('div', 'res-head'); head.id = 'res-head'; r.prepend(head); }
    head.innerHTML = '';
    const range = view.offset > 0
      ? T.range(view.offset + 1, view.offset + view.metrics.length)
      : T.count(view.metrics.length);
    const left = h('div'); left.appendChild(h('div', 'res-title', view.metrics.length ? range : (view.count === 0 && view.ranking ? T.noResultsTitle : T.resultsTitle)));
    head.appendChild(left);
    if (view.params && state.meta) {
      const label = h('label', 'result-sort'); label.appendChild(h('span', null, T.sortGroup));
      const select = h('select'); select.id = 'result-sort'; select.disabled = state.running || !!state.historyView;
      if (state.historyView) select.title = T.historySort;
      for (const [key, text] of sortOptions(view.params)) {
        const option = h('option', null, text); option.value = key; option.selected = (view.params.sort_by || '') === key; select.appendChild(option);
      }
      select.addEventListener('change', () => {
        const value = select.value; select.value = state.params.sort_by || '';
        refine({ ...state.params, sort_by: value || null, relative_preferences: null }, T.statusSort);
      });
      label.appendChild(select); head.appendChild(label);
    }
    let notice = $('#results-notice');
    if (!notice) { notice = h('div', 'results-notice'); notice.id = 'results-notice'; notice.setAttribute('role', 'status'); head.after(notice); }
    notice.textContent = state.historyView ? '' : state.resultNotice; notice.hidden = !notice.textContent;
    let notes = $('#res-notes'); if (!notes) { notes = h('div', 'res-notes'); notes.id = 'res-notes'; head.after(notes); }
    notes.innerHTML = '';
    for (const n of (view.ranking || '').split(';').map(s => s.trim()).filter(Boolean)) notes.appendChild(h('div', null, n));
  }

  function sortOptions(params) {
    const labels = state.meta.sort_labels || {};
    const opts = [['', params.relative_preferences?.length ? T.relativeSort : T.sortDefault]];
    for (const [key, text] of Object.entries(labels)) {
      if (key === 'near_place_distance' || key.startsWith('nearest:')) continue;
      opts.push([key, text]);
    }
    for (const n of params.amenity_needs || []) {
      const key = 'nearest:' + n.kind;
      if (labels[key] && !opts.some(o => o[0] === key)) opts.push([key, labels[key]]);
    }
    if (params.near_place?.name) opts.push(['near_place_distance', T.sortNearPlace(params.near_place.name)]);
    return opts;
  }

  function headline(m) {
    const p = resultView().params || {};
    const s = p.relative_preferences?.[0]?.field || p.sort_by; const meta = state.meta;
    if (s && meta.attributes[s] && m.context_scores && m.context_scores[s] != null) {
      return { v: m.context_scores[s], l: T.attrDegree(attrZh(s)), cls: '', sub: evidenceLine(m, s, 2) };
    }
    if (s === 'near_place_distance' && m.near_place) return { v: dist(m.near_place.distance_m), l: T.toPlace(m.near_place.name), cls: '', sub: '' };
    if (s && s.startsWith('nearest:')) {
      const kind = s.slice(8), a = (m.amenities || {})[kind];
      if (a && a.distance_m != null) return { v: dist(a.distance_m), l: kindZh(kind), cls: '', sub: a.name || '' };
    }
    if (s === 'cap_rate') return { v: '~' + pct(m.cap_rate), l: 'Cap Rate', cls: 'assume', sub: T.byOpexAssumption };
    if (s === 'roi') return { v: '~' + pct(m.roi), l: 'ROI', cls: 'assume', sub: T.byOpexAssumption };
    if (s === 'predicted_gap') return { v: (m.predicted_gap > 0 ? '+' : '') + pct(m.predicted_gap), l: T.valGap, cls: 'model', sub: T.modelValueSub(money(m.predicted_price)) };
    return { v: pct(m.gross_yield), l: T.grossYieldShort, cls: '', sub: T.annualRentSub(money(m.annual_rent)) };
  }

  function otherScores(m) {
    const p = resultView().params || {}, s = p.relative_preferences?.[0]?.field || p.sort_by, out = [];
    const attrs = new Set([...(p.abstract_needs || []).map(n => n.attribute), ...(p.relative_preferences || []).map(g => g.field)]);
    for (const a of attrs) {
      if (a === s) continue;
      const sc = m.context_scores && m.context_scores[a];
      if (sc == null) continue;
      out.push([attrZh(a), sc, a]);
    }
    return out.slice(0, 3);
  }

  function evidenceLine(m, attr, limit) {
    const parts = (state.meta.attribute_parts[attr] || []); const ev = m.context_evidence || {}; const out = [];
    for (const k of parts) { if (ev[k] == null) continue; const [label, unit] = state.meta.evidence_labels[k] || [k, '']; out.push(label + ' ' + fmtEvidence(ev[k], unit)); if (out.length >= limit) break; }
    return out.join(' · ');
  }
  function fmtEvidence(v, unit) { if (unit === '米' || unit === 'm') return dist(v); if (typeof v === 'number') return (Number.isInteger(v) ? v : v.toFixed(1)) + (unit ? ' ' + unit : ''); return String(v); }

  const WEAK = 25;
  function evidenceNodes(m, attr, limit) {
    const frag = document.createDocumentFragment();
    const parts = (state.meta.attribute_parts[attr] || []);
    const ev = m.context_evidence || {};
    const strength = ((m.context_parts || {})[attr]) || {};
    let n = 0;
    for (const k of parts) {
      if (ev[k] == null) continue;
      if (n >= limit) break;
      const [label, unit] = state.meta.evidence_labels[k] || [k, ''];
      if (n) frag.appendChild(document.createTextNode(' · '));
      const s = strength[k];
      const txt = label + ' ' + fmtEvidence(ev[k], unit);
      if (s != null && s <= WEAK) {
        const span = h('span', 'ev-weak', txt + T.weakNote(s));
        frag.appendChild(span);
      } else {
        frag.appendChild(document.createTextNode(txt));
      }
      n++;
    }
    return frag;
  }

  const shortLabel = l => l.replace(/^距最近/, '')
    .replace(/^\d+\s*米内/, '')
    .replace(/^Nearest\s+/i, '')
    .replace(/\s+within\s+\d+\s*m$/i, '');

  function topEvidence(m, attr) {
    const parts = (state.meta.attribute_parts[attr] || []); const ev = m.context_evidence || {};
    for (const k of parts) {
      if (ev[k] == null) continue;
      const [label, unit] = state.meta.evidence_labels[k] || [k, ''];
      return shortLabel(label) + ' ' + fmtEvidence(ev[k], unit);
    }
    return '';
  }

  function badges(m) {
    const p = resultView().params || {}; const s = p.sort_by;
    const asked = [], risk = [], extra = [];

    for (const [, , attr] of otherScores(m).map(x => [x[0], x[1], x[2]])) {
      const ev = topEvidence(m, attr);
      if (ev) asked.push([attrZh(attr) + ' · ' + ev, 'ask']);
    }
    for (const need of (p.amenity_needs || [])) {
      const a = m.amenities && m.amenities[need.kind];
      if (a && a.distance_m != null) asked.push([kindZh(need.kind) + ' ' + dist(a.distance_m), 'ask']);
    }
    if (m.near_place && s !== 'near_place_distance') asked.push([T.nearPlaceBadge(m.near_place.name, dist(m.near_place.distance_m)), 'ask']);
    if (p.school_zone && m.school_zones && m.school_zones.primary) asked.push([T.primaryZoneBadge(m.school_zones.primary.replace(/ Primary School$/, '')), 'ask']);
    if (p.min_gross_yield != null) asked.push([T.yieldBadge(pct(m.gross_yield)), 'ask']);

    if (s !== 'gross_yield' && p.min_gross_yield == null) extra.push([T.yieldBadge(pct(m.gross_yield)), '']);
    const vgap = m.predicted_gap, vpos = m.valuation_position;
    if (vgap != null && (vpos === 'below' || vpos === 'above')) {
      extra.push([(vgap > 0 ? T.gapAbove : T.gapBelow)(pct(Math.abs(vgap))), 'accent']);
    }
    if (!asked.length && m.school_zones && m.school_zones.primary) extra.push([T.primaryZoneBadge(m.school_zones.primary.replace(/ Primary School$/, '')), '']);

    const pl = m.planning || {};
    for (const o of (pl.overlays || [])) { if (o.effect === 'risk') risk.push([o.label, 'warn']); }
    if ((pl.overlays || []).some(o => o.family === 'HO')) risk.push([T.heritageOverlay, 'warn']);
    if (pl.nearby_total && !pl.nearby_high && pl.nearby_low) risk.push([T.lowDensityAround, 'warn']);

    const head = asked.slice(0, 4);
    return { asked: head.concat(extra.slice(0, Math.max(0, 4 - head.length))), risk: risk.slice(0, 3) };
  }

  function renderResults() {
    const r = $('#results');
    const view = resultView();
    renderResultsHead();
    const keep = new Set(['res-head', 'res-notes', 'results-notice', 'results-history']);
    for (const el of [...r.children]) if (!keep.has(el.id)) el.remove();
    renderMap();
    if (!view.metrics.length) { if (view.count === 0 && view.ranking) r.appendChild(h('div', 'empty', T.emptyMsg)); refreshRefs(); return; }
    let idx = 0;
    for (const m of view.metrics) {
      const no = view.offset + idx + 1;
      const c = h('div', 'card' + (m.id === state.selectedId ? ' selected' : '')); c.dataset.id = m.id;
      c.classList.toggle('has-relative', !!view.params?.relative_preferences?.length);
      c.dataset.no = no;
      c.style.setProperty('--d', Math.min(idx++, 8) * 45 + 'ms');
      const top = h('div', 'card-top'); const id = h('div', 'card-id');
      const addr = h('div', 'card-addr');
      addr.appendChild(h('span', 'card-no', String(no)));
      addr.appendChild(document.createTextNode((m.suburb || '') + ' · ' + (m.address || '')));
      id.appendChild(addr);
      id.appendChild(h('div', 'card-price', money(m.price)));
      id.appendChild(h('div', 'card-meta', [T.beds(m.bedrooms), T.baths(m.bathrooms), typeZh(m)].filter(Boolean).join(' · ')));
      const hl = headline(m);
      const met = h('div', 'metrics');
      for (const [name, sc] of otherScores(m)) {
        const col = h('div', 'metric');
        col.appendChild(h('div', 'ml', name));
        col.appendChild(h('div', 'mv', String(sc)));
        met.appendChild(col);
      }
      const pm = h('div', 'metric primary');
      pm.appendChild(h('div', 'ml', hl.l));
      const pv = h('div', 'mv ' + hl.cls); pv.textContent = hl.v; pm.appendChild(pv);
      met.appendChild(pm);
      const hd = met;
      top.appendChild(id); top.appendChild(hd); c.appendChild(top);
      if (hl.sub) c.appendChild(h('div', 'card-sub', hl.sub));
      const bg = badges(m);
      if (bg.asked.length) { const bs = h('div', 'badges'); for (const [t, cls] of bg.asked) bs.appendChild(h('span', 'badge ' + cls, t)); c.appendChild(bs); }
      if (bg.risk.length) {
        const rs = h('div', 'badges risks'); rs.appendChild(h('span', 'risk-lead', T.riskLead));
        for (const [t] of bg.risk) rs.appendChild(h('span', 'badge warn', t));
        c.appendChild(rs);
      }
      c.addEventListener('click', () => openDetail(m));
      c.addEventListener('mouseenter', () => hlPin(no, true));
      c.addEventListener('mouseleave', () => hlPin(no, false));
      if (window.nwFav) c.appendChild(window.nwFav.button(m));
      r.appendChild(c);
    }
    renderMoreBtn(r);
    refreshRefs();
  }

  function renderMoreBtn(r) {
    if (state.historyView) return;
    const left = state.pool.length - (state.offset + state.metrics.length);
    if (state.pool.length <= state.metrics.length) return;
    const box = h('div', 'more-batch');
    if (left > 0) {
      const b = h('button', 'btn ghost small', T.nextBatch(left)); b.type = 'button';
      b.disabled = state.running;
      b.addEventListener('click', nextBatch);
      box.appendChild(b);
    } else {
      const b = h('button', 'btn ghost small', T.backToFirst); b.type = 'button';
      b.disabled = state.running;
      b.addEventListener('click', () => { if (state.running) return; state.offset = -state.metrics.length; nextBatch(); });
      box.appendChild(b);
      box.appendChild(h('div', 'more-note', T.batchDone));
    }
    r.appendChild(box);
  }

  async function nextBatch() {
    if (state.running || state.historyView) return;
    const next = state.offset + state.metrics.length;
    state.offset = next >= state.pool.length ? 0 : next;
    state.metrics = state.pool.slice(state.offset, state.offset + 5);
    state.selectedId = null;
    closeSheet();
    renderResults();
    const col = document.querySelector('.col-results');
    if (col) scrollTo(col, 0);

    setRunning(true);
    const status = addStatus();
    status.querySelector('.t').textContent = T.statusRebatch;
    const a = addAssistantMsg();
    scrollChat();
    await streamPost('/api/rebatch', { thread_id: state.threadId, offset: state.offset, lang: state.lang },
                     runHandlers(status, a));
  }

  function refreshRefs() {
    const currentKey = resultKey(resultView()), latestKey = resultKey(state);
    for (const b of document.querySelectorAll('.ref, .msg-results')) {
      const binding = referenceTargets.get(b); if (!binding) continue;
      const { snapshot, targets, title } = binding;
      const valid = !targets || targets.every(t => t.id != null);
      b.disabled = state.running || !valid;
      b.classList.toggle('stale', !valid);
      if (targets) {
        b.title = !valid ? T.referenceUnavailable : (snapshot.key !== currentKey ? T.viewHistorical + ' · ' : '') + title;
      } else {
        const viewing = !!state.historyView && snapshot === state.historyView;
        b.textContent = viewing ? T.viewingHistorical : snapshot.key === latestKey ? T.currentResults : T.viewHistorical;
        b.setAttribute('aria-pressed', String(viewing));
        b.closest('.msg-row').classList.toggle('viewing-history', viewing);
      }
    }
  }

  function renderMap() {
    const box = $('#map'); if (!box) return;
    const view = resultView();
    clearTimeout(state.focusTimer); state.focusTimer = null;
    if (state.map) { state.map.remove(); state.map = null; }
    if (state.mapRO) { state.mapRO.disconnect(); state.mapRO = null; }
    state.pins = []; state.mapFit = null;
    state.measure = null;
    box.innerHTML = '';
    setMapCount(view.metrics.length);
    if (typeof L === 'undefined') { box.innerHTML = '<div class="map-empty"></div>'; box.firstChild.textContent = T.mapOffline; return; }
    const pts = view.metrics.filter(m => m.latitude != null && m.longitude != null);
    if (!pts.length) { box.innerHTML = '<div class="map-empty"></div>'; box.firstChild.textContent = T.mapNoCoords; return; }

    const map = L.map(box, { zoomControl: false, attributionControl: false, zoomAnimation: false });
    addBasemap(map);
    const group = [];
    const pins = [];
    const cluster = new Map();
    for (const m of pts) {
      const key = m.latitude.toFixed(4) + ',' + m.longitude.toFixed(4);
      if (!cluster.has(key)) cluster.set(key, []);
      cluster.get(key).push(m);
    }
    const nudgeOf = m => {
      const arr = cluster.get(m.latitude.toFixed(4) + ',' + m.longitude.toFixed(4)) || [];
      if (arr.length < 2) return 0;
      const i = arr.indexOf(m);
      return (i - (arr.length - 1) / 2) * 26;
    };
    pts.forEach(m => {
      const no = view.offset + view.metrics.indexOf(m) + 1;
      const mk = L.marker([m.latitude, m.longitude], {
        icon: L.divIcon({
          className: 'pin',
          html: '<span class="pin-in" style="--nx:' + nudgeOf(m) + 'px">'
              + '<span class="pin-badge">' + no + '</span><span class="pin-tip"></span></span>',
          iconSize: [26, 28], iconAnchor: [13, 28],
        }),
        keyboard: false,
      }).addTo(map);
      pins.push({ no, mk });
      mk.on('click', () => { openDetail(m); focusCard(no); });
      mk.on('mouseover', () => hlCard(no, true));
      mk.on('mouseout', () => hlCard(no, false));
      group.push([m.latitude, m.longitude]);
    });
    state.pins = pins;
    map.setView([-37.81, 144.96], 11);
    const fit = () => {
      if (!box.clientWidth || !box.clientHeight) return;
      map.invalidateSize();
      map.fitBounds(group, { paddingTopLeft: [170, 96], paddingBottomRight: [96, 104], maxZoom: 14, animate: false });
    };
    fit();
    state.map = map;
    const coord = () => { const c = map.getCenter(), e = $('#map-coord'); if (e) e.textContent = c.lat.toFixed(4) + ', ' + c.lng.toFixed(4); };
    map.on('move', coord); coord();

    let userTook = false;
    const mark = () => { userTook = true; };
    box.addEventListener('pointerdown', mark, { passive: true });
    box.addEventListener('wheel', mark, { passive: true });
    state.mapFit = () => { userTook = false; fit(); };
    state.mapTook = mark;
    map.on('unload', () => {
      box.removeEventListener('pointerdown', mark);
      box.removeEventListener('wheel', mark);
    });

    state.mapRO = new ResizeObserver(() => {
      if (state.map !== map) return;
      map.invalidateSize();
      if (!userTook) fit();
    });
    state.mapRO.observe(box);
  }

  function kv(k, v, cls, s) { const e = h('div', 'kv'); e.appendChild(h('div', 'k', k)); const vv = h('div', 'v' + (cls ? ' ' + cls : '')); vv.textContent = v; e.appendChild(vv); if (s) e.appendChild(h('div', 's', s)); return e; }
  function hlPin(no, on) {
    for (const { no: n, mk } of (state.pins || [])) {
      const el = mk.getElement && mk.getElement();
      if (el) el.classList.toggle('hl', on && n === no);
    }
  }
  function hlCard(no, on) {
    for (const c of document.querySelectorAll('.card')) c.classList.toggle('hl', on && c.dataset.no === String(no));
  }

  function markPin(no) {
    for (const { no: n, mk } of (state.pins || [])) {
      const el = mk.getElement && mk.getElement();
      if (el) el.classList.toggle('on', n === no);
    }
  }

  function focusOnMap(m) {
    const map = state.map;
    const view = resultView();
    const at = view.metrics.findIndex(item => item.id === m.id);
    markPin(at >= 0 ? view.offset + at + 1 : 0);
    if (!map || typeof L === 'undefined' || m.latitude == null || m.longitude == null) return;
    const ll = [m.latitude, m.longitude];
    const zoom = Math.max(map.getZoom(), 15);
    map.setView(ll, zoom, { animate: false });
    clearTimeout(state.focusTimer);
    state.focusTimer = setTimeout(() => {
      if (state.map !== map || state.selectedId !== m.id) return;
      const c = map.getCenter();
      if (Math.abs(c.lat - ll[0]) > 1e-4 || Math.abs(c.lng - ll[1]) > 1e-4) {
        map.setView(ll, zoom, { animate: false });
      }
      nudgeForPanel(map);
    }, 800);
  }

  function addBasemap(map) {
    const raster = () => L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 18, className: 'osm-raster' }).addTo(map);
    const attr = vector => { const e = document.querySelector('.attr-vector'); if (e) e.hidden = !vector; };
    if (!window.nwGlassBase) { raster(); attr(false); return; }
    window.nwGlassBase().then(style => {
      if (state.map !== map) return;
      if (style) { L.maplibreGL({ style, attributionControl: false, interactive: false }).addTo(map); attr(true); }
      else { raster(); attr(false); }
    });
  }

  function setMapCount(n) { const e = $('#map-count'); if (e) e.textContent = T.mapCount(n); }

  const CITIES = [['MELBOURNE', true], ['SYDNEY'], ['BRISBANE'], ['ADELAIDE'], ['PERTH'], ['CANBERRA'], ['HOBART']];
  function renderCityMenu() {
    const menu = $('#city-menu'); if (!menu) return;
    menu.replaceChildren(...CITIES.map(([name, live]) => {
      const li = h('li', 'city-opt' + (live ? ' on' : ''));
      li.setAttribute('role', 'option'); li.setAttribute('aria-selected', live ? 'true' : 'false');
      if (!live) li.setAttribute('aria-disabled', 'true');
      li.append(h('span', 'city-opt-name', name), h('span', 'city-opt-tag', live ? '' : T.citySoon));
      if (live) li.addEventListener('click', () => closeCityMenu());
      return li;
    }));
  }
  function closeCityMenu() {
    const menu = $('#city-menu'); if (!menu || menu.hidden) return false;
    menu.hidden = true; $('#city-btn').setAttribute('aria-expanded', 'false');
    document.removeEventListener('pointerdown', cityOutside, true);
    return true;
  }
  function cityOutside(ev) { if (!ev.target.closest('.city')) closeCityMenu(); }
  function initMapPanel() {
    $('#city-btn').addEventListener('click', () => {
      if (closeCityMenu()) return;
      renderCityMenu();
      $('#city-menu').hidden = false; $('#city-btn').setAttribute('aria-expanded', 'true');
      setTimeout(() => document.addEventListener('pointerdown', cityOutside, true), 0);
    });
    $('#map-fit').addEventListener('click', () => { if (state.mapFit) state.mapFit(); });
    $('#map-zin').addEventListener('click', () => { if (state.map) { state.mapTook && state.mapTook(); state.map.zoomIn(); } });
    $('#map-zout').addEventListener('click', () => { if (state.map) { state.mapTook && state.mapTook(); state.map.zoomOut(); } });
  }

  function nudgeForPanel(map) {
    const d = $('#detail');
    if (!d || !d.classList.contains('open') || !twoColumn()) return;
    const wrap = document.querySelector('.map-wrap');
    if (!wrap) return;
    const mr = wrap.getBoundingClientRect(), dr = d.getBoundingClientRect();
    const cut = Math.max(mr.left, Math.min(dr.left, mr.right));
    const visible = cut - mr.left;
    if (visible < 160) return;
    const dx = (mr.left + mr.width / 2) - (mr.left + visible / 2);
    if (dx > 20) map.panBy([dx, 0], { animate: false });
  }

  let dragOff = { x: 0, y: 0 };
  function setDetailPos(x, y) {
    dragOff = { x, y };
    const d = $('#detail');
    d.style.setProperty('--dx', x + 'px');
    d.style.setProperty('--dy', y + 'px');
  }

  function makeDraggable(bar) {
    bar.addEventListener('pointerdown', ev => {
      if (ev.target.closest('button, a, input, select, textarea, [role="button"]')) return;
      if (!twoColumn()) return;
      const d = $('#detail');
      const r = d.getBoundingClientRect();
      const baseLeft = r.left - dragOff.x, baseTop = r.top - dragOff.y;
      const startX = ev.clientX, startY = ev.clientY;
      const base = { x: dragOff.x, y: dragOff.y };
      const minX = -(baseLeft + r.width - 140), maxX = innerWidth - baseLeft - 140;
      const minY = 57 - baseTop, maxY = innerHeight - baseTop - 56;
      const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);

      bar.setPointerCapture(ev.pointerId);
      d.classList.add('dragging');
      const move = e => setDetailPos(clamp(base.x + e.clientX - startX, minX, maxX),
                                     clamp(base.y + e.clientY - startY, minY, maxY));
      const up = () => {
        d.classList.remove('dragging');
        try { bar.releasePointerCapture(ev.pointerId); } catch (_) {  }
        bar.removeEventListener('pointermove', move);
        bar.removeEventListener('pointerup', up);
        bar.removeEventListener('pointercancel', up);
      };
      bar.addEventListener('pointermove', move);
      bar.addEventListener('pointerup', up);
      bar.addEventListener('pointercancel', up);
      ev.preventDefault();
    });
  }

  function measureBlock(m) {
    const box = h('div', 'measure');
    const head = h('div', 'measure-head');
    head.appendChild(h('span', 'mh-t', T.measureTitle));
    head.appendChild(h('span', 'mh-k', T.measureKind));
    box.appendChild(head);

    const mkRow = (tag, label) => {
      const r = h('div', 'measure-row');
      r.appendChild(h('span', 'm-tag ' + tag, tag.toUpperCase()));
      r.appendChild(h('span', 'm-lab', label));
      const inp = h('input', 'm-in'); inp.type = 'text';
      r.appendChild(inp);
      return { row: r, inp };
    };

    const from = mkRow('a', T.measureFrom);
    from.inp.value = (m.suburb || '') + ' · ' + (m.address || '');
    let originCoords = { lat: m.latitude, lon: m.longitude };
    from.inp.addEventListener('input', () => { originCoords = null; });

    const to = mkRow('b', T.measureTo);
    to.inp.placeholder = T.measureToPlaceholder;

    const go = h('button', 'btn small m-go', T.measureGo); go.type = 'button';
    to.row.appendChild(go);
    box.appendChild(from.row); box.appendChild(to.row);

    const result = h('div', 'measure-out'); result.hidden = true;
    const big = h('div', 'm-dist');
    const pair = h('div', 'm-pair');
    const look = h('button', 'link m-look', T.measureLook); look.type = 'button';
    look.addEventListener('click', () => closeSheet());
    const wipe = h('button', 'link m-wipe', T.measureClear); wipe.type = 'button';
    wipe.addEventListener('click', () => {
      clearMeasure();
      to.inp.value = '';
      result.hidden = true; err.hidden = true;
      to.inp.focus();
    });
    const acts = h('div', 'm-acts');
    acts.appendChild(look); acts.appendChild(wipe);
    result.appendChild(big); result.appendChild(pair); result.appendChild(acts);
    box.appendChild(result);
    const err = h('div', 'measure-err'); err.hidden = true;
    box.appendChild(err);
    box.appendChild(h('div', 'caveat', T.measureNote));

    async function run() {
      const q = to.inp.value.trim();
      if (!q) { to.inp.focus(); return; }
      go.disabled = true; go.textContent = T.measureBusy; err.hidden = true;
      const origin = originCoords && originCoords.lat != null
        ? { lat: originCoords.lat, lon: originCoords.lon, label: from.inp.value.trim() }
        : { q: from.inp.value.trim() };
      let res;
      try {
        res = await fetch('/api/measure', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ origin, target: { q } }),
        });
      } catch (_) {
        go.disabled = false; go.textContent = T.measureGo; return;
      }
      go.disabled = false; go.textContent = T.measureGo;
      if (!res.ok) {
        let msg = '';
        try { msg = (await res.json()).detail || ''; } catch (_) { msg = await res.text(); }
        err.textContent = msg; err.hidden = false; result.hidden = true;
        return;
      }
      const j = await res.json();
      result.hidden = false;
      pair.textContent = T.measurePair(j.origin.name, j.target.name);
      big.textContent = dist(j.distance_m);
      countUp(big, j.distance_m);
      drawMeasure(j);
    }
    go.addEventListener('click', run);
    to.inp.addEventListener('keydown', ev => { if (ev.key === 'Enter') { ev.preventDefault(); run(); } });
    return box;
  }

  function countUp(el, meters, ms) {
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const total = ms || 620, t0 = performance.now();
    const step = now => {
      const k = Math.min(1, (now - t0) / total);
      el.textContent = dist(Math.round(meters * (1 - Math.pow(1 - k, 3))));
      if (k < 1) requestAnimationFrame(step); else el.textContent = dist(meters);
    };
    requestAnimationFrame(step);
  }

  function drawMeasure(j) {
    const map = state.map; if (!map) return;
    clearTimeout(state.focusTimer);
    clearMeasure();
    const a = [j.origin.latitude, j.origin.longitude];
    const b = [j.target.latitude, j.target.longitude];
    const layer = L.layerGroup().addTo(map);
    state.measure = layer;

    const line = L.polyline([a, b], { className: 'mline-halo', weight: 7,
                                      interactive: false }).addTo(layer);
    const core = L.polyline([a, b], { className: 'mline', weight: 1.75,
                                      interactive: false }).addTo(layer);
    drawStroke(core.getElement());
    const strip = () => {
      const el = core.getElement();
      if (!el) return;
      el.style.transition = '';
      el.style.strokeDasharray = '';
      el.style.strokeDashoffset = '';
    };
    map.on('zoomstart', strip);
    state.measureOff = () => map.off('zoomstart', strip);

    const pin = (cls, text) => L.divIcon({
      className: 'mpin ' + cls,
      html: '<span class="mpin-in"><span class="mpin-badge">' + text + '</span>'
          + '<span class="mpin-tip"></span></span>',
      iconSize: [26, 28], iconAnchor: [13, 28],
    });
    L.marker(a, { icon: pin('a', 'A'), keyboard: false, interactive: false }).addTo(layer);
    L.marker(b, { icon: pin('b', 'B'), keyboard: false, interactive: false }).addTo(layer);

    state.measureBounds = L.latLngBounds([a, b]);
    const mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    const label = L.marker(mid, {
      icon: L.divIcon({
        className: 'mlabel',
        html: '<span class="ml-chip"><b class="ml-num"></b></span><i class="ml-stem"></i>',
        iconSize: [0, 0], iconAnchor: [0, 0],
      }),
      keyboard: false, interactive: false,
    }).addTo(layer);
    const num = label.getElement().querySelector('.ml-num');
    num.textContent = dist(j.distance_m);
    countUp(num, j.distance_m);

    fitAvoidingPanel(map, state.measureBounds);
    return line;
  }

  function drawStroke(path) {
    if (!path) return;
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const len = path.getTotalLength();
    path.style.strokeDasharray = len;
    path.style.strokeDashoffset = len;
    void path.getBoundingClientRect();
    path.style.transition = 'stroke-dashoffset 620ms cubic-bezier(0.23,1,0.32,1)';
    path.style.strokeDashoffset = '0';
    setTimeout(() => {
      path.style.transition = '';
      path.style.strokeDasharray = '';
      path.style.strokeDashoffset = '';
    }, 700);
  }

  function fitAvoidingPanel(map, bounds) {
    const wrap = document.querySelector('.map-wrap');
    const d = $('#detail');
    let right = 56;
    if (wrap && d && d.classList.contains('open') && twoColumn()) {
      const mr = wrap.getBoundingClientRect(), dr = d.getBoundingClientRect();
      const overlap = Math.max(0, mr.right - Math.max(dr.left, mr.left));
      if (mr.width - overlap > 90) right = overlap + 16;
    }
    const tight = right > 56 ? 16 : 56;
    map.invalidateSize();
    map.fitBounds(bounds, { animate: false,
                            paddingTopLeft: [tight, tight], paddingBottomRight: [right, tight] });
  }

  function clearMeasure() {
    if (state.measureOff) { state.measureOff(); state.measureOff = null; }
    if (state.measure) { state.measure.remove(); state.measure = null; }
    state.measureBounds = null;
  }

  function row(k, v, cls, small) { const e = h('div', 'row'); e.appendChild(h('span', 'k', k)); const vv = h('span', 'v' + (cls ? ' ' + cls : '')); vv.textContent = v; if (small) { const sm = h('small', null, ' ' + small); vv.appendChild(sm); } e.appendChild(vv); return e; }

  function payRow(label, value, note, cls) {
    const e = h('div', 'pay' + (cls ? ' ' + cls : ''));
    const left = h('div'); left.appendChild(h('div', 'pk', label));
    if (note) left.appendChild(h('div', 'pn', note));
    e.appendChild(left); e.appendChild(h('div', 'pv', value));
    return e;
  }

  function assumeField(inp, big) {
    const box = h('span', 'assume-field' + (big ? ' big' : ''));
    box.appendChild(inp);
    const pen = h('span', 'af-pen'); pen.innerHTML = PENCIL;
    box.appendChild(pen);
    box.addEventListener('click', ev => { if (ev.target !== inp) inp.focus(); });
    return box;
  }

  function payRowNum(label, note, opts) {
    const e = h('div', 'pay assume editable');
    const left = h('div'); left.appendChild(h('div', 'pk', label));
    if (note) left.appendChild(note.nodeType ? note : h('div', 'pn', note));
    e.appendChild(left);
    const val = h('div', 'pv');
    val.appendChild(h('span', 'pv-pre', opts.prefix || ''));
    const inp = h('input', 'pv-in'); inp.type = 'number'; inp.step = opts.step || '1';
    inp.value = opts.value; inp.inputMode = 'decimal';
    if (opts.min != null) inp.min = opts.min;
    if (opts.max != null) inp.max = opts.max;
    val.appendChild(assumeField(inp, true));
    if (opts.suffix) val.appendChild(h('span', 'pv-pre', opts.suffix));
    e.appendChild(val);
    const fit = () => { inp.style.width = Math.max(2, String(inp.value).length) + 0.6 + 'ch'; };
    fit();
    inp.addEventListener('input', fit);
    inp.addEventListener('change', () => opts.onChange(parseFloat(inp.value)));
    inp.addEventListener('keydown', ev => { if (ev.key === 'Enter') inp.blur(); });
    return e;
  }

  function jargon(title, pairs) {
    const dt = document.createElement('details'); dt.className = 'jargon';
    const sm = document.createElement('summary'); sm.textContent = title;
    dt.appendChild(sm);
    for (const [term, plain] of pairs) {
      if (!plain) continue;
      const r = h('div', 'jrow');
      r.appendChild(h('span', 'jt', term)); r.appendChild(h('span', 'jp', plain));
      dt.appendChild(r);
    }
    return dt;
  }

  function rankText(m, attr) {
    const r = (m.context_ranks || {})[attr];
    if (r == null) return '';
    return T.rank(Math.min(Math.max(r, 0), 99));
  }

  function openDetail(m, { keepToken = false } = {}) {
    if (state.selectedId !== m.id) clearMeasure();
    state.selectedId = m.id;
    for (const c of document.querySelectorAll('.card')) c.classList.toggle('selected', c.dataset.id == m.id);
    focusOnMap(m);
    const d = $('#detail'); d.innerHTML = ''; d.classList.remove('compare');
    if (d.classList.contains('open')) dockDetail();
    state.compare = []; state.detailMetric = m;
    if (!keepToken) state.detailToken = m.id;
    d.appendChild(h('div', 'grabber'));
    const bar = h('div', 'd-bar');
    bar.appendChild(h('div', 'd-bar-title', (m.suburb || '') + ' · ' + (m.address || '')));
    const close = h('button', 'd-close'); close.type = 'button'; close.title = T.closeEsc;
    close.setAttribute('aria-label', T.close); close.innerHTML = X;
    close.addEventListener('click', () => closeSheet({ user: true }));
    bar.appendChild(compareButton(T.cmpAdd));
    bar.appendChild(close); d.appendChild(bar);
    makeDraggable(bar);

    renderDetailInto(d, m);
    openSheet();
    if (!keepToken) window.dispatchEvent(new CustomEvent('nw:detail', { detail: { open: true, id: state.detailToken } }));
  }

  function renderDetailInto(d, m, { compact = false } = {}) {
    const meta = state.meta;
    const head = h('div', 'd-head'); const l = h('div');
    l.appendChild(h('div', 'd-addr', (m.suburb || '') + ' · ' + (m.address || '')));
    l.appendChild(h('div', 'd-price', money(m.price)));
    const area = v => (typeof v === 'number' && v > 0) ? Math.round(v).toLocaleString() : null;
    const metaItems = [T.beds(m.bedrooms), T.baths(m.bathrooms), typeZh(m), m.distance_cbd != null ? T.toCbd(m.distance_cbd) : null,
      area(m.land_size) && T.landArea(area(m.land_size)) + (m.area_suspect?.land ? T.areaSuspect : ''),
      area(m.building_area) && T.buildingArea(area(m.building_area)) + (m.area_suspect?.building ? T.areaSuspect : '')].filter(Boolean);
    const metaLine = h('div', 'd-meta');
    metaItems.forEach((x, i) => { if (i) metaLine.appendChild(document.createTextNode(' · ')); metaLine.appendChild(h('span', 'nowrap', x)); });
    l.appendChild(metaLine);
    head.appendChild(l);
    const hl = headline(m);
    const hd = h('div', 'metrics');
    const pm = h('div', 'metric primary');
    pm.appendChild(h('div', 'ml', hl.l));
    const pv = h('div', 'mv ' + hl.cls); pv.textContent = hl.v; pm.appendChild(pv);
    const sortAttr = resultView().params?.sort_by;
    if (sortAttr && meta.attributes[sortAttr]) {
      const rt = rankText(m, sortAttr);
      if (rt) pm.appendChild(h('div', 'ms', rt));
    }
    hd.appendChild(pm); head.appendChild(hd);
    head.dataset.sec = 'head'; d.appendChild(head);

    if (m.predicted_price != null) {
      const s = h('div', 'sec');
      s.appendChild(h('div', 'sec-title', T.sec1));
      const cmp = h('div', 'cmp');
      cmp.appendChild(kv(T.askingPrice, money(m.price)));
      cmp.appendChild(kv(T.modelEstimate, money(m.predicted_price), 'model'));
      s.appendChild(cmp);

      const gap = m.predicted_gap, pos = m.valuation_position;
      const iv = m.valuation_interval || [], lvl = m.valuation_interval_level;
      if (iv[0] != null && lvl) {
        cmp.appendChild(kv(T.intervalLabel(pct(lvl, 0)), money(iv[0]) + ' – ' + money(iv[1]), 'model'));
      }
      let verdict = '';
      if (gap != null && pos) {
        if (pos === 'within') verdict = T.verdictWithin(pct(lvl, 0));
        else if (pos === 'below') verdict = T.verdictBelowRange(pct(gap), pct(lvl, 0));
        else verdict = T.verdictAboveRange(pct(-gap), pct(lvl, 0));
      }
      if (verdict) s.appendChild(h('div', 'plain', verdict));
      if (iv[0] != null && lvl) {
        s.appendChild(h('div', 'caveat', T.intervalNote(pct(lvl, 0), pct(m.valuation_interval_coverage, 0))));
      }
      s.dataset.sec = 'price'; d.appendChild(s);
    }

    const s2 = h('div', 'sec'); s2.appendChild(h('div', 'sec-title', T.sec2));
    const am = m.amenities || {}; const g4 = h('div', 'grid4'); let n4 = 0;
    for (const [k, v] of Object.entries(am)) { if (v && v.distance_m != null && n4 < 8) { g4.appendChild(kv(kindZh(k), dist(v.distance_m))); n4++; } }
    if (n4 || m.near_place) {
      s2.appendChild(g4);
      s2.appendChild(h('div', 'caveat', T.straightLine));
    }
    if (!compact) s2.appendChild(measureBlock(m));

    const scores = m.context_scores || {};
    const viewParams = resultView().params || {};
    const asked = new Set((viewParams.abstract_needs || []).map(a => a.attribute));
    for (const goal of viewParams.relative_preferences || []) if (meta.attributes[goal.field]) asked.add(goal.field);
    if (viewParams.sort_by && meta.attributes[viewParams.sort_by]) asked.add(viewParams.sort_by);
    for (const a of asked) {
      if (scores[a] == null) continue;
      const eb = h('div', 'evbox'); const t = h('div', 't');
      const rt = rankText(m, a);
      t.appendChild(h('b', null, attrZh(a) + ' ' + scores[a]));
      t.appendChild(h('span', null, rt || T.score0100));
      eb.appendChild(t);
      const evl = h('div');
      evl.appendChild(document.createTextNode(T.scoreBasis));
      evl.appendChild(evidenceNodes(m, a, 6));
      eb.appendChild(evl);
      if (meta.attribute_notes[a]) eb.appendChild(h('div', 'ev-note', T.highMeans + meta.attribute_notes[a]));
      s2.appendChild(eb);
    }
    s2.dataset.sec = 'living'; d.appendChild(s2);

    const a0 = m.assumptions || meta.assumptions.snapshot;
    const live = { opex_rate: a0.opex_rate, other_acquisition_costs: a0.other_acquisition_costs };
    const now = Object.assign({}, m);
    const out = {};

    async function recalc() {
      let res;
      try {
        res = await fetch('/api/recalc', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ price: m.price, annual_rent: m.annual_rent,
                                 opex_rate: live.opex_rate,
                                 other_acquisition_costs: live.other_acquisition_costs }),
        });
      } catch (_) { return; }
      if (!res.ok) { alert(await res.text()); return; }
      Object.assign(now, await res.json());
      if (out.total) out.total.textContent = money(now.total_cost);
      if (out.noi) out.noi.textContent = '~' + money(now.noi);
      if (out.roi) out.roi.textContent = '~' + pct(now.roi);
      if (out.jg) {
        out.jg.noi.textContent = T.jgNoiF(money(now.noi));
        out.jg.cap.textContent = T.jgCapF(pct(now.cap_rate));
        out.jg.roi.textContent = T.jgRoiF(pct(now.roi));
      }
    }

    if (m.price != null && m.stamp_duty != null) {
      const s3 = h('div', 'sec'); s3.appendChild(h('div', 'sec-title', T.sec3));
      const box = h('div', 'paybox');
      box.appendChild(payRow(T.rowPrice, money(m.price), T.rowPriceNote));
      box.appendChild(payRow(T.rowDuty, money(m.stamp_duty), T.rowDutyNote(pct(m.stamp_duty / m.price))));
      box.appendChild(payRowNum(T.rowFees, T.rowFeesNoteEditable, {
        prefix: '~$', value: Math.round(live.other_acquisition_costs), step: '100', min: 0,
        onChange: v => { if (!isNaN(v) && v >= 0) { live.other_acquisition_costs = v; recalc(); } },
      }));
      if (m.total_cost != null) {
        const totalRow = payRow(T.rowTotal, money(m.total_cost), T.rowTotalNote, 'total');
        out.total = totalRow.querySelector('.pv');
        box.appendChild(totalRow);
      }
      s3.appendChild(box);
      s3.dataset.sec = 'cost'; d.appendChild(s3);
    }

    if (m.annual_rent != null) {
      const s4 = h('div', 'sec'); s4.appendChild(h('div', 'sec-title', T.sec4));
      const box = h('div', 'paybox');
      box.appendChild(payRow(T.rowRent, money(m.annual_rent),
        [m.gross_yield != null ? T.rowRentNote(pct(m.gross_yield)) : '', T.rentScope[m.rent_source] || '']
          .filter(Boolean).join(' · ')));

      const opexNote = h('div', 'pn');
      const pctText = r => {
        let [mant, exp = '0'] = String(r).toLowerCase().split('e');
        let [ip, fp = ''] = mant.split('.'); let digits = ip + fp, point = ip.length + Number(exp) + 2;
        if (point <= 0) { digits = '0'.repeat(1 - point) + digits; point = 1; }
        if (point > digits.length) digits += '0'.repeat(point - digits.length);
        const whole = digits.slice(0, point).replace(/^0+(?=\d)/, ''), frac = digits.slice(point).replace(/0+$/, '');
        return frac ? whole + '.' + frac : whole;
      };
      const oi = h('input', 'pn-in'); oi.type = 'number'; oi.step = 'any'; oi.min = '0'; oi.max = '99.99999999999999';
      oi.value = pctText(live.opex_rate); oi.inputMode = 'decimal';
      const fitOi = () => { oi.style.width = Math.max(2, String(oi.value).length) + 0.6 + 'ch'; };
      fitOi(); oi.addEventListener('input', fitOi);
      oi.addEventListener('change', () => {
        const v = parseFloat(oi.value);
        if (!isNaN(v) && v >= 0 && v < 100) { live.opex_rate = v / 100; recalc(); }
      });
      oi.addEventListener('keydown', ev => { if (ev.key === 'Enter') oi.blur(); });
      const oiBox = assumeField(oi, false);
      T.opexNoteParts.forEach((part, i) => {
        if (i === 1) opexNote.appendChild(oiBox);
        opexNote.appendChild(document.createTextNode(part));
      });

      const noiRow = payRow(T.rowNoi, '~' + money(m.noi), null, 'assume');
      noiRow.firstChild.appendChild(opexNote);
      out.noi = noiRow.querySelector('.pv');
      box.appendChild(noiRow);

      const roiRow = payRow(T.rowRoi, '~' + pct(m.roi), T.rowRoiNote, 'assume');
      out.roi = roiRow.querySelector('.pv');
      box.appendChild(roiRow);
      s4.appendChild(box);

      const jg = jargon(T.jargonTitle, [
        [T.jgYield, T.jgYieldF(pct(m.gross_yield))],
        [T.jgNoi, T.jgNoiF(money(m.noi))],
        [T.jgCap, T.jgCapF(pct(m.cap_rate))],
        [T.jgRoi, T.jgRoiF(pct(m.roi))],
      ]);
      const jp = jg.querySelectorAll('.jp');
      out.jg = { noi: jp[1], cap: jp[2], roi: jp[3] };
      s4.appendChild(jg);
      s4.appendChild(h('div', 'caveat', T.rentCaveat));

      s4.dataset.sec = 'rent'; d.appendChild(s4);
    }

    const s5 = h('div', 'sec'); s5.appendChild(h('div', 'sec-title', T.sec5)); const rows = h('div', 'rows');
    const sz = m.school_zones || {};
    rows.appendChild(row(T.primaryZone, sz.primary || T.notInZone));
    rows.appendChild(row(T.secondaryZone, sz.secondary || T.notInZone));
    const pl = m.planning || {};
    if (pl.zone) {
      rows.appendChild(row(T.planningZone, pl.zone_label || pl.zone));
      const ovs = pl.overlays || [];
      rows.appendChild(row(T.overlays, ovs.length ? ovs.map(o => o.code + ' ' + o.label).join(state.lang === 'en' ? ', ' : '、') : T.none,
        ovs.some(o => o.effect === 'risk' || o.effect === 'build') ? 'warn' : ''));
      if (pl.nearby_total) rows.appendChild(row(T.nearbyRadius(pl.radius_m), T.nearbyLots(pl.nearby_total, pl.nearby_low, pl.nearby_high, pl.nearby_open)));
    } else rows.appendChild(row(T.planningZone, T.noZoneData));
    if (m.crime) rows.appendChild(row(T.crimeLabel(m.crime.lga), T.crimeRate(Math.round(m.crime.rate_per_100k).toLocaleString('en-AU')), '', T.crimeScope + (meta.crime_year ? ' · ' + meta.crime_year : '')));
    s5.appendChild(rows);
    s5.appendChild(h('div', 'caveat', T.sec5Caveat));
    s5.dataset.sec = 'watch'; d.appendChild(s5);

    const lgw = h('div', 'sec');
    lgw.appendChild(h('div', 'sec-title', T.legendTitle));
    const lg = h('div', 'legend');
    lg.innerHTML = '<span>' + DOT + ' <i></i></span>'
      + '<span><b style="color:#8A8A8E">~</b> <i></i></span>'
      + '<span style="color:#226B8E">' + DIAMOND + '<i style="color:#6E6E73"> </i></span>';
    const li = lg.querySelectorAll('i');
    li[0].textContent = ' ' + T.legendMeasured;
    li[1].textContent = ' ' + T.legendAssume;
    li[2].textContent = ' ' + T.legendModel;
    lgw.dataset.sec = 'legend'; lgw.appendChild(lg); d.appendChild(lgw);


  }

  const CMP_MAX = 3;
  const CMP_SECS = ['head', 'price', 'living', 'cost', 'rent', 'watch'];
  const sameId = (a, b) => String(a) === String(b);

  async function metricFor(id) {
    const hit = [...(resultView().metrics || []), ...(state.compare || []), state.detailMetric].find(x => x && sameId(x.id, id));
    if (hit) return hit;
    const r = await fetch(`/api/property/${id}?lang=${state.lang}`, { credentials: 'same-origin' });
    if (!r.ok) throw new Error(String(r.status));
    return (await r.json()).metric;
  }

  function openCompare(list, { announce = false } = {}) {
    state.compare = list.slice(0, CMP_MAX);
    if (announce) state.detailToken = 'compare';
    state.selectedId = state.compare[0] ? state.compare[0].id : null;
    for (const c of document.querySelectorAll('.card')) c.classList.toggle('selected', state.compare.some(x => sameId(x.id, c.dataset.id)));
    clearMeasure();
    const wasOpen = $('#detail').classList.contains('open');
    renderCompare();
    if (!wasOpen) openSheet();
    if (announce) window.dispatchEvent(new CustomEvent('nw:detail', { detail: { open: true, id: 'compare' } }));
  }

  function compareButton(label) {
    const b = h('button', 'cmp-add', label); b.type = 'button';
    b.setAttribute('aria-haspopup', 'dialog'); b.setAttribute('aria-expanded', 'false');
    b.addEventListener('click', ev => { ev.stopPropagation(); togglePicker(b); });
    return b;
  }

  function summaryCells(list) {
    const rows = [
      { k: T.askingPrice, v: m => m.price, f: money },
      { k: T.modelEstimate, v: m => m.predicted_price, f: money, cls: 'model' },
      { k: T.cmpYield, v: m => m.gross_yield, f: v => pct(v), best: 'max' },
      { k: T.rowDuty, v: m => m.stamp_duty, f: money, best: 'min' },
      { k: kindZh('train_station'), v: m => (m.amenities || {}).train_station?.distance_m, f: dist, best: 'min' },
    ];
    const cells = list.map(() => { const c = h('div', 'sec cmp-cell cmp-sum'); c.dataset.sec = 'summary'; return c; });
    for (const r of rows) {
      const vals = list.map(m => { const v = r.v(m); return typeof v === 'number' && isFinite(v) ? v : null; });
      const known = vals.filter(v => v != null);
      const pick = r.best && list.length > 1 && known.length > 1 && new Set(known).size > 1
        ? (r.best === 'max' ? Math.max(...known) : Math.min(...known)) : null;
      vals.forEach((v, i) => {
        const e = kv(r.k, v == null ? '—' : r.f(v), r.cls);
        if (pick != null && v === pick) { e.classList.add('best'); e.querySelector('.v').appendChild(h('span', 'best-tag', T.cmpBest)); }
        cells[i].appendChild(e);
      });
    }
    return cells;
  }

  function markSame(groups) {
    const rowsOf = cell => [...cell.querySelectorAll('.kv, .pay:not(.editable), .row')].map(r => ({
      r, k: (r.querySelector('.k, .pk') || {}).textContent?.trim(), v: (r.querySelector('.v, .pv') || {}).textContent?.trim() }));
    for (const cells of groups) {
      if (cells.some(c => !c || c.classList.contains('cmp-missing'))) continue;
      const cols = cells.map(rowsOf);
      for (const { r, k, v } of cols[0]) {
        if (!k) continue;
        const peers = cols.slice(1).map(rows => rows.find(x => x.k === k));
        if (peers.every(p => p && p.v === v)) { r.classList.add('same'); peers.forEach(p => p.r.classList.add('same')); }
      }
      const rows0 = cols[0];
      if (rows0.length && rows0.every(x => x.r.classList.contains('same')) && !cells[0].querySelector('.plain, .evbox')) {
        cells.forEach(c => { c.classList.add('all-same'); c.querySelector('.sec-title')?.setAttribute('data-same-note', T.cmpAllSame); });
      }
    }
  }

  function renderCompare() {
    const d = $('#detail'), list = state.compare;
    closePicker();
    if (list.length <= 1) {
      if (list.length) openDetail(list[0], { keepToken: true }); else closeSheet();
      return;
    }
    const top = d.scrollTop;
    d.innerHTML = ''; d.classList.add('compare'); d.style.setProperty('--n', list.length);
    if (d.classList.contains('open')) dockDetail();
    d.appendChild(h('div', 'grabber'));
    const bar = h('div', 'd-bar cmp-bar');
    bar.appendChild(h('div', 'd-bar-title', T.cmpTitle(list.length)));
    const diff = h('button', 'cmp-diff' + (state.cmpDiff ? ' on' : ''), T.cmpDiffOnly); diff.type = 'button';
    diff.setAttribute('aria-pressed', String(!!state.cmpDiff));
    diff.addEventListener('click', () => { state.cmpDiff = !state.cmpDiff; renderCompare(); });
    bar.appendChild(diff);
    if (list.length < CMP_MAX) bar.appendChild(compareButton(T.cmpAddMore));
    const close = h('button', 'd-close'); close.type = 'button'; close.title = T.closeEsc;
    close.setAttribute('aria-label', T.close); close.innerHTML = X;
    close.addEventListener('click', () => closeSheet({ user: true }));
    bar.appendChild(close); d.appendChild(bar);
    makeDraggable(bar);

    const grid = h('div', 'cmp-grid' + (state.cmpDiff ? ' diff-only' : ''));
    const cols = list.map(m => { const c = document.createElement('div'); renderDetailInto(c, m, { compact: true }); return c; });
    list.forEach(m => {
      const hd = h('div', 'cmp-colhead');
      hd.appendChild(h('div', 'cmp-coladdr', (m.suburb || '') + ' · ' + (m.address || '')));
      const rm = h('button', 'cmp-remove'); rm.type = 'button'; rm.innerHTML = X;
      rm.title = T.cmpRemove; rm.setAttribute('aria-label', T.cmpRemove + ':' + (m.address || ''));
      rm.addEventListener('click', () => { state.compare = state.compare.filter(x => x !== m); renderCompare(); });
      hd.appendChild(rm); grid.appendChild(hd);
    });
    summaryCells(list).forEach(c => grid.appendChild(c));
    const groups = [];
    for (const key of CMP_SECS) {
      const cells = cols.map(c => c.querySelector(`:scope > [data-sec="${key}"]`));
      if (cells.every(x => !x)) continue;
      const placed = cells.map(x => x || h('div', 'sec cmp-missing', T.cmpMissing));
      placed.forEach(x => { x.classList.add('cmp-cell'); grid.appendChild(x); });
      groups.push(placed);
    }
    markSame(groups);
    const legend = cols[0].querySelector(':scope > [data-sec="legend"]');
    if (legend) { legend.classList.add('cmp-span'); grid.appendChild(legend); }
    d.appendChild(grid);
    d.scrollTop = top;
  }

  let picker = null;
  function closePicker() {
    if (!picker) return;
    picker.el.remove(); picker.btn.setAttribute('aria-expanded', 'false'); picker = null;
    document.removeEventListener('pointerdown', pickerOutside, true);
  }
  function pickerOutside(ev) { if (picker && !picker.el.contains(ev.target) && ev.target !== picker.btn) closePicker(); }
  function togglePicker(btn) {
    if (picker) { const same = picker.btn === btn; closePicker(); if (same) return; }
    const open = state.compare.length ? state.compare : [state.detailMetric].filter(Boolean);
    const taken = id => open.some(x => sameId(x.id, id));
    const el = h('div', 'cmp-picker'); el.setAttribute('role', 'dialog'); el.setAttribute('aria-label', T.cmpPickTitle);
    el.appendChild(h('div', 'cmp-pick-title', T.cmpPickTitle));
    const groups = [
      [T.cmpFromResults, (resultView().metrics || []).filter(m => !taken(m.id)).map(m => ({ id: m.id, suburb: m.suburb, address: m.address, price: m.price }))],
      [T.cmpFromFavs, ((window.nwFav && window.nwFav.list && window.nwFav.list()) || []).filter(it => !taken(it.id))
        .map(it => ({ id: it.id, suburb: it.snapshot.suburb, address: it.snapshot.address, price: it.snapshot.price }))],
    ];
    let any = false;
    for (const [title, items] of groups) {
      const seen = new Set(), uniq = items.filter(x => !seen.has(String(x.id)) && seen.add(String(x.id)));
      if (!uniq.length) continue;
      any = true;
      el.appendChild(h('div', 'cmp-pick-group', title));
      for (const it of uniq) {
        const b = h('button', 'cmp-pick-item'); b.type = 'button';
        b.appendChild(h('span', 'cmp-pick-addr', (it.suburb || '') + ' · ' + (it.address || '')));
        b.appendChild(h('span', 'cmp-pick-price', it.price != null ? money(it.price) : ''));
        b.addEventListener('click', async () => {
          b.disabled = true; b.classList.add('busy');
          try {
            const m = await metricFor(it.id);
            closePicker();
            openCompare([...open, m]);
          } catch (_) { b.disabled = false; b.classList.remove('busy'); b.title = T.cmpFailed; b.classList.add('failed'); }
        });
        el.appendChild(b);
      }
    }
    if (!any) el.appendChild(h('div', 'cmp-pick-empty', T.cmpNone));
    const d = $('#detail'); d.appendChild(el);
    const br = btn.getBoundingClientRect(), dr = d.getBoundingClientRect();
    el.style.top = (br.bottom - dr.top + d.scrollTop + 6) + 'px';
    el.style.right = Math.max(8, dr.right - br.right) + 'px';
    btn.setAttribute('aria-expanded', 'true');
    picker = { el, btn };
    setTimeout(() => document.addEventListener('pointerdown', pickerOutside, true), 0);
  }

  async function compareIds(ids) {
    const list = [];
    for (const id of ids.slice(0, CMP_MAX)) {
      try { list.push(await metricFor(id)); }
      catch (err) { console.warn('compare: skipped', id, err); }
    }
    if (list.length >= 2) openCompare(list, { announce: true });
    else if (list.length === 1) openDetail(list[0]);
    return list.length;
  }

  function dockDetail() {
    const d = $('#detail'), chat = $('.col-chat'), res = $('.col-results');
    const on = twoColumn() && $('#app').dataset.stage === 'working' && chat && chat.offsetWidth > 0;
    d.classList.toggle('docked', !!on);
    if (!on) return false;
    const c = chat.getBoundingClientRect();
    let width = c.width;
    if (d.classList.contains('compare') && res) {
      const n = state.compare.length || 2;
      const span = detailIsSheet() ? c.width : res.getBoundingClientRect().right - c.left;
      width = Math.min(Math.max(span, n * 320 + 48), innerWidth - c.left - 16);
    }
    d.style.setProperty('--dl', c.left + 'px');
    d.style.setProperty('--dt', c.top + 'px');
    d.style.setProperty('--dw', width + 'px');
    d.style.setProperty('--dh', Math.max(240, innerHeight - c.top - 16) + 'px');
    return true;
  }
  addEventListener('resize', () => { if ($('#detail').classList.contains('open')) dockDetail(); });

  function openSheet() {
    const d = $('#detail');
    const docked = dockDetail();
    d.classList.add('open');
    if (detailIsSheet() && !docked) { $('#backdrop').classList.add('is-open'); $('#backdrop').onclick = () => closeSheet({ user: true }); }
    d.scrollTop = 0;
  }
  function closeSheet({ user = false } = {}) {
    const wasOpen = $('#detail').classList.contains('open');
    $('#detail').classList.remove('open'); $('#backdrop').classList.remove('is-open');
    closePicker();
    if (wasOpen) window.dispatchEvent(new CustomEvent('nw:detail', { detail: { open: false, id: state.detailToken ?? state.selectedId, user: user === true } }));
    if (state.measureBounds && state.map) {
      clearTimeout(state.focusTimer);
      state.focusTimer = setTimeout(() => {
        if (state.measureBounds && state.map) fitAvoidingPanel(state.map, state.measureBounds);
      }, 400);
    }
  }


  const drift = { cycle: null, timers: new Set(), paused: false, lastSlot: -1, bag: [], size: '', anchors: [] };
  const driftNarrow = matchMedia('(max-width: 599px)');
  const driftReduced = matchMedia('(prefers-reduced-motion: reduce)');
  const driftPointer = matchMedia('(hover: hover) and (pointer: fine)');

  function driftLater(fn, delay) {
    const id = setTimeout(() => { drift.timers.delete(id); fn(); }, delay);
    drift.timers.add(id);
  }

  function stopDrift() {
    clearTimeout(drift.cycle);
    drift.cycle = null;
    drift.timers.forEach(clearTimeout);
    drift.timers.clear();
    document.querySelectorAll('.drift-slot').forEach(slot => {
      slot.style.opacity = '';
      slot.style.transform = '';
    });
  }

  function driftCanRotate() {
    const host = $('#driftfield');
    if ($('#app').dataset.showroom === 'ready' && matchMedia('(min-width: 900px)').matches) return false;
    return host && $('#app').dataset.stage === 'welcome' && !document.hidden &&
      !drift.paused && !(driftPointer.matches && host.querySelector('.drift-chip:hover')) &&
      !host.contains(document.activeElement) && !$('#input').value.trim();
  }

  function animateDrift(slot, entering, done = () => {}) {
    const reduced = driftReduced.matches;
    const duration = reduced ? 220 : entering ? 1200 : 900;
    const start = performance.now();
    function frame() {
      const t = Math.min(1, (performance.now() - start) / duration);
      slot.style.opacity = String(entering ? t : 1 - t);
      const travel = entering ? Math.pow(1 - t, 3) : t * t * t;
      slot.style.transform = reduced ? '' :
        'translate3d(0,' + ((entering ? 8 : -5) * travel) + 'px,0) scale(' + (1 - .03 * travel) + ')';
      if (t < 1) driftLater(frame, 32);
      else {
        if (entering) { slot.style.opacity = ''; slot.style.transform = ''; }
        done();
      }
    }
    frame();
  }

  function shuffleDrift(items) {
    const result = items.slice();
    for (let i = result.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [result[i], result[j]] = [result[j], result[i]];
    }
    return result;
  }

  function fillDriftSlot(slot, text) {
    slot.dataset.query = text;
    const btn = h('button', 'drift-chip');
    btn.type = 'button';
    btn.setAttribute('aria-label', text);
    const parts = text.split(' · ');
    btn.appendChild(h('span', 'dc-l1', parts[0]));
    if (parts.length > 1) btn.appendChild(h('span', 'dc-l2', parts.slice(1).join(' · ')));
    btn.addEventListener('click', () => send(text));
    slot.replaceChildren(btn);
  }

  function driftRect(slot) {
    return { x: slot.offsetLeft, y: slot.offsetTop, w: slot.offsetWidth, h: slot.offsetHeight };
  }

  function driftAvoidRects(host) {
    const base = host.getBoundingClientRect();
    return Array.from(document.querySelectorAll('#app [data-drift-avoid]'))
      .filter(el => el.offsetParent !== null)
      .map(el => el.getBoundingClientRect())
      .map(r => ({ x: r.left - base.left, y: r.top - base.top, w: r.width, h: r.height }));
  }

  function cityRandom(seed) {
    return () => {
      seed |= 0; seed = seed + 0x6D2B79F5 | 0;
      let t = Math.imul(seed ^ seed >>> 15, 1 | seed);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    };
  }

  const CITY = (() => {
    const rnd = cityRandom(20260914), towers = [];
    for (let gy = 0; gy < 7; gy++) for (let gx = 0; gx < 7; gx++) {
      if (rnd() < .4) continue;
      const fin = rnd() < .5, back = Math.max(0, 10 - gx - gy);
      towers.push({
        x: gx + rnd() * .25, y: gy + rnd() * .25,
        w: fin ? .16 + rnd() * .12 : .55 + rnd() * .3,
        d: fin ? .8 + rnd() * .8 : .55 + rnd() * .3,
        h: 1.6 + rnd() * 2.2 + back * (.75 + rnd() * .35),
        lit: rnd() < .24,
      });
    }
    towers.sort((a, b) => (a.x + a.w / 2 + a.y + a.d / 2) - (b.x + b.w / 2 + b.y + b.d / 2));
    return towers;
  })();

  const ISO_C = Math.cos(Math.PI / 6), ISO_S = .5;

  function drawCity() {
    const svg = $('#city-svg'), host = $('#driftfield');
    if (!svg || !host || !host.clientWidth) return;
    const W = host.clientWidth, H = host.clientHeight, narrow = driftNarrow.matches;
    const iso = (x, y, z) => [(x - y) * ISO_C, (x + y) * ISO_S - z];
    let minX = Infinity, maxX = -Infinity, maxY = -Infinity;
    CITY.forEach(t => [[t.x, t.y], [t.x + t.w, t.y], [t.x + t.w, t.y + t.d], [t.x, t.y + t.d]].forEach(([x, y]) => {
      const [sx, sy] = iso(x, y, 0);
      minX = Math.min(minX, sx); maxX = Math.max(maxX, sx); maxY = Math.max(maxY, sy);
    }));
    const k = W * (narrow ? 1.02 : 1.08) / (maxX - minX);
    const tx = W * (narrow ? .5 : .56) - (minX + maxX) / 2 * k, ty = H * .97 - maxY * k;
    const P = (x, y, z) => { const [sx, sy] = iso(x, y, z); return [tx + sx * k, ty + sy * k]; };
    const pts = arr => arr.map(p => p[0].toFixed(1) + ',' + p[1].toFixed(1)).join(' ');
    const line = (a, b, cls) => '<line class="' + cls + '" x1="' + a[0].toFixed(1) + '" y1="' + a[1].toFixed(1) +
      '" x2="' + b[0].toFixed(1) + '" y2="' + b[1].toFixed(1) + '"></line>';

    let out = '<defs>' +
      '<linearGradient id="cg-face" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#FFFFFF" stop-opacity=".58"/>' +
        '<stop offset=".55" stop-color="#FFFFFF" stop-opacity=".22"/><stop offset="1" stop-color="#FBF7EF" stop-opacity=".06"/></linearGradient>' +
      '<linearGradient id="cg-side" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#DCD3C4" stop-opacity=".62"/>' +
        '<stop offset="1" stop-color="#D8CFBF" stop-opacity=".12"/></linearGradient>' +
      '<linearGradient id="cg-lit" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#FFF6E6" stop-opacity=".7"/>' +
        '<stop offset=".5" stop-color="#F4DDB6" stop-opacity=".42"/><stop offset="1" stop-color="#EFD3A4" stop-opacity=".1"/></linearGradient>' +
      '<radialGradient id="cg-glow"><stop offset="0" stop-color="#F2D6A6" stop-opacity=".38"/>' +
        '<stop offset=".45" stop-color="#F3DFBF" stop-opacity=".16"/><stop offset="1" stop-color="#F5EBDD" stop-opacity="0"/></radialGradient>' +
      '<filter id="cf-soft" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="' + (narrow ? 10 : 16) + '"/></filter>' +
      '<linearGradient id="cg-base" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#D9D1C3" stop-opacity="0"/>' +
        '<stop offset=".55" stop-color="#D6CDBD" stop-opacity=".2"/><stop offset="1" stop-color="#CFC5B3" stop-opacity=".34"/></linearGradient>' +
      '<linearGradient id="cg-mist" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#F4F1EB" stop-opacity="0"/>' +
        '<stop offset="1" stop-color="#F4F1EB" stop-opacity=".92"/></linearGradient>' +
      '</defs>';
    out += '<rect fill="url(#cg-base)" width="' + W + '" height="' + H + '"></rect>';
    out += '<ellipse fill="url(#cg-glow)" cx="' + (W * .5).toFixed(0) + '" cy="' + (H * .46).toFixed(0) +
      '" rx="' + (W * .46).toFixed(0) + '" ry="' + (H * .5).toFixed(0) + '"></ellipse>';
    const anchors = [];
    CITY.forEach((t, order) => {
      const A0 = P(t.x, t.y, 0), B0 = P(t.x + t.w, t.y, 0), C0 = P(t.x + t.w, t.y + t.d, 0), D0 = P(t.x, t.y + t.d, 0);
      const A1 = P(t.x, t.y, t.h), B1 = P(t.x + t.w, t.y, t.h), C1 = P(t.x + t.w, t.y + t.d, t.h), D1 = P(t.x, t.y + t.d, t.h);
      out += '<g class="tw' + (t.lit ? ' lit' : '') + '" opacity="' + (.55 + .45 * order / (CITY.length - 1)).toFixed(2) + '">';
      out += line(A0, A1, 'e-back') + line(A1, B1, 'e-back') + line(A1, D1, 'e-back');
      out += '<polygon class="f-left" fill="url(#' + (t.lit ? 'cg-lit' : 'cg-face') + ')" points="' + pts([D0, C0, C1, D1]) + '"></polygon>';
      out += '<polygon class="f-right" fill="url(#cg-side)" points="' + pts([B0, C0, C1, B1]) + '"></polygon>';
      out += '<polygon class="f-top" points="' + pts([A1, B1, C1, D1]) + '"></polygon>';
      const faceW = Math.hypot(C0[0] - D0[0], C0[1] - D0[1]);
      const on = (u, v) => [D0[0] + (C0[0] - D0[0]) * u, D0[1] + (C0[1] - D0[1]) * u + (D1[1] - D0[1]) * v];
      if (faceW > 26) {
        const u = .08 + ((t.x * 7 + t.y * 3) % 1) * .4;
        out += '<polygon class="refl" points="' + pts([on(u, .18), on(Math.min(1, u + .14), .18),
          on(Math.min(1, u + .44), .92), on(Math.min(1, u + .3), .92)]) + '"></polygon>';
      }
      const n = Math.min(9, Math.floor(faceW / 11));
      for (let i = 1; i < n; i++) {
        const f = i / n, lerp = (a, b) => [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
        out += line(lerp(D0, C0), lerp(D1, C1), 'mull');
      }
      const inset = (a, b, f) => [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
      const th = Math.min(.12, 4 / Math.max(faceW, 1));
      out += line(inset(C0, D0, th), inset(C1, D1, th), 'e-inner');
      out += line(inset(C0, B0, .1), inset(C1, B1, .1), 'e-shade');
      out += line(B0, B1, 'e-side') + line(D0, D1, 'e-side') + line(C0, C1, 'e-front');
      out += '<polyline class="e-top" points="' + pts([D1, C1, B1]) + '"></polyline>';
      out += '</g>';
      [[D0, C0, D1, C1], [B0, C0, B1, C1]].forEach(([g0, g1, u0, u1]) => {
        [.42, .6, .78].forEach(zf => {
          const bx = (g0[0] + g1[0]) / 2, by = (g0[1] + g1[1]) / 2, ux = (u0[0] + u1[0]) / 2, uy = (u0[1] + u1[1]) / 2;
          anchors.push({ x: bx + (ux - bx) * zf, y: by + (uy - by) * zf });
        });
      });
    });
    out += '<rect class="shaft" filter="url(#cf-soft)" x="' + (W * .47).toFixed(0) + '" y="' + (H * .05).toFixed(0) +
      '" width="' + (narrow ? 10 : 18) + '" height="' + (H * .7).toFixed(0) + '"></rect>';
    out += '<rect class="shaft dim" filter="url(#cf-soft)" x="' + (W * .63).toFixed(0) + '" y="' + (H * .18).toFixed(0) +
      '" width="' + (narrow ? 8 : 12) + '" height="' + (H * .55).toFixed(0) + '"></rect>';
    out += '<rect fill="url(#cg-mist)" y="' + (H * .72).toFixed(0) + '" width="' + W + '" height="' + (H * .28).toFixed(0) + '"></rect>';
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    svg.innerHTML = out;
    drift.anchors = anchors;
  }

  function findDriftPosition(slot, previous = null) {
    const host = $('#driftfield');
    const W = host.clientWidth, H = host.clientHeight, w = slot.offsetWidth, ht = slot.offsetHeight;
    const pad = 12, gap = driftNarrow.matches ? 10 : 16;
    const occupied = Array.from(host.children)
      .filter(other => other !== slot && other.dataset.placed === 'true').map(driftRect);
    const blocked = occupied.concat(driftAvoidRects(host));
    const minX = driftNarrow.matches ? pad : W * .22;
    const candidates = [];
    for (const a of shuffleDrift(drift.anchors)) {
      const x = a.x - w * .35, y = a.y - ht / 2;
      if (x < minX || y < pad || x + w > W - pad || y + ht > H - pad) continue;
      if (blocked.some(r => x < r.x + r.w + gap && x + w + gap > r.x &&
        y < r.y + r.h + gap && y + ht + gap > r.y)) continue;
      if (previous && Math.hypot(x - previous.x, y - previous.y) < Math.min(90, W * .18)) continue;
      const spread = occupied.length ? Math.min(...occupied.map(r =>
        Math.hypot((x + w / 2 - r.x - r.w / 2) / W, (y + ht / 2 - r.y - r.h / 2) / H))) : Math.random();
      candidates.push({ x, y, spread });
    }
    candidates.sort((a, b) => b.spread - a.spread);
    return candidates[Math.floor(Math.random() * Math.min(6, candidates.length))] || null;
  }

  function placeDriftSlot(slot, position) {
    slot.style.left = Math.round(position.x) + 'px';
    slot.style.top = Math.round(position.y) + 'px';
    slot.dataset.placed = 'true';
  }

  function layoutDrift() {
    const host = $('#driftfield');
    if (!host.clientWidth) return;
    let slots = Array.from(host.children);
    let fitted = false;
    for (;;) {
      const largestFirst = slots.slice().sort((a, b) =>
        b.offsetWidth * b.offsetHeight - a.offsetWidth * a.offsetHeight);
      for (let attempt = 0; attempt < 12 && !fitted; attempt++) {
        slots.forEach(slot => { slot.dataset.placed = 'false'; });
        fitted = largestFirst.every(slot => {
          const position = findDriftPosition(slot);
          if (!position) return false;
          placeDriftSlot(slot, position);
          return true;
        });
      }
      if (fitted || !slots.length) break;
      const drop = slots.pop();
      drift.bag.push(drop.dataset.query);
      drop.remove();
    }
    drift.size = host.clientWidth + ':' + host.clientHeight;
  }

  function scheduleDrift() {
    clearTimeout(drift.cycle);
    drift.cycle = null;
    if (!driftCanRotate()) return;
    drift.cycle = setTimeout(rotateDrift, (driftReduced.matches ? 5000 : 1800) + Math.random() * 1400);
  }

  function rotateDrift() {
    drift.cycle = null;
    if (!driftCanRotate()) return;
    const slots = Array.from($('#driftfield').children);
    const visible = new Set(slots.map(slot => slot.dataset.query));
    drift.bag = drift.bag.filter(text => !visible.has(text));
    if (!drift.bag.length) drift.bag = shuffleDrift(T.chips.filter(text => !visible.has(text)));
    if (!drift.bag.length || !slots.length) return;
    const choices = slots.map((_, i) => i).filter(i => i !== drift.lastSlot || slots.length === 1);
    const index = choices[Math.floor(Math.random() * choices.length)];
    const slot = slots[index];
    const previous = driftRect(slot);
    animateDrift(slot, false, () => {
      driftLater(() => {
        if (!driftCanRotate()) { stopDrift(); return; }
        const oldText = slot.dataset.query;
        fillDriftSlot(slot, drift.bag[0]);
        const position = findDriftPosition(slot, previous);
        if (position) {
          placeDriftSlot(slot, position);
          drift.bag.shift();
        } else {
          fillDriftSlot(slot, oldText);
        }
        drift.lastSlot = index;
        animateDrift(slot, true);
        scheduleDrift();
      }, driftReduced.matches ? 80 : 180);
    });
  }

  function renderDrift() {
    stopDrift();
    const host = $('#driftfield');
    if (!host) return;
    host.replaceChildren();
    if ($('#app').dataset.stage !== 'welcome') return;
    const pool = shuffleDrift(T.chips || []);
    const count = Math.min(driftNarrow.matches ? 3 : 4, pool.length);
    drift.bag = pool.slice(count);
    drift.lastSlot = -1;
    pool.slice(0, count).forEach(text => {
      const slot = h('div', 'drift-slot');
      fillDriftSlot(slot, text);
      host.appendChild(slot);
    });
    drawCity();
    layoutDrift();
    if (driftCanRotate()) Array.from(host.children).forEach(slot => animateDrift(slot, true));
    scheduleDrift();
  }

  function updateDriftControl() {
    const btn = $('#btn-drift');
    btn.textContent = drift.paused ? T.driftResume : T.driftPause;
    btn.setAttribute('aria-pressed', String(drift.paused));
  }

  driftNarrow.addEventListener('change', renderDrift);
  driftReduced.addEventListener('change', () => { stopDrift(); scheduleDrift(); });
  document.addEventListener('visibilitychange', () => { stopDrift(); scheduleDrift(); });
  new ResizeObserver(() => {
    const host = $('#driftfield');
    const size = host.clientWidth + ':' + host.clientHeight;
    if ($('#app').dataset.stage !== 'welcome' || size === drift.size) return;
    stopDrift();
    drawCity();
    layoutDrift();
    scheduleDrift();
  }).observe($('#driftfield'));


  function applyStaticText() {
    document.documentElement.lang = T.htmlLang;
    document.title = T.title;
    const set = (sel, text) => { const e = $(sel); if (e) e.textContent = text; };
    set('.brand-name', T.brandName);
    $('#brand-home').setAttribute('aria-label', `${T.brand} · ${T.brandHome}`);
    $('#brand-home').title = T.brandHome;
    set('#btn-new', T.btnNew);
    const wh = $('.welcome-h'); if (wh) wh.innerHTML = T.welcomeH;
    (T.rail || []).forEach((label, i) => set('#rail-' + (i + 1), label));
    set('#hero-foot', T.heroFoot);
    $('#city-btn').setAttribute('aria-label', T.cityPick); $('#city-btn').title = T.cityPick;
    $('.map-ctl').setAttribute('aria-label', T.mapControls);
    for (const [id, k] of [['#map-fit', 'mapFit'], ['#map-zin', 'zoomIn'], ['#map-zout', 'zoomOut']]) { $(id).setAttribute('aria-label', T[k]); $(id).title = T[k]; }
    if (!$('#city-menu').hidden) renderCityMenu();
    if ($('#map-count').textContent) setMapCount(resultView().metrics.length);
    set('#drift-label', T.driftLabel);
    updateDriftControl();
    const wp = $('.welcome-p'); if (wp) wp.innerHTML = T.welcomeP;
    renderDrift();
    const inp = $('#input'); if (inp) { inp.placeholder = $('#app').dataset.stage === 'working' ? T.workingPlaceholder : T.inputPlaceholder; inp.setAttribute('aria-label', inp.placeholder); }
    $('#chat').setAttribute('role', 'region'); $('#chat').setAttribute('aria-label', T.conversationAria); $('#chat').tabIndex = 0;
    $('#condition-dock').setAttribute('role', 'region'); $('#condition-dock').setAttribute('aria-label', T.filterAria);
    const followUps = $('#follow-ups'); followUps.replaceChildren(h('span', 'follow-label', T.tryFollowUp + ':'));
    for (const text of T.followUps) {
      const b = h('button', 'follow-up', text); b.type = 'button';
      b.addEventListener('click', () => { inp.value = inp.value.trim() ? inp.value.trim() + (state.lang === 'zh' ? '，' : ', ') + text : text; inp.focus(); });
      followUps.appendChild(b);
    }
    const snd = $('#composer .send'); if (snd) snd.setAttribute('aria-label', T.sendAria);
    const det = $('#detail'); if (det) det.setAttribute('aria-label', T.detailAria);
    const lb = $('#btn-lang'); if (lb) { lb.textContent = T.switchTo; lb.title = T.switchTitle; }
  }

  async function setLang(lang) {
    if (lang === state.lang || state.running) return;
    returnToLatest();
    state.lang = lang;
    T = window.I18N[lang] || window.I18N.zh;
    try { localStorage.setItem('lang', lang); } catch (_) {  }
    applyStaticText();
    window.dispatchEvent(new CustomEvent('nw:lang', { detail: lang }));
    state.meta = await fetch('/api/meta?lang=' + lang).then(r => r.json());
    if (state.params) renderConditions();
    if (state.metrics.length) renderResults(); else renderResultsHead();
    if (state.params && state.metrics.length) refine(state.params, T.statusRefine);
  }

  let metaReady = null;

  async function init() {
    try { state.lang = localStorage.getItem('lang') === 'zh' ? 'zh' : 'en'; } catch (_) {  }
    T = window.I18N[state.lang];
    initMapPanel();
    applyStaticText();
    metaReady = fetch('/api/meta?lang=' + state.lang).then(r => r.json()).then(j => { state.meta = j; });
    const saved = savedSession();
    if (saved) state.threadId = saved.threadId; else newThread();
    $('#composer').addEventListener('submit', ev => { ev.preventDefault(); const t = $('#input').value.trim(); if (!t) return; $('#input').value = ''; send(t); });
    window.addEventListener('nw:query', e => { if (state.running || !e.detail) return; $('#input').value = ''; send(e.detail); });
    window.addEventListener('nw:showroom', stopDrift);
    $('#btn-new').addEventListener('click', resetAll);
    $('#brand-home').addEventListener('click', () => { if ($('#app').dataset.stage !== 'welcome') resetAll(); });
    window.nwOpenDetail = m => openDetail(m);
    window.nwCompareIds = compareIds;
    $('#btn-lang').addEventListener('click', () => setLang(state.lang === 'zh' ? 'en' : 'zh'));
    $('#btn-drift').addEventListener('click', () => {
      drift.paused = !drift.paused;
      updateDriftControl();
      stopDrift();
      scheduleDrift();
    });
    const driftHost = $('#driftfield');
    driftHost.addEventListener('pointerover', e => {
      if (driftPointer.matches && e.target.closest('.drift-chip')) stopDrift();
    });
    driftHost.addEventListener('pointerout', e => {
      if (driftPointer.matches && e.target.closest('.drift-chip') &&
        !e.target.closest('.drift-chip').contains(e.relatedTarget)) scheduleDrift();
    });
    driftHost.addEventListener('focusin', stopDrift);
    driftHost.addEventListener('focusout', () => driftLater(scheduleDrift, 0));
    $('#input').addEventListener('input', () => { stopDrift(); scheduleDrift(); });
    if (saved) restoreSession(saved);
    document.fonts.ready.then(() => {
      if ($('#app').dataset.stage !== 'welcome') return;
      stopDrift();
      layoutDrift();
      scheduleDrift();
    });
    document.addEventListener('keydown', ev => { if (ev.key !== 'Escape') return; if (closeCityMenu()) return; if (picker) { closePicker(); return; } closePopover(); closeSheet({ user: true }); });
    await metaReady;
  }
  init();
})();
