"""Clip areas: turn an area request into one boundary.

Area strings (see catalog/clip_areas.json):
  ca-border | ca-watersheds | socal | central-ca | norcal | smc-region
  county:Los Angeles,Orange        county-coastal:Orange (counties extended over coastal water)
  huc8:18070105   huc12:180701050101,180701050102   (levels 2, 4, 6, 8, 10, 12)
  regional-board:4,8               smc-watershed:San Gabriel,Los Angeles
  file:C:/path/area.shp            file:C:/path/areas.gpkg|layer   (or just a path to an existing file)
Python callers can also pass a GeoDataFrame, GeoSeries or shapely geometry (assumed EPSG:4326 if it has no CRS).

Boundaries fetched from services are cached under <cache>/areas/, so each is downloaded once.
"""
import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import geopandas as gpd
import shapely
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from . import config, holdings
from .entries import CatalogError

WORK_CRS = config.EQUAL_AREA


@lru_cache(maxsize=None)
def registry():
    return {a['id']: a for a in json.loads(config.CLIP_AREAS_FILE.read_text(encoding='utf-8'))['areas']}


def areas():
    """Area types a user can choose, as (id, label, selection) tuples."""
    return [(a['id'], a['label'], a['selection']) for a in registry().values()]


@dataclass
class Area:
    key: str                    # normalised request, used in cache keys and file names
    label: str
    gdf: gpd.GeoDataFrame       # the member units (counties, HUCs, ...) in WORK_CRS, with a 'name' column

    @property
    def union(self):
        return self.gdf.geometry.union_all()

    def geometry(self, crs=WORK_CRS):
        g = self.union
        return g if crs == WORK_CRS else gpd.GeoSeries([g], crs=WORK_CRS).to_crs(crs).iloc[0]

    def bounds(self, crs=WORK_CRS, pad=0):
        """Bounding box in crs. Densified, so a box reprojected to another CRS still contains the whole area."""
        g = gpd.GeoSeries([self.union], crs=WORK_CRS)
        if crs != WORK_CRS:
            g = g.segmentize(1000).to_crs(crs)
        minx, miny, maxx, maxy = g.total_bounds
        return minx - pad, miny - pad, maxx + pad, maxy + pad

    @property
    def km2(self):
        return self.union.area / 1e6

    def buffered(self, km):
        if not km:
            return self
        g = self.union.buffer(km * 1000)
        return Area(f'{self.key}+{km:g}km', f'{self.label} plus {km:g} km',
                    gpd.GeoDataFrame({'name': [self.label]}, geometry=[g], crs=WORK_CRS))

    def as_bbox(self):
        return Area(f'{self.key}#bbox', f'bounding box of {self.label}',
                    gpd.GeoDataFrame({'name': [self.label]}, geometry=[box(*self.union.bounds)], crs=WORK_CRS))

    def fingerprint(self):
        """Changes when the boundary changes (custom files, a provider editing county lines)."""
        return hashlib.sha1(self.union.wkb).hexdigest()[:12]


# ----- boundary sources -------------------------------------------------------------------------------------------

def _cached_layer(name, fetch):
    """Read <cache>/areas/<name>.gpkg, or build it with fetch() -> GeoDataFrame and save it."""
    p = config.cache_dir() / 'areas' / f'{name}.gpkg'
    if p.exists():
        return gpd.read_file(p)
    gdf = fetch().to_crs(WORK_CRS)
    p.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(p, driver='GPKG')
    return gdf


def _service(url, where='1=1', fields='*'):
    from .handlers.arcgis import query_features
    return query_features(url, where=where, out_fields=fields, log=lambda m: None)


def _norm(s):
    return re.sub(r'\s+county$', '', s.strip(), flags=re.I).lower()


def _pick(gdf, field, wanted, what):
    """Rows of gdf whose field matches the wanted names or codes (case-insensitive, 'County' optional)."""
    keys = gdf[field].astype(str).map(_norm)
    rows = []
    for w in wanted:
        m = gdf[keys == _norm(w)]
        if m.empty:
            raise CatalogError(f'No {what} "{w}". Choices: {", ".join(sorted(gdf[field].astype(str)))[:600]}')
        rows.append(m)
    import pandas as pd
    return gpd.GeoDataFrame(pd.concat(rows), crs=gdf.crs)


def counties(coastal=False):
    reg = registry()['county']
    if coastal:
        url = reg['variants']['with_coastal_buffers']['url']
        gdf = _cached_layer('counties_coastal', lambda: _service(url, fields='CDTFA_COUNTY,CENSUS_GEOID'))
        gdf = gdf.dissolve('CDTFA_COUNTY', as_index=False)   # land and offshore parts are separate features
    else:
        src = reg['source']
        gdf = _cached_layer('counties', lambda: _service(src['url'], fields=f'{src["name_field"]},{src["code_field"]}'))
    return gdf.rename(columns={'CDTFA_COUNTY': 'name', 'CENSUS_GEOID': 'code'})


def _counties_area(names, coastal, key, label=None):
    gdf = counties(coastal)
    try:
        sel = _pick(gdf, 'name', names, 'county')
    except CatalogError as by_name:
        try:
            sel = _pick(gdf, 'code', names, 'county')   # FIPS codes such as 06059
        except CatalogError:
            raise by_name from None
    if label is None:
        found = [re.sub(r'\s+County$', '', n) for n in sel['name']]
        label = (', '.join(found) + (' County' if len(found) == 1 else ' Counties')
                 + (' with coastal waters' if coastal else ''))
    return Area(key, label, sel[['name', 'code', 'geometry']])


def _huc(level, codes):
    reg = registry()['huc']
    if int(level) not in reg['offered_levels']:
        raise CatalogError(f'HUC level {level} not offered; choose one of {reg["offered_levels"]}')
    field = f'huc{level}'
    bad = [c for c in codes if not re.fullmatch(rf'\d{{{level}}}', c)]
    if bad:
        raise CatalogError(f'{field} codes have {level} digits: {bad}')
    staged = _staged_huc(level, codes)
    if staged is not None:
        return staged
    url = f'{reg["source"]["url"]}/{reg["source"]["layers"][str(level)]}'
    parts = []
    for c in codes:
        g = _cached_layer(f'{field}_{c}', lambda c=c: _service(url, f"{field} = '{c}'", f'{field},name'))
        if g.empty:
            raise CatalogError(f'No {field} {c} in the Watershed Boundary Dataset')
        parts.append(g.rename(columns={field: 'code'}))
    import pandas as pd
    return gpd.GeoDataFrame(pd.concat(parts), crs=WORK_CRS)[['code', 'name', 'geometry']]


def staged_huc_layer(level):
    """Path of the fast-tier GeoPackage if WBD HUC<level> is staged there, else None."""
    fast = config.fast_dir()
    if (fast / 'meta' / f'wbd__huc{level}.json').exists() and (fast / 'sccwrp_fast.gpkg').exists():
        return fast / 'sccwrp_fast.gpkg'
    return None


def _staged_huc(level, codes):
    """HUC units from the staged WBD copy (instant) instead of the USGS service (tens of seconds per unit)."""
    path = staged_huc_layer(level)
    if path is None:
        return None
    quoted = ','.join(f"'{c}'" for c in codes)
    g = gpd.read_file(path, layer=f'wbd__huc{level}', where=f'HUC{level} IN ({quoted})',
                      columns=[f'HUC{level}', 'NAME'])
    missing = set(codes) - set(g[f'HUC{level}'])
    if missing:
        raise CatalogError(f'No huc{level} {", ".join(sorted(missing))} in the Watershed Boundary Dataset '
                           f'(California area)')
    return g.rename(columns={f'HUC{level}': 'code', 'NAME': 'name'}).to_crs(WORK_CRS)[['code', 'name', 'geometry']]


def _regional_boards(codes):
    src = registry()['regional-board']['source']
    gdf = _cached_layer('regional_boards', lambda: _service(src['url'], fields='rb,rb_name'))
    gdf = gdf.rename(columns={'rb': 'code', 'rb_name': 'name'})
    gdf['code'] = gdf['code'].astype(int).astype(str)
    try:
        return _pick(gdf, 'code', codes, 'Regional Board region')
    except CatalogError:
        return _pick(gdf, 'name', codes, 'Regional Board region')


def _smc():
    path = holdings.clip_area_path('smc-watersheds-2026')
    gdf = gpd.read_file(path).to_crs(WORK_CRS)
    src = registry()['smc-watershed']['source']
    return gdf.rename(columns={src['name_field']: 'name', src['code_field']: 'code'})


def _ca_border():
    src = registry()['ca-border']['source']
    return _cached_layer('ca_border', lambda: _service(src['url'], src['where'], 'NAME,STUSAB'))


# ----- ocean ----------------------------------------------------------------------------------------------------

def _src(kind):
    return registry()[kind]['source']


def marine_regions():
    s = _src('marine-region')
    g = _cached_layer('marine_regions', lambda: _service(s['url'], fields=f'{s["name_field"]},{s["code_field"]}'))
    return g.rename(columns={s['name_field']: 'name', s['code_field']: 'code'})


def bight_strata(year=None):
    s = _src('bight-strata')
    year = str(year or s['default_year'])
    if year not in s['layers']:
        raise CatalogError(f'No Bight {year} strata; surveys: {", ".join(sorted(s["layers"]))}')
    url = f'{s["url"]}/{s["layers"][year]}'
    g = _cached_layer(f'bight_strata_{year}', lambda: _service(url, fields=s['name_field']))
    g = g.rename(columns={s['name_field']: 'name'}).dissolve('name', as_index=False)
    return g.assign(code=g['name'])


def mpas():
    s = _src('mpa')
    g = _cached_layer('mpas', lambda: _service(s['url'], fields=f'{s["name_field"]},{s["code_field"]}'))
    g = g.rename(columns={s['name_field']: 'name', s['code_field']: 'code'})
    g['code'] = g['code'].astype(str)
    return g


def asbs():
    s = _src('asbs')
    g = _cached_layer('asbs', lambda: _service(s['url'], fields=f'{s["name_field"]},{s["code_field"]}'))
    g = g.rename(columns={s['name_field']: 'name', s['code_field']: 'code'})
    g['code'] = g['code'].astype(int).astype(str)
    return g.dissolve('code', as_index=False, aggfunc='first')


@lru_cache(maxsize=None)
def land():
    """California's land (CDT county boundaries, dissolved): the mask for land-only / water-only requests."""
    p = config.cache_dir() / 'areas' / 'ca_land.gpkg'
    if p.exists():
        return gpd.read_file(p).geometry.iloc[0]
    g = counties().union_all().buffer(0)
    gpd.GeoDataFrame(geometry=[g], crs=WORK_CRS).to_file(p, driver='GPKG')
    return g


def _state_waters():
    p = config.cache_dir() / 'areas' / 'state_waters.gpkg'
    if not p.exists():
        g = counties(coastal=True).union_all().difference(land().buffer(1))
        gpd.GeoDataFrame({'name': ['California state waters']}, geometry=[g], crs=WORK_CRS).to_file(p, driver='GPKG')
    return gpd.read_file(p)


def _ca_ocean():
    p = config.cache_dir() / 'areas' / 'ca_ocean.gpkg'
    if not p.exists():
        lat_box = gpd.GeoSeries([box(-127.5, 30.5, -116.5, 42.2)], crs='EPSG:4326').segmentize(0.1).to_crs(WORK_CRS).iloc[0]
        g = land().buffer(370_000).intersection(lat_box).difference(land().buffer(1))
        gpd.GeoDataFrame({'name': ['California ocean']}, geometry=[g], crs=WORK_CRS).to_file(p, driver='GPKG')
    return gpd.read_file(p)


def depth_band(lo, hi):
    """Ocean between lo and hi metres deep, from the staged statewide bathymetry (polygonized once per band)."""
    from osgeo import gdal, ogr, osr
    p = config.cache_dir() / 'areas' / f'depth_{lo:g}_{hi:g}.gpkg'
    if p.exists():
        return gpd.read_file(p)
    meta = config.fast_dir() / 'meta' / 'cdfw_marine__bathymetry_200m.json'
    if not meta.exists():
        raise CatalogError('Depth bands need the staged statewide bathymetry: '
                           'python -m sccwrp_data stage cdfw-marine --layer bathymetry-200m')
    tif = config.fast_dir() / json.loads(meta.read_text(encoding='utf-8'))['file']
    import numpy as np
    ds = gdal.Open(str(tif))
    b = ds.GetRasterBand(1)
    a = b.ReadAsArray().astype('float64')
    nd = b.GetNoDataValue()
    valid = np.isfinite(a) if nd is None else (a != nd) & np.isfinite(a)
    depth = -a if np.nanmedian(a[valid]) < 0 else a        # elevations below sea level are negative
    inband = valid & (depth >= lo) & (depth < hi)
    mem = gdal.GetDriverByName('MEM').Create('', ds.RasterXSize, ds.RasterYSize, 1, gdal.GDT_Byte)
    mem.SetGeoTransform(ds.GetGeoTransform())
    mem.SetProjection(ds.GetProjection())
    mem.GetRasterBand(1).WriteArray(inband.astype('uint8'))
    vds = (ogr.GetDriverByName('MEM') or ogr.GetDriverByName('Memory')).CreateDataSource('')
    srs = osr.SpatialReference(wkt=ds.GetProjection())
    lyr = vds.CreateLayer('band', srs=srs)
    lyr.CreateField(ogr.FieldDefn('v', ogr.OFTInteger))
    gdal.Polygonize(mem.GetRasterBand(1), mem.GetRasterBand(1), lyr, 0)
    geoms = [shapely.from_wkb(bytes(f.GetGeometryRef().ExportToWkb())) for f in lyr]
    if not geoms:
        raise CatalogError(f'No ocean between {lo:g} and {hi:g} m in the bathymetry')
    g = gpd.GeoDataFrame(geometry=[shapely.union_all(geoms)], crs=srs.ExportToWkt()).to_crs(WORK_CRS)
    g = g.assign(name=f'{lo:g}-{hi:g} m deep')
    g.to_file(p, driver='GPKG')
    return g


def _from_file(spec):
    path, _, layer = spec.partition('|')
    if not Path(path).exists():
        raise CatalogError(f'Area file not found: {path}')
    gdf = gpd.read_file(path, layer=layer or None)
    if gdf.crs is None:
        raise CatalogError(f'{path} has no coordinate system; define one before using it as an area')
    return gdf.to_crs(WORK_CRS)


# ----- entry point ------------------------------------------------------------------------------------------------

MASKS = ('both', 'land', 'water')


def resolve(area, buffer_km=0, method='clip', mask='both'):
    """Area string / geometry -> Area, with the buffer, land/water mask and bounding-box option applied.

    Area strings joined with & are intersected: 'county-coastal:Orange&depth:0-30'."""
    if mask not in MASKS:
        raise CatalogError(f'mask must be one of {", ".join(MASKS)}')
    if isinstance(area, str) and '&' in area:
        parts = [_resolve(p) for p in area.split('&')]
        g = parts[0].union
        for p in parts[1:]:
            g = g.intersection(p.union)
        if g.is_empty:
            raise CatalogError(f'The areas in "{area}" do not overlap')
        a = Area('&'.join(p.key for p in parts), ' ∩ '.join(p.label for p in parts),
                 gpd.GeoDataFrame({'name': [' ∩ '.join(p.label for p in parts)]}, geometry=[g], crs=WORK_CRS))
    else:
        a = _resolve(area)
    if a.key.startswith('point-') and not buffer_km:
        buffer_km = 1
    a = a.buffered(buffer_km)
    if mask != 'both':
        g = a.union.intersection(land()) if mask == 'land' else a.union.difference(land())
        if g.is_empty:
            raise CatalogError(f'{a.label} has no {mask} ({"it is all water" if mask == "land" else "it is all land"})')
        a = Area(f'{a.key}~{mask}', f'{a.label}, {mask} only',
                 gpd.GeoDataFrame({'name': [a.label]}, geometry=[g], crs=WORK_CRS))
    return a.as_bbox() if method == 'bbox' else a


def _resolve(area):
    if isinstance(area, Area):
        return area
    if isinstance(area, (gpd.GeoDataFrame, gpd.GeoSeries, BaseGeometry)):
        if isinstance(area, BaseGeometry):
            area = gpd.GeoSeries([area], crs='EPSG:4326')
        if area.crs is None:
            area = area.set_crs('EPSG:4326')
        g = gpd.GeoDataFrame({'name': ['custom area']}, geometry=[area.to_crs(WORK_CRS).union_all()], crs=WORK_CRS)
        a = Area('custom', 'custom area', g)
        a.key = f'custom-{a.fingerprint()}'
        return a
    s = str(area).strip()
    kind, _, rest = s.partition(':')
    kind = kind.lower()
    values = [v.strip() for v in rest.split(',') if v.strip()]
    if len(kind) == 1 or (kind not in registry() and kind not in ('county-coastal', 'file')
                          and not re.fullmatch(r'huc\d+', kind) and Path(s).exists()):
        kind, rest = 'file', s    # a Windows path such as C:\... or any existing file
    if kind == 'file':
        gdf = _from_file(rest)
        a = Area('file', Path(rest.partition('|')[0]).stem, gdf)
        a.gdf['name'] = a.gdf.get('name', a.label)
        a.key = f'file-{Path(rest.partition("|")[0]).stem}-{a.fingerprint()}'
        return a
    if kind == 'ca-border':
        return Area('ca-border', 'California', _ca_border().assign(name='California'))
    if kind == 'ca-watersheds':
        g = gpd.read_file(config.CLIP_AREAS_DIR / 'california_watersheds.gpkg').to_crs(WORK_CRS)
        return Area('ca-watersheds', registry()['ca-watersheds']['label'], g.assign(name='California by watersheds'))
    if kind in ('county', 'county-coastal'):
        if not values:
            raise CatalogError('Name one or more counties, e.g. county:Los Angeles,Orange')
        key = f'{kind}-' + '-'.join(sorted(_norm(v).replace(' ', '_') for v in values))
        return _counties_area(values, kind == 'county-coastal', key)
    reg = registry().get(kind)
    if reg and reg['source'].get('type') == 'county-group':
        return _counties_area(reg['counties'], False, kind, reg['label'])
    m = re.fullmatch(r'huc(\d+)', kind)
    if m:
        if not values:
            raise CatalogError(f'Give one or more codes, e.g. huc8:18070105')
        return Area(f'huc{m.group(1)}-' + '-'.join(sorted(values)), f'HUC{m.group(1)} ' + ', '.join(values),
                    _huc(m.group(1), values))
    if kind == 'regional-board':
        sel = _regional_boards(values)
        return Area('rb-' + '-'.join(sorted(sel['code'])), 'Regional Board ' + ', '.join(sel['name']), sel)
    if kind == 'smc-region':
        g = _smc()
        return Area('smc-region', 'SMC region', gpd.GeoDataFrame({'name': ['SMC region']},
                                                                  geometry=[g.union_all()], crs=WORK_CRS))
    if kind == 'marine-region':
        if not values:
            raise CatalogError('Name one or more regions: ' + ', '.join(marine_regions()['code']))
        try:
            sel = _pick(marine_regions(), 'code', values, 'marine region')
        except CatalogError:
            sel = _pick(marine_regions(), 'name', values, 'marine region')
        return Area('marine-' + '-'.join(sorted(sel['code'])), ', '.join(sel['name']), sel)
    m = re.fullmatch(r'bight-strata(?:-(\d{4}))?', kind)
    if m:
        g = bight_strata(m.group(1))
        sel = _pick(g, 'name', values, 'Bight stratum') if values else g
        year = m.group(1) or _src('bight-strata')['default_year']
        return Area(f'bight{year}-' + ('-'.join(sorted(_norm(v).replace(' ', '_') for v in values)) or 'all'),
                    f'Bight {year} ' + (', '.join(values) if values else 'survey area'), sel)
    if kind == 'bight':
        g = bight_strata()
        return Area('bight', 'Southern California Bight', gpd.GeoDataFrame(
            {'name': ['Southern California Bight']}, geometry=[g.union_all()], crs=WORK_CRS))
    if kind in ('mpa', 'asbs'):
        g = mpas() if kind == 'mpa' else asbs()
        what = 'MPA' if kind == 'mpa' else 'ASBS'
        if not values:
            raise CatalogError(f'Name one or more {what}s (see python -m sccwrp_data areas {kind})')
        try:
            sel = _pick(g, 'code', values, what)
        except CatalogError:
            sel = _pick(g, 'name', values, what)
        return Area(f'{kind}-' + '-'.join(sorted(sel['code'])), f'{what} ' + ', '.join(sel['name']), sel)
    if kind == 'state-waters':
        return Area('state-waters', 'California state waters', _state_waters())
    if kind == 'ca-ocean':
        return Area('ca-ocean', 'California ocean', _ca_ocean())
    if kind == 'depth':
        m = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*', rest)
        if not m:
            raise CatalogError('Depth bands are written depth:<shallow>-<deep> in metres, e.g. depth:30-120')
        lo, hi = float(m.group(1)), float(m.group(2))
        return Area(f'depth-{lo:g}-{hi:g}', f'{lo:g}-{hi:g} m deep', depth_band(lo, hi))
    if kind == 'point':
        try:
            lon, lat = (float(v) for v in values)
        except ValueError:
            raise CatalogError('A point is written point:<longitude>,<latitude>, e.g. point:-118.27,33.72') from None
        from shapely.geometry import Point
        g = gpd.GeoDataFrame({'name': [f'point {lat:.4f}, {lon:.4f}']}, geometry=[Point(lon, lat)],
                             crs='EPSG:4326').to_crs(WORK_CRS)
        return Area(f'point-{lon:.5f}-{lat:.5f}', f'Point {lat:.4f}, {lon:.4f}', g)
    if kind == 'smc-watershed':
        sel = _pick(_smc(), 'name', values, 'SMC watershed') if values else None
        if sel is None:
            raise CatalogError('Name one or more SMC watersheds, e.g. smc-watershed:San Gabriel')
        return Area('smc-' + '-'.join(sorted(_norm(v).replace(' ', '_') for v in values)),
                    'SMC ' + ', '.join(values), sel)
    raise CatalogError(f'Unknown area "{s}". Area types: {", ".join(registry())}, county-coastal, file:<path>')


def choices(kind):
    """Names a user can pick for a pick-many area type (counties, regional boards, SMC watersheds)."""
    if kind in ('county', 'county-coastal'):
        return sorted(counties(kind == 'county-coastal')['name'])
    if kind == 'regional-board':
        return [f'{r.code} {r.name}' for r in _regional_boards([str(i) for i in range(1, 10)]).itertuples()]
    if kind == 'smc-watershed':
        return list(registry()['smc-watershed']['choices'])
    if kind == 'marine-region':
        return [f'{r.code} {r.name}' for r in marine_regions().itertuples()]
    if kind.startswith('bight-strata'):
        return sorted(bight_strata(kind[13:] or None)['name'])
    if kind in ('mpa', 'asbs'):
        g = mpas() if kind == 'mpa' else asbs()
        return [f'{r.code} {r.name}' for r in g.sort_values('name').itertuples()]
    if kind == 'huc':
        return ['codes of the chosen level, e.g. huc8:18070105 (see https://apps.nationalmap.gov/viewer/)']
    return []
