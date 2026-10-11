#!/usr/bin/env python3
"""JTT MLB data adapter ? reshapes mlb_bundle.json into the unified shell's split data files.
Runs in the GitHub Actions pipeline after fetch_mlb.py produces mlb_bundle.json.
Batters + pitchers merge into one `players` pool (role: 'bat'|'pitch'); platoon splits,
batter-vs-pitcher (bvp) and h2h are preserved for mlb/scoring.js."""
import json, sys, os, hashlib

if '--selftest' in sys.argv:
    # Runs THIS script against synthetic bundles and asserts on what it writes. It exists because
    # every MLB data fault so far has been silent - a file that was valid, non-empty and wrong -
    # and the results path is the one most recently caught doing it.
    import subprocess, tempfile, shutil
    _tmp = tempfile.mkdtemp()
    _fails = []

    def _run(bundle, out, prev=None):
        os.makedirs(out, exist_ok=True)
        if prev is not None:
            with open(os.path.join(out, 'results.json'), 'w') as fh:
                json.dump(prev, fh)
        bp = os.path.join(_tmp, 'b_%d.json' % len(os.listdir(_tmp)))
        with open(bp, 'w') as fh:
            json.dump(bundle, fh)
        r = subprocess.run([sys.executable, os.path.abspath(__file__), bp, out],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise AssertionError('build failed: ' + (r.stderr or '')[-400:])
        with open(os.path.join(out, 'results.json'), encoding='utf-8') as fh:
            return json.load(fh), r.stdout

    def _check(name, cond, detail=''):
        print(('  ok   ' if cond else '  FAIL ') + name + ('  ' + detail if detail else ''))
        if not cond:
            _fails.append(name)

    def _sched(rows):
        return [{'gameId': g, 'date': d, 'g': n, 'home': h, 'away': a, 'hs': hs, 'as': a_s,
                 'season': 2026, 'week': int(d.replace('-', ''))}
                for (g, d, n, h, a, hs, a_s) in rows]

    _base = {'season': 2026, 'teams': {}, 'batters': {}, 'pitchers': {}, 'slate': [], 'parks': {}}

    # 1. the schedule block is used, and used whole
    full = _sched([(1, '2026-04-01', 1, 'LAD', 'SF', 5, 1),
                   (2, '2026-04-02', 1, 'SF', 'LAD', 3, 2),
                   (3, '2026-04-03', 1, 'NYY', 'BOS', 0, 4)])
    got, out = _run(dict(_base, results=full), os.path.join(_tmp, 'o1'))
    _check('the schedule block becomes results.json', len(got) == 3, '(%d rows)' % len(got))
    _check('home and away survive as written',
           any(r['home'] == 'LAD' and r['away'] == 'SF' and r['hs'] == 5 for r in got)
           and any(r['home'] == 'SF' and r['away'] == 'LAD' and r['hs'] == 3 for r in got),
           '(the same pair, both ways round, on different days - two real games)')

    # 2. a doubleheader is two rows, not one
    dh = _sched([(10, '2026-05-01', 1, 'CHC', 'STL', 2, 1),
                 (11, '2026-05-01', 2, 'CHC', 'STL', 7, 6)])
    got, _ = _run(dict(_base, results=dh), os.path.join(_tmp, 'o2'))
    _check('a doubleheader stays two games', len(got) == 2, '(%d rows)' % len(got))

    # 3. THE BUG THIS WAS WRITTEN FOR: one game reported from both clubs is ONE row
    dup = _sched([(20, '2026-06-01', 1, 'LAD', 'SF', 5, 1)]) + \
          [{'date': '2026-06-01', 'home': 'SF', 'away': 'LAD', 'hs': 1, 'as': 5, 'season': 2026}]
    got, _ = _run(dict(_base, results=dup), os.path.join(_tmp, 'o3'))
    _check('one game reported from both sides collapses to one row', len(got) == 1,
           '(%d rows - the old key counted it twice)' % len(got))

    # 4. a covered season SUPERSEDES what was on disk, mis-oriented legacy rows included
    # The legacy rows are of two kinds, and only one of them is removed by the de-duplication
    # above - which is what made the first version of this check pass while testing nothing:
    #   a) the same game the fresh fetch has, written the wrong way round
    #   b) a 2026 game the fresh fetch does NOT list at all
    # (b) is the one that needs the season filter. A re-fetched season is published whole, so a
    # row it does not contain is a row that should not survive; without the filter it lives on
    # for ever, and a game the schedule has since corrected or voided can never be withdrawn.
    legacy = [{'date': '2026-04-01', 'home': 'SF', 'away': 'LAD', 'hs': 1, 'as': 5, 'season': 2026},
              {'date': '2026-08-08', 'home': 'TEX', 'away': 'SEA', 'hs': 9, 'as': 9, 'season': 2026},
              {'date': '2025-09-09', 'home': 'NYM', 'away': 'ATL', 'hs': 3, 'as': 2, 'season': 2025}]
    got, _ = _run(dict(_base, results=full), os.path.join(_tmp, 'o4'), prev=legacy)
    _check('a re-fetched season replaces the rows already on disk',
           sum(1 for r in got if r.get('season') == 2026) == 3,
           '(%d rows for 2026, want the 3 from the schedule)'
           % sum(1 for r in got if r.get('season') == 2026))
    _check('the same game written the wrong way round is dropped',
           not any(r.get('date') == '2026-04-01' and r.get('home') == 'SF' for r in got))
    _check('a row from that season the fetch no longer lists is dropped too',
           not any(r.get('date') == '2026-08-08' for r in got),
           '(this is the one the de-duplication cannot do on its own)')
    _check('while a season the fetch did not cover is left alone',
           any(r.get('season') == 2025 for r in got))

    # 5. an older bundle with no schedule still builds, from the per-team recent arrays
    teams = {'1': {'abbr': 'LAD', 'name': 'Dodgers', 'recent': [
                {'date': '2026-07-01', 'opp': 'SF', 'rf': 4, 'ra': 2, 'res': 'W'}]},
             '2': {'abbr': 'SF', 'name': 'Giants', 'recent': [
                {'date': '2026-07-01', 'opp': 'LAD', 'rf': 2, 'ra': 4, 'res': 'L'}]}}
    got, out5 = _run(dict(_base, teams=teams), os.path.join(_tmp, 'o5'))
    _check('an old bundle still builds through the fallback', len(got) >= 1, '(%d rows)' % len(got))
    _check('and the fallback says so in the log', 'fell back' in out5)
    _check('and the same game from two clubs is still one row', len(got) == 1,
           '(%d rows)' % len(got))

    shutil.rmtree(_tmp, ignore_errors=True)
    print('\nselftest: ' + ('FAILED - ' + ', '.join(_fails) if _fails else 'ALL PASS ok'))
    sys.exit(1 if _fails else 0)

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
         'bvp': b.get('bvp'), 'h2h': b.get('h2h'), 'statcast': b.get('statcast'), 'vsPitch': b.get('vsPitch')}
    for k, v in s.items(): p[k] = v
    players.append(p)
    for gl in (b.get('gameLog') or []):
        yr, sk = ymd(gl.get('date', ''))
        row = {'Player': b['name'], 'Team': b.get('abbr'), 'Opp': gl.get('opp'),
               'venue': gl.get('venue'), 'home': gl.get('home'),
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
         'splitVsL': pt.get('splitVsL'), 'splitVsR': pt.get('splitVsR'), 'h2h': pt.get('h2h'), 'statcast': pt.get('statcast'), 'arsenal': pt.get('arsenal')}
    for k, v in s.items(): p[k] = v
    players.append(p)
    for gl in (pt.get('gameLog') or []):
        yr, sk = ymd(gl.get('date', ''))
        row = {'Player': pt['name'], 'Team': pt.get('abbr'), 'Opp': gl.get('opp'),
               'venue': gl.get('venue'), 'home': gl.get('home'),
               'Year': yr, 'Date': gl.get('date'), 'RoundName': str(sk), 'Week': sk, 'role': 'pitch'}
        for k in ('IP', 'K', 'BB', 'H', 'ER', 'HR', 'outs', 'win'):
            if k in gl: row[k] = gl[k]
        logs.append(row)

# ---- teams + dvp (vsStats = what a team allows) + results (from recent) ----
teams, dvp, results, seen_g = [], [], [], set()
for t in B.get('teams', {}).values():
    ab = t.get('abbr')
    _for = t.get('forStats') or {}
    _vs = t.get('vsStats') or {}
    trow = {'team': ab, 'name': t.get('name'), 'div': t.get('div'), 'park': t.get('park'),
            'kRate': t.get('kRate'), 'matches': t.get('games') or 0, 'forStats': _for, 'vsStats': _vs}
    for _k, _v in _for.items(): trow[_k] = _v            # team "For" per-game -> flat t[k]
    for _k, _v in _vs.items(): trow[_k + '_a'] = _v        # opponent "Allows" per-game -> t[k+'_a']
    teams.append(trow)
    vs = t.get('vsStats') or {}
    # A team whose stats fetch came back empty would otherwise land as a row of nulls and drag the
    # league average it is supposed to be ranked against. No numbers, no row.
    if any(vs.get(k) is not None for k in ('R', 'H', 'HR', 'BB', 'SO')):
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
        'parks': B.get('parks'), 'trends': B.get('trends'), 'standings': B.get('standings'), 'leagueLeaders': B.get('leagueLeaders'),
        'summary': {'players': len(players), 'gamelogs': len(logs), 'batters': len(B.get('batters', {})), 'pitchers': len(B.get('pitchers', {}))}}

# Auth: the unified shell reads meta.password_hash for the weekly gate. Match the browser's
# TextEncoder hash (UTF-8, no trailing newline) exactly as AFL/NFL builds do.
_pw = (os.environ.get('MLB_PASSWORD') or '').strip()
if _pw:
    meta['password_hash'] = hashlib.sha256(_pw.encode('utf-8')).hexdigest()


def merge_keep(name, fresh, key):
    """Keep what a previous run knew about teams that are not playing today.

    The fetcher only pulls rosters and team rates for clubs on the current slate. Through the
    regular season that is most of the league and nobody notices; in the postseason it collapsed to
    two teams, so players.json held 28 players and every "1st in the league" badge meant first of
    28. The season-long pool is what those rankings are for, so a row is only replaced when this
    run has a newer version of it - never dropped because its team is idle.

    `key` is a field name, or a function returning the key - a GROUP key, because replacement is
    group-wise: every fresh row with that key is kept and every previous row with it is dropped.
    That is what makes doubleheaders survive. MLB gamelogs have no game id, so rows are grouped by
    player and date, and a player's two games on one day move together instead of one of them
    being silently discarded as a duplicate.

    Rows whose key is missing are passed through untouched; a prior file that cannot be read is
    ignored, so a bad merge can never be worse than the plain write.
    """
    kf = key if callable(key) else (lambda r: r.get(key))
    path = os.path.join(OUT, name)
    try:
        with open(path, encoding='utf-8') as fh:
            prev = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return fresh
    if not isinstance(prev, list) or not isinstance(fresh, list):
        return fresh
    out, seen = [], set()
    for row in fresh:
        k = kf(row) if isinstance(row, dict) else None
        if k is not None:
            seen.add(k)
        out.append(row)
    kept = 0
    for row in prev:
        if not isinstance(row, dict):
            continue
        k = kf(row)
        if k is None or k in seen:
            continue
        out.append(row)
        kept += 1
    if kept:
        print('  %s: kept %d row(s) from the previous build (teams not playing today)' % (name, kept))
    return out


def w(name, obj):
    json.dump(obj, open(os.path.join(OUT, name), 'w', encoding='utf-8'), separators=(',', ':'))

# the season-long pools survive a day when most of the league is idle
players = merge_keep('players.json', players, 'id') if players else players
teams = merge_keep('teams.json', teams, 'team')
dvp = merge_keep('dvp.json', dvp, 'team')
w('players.json', players); w('teams.json', teams)
# gamelogs split per season (Cloudflare Pages 25 MiB/file cap); shell loads meta.gamelogFiles
#
# THE GAMELOGS WERE NOT MERGED, and that is what collapsed the league. players, teams and dvp were
# all protected above, so teams.json kept its thirty clubs while gamelogs.json held two - a file
# big enough to look healthy, covering a World Series and nothing else. Every hit rate, streak and
# matchup in the MLB tool was computed from whichever clubs happened to be playing that day.
_glkey = lambda r: '%s|%s' % (r.get('Player'), r.get('Date'))
_by_year = {}
for _r in logs: _by_year.setdefault(_r['Year'], []).append(_r)
_gl_files = []
for _yr in sorted(_by_year, reverse=True):
    _fn = 'gamelogs_%s.json' % _yr
    w(_fn, merge_keep(_fn, _by_year[_yr], _glkey)); _gl_files.append(_fn)
w('gamelogs.json', merge_keep('gamelogs.json', _by_year.get(season, []), _glkey))
meta['gamelogFiles'] = _gl_files
# ---- RESULTS ---------------------------------------------------------------------------------
# `results` above is stitched out of each club's `recent` array - an eighteen-day window, eight
# games a club. As a form guide that is the right shape. As the file the SIMULATOR reads its
# scorelines from, it was never close: 86 games against roughly 2,400 played, so a scoreline could
# be priced for 3% of games and the panel hid itself for the rest, with nothing broken anywhere to
# say why.
#
# fetch_mlb.py now puts every finished game of the covered seasons into the bundle, from the
# schedule endpoint, with the real home side and the game's own id. Where that is present it is
# AUTHORITATIVE for the seasons it covers - no merge, because a full-season fetch cannot be
# improved by remembering what a narrower run thought. Seasons the fetch no longer reaches are
# kept untouched.
#
# The fallback keeps an older bundle working, and the merge key is fixed either way. It used to be
# `date|home|away`, which is orientation-dependent: because `recent` names whichever club it was
# read from as "home", one game arrived as both LAD|SF and SF|LAD and accumulated as two rows that
# disagreed with each other. The key is now the date plus the two clubs in a fixed order, so a game
# is one row whichever side reported it.
def _res_pairkey(r):
    a, b2 = str(r.get('home') or ''), str(r.get('away') or '')
    lo, hi = (a, b2) if a <= b2 else (b2, a)
    # `g` is 2 for the back half of a doubleheader: two games, one date, one pair of clubs
    return '%s|%s|%s|%s' % (r.get('date'), lo, hi, r.get('g') or 1)


_sched = B.get('results') or []
if _sched:
    _covered = set(r.get('season') for r in _sched)
    try:
        with open(os.path.join(OUT, 'results.json'), encoding='utf-8') as _fh:
            _prev = json.load(_fh) or []
        if not isinstance(_prev, list):
            _prev = []
    except Exception:
        _prev = []
    # anything from a season the fetch covered is superseded, INCLUDING the mis-oriented rows the
    # old build wrote - that is the migration, and it needs no separate pass
    _keep = [r for r in _prev if isinstance(r, dict) and r.get('season') not in _covered]
    _seen, results = set(), []
    for r in _sched + _keep:
        k = _res_pairkey(r)
        if k in _seen:
            continue
        _seen.add(k)
        results.append(r)
    results.sort(key=lambda r: (str(r.get('date') or ''), r.get('gameId') or 0))
    print('results: %d from the schedule (season %s), %d older rows kept'
          % (len(_sched), ','.join(str(x) for x in sorted(_covered, key=str)), len(_keep)))
else:
    # an older bundle with no schedule block: keep accumulating, with the key fixed
    results = merge_keep('results.json', results, _res_pairkey)
    print('results: no schedule in the bundle - fell back to the per-team recent scrape '
          '(%d rows). Re-run fetch_mlb.py to publish a full season.' % len(results))
w('dvp.json', dvp); w('results.json', results); w('fixture.json', fixture)
w('lineups.json', lineups); w('meta.json', meta)
w('teams_form.json', teams)   # shell computes WWWLW from results; teams carries the rest
# Placeholders only when the file doesn't exist yet. Writing [] every run WIPED the day's MLB odds
# (odds.json is written by the odds workflow) and the ESPN injury report on each data refresh -
# live prices survived only because the two workflows happened to run in the right order.
for empty in ('injury.json', 'odds.json', 'weather.json'):
    if not os.path.exists(os.path.join(OUT, empty)):
        w(empty, [])
print('players=%d (bat+pitch)  gamelogs=%d  fixture=%d  teams=%d  results=%d  dvp=%d' % (
    len(players), len(logs), len(fixture), len(teams), len(results), len(dvp)))
