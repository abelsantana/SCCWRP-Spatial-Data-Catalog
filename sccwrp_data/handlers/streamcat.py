"""EPA StreamCat metrics for every NHDPlus V2 catchment in the area, joined to the catchment polygons."""
import pandas as pd

from .. import http
from .arcgis import query_features
from .base import Fetched, handler

CHUNK = 400   # COMIDs per API request


@handler('streamcat')
def streamcat(ctx):
    """Spec: url (metrics endpoint), catchments {url, id_field}. Params: metrics (list), aoi (ws | cat | ...)."""
    s = ctx.spec
    cat = query_features(s['catchments']['url'], area=ctx.area, out_fields=s['catchments']['id_field'], log=ctx.log)
    idf = s['catchments']['id_field']
    cat = cat.rename(columns={idf: 'comid'})[['comid', 'geometry']]
    cat['comid'] = cat['comid'].astype('int64')
    comids = sorted(cat['comid'].unique())
    metrics, aoi = ctx.params['metrics'], ctx.params['aoi']
    rows = []
    for i in range(0, len(comids), CHUNK):
        chunk = comids[i:i + CHUNK]
        data = {'name': ','.join(metrics), 'aoi': aoi, 'comid': ','.join(map(str, chunk))}
        r = http.post(s['url'], data=data)
        rows += r.json().get('items', [])
    table = pd.DataFrame(rows)
    ctx.log(f'  {len(table):,} catchments with metrics ({", ".join(metrics)}, {aoi})')
    if not table.empty:
        table['comid'] = table['comid'].astype('int64')
    joined = cat.merge(table, on='comid', how='left')
    csv = ctx.workdir / f'{ctx.layer}.csv'
    table.to_csv(csv, index=False)
    missing = int(joined.drop(columns=['comid', 'geometry']).isna().all(axis=1).sum()) if len(table.columns) > 1 else len(joined)
    notes = ['Metrics describe whole catchments, so catchments that touch the area are kept uncut.']
    if missing:
        notes.append(f'{missing:,} catchments have no StreamCat values (e.g. coastal or closed basins).')
    return Fetched('vector', [(ctx.layer, joined)], sources=[s['url'], s['catchments']['url']], notes=notes,
                   extra_files=[(f'{ctx.layer}.csv', csv)], method='intersects')
