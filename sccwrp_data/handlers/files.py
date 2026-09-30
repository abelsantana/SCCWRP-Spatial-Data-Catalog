"""Whole files: provider downloads (fetched once into the shared download cache) and SCCWRP's internal copies."""
import fnmatch
import hashlib
import shutil
import time
import zipfile
from pathlib import Path

import geopandas as gpd
from osgeo import gdal

from .. import config, holdings, http
from ..entries import CatalogError
from .base import Fetched, handler

_last_request = {}


def _cached_download(url, ctx, min_interval=0):
    """Download url once into <cache>/downloads and unpack zips there; later requests reuse it."""
    name = url.rstrip('/').rsplit('/', 1)[-1].split('?')[0] or 'download'
    folder = config.cache_dir() / 'downloads' / f'{hashlib.sha1(url.encode()).hexdigest()[:10]}_{name}'
    done = folder / '.complete'
    if not done.exists():
        wait = min_interval - (time.time() - _last_request.get('t', 0))
        if wait > 0:
            time.sleep(wait)
        folder.mkdir(parents=True, exist_ok=True)
        dest, secs = http.download(url, folder / (name if '.' in name else name + '.zip'))
        _last_request['t'] = time.time()
        ctx.log(f'  downloaded {dest.stat().st_size / 2**20:,.1f} MB in {secs:.0f} s')
        if zipfile.is_zipfile(dest):
            with zipfile.ZipFile(dest) as z:
                z.extractall(folder / 'unzipped')
        elif dest.stat().st_size < 20_000 and _looks_like_text(dest):
            # Providers answer quota and error conditions with a short page and HTTP 200; never cache that
            message = dest.read_text(encoding='utf-8', errors='replace').strip()[:500]
            shutil.rmtree(folder, ignore_errors=True)
            raise CatalogError(f'The provider sent a message instead of data: {message}')
        done.write_text(url)
    else:
        ctx.log('  using the cached download')
    return folder / 'unzipped' if (folder / 'unzipped').exists() else folder


def _looks_like_text(path):
    head = path.read_bytes()[:2000]
    return b'\x00' not in head and sum(32 <= c < 127 or c in (9, 10, 13) for c in head) > 0.95 * len(head)


def _find(folder, pattern):
    hits = sorted(p for p in folder.rglob('*') if fnmatch.fnmatch(p.name.lower(), pattern.lower()))
    if not hits:
        raise CatalogError(f'No file matching {pattern} in the download (has the provider changed the package?)')
    return hits[0]


def _read_vector(path, ctx, layer=None):
    """Features of a local file that touch the area (pyogrio filters while reading, so big files stay fast)."""
    mask = gpd.GeoSeries([ctx.area.geometry()], crs=ctx.area.gdf.crs)
    gdf = gpd.read_file(path, layer=layer, mask=mask)
    ctx.log(f'  {len(gdf):,} features touch the area')
    return gdf


def _items(kind, path, ctx, spec):
    if kind == 'vector':
        return _read_vector(path, ctx, spec.get('layer_name'))
    return path


@handler('download')
def download(ctx):
    """A file or zip the provider offers only as a whole. Spec: url, kind (vector | raster), member (file name or
    pattern inside the zip), layer_name (for geodatabases / GeoPackages)."""
    s = ctx.spec
    folder = _cached_download(s['url'], ctx)
    path = _find(folder, s['member']) if s.get('member') else next(folder.iterdir())
    return Fetched(s['kind'], [(ctx.layer, _items(s['kind'], path, ctx, s))], sources=[s['url']],
                   resampling=s.get('resampling', 'bilinear'))


@handler('download-template')
def download_template(ctx):
    """One download per parameter value, e.g. one grid per date. Spec: url_template (Python format string over the
    params; dates take format codes such as {date:%Y%m%d}), iterate (the list parameter), kind, member,
    min_interval_s (pause between requests, for providers that ask for it)."""
    s = ctx.spec
    it = s['iterate']
    items, sources = [], []
    values = ctx.params[it]
    for v in values:
        p = dict(ctx.params, **{it: v})
        url = s['url_template'].format(**p)
        folder = _cached_download(url, ctx, s.get('min_interval_s', 0))
        path = _find(folder, s['member'])
        tag = v.strftime('%Y%m%d') if hasattr(v, 'strftime') else str(v)
        items.append((f'{ctx.layer}_{tag}', _items(s['kind'], path, ctx, s)))
        sources.append(url)
    return Fetched(s['kind'], items, sources=sources, resampling=s.get('resampling', 'bilinear'))


def _legacy_raster_via_arcpy(src, ctx):
    """ESRI grids and other formats Esri's GDAL build cannot open: copy to a local folder first (never open data
    on the share in ArcGIS; it can rewrite files) and convert with arcpy."""
    try:
        import arcpy
    except ImportError:
        raise CatalogError(f'{src.name} is an ESRI grid; reading it needs GDAL with the AIG driver or ArcGIS Pro') from None
    ws = ctx.workdir / 'ws'
    ws.mkdir()
    shutil.copytree(src, ws / src.name)
    if (src.parent / 'info').is_dir():
        shutil.copytree(src.parent / 'info', ws / 'info')
    out = ctx.workdir / f'{src.name}.tif'
    arcpy.management.CopyRaster(str(ws / src.name), str(out), format='TIFF')
    return out


@handler('local')
def local(ctx):
    """SCCWRP's own copy on the internal server. Spec: holding (key in the internal holdings file), kind,
    layer_name, resampling."""
    s = ctx.spec
    path = holdings.layer_path(s['holding'])
    notes = []
    if s['kind'] == 'raster':
        try:
            gdal.Open(str(path)).GetDriver()
        except RuntimeError:
            if path.is_dir():
                ctx.log('  ESRI grid: copying to a local folder and converting with ArcGIS')
                path = _legacy_raster_via_arcpy(path, ctx)
                notes.append('Source is a legacy ESRI grid; convert it to GeoTIFF / COG on the new server.')
            else:
                raise
    return Fetched(s['kind'], [(ctx.layer, _items(s['kind'], path, ctx, s))], sources=['internal copy'],
                   resampling=s.get('resampling', 'bilinear'), notes=notes)
