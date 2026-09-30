"""One HTTP session with retries for every provider request, plus streaming downloads."""
import shutil
import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import USER_AGENT

_sessions = {}


class ServiceBusy(RuntimeError):
    """The provider timed out or is overloaded (HTTP 5xx, or an ArcGIS error reporting one)."""


TRANSIENT = (ServiceBusy, requests.exceptions.RetryError, requests.exceptions.Timeout,
             requests.exceptions.ConnectionError)


def session(retry=True):
    """Shared session. retry=False fails at once, for requests the caller can break into smaller ones instead."""
    if retry not in _sessions:
        s = requests.Session()
        policy = Retry(total=4 if retry else 0, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504),
                       allowed_methods=('GET', 'POST', 'HEAD'), raise_on_status=False)
        s.mount('https://', HTTPAdapter(max_retries=policy, pool_maxsize=16))
        s.mount('http://', HTTPAdapter(max_retries=policy, pool_maxsize=16))
        s.headers['User-Agent'] = USER_AGENT
        _sessions[retry] = s
    return _sessions[retry]


def _check(r):
    if r.status_code >= 500:
        raise ServiceBusy(f'{r.url[:120]}: HTTP {r.status_code}')
    r.raise_for_status()
    return r


def get(url, params=None, timeout=300, retry=True, **kw):
    return _check(session(retry).get(url, params=params, timeout=timeout, **kw))


def post(url, data=None, timeout=300, retry=True, **kw):
    return _check(session(retry).post(url, data=data, timeout=timeout, **kw))


def arcgis_json(url, params=None, method='get', retry=True):
    """Call an ArcGIS REST endpoint with f=json. ArcGIS reports errors with HTTP 200 and an 'error' key."""
    params = dict(params or {}, f=params.get('f', 'json') if params else 'json')
    r = post(url, data=params, retry=retry) if method == 'post' else get(url, params=params, retry=retry)
    d = r.json()
    if 'error' in d:
        e = d['error']
        msg = f'{url}: ArcGIS error {e.get("code")}: {e.get("message")} {e.get("details") or ""}'
        if e.get('code') in (500, 502, 503, 504) or 'timeout' in str(e.get('message', '')).lower():
            raise ServiceBusy(msg)
        raise RuntimeError(msg)
    return d


def download(url, dest, params=None):
    """Stream url to dest (written to a .part file first, so an interrupted download is never mistaken for a finished one)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + '.part')
    t0 = time.time()
    with session().get(url, params=params, stream=True, timeout=600) as r:
        r.raise_for_status()
        r.raw.decode_content = True
        with open(part, 'wb') as f:
            shutil.copyfileobj(r.raw, f, length=1 << 20)
    part.replace(dest)
    return dest, time.time() - t0


def exists(url):
    """True if url answers a HEAD request with 200 (used to skip tiles a provider does not have, e.g. open ocean)."""
    r = session().head(url, timeout=60, allow_redirects=True)
    return r.status_code == 200
