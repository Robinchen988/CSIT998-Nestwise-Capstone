import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js';
import { createTaskQueue, settleModels } from './asset-loading.mjs?v=ios-20261003-3';

const BASE = '/static/showroom/';
const q = new URLSearchParams(location.search);
const $ = s => document.querySelector(s);
const app = $('#app'), stage = $('#showroom'), viewport = $('#showroom-view'), signLayer = $('#showroom-signs'), input = $('#input');
const report = window.__showroom = { revision: 'ios-20261003-3', stage: 'boot', phase: 'module-ready' };
const WIDE = matchMedia('(min-width: 900px)');
let rendererForCleanup;

const HOUSES = ['cottage', 'villa', 'terrace', 'apartment'];
const lang = () => { try { return localStorage.getItem('lang') === 'zh' ? 'zh' : 'en'; } catch (_) { return 'en'; } };
let currentLang = lang();
const signChoices = (k, l = currentLang) => {
  const text = window.I18N[l] || window.I18N.zh;
  return [text.showroomSigns[k], ...(text.showroomSignVariants?.[k] || [])];
};

const Z_UP_TO_Y_UP = new THREE.Matrix4().set(1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1);
const toThree = p => new THREE.Vector3(p[0], p[2], -p[1]);
const PAGE_BG = new THREE.Color(getComputedStyle(document.documentElement).getPropertyValue('--bg').trim() || '#F5F4F0');
const json = async url => (await fetch(url, { cache: 'no-store' })).json();

function markWater(material, mask) {
  material.onBeforeCompile = shader => {
    shader.uniforms.uWaterMask = { value: mask };
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', '#include <common>\n        uniform sampler2D uWaterMask;')
      .replace('#include <map_fragment>', `#include <map_fragment>
        if (texture2D(uWaterMask, vMapUv).r > .5) diffuseColor.rgb = vec3(1., 0., 1.);`);
  };
  material.customProgramCacheKey = () => 'water-debug';
}
const DEBUG_WATER = new URLSearchParams(location.search).get('debug') === 'water';

function projectedMaterial(tex, wideVP, { instanced = false, shadow = false, fade = false, premultiply = false } = {}) {
  return new THREE.ShaderMaterial({
    uniforms: { uTex: { value: tex }, uVP: { value: wideVP }, uOpacity: { value: 1 } },
    transparent: shadow || fade, depthWrite: !shadow,
    alphaToCoverage: !shadow,
    vertexShader: `
      uniform mat4 uVP; varying vec2 vUv;
      void main() {
        vec4 wp = modelMatrix * ${instanced ? 'instanceMatrix * ' : ''}vec4(position, 1.0);
        vec4 c = uVP * wp; vUv = c.xy / c.w * .5 + .5;
        gl_Position = projectionMatrix * viewMatrix * wp;
      }`,
    fragmentShader: `
      uniform sampler2D uTex; uniform float uOpacity; varying vec2 vUv;
      void main() {
        vec4 c = texture2D(uTex, vUv);
        ${shadow ? 'gl_FragColor = vec4(0.0, 0.0, 0.0, c.a * uOpacity);' : `
          // 按屏幕像素宽度覆盖轮廓,由 MSAA 平滑树叶边缘,避免硬裁切随镜头闪烁。
          float width = max(fwidth(c.a), .001);
          float coverage = smoothstep(.5 - width, .5 + width, c.a);
          if (coverage <= 0.0) discard;
          // 离屏透明层需要预乘颜色,否则 MSAA 轮廓在合成时出现发白亮边。
          gl_FragColor = vec4(c.rgb ${premultiply ? '* coverage' : ''}, coverage);
        `}
        #include <colorspace_fragment>
      }`,
  });
}

const tiltMaterial = new THREE.ShaderMaterial({
  uniforms: { uTex: { value: null }, uHouseTex: { value: null }, uHouseOpacity: { value: 0 }, uTexel: { value: new THREE.Vector2() }, uFocus: { value: .55 }, uStrength: { value: 0 } },
  vertexShader: 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }',
  fragmentShader: `
    uniform sampler2D uTex, uHouseTex; uniform vec2 uTexel; uniform float uFocus, uStrength, uHouseOpacity; varying vec2 vUv;
    void main() {
      float d = clamp((abs(vUv.y - uFocus) - .12) / .3, 0.0, 1.0);
      float r = pow(d, 1.4) * 7.0 * uStrength;
      vec4 acc = vec4(0.0); float w = 0.0;
      for (int i = 0; i < 16; i++) {
        float a = float(i) * 2.39996; float rr = sqrt(float(i) / 16.0) * r;
        vec2 o = vec2(cos(a), sin(a)) * rr * uTexel;
        vec4 city = texture2D(uTex, vUv + o), house = texture2D(uHouseTex, vUv + o);
        // 透明底离屏层已由混合得到预乘 RGB,不要再乘一次 house.a。
        acc += vec4(city.rgb * (1.0 - house.a * uHouseOpacity) + house.rgb * uHouseOpacity, 1.0); w += 1.0;
      }
      gl_FragColor = acc / w;
      #include <colorspace_fragment>
    }`,
  depthTest: false, depthWrite: false,
});

function quadMatrix(w, h, [tl, tr, br, bl]) {
  const sys = (s, d) => {
    const A = [], B = [];
    for (let i = 0; i < 4; i++) {
      const [x, y] = s[i], [u, v] = d[i];
      A.push([x, y, 1, 0, 0, 0, -u * x, -u * y]); B.push(u);
      A.push([0, 0, 0, x, y, 1, -v * x, -v * y]); B.push(v);
    }
    for (let c = 0; c < 8; c++) {
      let p = c; for (let r = c + 1; r < 8; r++) if (Math.abs(A[r][c]) > Math.abs(A[p][c])) p = r;
      [A[c], A[p]] = [A[p], A[c]]; [B[c], B[p]] = [B[p], B[c]];
      for (let r = 0; r < 8; r++) if (r !== c) { const f = A[r][c] / A[c][c]; for (let k = c; k < 8; k++) A[r][k] -= f * A[c][k]; B[r] -= f * B[c]; }
    }
    return B.map((b, i) => b / A[i][i]);
  };
  const [a, b, c, d, e, f, g, hh] = sys([[0, 0], [w, 0], [w, h], [0, h]], [tl, tr, br, bl]);
  return `matrix3d(${a},${d},0,${g},${b},${e},0,${hh},0,0,1,0,${c},${f},0,1)`;
}

async function main() {
  report.phase = 'loading-cameras';
  const [camStart, camEnd, camWide, signs3d] = await Promise.all(['cam-start', 'cam-end', 'cam-wide', 'cam-start-signs3d'].map(n => json(BASE + n + '.json')));
  const [RX, RY] = camStart.resolution;

  report.phase = 'creating-renderer';
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
    report.webgl2 = true;
    rendererForCleanup = renderer;
  } catch (error) {
    report.webgl2 = false; report.stage = 'unsupported'; report.reason = 'webgl2-unavailable';
    throw error;
  }
  renderer.outputColorSpace = THREE.SRGBColorSpace; renderer.toneMapping = THREE.NoToneMapping; renderer.setClearColor(0, 0);
  renderer.domElement.addEventListener('webglcontextlost', () => { report.contextLost = true; report.phase = 'context-lost'; });
  renderer.domElement.addEventListener('webglcontextrestored', () => { report.contextLost = false; });
  viewport.appendChild(renderer.domElement);
  const scene = new THREE.Scene();

  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, .1, 20000);
  const M0 = Z_UP_TO_Y_UP.clone().multiply(new THREE.Matrix4().set(...camStart.matrix_world.flat()));
  M0.decompose(camera.position, camera.quaternion, camera.scale);
  const right = new THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion), up = new THREE.Vector3(0, 1, 0).applyQuaternion(camera.quaternion);
  const frameOf = c => {
    const m = Z_UP_TO_Y_UP.clone().multiply(new THREE.Matrix4().set(...c.matrix_world.flat()));
    const p = new THREE.Vector3().setFromMatrixPosition(m).sub(camera.position);
    return { cu: p.dot(right) + c.shift_x * c.ortho_scale, cv: p.dot(up) + c.shift_y * c.ortho_scale, w: c.ortho_scale };
  };
  const F1 = frameOf(camEnd);
  const F0 = frameOf(camStart);
  const FIT = { marginX: .10, gapBottom: .06, gapTop: .05, biasX: -.03, towerClear: 48, edgeMin: .025, shadowPad: 34 };
  const camPlane = p => ({ u: p.clone().sub(camera.position).dot(right), v: p.clone().sub(camera.position).dot(up) });
  function boxInCameraPlane(objs) {
    const b = { u0: Infinity, u1: -Infinity, v0: Infinity, v1: -Infinity, uTop: 0 };
    const box = new THREE.Box3(), c = new THREE.Vector3();
    for (const o of objs) {
      box.setFromObject(o);
      if (box.isEmpty()) continue;
      for (const sx of [box.min.x, box.max.x]) for (const sy of [box.min.y, box.max.y]) for (const sz of [box.min.z, box.max.z]) {
        const { u, v } = camPlane(c.set(sx, sy, sz));
        b.u0 = Math.min(b.u0, u); b.u1 = Math.max(b.u1, u); b.v0 = Math.min(b.v0, v);
        if (v > b.v1) { b.v1 = v; b.uTop = u; }
      }
    }
    return b;
  }

  const wideCam = new THREE.OrthographicCamera(-1, 1, 1, -1, .1, 20000);
  Z_UP_TO_Y_UP.clone().multiply(new THREE.Matrix4().set(...camWide.matrix_world.flat())).decompose(wideCam.position, wideCam.quaternion, wideCam.scale);
  { const W = camWide.ortho_scale, H = W * camWide.resolution[1] / camWide.resolution[0], sx = camWide.shift_x * W, sy = camWide.shift_y * W;
    Object.assign(wideCam, { left: -W / 2 + sx, right: W / 2 + sx, top: H / 2 + sy, bottom: -H / 2 + sy }); }
  wideCam.updateMatrixWorld(); wideCam.updateProjectionMatrix();
  const wideVP = new THREE.Matrix4().multiplyMatrices(wideCam.projectionMatrix, wideCam.matrixWorldInverse);

  report.phase = 'loading-assets';
  const manager = new THREE.LoadingManager();
  const assetName = url => url.startsWith('blob:') ? 'embedded-texture' : new URL(url, location.href).pathname.split('/').pop().replace(/[^a-zA-Z0-9_.-]/g, '').slice(0, 80);
  report.assets = {};
  report.decoder = { started: 0, completed: 0, failed: 0, workerLimit: 1, workersDisposed: false };
  report.imageQueue = {};
  const queueImage = createTaskQueue(2, report.imageQueue);
  report.images = { started: 0, completed: 0, failed: 0 };
  manager.onError = url => { report.failedAsset = assetName(url); };
  manager.onProgress = (_, loaded, total) => { report.requests = { loaded, total }; };
  const draco = new DRACOLoader(manager).setDecoderPath(BASE + 'draco/').setWorkerLimit(1);
  const getWorker = draco._getWorker.bind(draco);
  draco._getWorker = (...args) => getWorker(...args).then(worker => {
    if (!worker.__showroomErrors) {
      worker.__showroomErrors = true;
      const failed = () => {
        worker.__showroomFailure = true;
        report.reason = 'draco-worker-error'; report.failedAsset = 'draco-worker';
        for (const callback of Object.values(worker._callbacks)) callback.reject(new Error('DRACO worker failed'));
      };
      worker.addEventListener('error', failed);
      worker.addEventListener('messageerror', failed);
    }
    if (worker.__showroomFailure) throw new Error('DRACO worker failed');
    return worker;
  });
  const decodeGeometry = draco.decodeGeometry.bind(draco);
  const pendingDecodes = new Set();
  let decoderClosing = false;
  draco.decodeGeometry = (...args) => {
    report.decoder.started++;
    const task = Promise.resolve().then(() => {
      if (decoderClosing) throw new Error('DRACO decoder is closing');
      return decodeGeometry(...args);
    }).then(result => {
      report.decoder.completed++; return result;
    }, error => { report.decoder.failed++; throw error; });
    pendingDecodes.add(task);
    const finished = () => pendingDecodes.delete(task);
    task.then(finished, finished);
    return task;
  };
  const loader = new GLTFLoader(manager).setDRACOLoader(draco), texLoader = new THREE.TextureLoader(manager);
  loader.register(parser => {
    report.textureLoader = parser.textureLoader.isImageBitmapLoader ? 'ImageBitmap' : 'Image';
    const loadImage = parser.loadImageSource.bind(parser);
    parser.loadImageSource = (...args) => queueImage(() => {
      report.images.started++;
      return Promise.resolve().then(() => loadImage(...args)).then(result => {
        report.images.completed++; return result;
      }, error => { report.images.failed++; throw error; });
    });
    return { name: 'ShowroomAssetDiagnostics' };
  });
  const t0 = performance.now();
  report.assetStartedMs = t0;
  const track = (name, promise) => promise.then(result => {
    report.assets[name].state = 'ready'; return result;
  }, error => { report.assets[name].state = 'failed'; report.failedAsset = name; throw error; });
  const model = name => {
    const asset = report.assets[name] = { state: 'downloading', loaded: 0, total: 0 };
    return track(name, loader.loadAsync(BASE + name, progress => {
      asset.loaded = progress.loaded; asset.total = progress.total;
      if (progress.total > 0 && progress.loaded >= progress.total) asset.state = 'decoding';
    }));
  };
  const texture = name => {
    report.assets[name] = { state: 'loading' };
    return track(name, queueImage(() => texLoader.loadAsync(BASE + name)));
  };
  const models = settleModels([model('city-v26-baked.glb'), model('trees-v26.glb')], async () => {
    decoderClosing = true;
    while (pendingDecodes.size) await Promise.allSettled([...pendingDecodes]);
    draco.dispose(); report.decoder.workersDisposed = true;
  });
  const [[city, trees], treeTex, shadowTex, shadowHouseTex, lawnMaps, waterMasks] = await Promise.all([
    models,
    texture('layer-trees.webp'), texture('floor-shadow-city.webp'),
    texture('floor-shadow-house-clean.webp'),
    Promise.all(HOUSES.map(k => texture('lawn-' + k + '.png'))),
    DEBUG_WATER ? Promise.all(['plinth', 'river'].map(k => texture('water-mask-' + k + '.png'))) : [],
  ]);

  const WATER_OBJECTS = DEBUG_WATER ? { 'v27 plinth': waterMasks[0], '13 Continuous river': waterMasks[1] } : {};
  for (const m of waterMasks) { m.flipY = false; m.colorSpace = THREE.NoColorSpace; }
  report.loadMs = Math.round(performance.now() - t0);
  report.phase = 'building-scene';

  const aniso = renderer.capabilities.getMaxAnisotropy();
  report.lawnOverrides = [];
  let tris = 0;
  city.scene.traverse(o => {
    if (!o.isMesh) return;
    const assetName = o.userData.name || o.name.replaceAll('_', ' ');
    const lawnIndex = HOUSES.findIndex(k => assetName === 'v28 ' + k + ' lawn');
    const map = lawnIndex < 0 ? o.material.map : lawnMaps[lawnIndex];
    if (lawnIndex >= 0) map.flipY = false;
    if (map) { map.colorSpace = THREE.SRGBColorSpace; map.anisotropy = aniso; }
    o.material = new THREE.MeshBasicMaterial({ map, color: map ? 0xffffff : 0xff00ff,
      alphaTest: lawnIndex < 0 ? 0 : .5, alphaToCoverage: lawnIndex >= 0,
      premultipliedAlpha: lawnIndex >= 0 });
    if (WATER_OBJECTS[assetName]) markWater(o.material, WATER_OBJECTS[assetName]);
    if (lawnIndex >= 0) report.lawnOverrides.push({ key: HOUSES[lawnIndex], mesh: o.name,
      source: o.material.map.image.currentSrc || o.material.map.image.src });
    tris += (o.geometry.index ? o.geometry.index.count : o.geometry.attributes.position.count) / 3;
  });
  scene.add(city.scene);

  treeTex.colorSpace = THREE.SRGBColorSpace;
  treeTex.generateMipmaps = true; treeTex.minFilter = THREE.LinearMipmapLinearFilter; treeTex.anisotropy = aniso;
  const treeMat = projectedMaterial(treeTex, wideVP, { instanced: true });
  const houseTreeMat = projectedMaterial(treeTex, wideVP, { instanced: true, premultiply: true });
  trees.scene.updateMatrixWorld(true);
  const groups = new Map(); let treeCount = 0;
  const houseParts = [];
  trees.scene.traverse(o => {
    if (!o.isMesh) return;
    const side = o.name.startsWith('v28') ? 'house' : 'city';
    const key = side + '|' + o.geometry.uuid;
    const g = groups.get(key) || { geo: o.geometry, side, mats: [] };
    g.mats.push(o.matrixWorld.clone()); groups.set(key, g);
  });
  for (const { geo, side, mats } of groups.values()) {
    const inst = new THREE.InstancedMesh(geo, side === 'house' ? houseTreeMat : treeMat, mats.length); mats.forEach((m, i) => inst.setMatrixAt(i, m));
    inst.frustumCulled = false; scene.add(inst); treeCount += mats.length;
    if (side === 'house') houseParts.push(inst);
  }

  const floorGeo = new THREE.PlaneGeometry(8000, 8000).rotateX(-Math.PI / 2);
  const floors = [shadowTex, shadowHouseTex].map((tex, i) => {
    tex.generateMipmaps = false; tex.minFilter = THREE.LinearFilter;
    const m = new THREE.Mesh(floorGeo, projectedMaterial(tex, wideVP, { shadow: true, fade: i === 1 }));
    m.position.y = -40.02; m.renderOrder = -1; scene.add(m);
    return m;
  });
  const houseFloor = floors[1];
  houseFloor.material.uniforms.uOpacity.value = .45;
  report.houseShadowSource = shadowHouseTex.image.currentSrc || shadowHouseTex.image.src;

  const cityObjs = [], houseObjs = [];
  city.scene.traverse(o => { if (o.isMesh) (o.name.startsWith('v28') ? houseObjs : cityObjs).push(o); });
  houseParts.push(...houseObjs);
  for (const o of [...houseParts, houseFloor]) o.layers.set(1);
  report.cityBox = boxInCameraPlane(cityObjs);
  report.housesU0 = houseObjs.length ? boxInCameraPlane(houseObjs).u0 : null;
  Object.assign(report, { tris, treeCount });

  const rt = new THREE.WebGLRenderTarget(1, 1, { samples: 4, colorSpace: THREE.LinearSRGBColorSpace });
  let houseRT = null;
  const emptyHouseTexture = new THREE.DataTexture(new Uint8Array(4), 1, 1);
  emptyHouseTexture.colorSpace = THREE.LinearSRGBColorSpace; emptyHouseTexture.needsUpdate = true;
  report.houseTarget = { allocated: false, width: 0, height: 0, allocations: 0, samples: 4 };
  const postScene = new THREE.Scene(), postCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
  postScene.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), tiltMaterial));

  const cards = HOUSES.map(k => {
    const el = document.createElement('button');
    el.className = 'sign'; el.type = 'button';
    const card = { el, key: k, corners: signs3d['v28 ' + k].map(toThree),
      index: Math.floor(Math.random() * signChoices(k).length), timer: 0, fadeTimer: 0, hovered: false };
    el.addEventListener('click', () => {
      const t = signChoices(k)[card.index]; if (!t) return;
      report.clicked = t.query;
      window.dispatchEvent(new CustomEvent('nw:query', { detail: t.query }));
    });
    signLayer.appendChild(el);
    el.innerHTML = `<span class="sign-in"><i class="dash"></i><b></b><span class="ln"></span><span class="go"><span></span> <em>→</em></span></span>`;
    return card;
  });
  const headline = (c, i = c.index) => signChoices(c.key)[i]?.l1;
  const shuffle = a => { for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; };
  function pickSign(c, { change = false } = {}) {
    const taken = new Set(cards.filter(o => o !== c).map(o => headline(o)));
    const usable = i => !taken.has(headline(c, i)) && !(change && i === c.index);
    for (let pass = 0; pass < 2; pass++) {
      if (!c.deck?.length) c.deck = shuffle([...signChoices(c.key).keys()].filter(i => !(change && i === c.index)));
      const at = c.deck.findIndex(usable);
      if (at >= 0) return c.deck.splice(at, 1)[0];
      c.deck = [];
    }
    return c.index;
  }
  for (const c of cards) c.index = pickSign(c);
  function fillSign(c, l = currentLang) {
    const t = signChoices(c.key, l)[c.index];
    c.el.setAttribute('aria-label', t.aria);
    c.el.dataset.query = t.query;
    c.el.querySelector('b').textContent = t.l1; c.el.querySelector('.ln').textContent = t.l2;
    c.el.querySelector('.go span').textContent = t.go;
  }
  function fillSigns(l) {
    for (const c of cards) fillSign(c, l);
    const text = window.I18N[l] || window.I18N.zh;
    stage.querySelector('.sr-next')?.setAttribute('aria-label', text.showroomNext);
    stage.querySelector('.sr-back')?.setAttribute('aria-label', text.showroomBack);
  }
  fillSigns(currentLang);
  window.addEventListener('nw:lang', e => {
    currentLang = e.detail; stopSigns(); fillSigns(currentLang); syncSigns();
  });
  const CW = 390, CH = 620;

  let pan = 0, from = 0, to = 0, startT = 0, raf = 0;
  const size = new THREE.Vector2();
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const finePointer = matchMedia('(hover: hover) and (pointer: fine)');

  const fadeMs = 700;
  function stopSign(c, immediate = true) {
    clearTimeout(c.timer); c.timer = 0;
    clearTimeout(c.fadeTimer); c.fadeTimer = 0;
    if (immediate || reduced.matches) c.el.querySelector('.sign-in').style.removeProperty('opacity');
    else fadeSign(c, 1, () => scheduleSign(c), 400);
  }
  function stopSigns(immediate = true) { cards.forEach(c => stopSign(c, immediate)); }
  function canRotate(c) {
    return app.dataset.stage === 'welcome' && !document.hidden && !reduced.matches &&
      pan === 1 && to === 1 && !raf && !c.hovered && !c.el.contains(document.activeElement);
  }
  function fadeSign(c, target, done, duration = fadeMs) {
    clearTimeout(c.fadeTimer);
    const inner = c.el.querySelector('.sign-in');
    const initial = inner.style.opacity === '' ? 1 : Number(inner.style.opacity);
    const started = performance.now();
    function step() {
      const t = Math.min(1, (performance.now() - started) / duration);
      inner.style.opacity = String(initial + (target - initial) * t);
      if (t < 1) c.fadeTimer = setTimeout(step, 16);
      else {
        c.fadeTimer = 0;
        if (target === 1) inner.style.removeProperty('opacity');
        done?.();
      }
    }
    if (initial === target) { c.fadeTimer = 0; done?.(); }
    else step();
  }
  function scheduleSign(c) {
    if (c.timer || c.fadeTimer || !canRotate(c)) return;
    c.timer = setTimeout(() => {
      c.timer = 0;
      if (!canRotate(c)) return;
      fadeSign(c, 0, () => {
        if (!canRotate(c)) { stopSign(c, false); return; }
        c.index = pickSign(c, { change: true });
        fillSign(c);
        fadeSign(c, 1, () => scheduleSign(c));
      });
    }, 5000 + Math.random() * 3000);
  }
  function syncSigns() {
    for (const c of cards) { if (canRotate(c)) scheduleSign(c); else stopSign(c); }
  }
  for (const c of cards) {
    c.el.addEventListener('pointerenter', ev => {
      if (ev.pointerType === 'touch') return;
      c.hovered = true; stopSign(c, false);
    });
    c.el.addEventListener('pointerleave', () => { c.hovered = false; scheduleSign(c); });
    c.el.addEventListener('focus', () => stopSign(c, false));
    c.el.addEventListener('blur', () => scheduleSign(c));
    c.el.addEventListener('pointerdown', () => stopSign(c, false));
  }
  document.addEventListener('visibilitychange', syncSigns);
  WIDE.addEventListener('change', syncSigns);

  const PARALLAX = { x: 12, y: 10, speed: 36, ramp: .18, edgeRatio: .16, edgeMax: 160 };
  const par = { tx: 0, ty: 0, vx: 0, vy: 0, x: 0, y: 0, last: 0, raf: 0, fromX: 0, fromY: 0 };
  const arrows = { next: stage.querySelector('.sr-next'), back: stage.querySelector('.sr-back') };

  function cityFrame(aspect, W) {
    const b = report.cityBox;
    if (!b) return { cu: F0.cu, cv: F0.cv, w: F0.w };
    const sr = stage.getBoundingClientRect(), ir = input.closest('form').getBoundingClientRect();
    const xInput = Math.max(0, ir.right - sr.left), yInput = Math.max(0, ir.bottom - sr.top);
    const H = W / aspect;
    let w = Math.max((b.u1 - b.u0) / (1 - 2 * FIT.marginX),
                     ((b.v1 - b.v0) / (1 - FIT.gapBottom - FIT.gapTop)) * aspect);
    let h = 0, cv = 0, cu = 0;
    for (let i = 0; i < 6; i++) {
      h = w / aspect;
      cv = b.v0 + h * (.5 - FIT.gapBottom);
      const xT = Math.min(W * .92, xInput + FIT.towerClear + PARALLAX.x / w * W);
      const cuMax = b.uTop + w * (.5 - xT / W);
      const cuMin = b.u1 + FIT.shadowPad + w * (FIT.edgeMin - .5);
      cu = Math.max(cuMin, Math.min((b.u0 + b.u1) / 2 + FIT.biasX * w, cuMax));
      const xTower = (b.uTop - cu + w / 2 - PARALLAX.x) / w * W;
      const yTower = (cv + h / 2 - PARALLAX.y - b.v1) / h * H;
      if (xTower > xInput + 8 || yTower > yInput + 8) break;
      w *= 1.05;
    }
    return { cu, cv, w };
  }

  function frameAt(t, aspect, W) {
    const e = t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
    const A = cityFrame(aspect, W);
    const cu = A.cu + (F1.cu - A.cu) * e, cv = A.cv + (F1.cv - A.cv) * e, w = A.w + (F1.w - A.w) * e;
    return { cu, cv, w, e };
  }

  function draw() {
    const W = stage.clientWidth, H = stage.clientHeight;
    if (!W || !H) return;
    const aspect = W / H;
    const { cu, cv, w, e } = frameAt(pan, aspect, W);
    const h1 = F1.w * RY / RX;
    let h = w * H / W, wf = w;
    if (e > 0 && h < h1) { h = Math.max(h, h1 * e + (w / aspect) * (1 - e)); wf = h * aspect; }
    const anchor = (wf - w) / 2 * (1 - e);
    const bottom = cv - h / 2 + par.y;
    const cuP = cu + par.x - anchor;
    Object.assign(camera, { left: cuP - wf / 2, right: cuP + wf / 2, top: bottom + h, bottom });
    const f = Math.min(1, Math.max(0, (e - .04) / .22));
    tiltMaterial.uniforms.uHouseOpacity.value = f;
    camera.updateProjectionMatrix(); camera.updateMatrixWorld();
    const v = new THREE.Vector3();
    const px = p => { v.copy(p).project(camera); return [(v.x * .5 + .5) * W, (-v.y * .5 + .5) * H]; };
    let focus = 0;
    for (const c of cards) {
      const [bl0, br0, tr0, tl0] = c.corners.map(px);
      c.el.style.transform = quadMatrix(CW, CH, [tr0, tl0, bl0, br0]);
      c.el.style.opacity = String(Math.max(0, (e - .55) / .45));
      c.el.setAttribute('aria-hidden', e > .9 ? 'false' : 'true');
      c.el.tabIndex = e > .9 ? 0 : -1;
      focus += (tr0[1] + bl0[1]) / 2 / H;
    }
    tiltMaterial.uniforms.uFocus.value = 1 - focus / cards.length;
    camera.layers.set(0);
    renderer.setRenderTarget(rt); renderer.setClearColor(PAGE_BG, 1); renderer.clear(); renderer.render(scene, camera);
    if (f > 0 && !houseRT) {
      houseRT = rt.clone();
      report.houseTarget.allocated = true; report.houseTarget.allocations++;
      report.houseTarget.width = rt.width; report.houseTarget.height = rt.height;
    }
    if (houseRT) {
      camera.layers.set(1);
      renderer.setRenderTarget(houseRT); renderer.setClearColor(0, 0); renderer.clear();
      if (f > 0) renderer.render(scene, camera);
      camera.layers.set(0);
    }
    tiltMaterial.uniforms.uTex.value = rt.texture; tiltMaterial.uniforms.uHouseTex.value = houseRT ? houseRT.texture : emptyHouseTexture;
    tiltMaterial.uniforms.uStrength.value = q.get('tilt') === '0' ? 0 : e;
    tiltMaterial.uniforms.uTexel.value.set(1 / rt.width, 1 / rt.height);
    renderer.setRenderTarget(null); renderer.setClearColor(0, 0); renderer.render(postScene, postCam);
    if (arrows.next) arrows.next.dataset.on = e < .5 ? 'true' : 'false';
    if (arrows.back) arrows.back.dataset.on = e > .5 ? 'true' : 'false';
    report.pan = +pan.toFixed(3); report.par = [+par.x.toFixed(2), +par.y.toFixed(2)]; report.drawCalls = renderer.info.render.calls;
  }

  function parallaxLoop() {
    const now = performance.now();
    const dt = Math.max(0, now - par.last) / 1000; par.last = now;
    const x = par.x, y = par.y;
    function advance(position, velocity, target, limit) {
      const a = PARALLAX.speed / PARALLAX.ramp, direction = Math.sign(target - velocity);
      const rampTime = Math.min(dt, Math.abs(target - velocity) / a);
      const next = velocity + direction * a * rampTime;
      const distance = (velocity + next) * .5 * rampTime + target * (dt - rampTime);
      return [Math.max(-limit, Math.min(limit, position + distance)), next];
    }
    [par.x, par.vx] = advance(x, par.vx, par.tx, PARALLAX.x);
    [par.y, par.vy] = advance(y, par.vy, par.ty, PARALLAX.y);
    const moving = x !== par.x || y !== par.y;
    if (moving) draw();
    const canMove = (par.tx > 0 && par.x < PARALLAX.x) || (par.tx < 0 && par.x > -PARALLAX.x) ||
      (par.ty > 0 && par.y < PARALLAX.y) || (par.ty < 0 && par.y > -PARALLAX.y);
    par.raf = canMove ? requestAnimationFrame(parallaxLoop) : 0;
  }
  function stopParallax() {
    par.tx = par.ty = par.vx = par.vy = 0;
    cancelAnimationFrame(par.raf); par.raf = 0;
  }
  function parallaxTo(vx, vy) {
    if (reduced.matches || !finePointer.matches || raf) { stopParallax(); return; }
    if (Math.sign(vx) !== Math.sign(par.tx)) par.vx = 0;
    if (Math.sign(vy) !== Math.sign(par.ty)) par.vy = 0;
    par.tx = vx * PARALLAX.speed; par.ty = vy * PARALLAX.speed;
    if (!vx && !vy) { stopParallax(); return; }
    if (!par.raf) { par.last = performance.now(); par.raf = requestAnimationFrame(parallaxLoop); }
  }

  function animate(now) {
    const dur = reduced.matches ? 1 : 1300;
    const t = Math.min(1, (now - startT) / dur);
    pan = from + (to - from) * t;
    par.x = par.fromX * (1 - t); par.y = par.fromY * (1 - t);
    draw();
    raf = t < 1 ? requestAnimationFrame(animate) : 0;
    stage.dataset.pan = pan > .5 ? 'houses' : 'city';
    if (!raf) {
      syncSigns();
    }
  }
  function goTo(target) {
    if (to === target && (raf || pan === target)) return;
    stopSigns(false); stopParallax();
    par.fromX = par.x; par.fromY = par.y;
    from = pan; to = target; startT = performance.now();
    if (reduced.matches) {
      cancelAnimationFrame(raf); raf = 0; pan = target;
      par.x = par.y = 0;
      draw(); stage.dataset.pan = target ? 'houses' : 'city'; return;
    }
    cancelAnimationFrame(raf); raf = requestAnimationFrame(animate);
  }
  report.goTo = goTo;
  report.setPan = p => { cancelAnimationFrame(raf); raf = 0; pan = to = p; draw(); syncSigns(); };
  reduced.addEventListener('change', () => {
    stopSigns();
    if (reduced.matches) {
      cancelAnimationFrame(raf); cancelAnimationFrame(par.raf); raf = par.raf = 0;
      pan = to; stopParallax(); par.x = par.y = 0; draw();
    }
    syncSigns();
  });

  const onWelcome = () => app.dataset.stage === 'welcome';
  document.addEventListener('pointermove', ev => {
    if (!onWelcome() || ev.pointerType === 'touch' || ev.target.closest('button, input, form')) { stopParallax(); return; }
    const edge = (p, length) => {
      const band = Math.min(PARALLAX.edgeMax, length * PARALLAX.edgeRatio);
      return p < band ? -1 : p > length - band ? 1 : 0;
    };
    parallaxTo(edge(ev.clientX, innerWidth), -edge(ev.clientY, innerHeight));
  });
  document.documentElement.addEventListener('pointerleave', stopParallax);
  window.addEventListener('blur', stopParallax);
  document.addEventListener('visibilitychange', stopParallax);
  WIDE.addEventListener('change', stopParallax);
  finePointer.addEventListener('change', stopParallax);
  for (const [k, target] of [['next', 1], ['back', 0]]) {
    arrows[k] && arrows[k].addEventListener('click', () => {
      if (!onWelcome()) return;
      goTo(target);
      if (target === 0) input.blur();
    });
  }
  for (const ev of ['focus', 'pointerdown', 'input']) input.addEventListener(ev, () => { if (onWelcome()) goTo(1); });
  input.addEventListener('blur', () => setTimeout(() => {
    if (!input.value.trim() && !signLayer.contains(document.activeElement)) goTo(0);
  }, 120));
  document.addEventListener('keydown', ev => { if (ev.key === 'Escape' && onWelcome()) { input.blur(); goTo(0); } });
  new MutationObserver(() => {
    stopSigns(); cancelAnimationFrame(raf); cancelAnimationFrame(par.raf); raf = par.raf = 0;
    if (app.dataset.stage === 'welcome') {
      pan = to = 0; stopParallax(); par.x = par.y = 0;
      cards.forEach(c => { c.hovered = false; });
      stage.dataset.pan = 'city'; requestAnimationFrame(draw);
    }
  }).observe(app, { attributes: true, attributeFilter: ['data-stage'] });

  function resize() {
    renderer.setPixelRatio(Math.min(devicePixelRatio, Number(q.get('pr')) || 2));
    renderer.setSize(stage.clientWidth, stage.clientHeight, false);
    renderer.getDrawingBufferSize(size); rt.setSize(size.x, size.y);
    if (houseRT) {
      houseRT.setSize(size.x, size.y);
      report.houseTarget.width = size.x; report.houseTarget.height = size.y;
    }
    draw();
  }
  report.phase = 'first-render';
  new ResizeObserver(resize).observe(stage);
  resize();
  if (q.get('pan')) report.setPan(Number(q.get('pan')));
  app.dataset.showroom = 'ready';
  window.dispatchEvent(new Event('nw:showroom'));
  report.stage = 'ready'; report.phase = 'ready';
}

if (!stage) { report.stage = 'unsupported'; report.reason = 'missing-stage'; }
else main().catch(err => {
  console.warn('showroom disabled:', err);
  if (report.stage !== 'unsupported') report.stage = 'error';
  report.error = String(err);
  rendererForCleanup?.dispose();
  viewport.replaceChildren();
});
