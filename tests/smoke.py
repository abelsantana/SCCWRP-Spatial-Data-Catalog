"""End-to-end test: one real request per access path, each with a different kind of clip area.

    python tests/smoke.py                  run every case (uses the cache; --refresh to re-fetch)
    python tests/smoke.py 3dep nlcd        run cases whose name contains any of the words
    python tests/smoke.py --list

Run in ArcGIS Pro's Python. Needs internet; the local cases also need the SCCWRP network.
Writes outputs to tests/output/ and a summary to tests/output/smoke_report.json.
"""
import argparse
import json
import sys
import time
import traceback
from pathlib import Path

from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sccwrp_data as sd  # noqa: E402
from sccwrp_data.entries import layer_spec  # noqa: E402
from sccwrp_data.handlers.base import HANDLERS  # noqa: E402

OUT = Path(__file__).resolve().parent / 'output'

# name, dataset, layer, area, options, checks
CASES = [
    ('feature-service', 'ca-integrated-report', 'polygons', 'county:Orange', {}, {'min_features': 5}),
    ('map-service', 'epa-attains', 'lines', 'smc-watershed:San Juan', {}, {'min_features': 20}),
    ('image-service', 'usgs-3dep-ned', 'dem', 'county:Orange', {'resolution': 30}, {'value_range': (-50, 1800)}),
    ('cog-tiles', 'usgs-3dep-ned', 'dem-13-tiles', 'huc10:1807010501', {}, {'value_range': (-50, 3100)}),
    ('wcs-year', 'nlcd-annual', 'land-cover', 'regional-board:8', {'params': {'year': 2019}},
     {'value_range': (11, 95)}),
    ('opendap', 'noaa-tsunami-dems', 'san-diego', box(-117.30, 32.65, -117.10, 32.80), {},
     {'value_range': (-600, 200)}),
    ('point-cloud', 'la-river-lidar-2016', 'points', box(-118.232, 34.050, -118.224, 34.056), {}, {'min_bytes': 1e5}),
    ('api-table', 'watershed-metric-resources', 'streamcat', 'huc10:1807010501', {}, {'min_features': 20}),
    ('zip-download', 'shorelines', 'shoreline-1998', 'county-coastal:Orange', {}, {'min_features': 1}),
    ('templated-download', 'prism-daily', 'daily', 'socal',
     {'params': {'date': '2013-02-10/2013-02-12'}}, {'files': 3, 'value_range': (0, 400)}),
    ('local-vector', 'cpad', 'units', 'county:Los Angeles', {}, {'min_features': 500}),
    ('local-raster-grid', 'ca-seafloor-mapping', 'la-jolla-bathymetry', 'county-coastal:San Diego',
     {'fmt': 'gtiff'}, {'value_range': (-700, 10), 'max_cells': 30e6}),
    # Fast tier: must be answered from the staged California copy, quickly
    ('fast-vector', 'nhdplus-v21', 'flowlines', 'county:Los Angeles', {}, {'min_features': 3000, 'fast': True,
                                                                           'max_seconds': 5}),
    ('fast-ocean-raster', 'cdfw-marine', 'bathymetry-200m', 'bight-strata:Outer Shelf,Upper Slope', {},
     {'value_range': (-700, -30), 'fast': True, 'max_seconds': 5}),
    ('fast-water-mask', 'cdfw-marine', 'mpas', 'county-coastal:Orange', {'mask': 'water'},
     {'min_features': 1, 'fast': True, 'max_seconds': 5}),
]


def check(result, want):
    problems = []
    outs = result.manifest['files']
    if 'files' in want and len(outs) != want['files']:
        problems.append(f'expected {want["files"]} files, got {len(outs)}')
    for f in outs:
        if 'min_features' in want and f.get('features', 0) < want['min_features']:
            problems.append(f'{f["file"]}: {f.get("features")} features, expected >= {want["min_features"]}')
        if 'value_range' in want and f.get('statistics'):
            lo, hi = want['value_range']
            s = f['statistics']
            if s['min'] < lo or s['max'] > hi:
                problems.append(f'{f["file"]}: values {s["min"]}..{s["max"]} outside expected {lo}..{hi}')
        if 'max_cells' in want and f.get('width', 0) * f.get('height', 0) > want['max_cells']:
            problems.append(f'{f["file"]}: {f["width"]} x {f["height"]} cells, more than {want["max_cells"]:,.0f}')
        if 'min_bytes' in want and f.get('bytes', 0) < want['min_bytes']:
            problems.append(f'{f["file"]}: {f.get("bytes")} bytes')
    if want.get('fast') and not any(s.startswith('SCCWRP fast copy') for s in result.manifest['sources']):
        problems.append(f'expected the fast copy, got {result.manifest["sources"]}')
    if 'max_seconds' in want and result.manifest['seconds'] > want['max_seconds']:
        problems.append(f'took {result.manifest["seconds"]} s, limit {want["max_seconds"]} s')
    for p in result.files:
        if not Path(p).exists():
            problems.append(f'missing output {p}')
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('only', nargs='*')
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--list', action='store_true')
    a = ap.parse_args()
    if a.list:
        for c in CASES:
            print(f'{c[0]:20} {c[1]}/{c[2]}  area={c[3] if isinstance(c[3], str) else "custom box"}')
        return
    cases = [c for c in CASES if not a.only or any(w in c[0] or w in c[1] for w in a.only)]
    OUT.mkdir(exist_ok=True)
    report = []
    for name, ds, layer, area, opts, want in cases:
        print(f'\n=== {name}: {ds}/{layer}')
        t0 = time.time()
        row = {'case': name, 'dataset': ds, 'layer': layer,
               'handler': layer_spec(ds, layer)[1]['handler']}
        try:
            r = sd.get(ds, area, layer, out_dir=OUT / name, refresh=a.refresh, **opts)
            problems = check(r, want)
            row.update(ok=not problems, problems=problems, cached=r.cached, files=[str(f) for f in r.files],
                       outputs=r.manifest['files'], notes=r.notes, area=r.manifest['area'])
        except Exception as e:
            row.update(ok=False, problems=[f'{type(e).__name__}: {e}'], trace=traceback.format_exc())
            print(traceback.format_exc())
        row['seconds'] = round(time.time() - t0, 1)
        print(('PASS' if row['ok'] else 'FAIL') + f' {name} in {row["seconds"]:.0f} s ' + '; '.join(row['problems']))
        report.append(row)
    (OUT / 'smoke_report.json').write_text(json.dumps(report, indent=1, default=str), encoding='utf-8')
    print('\n' + '\n'.join(f'{"PASS" if r["ok"] else "FAIL"}  {r["case"]:20} {r["seconds"]:7.1f} s  '
                           f'{"(cached) " if r.get("cached") else ""}{"; ".join(r["problems"])}' for r in report))
    untested = set(HANDLERS) - {r['handler'] for r in report}
    if untested and not a.only:
        print(f'handlers without a case: {sorted(untested)}')
    sys.exit(0 if all(r['ok'] for r in report) else 1)


if __name__ == '__main__':
    main()
