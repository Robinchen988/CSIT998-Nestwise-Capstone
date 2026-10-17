(() => {
  const $ = s => document.querySelector(s);
  const favBtn = $('#btn-favs'), dlg = $('#favs'), list = $('#favs-list'), empty = $('#favs-empty');
  if (!favBtn || !dlg) return;
  const ids = new Set();
  let items = [], pending = null, loaded = false;
  let lang = (() => { try { return localStorage.getItem('lang') === 'zh' ? 'zh' : 'en'; } catch (_) { return 'en'; } })();
  const T = () => (window.I18N[lang] || window.I18N.zh).fav;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const user = () => window.nwAuth && window.nwAuth.user;
  const HEART = '<svg class="fav-icon" viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path d="M12 20.5s-7.5-4.6-9.3-9.2C1.4 8 3.3 4.6 6.8 4.6c2.1 0 3.6 1.2 4.4 2.5.8-1.3 2.3-2.5 4.4-2.5 3.5 0 5.4 3.4 4.1 6.7-1.8 4.6-9.3 9.2-9.3 9.2z"/></svg>';
  const money = v => new Intl.NumberFormat('en-AU', { style: 'currency', currency: 'AUD', maximumFractionDigits: 0 }).format(v);

  function button(m) {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'fav'; b.dataset.favId = m.id;
    b.innerHTML = HEART;
    paint(b);
    b.addEventListener('click', e => { e.stopPropagation(); toggle(m, b); });
    b.addEventListener('keydown', e => e.stopPropagation());
    return b;
  }
  function paint(b) {
    const on = ids.has(Number(b.dataset.favId));
    b.classList.toggle('on', on);
    b.setAttribute('aria-pressed', String(on));
    b.setAttribute('aria-label', on ? T().remove : T().add);
    b.title = on ? T().remove : T().add;
  }
  const paintAll = () => document.querySelectorAll('.fav').forEach(paint);

  function burst(b) {
    if (reduced.matches) return;
    b.classList.remove('pop'); void b.offsetWidth; b.classList.add('pop');
    const fx = document.createElement('span'); fx.className = 'fav-burst'; fx.setAttribute('aria-hidden', 'true');
    fx.innerHTML = '<i class="ring"></i>' + Array.from({ length: 8 }, (_, i) => `<i class="dot" style="--a:${i * 45}deg"></i>`).join('');
    b.appendChild(fx);
    setTimeout(() => { fx.remove(); b.classList.remove('pop'); }, 1000);
    favBtn.classList.remove('bump'); void favBtn.offsetWidth; favBtn.classList.add('bump');
    setTimeout(() => favBtn.classList.remove('bump'), 500);
  }

  async function toggle(m, b) {
    if (!user()) {
      pending = m;
      window.nwAuth && window.nwAuth.open('login');
      return;
    }
    const id = Number(m.id), on = !ids.has(id);
    on ? ids.add(id) : ids.delete(id);
    paintAll(); count();
    if (on && b) burst(b);
    try {
      const r = await fetch('/api/favorites/' + id, { method: on ? 'PUT' : 'DELETE', credentials: 'same-origin' });
      if (!r.ok) throw new Error(String(r.status));
      loaded = false;
    } catch (err) {
      on ? ids.delete(id) : ids.add(id);
      paintAll(); count();
      if (String(err.message) === '401') window.nwAuth && window.nwAuth.open('login');
      else if (b) b.title = T().failed;
    }
  }

  async function load() {
    const r = await fetch('/api/favorites', { credentials: 'same-origin' });
    if (!r.ok) throw new Error(String(r.status));
    items = (await r.json()).items || [];
    ids.clear(); items.forEach(it => ids.add(Number(it.id)));
    loaded = true; paintAll(); count();
  }
  function count() {
    favBtn.querySelector('.favs-count').textContent = String(ids.size);
    favBtn.classList.toggle('has', ids.size > 0);
    $('#favs-total').textContent = ids.size ? T().total(ids.size) : '';
  }

  window.addEventListener('nw:auth', async e => {
    const u = e.detail;
    favBtn.hidden = !u;
    if (!u) { ids.clear(); items = []; loaded = false; paintAll(); count(); close(); return; }
    try { await load(); } catch (_) {  }
    if (pending) {
      const m = pending; pending = null;
      if (!ids.has(Number(m.id))) toggle(m, document.querySelector(`.fav[data-fav-id="${m.id}"]`));
    }
  });
  const authDlg = $('#auth');
  if (authDlg) authDlg.addEventListener('close', () => setTimeout(() => { if (!user()) pending = null; }, 0));

  const SEARCH_AT = 20;
  const collapsed = new Set();
  let sortMode = (() => { try { return localStorage.getItem('favSort') || 'recent'; } catch (_) { return 'recent'; } })();
  const short = v => (v >= 1e6 || Math.round(v / 1e3) >= 1000) ? '$' + (v / 1e6).toFixed(2).replace(/\.?0+$/, '') + 'M' : '$' + Math.round(v / 1e3) + 'k';
  const byPrice = (a, b) => (a.snapshot.price ?? Infinity) - (b.snapshot.price ?? Infinity);

  function row(it, { inGroup = false } = {}) {
    const s = it.snapshot || {}, t = T();
    const li = document.createElement('li'); li.className = 'favs-item';
    const main = document.createElement('button'); main.type = 'button'; main.className = 'favs-main';
    li.classList.add('can-open');
    main.setAttribute('aria-label', `${t.view}:${[s.suburb, s.address].filter(Boolean).join(' ')}`);
    if (picked.has(Number(it.id))) li.classList.add('picked');
    if (selecting) main.setAttribute('aria-pressed', String(picked.has(Number(it.id))));
    main.addEventListener('click', () => selecting ? togglePick(it, li, main) : openDetail(it, li, main));
    const addr = document.createElement('div'); addr.className = 'favs-addr';
    if (!inGroup && s.suburb) { const b = document.createElement('b'); b.textContent = s.suburb; addr.append(b, ' · '); }
    addr.append(s.address || s.suburb || '');
    const line = document.createElement('div'); line.className = 'favs-line';
    const meta = document.createElement('span'); meta.className = 'favs-meta';
    meta.textContent = [s.bedrooms != null && t.beds(s.bedrooms), s.bathrooms != null && t.baths(s.bathrooms),
      t.types[s.property_type] || s.property_type].filter(Boolean).join(' · ');
    const price = document.createElement('span'); price.className = 'favs-price';
    const lab = document.createElement('i'); lab.textContent = t.price;
    const val = document.createElement('b'); val.textContent = s.price != null ? money(s.price) : '—';
    price.append(lab, val);
    line.append(meta, price);
    main.append(addr, line);
    const rm = document.createElement('button'); rm.type = 'button'; rm.className = 'fav on'; rm.innerHTML = HEART;
    rm.dataset.favId = it.id; rm.setAttribute('aria-label', t.remove); rm.title = t.remove; rm.setAttribute('aria-pressed', 'true');
    rm.addEventListener('click', async () => {
      await toggle({ id: it.id }, null);
      if (!ids.has(Number(it.id))) { items = items.filter(x => x.id !== it.id); render(); }
    });
    li.append(main, rm);
    return li;
  }

  let expectId = null, returnTo = null, lastScroll = 0;
  window.addEventListener('nw:detail', e => {
    const { open: opened, id, user: byUser } = e.detail || {};
    if (opened) { returnTo = expectId != null && String(id) === String(expectId) ? expectId : null; expectId = null; return; }
    const back = byUser && returnTo != null && String(id) === String(returnTo) && user();
    const rid = returnTo; returnTo = null;
    if (back) setTimeout(() => reopen(rid), 0);
  });
  async function reopen(id) {
    await open();
    list.scrollTop = lastScroll;
    const row = list.querySelector(`.fav[data-fav-id="${id}"]`)?.closest('.favs-item');
    if (!row) return;
    row.classList.add('just-viewed');
    row.querySelector('.favs-main').focus({ preventScroll: true });
    setTimeout(() => row.classList.remove('just-viewed'), 1600);
  }

  let selecting = false;
  const picked = new Set();
  function setSelecting(on) {
    selecting = on; if (!on) picked.clear();
    dlg.classList.toggle('selecting', on);
    $('#favs-cmp').setAttribute('aria-pressed', String(on));
    render();
  }
  function togglePick(it, li, main) {
    const id = Number(it.id);
    if (picked.has(id)) picked.delete(id);
    else if (picked.size < 3) picked.add(id);
    li.classList.toggle('picked', picked.has(id));
    main.setAttribute('aria-pressed', String(picked.has(id)));
    actions();
  }
  function actions() {
    const t = T(), go = $('#favs-cmp-go');
    $('#favs-actions').hidden = !selecting;
    go.textContent = t.compareGo(picked.size); go.disabled = picked.size < 2;
    $('#favs-actions-hint').textContent = t.compareHint;
    $('#favs-cmp-cancel').textContent = t.cancel;
    $('#favs-cmp').textContent = t.compare;
  }
  $('#favs-cmp').addEventListener('click', () => setSelecting(!selecting));
  $('#favs-cmp-cancel').addEventListener('click', () => setSelecting(false));
  $('#favs-cmp-go').addEventListener('click', async () => {
    if (picked.size < 2 || !window.nwCompareIds) return;
    const ids = [...picked];
    lastScroll = list.scrollTop; expectId = 'compare';
    setSelecting(false); close();
    const n = await window.nwCompareIds(ids);
    if (n < 2) { expectId = null; await open(); $('#favs-actions-hint').textContent = T().compareFailed; }
  });

  async function openDetail(it, li, main) {
    lastScroll = list.scrollTop;
    const card = document.querySelector(`#results .card[data-id="${it.id}"]`);
    if (card) { expectId = Number(it.id); close(); card.scrollIntoView({ block: 'center' }); card.click(); return; }
    if (li.classList.contains('busy')) return;
    li.classList.add('busy'); main.setAttribute('aria-busy', 'true');
    const note = li.querySelector('.favs-note') || li.querySelector('.favs-main').appendChild(Object.assign(document.createElement('span'), { className: 'favs-note' }));
    note.textContent = T().loading;
    try {
      const r = await fetch(`/api/property/${it.id}?lang=${lang}`, { credentials: 'same-origin' });
      if (r.status === 404) { note.textContent = T().gone; li.classList.add('gone'); return; }
      if (!r.ok) throw new Error(String(r.status));
      const { metric } = await r.json();
      note.remove();
      expectId = Number(it.id);
      close();
      window.nwOpenDetail && window.nwOpenDetail(metric);
    } catch (_) {
      note.textContent = T().detailFailed;
    } finally {
      li.classList.remove('busy'); main.removeAttribute('aria-busy');
    }
  }

  function group(name, rows) {
    const t = T(), li = document.createElement('li'); li.className = 'favs-group';
    const head = document.createElement('button'); head.type = 'button'; head.className = 'favs-ghead';
    const open = !collapsed.has(name);
    head.setAttribute('aria-expanded', String(open));
    const prices = rows.map(r => r.snapshot.price).filter(v => v != null);
    const lo = Math.min(...prices), hi = Math.max(...prices);
    const range = !prices.length ? '' : lo === hi ? short(lo) : `${short(lo)}–${short(hi)}`;
    head.innerHTML = '<svg class="favs-chev" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg><b></b><span class="favs-gcount"></span><span class="favs-grange"></span>';
    head.querySelector('b').textContent = name;
    head.querySelector('.favs-gcount').textContent = t.groupCount(rows.length);
    head.querySelector('.favs-grange').textContent = range ? `${t.price} ${range}` : '';
    const sub = document.createElement('ul'); sub.className = 'favs-sub'; sub.hidden = !open;
    rows.forEach(r => sub.appendChild(row(r, { inGroup: true })));
    head.addEventListener('click', () => { collapsed.has(name) ? collapsed.delete(name) : collapsed.add(name); render(); });
    li.append(head, sub);
    return li;
  }

  function render() {
    const t = T();
    $('#favs-title').textContent = t.title;
    $('#favs-close').setAttribute('aria-label', t.close);
    empty.querySelector('.favs-empty-t').textContent = t.empty;
    empty.querySelector('.favs-empty-h').textContent = t.emptyHint;
    const seg = $('#favs-seg'); seg.setAttribute('aria-label', t.sortLabel);
    seg.querySelectorAll('button').forEach(b => {
      b.textContent = t['sort' + b.dataset.sort[0].toUpperCase() + b.dataset.sort.slice(1)];
      b.setAttribute('aria-checked', String(b.dataset.sort === sortMode));
      b.tabIndex = b.dataset.sort === sortMode ? 0 : -1;
    });
    const search = $('#favs-search'); search.placeholder = t.search; search.setAttribute('aria-label', t.search);
    const live = items.filter(it => ids.has(Number(it.id)));
    search.hidden = live.length <= SEARCH_AT;
    if (search.hidden) search.value = '';
    const q = search.value.trim().toLowerCase();
    const shown = q ? live.filter(it => `${it.snapshot.suburb || ''} ${it.snapshot.address || ''}`.toLowerCase().includes(q)) : live;
    $('#favs-tools').hidden = !live.length;
    let nodes;
    if (sortMode === 'suburb') {
      const groups = new Map();
      shown.forEach(it => { const k = it.snapshot.suburb || '—'; groups.has(k) || groups.set(k, []); groups.get(k).push(it); });
      nodes = [...groups].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]))
        .map(([name, rows]) => group(name, rows.sort(byPrice)));
    } else {
      const list_ = sortMode === 'price' ? [...shown].sort(byPrice) : shown;
      nodes = list_.map(it => row(it));
    }
    list.replaceChildren(...nodes);
    list.classList.toggle('grouped', sortMode === 'suburb');
    empty.hidden = live.length > 0;
    $('#favs-nomatch').hidden = !(live.length && !shown.length);
    $('#favs-nomatch').textContent = t.noMatch;
    actions();
    count();
  }

  const seg = $('#favs-seg');
  function setSort(mode, focus = false) {
    sortMode = mode;
    try { localStorage.setItem('favSort', mode); } catch (_) {  }
    render();
    if (focus) seg.querySelector(`[data-sort="${mode}"]`).focus();
  }
  seg.addEventListener('click', e => { const b = e.target.closest('[data-sort]'); if (b) setSort(b.dataset.sort); });
  seg.addEventListener('keydown', e => {
    if (!['ArrowLeft', 'ArrowRight'].includes(e.key)) return;
    const modes = [...seg.querySelectorAll('[data-sort]')].map(b => b.dataset.sort);
    const i = (modes.indexOf(sortMode) + (e.key === 'ArrowRight' ? 1 : modes.length - 1)) % modes.length;
    e.preventDefault(); setSort(modes[i], true);
  });
  $('#favs-search').addEventListener('input', render);

  async function open() {
    if (!user()) { window.nwAuth && window.nwAuth.open('login'); return; }
    if (!loaded) { try { await load(); } catch (_) {  } }
    render();
    if (!dlg.open) { dlg.showModal(); document.documentElement.classList.add('auth-open'); }
  }
  function close() { if (dlg.open) dlg.close(); }
  dlg.addEventListener('close', () => {
    document.documentElement.classList.remove('auth-open');
    if (selecting) { selecting = false; picked.clear(); dlg.classList.remove('selecting'); $('#favs-cmp').setAttribute('aria-pressed', 'false'); }
  });
  dlg.addEventListener('click', e => { if (e.target === dlg) close(); });
  $('#favs-close').addEventListener('click', close);
  favBtn.addEventListener('click', open);
  const menuItem = $('#btn-menu-favs');
  if (menuItem) menuItem.addEventListener('click', () => {
    $('#auth-menu').hidden = true; $('#btn-user').setAttribute('aria-expanded', 'false'); open();
  });

  function labels() {
    const t = T();
    favBtn.setAttribute('aria-label', t.open); favBtn.title = t.open;
    if (menuItem) menuItem.textContent = t.open;
    paintAll();
    if (dlg.open) render();
  }
  window.addEventListener('nw:lang', e => { lang = e.detail === 'en' ? 'en' : 'zh'; labels(); });
  window.nwFav = { button, open, has: id => ids.has(Number(id)), list: () => items.filter(it => ids.has(Number(it.id))) };
  labels();
})();
