#!/usr/bin/env python3
"""A pool of real NFL plays, indexed by situation and call, for the two-minute drill.

    python nfl/build_plays.py [--seasons 2023,2024,2025,2026] [--out nfl/data] [--quarters 4]
    python nfl/build_plays.py --selftest        # no download; checks the call classifier

WHAT THIS IS FOR
The drill shows a real situation - down, distance, field position, clock, score - and asks for a
call. The result is then a REAL play that matches that call from a similar spot, not a simulated
one. So the file this writes is a pool to draw from, carrying only what a drill needs to pick a
play, show what happened and grade the call.

WHY THE GRADE NEEDS NO MODEL
nflverse ships win probability per play: wp before the snap, wpa for what the play did to it, plus
epa. "Was that a good call" is therefore a column, computed by people who do it properly, rather
than a win-probability model written here. That is the single thing that makes this buildable.

SIZE, AND WHY THE DEFAULTS ARE WHAT THEY ARE
Four seasons of fourth quarters is 29,650 plays, 9.0 MB of JSON, 0.97 MB gzipped - against the
43 MB gamelogs.json the tool already loads, so size is not the constraint. Pool DEPTH is. A player
picks a call while playing as a team, so the measurement that matters is how many real plays sit
behind each (team, call) pair:

    all of Q4, 2023-2026      128 of 128 pairs filled, smallest  41, median 199
    inside 2:00 of Q4          128 of 128 pairs filled, smallest   3, median  31
    2026 only, all of Q4       128 of 128 pairs filled, smallest   3, median  17
    2026 only, inside 2:00     106 of 128 pairs filled, smallest   1, median   3

So the drill draws from ALL of the fourth quarter across four seasons, not from genuine
inside-two-minutes plays in the current season - that last pool leaves 22 team-call pairs with
nothing in them at all and would hand the same play back repeatedly. Each play is therefore shown
with its real season, week and clubs, and the drill's own clock is the drill's, not the play's.
Narrowing --seasons or --quarters past the defaults makes the pool thin; the build measures the
depth and warns when it does.

ATTRIBUTION: play-by-play comes from nflverse, and the charting under it is CC-BY-SA 4.0 credited
to FTN Data via nflverse - the same line build_firstread.py carries. Whatever renders these plays
has to carry it too.
"""
import argparse
import datetime
import io
import json
import os
import sys
import urllib.request

REL = 'https://github.com/nflverse/nflverse-data/releases/download'
PBP = REL + '/pbp/play_by_play_%s.parquet'

# Only what a drill needs, and only what it actually emits below. Everything else in those 372
# columns is weight it would ship to a phone for nothing.
SITUATION = ['game_id', 'play_id', 'season', 'week', 'posteam', 'defteam', 'qtr', 'down',
             'ydstogo', 'yardline_100', 'game_seconds_remaining', 'score_differential']
CALL      = ['play_type', 'pass_length', 'run_location', 'run_gap']
RESULT    = ['yards_gained', 'air_yards', 'yards_after_catch', 'touchdown', 'first_down',
             'complete_pass', 'interception', 'fumble_lost', 'sack', 'penalty']
WHO       = ['passer_player_name', 'receiver_player_name', 'rusher_player_name']
GRADE     = ['wp', 'wpa', 'epa']
KEEP = SITUATION + CALL + RESULT + WHO + GRADE

# The drill offers four calls, not 27. Each is a bucket of real plays, so a call can only ever be
# answered by a play that genuinely was that call. pass_broken is the fifth bucket but NOT a fifth
# call: it is how a pass call goes wrong, drawn at the team's own rate.
CALLS = ('run_inside', 'run_outside', 'pass_short', 'pass_deep')
BUCKETS = CALLS + ('pass_broken',)

# Fewest plays a (team, call) pair needs before the drill starts handing out the same play twice.
MIN_BUCKET = 10


def grab(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (JTT build)'})
    with urllib.request.urlopen(req, timeout=240) as r:
        return io.BytesIO(r.read())


def call_of(r):
    """Which drill call a real play answers, or None if it is not one the drill offers."""
    pt = r.get('play_type')
    if pt == 'run':
        gap, loc = r.get('run_gap'), r.get('run_location')
        if gap in ('guard', 'tackle'):
            return 'run_inside'
        if gap == 'end' or loc in ('left', 'right'):
            return 'run_outside'
        return 'run_inside'          # up the middle with no gap charted
    if pt == 'pass':
        pl = r.get('pass_length')
        if pl == 'deep':
            return 'pass_deep'
        if pl == 'short':
            return 'pass_short'
        # A dropback that never became a charted throw: a sack, or the ball thrown away. These have
        # no pass_length, and dropping them was a real bug - it made a pass call in the drill
        # incapable of going wrong. Measured over four seasons of Q4: 1,579 of 18,550 dropbacks,
        # 1,349 of them sacks, mean -5.6 yards and mean wpa -0.037, and the rate runs from 6.2%
        # of dropbacks (LA) to 11.4% (LAC). So they are their own bucket, and a pass call resolves
        # against the team's own real rate of them before any charted throw is drawn.
        return 'pass_broken'
    return None


def main():
    today = datetime.date.today()
    this_season = today.year if today.month >= 3 else today.year - 1
    ap = argparse.ArgumentParser()
    ap.add_argument('--seasons', default=','.join(str(this_season - i) for i in range(3, -1, -1)))
    ap.add_argument('--out', default='nfl/data')
    ap.add_argument('--quarters', default='4',
                    help="which quarters to keep, comma separated; 'all' for every quarter")
    ap.add_argument('--selftest', action='store_true', help='check call_of; downloads nothing')
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    try:
        import pandas as pd
    except ImportError:
        print('pandas and pyarrow are required'); return 1

    qs = None if a.quarters.strip().lower() == 'all' else [int(x) for x in a.quarters.split(',')]
    seasons = [s.strip() for s in a.seasons.split(',') if s.strip()]
    frames, loaded = [], []
    for s in seasons:
        try:
            df = pd.read_parquet(grab(PBP % s))
        except Exception as e:
            # A season that has not been published yet is not a failure - the current one appears
            # mid-year. Say so and carry on with what did load.
            print('  %s: could not load (%s)' % (s, str(e)[:90]))
            continue
        miss = [c for c in KEEP if c not in df.columns]
        if miss:
            print('  %s: missing %d expected columns: %s' % (s, len(miss), ', '.join(miss[:6])))
        df = df[[c for c in KEEP if c in df.columns]]
        if 'season' not in df.columns:
            df = df.assign(season=int(s))
        before = len(df)
        df = df[df['play_type'].isin(['run', 'pass'])]
        if qs is not None:
            df = df[df['qtr'].isin(qs)]
        print('  %s: %d plays -> %d after run/pass%s' %
              (s, before, len(df), '' if qs is None else ' and quarter ' + a.quarters))
        frames.append(df); loaded.append(s)

    if not frames:
        print('no seasons loaded - nothing written'); return 1
    all_df = pd.concat(frames, ignore_index=True)

    rows, skipped = [], 0
    for r in all_df.to_dict('records'):
        c = call_of(r)
        if c is None:
            skipped += 1
            continue
        # wpa is what grades the call, so a play without it cannot carry a graded drill
        wpa = r.get('wpa')
        if wpa is None or wpa != wpa:
            skipped += 1
            continue
        # the drill always shows a down, so a play without one cannot be presented
        dn = r.get('down')
        if dn is None or dn != dn:
            skipped += 1
            continue

        def num(k, nd=None):
            v = r.get(k)
            if v is None or v != v:
                return None
            return round(float(v), nd) if nd is not None else int(v)

        def txt(k):
            v = r.get(k)
            return None if v is None or v != v or v == '' else str(v)

        rows.append({
            'id': '%s-%s' % (r.get('game_id'), r.get('play_id')),
            'sea': num('season'), 'wk': num('week'),
            'off': txt('posteam'), 'def': txt('defteam'),
            'q': num('qtr'), 'dn': num('down'), 'yg': num('ydstogo'), 'y100': num('yardline_100'),
            'clk': num('game_seconds_remaining'), 'sd': num('score_differential'),
            'call': c,
            'yds': num('yards_gained'), 'air': num('air_yards'), 'yac': num('yards_after_catch'),
            'td': num('touchdown'), 'fd': num('first_down'), 'cmp': num('complete_pass'),
            'int': num('interception'), 'fum': num('fumble_lost'), 'sack': num('sack'),
            'pen': num('penalty'),
            'qb': txt('passer_player_name'), 'rec': txt('receiver_player_name'),
            'run': txt('rusher_player_name'),
            'wp': num('wp', 4), 'wpa': num('wpa', 4), 'epa': num('epa', 3),
        })

    by_call = {}
    for r in rows:
        by_call[r['call']] = by_call.get(r['call'], 0) + 1

    # Per-team bucket counts, shipped with the pool. Two jobs: the drill reads a club's real
    # broken-dropback rate straight off it instead of scanning every row, and the depth that
    # decided the season range is auditable in the artifact rather than only in a build log.
    depth = {}
    for r in rows:
        if r['off']:
            depth.setdefault(r['off'], {})
            depth[r['off']][r['call']] = depth[r['off']].get(r['call'], 0) + 1
    for t in depth:
        for b in BUCKETS:
            depth[t].setdefault(b, 0)

    out = {
        'updated': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'),
        'seasons': loaded, 'quarters': a.quarters,
        'source': 'nflverse play-by-play; charting CC-BY-SA 4.0, credit FTN Data via nflverse',
        'calls': list(CALLS), 'buckets': list(BUCKETS), 'depth': depth, 'plays': rows,
    }
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, 'plays.json')
    with open(path, 'w') as fh:
        json.dump(out, fh, separators=(',', ':'))
    print('  wrote %s - %d plays, %.1f MB' % (path, len(rows), os.path.getsize(path) / 1e6))
    print('  skipped %d plays with no drill call, no wpa or no down' % skipped)
    for c in sorted(by_call):
        print('    %-12s %6d' % (c, by_call[c]))

    # POOL DEPTH. This is the measurement that decides whether the pool is usable, so the build
    # prints it rather than leaving it to be discovered in play. A player picks a call while playing
    # as a team, so every (team, call) pair has to have real plays behind it - and a thin pair means
    # the drill either repeats the same play or has nothing to answer with. Measured: all of Q4
    # across four seasons gives a minimum of 41 and a median near 200, while the current season
    # alone, inside two minutes, collapses to a median of 3 with 22 pairs empty.
    if depth:
        pairs = [(t, b, depth[t][b]) for t in depth for b in BUCKETS]
        vals = sorted(v for _, _, v in pairs)
        thin = sorted([p for p in pairs if p[2] < MIN_BUCKET], key=lambda p: p[2])
        print('  depth: %d teams, %d of %d team-bucket pairs filled, smallest %d, median %d'
              % (len(depth), sum(1 for v in vals if v), len(pairs), vals[0], vals[len(vals) // 2]))
        brk = [(depth[t]['pass_broken'] /
                max(1, sum(depth[t][b] for b in ('pass_short', 'pass_deep', 'pass_broken'))), t)
               for t in depth]
        brk.sort()
        print('  broken-dropback rate: %s %.1f%% lowest, %s %.1f%% highest (the drill\'s sack risk)'
              % (brk[0][1], 100 * brk[0][0], brk[-1][1], 100 * brk[-1][0]))
        if thin:
            print('  WARNING: %d team-bucket pairs hold fewer than %d plays - the drill will repeat '
                  'itself there. Widen --seasons or --quarters.' % (len(thin), MIN_BUCKET))
            print('    thinnest: ' + ', '.join('%s %s %d' % p for p in thin[:6]))
    return 0


def selftest():
    """call_of has to bucket every play the way a player would expect, and never invent a call."""
    fails = []

    def ok(name, cond, note=''):
        print(('  ok   ' if cond else '  FAIL ') + name + (('  ' + note) if note else ''))
        if not cond:
            fails.append(name)

    ok('an inside run is an inside run',
       call_of({'play_type': 'run', 'run_gap': 'guard', 'run_location': 'middle'}) == 'run_inside')
    ok('a run round the end is an outside run',
       call_of({'play_type': 'run', 'run_gap': 'end', 'run_location': 'left'}) == 'run_outside')
    ok('a middle run with no gap charted still counts as inside',
       call_of({'play_type': 'run', 'run_gap': None, 'run_location': 'middle'}) == 'run_inside',
       '(charting is not complete; a run up the middle is not an end run)')
    ok('a deep pass is a deep pass',
       call_of({'play_type': 'pass', 'pass_length': 'deep'}) == 'pass_deep')
    ok('a short pass is a short pass',
       call_of({'play_type': 'pass', 'pass_length': 'short'}) == 'pass_short')
    ok('a dropback with no charted throw is kept, as its own bucket',
       call_of({'play_type': 'pass', 'pass_length': None}) == 'pass_broken',
       '(sacks and throwaways - without these a pass call could never go wrong)')
    ok('and that bucket is not offered as a call',
       'pass_broken' in BUCKETS and 'pass_broken' not in CALLS,
       '(nobody calls a sack; it is drawn at the team\'s own rate)')
    ok('a punt is not a drill call', call_of({'play_type': 'punt'}) is None)
    ok('a field goal is not a drill call', call_of({'play_type': 'field_goal'}) is None)
    ok('a kneel-down is not a drill call', call_of({'play_type': 'qb_kneel'}) is None)
    ok('a wiped-out play is not a drill call', call_of({'play_type': 'no_play'}) is None)
    ok('it never returns a bucket the pool does not define',
       all(call_of(p) in BUCKETS or call_of(p) is None for p in [
           {'play_type': 'run', 'run_gap': 'tackle'}, {'play_type': 'run', 'run_location': None},
           {'play_type': 'pass', 'pass_length': 'short'}, {'play_type': 'pass'},
           {'play_type': 'extra_point'}, {'play_type': None}, {}]),
       '(' + ', '.join(BUCKETS) + ')')
    ok('every column it reads is one the build asks the parquet for',
       all(c in KEEP for c in ['play_type', 'run_gap', 'run_location', 'pass_length']),
       '(or call_of reads a column that was trimmed away and every play buckets as None)')

    print(('\nFAILED: ' + ', '.join(fails)) if fails else '\nall good')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main() or 0)
