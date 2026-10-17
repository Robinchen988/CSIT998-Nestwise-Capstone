(() => {
  const $ = s => document.querySelector(s);
  const dlg = $('#auth'), form = $('#auth-form');
  if (!dlg || !form) return;
  const openBtn = $('#btn-auth'), userBox = $('#auth-user'), userBtn = $('#btn-user'), menu = $('#auth-menu');
  const USERNAME = /^[A-Za-z0-9_一-鿿]{3,20}$/, EMAIL = /^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$/;
  const FIELD_OF = { username_invalid: 'username', username_taken: 'username', email_invalid: 'email',
    email_taken: 'email', password_length: 'password', password_is_username: 'password' };
  let lang = (() => { try { return localStorage.getItem('lang') === 'zh' ? 'zh' : 'en'; } catch (_) { return 'en'; } })();
  let user = null, busy = false, lastFocus = null;
  const T = () => (window.I18N[lang] || window.I18N.zh).auth;
  const mode = () => dlg.dataset.mode;
  const field = name => $('#auth-' + name);

  function render() {
    const t = T(), login = mode() === 'login';
    dlg.querySelector('.brand-name').textContent = (window.I18N[lang] || window.I18N.zh).brandName;
    openBtn.textContent = t.open;
    $('#auth-close').setAttribute('aria-label', t.close);
    $('#btn-logout').textContent = t.logout;
    userBtn.setAttribute('aria-label', t.menu);
    dlg.querySelectorAll('[data-t]').forEach(el => { el.textContent = t[el.dataset.t]; });
    $('#auth-title').textContent = login ? t.loginTitle : t.registerTitle;
    $('#auth-sub').textContent = login ? t.loginSub : t.registerSub;
    $('#auth-switch-q').textContent = login ? t.toRegisterQ : t.toLoginQ;
    $('#auth-switch').textContent = login ? t.toRegister : t.toLogin;
    $('#auth-submit').textContent = busy ? t.busy : (login ? t.submitLogin : t.submitRegister);
    field('password').autocomplete = login ? 'current-password' : 'new-password';
    dlg.querySelectorAll('[data-only]').forEach(el => { el.hidden = el.dataset.only !== mode(); });
    dlg.querySelectorAll('.auth-eye').forEach(b => {
      b.setAttribute('aria-label', b.getAttribute('aria-pressed') === 'true' ? t.hide : t.show);
    });
    dlg.querySelectorAll('.auth-msg').forEach(m => {
      if (!m.classList.contains('bad')) m.textContent = m.dataset.hint ? t[m.dataset.hint] : '';
    });
  }

  function setError(name, code) {
    const input = field(name), msg = $('#auth-' + name + '-msg');
    input.setAttribute('aria-invalid', 'true');
    input.setAttribute('aria-describedby', msg.id);
    msg.textContent = T().errors[code] || code; msg.classList.add('bad');
  }
  function clearErrors() {
    dlg.querySelectorAll('[aria-invalid]').forEach(i => i.removeAttribute('aria-invalid'));
    dlg.querySelectorAll('.auth-msg.bad').forEach(m => m.classList.remove('bad'));
    $('#auth-error').textContent = '';
    render();
  }
  function validate() {
    const v = n => field(n).value.trim(), errs = [];
    if (mode() === 'login') {
      if (!v('login')) errs.push(['login', 'required']);
      if (!field('password').value) errs.push(['password', 'required']);
      return errs;
    }
    if (!v('username')) errs.push(['username', 'required']);
    else if (!USERNAME.test(v('username'))) errs.push(['username', 'username_invalid']);
    if (v('email') && !EMAIL.test(v('email'))) errs.push(['email', 'email_invalid']);
    const pw = field('password').value;
    if (!pw) errs.push(['password', 'required']);
    else if (pw.length < 8 || pw.length > 128) errs.push(['password', 'password_length']);
    else if (pw.toLowerCase() === v('username').toLowerCase()) errs.push(['password', 'password_is_username']);
    if (!field('confirm').value) errs.push(['confirm', 'required']);
    else if (field('confirm').value !== pw) errs.push(['confirm', 'confirm_mismatch']);
    return errs;
  }

  function open(to = 'login') {
    if (dlg.open) return;
    lastFocus = document.activeElement;
    setMode(to, false);
    dlg.showModal();
    document.documentElement.classList.add('auth-open');
    field(to === 'login' ? 'login' : 'username').focus();
  }
  function close() { if (dlg.open) dlg.close(); }
  dlg.addEventListener('close', () => {
    document.documentElement.classList.remove('auth-open');
    form.reset(); clearErrors();
    dlg.querySelectorAll('.auth-eye[aria-pressed="true"]').forEach(b => toggleEye(b, false));
    if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
  });
  function setMode(next, keepFocus = true) {
    dlg.dataset.mode = next; clearErrors();
    if (keepFocus) field(next === 'login' ? 'login' : 'username').focus();
  }
  dlg.addEventListener('click', e => { if (e.target === dlg) close(); });
  $('#auth-close').addEventListener('click', close);
  $('#auth-switch').addEventListener('click', () => setMode(mode() === 'login' ? 'register' : 'login'));
  openBtn.addEventListener('click', () => open('login'));

  function toggleEye(btn, force) {
    const input = $('#' + btn.dataset.eye), show = force ?? input.type === 'password';
    input.type = show ? 'text' : 'password';
    btn.setAttribute('aria-pressed', String(show)); btn.classList.toggle('on', show);
    btn.setAttribute('aria-label', show ? T().hide : T().show);
  }
  dlg.querySelectorAll('.auth-eye').forEach(b => b.addEventListener('click', () => toggleEye(b)));
  form.addEventListener('input', e => {
    const name = e.target.name, msg = $('#auth-' + name + '-msg');
    if (msg && msg.classList.contains('bad')) {
      e.target.removeAttribute('aria-invalid'); msg.classList.remove('bad');
      msg.textContent = msg.dataset.hint ? T()[msg.dataset.hint] : '';
    }
    $('#auth-error').textContent = '';
  });

  form.addEventListener('submit', async e => {
    e.preventDefault();
    if (busy) return;
    clearErrors();
    const errs = validate();
    if (errs.length) { errs.forEach(([n, c]) => setError(n, c)); field(errs[0][0]).focus(); return; }
    const login = mode() === 'login';
    const body = login
      ? { login: field('login').value.trim(), password: field('password').value }
      : { username: field('username').value.trim(), email: field('email').value.trim() || null, password: field('password').value };
    busy = true; $('#auth-submit').disabled = true; render();
    try {
      const r = await fetch(login ? '/api/auth/login' : '/api/auth/register', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify(body) });
      const data = await r.json().catch(() => ({}));
      if (r.ok && data.user) { setUser(data.user); close(); return; }
      const code = data.error || 'network', name = FIELD_OF[code];
      if (name) { setError(name, code); field(name).focus(); }
      else { $('#auth-error').textContent = T().errors[code] || T().errors.network; }
    } catch (_) {
      $('#auth-error').textContent = T().errors.network;
    } finally {
      busy = false; $('#auth-submit').disabled = false; render();
    }
  });

  function setUser(u) {
    user = u || null;
    openBtn.hidden = !!user; userBox.hidden = !user;
    if (user) {
      userBox.querySelector('.auth-user-name').textContent = user.username;
      userBox.querySelector('.auth-avatar').textContent = [...user.username][0].toUpperCase();
      menu.querySelector('.auth-menu-who b').textContent = user.username;
      menu.querySelector('.auth-menu-who span').textContent = user.email || '';
    }
    showMenu(false);
    window.dispatchEvent(new CustomEvent('nw:auth', { detail: user }));
  }
  function showMenu(on) { menu.hidden = !on; userBtn.setAttribute('aria-expanded', String(on)); }
  userBtn.addEventListener('click', () => showMenu(menu.hidden));
  document.addEventListener('click', e => { if (!menu.hidden && !userBox.contains(e.target)) showMenu(false); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && !menu.hidden) { showMenu(false); userBtn.focus(); } });
  $('#btn-logout').addEventListener('click', async () => {
    try { await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' }); } catch (_) {  }
    setUser(null); openBtn.focus();
  });

  window.addEventListener('nw:lang', e => { lang = e.detail === 'en' ? 'en' : 'zh'; render(); });
  window.nwAuth = { open, close, get user() { return user; } };

  render();
  openBtn.hidden = false;
  fetch('/api/auth/me', { credentials: 'same-origin' }).then(r => r.json()).then(d => setUser(d.user)).catch(() => {});
})();
