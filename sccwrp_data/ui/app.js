/* SCCWRP Data Explorer: talks to the local engine service (python -m sccwrp_data serve).
   Inside ArcGIS Pro it runs in the add-in's WebView2 panel and asks Pro to add results to the map. */
(() => {
  'use strict';
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const pro = window.chrome && window.chrome.webview ? window.chrome.webview : null;
  const store = {
    get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* private window */ } },
  };

  const AREA_GROUPS = [['land', 'Land'], ['ocean', 'Ocean'], ['custom', 'Custom']];
  const AREA_MODES = [
    { id: 'county', label: 'County', click: true, group: 'land' },
    { id: 'county-coastal', label: 'County + coast', click: true, group: 'land' },
    { id: 'huc8', label: 'HUC 8', click: true, group: 'land' },
    { id: 'huc10', label: 'HUC 10', click: true, group: 'land' },
    { id: 'huc12', label: 'HUC 12', click: true, group: 'land' },
    { id: 'regional-board', label: 'Regional Board', click: true, group: 'land' },
    { id: 'smc-watershed', label: 'SMC watershed', click: true, group: 'land' },
    { id: 'socal', label: 'SoCal', fixed: true, group: 'land' },
    { id: 'ca-watersheds', label: 'All California', fixed: true, group: 'land' },
    { id: 'marine-region', label: 'Marine region', click: true, group: 'ocean' },
    { id: 'bight-strata', label: 'Bight strata', click: true, group: 'ocean' },
    { id: 'mpa', label: 'MPA', click: true, group: 'ocean' },
    { id: 'asbs', label: 'ASBS', click: true, group: 'ocean' },
    { id: 'bight', label: 'Whole Bight', fixed: true, group: 'ocean' },
    { id: 'state-waters', label: 'State waters', fixed: true, group: 'ocean' },
    { id: 'depth', label: 'Depth band', form: true, group: 'ocean' },
    { id: 'draw', label: 'Draw', group: 'custom' },
    { id: 'point', label: 'Point + radius', group: 'custom' },
  ];
  const BOLT = '<svg class="bolt" viewBox="0 0 24 24"><path d="M13 2 4 14h7l-1 8 9-12h-7l1-8z" fill="currentColor"/></svg>';
  const TIER = {
    instant: { label: 'Instant', icon: BOLT },
    'on-demand': { label: 'On demand', icon: '<svg viewBox="0 0 24 24"><path d="M7 18a5 5 0 1 1 .9-9.9A6 6 0 0 1 19 10a4 4 0 0 1-1 7.9H7z" fill="none" stroke="currentColor" stroke-width="2"/></svg>' },
    internal: { label: 'SCCWRP network', icon: '<svg viewBox="0 0 24 24"><rect x="4" y="10" width="16" height="10" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3" fill="none" stroke="currentColor" stroke-width="2"/></svg>' },
  };
  // Esri basemaps (no key needed; SCCWRP is an Esri customer). base + relief below the data, labels above it.
  const ESRI = n => [`https://services.arcgisonline.com/ArcGIS/rest/services/${n}/MapServer/tile/{z}/{y}/{x}`];
  const BASEMAPS = {
    dark: { base: ESRI('Canvas/World_Dark_Gray_Base'), relief: ESRI('Elevation/World_Hillshade_Dark'), reliefOpacity: 0.55,
            ref: ESRI('Canvas/World_Dark_Gray_Reference'), attr: 'Esri, HERE, Garmin, USGS, NGA' },
    light: { base: ESRI('Canvas/World_Light_Gray_Base'), relief: ESRI('Elevation/World_Hillshade'), reliefOpacity: 0.35,
             ref: ESRI('Canvas/World_Light_Gray_Reference'), attr: 'Esri, HERE, Garmin, USGS, NGA' },
    imagery: { base: ESRI('World_Imagery'), relief: null, reliefOpacity: 0,
               ref: ESRI('Reference/World_Boundaries_and_Places'), attr: 'Esri, Maxar, Earthstar Geographics' },
    ocean: { base: ESRI('Ocean/World_Ocean_Base'), relief: null, reliefOpacity: 0,
             ref: ESRI('Ocean/World_Ocean_Reference'), attr: 'Esri, GEBCO, NOAA, National Geographic, Garmin, HERE' },
  };
  const BASEMAP_CYCLE = ['theme', 'ocean', 'imagery'];

  const S = {
    catalog: null, dataset: null, layer: null, category: null, query: '',
    group: 'land', mode: null, selected: new Map(), fixedArea: null, drawn: null, drawing: [], method: 'clip', mask: 'both',
    point: null, pointKm: 2, depth: null,
    job: null, lastResult: null, basemap: 'theme',
  };

  // ---------- theme ----------
  const params = new URLSearchParams(location.search);
  function setTheme(t) {
    document.documentElement.dataset.theme = t;
    store.set('sd-theme', t);
    if (map && S.basemap === 'theme') setBasemap('theme');
  }
  $('#btn-theme').onclick = () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
  document.documentElement.dataset.theme = params.get('theme') || store.get('sd-theme', 'dark');

  // ---------- helpers ----------
  async function api(path, opts) {
    const r = await fetch(path, opts);
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
    return body;
  }
  function toast(msg) {
    const t = $('#toast'); t.textContent = msg; t.classList.add('show');
    clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove('show'), 2200);
  }
  function countUp(el, to) {
    const t0 = performance.now(), dur = 900;
    const step = now => { const k = Math.min(1, (now - t0) / dur); el.textContent = Math.round(to * (1 - Math.pow(1 - k, 3))); if (k < 1) requestAnimationFrame(step); };
    requestAnimationFrame(step);
  }
  const fmtKm2 = v => v >= 10000 ? `${Math.round(v).toLocaleString()} km²` : `${(+v).toLocaleString(undefined, { maximumFractionDigits: 1 })} km²`;
  const tierOf = ds => ds.layers.some(l => l.tier === 'instant') ? 'instant' : ds.layers.every(l => l.tier === 'internal') ? 'internal' : 'on-demand';

  // ---------- tabs (narrow layouts) ----------
  function showTab(name) {
    $$('#tabs button').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
    $$('[data-tab-panel]').forEach(p => p.classList.toggle('shown', p.dataset.tabPanel === name));
    if (name === 'map' && map) setTimeout(() => map.resize(), 30);
  }
  $$('#tabs button').forEach(b => b.onclick = () => showTab(b.dataset.tab));
  showTab('catalog');

  // ---------- catalog ----------
  function renderCategories() {
    const cats = [...new Set(S.catalog.datasets.map(d => d.category))].sort();
    $('#categories').innerHTML = ['All', ...cats].map(c =>
      `<button class="chip ${(!S.category && c === 'All') || S.category === c ? 'active' : ''}" data-c="${esc(c)}">${esc(c)}</button>`).join('');
    $$('#categories .chip').forEach(b => b.onclick = () => { S.category = b.dataset.c === 'All' ? null : b.dataset.c; renderCategories(); renderList(); });
  }
  function renderList() {
    const q = S.query.toLowerCase();
    const order = { instant: 0, 'on-demand': 1, internal: 2 };
    const hits = S.catalog.datasets
      .filter(d => !S.category || d.category === S.category)
      .filter(d => !q || [d.title, d.provider, d.id, d.category, ...d.layers.map(l => l.title)].join(' ').toLowerCase().includes(q))
      .sort((a, b) => order[tierOf(a)] - order[tierOf(b)] || a.title.localeCompare(b.title));
    $('#dataset-list').innerHTML = hits.map((d, i) => {
      const tier = tierOf(d);
      const n = d.layers.length;
      return `<button class="card ${S.dataset?.id === d.id ? 'active' : ''}" data-id="${esc(d.id)}" role="option" style="animation-delay:${Math.min(i, 12) * 25}ms">
        <div class="card-top"><span class="badge ${tier}">${tier === 'instant' ? BOLT : ''}${TIER[tier].label}</span></div>
        <div class="card-title">${esc(d.title)}</div>
        <div class="card-sub"><span>${esc(d.provider)}</span><span>·</span><span>${n} layer${n > 1 ? 's' : ''}</span></div>
      </button>`;
    }).join('') || '<p style="color:var(--muted);padding:12px">No datasets match.</p>';
    $$('#dataset-list .card').forEach(c => c.onclick = () => selectDataset(c.dataset.id));
  }
  $('#search').oninput = e => { S.query = e.target.value; renderList(); };

  function selectDataset(id, layerId) {
    S.dataset = S.catalog.datasets.find(d => d.id === id);
    S.layer = S.dataset.layers.find(l => l.id === (layerId || S.dataset.default_layer)) || S.dataset.layers[0];
    renderList();
    $('#request-empty').classList.add('hidden');
    $('#request-body').classList.remove('hidden');
    $('#ds-cat').textContent = S.dataset.category;
    $('#ds-title').textContent = S.dataset.title;
    $('#ds-meta').innerHTML = `<span>${esc(S.dataset.provider)} · ${esc(S.dataset.versions?.latest || '')}</span>` +
      (S.dataset.landing ? `<a href="${esc(S.dataset.landing)}" target="_blank" rel="noopener">Provider page ↗</a>` : '');
    $('#layer').innerHTML = S.dataset.layers.map(l =>
      `<option value="${esc(l.id)}" ${l.id === S.layer.id ? 'selected' : ''}>${l.tier === 'instant' ? '⚡ ' : ''}${esc(l.title)}</option>`).join('');
    renderLayer();
    hide('#result', '#error', '#progress');
    if (matchMedia('(max-width: 860px)').matches) showTab(S.mode ? 'request' : 'map');
  }
  $('#layer').onchange = e => { S.layer = S.dataset.layers.find(l => l.id === e.target.value); renderLayer(); };

  function renderLayer() {
    const l = S.layer;
    const b = $('#tier-banner');
    b.className = `tier-banner ${l.tier}`;
    const txt = {
      instant: [`Instant · SCCWRP fast copy`, `California copy${l.staged?.features ? ` of ${l.staged.features.toLocaleString()} features` : ''}, built ${(l.staged?.built || '').slice(0, 10)}. Expect a second or two.`],
      'on-demand': [`On demand · ${S.dataset.provider}`, 'Fetched from the provider for your area, then cached for 60 days.'],
      internal: ['SCCWRP network · internal copy', 'Read from SCCWRP\'s own copy on the server.'],
    }[l.tier];
    b.innerHTML = `<span class="ico">${TIER[l.tier].icon}</span><span><b>${esc(txt[0])}</b>${esc(txt[1])}</span>`;
    $('#params').innerHTML = Object.entries(l.params || {}).map(([k, p]) => paramField(k, p)).join('');
    const fmts = { vector: ['gpkg', 'gdb', 'shp', 'geojson', 'parquet'], raster: ['tif', 'gtiff'], pointcloud: ['laz'] }[l.kind] || ['gpkg'];
    const names = { gpkg: 'GeoPackage', gdb: 'File geodatabase', shp: 'Shapefile', geojson: 'GeoJSON', parquet: 'GeoParquet', tif: 'Cloud-optimized GeoTIFF', gtiff: 'GeoTIFF', laz: 'LAZ point cloud' };
    $('#fmt').innerHTML = fmts.map(f => `<option value="${f}">${names[f]}</option>`).join('');
    $('#res-field').classList.toggle('hidden', l.kind === 'vector');
    updateGo();
  }
  function paramField(k, p) {
    const label = esc(p.description || k);
    if (p.type === 'choice') return `<label class="field"><span>${label}</span><select data-p="${k}">${p.choices.map(c => `<option ${c === p.default ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select></label>`;
    if (p.type === 'int') return `<label class="field"><span>${label}</span><input data-p="${k}" type="number" min="${p.min ?? ''}" max="${p.max ?? ''}" value="${p.default ?? ''}"></label>`;
    if (p.type === 'dates') {
      const d = p.min && p.min > '2020-01-01' ? p.min : '2020-01-01';
      return `<div class="field"><span>${label}</span><div class="row2"><input data-p="${k}" data-part="start" type="date" min="${p.min || ''}" value="${d}"><input data-p="${k}" data-part="end" type="date" min="${p.min || ''}" value="${d}"></div></div>`;
    }
    return `<label class="field"><span>${label}</span><input data-p="${k}" value="${esc(p.default ?? '')}"></label>`;
  }
  function readParams() {
    const out = {};
    for (const el of $$('#params [data-p]')) {
      const k = el.dataset.p;
      if (el.dataset.part) {
        const s = $(`#params [data-p="${k}"][data-part="start"]`).value, e = $(`#params [data-p="${k}"][data-part="end"]`).value;
        out[k] = e && e !== s ? `${s}/${e}` : s;
      } else if (el.value !== '') out[k] = el.value;
    }
    return out;
  }

  // ---------- map ----------
  let map;
  const raster = (tiles, attr) => ({ type: 'raster', tiles, tileSize: 256, attribution: attr, maxzoom: 19 });
  function setBasemap(which) {
    S.basemap = which;
    const b = BASEMAPS[which !== 'theme' ? which : document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'];
    map.getSource('base').setTiles(b.base);
    map.getSource('ref').setTiles(b.ref);
    if (b.relief) map.getSource('relief').setTiles(b.relief);
    map.setPaintProperty('relief', 'raster-opacity', b.reliefOpacity);
  }
  $('#btn-basemap').onclick = () => {
    const next = BASEMAP_CYCLE[(BASEMAP_CYCLE.indexOf(S.basemap) + 1) % BASEMAP_CYCLE.length];
    setBasemap(next);
    toast({ theme: 'Standard basemap', ocean: 'Ocean basemap', imagery: 'Imagery' }[next]);
  };

  function initMap() {
    const b = BASEMAPS[document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'];
    map = new maplibregl.Map({
      container: 'map', attributionControl: { compact: true },
      style: { version: 8,
        sources: { base: raster(b.base, b.attr), relief: raster(b.relief, ''), ref: raster(b.ref, '') },
        layers: [{ id: 'base', type: 'raster', source: 'base' },
                 { id: 'relief', type: 'raster', source: 'relief', paint: { 'raster-opacity': b.reliefOpacity } }] },
      center: [-119.4, 37.2], zoom: 4.6, pitch: 0, maxPitch: 60,
    });
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'bottom-right');
    map.doubleClickZoom.disable();
    map.on('load', () => {
      const sel = getComputedStyle(document.documentElement).getPropertyValue('--select').trim() || '#22d3ee';
      map.addSource('units', { type: 'geojson', data: empty(), promoteId: 'code' });
      map.addLayer({ id: 'units-fill', type: 'fill', source: 'units', paint: {
        'fill-color': sel,
        'fill-opacity': ['case', ['boolean', ['feature-state', 'selected'], false], 0.32, ['boolean', ['feature-state', 'hover'], false], 0.14, 0.02] } });
      map.addLayer({ id: 'units-line', type: 'line', source: 'units', paint: {
        'line-color': ['case', ['boolean', ['feature-state', 'selected'], false], sel, 'rgba(160,190,230,0.55)'],
        'line-width': ['case', ['boolean', ['feature-state', 'selected'], false], 2.4, 0.7] } });
      map.addSource('area', { type: 'geojson', data: empty() });
      map.addLayer({ id: 'area-glow', type: 'line', source: 'area', paint: { 'line-color': sel, 'line-width': 9, 'line-blur': 7, 'line-opacity': 0.5 } });
      map.addLayer({ id: 'area-line', type: 'line', source: 'area', paint: { 'line-color': sel, 'line-width': 2.2 } });
      map.addLayer({ id: 'area-fill', type: 'fill', source: 'area', paint: { 'fill-color': sel, 'fill-opacity': 0.08 } });
      map.addSource('draw', { type: 'geojson', data: empty() });
      map.addLayer({ id: 'draw-line', type: 'line', source: 'draw', paint: { 'line-color': sel, 'line-width': 2, 'line-dasharray': [2, 1.5] } });
      map.addLayer({ id: 'draw-pt', type: 'circle', source: 'draw', filter: ['==', '$type', 'Point'], paint: { 'circle-radius': 4.5, 'circle-color': '#fff', 'circle-stroke-color': sel, 'circle-stroke-width': 2 } });
      map.addSource('result', { type: 'geojson', data: empty() });
      const hot = '#fbbf24';
      map.addLayer({ id: 'result-fill', type: 'fill', source: 'result', filter: ['==', '$type', 'Polygon'], paint: { 'fill-color': hot, 'fill-opacity': 0.25 } });
      map.addLayer({ id: 'result-glow', type: 'line', source: 'result', filter: ['!=', '$type', 'Point'], paint: { 'line-color': hot, 'line-width': 6, 'line-blur': 5, 'line-opacity': 0.45 } });
      map.addLayer({ id: 'result-line', type: 'line', source: 'result', filter: ['!=', '$type', 'Point'], paint: { 'line-color': '#fde68a', 'line-width': 1.3 } });
      map.addLayer({ id: 'result-pt', type: 'circle', source: 'result', filter: ['==', '$type', 'Point'], paint: { 'circle-radius': 4, 'circle-color': hot, 'circle-stroke-color': '#fff', 'circle-stroke-width': 1 } });
      map.addLayer({ id: 'ref', type: 'raster', source: 'ref' });   // labels above the data
      bindMapEvents();
      renderModes();
    });
  }
  const empty = () => ({ type: 'FeatureCollection', features: [] });

  let hoverId = null;
  function bindMapEvents() {
    map.on('mousemove', 'units-fill', e => {
      const f = e.features[0];
      if (hoverId !== null && hoverId !== f.id) map.setFeatureState({ source: 'units', id: hoverId }, { hover: false });
      hoverId = f.id; map.setFeatureState({ source: 'units', id: hoverId }, { hover: true });
      map.getCanvas().style.cursor = 'pointer';
      const c = $('#hover-card');
      c.innerHTML = `<b>${esc(f.properties.name)}</b>${esc(f.properties.code !== f.properties.name ? f.properties.code : '')} · ${fmtKm2(f.properties.km2)}`;
      c.style.left = `${e.point.x + 14}px`; c.style.top = `${e.point.y + 14}px`; c.classList.remove('hidden');
    });
    map.on('mouseleave', 'units-fill', () => {
      if (hoverId !== null) map.setFeatureState({ source: 'units', id: hoverId }, { hover: false });
      hoverId = null; map.getCanvas().style.cursor = ''; $('#hover-card').classList.add('hidden');
    });
    map.on('click', 'units-fill', e => {
      if (S.mode === 'draw') return;
      const f = e.features[0];
      const on = !S.selected.has(f.id);
      if (on) S.selected.set(f.id, { code: f.id, name: f.properties.name, km2: f.properties.km2 });
      else S.selected.delete(f.id);
      map.setFeatureState({ source: 'units', id: f.id }, { selected: on });
      renderSelection();
    });
    map.on('click', e => {
      if (S.mode === 'point') { S.point = [e.lngLat.lng, e.lngLat.lat]; renderPoint(); renderSelection(); return; }
      if (S.mode !== 'draw') return;
      S.drawing.push([e.lngLat.lng, e.lngLat.lat]); renderDraw();
    });
    map.on('dblclick', e => {
      if (S.mode !== 'draw' || S.drawing.length < 3) return;
      e.preventDefault();
      const ring = [...S.drawing, S.drawing[0]];
      S.drawn = { type: 'Polygon', coordinates: [ring] };
      S.drawing = [];
      map.getSource('draw').setData(empty());
      map.getSource('area').setData({ type: 'Feature', geometry: S.drawn, properties: {} });
      $('#draw-hint').classList.add('hidden');
      renderSelection();
    });
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && S.mode === 'draw') { S.drawing = []; renderDraw(); }
    });
  }
  function circle([lon, lat], km, n = 72) {
    const dy = km / 110.574, dx = km / (111.32 * Math.cos(lat * Math.PI / 180));
    const ring = [...Array(n + 1)].map((_, i) => { const t = (i % n) / n * 2 * Math.PI; return [lon + dx * Math.cos(t), lat + dy * Math.sin(t)]; });
    return { type: 'Feature', geometry: { type: 'Polygon', coordinates: [ring] }, properties: {} };
  }
  function renderPoint() {
    if (!S.point) { map.getSource('area').setData(empty()); map.getSource('draw').setData(empty()); return; }
    map.getSource('area').setData(circle(S.point, S.pointKm));
    map.getSource('draw').setData({ type: 'FeatureCollection', features: [{ type: 'Feature', geometry: { type: 'Point', coordinates: S.point } }] });
  }
  function renderDraw() {
    const pts = S.drawing.map(c => ({ type: 'Feature', geometry: { type: 'Point', coordinates: c } }));
    const line = S.drawing.length > 1 ? [{ type: 'Feature', geometry: { type: 'LineString', coordinates: S.drawing } }] : [];
    map.getSource('draw').setData({ type: 'FeatureCollection', features: [...line, ...pts] });
  }

  function renderModes() {
    $('#area-groups').innerHTML = AREA_GROUPS.map(([id, label]) => `<button role="tab" data-g="${id}" class="${S.group === id ? 'active' : ''}">${label}</button>`).join('');
    $$('#area-groups button').forEach(b => b.onclick = () => {
      S.group = b.dataset.g; renderModes();
      if (S.group === 'ocean' && S.basemap === 'theme') setBasemap('ocean');
      if (S.group === 'land' && S.basemap === 'ocean') setBasemap('theme');
    });
    $('#area-modes').innerHTML = AREA_MODES.filter(m => m.group === S.group)
      .map(m => `<button role="tab" data-m="${m.id}" class="${S.mode === m.id ? 'active' : ''}">${m.label}</button>`).join('');
    $$('#area-modes button').forEach(b => b.onclick = () => setMode(b.dataset.m));
    renderAreaForm();
  }
  function renderAreaForm() {
    const f = $('#area-form');
    if (S.mode === 'depth') {
      f.innerHTML = `<label>From <input id="d-lo" type="number" min="0" value="${S.depth ? S.depth.lo : 30}"> to <input id="d-hi" type="number" min="1" value="${S.depth ? S.depth.hi : 120}"> m deep</label>
        <label>within <select id="d-in"><option value="">all California waters</option><option value="bight">the Bight</option>
        <option value="marine-region:NCSR">North Coast</option><option value="marine-region:NCCSR">North Central Coast</option>
        <option value="marine-region:SFBSR">San Francisco Bay</option><option value="marine-region:CCSR">Central Coast</option>
        <option value="marine-region:SCSR">South Coast</option></select></label><button class="btn" id="d-go">Show</button>`;
      $('#d-in').value = S.depth ? S.depth.within : '';
      $('#d-go').onclick = () => { S.depth = { lo: +$('#d-lo').value, hi: +$('#d-hi').value, within: $('#d-in').value }; loadFixed(depthSpec()); };
    } else if (S.mode === 'point') {
      f.innerHTML = `<label>Radius <input id="p-km" type="range" min="0.5" max="25" step="0.5" value="${S.pointKm}"> <b id="p-km-v">${S.pointKm} km</b></label><span class="hint">Click the map to place the point</span>`;
      $('#p-km').oninput = e => { S.pointKm = +e.target.value; $('#p-km-v').textContent = `${S.pointKm} km`; renderPoint(); renderSelection(); };
    } else f.innerHTML = '';
    f.classList.toggle('hidden', !f.innerHTML);
  }
  const depthSpec = () => `${S.depth.within ? S.depth.within + '&' : ''}depth:${S.depth.lo}-${S.depth.hi}`;
  async function loadFixed(spec) {
    $('#area-summary').textContent = 'Building the area…';
    try {
      const a = await api(`/api/area?spec=${encodeURIComponent(spec)}`);
      S.fixedArea = { ...a, spec };
      map.getSource('area').setData(a.geojson);
      fit(a.geojson, 40);
      $('#area-summary').innerHTML = `<b>${esc(a.label)}</b> · ${fmtKm2(a.km2)}`;
    } catch (err) { $('#area-summary').textContent = err.message; }
    renderSelection();
  }
  async function setMode(id) {
    const m = AREA_MODES.find(x => x.id === id);
    S.mode = id; S.selected.clear(); S.fixedArea = null; S.drawn = null; S.drawing = []; S.point = null;
    renderModes();
    ['units', 'area', 'draw'].forEach(s => map.getSource(s).setData(empty()));
    $('#draw-hint').classList.toggle('hidden', id !== 'draw');
    map.getCanvas().style.cursor = id === 'draw' || id === 'point' ? 'crosshair' : '';
    if (id === 'draw') $('#area-summary').textContent = 'Draw the area on the map';
    if (id === 'point') $('#area-summary').textContent = 'Click the map to place a point';
    if (m.form) $('#area-summary').textContent = 'Set the depth range, then Show';
    renderSelection();
    if (m.click) {
      $('#area-summary').innerHTML = 'Loading boundaries…';
      try {
        const gj = await api(`/api/boundaries?kind=${id}`);
        if (S.mode !== id) return;
        map.getSource('units').setData(gj);
        fit(gj, 40);
        $('#area-summary').innerHTML = `Click to select · <b>${gj.features.length.toLocaleString()}</b> ${m.label.toLowerCase()} units`;
      } catch (err) { $('#area-summary').textContent = err.message; }
    } else if (m.fixed) {
      await loadFixed(id);
    }
  }
  function fit(gj, pad) {
    const b = new maplibregl.LngLatBounds();
    const walk = c => typeof c[0] === 'number' ? b.extend(c) : c.forEach(walk);
    (gj.features || [gj]).forEach(f => f.geometry && walk(f.geometry.coordinates));
    if (!b.isEmpty()) map.fitBounds(b, { padding: { top: 90 + pad, bottom: 60 + pad, left: pad, right: pad }, duration: 900, maxZoom: 12 });
  }

  function areaSpec() {
    if (!S.mode) return null;
    if (S.mode === 'draw') return S.drawn ? { type: 'Feature', geometry: S.drawn } : null;
    if (S.mode === 'point') return S.point ? `point:${S.point[0].toFixed(6)},${S.point[1].toFixed(6)}` : null;
    const m = AREA_MODES.find(x => x.id === S.mode);
    if (m.fixed || m.form) return S.fixedArea ? S.fixedArea.spec : null;
    if (!S.selected.size) return null;
    const vals = [...S.selected.values()].map(v => ['smc-watershed', 'bight-strata'].includes(S.mode) ? v.name : v.code);
    return `${S.mode}:${vals.join(',')}`;
  }
  function areaLabel() {
    if (S.mode === 'draw') return S.drawn ? 'Drawn area' : null;
    if (S.mode === 'point') return S.point ? `Point + ${S.pointKm} km · ${fmtKm2(Math.PI * S.pointKm ** 2)}` : null;
    if (S.fixedArea) return `${S.fixedArea.label} · ${fmtKm2(S.fixedArea.km2)}`;
    if (!S.selected.size) return null;
    const v = [...S.selected.values()];
    const km2 = v.reduce((s, x) => s + (+x.km2 || 0), 0);
    return `${v.length === 1 ? v[0].name : `${v.length} ${AREA_MODES.find(x => x.id === S.mode).label} units`} · ${fmtKm2(km2)}`;
  }
  function renderSelection() {
    const box = $('#selection');
    const chips = [...S.selected.values()];
    box.classList.toggle('hidden', !chips.length);
    $('#sel-chips').innerHTML = chips.map(c => `<span class="sel-chip">${esc(c.name)}<button data-code="${esc(c.code)}" aria-label="Remove ${esc(c.name)}">×</button></span>`).join('');
    $$('#sel-chips button').forEach(b => b.onclick = () => {
      S.selected.delete(b.dataset.code); map.setFeatureState({ source: 'units', id: b.dataset.code }, { selected: false }); renderSelection();
    });
    const label = areaLabel();
    const line = $('#area-line');
    line.classList.toggle('ok', !!label);
    line.querySelector('span').textContent = label || 'No area selected: pick one on the map';
    updateGo();
  }
  $('#btn-clear').onclick = () => { for (const k of S.selected.keys()) map.setFeatureState({ source: 'units', id: k }, { selected: false }); S.selected.clear(); renderSelection(); };

  // ---------- request ----------
  $('#buffer').oninput = e => { $('#buffer-val').textContent = `${e.target.value} km`; };
  $$('#method button').forEach(b => b.onclick = () => { S.method = b.dataset.v; $$('#method button').forEach(x => x.classList.toggle('active', x === b)); });
  $$('#mask button').forEach(b => b.onclick = () => { S.mask = b.dataset.v; $$('#mask button').forEach(x => x.classList.toggle('active', x === b)); });
  function updateGo() { $('#btn-get').disabled = !(S.dataset && S.layer && areaSpec()) || !!S.job; }
  function hide(...ids) { ids.forEach(i => $(i).classList.add('hidden')); }

  $('#btn-get').onclick = async () => {
    const body = {
      dataset: S.dataset.id, layer: S.layer.id, area: areaSpec(), params: readParams(),
      buffer_km: S.mode === 'point' ? S.pointKm + +$('#buffer').value : +$('#buffer').value,
      method: S.method, mask: S.mask, crs: $('#crs').value.trim() || undefined,
      fmt: $('#fmt').value, resolution: $('#resolution').value ? +$('#resolution').value : undefined,
      out_dir: $('#out-dir').value.trim() || undefined, refresh: $('#refresh').checked,
    };
    hide('#result', '#error');
    $('#progress').classList.remove('hidden');
    $('#log').innerHTML = '';
    const go = $('#btn-get'); go.classList.add('busy'); $('.go-label').textContent = 'Working…';
    map.getSource('result').setData(empty());
    clearRaster();
    const t0 = performance.now();
    try {
      const { job } = await api('/api/get', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      S.job = job; updateGo();
      let seen = 0, d;
      for (;;) {
        await new Promise(r => setTimeout(r, 120));
        d = await api(`/api/jobs/${job}`);
        for (; seen < d.log.length; seen++) $('#log').insertAdjacentHTML('beforeend', `<li>${esc(d.log[seen])}</li>`);
        $('#log').scrollTop = 1e9;
        if (d.status !== 'running') break;
      }
      if (d.status === 'error') throw new Error(d.error);
      showResult(d, (performance.now() - t0) / 1000);
    } catch (err) {
      const e = $('#error'); e.textContent = err.message; e.classList.remove('hidden');
    } finally {
      S.job = null; go.classList.remove('busy'); $('.go-label').textContent = 'Get data'; hide('#progress'); updateGo();
    }
  };

  function showResult(d, wall) {
    S.lastResult = d;
    const m = d.manifest;
    const t = Math.max(wall, d.seconds || 0);
    const el = $('#res-time');
    const t0 = performance.now();
    const tick = now => { const k = Math.min(1, (now - t0) / 600); el.textContent = (t * k).toFixed(t < 10 ? 2 : 1); if (k < 1) requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
    const fast = (m.sources || []).some(s => s.startsWith('SCCWRP fast copy'));
    $('#res-src').innerHTML = d.cached ? `${BOLT} From your cache` : fast ? `${BOLT} SCCWRP fast copy` : esc(m.provider);
    const f0 = m.files[0] || {};
    const stats = [];
    if (f0.features != null) stats.push([f0.features.toLocaleString(), 'features']);
    if (f0.width) stats.push([`${f0.width.toLocaleString()} × ${f0.height.toLocaleString()}`, 'cells']);
    if (f0.cell_size) stats.push([`${f0.cell_size} m`, 'cell size']);
    if (f0.statistics) stats.push([`${f0.statistics.min} – ${f0.statistics.max}`, 'value range']);
    if (m.files.length > 1) stats.push([m.files.length, 'files']);
    stats.push([fmtKm2(m.area.km2), m.area.label.length > 24 ? 'area' : m.area.label]);
    stats.push([m.crs.replace('EPSG:', ''), m.crs === 'EPSG:26911' ? 'UTM 11N' : 'EPSG']);
    $('#res-stats').innerHTML = stats.map(([b, s]) => `<div><b>${esc(b)}</b><span>${esc(s)}</span></div>`).join('');
    $('#res-files').innerHTML = d.files.map(f => `<li>${esc(f)}</li>`).join('');
    $('#res-notes').innerHTML = (d.notes || []).map(n => `<div>• ${esc(n)}</div>`).join('');
    $('#btn-add').hidden = !pro;
    $('#btn-open').hidden = !pro;
    $('#result').classList.remove('hidden');
    if (d.preview && d.preview.geojson) {
      map.getSource('result').setData(d.preview.geojson);
      fit(d.preview.geojson, 30);
    }
    if (d.preview && d.preview.type === 'raster') showRaster(d.preview);
  }
  function clearRaster() {
    if (map.getLayer('result-img')) map.removeLayer('result-img');
    if (map.getSource('result-img')) map.removeSource('result-img');
    $('#legend').classList.add('hidden');
  }
  function showRaster(p) {
    clearRaster();
    map.addSource('result-img', { type: 'image', url: p.image, coordinates: p.coordinates });
    map.addLayer({ id: 'result-img', type: 'raster', source: 'result-img',
      paint: { 'raster-opacity': 0, 'raster-opacity-transition': { duration: 900 }, 'raster-resampling': 'nearest' } }, 'area-glow');
    requestAnimationFrame(() => map.setPaintProperty('result-img', 'raster-opacity', 0.92));
    fit({ type: 'Feature', geometry: { type: 'Polygon', coordinates: [[...p.coordinates, p.coordinates[0]]] } }, 30);
    const L = p.legend, el = $('#legend');
    if (!L) return;
    if (L.type === 'classes') el.innerHTML = `<b>Land cover</b>${L.items.map(([c, n]) => `<div><i style="background:${c}"></i>${esc(n)}</div>`).join('')}`;
    else if (L.type === 'sealevel' && L.max <= 0) el.innerHTML = `<b>Depth (m)</b><div class="ramp sea"></div><div class="ramp-labels"><span>${L.min}</span><span>${L.max}</span></div>`;
    else if (L.type === 'sealevel') el.innerHTML = `<b>Elevation / depth (m)</b><div class="ramp sealevel"></div><div class="ramp-labels"><span>${L.min}</span><span>0</span><span>${L.max}</span></div>`;
    else el.innerHTML = `<b>Value</b><div class="ramp seq"></div><div class="ramp-labels"><span>${L.min}</span><span>${L.max}</span></div>`;
    el.classList.remove('hidden');
  }
  $('#btn-copy').onclick = async () => {
    const txt = (S.lastResult?.files || []).join('\n');
    try { await navigator.clipboard.writeText(txt); toast('Path copied'); } catch { toast(txt); }
  };
  $('#btn-add').onclick = () => { pro.postMessage({ action: 'addToMap', files: S.lastResult.files, dataset: S.dataset.id, layer: S.layer.id }); toast('Adding to the map…'); };
  $('#btn-open').onclick = () => pro.postMessage({ action: 'openFolder', path: S.lastResult.files[0] });
  if (pro) pro.addEventListener('message', e => { const m = typeof e.data === 'string' ? JSON.parse(e.data) : e.data; if (m.toast) toast(m.toast); });

  // Test hook (?test=1): lets the automated UI test select areas the way a map click does
  if (params.get('test')) {
    window.__sd = { S, selectDataset, setMode, get map() { return map; },
      pick(code, name, km2) { S.selected.set(code, { code, name, km2 }); map.setFeatureState({ source: 'units', id: code }, { selected: true }); renderSelection(); } };
  }

  // ---------- start ----------
  async function start() {
    initMap();
    try {
      S.catalog = await api('/api/catalog');
    } catch (err) {
      $('#dataset-list').innerHTML = `<p class="error">Could not reach the SCCWRP data service: ${esc(err.message)}</p>`;
      return;
    }
    const instant = S.catalog.datasets.reduce((n, d) => n + d.layers.filter(l => l.tier === 'instant').length, 0);
    countUp($('#stat-datasets'), S.catalog.datasets.length);
    countUp($('#stat-instant'), instant);
    renderCategories(); renderList();
    const want = params.get('dataset');
    if (want && S.catalog.datasets.some(d => d.id === want)) selectDataset(want, params.get('layer'));
  }
  start();
})();
