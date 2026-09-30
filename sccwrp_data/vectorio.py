"""Vector file reading that works with either GeoPandas I/O engine: pyogrio (faster, reads through Arrow) or fiona.

ArcGIS Pro's environments differ: a clone with geopandas added from Pro's package manager may have only fiona.
"""
import geopandas as gpd
from osgeo import gdal

try:
    import pyogrio  # noqa: F401
    ARROW = True
except ImportError:
    ARROW = False


def read(path, **kw):
    """gpd.read_file, through Arrow when pyogrio is available."""
    if ARROW:
        kw.setdefault('use_arrow', True)
    return gpd.read_file(path, **kw)


def layer_names(path):
    """Layer names in a GeoPackage or other multi-layer file."""
    ds = gdal.OpenEx(str(path), gdal.OF_VECTOR)
    return [ds.GetLayerByIndex(i).GetName() for i in range(ds.GetLayerCount())] if ds else []
