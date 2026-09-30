"""SCCWRP data tools: get catalog datasets clipped to an area, straight from the providers.

    import sccwrp_data as sd
    sd.catalog(fetchable_only=True)       # datasets the tools can get
    sd.info('nlcd-annual')                # one catalog entry
    sd.areas()                            # area types
    r = sd.get('nlcd-annual', 'county:Orange', params={'year': 2019}, out_dir='C:/work')
    r.files, r.manifest

Runs in ArcGIS Pro's Python (arcgispro-py3 or a clone), which has GDAL, geopandas, rasterio, PDAL and xarray.
"""
__version__ = '0.1.0'

from .clipareas import areas, choices  # noqa: E402,F401
from .entries import CatalogError, catalog, info, layers  # noqa: E402,F401
from .request import get  # noqa: E402,F401
