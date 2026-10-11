#!/usr/bin/env python3
"""Refuse to publish a dataset that has quietly fallen apart.

    python3 scripts/check_data.py nba/data
    python3 scripts/check_data.py mlb/data --tol 0.4      # a known, intended shrink
    python3 scripts/check_data.py --selftest

WHY THIS EXISTS
Every data failure this project has had looked identical from the outside: the workflow ran, went
green, committed, and published something wrong. Nothing errored, because nothing was checked.

  * MLB's gamelogs went from thirty teams to eight, and then to two. The file was still large and
    still valid JSON; only the teams in it had gone. It sat like that for weeks.
  * The NBA build wrote to nba/data/nba/ for months - green every day, and nothing read a byte
    of it.
  * NFL gamelogs were missing 15% of the games they should have had, which nobody could see
    because the rows that were there looked fine.

So this runs AFTER the build and BEFORE `git add`. If a file that should only ever grow has
shrunk, or has lost teams, the job fails and the last good data stays published. A build that
publishes nothing is a bad day; a build that publishes a league with two teams in it is worse,
because the tool goes on answering questions with it.

WHAT IT WATCHES
Only files that accumulate. A fixture list shrinks all season as games are played, and odds and
injuries turn over every day - guarding those would cry wolf until nobody read the output.
"""
import argparse, json, os, subprocess, sys

# Files that should never shrink much: they accumulate as a season goes on.
WATCH = ('gamelogs', 'results', 'teams', 'teams_form', 'players', 'dvp', 'profiles',
         'quarters', 'zones', 'firstread', 'redzone', 'dbs', 'parks', 'umpires')
# Files that legitimately shrink or turn over, and must not be guarded or the guard gets ignored.
IGNORE = ('fixture', 'odds', 'odds_open', 'injury', 'lineups', 'meta', 'calib', 'weather',
          'goalies', 'ladder', 'special_teams', 'firsttd')

MIN_ROWS = 50          # below this a file is too small for a percentage to mean anything
MIN_TEAMS = 4


def _rows(obj):
    """How many records a file holds, whatever shape it was written in."""
    if isinstance(obj, list):
        return len(obj)
    if isinstance(obj, dict):
        best = 0
        for v in obj.values():
            if isinstance(v, (list, dict)):
                best = max(best, len(v))
        return best
    return 0


def _teams(obj):
    """Distinct clubs in a file, where it names them.

    This is the check that would have caught MLB first: the row count was still in five figures
    while the number of teams had gone from thirty to eight. A file can keep its bulk and lose
    the thing that makes it a league.
    """
    rows = obj if isinstance(obj, list) else None
    if rows is None and isinstance(obj, dict):
        for v in obj.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                rows = v
                break
    if not rows:
        return None
    seen = set()
    for r in rows:
        if not isinstance(r, dict):
            return None
        t = r.get('Team') or r.get('team') or r.get('home')
        if t:
            seen.add(str(t).upper())
    return len(seen) or None


def _watched(name):
    stem = os.path.basename(name)[:-5] if name.endswith('.json') else os.path.basename(name)
    base = stem.split('_')[0]
    if stem in IGNORE or base in IGNORE:
        return False
    return stem in WATCH or base in WATCH


def _prev_from_git(path):
    try:
        out = subprocess.run(['git', 'show', 'HEAD:' + path], capture_output=True, timeout=120)
        if out.returncode != 0:
            return None
        return json.loads(out.stdout.decode('utf-8'))
    except Exception:
        return None


# A file big enough to be a season, covering a handful of clubs, is contradicting itself - no
# history needed to see it. The floor keeps opening night out of it: a 40-row file covering two
# teams is the first game of the year, a 3,000-row one is a build that has lost the league.
BIG_ENOUGH = 500
COVER = 0.6


def check(data_dir, tol=0.25, prev_dir=None, quiet=False):
    """Returns a list of complaints; empty means the data is fit to publish."""
    bad, looked = [], 0
    if not os.path.isdir(data_dir):
        return ['%s does not exist' % data_dir]

    # how many clubs this sport has, from its own teams file
    league = None
    tp = os.path.join(data_dir, 'teams.json')
    if os.path.exists(tp):
        try:
            with open(tp, encoding='utf-8') as fh:
                league = _rows(json.load(fh))
        except Exception:
            league = None
    for fn in sorted(os.listdir(data_dir)):
        if not fn.endswith('.json') or not _watched(fn):
            continue
        path = os.path.join(data_dir, fn)
        try:
            with open(path, encoding='utf-8') as fh:
                new = json.load(fh)
        except Exception as e:
            bad.append('%s is unreadable (%s)' % (path, e))
            continue
        old = (_prev_from_git(path) if prev_dir is None
               else (json.load(open(os.path.join(prev_dir, fn), encoding='utf-8'))
                     if os.path.exists(os.path.join(prev_dir, fn)) else None))
        if old is None:
            nr0, nt0 = _rows(new), _teams(new)
            if (league and league >= MIN_TEAMS and nt0 and nr0 >= BIG_ENOUGH
                    and nt0 < league * COVER and fn != 'teams.json'):
                bad.append('%s covers only %d of the league\'s %d clubs across %d rows - the file '
                           'is big enough to be a season but has lost most of the league'
                           % (fn, nt0, league, nr0))
            elif not quiet:
                print('  %-24s new file (%d rows) - nothing to compare' % (fn, nr0))
            continue
        looked += 1
        nr, orr = _rows(new), _rows(old)
        nt, ot = _teams(new), _teams(old)
        note = '%-24s %d rows (was %d)' % (fn, nr, orr)
        if nt and ot:
            note += ', %d teams (was %d)' % (nt, ot)
        # the check that needs no previous version at all
        if (league and league >= MIN_TEAMS and nt and nr >= BIG_ENOUGH
                and nt < league * COVER and fn != 'teams.json'):
            bad.append('%s covers only %d of the league\'s %d clubs across %d rows - the file is '
                       'big enough to be a season but has lost most of the league'
                       % (fn, nt, league, nr))
        elif orr >= MIN_ROWS and nr < orr * (1 - tol):
            bad.append('%s LOST %d%% of its rows: %d -> %d'
                       % (fn, round((1 - nr / orr) * 100), orr, nr))
        elif ot and nt and ot >= MIN_TEAMS and nt < ot * (1 - tol):
            bad.append('%s LOST teams: %d -> %d (rows %d -> %d, so the file still looks healthy)'
                       % (fn, ot, nt, orr, nr))
        elif nr == 0 and orr > 0:
            bad.append('%s is now empty (was %d rows)' % (fn, orr))
        elif not quiet:
            print('  ' + note)
    if not looked and not bad and not quiet:
        print('  nothing to compare against - first run, or no watched files')
    return bad


def selftest():
    import shutil, tempfile
    print('selftest: building synthetic data...')
    tmp = tempfile.mkdtemp()
    old_d, new_d = os.path.join(tmp, 'old'), os.path.join(tmp, 'new')
    os.makedirs(old_d); os.makedirs(new_d)
    def w(d, fn, obj):
        with open(os.path.join(d, fn), 'w') as fh: json.dump(obj, fh)

    full = [{'Team': 'T%d' % (i % 30), 'x': i} for i in range(600)]
    w(old_d, 'gamelogs.json', full)
    w(new_d, 'gamelogs.json', full + [{'Team': 'T1', 'x': 999}])
    assert check(new_d, prev_dir=old_d, quiet=True) == [], 'growth is fine'

    # the MLB failure exactly: the same number of rows, and the league has gone. The row count
    # is untouched here on purpose - that is what made it invisible for weeks.
    w(new_d, 'gamelogs.json', [{'Team': 'T%d' % (i % 2), 'x': i} for i in range(600)])
    out = check(new_d, prev_dir=old_d, quiet=True)
    assert any('LOST teams' in m for m in out), 'a league losing its teams must fail: %s' % out

    # the plain shrink
    w(new_d, 'gamelogs.json', full[:100])
    out = check(new_d, prev_dir=old_d, quiet=True)
    assert any('LOST' in m for m in out), 'a 83%% row drop must fail: %s' % out

    # an intended shrink inside tolerance passes (players 550 -> 466 when off-roster men were cut)
    w(old_d, 'players.json', [{'team': 'T%d' % (i % 30)} for i in range(550)])
    w(new_d, 'players.json', [{'team': 'T%d' % (i % 30)} for i in range(466)])
    w(new_d, 'gamelogs.json', full)
    assert check(new_d, prev_dir=old_d, quiet=True) == [], 'a 15% drop is within tolerance'

    # a fixture list shrinking all season must NOT be guarded, or nobody reads the output
    w(old_d, 'fixture.json', [{'home': 'T1'} for _ in range(400)])
    w(new_d, 'fixture.json', [{'home': 'T1'} for _ in range(12)])
    assert check(new_d, prev_dir=old_d, quiet=True) == [], 'fixtures are allowed to shrink'

    # an emptied file is caught even when it is small
    w(old_d, 'teams.json', [{'team': 'T%d' % i} for i in range(30)])
    w(new_d, 'teams.json', [])
    out = check(new_d, prev_dir=old_d, quiet=True)
    assert any('empty' in m or 'LOST' in m for m in out), 'an emptied file must fail: %s' % out

    # a season-sized file covering two clubs is caught with no history at all - this is the
    # state MLB is in right now, which a shrink check can never see because it is the status quo
    w(new_d, 'teams.json', [{'team': 'T%d' % i} for i in range(30)])
    w(new_d, 'gamelogs.json', [{'Team': 'T%d' % (i % 2), 'x': i} for i in range(3000)])
    out = check(new_d, prev_dir=os.path.join(tmp, 'empty'), quiet=True)
    assert any('lost most of the league' in m for m in out), \
        'a big file covering two clubs must fail with no history: %s' % out
    # but opening night must not trip it
    w(new_d, 'gamelogs.json', [{'Team': 'T%d' % (i % 2), 'x': i} for i in range(40)])
    out = check(new_d, prev_dir=os.path.join(tmp, 'empty'), quiet=True)
    assert not any('lost most of the league' in m for m in out), \
        'the first game of a season is not a broken build: %s' % out

    shutil.rmtree(tmp)
    print('selftest: ALL PASS ok')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('dirs', nargs='*', help='data directories, e.g. nba/data')
    ap.add_argument('--tol', type=float, default=0.25,
                    help='how much a file may shrink before the build is refused (default 0.25)')
    ap.add_argument('--prev', help='compare against this directory instead of git HEAD (testing)')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        selftest(); sys.exit(0)
    if not a.dirs:
        ap.error('give at least one data directory')
    problems = []
    for d in a.dirs:
        print('checking %s (tolerance %d%%)' % (d, round(a.tol * 100)))
        problems += [d + ': ' + m for m in check(d, tol=a.tol, prev_dir=a.prev)]
    if problems:
        print('\nREFUSING TO PUBLISH - the build produced data that looks broken:')
        for m in problems:
            print('  * ' + m)
        print('\nThe previously published data is untouched, so the tool is still answering\n'
              'questions with the last good version. A SHRINK that was intended can be waved\n'
              'through by re-running with a higher --tol; a file that has LOST MOST OF THE\n'
              'LEAGUE cannot, because no tolerance makes two clubs a season - that one is the\n'
              'build or its upstream feed, and needs fixing.')
        sys.exit(1)
    print('data looks sane')
