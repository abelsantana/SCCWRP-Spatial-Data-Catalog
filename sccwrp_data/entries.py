"""Read the catalog: one JSON file per dataset in catalog/datasets/."""
import json
from functools import lru_cache

from .config import DATASETS_DIR


class CatalogError(ValueError):
    """A request the catalog cannot satisfy (unknown dataset, layer or parameter)."""


@lru_cache(maxsize=None)
def _load_all():
    out = {}
    for p in sorted(DATASETS_DIR.glob('*.json')):
        d = json.loads(p.read_text(encoding='utf-8'))
        out[d['id']] = d
    return out


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
