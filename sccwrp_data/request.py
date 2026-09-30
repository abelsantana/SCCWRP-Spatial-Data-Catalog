"""get(): the one entry point every front end calls.

    get('nlcd-annual', 'county:Orange', params={'year': 2019})
    get('nhdplus-hr', 'huc8:18070105', out_dir='C:/work')

Results are cached by (dataset, layer, fetch spec, area, parameters, options). A repeat request is answered from
the cache without contacting the provider, until the result is older than config.max_age_days() or refresh=True.
"""
import datetime as dt
import hashlib
import json
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__, clip, config, stage
from .clipareas import resolve as resolve_area
from .entries import CatalogError, info, layer_spec
from .handlers import HANDLERS, Context, resolve_params


@dataclass
class Result:
    files: list                 # output files (in out_dir if given, else in the cache)
    manifest: dict
    cached: bool
    folder: Path
    notes: list = field(default_factory=list)

    def __repr__(self):
        return (f'<Result {self.manifest["dataset"]}/{self.manifest["layer"]} {self.manifest["area"]["label"]}: '
                f'{len(self.files)} file(s){" (cached)" if self.cached else ""}>')


def _slug(s, n=48):
    s = re.sub(r'[^A-Za-z0-9]+', '-', s).strip('-').lower()
    return s[:n].rstrip('-')


def _jsonable(v):
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    return v


def _param_tag(params):
    """Short file-name tag for the parameters: 2019, 20130101-20130107, ppt."""
    parts = []
    for k, v in params.items():
        if isinstance(v, list) and v and isinstance(v[0], dt.date):
            parts.append(v[0].strftime('%Y%m%d') + (f'-{v[-1].strftime("%Y%m%d")}' if len(v) > 1 else ''))
        elif isinstance(v, list):
            parts.append('-'.join(map(str, v))[:30])
        else:
            parts.append(str(v))
    return _slug('_'.join(parts), 40)


def get(dataset_id, area, layer=None, *, params=None, buffer_km=0, method='clip', mask='both', crs=config.DEFAULT_CRS,
        resolution=None, fmt=None, out_dir=None, refresh=False, log=print):
    """Get one layer of a catalog dataset for an area.

    dataset_id  catalog id, e.g. 'nlcd-annual' (see catalog())
    area        area string ('county:Orange', 'huc8:18070105', 'ca-watersheds', a file path) or a geometry
    layer       layer of the dataset (default: its first / default layer; see catalog.layers(id))
    params      layer parameters, e.g. {'year': 2019} or {'date': '2013-01-01/2013-01-07'}
    buffer_km   distance added around the area
    method      'clip' (cut to the area), 'intersects' (keep whole features that touch it), 'bbox'
    mask        'both' (default), 'land' (drop the ocean part of the area) or 'water' (drop the land part)
    crs         output coordinate system (default NAD83 / UTM zone 11N, EPSG:26911)
    resolution  output cell size in crs units (rasters); point spacing for point-cloud thinning
    fmt         output format: vector gpkg | gdb | shp | geojson | parquet; raster tif (COG) | gtiff; laz
    out_dir     copy the result here (otherwise it stays in the cache and Result.files points there)
    refresh     ignore the cache and fetch again
    """
    if method not in ('clip', 'intersects', 'bbox'):
        raise CatalogError('method must be clip, intersects or bbox')
    entry = info(dataset_id)
    layer, spec = layer_spec(dataset_id, layer)
    handler = HANDLERS.get(spec['handler'])
    if handler is None:
        raise CatalogError(f'Unknown handler "{spec["handler"]}" for {dataset_id}/{layer}')
    params = resolve_params(spec, params)
    kind = spec.get('kind') or {'arcgis-features': 'vector', 'streamcat': 'vector', 'ept': 'pointcloud'}.get(
        spec['handler'], 'raster')
    fmt = fmt or clip.DEFAULT_FORMAT[kind]
    ext = clip.extension(kind, fmt)
    a = resolve_area(area, buffer_km, method, mask)
    hit = stage.lookup(dataset_id, layer, params, a) if kind in ('vector', 'raster') else None
    if hit:                                  # fast tier: read the staged California copy instead of the provider
        path, smeta = hit
        spec = dict(spec, handler='staged', staged_path=str(path), staged_layer=smeta.get('gpkg_layer'),
                    staged_built=smeta['built'],
                    staged_from=f'SCCWRP fast copy built {smeta["built"][:10]} from {smeta["source"]}',
                    version=smeta.get('version'), kind=kind)
        handler = HANDLERS['staged']

    request = {'dataset': dataset_id, 'layer': layer, 'spec': spec, 'area': a.key, 'area_shape': a.fingerprint(),
               'params': _jsonable(params), 'method': method, 'crs': crs, 'resolution': resolution, 'fmt': fmt,
               'engine': __version__.split('.')[0]}
    key = hashlib.sha1(json.dumps(request, sort_keys=True).encode()).hexdigest()[:12]
    stem = f'{dataset_id}_{layer}_{_slug(a.key)}' + (f'_{_param_tag(params)}' if params else '')
    folder = config.cache_dir() / 'results' / dataset_id / f'{_slug(a.key, 40)}_{key}'
    mfile = folder / 'manifest.json'

    cached = False
    if mfile.exists() and not refresh:
        manifest = json.loads(mfile.read_text(encoding='utf-8'))
        age = (dt.datetime.now() - dt.datetime.fromisoformat(manifest['created'])).days
        if age <= config.max_age_days():
            cached = True
            log(f'{dataset_id}/{layer} for {a.label}: from cache ({age} days old)')
    if not cached:
        manifest = _fetch(entry, layer, spec, handler, kind, a, method, crs, resolution, params, fmt, ext, stem,
                          folder, log)

    files = [folder / f['file'] for f in manifest['files']] + [folder / f for f in manifest.get('extra_files', [])]
    if out_dir:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        copied = []
        for f in files:
            dest = out_dir / f.name
            if f.is_dir():
                shutil.rmtree(dest, ignore_errors=True)
                shutil.copytree(f, dest)
            else:
                shutil.copy2(f, dest)
            copied.append(dest)
        shutil.copy2(mfile, out_dir / f'{stem}.manifest.json')
        files = copied
    return Result(files, manifest, cached, folder, manifest.get('notes', []))


def _fetch(entry, layer, spec, handler, kind, a, method, crs, resolution, params, fmt, ext, stem, folder, log):
    t0 = time.time()
    log(f'{entry["id"]}/{layer} for {a.label} ({a.km2:,.0f} km2) via {spec["handler"]}')
    tmp_root = config.cache_dir() / 'tmp'
    tmp_root.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f'{entry["id"]}_', dir=tmp_root))
    staging = Path(tempfile.mkdtemp(prefix='out_', dir=tmp_root))
    try:
        ctx = Context(entry['id'], layer, spec, a, crs, method, resolution, params, work, log)
        got = handler(ctx)
        use_method = got.method or method
        outputs = []
        for name, item in got.items:
            out_name = stem if len(got.items) == 1 else f'{stem}_{name.rsplit("_", 1)[-1]}'
            out = staging / f'{out_name}{ext}'
            if got.final:
                shutil.move(str(item), out)
                stats = {'bytes': out.stat().st_size}
            elif got.kind == 'vector':
                stats = clip.vector(item, a, use_method, crs, fmt, out, name)
            else:
                stats = clip.raster(item, a, use_method, crs, resolution, got.resampling, fmt, out, work)
            outputs.append(dict(file=out.name, kind=got.kind, **stats))
            if stats.get('auto_resolution'):
                got.notes.append(f'Returned at {stats["auto_resolution"]:g} m cells to keep this large area quick; '
                                 f'set a cell size for finer detail, down to the source resolution.')
            log(f'  wrote {out.name}: ' + ', '.join(f'{k}={v}' for k, v in stats.items()
                                                    if k in ('features', 'width', 'height', 'bytes')))
        extras = []
        for name, p in got.extra_files:
            dest = staging / f'{stem}{Path(name).suffix}'
            shutil.copy2(p, dest)
            extras.append(dest.name)
        manifest = {
            'dataset': entry['id'], 'title': entry['title'], 'provider': entry['provider'], 'layer': layer,
            'layer_title': spec.get('title', layer), 'version': spec.get('version') or entry['versions']['latest'],
            'area': {'key': a.key, 'label': a.label, 'km2': round(a.km2, 1), 'method': use_method},
            'params': _jsonable(params), 'crs': crs, 'resolution': resolution, 'format': fmt,
            'files': outputs, 'extra_files': extras, 'sources': got.sources, 'notes': got.notes,
            'landing_page': next((l['url'] for l in entry['links'] if l['role'] == 'landing'), None),
            'recommendation': entry['recommendation'], 'catalog_last_reviewed': entry['last_reviewed'],
            'created': dt.datetime.now().isoformat(timespec='seconds'), 'seconds': round(time.time() - t0, 1),
            'engine_version': __version__}
        (staging / 'manifest.json').write_text(json.dumps(manifest, indent=1), encoding='utf-8')
        if folder.exists():
            shutil.rmtree(folder)
        folder.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging), folder)
        log(f'  done in {manifest["seconds"]:.0f} s')
        return manifest
    finally:
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)
