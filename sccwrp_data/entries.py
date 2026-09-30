"""Read the catalog: one JSON file per dataset in catalog/datasets/, or the published copy on GitHub.

With a URL source (the default outside a git checkout) the tools fetch docs/catalog.json once per process, keep the
last good copy, and fall back to it, then to the bundled catalog/datasets/, when GitHub cannot be reached. Entries this
version of the code cannot run (a handler it does not have) keep their bundled version.
"""
import json
import time
from functools import lru_cache

from . import config
from .config import DATASETS_DIR


class CatalogError(ValueError):
    """A request the catalog cannot satisfy (unknown dataset, layer or parameter)."""


SCHEMA = 1   # docs/catalog.json format this code reads; build_site.py writes the same number
# Handlers this code has (tests/toolbox_test.py checks it matches handlers.base.HANDLERS). Kept here so reading the
# catalog does not import the handlers and their GIS libraries.
KNOWN_HANDLERS = {'arcgis-features', 'arcgis-image', 'download', 'download-template', 'local', 'wcs', 'opendap',
                  'cog-tiles', 'ept', 'streamcat'}
TIMEOUT = (3, 10)   # seconds to connect, to read: a slow network must not hold up opening the tools

_status = {}


def _bundled():
    out = {}
    for p in sorted(DATASETS_DIR.glob('*.json')):
        d = json.loads(p.read_text(encoding='utf-8'))
        out[d['id']] = d
    return out


def _download(url):
    """The published catalog as a dict, from GitHub or the saved copy. None if neither is usable."""
    import requests
    copy = config.catalog_copy()
    saved = None
    if copy.exists():
        try:
            saved = json.loads(copy.read_text(encoding='utf-8'))
        except ValueError:
            saved = None
    headers = {'User-Agent': config.USER_AGENT}
    if saved and saved.get('_etag'):
        headers['If-None-Match'] = saved['_etag']
    try:
        r = requests.get(url, headers=headers, timeout=TIMEOUT)
        if r.status_code == 304:
            _status.update(state='current', detail='unchanged since the last download')
            return saved
        r.raise_for_status()
        data = r.json()
        if data.get('schema') != SCHEMA:
            raise ValueError(f'catalog format {data.get("schema")}, this code reads {SCHEMA}. Update the tools.')
        data['_etag'] = r.headers.get('ETag')
        data['_downloaded'] = time.strftime('%Y-%m-%d %H:%M:%S')
        copy.parent.mkdir(parents=True, exist_ok=True)
        tmp = copy.with_name(copy.name + '.part')
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        tmp.replace(copy)
        _status.update(state='current', detail='downloaded')
        return data
    except Exception as e:  # offline, GitHub down, bad file: never fatal
        if saved:
            _status.update(state='saved-copy', detail=f'could not use the GitHub catalog ({e}); using the copy from '
                                                      f'{saved.get("_downloaded")}')
            return saved
        _status.update(state='bundled', detail=f'could not use the GitHub catalog ({e}); using the installed one')
        return None


def _runnable(entry):
    layers = (entry.get('fetch') or {}).get('layers') or {}
    return all(spec.get('handler') in KNOWN_HANDLERS for spec in layers.values())


@lru_cache(maxsize=None)
def _load_all():
    bundled = _bundled()
    source = config.catalog_source()
    _status.clear()
    _status.update(source=source)
    if source == 'local':
        _status.update(state='local', detail='catalog/datasets in this folder')
        return bundled
    data = _download(source)
    if not data:
        return bundled
    out, kept = {}, []
    for d in data['datasets']:
        if _runnable(d):
            out[d['id']] = d
        elif d['id'] in bundled:
            out[d['id']] = bundled[d['id']]
            kept.append(d['id'])
        else:
            out[d['id']] = {k: v for k, v in d.items() if k != 'fetch'}   # listed with its links, not fetchable
            kept.append(d['id'])
    if kept:
        _status['needs_update'] = kept
    return dict(sorted(out.items()))


def source_status():
    """Where the catalog in use came from: source, state (local / current / saved-copy / bundled), detail, and
    needs_update (entries that need newer tools, run from the installed version instead)."""
    _load_all()
    return dict(_status)


def reload():
    """Read the catalog again (e.g. after a push), for long-running processes."""
    _load_all.cache_clear()
    return source_status()


def catalog(fetchable_only=False):
    """All catalog entries, as a list of dicts sorted by id. fetchable_only: only entries the tools can get."""
    entries = list(_load_all().values())
    if fetchable_only:
        entries = [e for e in entries if e.get('fetch')]
    return entries


def info(dataset_id):
    """The catalog entry for one dataset."""
    try:
        return _load_all()[dataset_id]
    except KeyError:
        close = [i for i in _load_all() if dataset_id.lower() in i]
        hint = f' Did you mean: {", ".join(close)}?' if close else ''
        raise CatalogError(f'No dataset "{dataset_id}" in the catalog.{hint}') from None


def layers(dataset_id):
    """{layer name: fetch spec} for a dataset, and the default layer name."""
    entry = info(dataset_id)
    fetch = entry.get('fetch')
    if not fetch:
        raise CatalogError(f'{dataset_id} has no "fetch" block yet, so the tools cannot get it. '
                           f'Use the links in the catalog: {entry["access"]["how"]}')
    return fetch['layers'], fetch.get('default') or next(iter(fetch['layers']))


def layer_spec(dataset_id, layer=None):
    """(layer name, fetch spec) for the requested layer or the dataset's default."""
    all_layers, default = layers(dataset_id)
    name = layer or default
    if name not in all_layers:
        raise CatalogError(f'{dataset_id} has no layer "{name}". Layers: {", ".join(all_layers)}')
    return name, all_layers[name]
