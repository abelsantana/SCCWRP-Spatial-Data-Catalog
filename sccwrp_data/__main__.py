"""Command line.

  python -m sccwrp_data list                         datasets the tools can get (--all: whole catalog)
  python -m sccwrp_data info nlcd-annual             layers and parameters of one dataset
  python -m sccwrp_data areas [county]               area types, or the choices for one
  python -m sccwrp_data get nlcd-annual --area county:Orange --param year=2019 --out C:/work
  python -m sccwrp_data stage nhdplus-v21 [--layer flowlines]    build fast-tier California copies
  python -m sccwrp_data staged                       list the fast tier
  python -m sccwrp_data serve [--port 8765]          local service for the data view
"""
import argparse
import json
import sys

from .clipareas import areas as area_types, choices
from .entries import CatalogError, catalog, info, layers
from .request import get
from .stage import stage, staged_list


def _params(pairs):
    out = {}
    for p in pairs or []:
        k, _, v = p.partition('=')
        if not v:
            sys.exit(f'--param takes name=value, got {p}')
        out[k] = v
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog='sccwrp_data', description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('list')
    s.add_argument('--all', action='store_true')
    s = sub.add_parser('info')
    s.add_argument('dataset')
    s = sub.add_parser('areas')
    s.add_argument('kind', nargs='?')
    s = sub.add_parser('stage')
    s.add_argument('dataset')
    s.add_argument('--layer')
    s.add_argument('--param', action='append')
    sub.add_parser('staged')
    s = sub.add_parser('serve')
    s.add_argument('--port', type=int, default=8765)
    s = sub.add_parser('get')
    s.add_argument('dataset')
    s.add_argument('--area', required=True)
    s.add_argument('--layer')
    s.add_argument('--param', action='append', help='name=value, repeatable (e.g. year=2019, date=2013-01-01/2013-01-07)')
    s.add_argument('--buffer-km', type=float, default=0)
    s.add_argument('--method', default='clip', choices=['clip', 'intersects', 'bbox'])
    s.add_argument('--mask', default='both', choices=['both', 'land', 'water'])
    s.add_argument('--crs', default='EPSG:26911')
    s.add_argument('--resolution', type=float)
    s.add_argument('--format', dest='fmt')
    s.add_argument('--out')
    s.add_argument('--refresh', action='store_true')
    a = ap.parse_args(argv)

    try:
        if a.cmd == 'list':
            for e in catalog(fetchable_only=not a.all):
                lay = ', '.join(e['fetch']['layers']) if e.get('fetch') else '-'
                print(f'{e["id"]:32} {e["access"]["mode"]:19} {lay}')
        elif a.cmd == 'info':
            e = info(a.dataset)
            print(f'{e["title"]}\n{e["provider"]} | {e["recommendation"]} | {e["access"]["mode"]}: {e["access"]["how"]}')
            if e.get('fetch'):
                lays, default = layers(a.dataset)
                for name, spec in lays.items():
                    print(f'\n  layer {name}{" (default)" if name == default else ""}: {spec.get("title", "")}'
                          f' [{spec["handler"]}]')
                    for p, d in spec.get('params', {}).items():
                        print(f'    --param {p}=...  {d.get("description", "")} '
                              f'{json.dumps({k: v for k, v in d.items() if k != "description"})}')
            else:
                print('\n  Not fetchable yet (no "fetch" block).')
        elif a.cmd == 'areas':
            if a.kind:
                print('\n'.join(choices(a.kind)) or 'No choices: this area type is fixed.')
            else:
                for i, label, sel in area_types():
                    print(f'{i:16} {sel:10} {label}')
                print(f'{"county-coastal":16} {"pick-many":10} County with its coastal waters')
                print(f'{"file:<path>":16} {"custom":10} Any shapefile / GeoPackage / geodatabase layer')
        elif a.cmd == 'stage':
            stage(a.dataset, a.layer, _params(a.param))
        elif a.cmd == 'serve':
            from .server import serve
            serve(a.port)
        elif a.cmd == 'staged':
            for m in staged_list():
                size = f'{m["features"]:,} features' if 'features' in m else f'{m["width"]:,} x {m["height"]:,} cells'
                print(f'{m["dataset"] + "/" + m["layer"]:40} {size:>22}  built {m["built"][:16]}  from {m["source"]}')
        elif a.cmd == 'get':
            r = get(a.dataset, a.area, a.layer, params=_params(a.param), buffer_km=a.buffer_km, method=a.method, mask=a.mask,
                    crs=a.crs, resolution=a.resolution, fmt=a.fmt, out_dir=a.out, refresh=a.refresh)
            for f in r.files:
                print(f)
            for n in r.notes:
                print('note:', n)
    except CatalogError as e:
        sys.exit(f'error: {e}')


if __name__ == '__main__':
    main()
