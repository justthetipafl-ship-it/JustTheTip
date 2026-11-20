#!/usr/bin/env python3
"""Steals and blocks: how often he gets one, two, three - and how much of that can be forecast.

    python nba/build_nba_stocks.py --out nba/data [--seasons 2026,2027]
    python nba/build_nba_stocks.py --selftest          # no network, no files

WHY
The books price steals and blocks for most of a slate and the page had nothing behind either.
They are also the two markets where an average misleads most: a line of 1.5 steals on a player
who averages 1.4 is not a coin flip, because steals come in ones and noughts and the average is
held up by the odd night of four. What a reader needs is the thing the line is actually asking -
how often does he get AT LEAST one, at least two, at least three - and how far to trust it.

WHAT IS MEASURED, every run, on the window it is given - nothing below is a constant:

  stability  first half-season against second, 40-game floor, the same split every other card
             uses. Blocks carry about as well as rebounds do; steals noticeably less.
  recent     a five-game run against his own season, WITHIN the season, with the minutes taken
             out - the same test as the scoring and rebounding cards.
  ladder     whether his own record of clearing a rung (1+, 2+, 3+) is honest about the next
             game. For each rung: what records like his said, and how often it then happened.
  opp        what the opponent is worth out of sample - steals against how many the opponent
             has been giving up, blocks against how many of its shots have been blocked, each
             taken only from games BEFORE the one being forecast.

WHAT IS WRITTEN
nba/data/stocks.json

    {"league":{"steals":{"avg","stability","recent","ladder":[..],"opp":{..}},
               "blocks":{...}},
     "defence":{"GS":{"stealsAllowed","stealsRank","blocksAllowed","blocksRank","of"}},
     "players":[{"playerId","name","team","games","minutes",
                 "steals":{"avg","per30","r1","r2","r3"},"blocks":{...}}]}

Everything is from the box-score game logs the 12:00 NBA build writes.
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

STATS = ['steals', 'blocks']
MIN_GAMES = 10
MIN_AVG = 0.4            # below this he is not a read in that stat
MIN_PRIOR = 20           # games behind him before his record is used to forecast the next one
MIN_OPP_PRIOR = 10
MIN_DEF_GAMES = 15
MIN_BAND = 80
WINDOW = 5
RUNGS = [1, 2, 3]
BANDS = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]


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


def tails(pool):
    pool = sorted(pool, key=lambda t: t[0])
    q = len(pool) // 5
    return (statistics.mean(y for _, y in pool[:q]), statistics.mean(y for _, y in pool[-q:]))


def seasons_of(rows):
    g = collections.defaultdict(list)
    for r in rows:
        if fnum(r.get('minutes')) > 0:
            i = pid(r.get('PlayerId'))
            if i:
                g[(i, str(r.get('Year') or ''))].append(r)
    for k in g:
        g[k].sort(key=lambda r: str(r.get('Date') or ''))
    return g


def stability(by, stat):
    a, b = [], []
    for g in by.values():
        if len(g) < MIN_STAB_GAMES:
            continue
        h = len(g) // 2
        a.append(statistics.mean(fnum(r.get(stat)) for r in g[:h]))
        b.append(statistics.mean(fnum(r.get(stat)) for r in g[h:]))
    return {'r': r3(corr(a, b)), 'n': len(a)}


def recent(by, stat):
    raw, per_min = [], []
    for g in by.values():
        if len(g) < MIN_STAB_GAMES:
            continue
        v = [fnum(r.get(stat)) for r in g]
        m = [fnum(r.get('minutes')) for r in g]
        base = statistics.mean(v)
        if base < MIN_AVG:
            continue
        pm = [(a / b * 30.0) if b else 0.0 for a, b in zip(v, m)]
        pmbase = statistics.mean(pm)
        for j in range(WINDOW, len(g)):
            raw.append((statistics.mean(v[j - WINDOW:j]) - base, v[j] - base))
            per_min.append((statistics.mean(pm[j - WINDOW:j]) - pmbase, pm[j] - pmbase))
    if len(raw) < 500:
        return None
    lo, hi = tails(raw)
    plo, phi = tails(per_min)
    return {'hottest': r3(hi), 'coldest': r3(lo), 'hottestPerMin': r3(phi), 'coldestPerMin': r3(plo),
            'window': WINDOW, 'n': len(raw)}


def opp_prior(rows, stat):
    """{(opponent, season, date): that stat conceded per game BEFORE that date}."""
    per = collections.defaultdict(lambda: collections.defaultdict(float))
    for r in rows:
        if fnum(r.get('minutes')) > 0:
            per[(str(r.get('Opp') or ''), str(r.get('Year') or ''))][str(r.get('Date') or '')] += fnum(r.get(stat))
    out = {}
    for (opp, yr), days in per.items():
        tot = 0.0
        for i, d in enumerate(sorted(days)):
            if i >= MIN_OPP_PRIOR:
                out[(opp, yr, d)] = tot / i
            tot += days[d]
    return out


def ladder_and_opp(by, rows, stat):
    """Is his record of clearing each rung honest, and what is the opponent worth?"""
    prior = opp_prior(rows, stat)
    obs = {k: [] for k in RUNGS}
    opp = []
    for (i, yr), g in by.items():
        for n in range(MIN_PRIOR, len(g)):
            before = [fnum(r.get(stat)) for r in g[:n]]
            base = statistics.mean(before)
            if base < MIN_AVG:
                continue
            got = fnum(g[n].get(stat))
            for k in RUNGS:
                obs[k].append((sum(1 for v in before if v >= k) / len(before), 1 if got >= k else 0))
            p = prior.get((str(g[n].get('Opp') or ''), yr, str(g[n].get('Date') or '')))
            if p is not None:
                opp.append((p, got - base))
    ladder = []
    for k in RUNGS:
        o = obs[k]
        if len(o) < 300:
            continue
        rec = {'rung': k, 'n': len(o), 'said': r3(statistics.mean(x for x, _ in o)),
               'actual': r3(statistics.mean(y for _, y in o)), 'bands': []}
        for lo, hi in BANDS:
            s = [(x, y) for x, y in o if lo <= x < hi]
            if len(s) >= MIN_BAND:
                rec['bands'].append({'lo': lo, 'hi': min(hi, 1.0), 'said': r3(statistics.mean(x for x, _ in s)),
                                     'actual': r3(statistics.mean(y for _, y in s)), 'n': len(s)})
        ladder.append(rec)
    oppo = None
    if len(opp) >= 500:
        lo, hi = tails(opp)
        oppo = {'r': r3(corr([x for x, _ in opp], [y for _, y in opp])),
                'toughest': r3(lo), 'softest': r3(hi), 'n': len(opp)}
    return ladder, oppo


def defence(rows):
    """Steals and blocks each team gives up a game. Rank 1 gives up the most."""
    per = collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, 0.0]))
    for r in rows:
        if fnum(r.get('minutes')) > 0 and r.get('Opp'):
            d = per[str(r.get('Opp'))][(str(r.get('Year')), str(r.get('Date')))]
            d[0] += fnum(r.get('steals'))
            d[1] += fnum(r.get('blocks'))
    rec = {}
    for t, d in per.items():
        if len(d) >= MIN_DEF_GAMES:
            rec[t] = {'stealsAllowed': round(sum(v[0] for v in d.values()) / len(d), 2),
                      'blocksAllowed': round(sum(v[1] for v in d.values()) / len(d), 2),
                      'games': len(d)}
    for key, rk in (('stealsAllowed', 'stealsRank'), ('blocksAllowed', 'blocksRank')):
        for i, t in enumerate(sorted(rec, key=lambda t: -rec[t][key])):
            rec[t][rk] = i + 1
            rec[t]['of'] = len(rec)
    return rec


def player_rows(rows):
    byp = collections.defaultdict(list)
    for r in rows:
        if fnum(r.get('minutes')) > 0:
            i = pid(r.get('PlayerId'))
            if i:
                byp[i].append(r)
    out = []
    for i, g in byp.items():
        if len(g) < MIN_GAMES:
            continue
        g.sort(key=lambda r: str(r.get('Date') or ''))
        mins = statistics.mean(fnum(r.get('minutes')) for r in g)
        rec = {'playerId': i, 'name': nm(g[-1].get('Player')), 'team': nm(g[-1].get('Team')),
               'games': len(g), 'minutes': round(mins, 1)}
        keep = False
        for st in STATS:
            v = [fnum(r.get(st)) for r in g]
            avg = statistics.mean(v)
            tot_min = sum(fnum(r.get('minutes')) for r in g)
            rec[st] = {'avg': round(avg, 2), 'per30': round(sum(v) / tot_min * 30.0, 2) if tot_min else 0.0,
                       'r1': round(sum(1 for x in v if x >= 1) / len(v), 3),
                       'r2': round(sum(1 for x in v if x >= 2) / len(v), 3),
                       'r3': round(sum(1 for x in v if x >= 3) / len(v), 3)}
            keep = keep or avg >= MIN_AVG
        if keep and rec['name']:
            out.append(rec)
    out.sort(key=lambda r: -(r['steals']['avg'] + r['blocks']['avg']))
    return out


def build(rows):
    by = seasons_of(rows)
    played = [r for r in rows if fnum(r.get('minutes')) > 0]
    league = {}
    for st in STATS:
        ladder, oppo = ladder_and_opp(by, rows, st)
        league[st] = {'avg': r3(statistics.mean(fnum(r.get(st)) for r in played)) if played else None,
                      'stability': stability(by, st), 'recent': recent(by, st),
                      'ladder': ladder, 'opp': oppo}
    return {'league': league, 'minGames': MIN_GAMES, 'defence': defence(rows), 'players': player_rows(rows)}


def write(out_dir, payload, seasons):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, 'stocks.json')
    if not payload['players'] and os.path.exists(path):
        print('  built nothing - keeping what is already in %s' % path)
        return
    payload['updated'] = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%MZ')
    payload['seasons'] = list(seasons)
    payload['source'] = 'JTT gamelogs (box score)'
    body = json.dumps(payload, separators=(',', ':'), allow_nan=False)
    with open(path, 'w') as f:
        f.write(body)
    print('  wrote %s (%.0f KB) - %d players, %d teams'
          % (path, os.path.getsize(path) / 1024, len(payload['players']), len(payload['defence'])))
    for st in STATS:
        lg = payload['league'][st]
        print('  %s: stability %s  recent %s  opp %s' % (st, lg['stability'], lg['recent'], lg['opp']))
        for L in lg['ladder']:
            print('     %d+: said %.3f actual %.3f (n=%d)  %s' % (
                L['rung'], L['said'], L['actual'], L['n'],
                ' '.join('%.2f>%.2f' % (b['said'], b['actual']) for b in L['bands'])))


def selftest():
    fails = []

    def ok(name, cond, note=''):
        print(('  ok   ' if cond else '  FAIL ') + name + (('  ' + note) if note else ''))
        if not cond:
            fails.append(name)

    def row(i, d, stl, blk, m=30, opp='X', yr='2027'):
        return {'PlayerId': i, 'Player': 'P' + str(i), 'Team': 'T', 'Opp': opp, 'Year': yr,
                'Date': '2027-%02d-%02d' % (1 + (d - 1) // 28, 1 + (d - 1) % 28),
                'steals': stl, 'blocks': blk, 'minutes': m}

    # 12 games: steals 2,2,2,1,1,1,1,0,0,0,0,4  -> avg 14/12, 1+ in 8, 2+ in 4, 3+ in 1
    seq = [2, 2, 2, 1, 1, 1, 1, 0, 0, 0, 0, 4]
    g = [row(1, d + 1, s, 0) for d, s in enumerate(seq)]
    p = player_rows(g)[0]
    ok('the average is the average', p['steals']['avg'] == round(14 / 12, 2))
    ok('1+ is how often he got at least one', p['steals']['r1'] == round(8 / 12, 3))
    ok('2+ and 3+ likewise', p['steals']['r2'] == round(4 / 12, 3) and p['steals']['r3'] == round(1 / 12, 3))
    ok('the rungs can only fall as they climb', p['steals']['r1'] >= p['steals']['r2'] >= p['steals']['r3'])
    ok('per 30 uses his real minutes', p['steals']['per30'] == round(14 / (12 * 30) * 30, 2))
    ok('a did-not-play is not a game',
       player_rows(g + [row(1, 20, 0, 0, m=0)])[0]['games'] == 12)
    ok('a player with neither is left out', player_rows([row(2, d, 0, 0) for d in range(1, 13)]) == [])
    ok('a shot-blocker with no steals is kept',
       len(player_rows([row(3, d, 0, 2) for d in range(1, 13)])) == 1)
    bad = [dict(row(5, d, 2, 0), Player=float('nan')) for d in range(1, 13)]
    ok('a NaN name does not reach the file', player_rows(bad) == [])

    rows = []
    for d in range(1, 17):
        rows.append(row(1, d, 3, 0, opp='LOOSE'))
        rows.append(row(2, d, 0, 1, opp='TIGHT'))
    d_ = defence(rows)
    ok('rank 1 gives up the most steals', d_['LOOSE']['stealsRank'] == 1 and d_['TIGHT']['stealsRank'] == 2)
    ok('and blocks are ranked on their own', d_['TIGHT']['blocksRank'] == 1)
    pri = opp_prior(rows, 'steals')
    ok('the opponent figure only uses games before the one in question',
       ('LOOSE', '2027', '2027-01-05') not in pri and pri[('LOOSE', '2027', '2027-01-12')] == 3.0)

    # a tiny window measures nothing rather than something
    lg = build(g)['league']['steals']
    ok('too little data leaves the measurements empty, not invented',
       lg['recent'] is None and lg['ladder'] == [] and lg['opp'] is None)
    json.dumps(build(g), allow_nan=False)

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
    print('stocks: seasons %s' % ','.join(seasons))
    rows = load(a.out, seasons)
    if not rows:
        print('  no gamelogs - nothing built')
        return 0
    write(a.out, build(rows), seasons)
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
