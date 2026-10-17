(function () {
  const STYLE_URL = 'https://tiles.openfreemap.org/styles/positron';
  const LIBS = [
    ['link', { rel: 'stylesheet', href: 'https://cdnjs.cloudflare.com/ajax/libs/maplibre-gl/4.7.1/maplibre-gl.min.css' }],
    ['script', { src: 'https://cdnjs.cloudflare.com/ajax/libs/maplibre-gl/4.7.1/maplibre-gl.min.js' }],
    ['script', { src: 'https://cdn.jsdelivr.net/npm/@maplibre/maplibre-gl-leaflet@0.1.3/leaflet-maplibre-gl.js' }],
  ];

  const C = {
    land: '#E3E6E6', resid: '#E1E4E3', park: '#D2DECD', wood: '#D5E0CF',
    water: '#C6D4DB', waterway: '#BCCCD4', building: '#DCDFDF', buildingLine: '#D2D5D5',
    casing: '#DCDFDF', subtle: '#FFFFFF', road: '#FFFFFF', minor: '#F7F8F8', path: '#EEF0F0', rail: '#D0D4D5',
    label: '#8C8D8F', labelMid: '#6C6D70', labelStrong: '#3E3F42', halo: 'rgba(255,255,255,.85)', waterLabel: '#93A4AB',
  };
  const HIDE = /^(highway-shield|road_shield|boundary_|landcover_ice|landcover_glacier|highway-name-path|waterway_line_label)/;
  const LATIN = ['coalesce', ['get', 'name:en'], ['get', 'name:latin'], ['get', 'name']];

  function restyle(style) {
    for (const l of style.layers) {
      const p = l.paint || (l.paint = {}), id = l.id;
      if (HIDE.test(id)) { (l.layout || (l.layout = {})).visibility = 'none'; continue; }
      if (id === 'background') p['background-color'] = C.land;
      else if (id === 'park') p['fill-color'] = C.park;
      else if (id === 'landcover_wood') p['fill-color'] = C.wood;
      else if (id === 'landuse_residential') p['fill-color'] = C.resid;
      else if (id === 'water') p['fill-color'] = C.water;
      else if (id === 'waterway') p['line-color'] = C.waterway;
      else if (id === 'building') { p['fill-color'] = C.building; p['fill-outline-color'] = C.buildingLine; }
      else if (/casing/.test(id) && l.type === 'line') p['line-color'] = C.casing;
      else if (/^(highway_major_inner|highway_motorway_inner|highway_motorway_bridge_inner|tunnel_motorway_inner)$/.test(id)) p['line-color'] = C.road;
      else if (/_subtle$/.test(id)) p['line-color'] = C.subtle;
      else if (id === 'highway_minor') p['line-color'] = C.minor;
      else if (id === 'highway_path') p['line-color'] = C.path;
      else if (/^railway/.test(id) && !/dashline/.test(id)) p['line-color'] = C.rail;
      if (l.type === 'symbol' && l.layout && l.layout['text-field'] && !/shield/.test(id)) {
        l.layout['text-field'] = LATIN;
        const strong = /label_city|label_town/.test(id), water = /^water_name/.test(id);
        p['text-color'] = water ? C.waterLabel : strong ? C.labelStrong : /label_/.test(id) ? C.labelMid : C.label;
        p['text-halo-color'] = C.halo; p['text-halo-width'] = 1.2; p['text-halo-blur'] = 0.6;
      }
    }
    return style;
  }

  function hasWebGL() {
    try { const c = document.createElement('canvas'); return !!(c.getContext('webgl2') || c.getContext('webgl')); }
    catch (_) { return false; }
  }

  function load([tag, attrs]) {
    return new Promise((ok, fail) => {
      const el = document.createElement(tag);
      Object.assign(el, attrs);
      el.onload = ok; el.onerror = () => fail(new Error('load failed: ' + (attrs.src || attrs.href)));
      document.head.appendChild(el);
    });
  }

  let ready = null;
  window.nwGlassBase = function () {
    if (!ready) ready = (async () => {
      if (!hasWebGL()) return null;
      const [style] = await Promise.all([
        fetch(STYLE_URL).then(r => { if (!r.ok) throw new Error('style ' + r.status); return r.json(); }),
        LIBS.reduce((p, lib) => p.then(() => load(lib)), Promise.resolve()),
      ]);
      if (!window.maplibregl || !(window.L && L.maplibreGL)) return null;
      return restyle(style);
    })().catch(err => { console.warn('[map] vector basemap unavailable, using raster tiles:', err); return null; });
    return ready;
  };
})();
