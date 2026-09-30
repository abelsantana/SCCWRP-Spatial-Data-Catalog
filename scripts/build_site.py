"""Build docs/index.html (GitHub Pages) from catalog/datasets/*.json and catalog/link_status.json.

Standard library only. The page is self-contained: catalog data is embedded, no external scripts.
"""
import html
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATASETS = REPO / 'catalog' / 'datasets'
STATUS = REPO / 'catalog' / 'link_status.json'
OUT = REPO / 'docs' / 'index.html'


def load():
    datasets = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(DATASETS.glob('*.json'))]
    status = json.loads(STATUS.read_text(encoding='utf-8')) if STATUS.exists() else {'checked': None, 'links': {}}
    return datasets, status


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SCCWRP Data Catalog</title>
<style>
:root {
  --bg: #f7f7f5; --panel: #ffffff; --text: #1d1f21; --muted: #5f6368; --line: #e3e3de;
  --accent: #1f6f8b; --ok: #2e7d32; --warn: #b26a00; --bad: #c62828; --chip: #eef3f5;
  --link-out: #1f6f8b; --current: #6a4c93; --keep: #8a5a00; --restricted: #8b2f2f;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #15171a; --panel: #1d2024; --text: #e8eaed; --muted: #a0a4a8; --line: #2d3136;
    --accent: #6cb6d0; --ok: #66bb6a; --warn: #ffb74d; --bad: #ef5350; --chip: #23303a;
    --link-out: #6cb6d0; --current: #b39ddb; --keep: #e0b25c; --restricted: #e57373;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
header { padding: 28px 16px 12px; max-width: 1100px; margin: 0 auto; }
h1 { margin: 0 0 4px; font-size: 26px; }
.sub { color: var(--muted); margin: 0; }
.controls { position: sticky; top: 0; z-index: 2; background: var(--bg); border-bottom: 1px solid var(--line); }
.controls .inner { max-width: 1100px; margin: 0 auto; padding: 10px 16px; display: grid; gap: 8px; }
input[type=search] { width: 100%; padding: 10px 12px; font-size: 15px; border: 1px solid var(--line);
  border-radius: 8px; background: var(--panel); color: var(--text); }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { border: 1px solid var(--line); background: var(--panel); color: var(--text); border-radius: 999px;
  padding: 3px 10px; font-size: 13px; cursor: pointer; }
.chip[aria-pressed=true] { background: var(--accent); border-color: var(--accent); color: #fff; }
main { max-width: 1100px; margin: 0 auto; padding: 12px 16px 48px; }
.count { color: var(--muted); font-size: 13px; margin: 4px 0 12px; }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; margin-bottom: 12px; }
.card h2 { font-size: 17px; margin: 0 0 2px; }
.meta { color: var(--muted); font-size: 13px; display: flex; flex-wrap: wrap; gap: 4px 14px; }
.badge { display: inline-block; font-size: 12px; font-weight: 600; padding: 1px 8px; border-radius: 999px; color: #fff; }
.rec-link-out { background: var(--link-out); } .rec-link-out-current { background: var(--current); }
.rec-keep-local { background: var(--keep); } .rec-keep-local-restricted { background: var(--restricted); }
.badge.acc { background: var(--chip); color: var(--text); border: 1px solid var(--line); font-weight: 500; }
dl.versions { display: grid; grid-template-columns: max-content 1fr; gap: 2px 12px; margin: 10px 0; font-size: 13px; }
dl.versions dt { color: var(--muted); } dl.versions dd { margin: 0; }
ul.links { list-style: none; padding: 0; margin: 8px 0; }
ul.links li { display: flex; align-items: flex-start; gap: 8px; padding: 4px 0; border-top: 1px dashed var(--line); font-size: 14px; }
ul.links li:first-child { border-top: 0; }
.dot { width: 9px; height: 9px; border-radius: 50%; margin-top: 7px; flex: none; background: var(--line); }
.dot.ok { background: var(--ok); } .dot.blocked { background: var(--warn); } .dot.broken, .dot.error, .dot.timeout { background: var(--bad); }
.ltype { font-size: 11px; color: var(--muted); background: var(--chip); border-radius: 4px; padding: 0 6px; white-space: nowrap; margin-top: 2px; }
.lurl { overflow-wrap: anywhere; flex: 1; }
a { color: var(--accent); }
button.copy { border: 1px solid var(--line); background: transparent; color: var(--muted); border-radius: 6px;
  font-size: 12px; padding: 1px 8px; cursor: pointer; flex: none; }
details { margin-top: 8px; } summary { cursor: pointer; color: var(--accent); font-size: 14px; }
pre { background: var(--chip); padding: 8px 10px; border-radius: 6px; overflow-x: auto; font-size: 12.5px; margin: 6px 0; }
.snip h3 { font-size: 13px; margin: 10px 0 0; color: var(--muted); font-weight: 600; }
.notes { font-size: 14px; margin: 8px 0 0; }
.legend { font-size: 12px; color: var(--muted); display: flex; flex-wrap: wrap; gap: 4px 14px; }
.legend span { display: inline-flex; align-items: center; gap: 5px; }
.legend .dot { margin-top: 0; }
footer { max-width: 1100px; margin: 0 auto; padding: 0 16px 32px; color: var(--muted); font-size: 12px; }
</style>
</head>
<body>
<header>
  <h1>SCCWRP Data Catalog</h1>
  <p class="sub">Where to get the public datasets SCCWRP uses, straight from the provider. __N__ datasets. Links last checked: __CHECKED__.</p>
</header>
<div class="controls"><div class="inner">
  <input type="search" id="q" placeholder="Search datasets, providers, notes…" aria-label="Search">
  <div class="chips" id="cats" aria-label="Category"></div>
  <div class="chips" id="recs" aria-label="Recommendation"></div>
  <div class="chips" id="accs" aria-label="Access"></div>
  <div class="legend">
    <span><i class="dot ok"></i>link OK</span><span><i class="dot blocked"></i>site blocks automated checks (open in browser)</span>
    <span><i class="dot broken"></i>broken or unreachable</span><span><i class="dot"></i>not checked</span>
  </div>
</div></div>
<main>
  <div class="count" id="count"></div>
  <div id="list"></div>
</main>
<footer>Generated from the catalog JSON in this repository. To add or fix a dataset, edit its file in <code>catalog/datasets/</code>.</footer>
<script id="data" type="application/json">__DATA__</script>
<script>
const {datasets, status} = JSON.parse(document.getElementById('data').textContent);
const REC = {'link-out': 'Get it from the provider', 'link-out-current': 'Use the current provider version',
             'keep-local': 'SCCWRP copy (not public)', 'keep-local-restricted': 'SCCWRP copy (licensed / SCCWRP-made)'};
const ACCESS = {'stream': 'Stream it (service / API)', 'read-in-place': 'Read in place (cloud files)',
                'download-on-demand': 'Download what you need', 'keep-local': 'Local copy needed'};
const state = {q: '', cat: null, rec: null, acc: null};
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

function s3Path(url) {
  let m = url.match(/^https:\/\/([^./]+)\.s3[.-][^/]*amazonaws\.com\/(.*)$/) || url.match(/^https:\/\/s3[.-][^/]*amazonaws\.com\/([^/]+)\/(.*)$/);
  if (!m) return null;
  return `s3://${m[1]}/${m[2].replace(/index\.html$/, '')}`;
}
function featureQuery(url) {
  const base = /\/FeatureServer\/\d+\/?$/i.test(url) ? url.replace(/\/$/, '') : url.replace(/\/$/, '') + '/0';
  return base + '/query?where=1%3D1&outFields=*&f=geojson';
}
function snippets(d) {
  const out = [];
  const byType = t => d.links.find(l => l.type === t);
  const fs = byType('arcgis-featureserver'), ms = byType('arcgis-mapserver') || byType('arcgis-imageserver');
  const wms = byType('wms'), dl = d.links.find(l => l.role === 'download' && l.type === 'download');
  const bucket = d.links.map(l => s3Path(l.url)).find(Boolean) || (byType('s3') || {}).url;
  const ept = byType('entwine-pointcloud');
  if (fs || ms) out.push(['ArcGIS Pro', `Map tab > Add Data > Data From Path, paste:\n${(fs || ms).url}`]);
  if (wms) out.push(['ArcGIS Pro (WMS)', `Insert tab > Connections > Server > New WMS Server, paste:\n${wms.url}`]);
  if (fs) {
    out.push(['Python', `import geopandas as gpd\ngdf = gpd.read_file("${featureQuery(fs.url)}")`]);
    out.push(['R', `library(sf)\nx <- st_read("${featureQuery(fs.url)}")`]);
  }
  if (dl) {
    out.push(['Python (download)', `import urllib.request\nurllib.request.urlretrieve("${dl.url}", "${dl.url.split('/').pop().split('?')[0] || 'download'}")`]);
    out.push(['R (download)', `download.file("${dl.url}", destfile = "${dl.url.split('/').pop().split('?')[0] || 'download'}", mode = "wb")`]);
  }
  if (bucket) out.push(['AWS CLI (public bucket, no account)', `aws s3 ls --no-sign-request ${bucket}\naws s3 sync --no-sign-request ${bucket} ./local_folder`]);
  if (ept) out.push(['PDAL (point cloud, clip to your area)', `import pdal\npipe = pdal.Reader.ept("${ept.url}", bounds="([xmin, xmax], [ymin, ymax])") | pdal.Writer.las("clip.laz")\npipe.execute()`]);
  for (const p of d.packages) out.push([`${p.language} package`, p.language === 'R' ? `install.packages("${p.name}")` : `pip install ${p.name}`]);
  return out;
}
function linkRow(l) {
  const st = (status.links || {})[l.url];
  const cls = st ? st.status : '';
  const tip = st ? `${st.status}${st.code ? ' (HTTP ' + st.code + ')' : ''}, checked ${st.checked}` : 'not checked yet';
  const href = l.url.startsWith('s3://') ? null : l.url;
  return `<li><i class="dot ${esc(cls)}" title="${esc(tip)}"></i><span class="ltype">${esc(l.role === 'landing' ? 'info page' : l.type)}</span>
    <span class="lurl">${href ? `<a href="${esc(href)}" target="_blank" rel="noopener">${esc(l.url)}</a>` : esc(l.url)}${l.label ? ` <span class="meta">(${esc(l.label)})</span>` : ''}</span>
    <button class="copy" data-copy="${esc(l.url)}">Copy</button></li>`;
}
function card(d) {
  const sn = snippets(d);
  return `<article class="card" id="${esc(d.id)}">
    <h2>${esc(d.title)}</h2>
    <div class="meta"><span class="badge rec-${esc(d.recommendation)}">${esc(REC[d.recommendation] || d.recommendation)}</span>
      <span class="badge acc">${esc(ACCESS[d.access.mode] || d.access.mode)}</span>
      <span>${esc(d.provider)}</span><span>${esc(d.category)}</span><span>id: ${esc(d.id)}</span></div>
    <p class="notes"><strong>How to access:</strong> ${esc(d.access.how)}</p>
    <dl class="versions">
      <dt>Latest</dt><dd>${esc(d.versions.latest)}</dd>
      <dt>Older versions online</dt><dd>${esc(d.versions.older_versions_online)}</dd>
      <dt>SCCWRP's version online</dt><dd>${esc(d.versions.sccwrp_version_online)}</dd>
    </dl>
    <ul class="links">${d.links.map(linkRow).join('')}</ul>
    ${d.other_access.length ? `<p class="notes"><strong>Also:</strong> ${d.other_access.map(esc).join('; ')}</p>` : ''}
    ${d.notes ? `<p class="notes">${esc(d.notes)}</p>` : ''}
    ${sn.length ? `<details><summary>How to use it in ArcGIS Pro, Python, R</summary><div class="snip">${
      sn.map(([h, code]) => `<h3>${esc(h)}</h3><pre>${esc(code)}</pre>`).join('')}</div></details>` : ''}
  </article>`;
}
function chips(el, values, key, labels) {
  el.innerHTML = values.map(v => `<button class="chip" aria-pressed="false" data-v="${esc(v)}">${esc(labels ? labels[v] : v)}</button>`).join('');
  el.addEventListener('click', e => {
    const b = e.target.closest('.chip'); if (!b) return;
    state[key] = state[key] === b.dataset.v ? null : b.dataset.v;
    el.querySelectorAll('.chip').forEach(c => c.setAttribute('aria-pressed', c.dataset.v === state[key]));
    render();
  });
}
function render() {
  const q = state.q.toLowerCase();
  const hits = datasets.filter(d => (!state.cat || d.category === state.cat) && (!state.rec || d.recommendation === state.rec)
    && (!state.acc || d.access.mode === state.acc)
    && (!q || JSON.stringify(d).toLowerCase().includes(q)));
  document.getElementById('count').textContent = `${hits.length} of ${datasets.length} datasets`;
  document.getElementById('list').innerHTML = hits.map(card).join('');
}
chips(document.getElementById('cats'), [...new Set(datasets.map(d => d.category))].sort(), 'cat');
chips(document.getElementById('recs'), Object.keys(REC).filter(r => datasets.some(d => d.recommendation === r)), 'rec', REC);
chips(document.getElementById('accs'), Object.keys(ACCESS).filter(a => datasets.some(d => d.access.mode === a)), 'acc', ACCESS);
document.getElementById('q').addEventListener('input', e => { state.q = e.target.value; render(); });
document.addEventListener('click', e => {
  const b = e.target.closest('button.copy'); if (!b) return;
  navigator.clipboard.writeText(b.dataset.copy).then(() => { b.textContent = 'Copied'; setTimeout(() => b.textContent = 'Copy', 1200); });
});
render();
</script>
</body>
</html>
"""


def main():
    datasets, status = load()
    datasets.sort(key=lambda d: (d['category'], d['title'].lower()))
    data = json.dumps({'datasets': datasets, 'status': status}, ensure_ascii=False).replace('</', '<\\/')
    page = (PAGE.replace('__DATA__', data)
            .replace('__N__', str(len(datasets)))
            .replace('__CHECKED__', html.escape(status.get('checked') or 'not yet')))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page, encoding='utf-8')
    print(f'Wrote {OUT} ({len(datasets)} datasets)')


if __name__ == '__main__':
    main()
