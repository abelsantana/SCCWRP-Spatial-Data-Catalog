"""SCCWRP's own copies: resolves keep-local layers and internal clip areas to server paths.

The paths live in an internal holdings file (see config.holdings_file), never in this public repo, so a server
move only changes that one file. Its shape:
  {"root": "\\\\server\\share",
   "clip_area_sources": {"<ref>": "relative\\path.shp"},
   "layers": {"<dataset-id>/<layer>": "relative\\path"}}
"""
import json
from functools import lru_cache
from pathlib import Path

from . import config
from .entries import CatalogError


@lru_cache(maxsize=None)
def _load():
    p = config.holdings_file()
    if not p or not Path(p).exists():
        raise CatalogError('This needs SCCWRP\'s internal holdings file, which is only available on the SCCWRP '
                           'network. Set SCCWRP_DATA_HOLDINGS to its path.')
    return json.loads(Path(p).read_text(encoding='utf-8'))


def _full(rel):
    h = _load()
    p = Path(h['root']) / rel
    if not p.exists():
        raise CatalogError(f'Internal copy not found: {p} (is the server reachable, or has the holdings file '
                           f'fallen behind a move?)')
    return p


def clip_area_path(ref):
    try:
        return _full(_load()['clip_area_sources'][ref])
    except KeyError:
        raise CatalogError(f'No clip area source "{ref}" in the holdings file') from None


def layer_path(key):
    try:
        return _full(_load()['layers'][key])
    except KeyError:
        raise CatalogError(f'No internal copy registered for "{key}" in the holdings file') from None
