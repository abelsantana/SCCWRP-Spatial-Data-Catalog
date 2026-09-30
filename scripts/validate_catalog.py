"""Check every catalog/datasets/*.json entry for required fields and valid values. Exit 1 on errors."""
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATASETS = REPO / 'catalog' / 'datasets'

REQUIRED = ['id', 'title', 'category', 'provider', 'recommendation', 'versions', 'links', 'access', 'packages',
            'other_access', 'notes', 'last_reviewed']
ACCESS_MODES = {'stream', 'read-in-place', 'download-on-demand', 'keep-local'}
VERSION_KEYS = ['latest', 'older_versions_online', 'sccwrp_version_online']
RECOMMENDATIONS = {'link-out', 'link-out-current', 'keep-local', 'keep-local-restricted'}
ROLES = {'landing', 'download', 'access'}
LINK_TYPES = {'landing', 'web', 'download', 'cloud-bucket', 's3', 'api', 'stac', 'thredds', 'wms', 'wcs',
              'arcgis-featureserver', 'arcgis-mapserver', 'arcgis-imageserver', 'arcgis-directory',
              'entwine-pointcloud'}
ID_RE = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')
# Optional "fetch" block used by the sccwrp_data tools: handler -> settings each layer must have
HANDLER_KEYS = {
    'arcgis-features': ['url'], 'arcgis-image': ['url'],
    'wcs': ['url', 'coverage', 'native_crs', 'resolution'], 'opendap': ['url', 'variable', 'x', 'y', 'crs'],
    'cog-tiles': ['url_template', 'tile_scheme', 'crs'], 'ept': ['url'],
    'streamcat': ['url', 'catchments'], 'sda': ['url'], 'download': ['url', 'kind'],
    'download-template': ['url_template', 'iterate', 'kind', 'member'], 'local': ['holding', 'kind'],
}
PARAM_TYPES = {'int', 'str', 'choice', 'list', 'dates'}


def check_fetch(fetch):
    errors = []
    layers = fetch.get('layers')
    if not isinstance(layers, dict) or not layers:
        return ['fetch.layers must be a non-empty object']
    if fetch.get('default') and fetch['default'] not in layers:
        errors.append(f'fetch.default "{fetch["default"]}" is not a layer')
    for name, spec in layers.items():
        where = f'fetch.layers.{name}'
        if not ID_RE.match(name):
            errors.append(f'{where}: layer names are lowercase words joined by hyphens')
        h = spec.get('handler')
        if h not in HANDLER_KEYS:
            errors.append(f'{where}: handler "{h}" not in {sorted(HANDLER_KEYS)}')
            continue
        for k in HANDLER_KEYS[h]:
            if k not in spec:
                errors.append(f'{where}: handler {h} needs "{k}"')
        if spec.get('kind') and spec['kind'] not in ('vector', 'raster'):
            errors.append(f'{where}: kind must be vector or raster')
        for p, d in spec.get('params', {}).items():
            if d.get('type', 'str') not in PARAM_TYPES:
                errors.append(f'{where}.params.{p}: type must be one of {sorted(PARAM_TYPES)}')
            if d.get('type') == 'choice' and d.get('default') not in d.get('choices', []) + [None]:
                errors.append(f'{where}.params.{p}: default is not one of the choices')
        if h == 'download-template' and spec.get('iterate') not in spec.get('params', {}):
            errors.append(f'{where}: iterate names a parameter that is not declared')
    return errors


def check(path):
    errors = []
    try:
        d = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as e:
        return [f'invalid JSON: {e}']
    for k in REQUIRED:
        if k not in d:
            errors.append(f'missing field "{k}"')
    if errors:
        return errors
    if d['id'] != path.stem:
        errors.append(f'id "{d["id"]}" does not match file name "{path.stem}"')
    if not ID_RE.match(d['id']):
        errors.append('id must be lowercase words joined by hyphens')
    if d['recommendation'] not in RECOMMENDATIONS:
        errors.append(f'recommendation must be one of {sorted(RECOMMENDATIONS)}')
    for k in VERSION_KEYS:
        if k not in d['versions']:
            errors.append(f'versions missing "{k}"')
    if not any(l.get('role') == 'landing' for l in d['links']):
        errors.append('needs at least one link with role "landing"')
    for i, l in enumerate(d['links']):
        if l.get('role') not in ROLES:
            errors.append(f'links[{i}] role must be one of {sorted(ROLES)}')
        if l.get('type') not in LINK_TYPES:
            errors.append(f'links[{i}] type "{l.get("type")}" not in {sorted(LINK_TYPES)}')
        if not re.match(r'^(https?|s3)://', l.get('url', '')):
            errors.append(f'links[{i}] url must start with http(s):// or s3://')
    if d['access'].get('mode') not in ACCESS_MODES:
        errors.append(f'access.mode must be one of {sorted(ACCESS_MODES)}')
    if not d['access'].get('how'):
        errors.append('access.how should say briefly how to use it without a local copy')
    for p in d['packages']:
        if p.get('language') not in ('Python', 'R') or not p.get('name'):
            errors.append(f'package entry needs language Python/R and a name: {p}')
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', d['last_reviewed']):
        errors.append('last_reviewed must be YYYY-MM-DD')
    if 'fetch' in d:
        errors += check_fetch(d['fetch'])
    return errors


def main():
    files = sorted(DATASETS.glob('*.json'))
    bad = 0
    for f in files:
        for e in check(f):
            bad += 1
            print(f'{f.name}: {e}')
    print(f'{len(files)} datasets checked, {bad} problems')
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
