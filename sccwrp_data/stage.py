"""Fast tier: California copies of heavily used layers, prepared ahead of time.

A staged copy covers the California-by-watersheds area, is stored in the default output CRS (UTM 11N) and is
spatially indexed. All vector layers live in one GeoPackage, sccwrp_fast.gpkg (one layer per dataset/layer, each
with an R-tree index); rasters are tiled COGs with overviews beside it (GeoPackage rasters are image tiles and
handle continuous values poorly). Per-layer metadata goes in meta/<dataset>__<layer>.json. get() reads a staged copy
whenever one exists and the requested area lies inside it, so a county comes back without contacting any provider.

A layer is staged from the source named in its "stage" block:
  "stage": {"source": "local", "holding": "<holdings key>", "layer_name": "<layer in a geodatabase>"}
      SCCWRP's own intact copy (the version our analyses already use), read from the server with GDAL
  "stage": {"source": "provider"}   (or no "source")
      the layer's own handler, run once for the whole area

    python -m sccwrp_data stage nhdplus-v21            all layers of a dataset
    python -m sccwrp_data stage nhdplus-v21 --layer flowlines
    python -m sccwrp_data staged                       what is staged, when, how big
"""
import datetime as dt
import json
import shutil
import tempfile
import time
from pathlib import Path

import geopandas as gpd
import shapely
from osgeo import gdal

from . import config, holdings, vectorio
from .clipareas import resolve as resolve_area
from .entries import CatalogError, info, layer_spec, layers
from .handlers import HANDLERS, Context, Fetched, handler, resolve_params

gdal.UseExceptions()
STAGE_BUFFER_KM = 5          # so areas that just touch the California edge still fall inside the copy
EXTENTS = {                  # "extent" in a layer's stage block
    'land': 'ca-watersheds',                 # California plus every watershed crossing into it (default)
    'ocean': 'ca-ocean',                     # California's ocean to about 200 nautical miles
    'coast': 'ca-watersheds|ca-ocean',       # both, for data that spans the shoreline (coastal relief models)
}


def _stage_area(extent):
    from .clipareas import Area, WORK_CRS
    parts = [resolve_area(k, STAGE_BUFFER_KM) for k in EXTENTS[extent].split('|')]
    if len(parts) == 1:
        return parts[0]
    g = shapely.union_all([p.union for p in parts])
    return Area(extent, 'California land and ocean', gpd.GeoDataFrame({'name': ['California land and ocean']},
                                                                      geometry=[g], crs=WORK_CRS))


FAST_GPKG = 'sccwrp_fast.gpkg'


def _name(dataset_id, layer, params=None):
    """Layer / file name for a staged copy, e.g. nhdplus_v21__flowlines or nlcd_annual__land_cover__year_2019."""
    tag = ''.join(f'__{k}_{v}' for k, v in sorted((params or {}).items()))
    return f'{dataset_id}__{layer}{tag}'.replace('-', '_')


def _meta_path(name):
    return config.fast_dir() / 'meta' / f'{name}.json'


def lookup(dataset_id, layer, params, area):
    """Staged copy covering the area: (path, info) or None. For vectors, info['gpkg_layer'] names the layer."""
    meta = _meta_path(_name(dataset_id, layer, params))
    if not meta.exists():
        return None
    m = json.loads(meta.read_text(encoding='utf-8'))
    path = config.fast_dir() / m['file']
    if not path.exists():
        return None
    extent = gpd.GeoSeries.from_wkt([m['extent_wkt']], crs=config.EQUAL_AREA).iloc[0]
    return (path, m) if extent.contains(area.geometry(config.EQUAL_AREA)) else None


@handler('staged')
def staged(ctx):
    """Read a staged copy for the area (spatial index; only the pages covering the area are read)."""
    path = Path(ctx.spec['staged_path'])
    if path.suffix == '.gpkg':
        mask = gpd.GeoSeries([ctx.area.geometry()], crs=ctx.area.gdf.crs)
        t = time.time()
        gdf = vectorio.read(path, layer=ctx.spec['staged_layer'], mask=mask)
        ctx.log(f'  {len(gdf):,} features from the fast copy in {time.time() - t:.1f} s')
        return Fetched('vector', [(ctx.layer, gdf)], sources=[ctx.spec['staged_from']])
    return Fetched('raster', [(ctx.layer, path)], sources=[ctx.spec['staged_from']],
                   resampling=ctx.spec.get('resampling', 'bilinear'))


def stage(dataset_id, layer=None, params=None, log=print):
    """Build the staged copy of one layer (or every layer that has a "stage" block)."""
    if layer is None:
        all_layers, _ = layers(dataset_id)
        names = [n for n, s in all_layers.items() if 'stage' in s]
        if not names:
            raise CatalogError(f'{dataset_id} has no layers marked for staging ("stage" block)')
        return [stage(dataset_id, n, params, log) for n in names]
    layer, spec = layer_spec(dataset_id, layer)
    st = spec.get('stage')
    if st is None:
        raise CatalogError(f'{dataset_id}/{layer} is not marked for staging (no "stage" block)')
    params = resolve_params(spec, params)
    extent = st.get('extent', 'land')
    if extent not in EXTENTS:
        raise CatalogError(f'stage.extent must be one of {", ".join(EXTENTS)}')
    area = _stage_area(extent)
    name = _name(dataset_id, layer, params)
    meta = _meta_path(name)
    meta.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    log(f'staging {dataset_id}/{layer} for {area.label}')
    work = Path(tempfile.mkdtemp(prefix='stage_', dir=config.fast_dir()))
    try:
        if st.get('source') == 'local':
            src = holdings.layer_path(st['holding'])
            kind = spec.get('kind') or st.get('kind', 'vector')
            source = f'SCCWRP copy: {st["holding"]}' + (f' / {st["layer_name"]}' if st.get('layer_name') else '')
            got = Fetched(kind, [(layer, src)], sources=[source], resampling=spec.get('resampling', 'bilinear'))
        else:
            h = HANDLERS[spec['handler']]
            ctx = Context(dataset_id, layer, spec, area, config.DEFAULT_CRS, 'intersects', None, params, work, log)
            got = h(ctx)
            source = ', '.join(got.sources)
        _, item = got.items[0]
        if got.kind == 'vector':
            out, stats = _stage_vector(item, st.get('layer_name'), area, name, log)
        elif got.kind == 'raster':
            out, stats = _stage_raster(item, area, config.fast_dir() / f'{name}.tif', got.resampling, work, log)
        else:
            raise CatalogError('Only vector and raster layers can be staged')
        m = {'dataset': dataset_id, 'layer': layer, 'params': {k: str(v) for k, v in params.items()},
             'file': out.name, 'gpkg_layer': name if got.kind == 'vector' else None, 'source': source, 'version': spec.get('version') or info(dataset_id)['versions']['latest'],
             'built': dt.datetime.now().isoformat(timespec='seconds'), 'crs': config.DEFAULT_CRS,
             'extent': extent, 'extent_wkt': _coverage(extent, area).simplify(100).wkt, 'seconds': round(time.time() - t0),
             **stats}
        meta.write_text(json.dumps(m, indent=1), encoding='utf-8')
        log(f'  staged {name} in {out.name} in {m["seconds"]} s')
        return m
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _coverage(extent, area):
    """Where a staged copy is complete: all of California's land and ocean, whatever the staging extent.

    A copy staged for the ocean has nothing to miss on land (features touching the ocean are kept whole), and a copy
    staged for land has nothing to miss offshore, so either answers any California area. Areas reaching beyond
    California (other states, Mexico, the open Pacific) still go to the provider."""
    return _stage_area('coast').geometry(config.EQUAL_AREA)


def refresh_coverage():
    """Rewrite the coverage of existing staged copies (after a change to how coverage is defined)."""
    for meta in (config.fast_dir() / 'meta').glob('*.json'):
        m = json.loads(meta.read_text(encoding='utf-8'))
        ext = m.get('extent', 'land')
        m['extent_wkt'] = _coverage(ext, _stage_area(ext)).simplify(100).wkt
        meta.write_text(json.dumps(m, indent=1), encoding='utf-8')


def _stage_vector(item, layer_name, area, name, log):
    out = config.fast_dir() / FAST_GPKG
    shape = area.geometry(config.EQUAL_AREA)
    if isinstance(item, (str, Path)):
        # Read only the California part of the source through its spatial index, then keep whole features
        # that touch the area (clipping happens per request, later)
        mask = gpd.GeoSeries([shape], crs=config.EQUAL_AREA)
        t = time.time()
        gdf = vectorio.read(item, layer=layer_name, mask=mask)
        log(f'  read {len(gdf):,} features from the source in {time.time() - t:.0f} s')
    else:
        gdf = item
    gdf = gdf.to_crs(config.DEFAULT_CRS)
    if gdf.geometry.has_z.any():
        gdf.geometry = gdf.geometry.force_2d()
    gdf = gdf[gdf.intersects(gpd.GeoSeries([shape], crs=config.EQUAL_AREA).to_crs(config.DEFAULT_CRS).iloc[0])]
    if out.exists() and name in vectorio.layer_names(out):
        ds = gdal.OpenEx(str(out), gdal.OF_VECTOR | gdal.OF_UPDATE)
        ds.DeleteLayer(name)
        ds = None
    t = time.time()
    gdf.to_file(out, layer=name, driver='GPKG', SPATIAL_INDEX='YES')
    log(f'  wrote {len(gdf):,} features to {out.name} in {time.time() - t:.0f} s')
    return out, {'features': len(gdf), 'geometry_types': sorted(gdf.geom_type.unique().tolist())}


def _stage_raster(src, area, out, resampling, work, log):
    tmp = work / out.name
    try:
        gdal.Open(str(src))
    except RuntimeError:
        if not Path(src).is_dir():
            raise
        from .handlers.files import _legacy_raster_via_arcpy
        log('  ESRI grid: copying to a local folder and converting with ArcGIS')
        src = _legacy_raster_via_arcpy(Path(src), type('C', (), {'workdir': work})())
    cut = work / 'cut.gpkg'
    from .clip import _footprint
    # Only where the source has data: a Bight grid staged for 'the ocean' must not become a statewide empty grid
    shape = area.geometry(config.DEFAULT_CRS).intersection(_footprint(gdal.Open(str(src)), config.DEFAULT_CRS))
    gpd.GeoDataFrame(geometry=[shape], crs=config.DEFAULT_CRS).to_file(cut)
    gdal.Warp(str(tmp), str(src), format='COG', dstSRS=config.DEFAULT_CRS, cutlineDSName=str(cut),
              cropToCutline=True, resampleAlg=resampling, multithread=True, warpOptions=['NUM_THREADS=ALL_CPUS'],
              creationOptions=['COMPRESS=DEFLATE', 'BIGTIFF=YES', 'NUM_THREADS=ALL_CPUS',
                               f'OVERVIEW_RESAMPLING={"NEAREST" if resampling == "nearest" else "AVERAGE"}'])
    shutil.move(str(tmp), out)
    d = gdal.Open(str(out))
    return out, {'width': d.RasterXSize, 'height': d.RasterYSize, 'cell_size': abs(d.GetGeoTransform()[1])}


def staged_list():
    out = []
    for meta in sorted((config.fast_dir() / 'meta').glob('*.json')):
        out.append(json.loads(meta.read_text(encoding='utf-8')))
    return out
