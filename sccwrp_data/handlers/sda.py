"""USDA NRCS Soil Data Access (SDA): SSURGO map unit polygons touching the area, with map unit attributes.

SDA answers SQL posted to its REST endpoint. Finding the polygons is quick; sending their outlines is slow (about
1 MB of coordinates per 15 s per request), so the outlines are asked for in parallel batches of similar size.
"""
import html
import re
from concurrent.futures import ThreadPoolExecutor

import geopandas as gpd
import pandas as pd
import requests
from shapely import wkt
from shapely.geometry import box

from .. import http
from ..entries import CatalogError
from .arcgis import MAX_QUERY_VERTICES
from .base import Fetched, handler

MAX_POLYGONS = 100_000        # SDA returns at most 100,000 rows per query
BATCH_VERTICES = 25_000       # vertices per outline request (about 1 MB of text)
COLUMNS = {'mapunit': ['musym', 'muname', 'mukind'],
           'muaggatt': ['hydgrpdcd', 'drclassdcd', 'flodfreqdcd', 'slopegradwta', 'aws0150wta', 'brockdepmin',
                        'wtdepannmin', 'hydclprs']}
NAME_RE = re.compile(r'^[a-z][a-z0-9_]*$')


def query(url, sql):
    """Rows of an SDA query, as a DataFrame. SDA reports SQL errors with HTTP 400 and an XML message."""
    try:
        r = http.post(url, data={'query': sql, 'format': 'JSON+COLUMNNAME'})
    except requests.HTTPError as e:
        msg = html.unescape(re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', e.response.text))).strip()
        raise RuntimeError(f'Soil Data Access error: {msg[:400]}') from None
    table = r.json().get('Table') or [[]]
    return pd.DataFrame(table[1:], columns=table[0])


def _area_wkt(area):
    """The area as a WKT filter in EPSG:4326: a simplified, slightly enlarged outline, or its bounding box."""
    g = area.geometry('EPSG:3310').buffer(300).simplify(200)
    g = gpd.GeoSeries([g], crs='EPSG:3310').to_crs('EPSG:4326').iloc[0]
    n = sum(len(p.exterior.coords) for p in getattr(g, 'geoms', [g]))
    if n > MAX_QUERY_VERTICES or g.geom_type not in ('Polygon', 'MultiPolygon'):
        g = box(*g.bounds)
    return wkt.dumps(g, rounding_precision=6)


def _batches(keys, sizes):
    """Polygon keys in groups of about BATCH_VERTICES vertices."""
    out, cur, n = [], [], 0
    for k, v in zip(keys, sizes):
        if cur and n + v > BATCH_VERTICES:
            out.append(cur)
            cur, n = [], 0
        cur.append(k)
        n += v
    return out + [cur] if cur else out


@handler('sda')
def sda(ctx):
    """SSURGO map unit polygons from Soil Data Access. Spec: url (the Tabular/post.rest endpoint), columns
    (map unit attributes to join by mukey, as {table: [column, ...]}; default: mapunit and muaggatt essentials)."""
    s = ctx.spec
    url = s['url']
    columns = s.get('columns', COLUMNS)
    for t, cols in columns.items():
        bad = [c for c in [t] + list(cols) if not NAME_RE.match(c)]
        if bad:
            raise CatalogError(f'Not a Soil Data Access table or column name: {bad}')
    where = f"mupolygongeo.STIntersects(geometry::STGeomFromText('{_area_wkt(ctx.area)}', 4326).MakeValid()) = 1"
    count = query(url, f'SELECT COUNT(*) AS n, SUM(mupolygongeo.STNumPoints()) AS v FROM mupolygon WHERE {where}')
    n, v = int(count['n'][0]), int(count['v'][0] or 0)
    if n >= MAX_POLYGONS:
        raise CatalogError(f'{n:,} soil polygons touch this area; Soil Data Access returns at most '
                           f'{MAX_POLYGONS:,} per request. Use a smaller area, or the gSSURGO state geodatabase.')
    ctx.log(f'  {n:,} map unit polygons touch the area ({v:,} vertices)')
    if not n:
        return Fetched('vector', [(ctx.layer, gpd.GeoDataFrame(geometry=[], crs='EPSG:4326'))], sources=[url])
    keys = query(url, f'SELECT mupolygonkey, mupolygongeo.STNumPoints() AS v FROM mupolygon WHERE {where} '
                      f'ORDER BY mupolygonkey')
    batches = _batches(keys['mupolygonkey'], keys['v'].astype(int))

    def outlines(batch):
        return query(url, f'SELECT mupolygonkey, areasymbol, mukey, mupolygongeo.STAsText() AS wkt FROM mupolygon '
                          f'WHERE mupolygonkey IN ({",".join(batch)})')

    parts = []
    with ThreadPoolExecutor(4) as pool:
        for i, part in enumerate(pool.map(outlines, batches)):
            parts.append(part)
            if len(batches) > 5 and (i + 1) % max(1, len(batches) // 5) == 0:
                ctx.log(f'  {sum(map(len, parts)):,} / {n:,} polygons')
    polys = pd.concat(parts, ignore_index=True)
    gdf = gpd.GeoDataFrame(polys.drop(columns='wkt'), geometry=gpd.GeoSeries.from_wkt(polys['wkt']), crs='EPSG:4326')

    mukeys = ','.join(sorted(gdf['mukey'].unique()))
    select = ', '.join(f'{t}.{c}' for t, cols in columns.items() for c in cols)
    joins = ' '.join(f'LEFT JOIN {t} ON {t}.mukey = mapunit.mukey' for t in columns if t != 'mapunit')
    attrs = query(url, f'SELECT mapunit.mukey, {select} FROM mapunit {joins} WHERE mapunit.mukey IN ({mukeys})')
    gdf = gdf.merge(attrs, on='mukey', how='left')
    for c in attrs.columns.drop('mukey'):           # SDA sends every value as text
        num = pd.to_numeric(gdf[c], errors='coerce')
        if num.notna().sum() == gdf[c].notna().sum():
            gdf[c] = num
    gdf = gdf[['mukey'] + [c for c in gdf.columns if c not in ('mukey', 'geometry')] + ['geometry']]

    areas = ','.join(f"'{a}'" for a in sorted(gdf['areasymbol'].unique()))
    surveys = query(url, f'SELECT areasymbol, CONVERT(char(10), saverest, 23) AS saverest FROM sacatalog '
                         f'WHERE areasymbol IN ({areas}) ORDER BY areasymbol')
    notes = ['Soil survey areas and their last update: ' +
             ', '.join(f'{a} ({d})' for a, d in zip(surveys['areasymbol'], surveys['saverest']))]
    ctx.log(f'  {len(gdf):,} polygons, {len(attrs):,} map units, survey areas {", ".join(surveys["areasymbol"])}')
    return Fetched('vector', [(ctx.layer, gdf)], sources=[url], notes=notes)
