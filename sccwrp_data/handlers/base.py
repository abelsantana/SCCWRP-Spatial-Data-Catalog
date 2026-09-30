"""What every handler receives and returns.

A handler asks the provider for as little as it can (the area's bounding box or shape, one date, one year) and
returns what it got. Clipping to the exact area, reprojecting and writing the output format happen afterwards in
clip.py, the same way for every handler. Point clouds are the exception: PDAL crops and writes in one pass.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..entries import CatalogError

HANDLERS = {}


def handler(name):
    def register(fn):
        HANDLERS[name] = fn
        return fn
    return register


@dataclass
class Context:
    dataset_id: str
    layer: str
    spec: dict                  # the layer's fetch block from the catalog
    area: Any                   # areas.Area
    out_crs: str
    method: str                 # clip | intersects | bbox
    resolution: float | None    # output cell size in out_crs units (rasters, point cloud thinning)
    params: dict                # resolved layer parameters (year, date, metrics, ...)
    workdir: Path               # scratch folder for this request, deleted afterwards
    log: Callable = print


@dataclass
class Fetched:
    kind: str                   # vector | raster | pointcloud
    items: list                 # [(name, GeoDataFrame | Path)]; several items become several outputs
    sources: list = field(default_factory=list)   # URLs or paths actually read, for the manifest
    notes: list = field(default_factory=list)
    resampling: str = 'bilinear'  # rasters: 'nearest' for categories (land cover, classes)
    extra_files: list = field(default_factory=list)  # [(name, Path)] copied beside the output (e.g. a CSV)
    final: bool = False         # True when the handler already wrote the finished output (point clouds)
    method: str | None = None   # overrides the user's clip method (e.g. keep whole catchments for StreamCat)


def resolve_params(spec, given):
    """Check user parameters against the layer's declared 'params' and fill defaults."""
    declared = spec.get('params', {})
    given = dict(given or {})
    unknown = set(given) - set(declared)
    if unknown:
        raise CatalogError(f'Unknown parameter(s) {sorted(unknown)}. This layer takes: '
                           f'{", ".join(declared) or "no parameters"}')
    out = {}
    for name, d in declared.items():
        v = given.get(name, d.get('default'))
        if v is None:
            if d.get('required'):
                raise CatalogError(f'Parameter "{name}" is required: {d.get("description", "")}')
            continue
        t = d.get('type', 'str')
        if t == 'int':
            v = int(v)
            if ('min' in d and v < d['min']) or ('max' in d and v > d['max']):
                raise CatalogError(f'{name}={v} is outside {d.get("min")}..{d.get("max")}')
        elif t == 'choice':
            v = next((c for c in d['choices'] if str(c) == str(v)), v)   # "2019" from a form matches 2019
            if v not in d['choices']:
                raise CatalogError(f'{name}="{v}" is not one of {d["choices"]}')
        elif t == 'list':
            v = [s.strip() for s in v.split(',')] if isinstance(v, str) else list(v)
        elif t == 'dates':
            v = parse_dates(v, d)
        out[name] = v
    return out


def parse_dates(v, d):
    """'2013-01-15', '2013-01-01/2013-01-07' (inclusive) or a list; limited by the declared min/max and max_count."""
    import datetime as dt
    if isinstance(v, (list, tuple)):
        days = [dt.date.fromisoformat(str(x)) for x in v]
    elif '/' in str(v):
        a, b = (dt.date.fromisoformat(x) for x in str(v).split('/'))
        days = [a + dt.timedelta(n) for n in range((b - a).days + 1)]
    else:
        days = [dt.date.fromisoformat(str(v))]
    lo = dt.date.fromisoformat(d['min']) if 'min' in d else None
    hi = dt.date.fromisoformat(d['max']) if 'max' in d else dt.date.today() - dt.timedelta(1)
    bad = [x for x in days if (lo and x < lo) or x > hi]
    if bad:
        raise CatalogError(f'Dates outside {lo}..{hi}: {bad[:3]}')
    cap = d.get('max_count', 366)
    if len(days) > cap:
        raise CatalogError(f'{len(days)} dates requested; at most {cap} per request')
    return days
