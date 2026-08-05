#!/usr/bin/env python3
"""JTT MLB — team logo fetcher.
Downloads official team logos from the MLB Stats API (mlbstatic.com) using the
teamId already present in mlb_bundle.json, and saves them as
mlb/assets/logos/{abbr}.svg (lowercase) — the path/extension the unified shell
loads for MLB (SPORT_CONFIG.logoExt = '.svg').

Run once, or in the GitHub Action after fetch_mlb.py. Logos rarely change, so a
weekly/occasional run is plenty.

    python3 fetch_mlb_logos.py [mlb_bundle.json] [assets/logos]
"""
import json, os, sys, urllib.request

SRC = sys.argv[1] if len(sys.argv) > 1 else 'data/mlb_bundle.json'
OUT = sys.argv[2] if len(sys.argv) > 2 else 'assets/logos'
os.makedirs(OUT, exist_ok=True)

# MLB Stats API logo endpoints, tried in order. The first is the primary full team
# logo (transparent SVG); the cap "spot" is a fallback if the primary 404s.
URLS = [
    'https://www.mlbstatic.com/team-logos/{tid}.svg',
    'https://www.mlbstatic.com/team-logos/team-cap-on-light/{tid}.svg',
]
HDRS = {'User-Agent': 'Mozilla/5.0 (JTT logo fetcher)'}


def fetch(tid):
    for tpl in URLS:
        url = tpl.format(tid=tid)
        try:
            req = urllib.request.Request(url, headers=HDRS)
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read()
                if data and b'<svg' in data[:400].lower():
                    return data
        except Exception as e:
            sys.stderr.write('  %s -> %s\n' % (url, e))
    return None


def main():
    bundle = json.load(open(SRC, encoding='utf-8'))
    teams = bundle.get('teams', {})
    ok, miss = 0, []
    for t in teams.values():
        tid, abbr = t.get('id') or t.get('teamId'), (t.get('abbr') or '').lower()
        if not tid or not abbr:
            continue
        svg = fetch(tid)
        if svg:
            with open(os.path.join(OUT, abbr + '.svg'), 'wb') as f:
                f.write(svg)
            ok += 1
        else:
            miss.append(abbr.upper())
    print('logos saved: %d  missing: %s' % (ok, ', '.join(miss) if miss else 'none'))
    if miss:
        sys.stderr.write('Some logos failed — verify the MLB Stats API endpoint for: %s\n' % ', '.join(miss))


if __name__ == '__main__':
    main()
