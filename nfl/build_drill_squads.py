#!/usr/bin/env python3
"""This week's fixtures and squads, compact enough for a public game page.

    python nfl/build_drill_squads.py [--out nfl/data] [--data nfl/data] [--season 2026]
    python nfl/build_drill_squads.py --selftest       # reads nothing; checks the maths

WHY THIS FILE EXISTS
The drill needs the players who are actually playing this week, and it needs enough of their real
form to put a believable game on the board before the player takes over. Both of those live in
gamelogs.json, which is 42 MB - fine for the lab, impossible for a free page that has to load in a
couple of seconds on a phone. So this reduces them to the handful of numbers a drill actually uses,
for the offensive players on this week's depth charts only.

WHAT IT EMITS, AND WHAT EACH NUMBER IS FOR
  tgtShare / carryShare  who gets the ball on a given snap during the drive. These are real: a
                         player's share of his club's targets and carries this season.
  ypt, ypc               yards per target and per carry, used only to sanity-check the pool draw.
  recYds/rushYds/passYds mean and spread across his real games this season. The page uses these to
                         put three quarters of a game on the board before you take over, because a
                         two-minute drive on its own can never reach a receiving or passing line.

THE THREE QUARTERS ARE SIMULATED AND THE PAGE SAYS SO
Nothing in the pre-drive box score happened. It is drawn from each player's real rates, which makes
it plausible, and that is all it is - not a projection, not a forecast, not a result. The page says
this in as many words wherever the box score appears. Three quarters is also the honest fraction:
a season line is priced for a full game, so a player sits near three-quarters of it when you take
over, and your drive is what decides whether he clears it.
"""
import argparse
import datetime
import json
import math
import os
import re
import sys

# Only the positions that touch the ball on offence. The NFL depth chart carries exactly these.
SKILL = ('QB', 'RB', 'WR', 'TE')
MIN_GAMES = 1


def mean_sd(xs):
    """Mean and sample standard deviation, with the degenerate cases named rather than crashing."""
    xs = [float(x) for x in xs if x is not None]
    if not xs:
        return 0.0, 0.0
    m = sum(xs) / len(xs)
    if len(xs) < 2:
        # one game is a mean with no measured spread; assume a moderate one rather than zero, or
        # every drill would hand that player exactly the same line every time
        return m, m * 0.5
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return m, math.sqrt(var)


def norm_name(n):
    """Match a depth-chart name to a gamelog name.

    The two sources disagree about generational suffixes and punctuation, and not in one direction:
    the sheet says "Aaron Jones Sr." where the logs say "Aaron Jones", and "Oronde Gadsden" where
    the logs say "Oronde Gadsden II". Measured on week 6 of 2026, 19 depth-chart players failed an
    exact match and 17 of them carried real usage - including Aaron Jones (115 touches) and James
    Cook (106), the lead backs at Minnesota and Buffalo. Left unmatched, the drill would never hand
    either club's best runner the ball. Matching is always within a club, so dropping the suffix
    cannot collide a father and son on different teams.
    """
    n = re.sub(r'\s+(Jr|Sr|II|III|IV|V)\.?$', '', str(n or '').strip(), flags=re.I)
    n = n.replace('.', '').replace("'", '').replace('-', ' ')
    return re.sub(r'\s+', ' ', n).lower()


def rows_of(blob, *names):
    """These files are sometimes a list and sometimes a wrapper around one."""
    if isinstance(blob, list):
        return blob
    for n in names:
        if isinstance(blob.get(n), list):
            return blob[n]
    for v in blob.values():
        if isinstance(v, list):
            return v
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default='nfl/data', help='where gamelogs/lineups/fixture live')
    ap.add_argument('--out', default='nfl/data')
    ap.add_argument('--season', default=None, help='defaults to the season in fixture.json')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    def load(n):
        with open(os.path.join(a.data, n)) as fh:
            return json.load(fh)

    fixtures = rows_of(load('fixture.json'), 'fixture', 'games')
    lineups = rows_of(load('lineups.json'), 'lineups', 'rows')
    if not fixtures:
        print('fixture.json has no games - nothing to build'); return 1
    season = str(a.season or fixtures[0].get('season') or '')
    week = fixtures[0].get('week')
    print('  season %s, week %s, %d fixtures' % (season, week, len(fixtures)))

    # Who is actually on this week's depth charts, by club. Depth order matters: it is how the
    # drill labels a man WR1 or RB2 and how it falls back when a player has never played.
    sheet = {}
    for r in lineups:
        pos = r.get('position') or r.get('pos')
        if pos not in SKILL:
            continue
        t = r.get('team')
        if not t:
            continue
        sheet.setdefault(t, []).append({
            'name': r.get('player'), 'pos': pos,
            'depth': int(r.get('depth') or 9),
            'starter': str(r.get('status') or '').upper() == 'STARTER',
        })

    # lineups.json lists some men twice under two spellings - Tampa carries "Ted Hurst III" at
    # depth 3 and "Ted Hurst" at depth 4, and "Eric Rivers Jr." and "Eric Rivers" both at depth 10.
    # Left alone, each copy claims the same gamelog rows, so his target share is counted twice
    # (Tampa's summed to 1.11) and the drill would throw to him twice as often as it should. Keep
    # the shallower entry, preferring the one marked STARTER.
    dupes = 0
    for t, men in sheet.items():
        best = {}
        for m in men:
            k = norm_name(m['name'])
            cur_m = best.get(k)
            if cur_m is None:
                best[k] = m
            else:
                dupes += 1
                if (not cur_m['starter'] and m['starter']) or m['depth'] < cur_m['depth']:
                    best[k] = m
        sheet[t] = list(best.values())
    print('  %d clubs on the depth chart, %d skill players%s'
          % (len(sheet), sum(len(v) for v in sheet.values()),
             (' (%d duplicate listings merged)' % dupes) if dupes else ''))

    logs = rows_of(load('gamelogs.json'), 'gamelogs', 'rows')
    cur = [r for r in logs if str(r.get('Year')) == season]
    print('  %d gamelog rows this season, of %d total' % (len(cur), len(logs)))

    # Per-player game lists, so a mean has a real spread behind it rather than a single number.
    byp, bynorm = {}, {}
    for r in cur:
        n, t = r.get('Player'), r.get('Team')
        if not n or not t:
            continue
        byp.setdefault((t, n), []).append(r)
        bynorm.setdefault((t, norm_name(n)), []).append(r)

    out_squads, thin, matched = {}, [], 0
    for team, men in sheet.items():
        tgt_tot = sum(float(r.get('targets') or 0) for r in cur if r.get('Team') == team)
        car_tot = sum(float(r.get('rushAtt') or 0) for r in cur if r.get('Team') == team)
        squad = []
        for m in men:
            gs = byp.get((team, m['name']))
            if gs is None:
                gs = bynorm.get((team, norm_name(m['name'])), [])
                if gs:
                    matched += 1
            if len(gs) < MIN_GAMES and m['depth'] > 3:
                continue                      # deep bench who has not played: not in the drill
            get = lambda k: [float(g.get(k) or 0) for g in gs]
            tgt = sum(get('targets')); car = sum(get('rushAtt'))
            rec = sum(get('receptions'))
            recY, _ = mean_sd(get('recYds')); recYsd = mean_sd(get('recYds'))[1]
            rushY, rushYsd = mean_sd(get('rushYds'))
            passY, passYsd = mean_sd(get('passYds'))
            recN, _ = mean_sd(get('receptions'))
            rd = lambda v, n=1: round(float(v), n)
            squad.append({
                'n': m['name'], 'pos': m['pos'], 'd': m['depth'], 'st': 1 if m['starter'] else 0,
                'gm': len(gs),
                'tgtShare': rd(tgt / tgt_tot, 4) if tgt_tot else 0,
                'carryShare': rd(car / car_tot, 4) if car_tot else 0,
                'ypt': rd(sum(get('recYds')) / tgt, 2) if tgt else 0,
                'ypc': rd(sum(get('rushYds')) / car, 2) if car else 0,
                'catch': rd(rec / tgt, 3) if tgt else 0,
                'recYds': [rd(recY), rd(recYsd)],
                'rushYds': [rd(rushY), rd(rushYsd)],
                'passYds': [rd(passY), rd(passYsd)],
                'rec': rd(recN, 2),
            })
        squad.sort(key=lambda s: (s['pos'] != 'QB', s['d'], s['n']))
        out_squads[team] = squad
        live = [s for s in squad if s['tgtShare'] or s['carryShare']]
        if len(live) < 4:
            thin.append('%s %d' % (team, len(live)))

    playing = set()
    for f in fixtures:
        playing.add(f.get('home')); playing.add(f.get('away'))
    missing = sorted(t for t in playing if t and not out_squads.get(t))
    out = {
        'updated': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'),
        'season': season, 'week': week,
        'note': 'Usage shares and per-game form are real. Any game state the drill shows before '
                'your drive is simulated from these rates and did not happen.',
        'fixtures': [{'home': f.get('home'), 'away': f.get('away'), 'utc': f.get('utc'),
                      'venue': f.get('venue'), 'week': f.get('week')} for f in fixtures],
        'squads': {t: out_squads[t] for t in sorted(out_squads) if t in playing},
    }
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, 'drill_squads.json')
    with open(path, 'w') as fh:
        json.dump(out, fh, separators=(',', ':'))
    kb = os.path.getsize(path) / 1024
    print('  wrote %s - %d clubs playing, %d players, %.0f KB'
          % (path, len(out['squads']), sum(len(v) for v in out['squads'].values()), kb))
    if matched:
        print('  %d players matched to their logs only after normalising the name '
              '(suffixes and punctuation)' % matched)
    if missing:
        print('  WARNING: no depth chart for %s - those clubs cannot be played' % ', '.join(missing))
    if thin:
        print('  WARNING: fewer than 4 players with usage at %s - early season or a stale '
              'gamelogs.json' % ', '.join(thin))
    # a club whose shares do not add up is a bug worth seeing at build time, not in play
    for t, sq in out['squads'].items():
        s = sum(p['tgtShare'] for p in sq)
        if sq and not (0.5 <= s <= 1.05):
            print('  NOTE: %s target share on the depth chart sums to %.2f (the rest went to men '
                  'not on this week\'s sheet)' % (t, s))
    return 0


def selftest():
    fails = []

    def ok(name, cond, note=''):
        print(('  ok   ' if cond else '  FAIL ') + name + (('  ' + note) if note else ''))
        if not cond:
            fails.append(name)

    m, s = mean_sd([10, 20, 30])
    ok('the mean is the mean', abs(m - 20) < 1e-9, '(%.1f)' % m)
    ok('and the spread is the sample sd', abs(s - 10) < 1e-9, '(%.1f)' % s)
    m, s = mean_sd([42])
    ok('one game gets a mean and an assumed spread', m == 42 and s == 21,
       '(a single game has no measured spread; zero would hand that player the same line forever)')
    m, s = mean_sd([])
    ok('no games is zero, not a crash', m == 0 and s == 0)
    m, s = mean_sd([7, 7, 7])
    ok('a player who does the same thing every week has no spread', s == 0, '(%.1f)' % s)
    ok('a generational suffix does not hide a player from his own logs',
       norm_name('Aaron Jones Sr.') == norm_name('Aaron Jones') == 'aaron jones')
    ok('and it works the other way round too',
       norm_name('Oronde Gadsden') == norm_name('Oronde Gadsden II'),
       '(the sheet and the logs disagree in both directions)')
    ok('punctuation and case do not either',
       norm_name("Ja'seem Reed") == norm_name("Ja'Seem Reed") and
       norm_name("Tre' Harris") == norm_name('Tre Harris'))
    ok('but two different men do not collide',
       norm_name('Aaron Jones') != norm_name('Aaron Rodgers') and
       norm_name('Michael Pittman Jr.') != norm_name('Michael Thomas'))
    ok('a lone initial is not treated as a suffix',
       norm_name('A.J. Brown') == 'aj brown', '(%s)' % norm_name('A.J. Brown'))
    ok('rows_of unwraps a list', rows_of([1, 2]) == [1, 2])
    ok('rows_of unwraps a named wrapper', rows_of({'gamelogs': [1]}, 'gamelogs') == [1])
    ok('rows_of finds the only list if the name is wrong', rows_of({'other': [1, 2]}) == [1, 2])
    ok('rows_of returns empty rather than guessing', rows_of({'a': 1}) == [])
    ok('only ball-carrying positions are in the drill',
       set(SKILL) == {'QB', 'RB', 'WR', 'TE'}, '(no K; a kicker cannot be called in this drill)')
    print(('\nFAILED: ' + ', '.join(fails)) if fails else '\nall good')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main() or 0)
