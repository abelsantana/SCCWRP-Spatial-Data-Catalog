# -*- coding: utf-8 -*-
"""ArcGIS Pro toolbox for the SCCWRP data catalog.

Get Data        get a catalog dataset for an area (county, watershed, region, a layer or a drawn shape),
                straight from the provider, clipped and in the chosen coordinate system
Add Live Layer  add a provider's live service to the map without downloading anything

Works in ArcGIS Pro's default Python environment (arcgispro-py3). The tools read the catalog in ../catalog and
the engine in ../sccwrp_data, so keep this file inside the repository.
"""
import sys
from pathlib import Path

import arcpy

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import sccwrp_data  # noqa: E402  (light: reads JSON only; the GIS parts load when a tool runs)
from sccwrp_data import entries  # noqa: E402

MISSING = sccwrp_data.missing_packages()
MISSING_MSG = (f"ArcGIS Pro's Python environment ({sys.prefix}) is missing {', '.join(MISSING)}. "
               'In Pro: Settings > Package Manager > Environment Manager, clone arcgispro-py3 and activate the clone, '
               'then add geopandas under Add Packages and restart Pro. See INSTALL.md.')

CUSTOM = 'Custom area (feature layer, drawn shape or file)'
METHODS = {'Clip to the area': 'clip', 'Keep whole features that touch the area': 'intersects',
           'Bounding box of the area': 'bbox'}
HUC_LEVELS = [2, 4, 6, 8, 10, 12]


def _area_types():
    """Label -> area id, in the order users see them."""
    import json
    reg = json.loads((REPO / 'catalog' / 'clip_areas.json').read_text(encoding='utf-8'))['areas']
    out = {}
    for a in reg:
        if a['id'] == 'huc':
            for lv in HUC_LEVELS:
                out[f'HUC {lv} watershed'] = f'huc{lv}'
        else:
            out[a['label']] = a['id']
        if a['id'] == 'county':
            out['County with its coastal waters'] = 'county-coastal'
    out[CUSTOM] = 'custom'
    return out


AREA_TYPES = _area_types()
PICK_MANY = {'county', 'county-coastal', 'regional-board', 'smc-watershed'} | {f'huc{lv}' for lv in HUC_LEVELS}
TYPED = {'point': 'Type longitude,latitude, e.g. -117.85,33.65, and set a buffer',
         'depth': 'Type shallow-deep in metres, e.g. 30-120'}   # one typed value instead of a pick list


def _dataset_items(kinds=None):
    items = []
    for e in entries.catalog(fetchable_only=True):
        if kinds and not any(s['handler'] in kinds for s in e['fetch']['layers'].values()):
            continue
        items.append(f'{e["id"]}: {e["title"]}')
    return items


def _id(item):
    return item.split(':', 1)[0].strip() if item else None


def _layer_items(dataset_id, kinds=None):
    lays, default = entries.layers(dataset_id)
    items = [f'{n}: {s.get("title", n)}' for n, s in lays.items() if not kinds or s['handler'] in kinds]
    first = next((i for i in items if _id(i) == default), items[0] if items else None)
    return items, first


def _reload():
    """Pick up edits to the engine without restarting Pro: drop its modules so the next import reads them afresh.

    Pro may run a tool with a different sys.path than it loaded the toolbox with, so the repo is put back first.
    """
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    for name in [m for m in sys.modules if m == 'sccwrp_data' or m.startswith('sccwrp_data.')]:
        del sys.modules[name]


class Toolbox:
    def __init__(self):
        self.label = 'SCCWRP Data'
        self.alias = 'sccwrpdata'
        self.tools = [GetData, AddLiveLayer]


class GetData:
    def __init__(self):
        self.label = 'Get Data'
        self.description = ('Get a catalog dataset for an area, straight from the provider, clipped and projected. '
                            'Results are cached, so asking again for the same area is instant.')
        self.canRunInBackground = True

    def getParameterInfo(self):
        P = arcpy.Parameter
        dataset = P(name='dataset', displayName='Dataset', datatype='GPString', parameterType='Required',
                    direction='Input')
        dataset.filter.type = 'ValueList'
        dataset.filter.list = _dataset_items()
        layer = P(name='layer', displayName='Layer', datatype='GPString', parameterType='Required', direction='Input')
        layer.filter.type = 'ValueList'
        area_type = P(name='area_type', displayName='Area', datatype='GPString', parameterType='Required',
                      direction='Input')
        area_type.filter.type = 'ValueList'
        area_type.filter.list = list(AREA_TYPES)
        area_type.value = 'County'
        area_values = P(name='area_values', displayName='Area selection', datatype='GPString',
                        parameterType='Optional', direction='Input', multiValue=True)
        custom = P(name='custom_area', displayName='Custom area', datatype='GPFeatureRecordSetLayer',
                   parameterType='Optional', direction='Input', enabled=False)
        buffer_km = P(name='buffer_km', displayName='Buffer (km)', datatype='GPDouble', parameterType='Optional',
                      direction='Input')
        buffer_km.value = 0
        method = P(name='method', displayName='Clip method', datatype='GPString', parameterType='Required',
                   direction='Input')
        method.filter.type = 'ValueList'
        method.filter.list = list(METHODS)
        method.value = 'Clip to the area'
        params = P(name='params', displayName='Dataset parameters', datatype='GPValueTable',
                   parameterType='Optional', direction='Input')
        params.columns = [['GPString', 'Parameter'], ['GPString', 'Value']]
        crs = P(name='out_crs', displayName='Output coordinate system', datatype='GPCoordinateSystem',
                parameterType='Required', direction='Input')
        crs.value = arcpy.SpatialReference(26911)
        cell = P(name='cell_size', displayName='Cell size / point spacing (output units, rasters and point clouds)',
                 datatype='GPDouble', parameterType='Optional', direction='Input')
        folder = P(name='out_folder', displayName='Output folder', datatype='DEFolder', parameterType='Required',
                   direction='Input')
        fmt = P(name='out_format', displayName='Output format', datatype='GPString', parameterType='Required',
                direction='Input')
        fmt.filter.type = 'ValueList'
        add = P(name='add_to_map', displayName='Add result to the current map', datatype='GPBoolean',
                parameterType='Optional', direction='Input')
        add.value = True
        refresh = P(name='refresh', displayName='Fetch again even if cached', datatype='GPBoolean',
                    parameterType='Optional', direction='Input')
        refresh.value = False
        out = P(name='out_files', displayName='Output files', datatype='GPString', parameterType='Derived',
                direction='Output', multiValue=True)
        return [dataset, layer, area_type, area_values, custom, buffer_km, method, params, crs, cell, folder, fmt,
                add, refresh, out]

    def isLicensed(self):
        return True

    def updateParameters(self, p):
        dataset, layer, area_type, area_values, custom, _, _, params, _, cell, _, fmt = p[:12]
        ds = _id(dataset.valueAsText)
        dataset_changed = ds and dataset.altered and not dataset.hasBeenValidated
        # Only replace a value when it does not fit the new dataset / layer: a run from history or a script sets
        # every parameter at once, and those values must survive the first validation.
        if dataset_changed:
            items, first = _layer_items(ds)
            layer.filter.list = items
            if layer.valueAsText not in items:
                layer.value = first
        lay = _id(layer.valueAsText)
        if ds and lay and (dataset_changed or (layer.altered and not layer.hasBeenValidated)):
            try:
                _, spec = entries.layer_spec(ds, lay)
            except entries.CatalogError:
                spec = None
            if spec:
                declared = spec.get('params', {})
                params.enabled = bool(declared)
                params.filters[0].type = 'ValueList'
                params.filters[0].list = list(declared) or ['(none)']
                given = {n for n, _ in (params.values or [])}
                if not given or not given <= set(declared):
                    params.values = [[n, str(d.get('default', ''))] for n, d in declared.items()]
                kind = entries.layer_kind(spec)
                fmt.filter.list = {'vector': ['gpkg', 'gdb', 'shp', 'geojson', 'parquet'],
                                   'raster': ['tif', 'gtiff'], 'pointcloud': ['laz']}[kind]
                if fmt.valueAsText not in fmt.filter.list:
                    fmt.value = fmt.filter.list[0]
                cell.enabled = kind != 'vector'
        kind_id = AREA_TYPES.get(area_type.valueAsText)
        if area_type.altered and not area_type.hasBeenValidated:
            custom.enabled = kind_id == 'custom'
            area_values.enabled = kind_id in PICK_MANY or kind_id in TYPED
            if area_values.hasBeenValidated:     # left over from the previous area type, not entered with this one
                area_values.value = None
            if kind_id in PICK_MANY and not kind_id.startswith('huc'):
                try:
                    from sccwrp_data import clipareas
                    area_values.filter.list = clipareas.choices(kind_id)
                except Exception as e:   # offline: let users type names
                    area_values.filter.list = []
                    arcpy.AddWarning(f'Could not load choices: {e}')
            else:
                area_values.filter.list = []

    def updateMessages(self, p):
        dataset, layer, area_type, area_values, custom, *_ = p
        if MISSING:
            dataset.setErrorMessage(MISSING_MSG)
        kind_id = AREA_TYPES.get(area_type.valueAsText)
        if kind_id in PICK_MANY and not area_values.values:
            area_values.setErrorMessage('Choose one or more' + (' HUC codes, e.g. 18070105' if kind_id.startswith('huc')
                                                               else ''))
        if kind_id in TYPED and len(area_values.values or []) != 1:
            area_values.setErrorMessage(TYPED[kind_id])
        if kind_id == 'point' and not (p[5].value or 0) > 0:
            p[5].setErrorMessage('A point needs a buffer, e.g. 0.5 km')
        if kind_id == 'custom' and not custom.value:
            custom.setErrorMessage('Pick a feature layer, draw a shape or browse to a file')
        ds, lay = _id(dataset.valueAsText), _id(layer.valueAsText)
        if ds and lay:
            try:
                _, spec = entries.layer_spec(ds, lay)
                if spec['handler'] == 'local':
                    layer.setWarningMessage('Read from SCCWRP\'s internal copy: needs the SCCWRP network.')
            except entries.CatalogError as e:
                layer.setErrorMessage(str(e))

    def execute(self, p, messages):
        if MISSING:
            raise RuntimeError(MISSING_MSG)
        _reload()
        import sccwrp_data as sd
        v = {x.name: x for x in p}
        ds, lay = _id(v['dataset'].valueAsText), _id(v['layer'].valueAsText)
        kind_id = AREA_TYPES[v['area_type'].valueAsText]
        if kind_id == 'custom':
            tmp = Path(arcpy.env.scratchFolder) / 'sccwrp_custom_area.shp'
            arcpy.management.CopyFeatures(v['custom_area'].value, str(tmp))
            area = str(tmp)
        elif kind_id in PICK_MANY:
            vals = [x.split(' ', 1)[0] if kind_id == 'regional-board' else x for x in v['area_values'].values]
            area = f'{kind_id}:{",".join(vals)}'
        elif kind_id in TYPED:
            typed = str(v['area_values'].values[0]).strip()
            area = typed if typed.startswith(f'{kind_id}:') else f'{kind_id}:{typed}'
        else:
            area = kind_id
        params = {n: val for n, val in (v['params'].values or []) if n and n != '(none)' and str(val).strip()}
        sr = arcpy.SpatialReference()
        sr.loadFromString(v['out_crs'].valueAsText)
        if not sr.factoryCode:
            raise arcpy.ExecuteError('Choose an output coordinate system that has an EPSG / WKID code')
        method = METHODS[v['method'].valueAsText]
        try:
            r = sd.get(ds, area, lay, params=params, buffer_km=v['buffer_km'].value or 0, method=method,
                       crs=f'EPSG:{sr.factoryCode}', resolution=v['cell_size'].value, fmt=v['out_format'].valueAsText,
                       out_dir=v['out_folder'].valueAsText, refresh=bool(v['refresh'].value), log=arcpy.AddMessage)
        except sd.CatalogError as e:
            arcpy.AddError(str(e))
            raise arcpy.ExecuteError(str(e)) from None
        for n in r.notes:
            arcpy.AddWarning(n)
        v['out_files'].values = [str(f) for f in r.files]
        if v['add_to_map'].value:
            _add_to_map(r, arcpy.AddMessage)

    def postExecute(self, p):
        return


def _add_to_map(result, log):
    try:
        m = arcpy.mp.ArcGISProject('CURRENT').activeMap
    except Exception:
        log('No open map to add the result to.')
        return
    if m is None:
        return
    for f in result.files:
        f = Path(f)
        if f.suffix == '.gpkg':
            layer = result.manifest['layer']
            m.addDataFromPath(f'{f}\\main.{layer}')
        elif f.suffix in ('.tif', '.shp', '.geojson'):
            m.addDataFromPath(str(f))
        elif f.suffix == '.gdb':
            arcpy.env.workspace = str(f)
            for fc in arcpy.ListFeatureClasses():
                m.addDataFromPath(str(f / fc))
        elif f.suffix == '.laz':
            lasd = f.with_suffix('.lasd')
            arcpy.management.CreateLasDataset(str(f), str(lasd), compute_stats='COMPUTE_STATS')
            m.addDataFromPath(str(lasd))


class AddLiveLayer:
    LIVE = ('arcgis-features', 'arcgis-image')

    def __init__(self):
        self.label = 'Add Live Layer'
        self.description = ('Add a provider\'s live map, feature or image service to the current map. Nothing is '
                            'downloaded; the map always shows the provider\'s current data.')
        self.canRunInBackground = False

    def getParameterInfo(self):
        dataset = arcpy.Parameter(name='dataset', displayName='Dataset', datatype='GPString',
                                  parameterType='Required', direction='Input')
        dataset.filter.type = 'ValueList'
        dataset.filter.list = _dataset_items(self.LIVE)
        layer = arcpy.Parameter(name='layer', displayName='Layer', datatype='GPString', parameterType='Required',
                                direction='Input')
        layer.filter.type = 'ValueList'
        return [dataset, layer]

    def updateParameters(self, p):
        ds = _id(p[0].valueAsText)
        if p[0].altered and not p[0].hasBeenValidated and ds:
            items, first = _layer_items(ds, self.LIVE)
            p[1].filter.list = items
            p[1].value = first

    def execute(self, p, messages):
        _, spec = entries.layer_spec(_id(p[0].valueAsText), _id(p[1].valueAsText))
        m = arcpy.mp.ArcGISProject('CURRENT').activeMap
        if m is None:
            raise arcpy.ExecuteError('Open a map first')
        m.addDataFromPath(spec['url'])
        arcpy.AddMessage(f'Added {spec["url"]}')
