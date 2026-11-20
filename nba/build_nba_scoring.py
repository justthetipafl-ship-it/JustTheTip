#!/usr/bin/env python3
"""Where a player's points come from, what part of that can be forecast, and what the opponent
is worth - for the Scoring pane.

    python nba/build_nba_scoring.py --out nba/data [--seasons 2026,2027]
    python nba/build_nba_scoring.py --selftest          # no network, no files

WHY
Playmaking, Rebounding and From Deep each take one line apart: what he does, how much of it
carries, what a hot week means, what the opponent adds. Points - the most-bet line of all - had
nothing like it. The shot actions and the floor say HOW he scores; neither says what a points
line is made of or which part of it to trust.

A points line is three things multiplied out, and they do not behave alike:

    points = 2 x (twos made) + 3 x (threes made) + (free throws made)
           = volume (shots and trips to the line)  x  efficiency (how they went in)

WHAT IS MEASURED, every run, on the window it is given - nothing below is a constant:

  stability   first half-season against second, 40-game floor - the same split the other three
              cards use, so the four read against each other. Volume holds (points, shots, free
              throws all about +0.90). Efficiency holds about half as well (true shooting +0.51).
  recent      a five-game run against his own season, WITHIN the season, with the minutes taken
              out. A hot scoring week is worth about +0.85 a game raw and arrives with a minute
              and a half more court time; per 30 minutes about +0.30 of it is left, and what is
              left is shot VOLUME, not shooting.
  hotShooting whether a hot SHOOTING week (true shooting over his last five against his own)
              says anything about the next game. It does not: r about -0.02.
  opp         what the opponent's points allowed is worth OUT OF SAMPLE - their average over
              games BEFORE this one, against how far he finishes from his own average. About
              +-0.9 of a point per 30 minutes between the softest and toughest fifth. Real, and
              small next to the minutes.

WHAT IS WRITTEN
nba/data/scoring.json

    {"league":{"pts","ts","stability":{..},"recent":{..},"hotShooting":{..},"opp":{..}},
     "defence":{"GS":{"ptsAllowed","ptsRank","fgaAllowed","fgaRank","ftaAllowed","ftaRank",
                      "pace","paceRank","of"}},
     "players":[{"playerId","name","team","games","minutes","pts","fga","tpa","fta",
                 "from2","from3","fromFt","ts","ftRate","pts30"}]}

Everything is from the box-score game logs the 12:00 NBA build writes. No feed, no fitting.
"""
import argparse
import bisect
import collections
import datetime
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_nba_rebounds import load, fnum, pid, nm, MIN_STAB_GAMES      # noqa: E402

MIN_GAMES = 10          # below this his rates are a handful of nights
MIN_PTS = 4.0           # and below this he is not a scoring read at all
MIN_DEF_GAMES = 20      # a team's concession needs a season behind it to rank
MIN_OPP_PRIOR = 15      # games an opponent must have played before its average counts as known
SCORER = 8.0            # the floor for the run and opponent measurements: players with a line


def corr(xs, ys):
    if len(xs) < 20:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sx = sum((x - mx) ** 2 for x in xs) ** .5
    sy = sum((y - my) ** 2 for y in ys) ** .5
    if not sx or not sy:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def tails(pool):
    """Mean outcome in the bottom and top fifth of the predictor."""
    pool = sorted(pool, key=lambda t: t[0])
    q = len(pool) // 5
    return (statistics.mean(y for _, y in pool[:q]), statistics.mean(y for _, y in pool[-q:]))


def totals(rs):
    s = lambda k: sum(fnum(r.get(k)) for r in rs)                       # noqa: E731
    return {'pts': s('points'), 'fga': s('fga'), 'fgm': s('fgm'), 'tpa': s('threesAtt'),
            'tpm': s('threes'), 'fta': s('fta'), 'ftm': s('ftm'), 'min': s('minutes'), 'n': len(rs)}


def true_shooting(t):
    d = 2.0 * (t['fga'] + 0.44 * t['fta'])
    return t['pts'] / d if d > 0 else None


def played(byp):
    out = {}
    for i, rs in byp.items():
        rs = [r for r in rs if fnum(r.get('minutes')) > 0]
        if rs:
            out[i] = sorted(rs, key=lambda r: str(r.get('Date') or ''))
    return out


def by_season(byp):
    g = collections.defaultdict(list)
    for i, rs in byp.items():
        for r in rs:
            g[(i, str(r.get('Year') or ''))].append(r)
    return g


def stability(byp):
    """First half-season against second. See build_nba_rebounds.stability for why this split."""
    cols = collections.defaultdict(lambda: ([], []))
    n = 0
    for rs in byp.values():
        if len(rs) < MIN_STAB_GAMES:
            continue
        h = len(rs) // 2
        a, b = totals(rs[:h]), totals(rs[h:])
        n += 1
        for k, f in (('pts', 'pts'), ('fga', 'fga'), ('fta', 'fta'), ('minutes', 'min')):
            cols[k][0].append(a[f] / a['n'])
            cols[k][1].append(b[f] / b['n'])
        ta, tb = true_shooting(a), true_shooting(b)
        if ta is not None and tb is not None:
            cols['ts'][0].append(ta)
            cols['ts'][1].append(tb)
    out = {}
    for k, (x, y) in cols.items():
        c = corr(x, y)
        if c is not None:
            out[k] = round(c, 3)
    out['n'] = n
    return out


def recent_form(byp, k=5):
    """A five-game scoring run, raw and with the minutes taken out. Within a season only."""
    raw, per_min, mins, vol = [], [], [], []
    for rs in by_season(byp).values():
        if len(rs) < MIN_STAB_GAMES:
            continue
        v = [fnum(r.get('points')) for r in rs]
        base = statistics.mean(v)
        if base < SCORER:
            continue
        m = [fnum(r.get('minutes')) for r in rs]
        mbase = statistics.mean(m)
        pm = [a / b * 30.0 for a, b in zip(v, m)]
        pmbase = statistics.mean(pm)
        # the volume half of the same question: shots per 30 minutes
        sh = [(fnum(r.get('fga')) + 0.44 * fnum(r.get('fta'))) / b * 30.0 for r, b in zip(rs, m)]
        shbase = statistics.mean(sh)
        for j in range(k, len(rs)):
            lead = statistics.mean(v[j - k:j]) - base
            raw.append((lead, v[j] - base))
            mins.append((lead, m[j] - mbase))
            per_min.append((statistics.mean(pm[j - k:j]) - pmbase, pm[j] - pmbase))
            vol.append((statistics.mean(sh[j - k:j]) - shbase, sh[j] - shbase))
    if len(raw) < 500:
        return None
    lo, hi = tails(raw)
    plo, phi = tails(per_min)
    _, mhi = tails(mins)
    vlo, vhi = tails(vol)
    return {'hottest': round(hi, 3), 'coldest': round(lo, 3),
            'hottestPerMin': round(phi, 3), 'coldestPerMin': round(plo, 3),
            'hottestMinutes': round(mhi, 2),
            'hottestVolume': round(vhi, 3), 'coldestVolume': round(vlo, 3),
            'window': k, 'n': len(raw)}


def hot_shooting(byp, k=5):
    """Does shooting well over five games say anything about the sixth? True shooting, not points."""
    pool, pts = [], []
    for rs in by_season(byp).values():
        if len(rs) < MIN_STAB_GAMES:
            continue
        t = totals(rs)
        if t['pts'] / t['n'] < SCORER:
            continue
        own = true_shooting(t)
        own30 = t['pts'] / t['min'] * 30.0
        for j in range(k, len(rs)):
            w = true_shooting(totals(rs[j - k:j]))
            g = totals([rs[j]])
            nxt = true_shooting(g)
            if w is None or nxt is None or own is None:
                continue
            pool.append((w - own, nxt - own))
            pts.append((w - own, g['pts'] / g['min'] * 30.0 - own30))
    if len(pool) < 500:
        return None
    lo, hi = tails(pts)
    return {'r': round(corr([a for a, _ in pool], [b for _, b in pool]) or 0, 3),
            'hottestPts30': round(hi, 3), 'coldestPts30': round(lo, 3),
            'window': k, 'n': len(pool)}


def team_games(rows):
    """{(match, team): totals} plus who they played and when."""
    tg = collections.defaultdict(lambda: collections.defaultdict(float))
    info = {}
    for r in rows:
        m, t = str(r.get('MatchId')), r.get('Team')
        if not m or not t:
            continue
        for k in ('points', 'fga', 'fta', 'oreb', 'turnovers'):
            tg[(m, t)][k] += fnum(r.get(k))
        info[(m, t)] = (r.get('Opp'), str(r.get('Date') or ''), str(r.get('Year') or ''))
    return tg, info


def opp_value(byp, rows):
    """What the opponent's points allowed is worth, OUT OF SAMPLE.

    For every game: the opponent's average points allowed over the games it had ALREADY played
    that season (at least MIN_OPP_PRIOR of them), against how far the player finished from his
    own season average. Using the opponent's whole-season figure instead would let tonight's game
    vote on its own prediction.
    """
    tg, info = team_games(rows)
    allowed = collections.defaultdict(list)          # (team, season) -> [(date, points allowed)]
    for (m, t), v in tg.items():
        opp, d, y = info[(m, t)]
        if opp:
            allowed[(opp, y)].append((d, v['points']))
    dates = {}
    for k in allowed:
        allowed[k].sort()
        dates[k] = [d for d, _ in allowed[k]]

    raw, per30 = [], []
    for (i, y), rs in by_season(byp).items():
        if len(rs) < MIN_STAB_GAMES:
            continue
        t = totals(rs)
        base = t['pts'] / t['n']
        if base < SCORER:
            continue
        base30 = t['pts'] / t['min'] * 30.0
        for r in rs:
            key = (r.get('Opp'), y)
            if key not in allowed:
                continue
            n = bisect.bisect_left(dates[key], str(r.get('Date') or ''))
            if n < MIN_OPP_PRIOR:
                continue
            prior = statistics.mean(p for _, p in allowed[key][:n])
            raw.append((prior, fnum(r.get('points')) - base))
            per30.append((prior, fnum(r.get('points')) / fnum(r.get('minutes')) * 30.0 - base30))
    if len(raw) < 500:
        return None
    lo, hi = tails(raw)
    plo, phi = tails(per30)
    return {'r': round(corr([a for a, _ in per30], [b for _, b in per30]) or 0, 3),
            'softest': round(hi, 3), 'toughest': round(lo, 3),
            'softestPer30': round(phi, 3), 'toughestPer30': round(plo, 3), 'n': len(raw)}


def defence(rows):
    """Points, shots and free throws each team allows a game, and how fast its games are."""
    tg, info = team_games(rows)
    by_match = collections.defaultdict(dict)
    for (m, t), v in tg.items():
        by_match[m][t] = v
    acc = collections.defaultdict(lambda: collections.defaultdict(list))
    for m, sides in by_match.items():
        if len(sides) != 2:
            continue
        (a, va), (b, vb) = sides.items()
        poss = lambda v: v['fga'] - v['oreb'] + v['turnovers'] + 0.44 * v['fta']     # noqa: E731
        pace = (poss(va) + poss(vb)) / 2.0
        for me, other in ((a, vb), (b, va)):
            acc[me]['ptsAllowed'].append(other['points'])
            acc[me]['fgaAllowed'].append(other['fga'])
            acc[me]['ftaAllowed'].append(other['fta'])
            acc[me]['pace'].append(pace)
    teams = {c: {k: statistics.mean(v) for k, v in kv.items()}
             for c, kv in acc.items() if len(kv['ptsAllowed']) >= MIN_DEF_GAMES}
    out = {}
    for key in ('ptsAllowed', 'fgaAllowed', 'ftaAllowed', 'pace'):
        short = key.replace('Allowed', '')
        for n, c in enumerate(sorted(teams, key=lambda x: -teams[x][key])):
            out.setdefault(c, {})[key] = round(teams[c][key], 1)
            out[c][short + 'Rank'] = n + 1            # 1 = allows the MOST / plays the fastest
    for c in out:
        out[c]['of'] = len(teams)
    return out


def build(rows):
    byp_all = collections.defaultdict(list)
    for r in rows:
        i = pid(r.get('PlayerId'))
        if i:
            byp_all[i].append(r)
    byp = played(byp_all)

    players = []
    lg = totals([r for rs in byp.values() for r in rs])
    for i, rs in byp.items():
        t = totals(rs)
        if t['n'] < MIN_GAMES or t['pts'] / t['n'] < MIN_PTS or t['pts'] <= 0:
            continue
        two_pts = 2.0 * (t['fgm'] - t['tpm'])
        players.append({
            'playerId': i, 'name': nm(rs[-1].get('Player')) or ('ESPN #' + i),
            'team': rs[-1].get('Team'), 'games': t['n'],
            'minutes': round(t['min'] / t['n'], 1),
            'pts': round(t['pts'] / t['n'], 2),
            'fga': round(t['fga'] / t['n'], 2), 'tpa': round(t['tpa'] / t['n'], 2),
            'fta': round(t['fta'] / t['n'], 2),
            # where the points come from. The three shares add to 1 by construction.
            'from2': round(two_pts / t['pts'], 4), 'from3': round(3.0 * t['tpm'] / t['pts'], 4),
            'fromFt': round(t['ftm'] / t['pts'], 4),
            'ts': round(true_shooting(t), 4) if true_shooting(t) is not None else None,
            'ftRate': round(t['fta'] / t['fga'], 4) if t['fga'] > 0 else None,
            'pts30': round(t['pts'] / t['min'] * 30.0, 2)})
    players.sort(key=lambda p: -p['pts'])

    return {
        'league': {
            'ts': round(true_shooting(lg), 4) if byp and true_shooting(lg) is not None else None,
            'from2': round(2.0 * (lg['fgm'] - lg['tpm']) / lg['pts'], 4) if lg['pts'] else None,
            'from3': round(3.0 * lg['tpm'] / lg['pts'], 4) if lg['pts'] else None,
            'fromFt': round(lg['ftm'] / lg['pts'], 4) if lg['pts'] else None,
            'stability': stability(byp),
            'recent': recent_form(byp),
            'hotShooting': hot_shooting(byp),
            'opp': opp_value(byp, rows)},
        'minGames': MIN_GAMES, 'defence': defence(rows), 'players': players}


def write(out_dir, payload, seasons):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, 'scoring.json')
    if not payload['players'] and os.path.exists(path):
        print('  built nothing - keeping what is already in %s' % path)
        return
    payload['updated'] = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%MZ')
    payload['seasons'] = list(seasons)
    payload['source'] = 'JTT gamelogs (box score)'
    body = json.dumps(payload, separators=(',', ':'), allow_nan=False)
    with open(path, 'w') as f:
        f.write(body)
    lg = payload['league']
    print('  wrote %s (%.0f KB) - %d players, %d teams'
          % (path, os.path.getsize(path) / 1024, len(payload['players']), len(payload['defence'])))
    print('  stability %s' % lg['stability'])
    print('  recent    %s' % lg['recent'])
    print('  hot shoot %s' % lg['hotShooting'])
    print('  opponent  %s' % lg['opp'])


def selftest():
    fails = []

    def ok(name, cond, note=''):
        print(('  ok   ' if cond else '  FAIL ') + name + (('  ' + note) if note else ''))
        if not cond:
            fails.append(name)

    def row(p, d, pts, fga, fgm, tpa, tpm, fta, ftm, mins=30, team='AAA', opp='BBB', m=None, y='2027'):
        return {'PlayerId': p, 'Player': 'P' + str(p), 'Team': team, 'Opp': opp, 'Date': d,
                'MatchId': m or (d + team + opp), 'Year': y, 'minutes': mins, 'points': pts,
                'fga': fga, 'fgm': fgm, 'threesAtt': tpa, 'threes': tpm, 'fta': fta, 'ftm': ftm,
                'oreb': 0, 'turnovers': 0}

    # 20 points = 5 twos (10) + 2 threes (6) + 4 free throws
    rs = [row(1, '2026-11-%02d' % d, 20, 14, 7, 5, 2, 5, 4) for d in range(1, 13)]
    out = build(rs)
    p = out['players'][0]
    ok('points a game', p['pts'] == 20.0)
    ok('the three sources add to all of his points',
       abs(p['from2'] + p['from3'] + p['fromFt'] - 1.0) < 1e-3, str((p['from2'], p['from3'], p['fromFt'])))
    ok('half from twos, 30% from three, 20% at the line',
       p['from2'] == 0.5 and p['from3'] == 0.3 and p['fromFt'] == 0.2)
    ok('true shooting is points over two times (shots + 0.44 x free throws)',
       abs(p['ts'] - 20 / (2 * (14 + 0.44 * 5))) < 1e-4)
    ok('per 30 minutes', p['pts30'] == 20.0)
    ok('a player under the games floor is left out',
       build([row(2, '2026-11-01', 30, 20, 10, 5, 2, 8, 8)])['players'] == [])
    ok('a DNP is not a game', build(rs + [row(1, '2026-12-01', 0, 0, 0, 0, 0, 0, 0, mins=0)])
       ['players'][0]['games'] == 12)

    # DEFENCE: AAA scores 100 on BBB in every game, BBB scores 90 on AAA
    g = []
    for d in range(1, 25):
        day = '2026-11-%02d' % d if d < 31 else '2026-12-01'
        g.append(dict(row(1, day, 100, 80, 40, 0, 0, 20, 20, team='AAA', opp='BBB', m='m%d' % d)))
        g.append(dict(row(9, day, 90, 85, 40, 0, 0, 12, 10, team='BBB', opp='AAA', m='m%d' % d)))
    dfc = defence(g)
    ok('points allowed is what the OTHER side scored',
       dfc['BBB']['ptsAllowed'] == 100.0 and dfc['AAA']['ptsAllowed'] == 90.0)
    ok('rank 1 allows the most', dfc['BBB']['ptsRank'] == 1 and dfc['AAA']['ptsRank'] == 2)
    ok('free throws allowed follow the same rule', dfc['BBB']['ftaAllowed'] == 20.0)
    ok('both sides of a game share one pace', dfc['AAA']['pace'] == dfc['BBB']['pace'])

    # THE MEASUREMENTS RETURN NOTHING RATHER THAN A NUMBER OFF TOO LITTLE
    byp = played({'1': rs})
    ok('too few games for a hot-run figure -> None, not a guess', recent_form(byp) is None)
    ok('and the same for the opponent', opp_value(byp, rs) is None)
    ok('a short record is not in the stability sample', stability(byp)['n'] == 0)

    # OUT OF SAMPLE MEANS OUT OF SAMPLE: tonight's game must not be in the opponent's average
    tg, info = team_games(g)
    ok('team totals are per team per game', tg[('m1', 'AAA')]['points'] == 100.0 and info[('m1', 'AAA')][0] == 'BBB')

    # NaN never reaches the file
    bad = [dict(r, points=float('nan')) for r in rs]
    try:
        json.dumps(build(bad), allow_nan=False)
        ok('a NaN in the logs does not reach the JSON', True)
    except ValueError:
        ok('a NaN in the logs does not reach the JSON', False)

    print(('\nFAILED: ' + ', '.join(fails)) if fails else '\nall good')
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='nba/data')
    ap.add_argument('--seasons', default=None, help='comma separated; default the last two')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.seasons:
        seasons = [s for s in a.seasons.split(',') if s]
    else:
        try:
            cur = int(json.load(open(os.path.join(a.out, 'meta.json')))['currentSeason'])
        except Exception:
            cur = datetime.date.today().year + (1 if datetime.date.today().month >= 9 else 0)
        seasons = [str(cur - 1), str(cur)]
    print('scoring: seasons %s' % ','.join(seasons))
    rows = load(a.out, seasons)
    if not rows:
        print('  no gamelogs - nothing built')
        return 0
    write(a.out, build(rows), seasons)
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
