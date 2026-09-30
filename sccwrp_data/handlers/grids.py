"""Gridded data from OGC and scientific services: WCS, OPeNDAP / THREDDS NetCDF, cloud-optimized GeoTIFF tiles."""
import math
import re
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from osgeo import gdal, osr

from .. import http
from ..entries import CatalogError
from .base import Fetched, handler
from .rasterutil import fetch_tiles, mosaic, plan_tiles

# Read remote GeoTIFFs by byte range without listing directories or downloading whole files
REMOTE_GDAL = {'GDAL_DISABLE_READDIR_ON_OPEN': 'EMPTY_DIR', 'CPL_VSIL_CURL_ALLOWED_EXTENSIONS': '.tif,.tiff,.vrt',
               'GDAL_HTTP_MULTIRANGE': 'YES', 'GDAL_HTTP_MERGE_CONSECUTIVE_RANGES': 'YES', 'VSI_CACHE': 'TRUE',
               'GDAL_HTTP_MAX_RETRY': '4', 'GDAL_HTTP_RETRY_DELAY': '2'}


def _fill(template, params):
    return template.format(**params)


@handler('wcs')
def wcs(ctx):
    """OGC WCS 1.0.0 GetCoverage in the coverage's native CRS and cell size, tiled. Spec: url, coverage,
    native_crs, resolution, max_size, time (template filled from params, e.g. "{year}-01-01T00:00:00.000Z"),
    resampling."""
    s = ctx.spec
    crs, res = s['native_crs'], s['resolution']
    tiles = plan_tiles(ctx.area.bounds(crs, pad=2 * res), res, int(s.get('max_size', 2000)),
                       keep=ctx.area.geometry(crs))
    extra = {}
    if s.get('time'):
        extra['time'] = _fill(s['time'], ctx.params)

    def one(i, t):
        minx, miny, maxx, maxy, w, h = t
        p = dict(service='WCS', version='1.0.0', request='GetCoverage', coverage=s['coverage'], crs=crs,
                 bbox=f'{minx},{miny},{maxx},{maxy}', width=w, height=h, format=s.get('format', 'GeoTIFF'), **extra)
        r = http.get(s['url'], params=p)
        if 'xml' in r.headers.get('content-type', ''):
            raise RuntimeError(f'WCS error: {r.text[:400]}')
        out = ctx.workdir / f'wcs_{i}.tif'
        out.write_bytes(r.content)
        return out

    vrt = mosaic(fetch_tiles(tiles, one, ctx.log), ctx.workdir / 'mosaic.vrt')
    return Fetched('raster', [(ctx.layer, vrt)], sources=[s['url']], resampling=s.get('resampling', 'nearest'))


DAP_TYPES = {'Float32': '>f4', 'Float64': '>f8', 'Int32': '>i4', 'UInt32': '>u4', 'Int16': '>i4', 'UInt16': '>u4',
             'Byte': 'u1'}   # DAP2 sends 16-bit integers as 32-bit XDR values
DAP_MAX_CELLS = 16_000_000     # cells per request; larger areas are read in row bands


def _dap_variable(url, name):
    """(DAP type, [(dimension, size)]) of a variable, from the dataset's DDS."""
    dds = http.get(url + '.dds').text
    m = re.search(rf'\b(\w+)\s+{re.escape(name)}((?:\[\s*\w+\s*=\s*\d+\s*\])+)\s*;', dds)
    if not m:
        raise CatalogError(f'No variable {name} in {url}')
    return m.group(1), [(d, int(n)) for d, n in re.findall(r'\[\s*(\w+)\s*=\s*(\d+)\s*\]', m.group(2))]


def _dap_read(url, expr, dap_type):
    """First array in a DAP2 binary response (.dods): the DDS text, 'Data:', then XDR values."""
    r = http.get(f'{url}.dods?{expr}', timeout=600)
    body = r.content
    at = body.find(b'\nData:\n')
    if at < 0:
        raise RuntimeError(f'OPeNDAP error for {expr}: {body[:300]!r}')
    data = memoryview(body)[at + 7:]
    n = int(np.frombuffer(data[:4], '>u4')[0])
    return np.frombuffer(data[8:8 + n * np.dtype(DAP_TYPES[dap_type]).itemsize], DAP_TYPES[dap_type])


@handler('opendap')
def opendap(ctx):
    """NetCDF grid over OPeNDAP (DAP2): read only the rows and columns covering the area. Spec: url, variable,
    x, y (coordinate variable names), crs, nodata, fill_value, fixed (index for any other dimension, e.g. {"time": 0}).

    Speaks DAP2 over the shared HTTP session instead of relying on the NetCDF library's remote access, which
    ArcGIS Pro's default environment lacks."""
    s = ctx.spec
    url = s['url']
    vtype, dims = _dap_variable(url, s['variable'])
    names = [d for d, _ in dims]
    if names[-2:] != [s['y'], s['x']]:
        raise CatalogError(f'{s["variable"]} dimensions are {names}; expected the last two to be {s["y"]}, {s["x"]}')
    xs = _dap_read(url, s['x'], _dap_variable(url, s['x'])[0]).astype('float64')
    ys = _dap_read(url, s['y'], _dap_variable(url, s['y'])[0]).astype('float64')
    dx, dy = abs(xs[1] - xs[0]), abs(ys[1] - ys[0])
    minx, miny, maxx, maxy = ctx.area.bounds(s['crs'])
    ix = np.where((xs >= minx - 2 * dx) & (xs <= maxx + 2 * dx))[0]
    iy = np.where((ys >= miny - 2 * dy) & (ys <= maxy + 2 * dy))[0]
    if not len(ix) or not len(iy):
        raise CatalogError(f'The area is outside this grid ({xs.min():.3f}..{xs.max():.3f}, {ys.min():.3f}..{ys.max():.3f})')
    lead = ''.join(f'[{int(s.get("fixed", {}).get(d, 0))}]' for d in names[:-2])
    x0i, x1i, y0i, y1i = int(ix[0]), int(ix[-1]), int(iy[0]), int(iy[-1])
    ncols = x1i - x0i + 1
    ctx.log(f'  reading {y1i - y0i + 1:,} x {ncols:,} cells of {dims[-2][1]:,} x {dims[-1][1]:,}')
    band = max(1, DAP_MAX_CELLS // ncols)
    parts = []
    for r0 in range(y0i, y1i + 1, band):
        r1 = min(y1i, r0 + band - 1)
        vals = _dap_read(url, f'{s["variable"]}{lead}[{r0}:1:{r1}][{x0i}:1:{x1i}]', vtype)
        parts.append(vals.reshape(r1 - r0 + 1, ncols))
    sub = np.vstack(parts).astype('float32')
    yv = ys[y0i:y1i + 1]
    if yv[0] < yv[-1]:          # rows stored south to north; GeoTIFF wants north first
        sub, yv = sub[::-1], yv[::-1]
    nodata = s.get('nodata', -9999.0)
    sub[np.isnan(sub)] = nodata
    if 'fill_value' in s:
        sub[sub == s['fill_value']] = nodata
    xv = xs[x0i:x1i + 1]
    x0 = float(xv[0]) - dx / 2          # cell centres -> upper-left corner
    y0 = float(yv[0]) + dy / 2
    out = ctx.workdir / 'opendap.tif'
    d = gdal.GetDriverByName('GTiff').Create(str(out), sub.shape[1], sub.shape[0], 1, gdal.GDT_Float32,
                                             options=['COMPRESS=DEFLATE', 'TILED=YES'])
    d.SetGeoTransform((x0, dx, 0, y0, 0, -dy))
    srs = osr.SpatialReference()
    srs.SetFromUserInput(s['crs'])
    d.SetProjection(srs.ExportToWkt())
    b = d.GetRasterBand(1)
    b.SetNoDataValue(nodata)
    b.WriteArray(sub)
    d = b = None
    return Fetched('raster', [(ctx.layer, out)], sources=[url], resampling=s.get('resampling', 'bilinear'))


def _usgs_1deg(bounds):
    """USGS 3DEP 1x1 degree tile names (named by their north-west corner, e.g. n34w119) covering bounds."""
    minx, miny, maxx, maxy = bounds
    names = []
    for lat in range(math.floor(miny) + 1, math.ceil(maxy) + 1):
        for lon in range(math.floor(minx), math.ceil(maxx)):
            names.append(f'{"n" if lat >= 0 else "s"}{abs(lat):02d}{"w" if lon < 0 else "e"}{abs(lon):03d}')
    return names


TILE_SCHEMES = {'usgs-1deg': _usgs_1deg}


@handler('cog-tiles')
def cog_tiles(ctx):
    """Tiled GeoTIFFs on a public bucket, read in place (only the byte ranges covering the area are transferred).
    Spec: url_template with {tile}, tile_scheme, crs (of the tile names), resampling."""
    s = ctx.spec
    names = TILE_SCHEMES[s['tile_scheme']](ctx.area.bounds(s['crs']))
    urls = [s['url_template'].format(tile=n, **ctx.params) for n in names]
    with ThreadPoolExecutor(8) as pool:
        present = [u for u, ok in zip(urls, pool.map(http.exists, urls)) if ok]
    ctx.log(f'  {len(present)} of {len(urls)} tiles exist (the rest are open ocean or outside the dataset)')
    if not present:
        raise CatalogError('No tiles of this dataset cover the area.')
    for k, v in REMOTE_GDAL.items():
        gdal.SetConfigOption(k, v)
    vrt = mosaic([f'/vsicurl/{u}' for u in present], ctx.workdir / 'tiles.vrt')
    return Fetched('raster', [(ctx.layer, vrt)], sources=present, resampling=s.get('resampling', 'bilinear'))
