import http from 'node:http';
const upstream = process.env.SHOWROOM_URL || 'http://127.0.0.1:8520';
const port = Number(process.env.SHOWROOM_TEST_PORT || 8521);

function browserChecks() {
  const queryEvents = [];
  window.addEventListener('nw:query', e => { queryEvents.push(e.detail); e.stopImmediatePropagation(); }, true);
  window.addEventListener('DOMContentLoaded', () => {
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const nextFrame = () => new Promise(resolve => requestAnimationFrame(resolve));
  const result = { viewport: [innerWidth, innerHeight], samples: 0, maxAnchorErrorPx: 0, failures: [] };
  const check = (ok, text) => { if (!ok) result.failures.push(text); };
  const panel = document.createElement('pre'); panel.id = 'showroom-test-result';
  panel.style.cssText = 'position:fixed;bottom:4px;left:4px;z-index:9999;background:#fff;padding:8px;font:12px monospace;max-width:95vw;max-height:35vh;overflow:auto;pointer-events:none';
  document.body.append(panel);
  const output = text => { panel.textContent = text; };
  const signs = () => [...document.querySelectorAll('.showroom .sign')];
  function measure() {
    for (const el of signs()) {
      const target = new DOMMatrix(el.style.transform), actual = new DOMMatrix(getComputedStyle(el).transform);
      for (const [x, y] of [[0, 0], [390, 0], [390, 620], [0, 620]]) {
        const a = target.transformPoint(new DOMPoint(x, y)), b = actual.transformPoint(new DOMPoint(x, y));
        result.maxAnchorErrorPx = Math.max(result.maxAnchorErrorPx, Math.hypot(a.x / a.w - b.x / b.w, a.y / a.w - b.y / b.w));
      }
      const inside = el.querySelector('.sign-in');
      check(inside.scrollWidth <= inside.clientWidth && inside.scrollHeight <= inside.clientHeight, 'Text overflows board');
      const title = el.querySelector('b').getBoundingClientRect(), action = el.querySelector('.go').getBoundingClientRect();
      check(title.bottom < action.top, 'Title overlaps action');
    }
    result.samples++;
  }
  async function sample(ms) {
    const until = performance.now() + ms;
    while (performance.now() < until) { await nextFrame(); measure(); }
  }
  (async () => {
    output('Loading real showroom…');
    for (let i = 0; i < 300 && window.__showroom?.stage !== 'ready'; i++) await sleep(100);
    check(window.__showroom?.stage === 'ready', 'Showroom failed to load');
    result.lawnOverrides = window.__showroom?.lawnOverrides || [];
    result.houseShadowSource = window.__showroom?.houseShadowSource;
    check(result.houseShadowSource?.endsWith('/floor-shadow-house-clean.webp'), 'Unoccluded floor shadow is not loaded');
    check(['cottage', 'villa', 'terrace', 'apartment'].every(key => result.lawnOverrides.some(
      x => x.key === key && new URL(x.source).pathname.endsWith('/lawn-' + key + '.png'))),
      'Clean lawn textures are not bound to all four loaded meshes');
    if (result.failures.length) throw new Error(result.failures[0]);
    const stage = document.querySelector('#showroom');
    output('Testing camera transition, reversal and pointer movement…');
    window.__showroom.goTo(1); await sample(1600);
    const move = (x, y, target = stage) => target.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, pointerType: 'mouse', clientX: x, clientY: y }));
    const rect = stage.getBoundingClientRect(), midY = rect.top + rect.height / 2;
    const offset = () => [...window.__showroom.par];
    const origin = offset();
    for (const x of [.25, .5, .75, .4]) { move(innerWidth * x, midY); await sample(100); }
    result.centralMovement = Math.hypot(...offset().map((v, i) => v - origin[i]));
    check(result.centralMovement === 0, 'Central mouse movement moved the camera');
    const edgeStart = performance.now();
    const edgeX = innerWidth - Math.min(160, innerWidth * .16) + 2;
    move(edgeX, midY); await sample(80);
    const early = offset(), earlyMs = performance.now() - edgeStart;
    await sample(180); const later = offset(), totalMs = performance.now() - edgeStart;
    result.startup = { earlyMs, totalMs, earlyDistance: early[0] - origin[0], laterDistance: later[0] - early[0] };
    if (earlyMs < 140 && totalMs < 400) {
      const slowSpeed = (early[0] - origin[0]) / earlyMs;
      const laterSpeed = (later[0] - early[0]) / (totalMs - earlyMs);
      result.startup.measured = true;
      check(laterSpeed > slowSpeed * 1.5, 'Edge pan jumped directly to full speed');
    } else result.startup.measured = false;
    await sample(300); const right = offset();
    check(right[0] === 12, 'Shallow edge entry still crawls instead of reaching the limit promptly');
    result.edgeOffsets = { origin, right };
    check(right[0] > origin[0] + 1, 'Right edge did not move camera right');
    move(innerWidth / 2, midY); await sample(600);
    result.releaseMovement = Math.hypot(...offset().map((v, i) => v - right[i]));
    check(result.releaseMovement === 0, 'Camera kept drifting or bounced back after leaving edge');
    move(2, midY); await sample(600);
    result.edgeOffsets.left = offset();
    check(offset()[0] < right[0] - 1, 'Left edge did not move camera left');
    move(innerWidth - 2, midY, signs()[0]); const reading = offset(); await sample(400);
    check(offset().every((v, i) => v === reading[i]), 'Reading a board moved the camera');
    move(innerWidth - 2, midY); await sample(2200); const limit = offset(); await sample(400);
    result.edgeOffsets.limit = limit; result.edgeOffsets.afterLimit = offset();
    check(offset().every((v, i) => v === limit[i]), 'Edge pan did not stop at its limit');
    move(innerWidth / 2, midY);
    const beforeUp = offset();
    move(innerWidth / 2, 2, document.querySelector('header')); await sample(750);
    result.edgeOffsets.up = offset();
    check(offset()[1] > beforeUp[1] + 1, 'Top viewport edge over header did not pan up');
    move(innerWidth / 2, innerHeight - 2); await sample(750);
    result.edgeOffsets.down = offset();
    check(offset()[1] < result.edgeOffsets.up[1] - 1, 'Bottom viewport edge did not pan down');
    move(innerWidth / 2, midY); const verticalStop = offset(); await sample(200);
    check(offset().every((v, i) => v === verticalStop[i]), 'Vertical pan continued in center');
    for (const [x, y] of [[5, 70], [innerWidth - 5, innerHeight - 5], [innerWidth / 2, innerHeight / 2]]) {
      stage.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, pointerType: 'mouse', clientX: x, clientY: y }));
      await sample(600);
    }
    window.__showroom.goTo(0); await sample(400);
    window.__showroom.goTo(1); await sample(1600);
    check(result.maxAnchorErrorPx < .1, 'Text lags behind camera projection');
    output('Measuring fade opacity and real timed rotation (18 seconds)…');
    const seen = signs().map(e => new Set([e.textContent]));
    const fades = signs().map(e => ({ previous: e.textContent, lastOpacity: 1, down: 0, up: 0, levels: new Set(), swapOpacity: [] }));
    for (let i = 0; i < 360; i++) {
      await sleep(50); measure(); signs().forEach((e, j) => {
        seen[j].add(e.textContent);
        const opacity = Number(getComputedStyle(e.querySelector('.sign-in')).opacity), f = fades[j];
        if (opacity > .1 && opacity < .9) {
          f.levels.add(Math.floor(opacity * 10));
          if (opacity < f.lastOpacity) f.down++; else if (opacity > f.lastOpacity) f.up++;
        }
        if (e.textContent !== f.previous) f.swapOpacity.push(opacity);
        f.previous = e.textContent; f.lastOpacity = opacity;
      });
    }
    result.fades = fades.map(f => ({ down: f.down, up: f.up, levels: f.levels.size, swapOpacity: f.swapOpacity }));
    check(fades.every(f => f.down >= 5 && f.up >= 5 && f.levels.size >= 6), 'Fade skipped intermediate opacity levels');
    check(fades.every(f => f.swapOpacity.every(a => a < .2)), 'Words changed before fading out');
    result.variantsSeen = seen.map(s => s.size);
    check(seen.every(s => s.size > 1), 'One or more boards did not rotate');
    const focused = signs()[0]; focused.focus(); const stable = focused.textContent;
    await sleep(8500); check(focused.textContent === stable, 'Focused suggestion changed under user');
    focused.blur();
    for (const el of signs()) { const expected = el.dataset.query; el.click(); check(queryEvents.at(-1) === expected, 'Displayed suggestion and dispatched query differ'); }
    if (document.querySelector('#btn-lang').textContent.trim() === 'EN') document.querySelector('#btn-lang').click();
    await sleep(500); measure();
    result.englishLabels = signs().map(e => e.getAttribute('aria-label'));
    check(result.englishLabels.every(s => /view homes/.test(s)), 'Language change missed a board');
    await sample(1000);
    for (const el of signs()) { const expected = el.dataset.query; el.click(); check(queryEvents.at(-1) === expected, 'English query mismatch'); }
    document.querySelector('#btn-lang').click();
    window.__showroom.goTo(0); await sample(1600);
    check(signs().every(e => e.tabIndex === -1 && e.getAttribute('aria-hidden') === 'true'), 'City view leaves hidden boards interactive');
    const paused = signs().map(e => e.textContent); await sleep(8500);
    check(signs().every((e, i) => e.textContent === paused[i]), 'Rotation continued in city view');
    window.__showroom.goTo(1); await sample(1600);
    result.failures = [...new Set(result.failures)];
    check(result.maxAnchorErrorPx < .1, 'Text lags behind camera projection');
    result.status = result.failures.length ? 'FAIL' : 'PASS';
    output(JSON.stringify(result, null, 2));
    await fetch('/__result', { method: 'POST', body: JSON.stringify(result) });
  })().catch(error => { result.status = 'ERROR'; result.failures.push(String(error)); output(JSON.stringify(result, null, 2)); });
  });
}

http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, upstream);
    if (url.pathname === '/' && url.searchParams.has('width')) {
      const width = Math.max(320, Math.min(3840, Number(url.searchParams.get('width')) || 1280));
      const height = Math.max(480, Math.min(2160, Number(url.searchParams.get('height')) || 720));
      res.setHeader('content-type', 'text/html; charset=utf-8');
      res.end(`<title>Showroom responsive check</title><iframe title="${width} by ${height} homepage test" src="/" style="width:${width}px;height:${height}px;border:0"></iframe>`);
      return;
    }
    if (req.url === '/__verify.js') { res.setHeader('content-type', 'text/javascript'); res.end(`(${browserChecks.toString()})();`); return; }
    if (req.url === '/__result') {
      let body = ''; for await (const part of req) body += part;
      console.log(body); res.end('ok'); return;
    }
    const response = await fetch(new URL(req.url, upstream));
    res.statusCode = response.status;
    res.setHeader('content-type', response.headers.get('content-type') || 'application/octet-stream');
    res.setHeader('cache-control', 'no-store');
    if (new URL(req.url, upstream).pathname === '/') {
      res.end((await response.text()).replace('</head>', '<script src="/__verify.js"></script></head>'));
    } else res.end(Buffer.from(await response.arrayBuffer()));
  } catch (error) { res.statusCode = 502; res.end(String(error)); }
}).listen(port, '127.0.0.1', () => console.log(`Showroom checks: http://127.0.0.1:${port}/`));
