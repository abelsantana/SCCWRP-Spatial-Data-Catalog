"""Point clouds published as Entwine Point Tiles (EPT): fetch only the octree nodes that overlap the area.

EPT is a folder of LAZ files, one per octree node (ept-data/D-X-Y-Z.laz), with a JSON index of point counts
(ept-hierarchy/). This module walks the index itself and downloads the overlapping nodes with the shared HTTP
session; PDAL then merges, crops, reprojects and writes them locally. (PDAL's own remote reader in Esri's build
times out on HTTPS, and doing the walk here also gives an exact point count before anything is downloaded.)
"""
import json
import math
from concurrent.futures import ThreadPoolExecutor

from shapely.geometry import box

from .. import http
from ..entries import CatalogError
from .base import Fetched, handler

DEFAULT_MAX_POINTS = 300_000_000   # about 8 GB of LAZ; larger requests must thin (resolution) or shrink the area


def _node_box(cube, d, x, y):
    size = (cube[3] - cube[0]) / 2 ** d
    return box(cube[0] + x * size, cube[1] + y * size, cube[0] + (x + 1) * size, cube[1] + (y + 1) * size)


def _nodes(base, meta, shape, max_depth=None, cap=None):
    """[(key, count)] of nodes whose footprint touches shape, reading hierarchy sub-files as needed.

    Sub-files are fetched in parallel batches, and the walk stops as soon as the running point count passes cap,
    so an oversized request (a whole county of lidar) is refused in seconds rather than after reading the index."""
    cube = meta['bounds']
    out, total, seen = [], 0, set()
    pending = ['0-0-0-0']
    with ThreadPoolExecutor(16) as pool:
        while pending:
            files = list(pool.map(lambda k: http.get(f'{base}/ept-hierarchy/{k}.json').json(), pending))
            pending = []
            for h in files:
                for key, count in h.items():
                    if key in seen:          # a sub-file repeats its own root node
                        continue
                    d, x, y, _ = map(int, key.split('-'))
                    if max_depth is not None and d > max_depth:
                        continue
                    if not shape.intersects(_node_box(cube, d, x, y)):
                        continue
                    if count == -1:
                        pending.append(key)
                    elif count > 0:
                        seen.add(key)
                        out.append((key, count))
                        total += count
            if cap and total > cap:
                raise CatalogError(f'More than {cap / 1e6:,.0f} million points in this area (counted '
                                   f'{total / 1e6:,.0f} million before stopping). Choose a smaller area or thin '
                                   f'the cloud with a resolution in metres (e.g. 2).')
    return out


@handler('ept')
def ept(ctx):
    """Spec: url (ept.json), max_points. resolution (m) stops at the octree depth with about that point spacing."""
    import pdal
    s = ctx.spec
    base = s['url'].rsplit('/', 1)[0]
    meta = http.get(s['url']).json()
    if meta.get('dataType') != 'laszip':
        raise CatalogError(f'EPT data type {meta.get("dataType")} not supported (laszip only)')
    srs = f'EPSG:{meta["srs"]["horizontal"]}'
    shape = ctx.area.geometry(srs)
    max_depth = None
    if ctx.resolution:           # spacing at depth d = cube width / span / 2**d
        width = meta['bounds'][3] - meta['bounds'][0]
        max_depth = max(0, math.ceil(math.log2(width / meta['span'] / ctx.resolution)))
    nodes = _nodes(base, meta, shape, max_depth, cap=s.get('max_points', DEFAULT_MAX_POINTS))
    total = sum(c for _, c in nodes)
    ctx.log(f'  {len(nodes):,} octree nodes, at most {total / 1e6:,.1f} million points before cropping '
            f'(whole cloud {meta["points"] / 1e9:,.1f} billion)')
    if not nodes:
        raise CatalogError('The area does not overlap this point cloud.')
    folder = ctx.workdir / 'nodes'
    folder.mkdir()

    def fetch(node):
        dest = folder / f'{node[0]}.laz'
        http.download(f'{base}/ept-data/{node[0]}.laz', dest)
        return dest

    with ThreadPoolExecutor(8) as pool:
        files = list(pool.map(fetch, nodes))
    ctx.log(f'  downloaded {sum(f.stat().st_size for f in files) / 2**20:,.0f} MB')
    stages = [{'type': 'readers.las', 'filename': str(f), 'override_srs': srs} for f in files]
    stages.append({'type': 'filters.merge'})
    if ctx.method == 'bbox':
        minx, miny, maxx, maxy = shape.bounds
        stages.append({'type': 'filters.crop', 'bounds': f'([{minx}, {maxx}], [{miny}, {maxy}])'})
    else:
        stages.append({'type': 'filters.crop', 'polygon': shape.wkt})
    out = ctx.workdir / f'{ctx.layer}.laz'
    stages += [{'type': 'filters.reprojection', 'out_srs': ctx.out_crs},
               {'type': 'writers.las', 'filename': str(out), 'a_srs': ctx.out_crs, 'forward': 'all',
                'minor_version': 4, 'compression': True}]
    n = pdal.Pipeline(json.dumps(stages)).execute()
    ctx.log(f'  {n:,} points written')
    if n == 0:
        raise CatalogError('No points in this area.')
    return Fetched('pointcloud', [(ctx.layer, out)], sources=[s['url']], final=True,
                   notes=[f'{n:,} points'] + ([f'thinned to about one point per {ctx.resolution:g} m']
                                              if ctx.resolution else []))
