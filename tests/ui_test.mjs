// End-to-end UI test: drives the data view in headless Edge through the DevTools protocol (no packages needed).
// Needs the service running: python -m sccwrp_data serve
//   node tests/ui_test.mjs [outdir]
// Writes screenshots (wide and narrow) and fails on JavaScript errors or a failed request.
import { spawn } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const OUT = process.argv[2] || join(process.cwd(), 'tests', 'output', 'ui');
mkdirSync(OUT, { recursive: true });
const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const PORT = 9333;
const URL = 'http://127.0.0.1:8765/?test=1';
const sleep = ms => new Promise(r => setTimeout(r, ms));

const edge = spawn(EDGE, ['--headless=new', `--remote-debugging-port=${PORT}`, '--no-first-run', '--disable-gpu-sandbox',
  '--window-size=1600,900', `--user-data-dir=${join(tmpdir(), 'sd-ui-test')}`, 'about:blank'], { stdio: 'ignore' });

let ws, id = 0;
const pending = new Map();
const errors = [];
function send(method, params = {}) {
  return new Promise((res, rej) => { const i = ++id; pending.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params })); });
}
async function evaluate(expr) {
  const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(`${expr.slice(0, 60)}: ${r.exceptionDetails.exception?.description || r.exceptionDetails.text}`);
  return r.result.value;
}
async function waitFor(expr, ms = 20000) {
  const t = Date.now();
  while (Date.now() - t < ms) { if (await evaluate(expr)) return true; await sleep(150); }
  throw new Error(`timed out waiting for ${expr}`);
}
async function shot(name, w, h) {
  await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: false });
  await sleep(1500);
  const { data } = await send('Page.captureScreenshot', { format: 'png' });
  writeFileSync(join(OUT, `${name}.png`), Buffer.from(data, 'base64'));
  console.log(`screenshot ${name}.png`);
}

try {
  let target;
  for (let i = 0; i < 40 && !target; i++) {
    await sleep(250);
    try { target = (await (await fetch(`http://127.0.0.1:${PORT}/json`)).json()).find(t => t.type === 'page'); } catch { /* starting */ }
  }
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise(r => ws.addEventListener('open', r));
  ws.addEventListener('message', ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.rej(new Error(m.error.message)) : p.res(m.result); }
    if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') errors.push(m.params.args.map(a => a.value ?? a.description).join(' '));
  });
  await send('Runtime.enable'); await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 900, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: URL });
  await waitFor(`document.querySelectorAll('#dataset-list .card').length > 5`);
  await waitFor(`window.__sd && window.__sd.map && window.__sd.map.isStyleLoaded() && document.querySelectorAll('#area-modes button').length > 3`);
  console.log('catalog cards:', await evaluate(`document.querySelectorAll('#dataset-list .card').length`));
  await shot('01-start', 1600, 900);

  // Dataset -> county mode -> pick Los Angeles County (FIPS 06037) -> get
  await evaluate(`window.__sd.selectDataset('nhdplus-v21', 'flowlines')`);
  await evaluate(`window.__sd.setMode('county')`);
  await waitFor(`document.querySelector('#area-summary').textContent.includes('Click to select')`);
  await shot('02-counties', 1600, 900);
  await evaluate(`window.__sd.pick('06037', 'Los Angeles County', 10589)`);
  await evaluate(`document.querySelector('#refresh').checked = true`);
  const t0 = Date.now();
  await evaluate(`document.querySelector('#btn-get').click()`);
  await waitFor(`!document.querySelector('#result').classList.contains('hidden') || !document.querySelector('#error').classList.contains('hidden')`, 120000);
  const err = await evaluate(`document.querySelector('#error').classList.contains('hidden') ? null : document.querySelector('#error').textContent`);
  if (err) throw new Error(`request failed: ${err}`);
  console.log(`request shown after ${((Date.now() - t0) / 1000).toFixed(2)} s; panel says ${await evaluate(`document.querySelector('#res-time').textContent`)} s`);
  await shot('03-result', 1600, 900);

  // Raster with a parameter: Annual NLCD 2019 for one HUC10 typed as a spec through the same path
  await evaluate(`window.__sd.selectDataset('nlcd-annual')`);
  await evaluate(`window.__sd.setMode('huc10')`);
  await waitFor(`document.querySelector('#area-summary').textContent.includes('Click to select')`, 60000);
  await evaluate(`window.__sd.pick('1807010501', 'Upper Los Angeles River', 395)`);
  await evaluate(`document.querySelector('#params [data-p=year]').value = 2019`);
  await evaluate(`document.querySelector('#btn-get').click()`);
  await waitFor(`!document.querySelector('#result').classList.contains('hidden') || !document.querySelector('#error').classList.contains('hidden')`, 120000);
  const err2 = await evaluate(`document.querySelector('#error').classList.contains('hidden') ? null : document.querySelector('#error').textContent`);
  if (err2) throw new Error(`raster request failed: ${err2}`);
  console.log('raster result stats:', await evaluate(`document.querySelector('#res-stats').innerText.replace(/\\n/g, ' ')`));
  await shot('04-raster', 1600, 900);

  // Ocean: statewide bathymetry for two Bight strata (raster preview with the sea-level ramp)
  await evaluate(`window.__sd.selectDataset('cdfw-marine', 'bathymetry-200m')`);
  await evaluate(`document.querySelector('[data-g=ocean]').click()`);
  await evaluate(`window.__sd.setMode('bight-strata')`);
  await waitFor(`document.querySelector('#area-summary').textContent.includes('Click to select')`, 60000);
  await evaluate(`window.__sd.pick('Outer Shelf', 'Outer Shelf', 1500); window.__sd.pick('Upper Slope', 'Upper Slope', 2500)`);
  await evaluate(`document.querySelector('#btn-get').click()`);
  await waitFor(`!document.querySelector('#result').classList.contains('hidden') || !document.querySelector('#error').classList.contains('hidden')`, 120000);
  const err3 = await evaluate(`document.querySelector('#error').classList.contains('hidden') ? null : document.querySelector('#error').textContent`);
  if (err3) throw new Error(`bathymetry request failed: ${err3}`);
  await waitFor(`!!window.__sd.map.getLayer('result-img')`, 20000);
  console.log('bathymetry:', (await evaluate(`document.querySelector('#res-src').innerText + ' | ' + document.querySelector('#res-stats').innerText`)).split('\n').join(' '));
  await shot('09-ocean-bathymetry', 1600, 900);

  // Ocean: MPAs in the Central Coast marine region
  await evaluate(`window.__sd.selectDataset('cdfw-marine', 'mpas')`);
  await evaluate(`window.__sd.setMode('marine-region')`);
  await waitFor(`document.querySelector('#area-summary').textContent.includes('Click to select')`, 60000);
  await evaluate(`window.__sd.pick('CCSR', 'Central Coast', 2970)`);
  await evaluate(`document.querySelector('#btn-get').click()`);
  await waitFor(`!document.querySelector('#result').classList.contains('hidden') || !document.querySelector('#error').classList.contains('hidden')`, 120000);
  console.log('MPAs:', (await evaluate(`document.querySelector('#res-src').innerText + ' | ' + document.querySelector('#res-stats').innerText`)).split('\n').join(' '));
  await shot('10-ocean-mpas', 1600, 900);

  // Ocean: depth band inside the Bight
  await evaluate(`window.__sd.setMode('depth')`);
  await evaluate(`document.querySelector('#d-lo').value = 30; document.querySelector('#d-hi').value = 120; document.querySelector('#d-in').value = 'bight'; document.querySelector('#d-go').click()`);
  await waitFor(`document.querySelector('#area-summary').textContent.includes('km')`, 60000);
  console.log('depth band:', await evaluate(`document.querySelector('#area-summary').innerText`));
  await shot('11-depth-band', 1600, 900);

  // Narrow layout (ArcGIS Pro dock pane width)
  await shot('05-narrow-data', 420, 820);
  await evaluate(`document.querySelector('[data-tab=map]').click()`);
  await shot('06-narrow-map', 420, 820);
  await evaluate(`document.querySelector('[data-tab=request]').click()`);
  await shot('07-narrow-get', 420, 820);
  await evaluate(`document.querySelector('#btn-theme').click()`);
  await evaluate(`document.querySelector('[data-tab=map]').click()`);
  await shot('08-light', 1600, 900);
} catch (e) {
  errors.push(String(e.message || e));
} finally {
  edge.kill();
}
if (errors.length) { console.log('ERRORS:\n' + errors.join('\n')); process.exit(1); }
console.log('UI test passed');
process.exit(0);
