"""Local engine service for the data view (web app and ArcGIS Pro panel).

Keeps the engine loaded in one process so requests do not pay Python's start-up and import time, runs requests in
background threads and reports their progress. Listens on 127.0.0.1 only.

    python -m sccwrp_data serve [--port 8765]      then open http://127.0.0.1:8765/

API (JSON):
  GET  /api/catalog                    datasets the tools can get, with layers, parameters and tier (instant / on demand)
  GET  /api/areas                      area types
  GET  /api/areas/<type>/choices       names for a pick-many area type
  GET  /api/area?spec=county:Orange    the area as GeoJSON (WGS84, simplified) for the map
  GET  /api/boundaries?kind=county     every unit of a pick-many area type as GeoJSON, for clicking on the map
                                       (county, county-coastal, regional-board, smc-watershed, huc2 ... huc12)
  POST /api/get                        {"dataset", "layer", "area", "params", "buffer_km", "method", "crs",
                                        "resolution", "fmt", "out_dir", "refresh"} -> {"job": id}
  GET  /api/jobs/<id>                  status, log, files, manifest, preview (GeoJSON for vector results)
  GET  /api/health
"""
import gzip
import json
import mimetypes
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import geopandas as gpd
import shapely

from . import __version__, config
from . import clipareas
from .clipareas import areas, choices, resolve
from . import entries, vectorio
from .entries import CatalogError, catalog
from .request import get
from .stage import staged_list

UI_DIR = Path(__file__).resolve().parent / 'ui'
PREVIEW_MAX_FEATURES = 5000
JOBS = {}
_BOUNDARIES = {}
SIMPLIFY_M = {'county': 150, 'county-coastal': 150, 'regional-board': 250, 'smc-watershed': 100,
              'huc2': 500, 'huc4': 400, 'huc6': 300, 'huc8': 200, 'huc10': 150, 'huc12': 100,
              'marine-region': 150, 'bight-strata': 80, 'mpa': 20, 'asbs': 10}
PRECISION_DEG = 1e-5          # about 1 m: plenty for drawing on a map, and a third of the bytes


def _web_geojson(gdf):
    """WGS84 GeoJSON with coordinates rounded for the browser."""
    g = gdf.to_crs('EPSG:4326')
    g['geometry'] = shapely.set_precision(g.geometry.values, PRECISION_DEG)
    return json.loads(g[~g.geometry.is_empty].to_json(drop_id=True))


def _boundaries(kind):
    """All units of an area type (code, name, geometry) as WGS84 GeoJSON; built once per service run."""
    if kind in _BOUNDARIES:
        return _BOUNDARIES[kind]
    disk = config.fast_dir() / 'boundaries' / f'{kind}.geojson'
    if disk.exists():
        _BOUNDARIES[kind] = json.loads(disk.read_text(encoding='utf-8'))
        return _BOUNDARIES[kind]
    if kind in ('county', 'county-coastal'):
        g = clipareas.counties(kind == 'county-coastal')
    elif kind == 'regional-board':
        g = clipareas._regional_boards([str(i) for i in range(1, 10)])
    elif kind == 'smc-watershed':
        g = clipareas._smc()
    elif kind == 'marine-region':
        g = clipareas.marine_regions()
    elif kind.startswith('bight-strata'):
        g = clipareas.bight_strata(kind[13:] or None)
    elif kind == 'mpa':
        g = clipareas.mpas()
    elif kind == 'asbs':
        g = clipareas.asbs()
    elif kind.startswith('huc') and kind[3:].isdigit():
        lv = kind[3:]
        path = clipareas.staged_huc_layer(lv)
        if path is None:
            raise CatalogError(f'HUC{lv} boundaries are not staged; type HUC codes instead')
        g = gpd.read_file(path, layer=f'wbd__huc{lv}', columns=[f'HUC{lv}', 'NAME']).rename(
            columns={f'HUC{lv}': 'code', 'NAME': 'name'}).to_crs(clipareas.WORK_CRS)
    else:
        raise CatalogError(f'No clickable boundaries for {kind}')
    g = g[['code', 'name', 'geometry']].copy() if 'code' in g else g.assign(code=g['name'])[['code', 'name', 'geometry']]
    g['code'] = g['code'].astype(str)
    g['km2'] = (g.area / 1e6).round(1)
    g['geometry'] = g.geometry.simplify(SIMPLIFY_M.get(kind.split('-2')[0], 150))
    out = _web_geojson(g)
    disk.parent.mkdir(parents=True, exist_ok=True)
    disk.write_text(json.dumps(out), encoding='utf-8')
    _BOUNDARIES[kind] = out
    return out


def _area_arg(area):
    """Area from the UI: a string spec, or a GeoJSON geometry drawn on the map (WGS84)."""
    if isinstance(area, dict):
        from shapely.geometry import shape
        return shape(area.get('geometry', area))
    return area


def _catalog_payload():
    source = entries.reload()   # the page is opening: pick up anything pushed since the service started
    staged = {(m['dataset'], m['layer']): m for m in staged_list()}
    out = []
    for e in catalog(fetchable_only=True):
        lays = []
        for name, spec in e['fetch']['layers'].items():
            s = staged.get((e['id'], name))
            lays.append({'id': name, 'title': spec.get('title', name), 'handler': spec['handler'],
                         'kind': entries.layer_kind(spec),
                         'params': spec.get('params', {}),
                         'tier': 'instant' if s else ('internal' if spec['handler'] == 'local' else 'on-demand'),
                         'staged': {k: s[k] for k in ('built', 'features', 'source') if k in s} if s else None,
                         'live_url': spec.get('url') if spec['handler'] in ('arcgis-features', 'arcgis-image') else None})
        out.append({'id': e['id'], 'title': e['title'], 'category': e['category'], 'provider': e['provider'],
                    'recommendation': e['recommendation'], 'access': e['access'], 'versions': e['versions'],
                    'notes': e.get('notes', ''), 'default_layer': e['fetch'].get('default') or lays[0]['id'],
                    'landing': next((l['url'] for l in e['links'] if l['role'] == 'landing'), None), 'layers': lays})
    return {'datasets': out, 'default_crs': config.DEFAULT_CRS, 'version': __version__,
            'catalog_source': source}


def _area_geojson(spec):
    a = resolve(spec)
    g = a.gdf.copy()
    g['geometry'] = g.geometry.simplify(max(20, a.km2 ** 0.5 * 2))
    g = g.to_crs('EPSG:4326')
    return {'label': a.label, 'km2': round(a.km2, 1), 'key': a.key,
            'geojson': json.loads(g[['name', 'geometry']].to_json())}


NLCD_COLORS = {11: '#466b9f', 12: '#d1def8', 21: '#dec5c5', 22: '#d99282', 23: '#eb0000', 24: '#ab0000',
               31: '#b3ac9f', 41: '#68ab5f', 42: '#1c5f2c', 43: '#b5c58f', 51: '#af963c', 52: '#ccb879',
               71: '#dfdfc2', 72: '#d1d182', 73: '#a3cc51', 74: '#82ba9e', 81: '#dcd939', 82: '#ab6c28',
               90: '#b8d9eb', 95: '#6c9fb8'}
NLCD_NAMES = {11: 'Open water', 12: 'Ice / snow', 21: 'Developed, open', 22: 'Developed, low', 23: 'Developed, medium',
              24: 'Developed, high', 31: 'Barren', 41: 'Deciduous forest', 42: 'Evergreen forest', 43: 'Mixed forest',
              52: 'Shrub / scrub', 71: 'Grassland', 81: 'Pasture / hay', 82: 'Cultivated crops',
              90: 'Woody wetlands', 95: 'Herbaceous wetlands'}
# Below sea level: deep navy to pale cyan at 0 m. Above: coastal green to tan to white peaks.
SEA = [(0.0, (8, 29, 88)), (0.55, (34, 94, 168)), (0.85, (65, 182, 196)), (1.0, (199, 233, 240))]
LAND = [(0.0, (26, 150, 65)), (0.35, (166, 217, 106)), (0.65, (230, 200, 140)), (0.9, (190, 150, 110)), (1.0, (250, 250, 250))]
SEQ = [(0.0, (68, 1, 84)), (0.25, (59, 82, 139)), (0.5, (33, 145, 140)), (0.75, (94, 201, 98)), (1.0, (253, 231, 37))]
PREVIEW_PX = 1400


def _ramp(stops, t):
    import numpy as np
    t = np.clip(t, 0, 1)
    xs = [s[0] for s in stops]
    return np.stack([np.interp(t, xs, [s[1][i] for s in stops]) for i in range(3)], axis=-1)


def _raster_preview(path, dataset_id):
    """A coloured PNG of a raster result in Web Mercator, with its corner coordinates, for the map."""
    import base64
    import numpy as np
    from osgeo import gdal
    src = gdal.Open(str(path))
    categorical = dataset_id.startswith('nlcd')
    w, h = src.RasterXSize, src.RasterYSize
    scale = min(1.0, PREVIEW_PX / max(w, h))
    ds = gdal.Warp('', src, format='MEM', dstSRS='EPSG:3857', width=max(2, int(w * scale)), height=max(2, int(h * scale)),
                   resampleAlg='nearest' if categorical else 'average')
    b = ds.GetRasterBand(1)
    a = b.ReadAsArray().astype('float64')
    nd = b.GetNoDataValue()
    valid = np.isfinite(a) & ((a != nd) if nd is not None else True)
    rgb = np.zeros(a.shape + (3,))
    legend = None
    if categorical:
        for code, hexc in NLCD_COLORS.items():
            m = valid & (a == code)
            rgb[m] = [int(hexc[i:i + 2], 16) for i in (1, 3, 5)]
        present = sorted(int(v) for v in np.unique(a[valid]) if int(v) in NLCD_NAMES)
        legend = {'type': 'classes', 'items': [[NLCD_COLORS[c], NLCD_NAMES[c]] for c in present]}
    else:
        v = a[valid]
        if v.size == 0:
            return None
        lo, hi = np.percentile(v, [1, 99])
        if lo < 0 < hi or hi <= 0:          # bathymetry or a land-sea DEM: hard break at sea level
            deep = min(lo, -1.0)
            top = max(hi, 1.0)
            below = valid & (a < 0)
            above = valid & (a >= 0)
            rgb[below] = _ramp(SEA, 1 - a[below] / deep)
            rgb[above] = _ramp(LAND, a[above] / top)
            legend = {'type': 'sealevel', 'min': round(float(lo), 1), 'max': round(float(hi), 1)}
        else:
            rgb[valid] = _ramp(SEQ, (a[valid] - lo) / max(hi - lo, 1e-9))
            legend = {'type': 'ramp', 'min': round(float(lo), 2), 'max': round(float(hi), 2)}
    rgba = np.dstack([rgb, np.where(valid, 235, 0)]).astype('uint8')
    mem = gdal.GetDriverByName('MEM').Create('', rgba.shape[1], rgba.shape[0], 4, gdal.GDT_Byte)
    for i in range(4):
        mem.GetRasterBand(i + 1).WriteArray(rgba[:, :, i])
    gdal.GetDriverByName('PNG').CreateCopy('/vsimem/preview.png', mem)
    f = gdal.VSIFOpenL('/vsimem/preview.png', 'rb')
    gdal.VSIFSeekL(f, 0, 2)
    size = gdal.VSIFTellL(f)
    gdal.VSIFSeekL(f, 0, 0)
    png = gdal.VSIFReadL(1, size, f)
    gdal.VSIFCloseL(f)
    gdal.Unlink('/vsimem/preview.png')
    gt = ds.GetGeoTransform()
    x0, y0 = gt[0], gt[3]
    x1, y1 = x0 + gt[1] * ds.RasterXSize, y0 + gt[5] * ds.RasterYSize
    from pyproj import Transformer
    tr = Transformer.from_crs(3857, 4326, always_xy=True)
    corners = [list(tr.transform(x, y)) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    return {'type': 'raster', 'image': 'data:image/png;base64,' + base64.b64encode(png).decode(),
            'coordinates': corners, 'legend': legend}


def _preview(result):
    """Map preview of a result: GeoJSON for vectors (simplified, capped), a coloured image for rasters."""
    for f in result.files:
        if f.suffix == '.tif':
            return _raster_preview(f, result.manifest['dataset'])
    for f in result.files:
        if f.suffix == '.gpkg':
            g = vectorio.read(f)
            n = len(g)
            if n > PREVIEW_MAX_FEATURES:
                g = g.sample(PREVIEW_MAX_FEATURES, random_state=0)
            g['geometry'] = g.geometry.simplify(5)
            return {'type': 'vector', 'features_total': n, 'geojson': _web_geojson(g[['geometry']])}
    return None


def _run(job_id, req):
    job = JOBS[job_id]

    def log(msg):
        job['log'].append(msg.strip())

    try:
        r = get(req['dataset'], _area_arg(req['area']), req.get('layer'), params=req.get('params') or None,
                buffer_km=float(req.get('buffer_km') or 0), method=req.get('method') or 'clip',
                mask=req.get('mask') or 'both',
                crs=req.get('crs') or config.DEFAULT_CRS, resolution=req.get('resolution') or None,
                fmt=req.get('fmt') or None, out_dir=req.get('out_dir') or str(config.output_dir() / req['dataset']),
                refresh=bool(req.get('refresh')),
                log=log)
        seconds = round(time.time() - job['started'], 2)       # the request itself, before the preview
        try:
            preview = _preview(r)
        except Exception as e:           # a preview problem must not fail the request
            preview = None
            job['log'].append(f'(no preview: {e})')
        job.update(files=[str(f) for f in r.files], manifest=r.manifest, cached=r.cached, notes=r.notes,
                   preview=preview, seconds=seconds)
        job['status'] = 'done'           # last, so a client never sees 'done' without the details
    except CatalogError as e:
        job.update(error=str(e), seconds=round(time.time() - job['started'], 2))
        job['status'] = 'error'
    except Exception as e:
        job.update(error=f'{type(e).__name__}: {e}', trace=traceback.format_exc(),
                   seconds=round(time.time() - job['started'], 2))
        job['status'] = 'error'


class Handler(BaseHTTPRequestHandler):
    server_version = f'sccwrp-data/{__version__}'

    def log_message(self, fmt, *args):     # quiet console
        pass

    def _send(self, code, body, ctype='application/json'):
        data = json.dumps(body, default=str, separators=(',', ':')).encode() if ctype == 'application/json' else body
        zipped = len(data) > 20_000 and 'gzip' in self.headers.get('Accept-Encoding', '') and (
            ctype.startswith(('application/json', 'text/', 'application/javascript')))
        if zipped:
            data = gzip.compress(data, compresslevel=5)
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        if zipped:
            self.send_header('Content-Encoding', 'gzip')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == '/api/health':
                return self._send(200, {'ok': True, 'version': __version__})
            if u.path == '/api/catalog':
                return self._send(200, _catalog_payload())
            if u.path == '/api/areas':
                return self._send(200, [{'id': i, 'label': l, 'selection': s} for i, l, s in areas()])
            if u.path.startswith('/api/areas/') and u.path.endswith('/choices'):
                return self._send(200, choices(u.path.split('/')[3]))
            if u.path == '/api/area':
                return self._send(200, _area_geojson(q['spec']))
            if u.path == '/api/boundaries':
                return self._send(200, _boundaries(q['kind']))
            if u.path.startswith('/api/jobs/'):
                job = JOBS.get(u.path.rsplit('/', 1)[-1])
                return self._send(200 if job else 404, job or {'error': 'no such job'})
            return self._static(u.path)
        except CatalogError as e:
            return self._send(400, {'error': str(e)})
        except Exception as e:
            return self._send(500, {'error': f'{type(e).__name__}: {e}'})

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != '/api/get':
            return self._send(404, {'error': 'not found'})
        req = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
        job_id = uuid.uuid4().hex[:10]
        JOBS[job_id] = {'id': job_id, 'status': 'running', 'log': [], 'request': req, 'started': time.time()}
        threading.Thread(target=_run, args=(job_id, req), daemon=True).start()
        return self._send(202, {'job': job_id})

    def _static(self, path):
        rel = 'index.html' if path in ('/', '') else path.lstrip('/')
        f = (UI_DIR / rel).resolve()
        if UI_DIR not in f.parents and f != UI_DIR / 'index.html' or not f.is_file():
            return self._send(404, {'error': 'not found'})
        return self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or 'application/octet-stream')


def _warm():
    """Build the clickable boundary layers in the background so the first click on the map is instant."""
    for kind in ('county', 'regional-board', 'huc8', 'huc10', 'huc12', 'county-coastal', 'smc-watershed',
                 'marine-region', 'bight-strata', 'mpa', 'asbs'):
        try:
            _boundaries(kind)
        except Exception:
            pass


def serve(port=8765):
    threading.Thread(target=_warm, daemon=True).start()
    httpd = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print(f'SCCWRP data service on http://127.0.0.1:{port}/  (Ctrl+C to stop)', flush=True)
    httpd.serve_forever()
