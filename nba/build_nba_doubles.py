#!/usr/bin/env python3
"""Double-doubles and triple-doubles: how often, what decides them, and what moves the chance.

    python nba/build_nba_doubles.py --out nba/data [--seasons 2026,2027]
    python nba/build_nba_doubles.py --selftest          # no network, no files

WHY
The odds feed carries no double-double or triple-double market for the NBA (data/odds_markets.json
lists every key it does carry), so the Double Trouble and Triple Threat tiles could never show a
price and came down to one number: how many of his last 20. That number is real, but it is the
least of what the game logs know. A double-double is two lines landing in one game, and most of
the research is in which one is the doubt, how close he comes when he misses, and what the night
does to his chance.

WHAT IS MEASURED, every run, on the window it is given - nothing below is a constant:

  stability  first half-season against second, 40-game floor - the same split the other cards
             use. A player's double-double rate carries about as well as his rebounds do.
  forecast   which record predicts the NEXT game best, scored by Brier among real candidates:
             everything this season, his last 20, last 10, last 5. The longest record wins and
             the shortest is clearly worst - a run of three in a row is not a read.
  shrink     how much a record overstates. Candidates are picked BECAUSE their rate is high, so
             the rate has luck in it; next game they land about nine-tenths as often as their
             record says. The "fair" figure on the page is the record with that taken off.
  minutes    the biggest mover there is. Four minutes under his average and the chance is less
             than half his record; four over and it is a quarter higher.
  opp        the opponent matters far more here than it does for points. Against the fifth of
             defences that concede the fewest double-doubles, candidates land about ten points
             of probability under their record - measured out of sample, on what each defence
             had conceded BEFORE that game.
  legs       whether the two legs travel together. They do, slightly: both land about 6% more
             often than multiplying the two rates says, so a two-leg multi priced as independent
             is a touch short of the true chance.

WHAT IS WRITTEN
nba/data/doubles.json

    {"league":{"base","stability":{..},"forecast":{..},"shrink":{..},"minutes":[..],
               "opp":{..},"legs":{..}},
     "defence":{"GS":{"dd","ddRank","of"}},
     "players":[{"playerId","name","team","games","minutes","dd","ddRate","td","tdRate","fair",
                 "legs":[{"k","rate","avg"}],"hard","near","nearN"}]}

Everything is from the box-score game logs the 12:00 NBA build writes. No feed, no fitting beyond
one straight line.
"""
import argparse
import collections
import datetime
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_nba_rebounds import load, fnum, pid, nm, MIN_STAB_GAMES      # noqa: E402

CATS = ['points', 'rebounds', 'assists', 'steals', 'blocks']
MIN_GAMES = 10          # below this his rate is a handful of nights
MIN_RATE = 0.08         # and below this he is not a double-double read at all
CAND = 0.20             # a "candidate" for the measurements: one in five or better, so far
MIN_PRIOR = 20          # games behind him before his record is used to forecast the next one
MIN_OPP_PRIOR = 10      # games an opponent must have played before its concession counts
MIN_DEF_GAMES = 15      # a team's concession needs this many games to rank
MIN_BAND = 60           # a measured band is only written with this many games in it
MIN_BANDS = [('4+ under', -99, -4), ('1.5 to 4 under', -4, -1.5), ('about his average', -1.5, 1.5),
             ('1.5 to 4 over', 1.5, 4), ('4+ over', 4, 99)]


def tens(r):
    return sum(1 for k in CATS if fnum(r.get(k)) >= 10)


def corr(xs, ys):
    if len(xs) < 20:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sx = sum((x - mx) ** 2 for x in xs) ** .5
    sy = sum((y - my) ** 2 for y in ys) ** .5
    if not sx or not sy:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def r3(v):
    return None if v is None else round(v, 3)


def seasons_of(rows):
    """{(player, season): [rows in date order]} for games he actually played."""
    g = collections.defaultdict(list)
    for r in rows:
        if fnum(r.get('minutes')) > 0:
            i = pid(r.get('PlayerId'))
            if i:
                g[(i, str(r.get('Year') or ''))].append(r)
    for k in g:
        g[k].sort(key=lambda r: str(r.get('Date') or ''))
    return g


def stability(by):
    a, b = [], []
    for g in by.values():
        if len(g) < MIN_STAB_GAMES:
            continue
        h = len(g) // 2
        a.append(sum(tens(r) >= 2 for r in g[:h]) / h)
        b.append(sum(tens(r) >= 2 for r in g[h:]) / (len(g) - h))
    cand = [(x, y) for x, y in zip(a, b) if x >= 0.1 or y >= 0.1]
    return {'r': r3(corr(a, b)), 'n': len(a),
            'rCand': r3(corr([x for x, _ in cand], [y for _, y in cand])), 'nCand': len(cand)}


def opp_prior(rows):
    """{(opponent, season, date): double-doubles conceded per game BEFORE that date}."""
    per = collections.defaultdict(lambda: collections.defaultdict(int))
    for r in rows:
        if fnum(r.get('minutes')) > 0:
            per[(str(r.get('Opp') or ''), str(r.get('Year') or ''))][str(r.get('Date') or '')] += (tens(r) >= 2)
    out = {}
    for (opp, yr), days in per.items():
        tot = 0
        for i, d in enumerate(sorted(days)):
            if i >= MIN_OPP_PRIOR:
                out[(opp, yr, d)] = tot / i
            tot += days[d]
    return out


def observe(by, prior):
    """One row per candidate game: what was known beforehand, and whether it landed."""
    obs = []
    for (i, yr), g in by.items():
        for n in range(MIN_PRIOR, len(g)):
            before = g[:n]
            hits = [1 if tens(r) >= 2 else 0 for r in before]
            season = sum(hits) / len(hits)
            if season < CAND:
                continue
            mins = statistics.mean(fnum(r.get('minutes')) for r in before)
            obs.append({
                'y': 1 if tens(g[n]) >= 2 else 0, 'season': season,
                'l20': sum(hits[-20:]) / 20.0, 'l10': sum(hits[-10:]) / 10.0, 'l5': sum(hits[-5:]) / 5.0,
                'dmin': fnum(g[n].get('minutes')) - mins,
                'opp': prior.get((str(g[n].get('Opp') or ''), yr, str(g[n].get('Date') or ''))),
            })
    return obs


def measure(obs):
    out = {'forecast': {}, 'shrink': {}, 'minutes': [], 'opp': {}}
    if len(obs) < 200:
        return out
    n = len(obs)
    brier = lambda k: sum((o[k] - o['y']) ** 2 for o in obs) / n          # noqa: E731
    out['forecast'] = {'season': round(brier('season'), 4), 'last20': round(brier('l20'), 4),
                       'last10': round(brier('l10'), 4), 'last5': round(brier('l5'), 4),
                       'n': n, 'rate': r3(sum(o['y'] for o in obs) / n),
                       'said': r3(sum(o['season'] for o in obs) / n)}
    # one straight line: what actually happened, on what his record said
    xs, ys = [o['season'] for o in obs], [o['y'] for o in obs]
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else 1.0
    out['shrink'] = {'slope': r3(slope), 'intercept': r3(my - slope * mx), 'n': n}
    for lab, lo, hi in MIN_BANDS:
        s = [o for o in obs if lo <= o['dmin'] < hi]
        if len(s) >= MIN_BAND:
            out['minutes'].append({'label': lab, 'said': r3(sum(o['season'] for o in s) / len(s)),
                                   'actual': r3(sum(o['y'] for o in s) / len(s)), 'n': len(s)})
    known = sorted([o for o in obs if o['opp'] is not None], key=lambda o: o['opp'])
    q = len(known) // 5
    if q >= MIN_BAND:
        gap = lambda s: sum(o['y'] - o['season'] for o in s) / len(s)       # noqa: E731
        # every candidate lands a little under his record (see shrink), so the opponent's effect
        # is the gap in each fifth MEASURED AGAINST THE MIDDLE, not against zero
        mid = gap(known[2 * q:3 * q])
        out['opp'] = {'toughest': r3(gap(known[:q]) - mid), 'softest': r3(gap(known[-q:]) - mid),
                      'toughestRaw': r3(gap(known[:q])), 'softestRaw': r3(gap(known[-q:])),
                      'n': len(known)}
    return out


def top_two(g):
    means = {c: statistics.mean(fnum(r.get(c)) for r in g) for c in CATS}
    return sorted(CATS, key=lambda c: -means[c])[:2]


def legs_together(by):
    """Do his two legs land together more often than chance? Median of both / (a x b)."""
    ratios = []
    for g in by.values():
        if len(g) < 30 or sum(tens(r) >= 2 for r in g) / len(g) < CAND:
            continue
        a, b = top_two(g)
        pa = sum(fnum(r.get(a)) >= 10 for r in g) / len(g)
        pb = sum(fnum(r.get(b)) >= 10 for r in g) / len(g)
        both = sum(fnum(r.get(a)) >= 10 and fnum(r.get(b)) >= 10 for r in g) / len(g)
        if pa * pb > 0:
            ratios.append(both / (pa * pb))
    return {'ratio': r3(statistics.median(ratios)) if len(ratios) >= 10 else None, 'n': len(ratios)}


def defence(rows):
    """Double-doubles each team concedes a game. Rank 1 concedes the most."""
    per = collections.defaultdict(lambda: collections.defaultdict(int))
    for r in rows:
        if fnum(r.get('minutes')) > 0 and r.get('Opp'):
            per[str(r.get('Opp'))][(str(r.get('Year')), str(r.get('Date')))] += (tens(r) >= 2)
    rec = {t: {'dd': round(sum(d.values()) / len(d), 2), 'games': len(d)}
           for t, d in per.items() if len(d) >= MIN_DEF_GAMES}
    order = sorted(rec, key=lambda t: -rec[t]['dd'])
    for i, t in enumerate(order):
        rec[t]['ddRank'] = i + 1
        rec[t]['of'] = len(order)
    return rec


def player_rows(rows, shrink):
    byp = collections.defaultdict(list)
    for r in rows:
        if fnum(r.get('minutes')) > 0:
            i = pid(r.get('PlayerId'))
            if i:
                byp[i].append(r)
    slope = shrink.get('slope') if shrink.get('slope') is not None else 1.0
    icpt = shrink.get('intercept') if shrink.get('intercept') is not None else 0.0
    out = []
    for i, g in byp.items():
        if len(g) < MIN_GAMES:
            continue
        g.sort(key=lambda r: str(r.get('Date') or ''))
        dd = sum(tens(r) >= 2 for r in g)
        td = sum(tens(r) >= 3 for r in g)
        rate = dd / len(g)
        if rate < MIN_RATE and not td:
            continue
        legs = sorted(({'k': c, 'rate': round(sum(fnum(r.get(c)) >= 10 for r in g) / len(g), 3),
                        'avg': round(statistics.mean(fnum(r.get(c)) for r in g), 2)} for c in CATS),
                      key=lambda x: (-x['rate'], -x['avg']))[:3]
        hard = legs[1]['k']
        # HOW CLOSE WHEN HE MISSES: of the games with no double-double, how often the deciding
        # leg finished on 8 or 9. A miss by one rebound and a miss by six are not the same miss.
        misses = [r for r in g if tens(r) < 2]
        near = sum(1 for r in misses if 8 <= fnum(r.get(hard)) < 10)
        out.append({
            'playerId': i, 'name': nm(g[-1].get('Player')), 'team': nm(g[-1].get('Team')),
            'games': len(g), 'minutes': round(statistics.mean(fnum(r.get('minutes')) for r in g), 1),
            'dd': dd, 'ddRate': round(rate, 3), 'td': td, 'tdRate': round(td / len(g), 3),
            'fair': round(max(0.0, min(0.98, icpt + slope * rate)), 3),
            'legs': legs, 'hard': hard,
            'near': round(near / len(misses), 3) if misses else None, 'nearN': len(misses),
        })
    out.sort(key=lambda r: -r['ddRate'])
    return [r for r in out if r['name']]


def build(rows):
    by = seasons_of(rows)
    obs = observe(by, opp_prior(rows))
    m = measure(obs)
    played = [r for r in rows if fnum(r.get('minutes')) > 0]
    league = {'base': r3(sum(tens(r) >= 2 for r in played) / len(played)) if played else None,
              'stability': stability(by), 'legs': legs_together(by)}
    league.update(m)
    return {'league': league, 'minGames': MIN_GAMES, 'defence': defence(rows),
            'players': player_rows(rows, m.get('shrink') or {})}


def write(out_dir, payload, seasons):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, 'doubles.json')
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
    for k in ('stability', 'forecast', 'shrink', 'opp', 'legs'):
        print('  %-9s %s' % (k, lg.get(k)))
    for b in lg.get('minutes') or []:
        print('  minutes   %-18s said %.3f actual %.3f (n=%d)' % (b['label'], b['said'], b['actual'], b['n']))


def selftest():
    fails = []

    def ok(name, cond, note=''):
        print(('  ok   ' if cond else '  FAIL ') + name + (('  ' + note) if note else ''))
        if not cond:
            fails.append(name)

    def row(i, d, p, r, a, m=30, opp='X', yr='2027', s=0, b=0):
        return {'PlayerId': i, 'Player': 'P' + str(i), 'Team': 'T', 'Opp': opp, 'Year': yr,
                'Date': '2027-01-%02d' % d if d <= 31 else '2027-02-%02d' % (d - 31),
                'points': p, 'rebounds': r, 'assists': a, 'steals': s, 'blocks': b, 'minutes': m}

    ok('ten in two categories is a double-double', tens(row(1, 1, 10, 10, 0)) == 2)
    ok('nine is not ten', tens(row(1, 1, 30, 9, 9)) == 1)
    ok('steals and blocks count', tens(row(1, 1, 4, 3, 2, s=10, b=10)) == 2)
    ok('three is a triple', tens(row(1, 1, 12, 11, 10)) == 3)

    # a player who lands it in 6 of 12, missing by one rebound in half his misses
    g = ([row(7, d, 20, 12, 3) for d in range(1, 7)] + [row(7, d, 20, 9, 3) for d in range(7, 10)]
         + [row(7, d, 20, 4, 3) for d in range(10, 13)])
    pr = player_rows(g, {'slope': 0.9, 'intercept': 0.0})
    ok('one player comes out', len(pr) == 1)
    p = pr[0]
    ok('his rate is 6 of 12', p['dd'] == 6 and p['ddRate'] == 0.5 and p['games'] == 12)
    ok('the deciding leg is rebounds, not the points he always gets', p['hard'] == 'rebounds')
    ok('legs are in order of how often he reaches ten', [x['k'] for x in p['legs']][:2] == ['points', 'rebounds'])
    ok('half his misses were on 8 or 9', p['near'] == 0.5 and p['nearN'] == 6)
    ok('the fair figure is the record with the shrink taken off', p['fair'] == 0.45)
    ok('a player who never gets near is left out',
       player_rows([row(8, d, 6, 2, 1) for d in range(1, 13)], {}) == [])
    ok('a did-not-play is not a game', player_rows(
        [row(9, d, 20, 12, 3) for d in range(1, 11)] + [row(9, 11, 0, 0, 0, m=0)], {})[0]['games'] == 10)
    bad = [dict(row(5, d, 20, 12, 3), Player=float('nan')) for d in range(1, 12)]
    ok('a NaN name does not reach the file', player_rows(bad, {}) == [])

    # the opponent's concession is what it had conceded BEFORE the game, never including it
    rows = []
    for d in range(1, 16):
        rows.append(row(1, d, 20, 12, 3, opp='SOFT'))
        rows.append(row(2, d, 20, 3, 3, opp='HARD'))
    pri = opp_prior(rows)
    ok('no concession until the opponent has enough games behind it',
       ('SOFT', '2027', '2027-01-05') not in pri)
    ok('then it is the average of the games before', pri[('SOFT', '2027', '2027-01-12')] == 1.0
       and pri[('HARD', '2027', '2027-01-12')] == 0.0)

    d = defence(rows + [row(1, 16, 20, 12, 3, opp='SOFT')])
    ok('rank 1 concedes the most', d['SOFT']['ddRank'] == 1 and d['HARD']['ddRank'] == 2 and d['SOFT']['of'] == 2)

    # measure(): shrink is the straight line of outcome on record
    obs = ([{'y': 1, 'season': 0.8, 'l20': 0.8, 'l10': 0.8, 'l5': 0.4, 'dmin': 0, 'opp': None}] * 144
           + [{'y': 0, 'season': 0.8, 'l20': 0.8, 'l10': 0.8, 'l5': 1.0, 'dmin': 0, 'opp': None}] * 56
           + [{'y': 1, 'season': 0.3, 'l20': 0.3, 'l10': 0.3, 'l5': 0.0, 'dmin': -5, 'opp': None}] * 54
           + [{'y': 0, 'season': 0.3, 'l20': 0.3, 'l10': 0.3, 'l5': 0.6, 'dmin': -5, 'opp': None}] * 146)
    m = measure(obs)
    ok('the line through 0.3 -> 0.27 and 0.8 -> 0.72 has slope 0.9', m['shrink']['slope'] == 0.9)
    ok('the full record out-forecasts the last five', m['forecast']['season'] < m['forecast']['last5'])
    ok('minute bands carry what was said and what happened',
       any(b['label'] == '4+ under' and b['actual'] == 0.27 and b['said'] == 0.3 for b in m['minutes']))
    ok('too few games measures nothing rather than something', measure(obs[:50])['forecast'] == {})

    payload = build(g)
    json.dumps(payload, allow_nan=False)
    ok('a tiny window still builds valid JSON', payload['players'][0]['name'] == 'P7')

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
    print('doubles: seasons %s' % ','.join(seasons))
    rows = load(a.out, seasons)
    if not rows:
        print('  no gamelogs - nothing built')
        return 0
    write(a.out, build(rows), seasons)
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
