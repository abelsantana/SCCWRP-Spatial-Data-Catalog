"""Run the Pro toolbox's tools through arcpy, the way ArcGIS Pro calls them (validation and execute).

    "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" tests/toolbox_test.py
"""
import sys
import tempfile
from pathlib import Path

import arcpy

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from sccwrp_data import entries  # noqa: E402

arcpy.ImportToolbox(str(REPO / 'toolbox' / 'SCCWRP Data.pyt'))


def item(ds):
    return f'{ds}: {entries.info(ds)["title"]}'


def layer_item(ds, layer):
    return f'{layer}: {entries.layer_spec(ds, layer)[1].get("title", layer)}'


out = Path(tempfile.mkdtemp(prefix='sccwrp_toolbox_'))
cases = [
    ('vector, county', dict(dataset=item('ca-integrated-report'), layer=layer_item('ca-integrated-report', 'polygons'),
                            area_type='County', area_values='Orange County', out_format='gpkg')),
    ('raster, year parameter, HUC', dict(dataset=item('nlcd-annual'), layer=layer_item('nlcd-annual', 'land-cover'),
                                         area_type='HUC 10 watershed', area_values='1807010501',
                                         params='year 2001', out_format='tif', expect=['_2001.tif'])),
    ('regional board, gdb output', dict(dataset=item('epa-attains'), layer=layer_item('epa-attains', 'areas'),
                                        area_type='Regional Water Quality Control Board region',
                                        area_values="'8 Santa Ana'", out_format='gdb', expect=['areas', '.gdb'])),
]
failed = 0
for name, kw in cases:
    folder = out / name.split(',')[0].replace(' ', '_')
    folder.mkdir()
    try:
        r = arcpy.sccwrpdata.GetData(kw['dataset'], kw['layer'], kw['area_type'], kw.get('area_values'), None, 0,
                                     'Clip to the area', kw.get('params'), arcpy.SpatialReference(3310), None,
                                     str(folder), kw['out_format'], False, False)
        got = r.getOutput(0)
        problems = [f'expected "{w}" in the output name' for w in kw.get('expect', []) if w not in got]
        if problems:
            raise RuntimeError('; '.join(problems) + f' (got {got})')
        print(f'PASS {name}: {got}')
    except Exception as e:
        failed += 1
        print(f'FAIL {name}: {e}\n{arcpy.GetMessages()}')
print(f'outputs in {out}')
sys.exit(1 if failed else 0)
