"""SCCWRP data tools: get catalog datasets clipped to an area, straight from the providers.

    import sccwrp_data as sd
    sd.catalog(fetchable_only=True)       # datasets the tools can get
    sd.info('nlcd-annual')                # one catalog entry
    sd.areas()                            # area types
    r = sd.get('nlcd-annual', 'county:Orange', params={'year': 2019}, out_dir='C:/work')
    r.files, r.manifest

Runs in ArcGIS Pro's Python (arcgispro-py3 or a clone), which has GDAL, geopandas, rasterio, PDAL and xarray.
"""
__version__ = '0.2.0'

import importlib.util  # noqa: E402

from .entries import CatalogError, NoFeatures, catalog, info, layers  # noqa: E402,F401

# Packages the engine needs beyond Pro's default environment, and one GeoPandas file engine (either will do)
NEEDED = ['geopandas', 'shapely', 'pyproj', 'osgeo', 'requests']
FILE_ENGINES = ['pyogrio', 'fiona']


def missing_packages():
    """Names of required packages this Python lacks (empty when everything is there)."""
    missing = [m for m in NEEDED if importlib.util.find_spec(m) is None]
    if not any(importlib.util.find_spec(m) for m in FILE_ENGINES):
        missing.append('pyogrio (or fiona)')
    return missing


def __getattr__(name):
    """The GIS parts load on first use, so reading the catalog (and opening the Pro toolbox) needs only the standard
    library, and a missing package is reported with a clear message instead of an import error."""
    if name in ('areas', 'choices'):
        from . import clipareas
        return getattr(clipareas, name)
    if name == 'get':
        from .request import get
        return get
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
