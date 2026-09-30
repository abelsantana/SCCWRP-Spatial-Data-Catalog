"""Helpers shared by the raster handlers: request grids, tiled downloads, georeferencing."""
import math
from concurrent.futures import ThreadPoolExecutor

from osgeo import gdal
from shapely.geometry import box

from ..entries import CatalogError

gdal.UseExceptions()
MAX_CELLS = 1.5e9      # refuse requests larger than this (about 6 GB as 32-bit floats) and suggest a coarser cell size


def epsg(crs):
    """'EPSG:3310' -> 3310. Services take a numeric spatial reference."""
    s = str(crs).upper()
    if not s.startswith('EPSG:'):
        raise CatalogError(f'Use an EPSG code for the output coordinate system (got {crs})')
    return int(s.split(':')[1])


def plan_tiles(bounds, res, tile_px, keep=None, origin=(0, 0)):
    """Split bounds into a grid of tiles of at most tile_px cells a side, aligned to res.

    Returns [(minx, miny, maxx, maxy, width, height)]. keep: optional shapely geometry (same CRS);
    tiles that do not touch it are skipped, so a diagonal coastline does not fetch empty ocean.
    origin: any cell corner of the source grid, so tiles line up with its cells and the server does not resample.
    """
    minx, miny, maxx, maxy = bounds
    ox, oy = origin
    minx, miny = math.floor((minx - ox) / res) * res + ox, math.floor((miny - oy) / res) * res + oy
    maxx, maxy = math.ceil((maxx - ox) / res) * res + ox, math.ceil((maxy - oy) / res) * res + oy
    cols, rows = round((maxx - minx) / res), round((maxy - miny) / res)
    if cols * rows > MAX_CELLS:
        need = res * math.sqrt(cols * rows / MAX_CELLS)
        raise CatalogError(f'The area at {res:g} m is {cols:,} x {rows:,} cells, too large for one request. '
                           f'Use a cell size of at least {math.ceil(need):g} m or a smaller area.')
    tiles = []
    for r0 in range(0, rows, tile_px):
        for c0 in range(0, cols, tile_px):
            w, h = min(tile_px, cols - c0), min(tile_px, rows - r0)
            x0, y1 = minx + c0 * res, maxy - r0 * res
            t = (x0, y1 - h * res, x0 + w * res, y1, w, h)
            if keep is None or keep.intersects(box(*t[:4])):
                tiles.append(t)
    return tiles


def georeference(raw_path, out_path, bounds, crs, nodata=None):
    """Write raw_path as a GeoTIFF with the given bounds and CRS (service responses are not always georeferenced)."""
    minx, miny, maxx, maxy = bounds
    gdal.Translate(str(out_path), str(raw_path), outputBounds=[minx, maxy, maxx, miny], outputSRS=crs,
                   noData=nodata, creationOptions=['COMPRESS=DEFLATE', 'TILED=YES'])
    return out_path


def fetch_tiles(tiles, fetch_one, log, workers=4):
    """Run fetch_one(index, tile) -> Path | None in parallel; return the paths that came back."""
    log(f'  {len(tiles)} tile request(s)')
    done = []
    with ThreadPoolExecutor(workers) as pool:
        for i, p in enumerate(pool.map(lambda a: fetch_one(*a), enumerate(tiles))):
            if p is not None:
                done.append(p)
            if len(tiles) > 4 and (i + 1) % max(1, len(tiles) // 10) == 0:
                log(f'  {i + 1}/{len(tiles)} tiles')
    return done


def mosaic(paths, out_vrt):
    """One VRT over the tiles; the clip step reads it like a single raster."""
    if not paths:
        raise CatalogError('The provider returned no data for this area.')
    gdal.BuildVRT(str(out_vrt), [str(p) for p in paths])
    return out_vrt
