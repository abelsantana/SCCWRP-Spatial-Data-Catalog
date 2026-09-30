"""Clip to the area, reproject and write the output format: the same last step for every handler."""
from pathlib import Path

import geopandas as gpd
import shapely
from osgeo import gdal
from shapely.geometry import MultiLineString, MultiPoint, MultiPolygon, box

from .entries import CatalogError

gdal.UseExceptions()

VECTOR_FORMATS = {'gpkg': ('GPKG', '.gpkg'), 'gdb': ('OpenFileGDB', '.gdb'), 'shp': ('ESRI Shapefile', '.shp'),
                  'geojson': ('GeoJSON', '.geojson'), 'parquet': (None, '.parquet')}
RASTER_FORMATS = {'tif': ('COG', '.tif'), 'gtiff': ('GTiff', '.tif')}
POINTCLOUD_FORMATS = {'laz': (None, '.laz')}
DEFAULT_FORMAT = {'vector': 'gpkg', 'raster': 'tif', 'pointcloud': 'laz'}


def formats(kind):
    return list({'vector': VECTOR_FORMATS, 'raster': RASTER_FORMATS, 'pointcloud': POINTCLOUD_FORMATS}[kind])


def extension(kind, fmt):
    table = {'vector': VECTOR_FORMATS, 'raster': RASTER_FORMATS, 'pointcloud': POINTCLOUD_FORMATS}[kind]
    if fmt not in table:
        raise CatalogError(f'{kind} data can be written as {", ".join(table)}, not {fmt}')
    return table[fmt][1]


MULTI = {'Polygon': MultiPolygon, 'LineString': MultiLineString, 'Point': MultiPoint}
FAMILY = {'Polygon': 'Polygon', 'MultiPolygon': 'Polygon', 'LineString': 'LineString',
          'MultiLineString': 'LineString', 'Point': 'Point', 'MultiPoint': 'Point'}


def _one_geometry_type(gdf):
    """Make every feature the multi-part form of the layer's main geometry type.

    Clipping turns some polygons into multipolygons (and repairs can leave geometry collections); file geodatabases
    and many tools need one geometry type per layer."""
    if gdf.empty:
        return gdf
    families = gdf.geom_type.map(FAMILY)
    family = families.mode().iloc[0] if families.notna().any() else None
    if family is None:
        return gdf

    def fix(g):
        parts = [g] if g.geom_type != 'GeometryCollection' else list(g.geoms)
        keep = []
        for p in parts:
            if FAMILY.get(p.geom_type) == family:
                keep += list(p.geoms) if p.geom_type.startswith('Multi') else [p]
        return MULTI[family](keep) if keep else None

    gdf = gdf.copy()
    gdf['geometry'] = gdf.geometry.map(fix)
    return gdf[gdf.geometry.notna()]


def fast_clip(gdf, shape):
    """Clip features to shape, doing geometry work only where it is needed.

    Features wholly inside the area (most of them) are kept as they are. Only features that cross the boundary are
    cut, and each is cut against the piece of the boundary near it rather than the whole (often very detailed)
    outline. Same result as geopandas.clip, typically 10-50x faster for counties and states."""
    if gdf.empty:
        return gdf
    shapely.prepare(shape)
    hit = gdf.sindex.query(shape, predicate='intersects')
    inside = set(gdf.sindex.query(shape, predicate='contains_properly').tolist()) if len(hit) else set()
    edge = [i for i in hit if i not in inside]
    geoms = gdf.geometry.values
    cut = []
    for i in edge:
        g = geoms[i]
        minx, miny, maxx, maxy = g.bounds
        pad = max(maxx - minx, maxy - miny) * 0.01 + 1
        local = shapely.clip_by_rect(shape, minx - pad, miny - pad, maxx + pad, maxy + pad)
        cut.append(shapely.intersection(g, local))
    out = gdf.iloc[sorted(inside) + edge].copy()
    if edge:
        out.iloc[len(inside):, out.columns.get_loc(out.geometry.name)] = cut
    return out[~out.geometry.is_empty]


def vector(gdf, area, method, out_crs, fmt, out_path, layer_name):
    """Clip / select features and write them. Returns stats for the manifest."""
    if gdf.crs is None:
        raise CatalogError('Source data has no coordinate system')
    gdf = gdf.to_crs(out_crs)
    bad = ~gdf.is_valid
    if bad.any():
        gdf.loc[bad, 'geometry'] = gdf.loc[bad].make_valid()
    shape = area.geometry(out_crs)
    if method == 'intersects':
        gdf = gdf.iloc[gdf.sindex.query(shape, predicate='intersects')]
    else:                                   # clip, and bbox (the area is already its bounding box)
        gdf = fast_clip(gdf, shape)
    gdf = _one_geometry_type(gdf[~gdf.geometry.is_empty & gdf.geometry.notna()])
    driver, _ = VECTOR_FORMATS[fmt]
    if fmt == 'parquet':
        gdf.to_parquet(out_path)
    elif fmt == 'gdb':
        gdf.to_file(out_path, layer=layer_name.replace('-', '_'), driver=driver)
    elif fmt == 'shp':
        gdf.to_file(out_path, driver=driver, encoding='utf-8')
    else:
        gdf.to_file(out_path, layer=layer_name, driver=driver)
    return {'features': len(gdf), 'geometry_types': sorted(gdf.geom_type.unique().tolist()),
            'bounds': [round(v, 3) for v in gdf.total_bounds.tolist()] if len(gdf) else None}


AUTO_MAX_CELLS = 40e6        # above this, and with no cell size given, results come back coarser (see raster())
NICE_CELL_SIZES = [1, 2, 3, 5, 10, 15, 20, 30, 50, 100, 200, 250, 500, 1000, 2000, 5000, 10000]


def _footprint(ds, crs):
    """The raster's extent as a polygon in crs (edges densified so the shape survives reprojection)."""
    gt = ds.GetGeoTransform()
    x0, y0 = gt[0], gt[3]
    x1, y1 = x0 + gt[1] * ds.RasterXSize + gt[2] * ds.RasterYSize, y0 + gt[4] * ds.RasterXSize + gt[5] * ds.RasterYSize
    src_crs = ds.GetSpatialRef().ExportToWkt()
    g = gpd.GeoSeries([box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))], crs=src_crs)
    step = max(abs(x1 - x0), abs(y1 - y0)) / 50
    return g.segmentize(step).to_crs(crs).iloc[0]


def raster(src, area, method, out_crs, resolution, resampling, fmt, out_path, workdir):
    """Warp to out_crs, masked to the area (or cut to its bounding box). Returns stats for the manifest."""
    ds = gdal.Open(str(src))
    band = ds.GetRasterBand(1)
    nodata = band.GetNoDataValue()
    dtype = gdal.GetDataTypeName(band.DataType)
    if nodata is None:
        nodata = 0 if dtype == 'Byte' and ds.RasterCount >= 3 else (255 if dtype == 'Byte' else -9999)
    opts = dict(dstSRS=out_crs, resampleAlg=resampling, dstNodata=nodata, multithread=True,
                warpOptions=['NUM_THREADS=ALL_CPUS'], warpMemoryLimit=1024)
    # Cut to where the area and the data overlap; a small survey inside a large area must not become a huge
    # mostly-empty grid
    foot = _footprint(ds, out_crs)
    shape = area.geometry(out_crs).intersection(foot)
    if shape.is_empty:
        raise CatalogError('The area does not overlap this dataset.')
    auto = None
    if not resolution:
        # Very large results come back at a coarser cell size (read from the file's overviews) so they stay quick;
        # an explicit cell size is always honoured
        native = (foot.area / (ds.RasterXSize * ds.RasterYSize)) ** 0.5
        minx, miny, maxx, maxy = shape.bounds
        cells = (maxx - minx) * (maxy - miny) / native ** 2
        if cells > AUTO_MAX_CELLS:
            want = native * (cells / AUTO_MAX_CELLS) ** 0.5
            resolution = auto = next(n for n in NICE_CELL_SIZES if n >= want)
    if resolution:
        opts.update(xRes=resolution, yRes=resolution, targetAlignedPixels=True)
    if method == 'bbox':
        opts['outputBounds'] = shape.bounds
    else:
        cut = Path(workdir) / 'cutline.gpkg'
        gpd.GeoDataFrame(geometry=[shape], crs=out_crs).to_file(cut, driver='GPKG')
        opts.update(cutlineDSName=str(cut), cropToCutline=True)
    driver, _ = RASTER_FORMATS[fmt]
    predictor = '3' if 'Float' in dtype else '2'
    co = ['COMPRESS=DEFLATE', f'PREDICTOR={predictor}', 'BIGTIFF=IF_SAFER']
    if driver == 'GTiff':
        co.append('TILED=YES')
    gdal.Warp(str(out_path), ds, format=driver, creationOptions=co, **opts)
    ds = None
    out = gdal.Open(str(out_path))
    b = out.GetRasterBand(1)
    try:
        mn, mx, mean, _ = b.ComputeStatistics(False)
        stats = {'min': round(mn, 4), 'max': round(mx, 4), 'mean': round(mean, 4)}
    except RuntimeError:        # every cell is no-data
        stats = None
    gt = out.GetGeoTransform()
    info = {'width': out.RasterXSize, 'height': out.RasterYSize, 'bands': out.RasterCount, 'data_type': dtype,
            'cell_size': round(abs(gt[1]), 4), 'nodata': nodata, 'statistics': stats}
    if auto:
        info['auto_resolution'] = auto
    out = None
    if stats is None:
        raise CatalogError('The result contains no data for this area (only no-data cells).')
    return info
