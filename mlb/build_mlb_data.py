#!/usr/bin/env python3
"""JTT MLB data adapter — reshapes mlb_bundle.json into the unified shell's split data files.
Runs in the GitHub Actions pipeline after fetch_mlb.py produces mlb_bundle.json.
Batters + pitchers merge into one `players` pool (role: 'bat'|'pitch'); platoon splits,
batter-vs-pitcher (bvp) and h2h are preserved for mlb/scoring.js."""
import json, sys, os, hashlib

SRC = sys.argv[1] if len(sys.argv) > 1 else 'mlb_bundle.json'
OUT = sys.argv[2] if len(sys.argv) > 2 else 'data'
os.makedirs(OUT, exist_ok=True)
B = json.load(open(SRC, encoding='utf-8'))
season = B.get('season', 2026)

def ymd(d):  # "2026-08-01" -> 20260801 (sortable), and year
    try:
        p = d.split('-'); return int(p[0]), int(p[0]) * 10000 + int(p[1]) * 100 + int(p[2])
    except Exception:
        return season, 0

players, logs = [], []

# ---- batters ----
for b in B.get('batters', {}).values():
    s = b.get('season', {})
    p = {'name': b['name'], 'team': b.get('abbr'), 'position': b.get('pos') or 'DH', 'role': 'bat',
         'id': b.get('id'), 'teamId': b.get('teamId'), 'bats': b.get('bats'),
         'order': b.get('order'), 'tier': b.get('tier'), 'matches': s.get('G', 0),
         'splitVsL': b.get('splitVsL'), 'splitVsR': b.get('splitVsR'),
         'bvp': b.get('bvp'), 'h2h': b.get('h2h')}
    for k, v in s.items(): p[k] = v
    players.append(p)
    for gl in (b.get('gameLog') or []):
        yr, sk = ymd(gl.get('date', ''))
        row = {'Player': b['name'], 'Team': b.get('abbr'), 'Opp': gl.get('opp'),
               'Year': yr, 'Date': gl.get('date'), 'RoundName': str(sk), 'Week': sk, 'role': 'bat'}
        for k in ('H', 'TB', 'HR', 'RBI', 'R', 'SB', 'BB', 'SO'):
            if k in gl: row[k] = gl[k]
        logs.append(row)

# ---- pitchers ----
for pt in B.get('pitchers', {}).values():
    s = pt.get('season', {})
    p = {'name': pt['name'], 'team': pt.get('abbr'), 'position': pt.get('role') or 'SP', 'role': 'pitch',
         'id': pt.get('id'), 'teamId': pt.get('teamId'), 'throws': pt.get('throws'),
         'tier': pt.get('tier'), 'matches': s.get('GS', s.get('G', 0)),
         'splitVsL': pt.get('splitVsL'), 'splitVsR': pt.get('splitVsR'), 'h2h': pt.get('h2h')}
    for k, v in s.items(): p[k] = v
    players.append(p)
    for gl in (pt.get('gameLog') or []):
        yr, sk = ymd(gl.get('date', ''))
        row = {'Player': pt['name'], 'Team': pt.get('abbr'), 'Opp': gl.get('opp'),
               'Year': yr, 'Date': gl.get('date'), 'RoundName': str(sk), 'Week': sk, 'role': 'pitch'}
        for k in ('IP', 'K', 'BB', 'H', 'ER', 'HR', 'outs', 'win'):
            if k in gl: row[k] = gl[k]
        logs.append(row)

# ---- teams + dvp (vsStats = what a team allows) + results (from recent) ----
teams, dvp, results, seen_g = [], [], [], set()
for t in B.get('teams', {}).values():
    ab = t.get('abbr')
    teams.append({'team': ab, 'name': t.get('name'), 'div': t.get('div'), 'park': t.get('park'),
                  'kRate': t.get('kRate'), 'forStats': t.get('forStats'), 'vsStats': t.get('vsStats')})
    vs = t.get('vsStats') or {}
    dvp.append({'team': ab, 'pos': 'ALL', **{k: vs.get(k) for k in ('R', 'H', 'HR', 'BB', 'SO')}})
    for r in (t.get('recent') or []):
        key = tuple(sorted([ab, r.get('opp', '')])) + (r.get('date', ''),)
        if key in seen_g: continue
        seen_g.add(key)
        yr, sk = ymd(r.get('date', ''))
        # r is from this team's perspective: rf (runs for), ra (runs against), res W/L
        results.append({'home': ab, 'away': r.get('opp'), 'hs': r.get('rf'), 'as': r.get('ra'),
                        'season': yr, 'date': r.get('date'), 'week': sk})

# ---- fixture (slate) + lineups + weather ----
fixture, lineups = [], []
for g in B.get('slate', []):
    hm, aw = g.get('home', {}), g.get('away', {})
    parks = B.get('parks', {})
    pk = parks.get(g.get('parkId'), {})
    fixture.append({'home': hm.get('abbr'), 'away': aw.get('abbr'), 'homeAbbr': hm.get('abbr'), 'awayAbbr': aw.get('abbr'),
                    'date': g.get('date'), 'time': g.get('time'), 'gameTimeUTC': g.get('gameTimeUTC'),
                    'venue': pk.get('name') or g.get('parkId'), 'parkId': g.get('parkId'),
                    'status': g.get('status'), 'gamePk': g.get('gamePk'),
                    'homePitcher': (hm.get('probablePitcher') or {}).get('name'),
                    'awayPitcher': (aw.get('probablePitcher') or {}).get('name'),
                    'park': pk, 'weather': g.get('weather'), 'lines': g.get('lines')})
    if g.get('lineups'): lineups.append({'gamePk': g.get('gamePk'), 'home': g['lineups'].get('home'), 'away': g['lineups'].get('away')})

meta = {'currentSeason': str(season), 'season': season, 'asOf': B.get('asOf'), 'generated': B.get('generated'),
        'parks': B.get('parks'), 'trends': B.get('trends'), 'standings': B.get('standings'),
        'summary': {'players': len(players), 'gamelogs': len(logs), 'batters': len(B.get('batters', {})), 'pitchers': len(B.get('pitchers', {}))}}

# Auth: the unified shell reads meta.password_hash for the weekly gate. Match the browser's
# TextEncoder hash (UTF-8, no trailing newline) exactly as AFL/NFL builds do.
_pw = (os.environ.get('MLB_PASSWORD') or '').strip()
if _pw:
    meta['password_hash'] = hashlib.sha256(_pw.encode('utf-8')).hexdigest()

def w(name, obj):
    json.dump(obj, open(os.path.join(OUT, name), 'w', encoding='utf-8'), separators=(',', ':'))

w('players.json', players); w('gamelogs.json', logs); w('teams.json', teams)
w('dvp.json', dvp); w('results.json', results); w('fixture.json', fixture)
w('lineups.json', lineups); w('meta.json', meta)
w('teams_form.json', teams)   # shell computes WWWLW from results; teams carries the rest
for empty in ('injury.json', 'odds.json', 'weather.json'):
    w(empty, [])
print('players=%d (bat+pitch)  gamelogs=%d  fixture=%d  teams=%d  results=%d  dvp=%d' % (
    len(players), len(logs), len(fixture), len(teams), len(results), len(dvp)))
