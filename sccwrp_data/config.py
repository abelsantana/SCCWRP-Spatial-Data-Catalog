"""Paths and settings. Everything a site may need to change comes from environment variables or a config file.

Settings are read, in order of precedence, from:
  1. environment variables (SCCWRP_DATA_CACHE, SCCWRP_DATA_HOLDINGS, SCCWRP_DATA_MAX_AGE_DAYS)
  2. %APPDATA%/sccwrp-data/config.json   keys: cache, holdings, max_age_days
  3. defaults below
"""
import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CATALOG_DIR = REPO / 'catalog'
DATASETS_DIR = CATALOG_DIR / 'datasets'
CLIP_AREAS_FILE = CATALOG_DIR / 'clip_areas.json'
CLIP_AREAS_DIR = CATALOG_DIR / 'clip_areas'

DEFAULT_CRS = 'EPSG:26911'     # NAD83 / UTM zone 11N, the team's standard (decided 2026-09-30)
EQUAL_AREA = 'EPSG:3310'       # internal only: buffers and area estimates (California Albers)
USER_AGENT = 'sccwrp-data/0.1 (+https://github.com/SCCWRP)'


def _user_config():
    p = Path(os.environ.get('APPDATA', Path.home())) / 'sccwrp-data' / 'config.json'
    if p.exists():
        return json.loads(p.read_text(encoding='utf-8'))
    return {}


_CFG = _user_config()


def _setting(env, key, default):
    return os.environ.get(env) or _CFG.get(key) or default


def cache_dir():
    """Root of the result and download cache. AppData for development; must move to a shared folder on the new
    server before beta testing."""
    default = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.cache')) / 'sccwrp-data' / 'cache'
    p = Path(_setting('SCCWRP_DATA_CACHE', 'cache', default))
    p.mkdir(parents=True, exist_ok=True)
    return p


def fast_dir():
    """Fast tier: staged California copies of heavily used layers. On this machine for development; moves to the
    new server with the cache."""
    default = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.cache')) / 'sccwrp-data' / 'fast'
    p = Path(_setting('SCCWRP_DATA_FAST', 'fast', default))
    p.mkdir(parents=True, exist_ok=True)
    return p


def output_dir():
    """Where the data view saves results unless the user picks a folder: Documents/SCCWRP Data."""
    default = Path.home() / 'Documents' / 'SCCWRP Data'
    return Path(_setting('SCCWRP_DATA_OUTPUT', 'output', default))


def max_age_days():
    """Cached results older than this are fetched again (60 days, decided 2026-09-30)."""
    return float(_setting('SCCWRP_DATA_MAX_AGE_DAYS', 'max_age_days', 60))


CATALOG_URL = ('https://raw.githubusercontent.com/abelsantana/SCCWRP-Spatial-Data-Catalog/main/docs/catalog.json')


def catalog_source():
    """Where dataset entries come from: the published catalog on GitHub (default), so a pushed fix reaches everyone
    without reinstalling, or 'local' (the catalog/ folder next to this code) to try entries before pushing them.
    """
    s = _setting('SCCWRP_DATA_CATALOG', 'catalog', CATALOG_URL)
    return CATALOG_URL if s == 'github' else s


def catalog_copy():
    """Last catalog downloaded from GitHub, used when GitHub cannot be reached."""
    return Path(os.environ.get('LOCALAPPDATA', Path.home() / '.cache')) / 'sccwrp-data' / 'catalog.json'


def holdings_file():
    """Internal file that maps keep-local layers to server paths. Not part of this public repo.

    Falls back to the S-drive audit repo next to this one, for development on SCCWRP machines.
    """
    p = _setting('SCCWRP_DATA_HOLDINGS', 'holdings', None)
    if p:
        return Path(p)
    sibling = REPO.parent / 'SDriveInventory' / 'sdrive_holdings.json'
    return sibling if sibling.exists() else None
