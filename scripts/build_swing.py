#!/usr/bin/env python3
"""Buy Low / Sell High - players the market has re-priced off the wrong evidence.

    python3 scripts/build_swing.py nfl            # writes nfl/data/swing.json
    python3 scripts/build_swing.py nfl --print    # show what it found, write nothing
    python3 scripts/build_swing.py --selftest

THE BET
A player runs into two hard defences in a row, his numbers fall, and the book moves his line down.
Then he draws a soft one. If the fall was the DEFENCES and not the player, the line is now low
against a matchup that should not hold him - and the over is the bet. Sell High is the mirror: two
soft weeks, inflated numbers, a line that has climbed, and a hard defence next.

WHY THE USAGE CHECK IS THE WHOLE SIGNAL
A player's yards can fall for two reasons that look identical in a box score: he was held, or he
was barely out there. Only the first one comes back when the matchup softens. A back who lost his
job and ran for 20 yards is not a buy at any price, and his line is low for a reason that is still
true next week. So a drop in production is only read as a matchup effect when the usage behind it
held up - carries for a back, targets for a receiver, attempts for a quarterback. That test is what
separates this from "his numbers are down, back him".

WHAT COUNTS AS A HARD OR SOFT DEFENCE
dvp.json, ranked within the player's own position and market: how many yards that defence gives up
to running backs is the only thing that matters to a running back. Rank 1 is the stingiest.

WHERE THE LINE COMES FROM
odds-history holds a daily snapshot, and each one carries the fixture list it was taken against -
so every snapshot knows its own week and no date arithmetic is needed. Within a week the last
snapshot is that week's closing line. The comparison is this week's line against the median of the
closing lines BEFORE the two-game run, which is where the market had him before the hard stretch.

Only a two-way rung is used, and only the one priced closest to even money. An alt ladder carries
up to 32 rungs for one player and market; taking the line off one row and the price off another
produces a number that never existed, which is a mistake this project has already made once.
"""
import argparse
import datetime as dt
import glob
import gzip
import json
import os
import statistics
import sys
from collections import defaultdict

# the market that describes each position, and the usage behind it that has to hold up
MARKETS = {
    'RB': [('rushYds', 'rushAtt'), ('rushRecYds', 'rushAtt')],
    'WR': [('recYds', 'targets'), ('receptions', 'targets')],
    'TE': [('recYds', 'targets'), ('receptions', 'targets')],
    'QB': [('passYds', 'passAtt')],
}
RUN_GAMES = 2          # the hard (or soft) stretch, as Ant described it
MIN_BASE = 2           # games before the stretch, or there is no "before" to compare with
TOUGH_RANK = 10        # of 32, within the position
SOFT_RANK = 23
PROD_DROP = 0.75       # the stretch has to have actually hurt
PROD_RISE = 1.25
# Usage floors are per position, because the same percentage means different things. A
# quarterback's attempts swing with the score - trailing he throws 45, in front 24 - and two games
# against elite defences are exactly the games a team is most likely to be behind in, so a flat
# floor reads ordinary game script as a lost role. Carries move with the script too. Targets are
# the steadiest of the three, so they get the strictest test.
USAGE_HELD = {'QB': 0.75, 'RB': 0.80, 'WR': 0.85, 'TE': 0.85}
USAGE_CAP = 1.20       # for a sell, the good run must not just be extra volume
LINE_MOVE = 0.06       # the market has to have noticed
MIN_LINE = 2.0         # below this a percentage move is noise on a half-point


def load(path, default=None):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def main_line(rows, player, market):
    """The rung priced closest to even money, with its own prices.

    An alt ladder is not one line. The rung nearest even money is the one the book is actually
    hanging the market on, and line and prices have to travel together or the result is a price
    that was never offered at that number.
    """
    best = None
    for r in rows:
        if r.get('player') != player or r.get('market') != market:
            continue
        ov, un, ln = r.get('over'), r.get('under'), r.get('line')
        if not ov or not un or ln is None:
            continue
        try:
            bal = abs(1.0 / float(ov) - 1.0 / float(un))
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if best is None or bal < best[0]:
            best = (bal, float(ln), float(ov), float(un),
                    r.get('overBook') or r.get('book') or '')
    return best


def week_lines(root, sport):
    """{(player, market): {week: (line, over, under, book)}} - one closing line per week.

    Each snapshot carries the fixture list it was taken against, so it knows its own week without
    any mapping from dates. Snapshots are read in order and the last one for a week wins, which is
    that week's closing line.
    """
    out = defaultdict(dict)
    files = sorted(glob.glob(os.path.join(root, sport, 'odds-history', '*.json.gz')))
    for path in files:
        try:
            with gzip.open(path) as fh:
                snap = json.load(fh)
        except (OSError, json.JSONDecodeError, EOFError):
            continue
        weeks = [f.get('week') for f in (snap.get('fixture') or []) if f.get('week')]
        if not weeks:
            continue
        week = statistics.mode(weeks)
        rows = snap.get('odds') or []
        seen = defaultdict(list)
        for r in rows:
            if r.get('player') and r.get('market'):
                seen[(r['player'], r['market'])].append(r)
        for key, rs in seen.items():
            ml = main_line(rs, key[0], key[1])
            if ml:
                out[key][week] = (ml[1], ml[2], ml[3], ml[4])
    return out


def dvp_ranks(dvp, markets):
    """(pos, market, team) -> (rank, of, allowed). Rank 1 is the stingiest."""
    out = {}
    wanted = {m for pairs in markets.values() for m, _u in pairs}
    for pos in markets:
        for mkt in wanted:
            rows = [r for r in dvp if r.get('pos') == pos and (r.get(mkt) or 0) > 0]
            if len(rows) < 8:
                continue
            rows.sort(key=lambda r: r[mkt])
            for i, r in enumerate(rows, 1):
                out[(pos, mkt, r['team'])] = (i, len(rows), r[mkt])
    return out


def build(root, sport, markets=None, today=None):
    markets = markets or MARKETS
    base = os.path.join(root, sport, 'data')
    logs = load(os.path.join(base, 'gamelogs.json'), []) or []
    players = {p['name']: p for p in (load(os.path.join(base, 'players.json'), []) or [])}
    dvp = load(os.path.join(base, 'dvp.json'), []) or []
    fixture = load(os.path.join(base, 'fixture.json'), []) or []
    meta = load(os.path.join(base, 'meta.json'), {}) or {}
    season = str(meta.get('currentSeason') or max(
        (str(r.get('Year') or '') for r in logs), default=''))

    ranks = dvp_ranks(dvp, markets)
    lines = week_lines(root, sport)

    # who each team plays next, from the fixture file
    nxt, nxt_week = {}, None
    for gm in fixture:
        h, a = gm.get('home'), gm.get('away')
        if h and a:
            nxt[h] = a
            nxt[a] = h
            if gm.get('week'):
                nxt_week = max(nxt_week or 0, int(gm['week']))

    by_player = defaultdict(list)
    for r in logs:
        if str(r.get('Year') or '') == season and r.get('Player'):
            by_player[r['Player']].append(r)

    rows = []
    for name, games in by_player.items():
        info = players.get(name) or {}
        pos = info.get('position')
        if pos not in markets:
            continue
        games = sorted(games, key=lambda r: int(r.get('Week') or 0))
        if len(games) < RUN_GAMES + MIN_BASE:
            continue
        run, pre = games[-RUN_GAMES:], games[:-RUN_GAMES]
        team = info.get('team') or run[-1].get('Team')
        opp_next = nxt.get(team)
        if not opp_next:
            continue
        # the fixture file normally leads the gamelogs, but if a build runs while they overlap the
        # "next" game can be one he has already played - which would grade a bet on a result that
        # is already known. Only ever look forward.
        played = {int(g.get('Week') or 0) for g in games}
        if nxt_week and nxt_week in played:
            continue
        for mkt, use in markets[pos]:
            rk_run = [ranks.get((pos, mkt, g.get('Opp'))) for g in run]
            rk_next = ranks.get((pos, mkt, opp_next))
            if not all(rk_run) or not rk_next:
                continue
            prod_run = statistics.mean([float(g.get(mkt) or 0) for g in run])
            prod_pre = statistics.mean([float(g.get(mkt) or 0) for g in pre])
            use_run = statistics.mean([float(g.get(use) or 0) for g in run])
            use_pre = statistics.mean([float(g.get(use) or 0) for g in pre])
            if prod_pre <= 0 or use_pre <= 0:
                continue

            hist = lines.get((name, mkt)) or {}
            if not hist:
                continue
            weeks_run = {int(g.get('Week') or 0) for g in run}
            first_run = min(weeks_run) if weeks_run else None
            cur_week = max(hist)
            # where the market has him now, against where it had him before the stretch
            prior = [v[0] for w, v in hist.items() if first_run and w < first_run]
            if not prior or cur_week in weeks_run:
                continue
            line_now, over, under, book = hist[cur_week]
            line_prior = statistics.median(prior)
            if line_prior < MIN_LINE or line_now < MIN_LINE:
                continue
            move = (line_now - line_prior) / line_prior

            hard = [r[0] <= TOUGH_RANK for r in rk_run]
            soft = [r[0] >= SOFT_RANK for r in rk_run]
            side = None
            if (all(hard) and rk_next[0] >= SOFT_RANK
                    and prod_run <= prod_pre * PROD_DROP
                    and use_run >= use_pre * USAGE_HELD.get(pos, 0.85)
                    and move <= -LINE_MOVE):
                side = 'buy'
            elif (all(soft) and rk_next[0] <= TOUGH_RANK
                    and prod_run >= prod_pre * PROD_RISE
                    and use_run <= use_pre * USAGE_CAP
                    and move >= LINE_MOVE):
                side = 'sell'
            if not side:
                continue

            rows.append({
                'player': name, 'team': team, 'pos': pos, 'market': mkt, 'side': side,
                'runOpps': [{'team': g.get('Opp'), 'rank': r[0], 'of': r[1]}
                            for g, r in zip(run, rk_run)],
                'nextOpp': {'team': opp_next, 'rank': rk_next[0], 'of': rk_next[1],
                            'allowed': round(float(rk_next[2]), 1)},
                'basePerGame': round(prod_pre, 1), 'runPerGame': round(prod_run, 1),
                'baseGames': len(pre),
                'usageStat': use, 'usageBase': round(use_pre, 1), 'usageRun': round(use_run, 1),
                'lineNow': line_now, 'linePrior': line_prior, 'move': round(move, 3),
                'over': over, 'under': under, 'book': book,
                'week': nxt_week or cur_week,
            })

    # the clearest first: the biggest line move against the biggest matchup swing
    rows.sort(key=lambda r: -(abs(r['move']) * abs(r['nextOpp']['rank']
                                                  - statistics.mean([o['rank'] for o in r['runOpps']]))))
    return {'sport': sport, 'season': season, 'week': nxt_week,
            'updated': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'),
            'rows': rows}


# --------------------------------------------------------------------------------------------
# selftest - synthetic, so every case is a rule rather than whatever this week happens to hold
# --------------------------------------------------------------------------------------------
def selftest():
    import tempfile
    fails = []

    def ok(name, cond, note=''):
        print(('  ok   ' if cond else '  FAIL ') + name + (('  ' + note) if note else ''))
        if not cond:
            fails.append(name)

    root = tempfile.mkdtemp(prefix='swing')
    base = os.path.join(root, 'nfl', 'data')
    os.makedirs(base, exist_ok=True)
    os.makedirs(os.path.join(root, 'nfl', 'odds-history'), exist_ok=True)

    # 32 defences, rank 1 stingiest: T01 gives up least, T32 most
    dvp = [{'team': f'T{i:02d}', 'pos': 'RB', 'rushYds': 40.0 + i * 3,
            'rushAtt': 20.0, 'recYds': 0.0, 'receptions': 0.0,
            'rushRecYds': 50.0 + i * 3, 'targets': 0, 'passYds': 0, 'passAtt': 0}
           for i in range(1, 33)]
    with open(os.path.join(base, 'dvp.json'), 'w') as fh:
        json.dump(dvp, fh)
    with open(os.path.join(base, 'meta.json'), 'w') as fh:
        json.dump({'currentSeason': '2026'}, fh)
    with open(os.path.join(base, 'fixture.json'), 'w') as fh:
        json.dump([{'home': 'MINE', 'away': 'T30', 'week': 6}], fh)

    def logs_for(prod, use, opps):
        """weeks 1..n for one back, with whatever he did and who he did it against"""
        out = []
        for i, (p, u, o) in enumerate(zip(prod, use, opps), 1):
            out.append({'Year': '2026', 'Week': i, 'Player': 'Test Back', 'Team': 'MINE',
                        'Opp': o, 'rushYds': p, 'rushAtt': u, 'rushRecYds': p,
                        'receptions': 0, 'targets': 0, 'recYds': 0})
        return out

    def snaps(by_week):
        for wk, line in by_week.items():
            path = os.path.join(root, 'nfl', 'odds-history', f'2026-09-{wk:02d}.json.gz')
            with gzip.open(path, 'wt') as fh:
                json.dump({'fixture': [{'week': wk}],
                           'odds': [{'player': 'Test Back', 'market': 'rushYds', 'line': line,
                                     'over': 1.9, 'under': 1.9, 'overBook': 'Book'},
                                    # an alt rung priced miles from even money: must be ignored
                                    {'player': 'Test Back', 'market': 'rushYds',
                                     'line': line + 40, 'over': 9.0, 'under': 1.05,
                                     'overBook': 'Book'}]}, fh)

    def run(prod, use, opps, lines_by_week, players=None):
        with open(os.path.join(base, 'gamelogs.json'), 'w') as fh:
            json.dump(logs_for(prod, use, opps), fh)
        with open(os.path.join(base, 'players.json'), 'w') as fh:
            json.dump(players or [{'name': 'Test Back', 'team': 'MINE', 'position': 'RB'}], fh)
        for f in glob.glob(os.path.join(root, 'nfl', 'odds-history', '*.gz')):
            os.remove(f)
        snaps(lines_by_week)
        return [r for r in build(root, 'nfl')['rows'] if r['market'] == 'rushYds']

    print('THE BET')
    # four games: two soft-ish, then two of the very hardest, next one soft. Usage never moves.
    HARD, SOFT = ['T01', 'T02'], 'T30'
    got = run([90, 95, 30, 25], [18, 18, 18, 17], ['T20', 'T22'] + HARD,
              {1: 88.5, 2: 90.5, 3: 85.5, 4: 86.5, 5: 72.5})
    ok('two hard defences, a dip with the role intact, then a soft one',
       len(got) == 1 and got[0]['side'] == 'buy',
       '(90 and 95, then 30 and 25, line 86.5 -> 72.5)')
    if got:
        r = got[0]
        ok('it names both defences he ran into and the one coming',
           [o['rank'] for o in r['runOpps']] == [1, 2] and r['nextOpp']['rank'] == 30)
        ok('and shows the line it moved from and to',
           r['linePrior'] == 89.5 and r['lineNow'] == 72.5,
           '(prior is the median of the weeks BEFORE the run)')
        ok('and reads the main line, not the alt rung beside it',
           r['over'] == 1.9, '(the +40 rung at $9.00 is a different bet)')

    print('AND WHAT IT REFUSES')
    # the whole point: same collapse, but he stopped getting the ball
    got = run([90, 95, 30, 25], [18, 18, 6, 5], ['T20', 'T22'] + HARD,
              {1: 88.5, 2: 90.5, 3: 85.5, 4: 86.5, 5: 72.5})
    ok('a player whose carries vanished is NOT a buy',
       not got, '(18 carries down to 5 - the line is low for a reason that is still true)')

    got = run([90, 95, 30, 25], [18, 18, 18, 17], ['T20', 'T22'] + HARD,
              {1: 88.5, 2: 90.5, 3: 85.5, 4: 86.5, 5: 86.5})
    ok('no buy when the market never moved', not got, '(the edge is the re-pricing)')

    got = run([90, 95, 30, 25], [18, 18, 18, 17], ['T20', 'T22', 'T01', 'T25'],
              {1: 88.5, 2: 90.5, 3: 85.5, 4: 86.5, 5: 72.5})
    ok('no buy when only one of the two was hard', not got,
       '(one bad matchup is a game, two is a stretch)')

    got = run([90, 95, 30, 25], [18, 18, 18, 17], ['T20', 'T22'] + HARD,
              {1: 88.5, 2: 90.5, 3: 85.5, 4: 86.5, 5: 72.5},
              players=[{'name': 'Test Back', 'team': 'MINE', 'position': 'RB'}])
    before = len(got)
    with open(os.path.join(base, 'fixture.json'), 'w') as fh:
        json.dump([{'home': 'MINE', 'away': 'T02', 'week': 6}], fh)   # hard again next
    got2 = run([90, 95, 30, 25], [18, 18, 18, 17], ['T20', 'T22'] + HARD,
               {1: 88.5, 2: 90.5, 3: 85.5, 4: 86.5, 5: 72.5})
    ok('no buy when the matchup ahead is hard too', before == 1 and not got2,
       '(the bet is the softening, not the slump)')
    with open(os.path.join(base, 'fixture.json'), 'w') as fh:
        json.dump([{'home': 'MINE', 'away': 'T30', 'week': 6}], fh)

    got = run([90, 95, 88, 86], [18, 18, 18, 17], ['T20', 'T22'] + HARD,
              {1: 88.5, 2: 90.5, 3: 85.5, 4: 86.5, 5: 72.5})
    ok('no buy when the hard run did not actually hurt him', not got,
       '(nothing to buy back)')

    print('THE MIRROR')
    with open(os.path.join(base, 'fixture.json'), 'w') as fh:
        json.dump([{'home': 'MINE', 'away': 'T01', 'week': 6}], fh)
    got = run([40, 45, 120, 130], [14, 14, 15, 15], ['T05', 'T06', 'T30', 'T31'],
              {1: 48.5, 2: 50.5, 3: 52.5, 4: 55.5, 5: 78.5})
    ok('two soft defences, a spike on the same workload, then a hard one',
       len(got) == 1 and got[0]['side'] == 'sell',
       '(120 and 130 against the two softest, line 49.5 -> 78.5)')
    got = run([40, 45, 120, 130], [14, 14, 30, 32], ['T05', 'T06', 'T30', 'T31'],
              {1: 48.5, 2: 50.5, 3: 52.5, 4: 55.5, 5: 78.5})
    ok('but not when the spike was simply twice the carries', not got,
       '(14 up to 32 - that is a role change, and it may well hold)')

    print('AND THE THINGS THAT WOULD BREAK IT QUIETLY')
    got = run([90, 95, 30, 25], [18, 18, 18, 17], ['T20', 'T22'] + HARD, {5: 72.5})
    ok('a player with no line history before the run is skipped', not got,
       '(there is no "before" to call it a dip against)')
    got = run([90, 95], [18, 18], HARD, {1: 88.5, 2: 72.5})
    ok('and so is one with no games before the run', not got)

    print(('\nFAILED: ' + ', '.join(fails)) if fails else '\nall good')
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('sport', nargs='?', default='nfl')
    ap.add_argument('--root', default='.')
    ap.add_argument('--print', dest='show', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    out = build(a.root, a.sport)
    rows = out['rows']
    buy = [r for r in rows if r['side'] == 'buy']
    sell = [r for r in rows if r['side'] == 'sell']
    print(f"{a.sport}: {len(buy)} buy low, {len(sell)} sell high (week {out['week']})")
    for r in rows:
        opps = ' then '.join(f"{o['team']} #{o['rank']}" for o in r['runOpps'])
        print(f"  {r['side'].upper():4} {r['player']:<24} {r['market']:<11} {opps}"
              f" -> {r['nextOpp']['team']} #{r['nextOpp']['rank']}"
              f" | {r['basePerGame']} -> {r['runPerGame']}"
              f" | line {r['linePrior']} -> {r['lineNow']} ({r['move']:+.0%})"
              f" | {r['usageStat']} {r['usageBase']} -> {r['usageRun']}")
    if a.show:
        return 0
    path = os.path.join(a.root, a.sport, 'data', 'swing.json')
    with open(path, 'w') as fh:
        json.dump(out, fh, separators=(',', ':'))
    print(f'wrote {path} ({os.path.getsize(path) / 1024:.0f} KB)')
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
