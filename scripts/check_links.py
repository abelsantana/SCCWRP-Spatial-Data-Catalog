"""Check every link in the catalog and write catalog/link_status.json. Standard library only.

Downloads nothing: ArcGIS REST endpoints get a small ?f=json metadata request; everything else a
HEAD request, falling back to a one-byte ranged GET where servers refuse HEAD.

  python scripts/check_links.py [--only <dataset-id>] [--fail-on-broken]
"""
import argparse
import datetime
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATASETS = REPO / 'catalog' / 'datasets'
STATUS = REPO / 'catalog' / 'link_status.json'
UA = 'Mozilla/5.0 (compatible; SCCWRP-data-catalog-link-check; +https://github.com/)'
TIMEOUT = 30
# Codes that usually mean "this site blocks scripts", not "this link is gone"
BLOCKED_CODES = {401, 403, 405, 406, 429, 503}


def request(url, method, extra=None):
    req = urllib.request.Request(url, method=method, headers={'User-Agent': UA, **(extra or {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        body = r.read(65536) if method == 'GET' else b''
        return r.status, body


S3_HOST = re.compile(r'^https://([^./]+)\.s3[.-]?[^/]*\.amazonaws\.com/(.*)$')


def check_s3_prefix(url):
    """S3 has no folder objects, so a '.../prefix/' URL 404s. List one key under the prefix instead."""
    m = S3_HOST.match(url)
    bucket, prefix = m.group(1), m.group(2)
    host = url[:url.index('.com/') + 4]
    list_url = f'{host}/?list-type=2&max-keys=1&prefix={urllib.parse.quote(prefix)}'
    code, body = request(list_url, 'GET')
    found = b'<Key>' in body
    return {'status': 'ok' if found else 'broken', 'code': code, **({} if found else {'detail': 'no objects under prefix'})}


def check(link):
    url, ltype = link['url'], link['type']
    if url.startswith('s3://'):
        return {'status': 'not-http'}
    target = url.split('#')[0] or url
    try:
        if target.endswith('/') and S3_HOST.match(target):
            return check_s3_prefix(target)
        if ltype.startswith('arcgis-'):
            sep = '&' if '?' in target else '?'
            code, body = request(f'{target}{sep}f=json', 'GET')
            try:
                meta = json.loads(body.decode('utf-8', 'replace'))
            except ValueError:
                meta = {}
            if isinstance(meta, dict) and 'error' in meta:
                err = meta['error']
                return {'status': 'broken', 'code': err.get('code'), 'detail': str(err.get('message'))[:120]}
            return {'status': 'ok', 'code': code}
        try:
            code, _ = request(target, 'HEAD')
        except urllib.error.HTTPError as e:
            if e.code not in (403, 405, 501):
                raise
            code, _ = request(target, 'GET', {'Range': 'bytes=0-0'})
        return {'status': 'ok', 'code': code}
    except urllib.error.HTTPError as e:
        return {'status': 'blocked' if e.code in BLOCKED_CODES else 'broken', 'code': e.code}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, 'reason', e)
        return {'status': 'timeout' if 'timed out' in str(reason) else 'error', 'detail': str(reason)[:120]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only')
    ap.add_argument('--fail-on-broken', action='store_true')
    args = ap.parse_args()

    links = {}
    for p in sorted(DATASETS.glob('*.json')):
        d = json.loads(p.read_text(encoding='utf-8'))
        if args.only and d['id'] != args.only:
            continue
        for l in d['links']:
            links.setdefault(l['url'], (l, d['id']))

    today = datetime.date.today().isoformat()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = dict(zip(links, pool.map(lambda item: check(item[0]), links.values())))

    previous = json.loads(STATUS.read_text(encoding='utf-8'))['links'] if STATUS.exists() else {}
    merged = {**previous, **{u: {**r, 'checked': today} for u, r in results.items()}}
    STATUS.write_text(json.dumps({'checked': today, 'links': dict(sorted(merged.items()))}, indent=1) + '\n',
                      encoding='utf-8')

    counts = {}
    for u, r in results.items():
        counts[r['status']] = counts.get(r['status'], 0) + 1
        if r['status'] in ('broken', 'error', 'timeout'):
            print(f"{r['status'].upper():8s} {links[u][1]}: {u} {r.get('code', '')} {r.get('detail', '')}")
    print(f'{len(results)} links checked: ' + ', '.join(f'{k} {v}' for k, v in sorted(counts.items())))
    if args.fail_on_broken and counts.get('broken'):
        sys.exit(1)


if __name__ == '__main__':
    main()
