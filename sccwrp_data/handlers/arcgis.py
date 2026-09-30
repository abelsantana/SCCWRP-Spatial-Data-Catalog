"""ArcGIS REST services: feature/map service layers (vector query) and image services (exportImage)."""
import json
from concurrent.futures import ThreadPoolExecutor

import geopandas as gpd
from shapely.geometry import box, mapping
from shapely.geometry.polygon import orient

from .. import http
from .base import Fetched, handler
from .rasterutil import epsg, fetch_tiles, georeference, mosaic, plan_tiles

MAX_QUERY_VERTICES = 1500   # above this, send the area's bounding box instead of its outline


def _esri_polygon(geom):
    """shapely (Multi)Polygon in EPSG:4326 -> Esri JSON polygon (outer rings clockwise, holes counter-clockwise)."""
    polys = getattr(geom, 'geoms', [geom])
    rings = []
    for p in polys:
        p = orient(p, sign=-1.0)
        rings.append([list(c) for c in p.exterior.coords])
        rings += [[list(c) for c in r.coords] for r in p.interiors]
    return {'rings': rings, 'spatialReference': {'wkid': 4326}}


def _query_geometry(area):
    """The area as a query filter: a simplified, slightly enlarged outline so nothing inside is missed."""
    g = area.geometry('EPSG:3310').buffer(300).simplify(200)
    g4326 = gpd.GeoSeries([g], crs='EPSG:3310').to_crs('EPSG:4326').iloc[0]
    n = sum(len(p.exterior.coords) for p in getattr(g4326, 'geoms', [g4326]))
    if n > MAX_QUERY_VERTICES or g4326.geom_type not in ('Polygon', 'MultiPolygon'):
        minx, miny, maxx, maxy = g4326.bounds
        return {'xmin': minx, 'ymin': miny, 'xmax': maxx, 'ymax': maxy, 'spatialReference': {'wkid': 4326}}, \
            'esriGeometryEnvelope'
    return _esri_polygon(g4326), 'esriGeometryPolygon'


def _esri_to_geojson(g):
    """Esri JSON geometry -> GeoJSON-like dict (fallback for services without f=geojson)."""
    if g is None:
        return None
    if 'x' in g:
        return {'type': 'Point', 'coordinates': [g['x'], g['y']]}
    if 'points' in g:
        return {'type': 'MultiPoint', 'coordinates': g['points']}
    if 'paths' in g:
        return {'type': 'MultiLineString', 'coordinates': g['paths']}
    if 'rings' in g:
        from shapely.geometry import LinearRing, Polygon
        from shapely.ops import unary_union
        outers, holes = [], []
        for r in g['rings']:
            (outers if LinearRing(r).is_ccw is False else holes).append(Polygon(r))
        polys = [p for p in outers]
        for h in holes:
            for i, p in enumerate(polys):
                if p.contains(h.representative_point()):
                    polys[i] = p.difference(h)
                    break
        return mapping(unary_union(polys))
    raise ValueError(f'Unknown Esri geometry: {list(g)[:3]}')


def layer_info(url):
    return http.arcgis_json(url)


def _ids_query(url, where, geom, gtype):
    p = {'where': where, 'returnIdsOnly': 'true'}
    if geom is not None:
        p.update(geometry=json.dumps(geom), geometryType=gtype, inSR=4326, spatialRel='esriSpatialRelIntersects')
    return http.arcgis_json(url + '/query', p, method='post', retry=False).get('objectIds') or []


def _object_ids(url, where, area, log, max_depth=4):
    """Object ids of the features touching area. Busy servers time out on one big spatial query (NHDPlus HR
    flowlines at USGS do), so on failure the area's bounding box is split into quarters, recursively."""
    if area is None:
        return _ids_query(url, where, None, None)
    geom, gtype = _query_geometry(area)
    try:
        return _ids_query(url, where, geom, gtype)
    except http.TRANSIENT as e:
        log(f'  spatial query timed out ({type(e).__name__}); splitting the area into smaller boxes')
    shape = area.geometry('EPSG:4326')

    def split(b, depth):
        if not shape.intersects(box(*b)):
            return set()
        env = {'xmin': b[0], 'ymin': b[1], 'xmax': b[2], 'ymax': b[3], 'spatialReference': {'wkid': 4326}}
        try:
            return set(_ids_query(url, where, env, 'esriGeometryEnvelope'))
        except http.TRANSIENT:
            if depth >= max_depth:
                raise
        mx, my = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        quads = [(b[0], b[1], mx, my), (mx, b[1], b[2], my), (b[0], my, mx, b[3]), (mx, my, b[2], b[3])]
        return set().union(*(split(q, depth + 1) for q in quads))

    ids = split(shape.bounds, 1)
    return list(ids)


def query_features(url, where='1=1', area=None, out_fields='*', log=print, workers=4):
    """All features of a layer matching where (and touching area), as a GeoDataFrame in EPSG:4326.

    Pages by object id, which works on every ArcGIS Server version and ignores maxRecordCount limits.
    """
    meta = layer_info(url)
    page = min(int(meta.get('maxRecordCount') or 1000), 2000)
    formats = (meta.get('supportedQueryFormats') or '').lower()
    use_geojson = 'geojson' in formats
    ids = _object_ids(url, where, area, log)
    oid_field = meta.get('objectIdField') or next((f['name'] for f in meta.get('fields', [])
                                                   if f['type'] == 'esriFieldTypeOID'), 'OBJECTID')
    log(f'  {len(ids):,} features in the area ({url.rsplit("/services/", 1)[-1]})')
    if not ids:
        return gpd.GeoDataFrame(geometry=[], crs='EPSG:4326')
    ids.sort()
    chunks = [ids[i:i + page] for i in range(0, len(ids), page)]

    def fetch(chunk):
        p = {'objectIds': ','.join(map(str, chunk)), 'outFields': out_fields, 'outSR': 4326,
             'returnGeometry': 'true', 'f': 'geojson' if use_geojson else 'json'}
        d = http.arcgis_json(url + '/query', p, method='post')
        if use_geojson:
            return d.get('features', [])
        return [{'type': 'Feature', 'properties': f.get('attributes', {}),
                 'geometry': _esri_to_geojson(f.get('geometry'))} for f in d.get('features', [])]

    feats = []
    with ThreadPoolExecutor(workers) as pool:
        for i, part in enumerate(pool.map(fetch, chunks)):
            feats += part
            if len(chunks) > 5 and (i + 1) % max(1, len(chunks) // 5) == 0:
                log(f'  {len(feats):,} / {len(ids):,}')
    gdf = gpd.GeoDataFrame.from_features(feats, crs='EPSG:4326')
    if oid_field in gdf.columns:
        gdf = gdf.drop_duplicates(oid_field)
    return gdf


@handler('arcgis-features')
def features(ctx):
    """Feature service or map service layer. Spec: url (layer URL), optional where, out_fields."""
    s = ctx.spec
    gdf = query_features(s['url'], where=s.get('where', '1=1'), area=ctx.area,
                         out_fields=s.get('out_fields', '*'), log=ctx.log)
    return Fetched('vector', [(ctx.layer, gdf)], sources=[s['url']])


@handler('arcgis-image')
def image(ctx):
    """Image service exportImage, tiled. Spec: url, resolution (default cell size, m), pixel_type, nodata,
    resampling (nearest | bilinear), max_size (service limit, px)."""
    s = ctx.spec
    res = ctx.resolution or s.get('resolution', 30)
    code = epsg(ctx.out_crs)
    geom = ctx.area.geometry(ctx.out_crs)
    tile_px = min(int(s.get('max_size', 4000)), 4000)
    tiles = plan_tiles(ctx.area.bounds(ctx.out_crs, pad=2 * res), res, tile_px, keep=geom)
    interp = 'RSP_NearestNeighbor' if s.get('resampling') == 'nearest' else 'RSP_BilinearInterpolation'
    nodata = s.get('nodata', -9999)

    def one(i, t):
        minx, miny, maxx, maxy, w, h = t
        p = {'bbox': f'{minx},{miny},{maxx},{maxy}', 'bboxSR': code, 'imageSR': code, 'size': f'{w},{h}',
             'format': 'tiff', 'pixelType': s.get('pixel_type', 'F32'), 'noData': nodata,
             'interpolation': interp, 'f': 'image'}
        if s.get('rendering_rule'):
            p['renderingRule'] = json.dumps(s['rendering_rule'])
        raw = ctx.workdir / f'raw_{i}.tif'
        r = http.get(s['url'].rstrip('/') + '/exportImage', params=p)
        if not r.headers.get('content-type', '').startswith('image'):
            raise RuntimeError(f'exportImage returned {r.headers.get("content-type")}: {r.text[:300]}')
        raw.write_bytes(r.content)
        return georeference(raw, ctx.workdir / f'tile_{i}.tif', t[:4], ctx.out_crs, nodata)

    paths = fetch_tiles(tiles, one, ctx.log)
    vrt = mosaic(paths, ctx.workdir / 'mosaic.vrt')
    return Fetched('raster', [(ctx.layer, vrt)], sources=[s['url']],
                   resampling=s.get('resampling', 'bilinear'),
                   notes=[f'Requested at {res:g} m cells in {ctx.out_crs}'])
