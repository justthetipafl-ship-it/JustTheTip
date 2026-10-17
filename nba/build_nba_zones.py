#!/usr/bin/env python3
"""NBA shot zones, from ESPN play-by-play coordinates.

    python nba/build_nba_zones.py --out nba/data [--seasons 2026,2027]
    python nba/build_nba_zones.py --out nba/data --seasons 2023,2024,2025,2026 --per-season
    python nba/build_nba_zones.py --out nba/data --persist      # offline, reads what the above wrote
    python nba/build_nba_zones.py --selftest                    # offline, no network, no pandas

DOES A DEFENCE'S ZONE RECORD PERSIST?
The page shows a shot-zone matchup as a READ, deliberately carrying no price, because the number
that would make it a signal has never been measured: whether what a defence conceded at the rim
last season tells you anything about what it will concede next season. A single season of zone
percentages describes the past perfectly well and may predict nothing at all - defences change
personnel and scheme, and a season of opponent shooting carries a great deal of luck.

--per-season keeps each season's defensive block separately instead of pooling them, which is all
the main build needs to change. --persist then reads that file with no network and no pandas and
answers the question directly: for every pair of consecutive seasons it regresses a team's gap to
the league in season N+1 on the same gap in season N. THE SLOPE IS THE ANSWER. A slope of 1 means
the gap carries over whole and the matchup read is worth its face value; 0.3 means only a third of
it survives and every swing the page computes should be multiplied by 0.3; 0 means last season's
zone record is worth nothing about next season and the read should never become a signal.

WHY
Two players averaging the same points are different bets if one lives at the rim and the other
settles for long twos. Zones say where the points come from, and against a defence that concedes
at the rim or runs you off the line, that is the read.

GEOMETRY
ESPN gives half-court feet: x across the floor 0-50, y from the baseline. The basket sits at
(25, 1) - checked against the data rather than assumed, because dunks plot at exactly that point.
A wrong origin smears the zones into each other: at (25, 5.25) every dunk measured 4.2 feet out.

ZONES
  rim         within 4 feet
  paint       inside the lane, out to the free-throw line
  midRange    two-pointers beyond the paint
  cornerThree threes taken from low along the baseline (within 14 feet of it)
  armsThree   every other three - above the break
Free throws are excluded: they are not field goals and have no location worth plotting.
"""
import argparse
import datetime
import io
import json
import os
import random
import sys
import urllib.request

REL = 'https://github.com/sportsdataverse/sportsdataverse-data/releases/download'
PBP = REL + '/espn_nba_pbp/play_by_play_%s.parquet'
BOX = REL + '/espn_nba_player_boxscores/player_box_%s.parquet'
# Checked, not assumed: dunks plot at a median of exactly (25, 1), so that IS the basket on ESPN's
# grid. Using the real-world 5.25-foot rim offset put every dunk 4.2 feet from the hoop, pushed a
# third of the restricted area into "paint", and left rim shooting at 56% when it should be ~65%.
HOOP_X, HOOP_Y = 25.0, 1.0
LANE_MIN, LANE_MAX = 17.0, 33.0      # the lane is 16 feet wide, centred
FT_LINE = 19.0                       # free-throw line, feet from the baseline
CORNER_Y = 14.0                      # a three from below this is a corner three


def grab(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (JTT build)'})
    with urllib.request.urlopen(req, timeout=300) as r:
        return io.BytesIO(r.read())


def pid(v):
    t = str(v).strip()
    if t.endswith('.0'):
        t = t[:-2]
    return t if t.isdigit() else ''


def zone_of(x, y, dist, is_three):
    if is_three:
        return 'cornerThree' if y <= CORNER_Y else 'armsThree'
    if dist <= 4.0:
        return 'rim'
    if LANE_MIN <= x <= LANE_MAX and y <= FT_LINE:
        return 'paint'
    return 'midRange'


# ALL-STAR TEAMS ARE NOT DEFENCES. The play-by-play carries the exhibition games with the rest,
# and their "clubs" - EAST, WEST, GIA, LEB - arrive looking like any other team. Nobody guards
# anybody in them, so they posted rim percentages of 82, 90 and 81, which placed them at the bottom
# of every ranking and pushed every real club up by as many as three places: the file said "2nd of
# 33" where the truth was 2nd of 30. The 50-attempt floor below was no help at all, because an
# All-Star game still produces 57 rim attempts.
#
# The separation is not marginal and needs no list of names to maintain: across five seasons the
# largest exhibition entry faced 67 shots in total and the smallest real club faced 6,838. A floor
# of a thousand sits in the middle of a hundredfold gap.
MIN_DEF_ATT = 1000


def _ols(xs, ys):
    """Slope and Pearson r of y on x. Written out rather than imported because --persist has to
    run with no pandas and no network - the whole point is that it reads a committed file."""
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx <= 0 or syy <= 0:
        return None
    return {'n': n, 'slope': sxy / sxx, 'r': sxy / (sxx ** 0.5 * syy ** 0.5)}


def measure_persistence(by_season, zones):
    """For each zone, regress a team's gap to the league in season N+1 on the same gap in season N.

    THE GAP, not the raw percentage. League shooting moves between seasons, so a team can concede
    a higher rim percentage two years running purely because the whole league shot better, and a
    regression on raw percentages would read that shared drift as persistence.

    The slope is what the page needs. It is the fraction of a measured gap that carries over, and
    therefore the factor every computed swing should be multiplied by before anybody calls it an
    edge."""
    seasons = sorted(by_season)
    out = {}
    for z in zones:
        xs, ys = [], []
        for i in range(len(seasons) - 1):
            a_s, b_s = by_season[seasons[i]], by_season[seasons[i + 1]]
            la, lb = (a_s.get('league') or {}).get(z), (b_s.get('league') or {}).get(z)
            if la is None or lb is None:
                continue
            for team, za in (a_s.get('teams') or {}).items():
                zb = (b_s.get('teams') or {}).get(team)
                if not zb or z not in za or z not in zb:
                    continue
                # a file written before MIN_DEF_ATT existed still carries the exhibition sides
                if (za[z].get('att', 0) < 50 or zb[z].get('att', 0) < 50):
                    continue
                xs.append(za[z]['pct'] - la)
                ys.append(zb[z]['pct'] - lb)
        fit = _ols(xs, ys)
        if fit:
            fit['meanAbsGap'] = sum(abs(x) for x in xs) / len(xs)
            out[z] = fit
    return out


def persist(out_dir):
    path = os.path.join(out_dir, 'zones_by_season.json')
    if not os.path.exists(path):
        print('no %s - run the build with --per-season first' % path)
        return
    with open(path) as fh:
        d = json.load(fh)
    by, zones = d.get('bySeason') or {}, d.get('zones') or []
    if len(by) < 2:
        # Not an error. There is simply nothing to measure yet, and failing the workflow over it
        # marks a perfectly good data build as broken.
        print('only %d season(s) in %s - persistence needs at least two consecutive ones, so'
              % (len(by), path))
        print('there is nothing to measure yet. Rebuild with more seasons when you want the test:')
        print('  python3 nba/build_nba_zones.py --out %s --seasons 2022,2023,2024,2025,2026 '
              '--per-season' % out_dir)
        return
    res = measure_persistence(by, zones)
    print('zone defence, season to season (%s)' % ', '.join(sorted(by)))
    print('  %-12s %5s %7s %7s  %s' % ('zone', 'n', 'slope', 'r', 'what it means'))
    for z in zones:
        f = res.get(z)
        if not f:
            print('  %-12s     - not enough paired seasons' % z)
            continue
        sl = f['slope']
        verdict = ('carries over almost whole' if sl >= 0.7 else
                   'about half carries over' if sl >= 0.4 else
                   'mostly washes out' if sl >= 0.15 else
                   'last season says nothing about next')
        print('  %-12s %5d %7.2f %7.2f  %s' % (z, f['n'], sl, f['r'], verdict))
    keep = {z: round(max(0.0, min(1.0, f['slope'])), 3) for z, f in res.items()}
    best = max(keep.values()) if keep else 0.0
    print('')
    print('SHRINKAGE FACTORS (multiply the page\'s computed swing by these):')
    print('  ' + '  '.join('%s %.2f' % (z, keep[z]) for z in zones if z in keep))
    print('')
    # THIS ANSWERS ONE QUESTION ONLY: does a gap persist. It does NOT say whether what survives
    # is big enough to bet, and the first version of this text blurred the two - it printed
    # "strong enough to be worth pricing" off a rim slope of 0.60, when applying that same 0.60
    # to a real shot diet left the largest edge in the league at 0.47 points, far inside the vig.
    # Persistence is necessary and nowhere near sufficient. Say what was measured, and name the
    # second question rather than appearing to have answered it.
    if best < 0.15:
        print('MEASURED: no zone carries over. Last season\'s zone record says nothing about next,')
        print('          so there is no basis for a priced tile and nothing further to check.')
    elif best < 0.4:
        print('MEASURED: weak persistence - the best zone keeps %.0f%% of its gap.' % (best * 100))
        print('          Most of what a single season shows is noise.')
    else:
        print('MEASURED: the interior zones keep about half their gap, which is real.')
        print('          THIS IS NOT YET AN EDGE. It says the defensive number means something,')
        print('          not that what survives is big enough to bet. The second question is what')
        print('          the shrunk swing comes to on an actual shot diet - when that was measured')
        print('          the largest in the league was 0.47 points, which is inside the margin.')
    print('          Multiply every computed swing by the factors above before judging it.')
    with open(os.path.join(out_dir, 'zone_persistence.json'), 'w') as fh:
        json.dump({'measured': res, 'keep': keep, 'seasons': sorted(by),
                   'updated': datetime.datetime.now(datetime.timezone.utc)
                               .strftime('%Y-%m-%dT%H:%MZ')}, fh, indent=1)
    print('wrote %s' % os.path.join(out_dir, 'zone_persistence.json'))


def selftest():
    """The persistence maths, on data whose answer is known in advance. This exists because the
    whole decision - price it or leave it as a read - rests on one number, and a regression that
    is subtly wrong produces a number that looks perfectly reasonable."""
    ok = [True]

    def check(name, cond, detail=''):
        print(('  ok   ' if cond else '  FAIL ') + name + (('  ' + detail) if detail else ''))
        if not cond:
            ok[0] = False

    zones = ['rim']
    teams = ['T%02d' % i for i in range(30)]

    def build(gaps_a, gaps_b, la=0.66, lb=0.70):
        return {'2024': {'league': {'rim': la},
                         'teams': {t: {'rim': {'att': 500, 'pct': la + g}}
                                   for t, g in zip(teams, gaps_a)}},
                '2025': {'league': {'rim': lb},
                         'teams': {t: {'rim': {'att': 500, 'pct': lb + g}}
                                   for t, g in zip(teams, gaps_b)}}}

    base = [(-0.06 + 0.004 * i) for i in range(30)]

    # a gap that repeats exactly must measure as carrying over whole
    r = measure_persistence(build(base, base), zones)['rim']
    check('a gap that repeats exactly scores a slope of 1', abs(r['slope'] - 1.0) < 1e-9,
          '(slope %.3f, r %.3f, n %d)' % (r['slope'], r['r'], r['n']))

    # half the gap repeating must measure as half
    r = measure_persistence(build(base, [g * 0.5 for g in base]), zones)['rim']
    check('half of it carrying over scores a slope of 0.5', abs(r['slope'] - 0.5) < 1e-9,
          '(slope %.3f)' % r['slope'])

    # A second season unrelated to the first must measure near zero. The obvious way to build one
    # - rotating the list - is not unrelated at all: rotating a monotonic sequence produces a
    # slope of -0.50, which this test duly reported as a failure of the maths when it was a
    # failure of the fixture. A seeded shuffle is genuinely unstructured and still deterministic.
    shuffled = base[:]
    random.Random(1).shuffle(shuffled)
    r = measure_persistence(build(base, shuffled), zones)['rim']
    check('an unrelated second season scores near zero', abs(r['slope']) < 0.35,
          '(slope %.3f)' % r['slope'])

    # THE ONE THAT MATTERS: league-wide drift must not be read as persistence. Every team's raw
    # percentage rises by four points between the seasons while the gaps are unrelated; regressing
    # raw percentages would see a strong relationship that is not there.
    r = measure_persistence(build(base, shuffled, la=0.66, lb=0.70), zones)['rim']
    check('a league-wide shift is not mistaken for persistence', abs(r['slope']) < 0.35,
          '(slope %.3f - both seasons are centred on their own league rate)' % r['slope'])
    # and the same drift with a gap that DOES repeat must still read as repeating, so the
    # centring cannot be hiding a real relationship as well as a spurious one
    r = measure_persistence(build(base, base, la=0.66, lb=0.70), zones)['rim']
    check('while a real relationship survives the same shift', abs(r['slope'] - 1.0) < 1e-9,
          '(slope %.3f)' % r['slope'])

    # and a team missing from one season is simply not paired, rather than counted as a zero
    d = build(base, base)
    del d['2025']['teams']['T00']
    r = measure_persistence(d, zones)['rim']
    check('a team absent from one season is left out of the pairing', r['n'] == 29,
          '(n %d of 30)' % r['n'])

    # too few pairs must return nothing rather than a confident number off three teams
    d = build(base[:2], base[:2])
    check('two teams is not enough to fit anything', 'rim' not in measure_persistence(d, zones))

    print('\n' + ('ALL GREEN' if ok[0] else 'FAILED'))
    sys.exit(0 if ok[0] else 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='nba/data')
    ap.add_argument('--seasons', default=None)
    ap.add_argument('--min-shots', type=int, default=25)
    ap.add_argument('--pool-seasons', default=None,
                    help='which of --seasons feed the DISPLAYED blocks (default: the two most '
                         'recent read). '
                         'The persistence fit wants years of history; the page wants recent form, '
                         'and pooling five seasons of shot diet into zones.json makes every '
                         'profile stale.')
    ap.add_argument('--per-season', action='store_true',
                    help='also write zones_by_season.json: each season kept apart, for --persist')
    ap.add_argument('--persist', action='store_true',
                    help='offline: read zones_by_season.json and measure whether zone defence carries over')
    ap.add_argument('--selftest', action='store_true', help='offline checks on the persistence maths')
    a = ap.parse_args()
    if a.selftest:
        selftest(); return
    if a.persist:
        persist(a.out); return
    try:
        import pandas as pd
        import numpy as np
    except ImportError:
        print('pandas, numpy and pyarrow are required'); sys.exit(1)

    today = datetime.date.today()
    nxt = today.year + 1 if today.month >= 9 else today.year
    seasons = [s.strip() for s in (a.seasons or '%d,%d' % (nxt - 1, nxt)).split(',') if s.strip()]
    # READ these, but POOL only these into what the page shows. A history run reads five seasons so
    # the persistence fit has pairs to work with, and must still publish a defence profile and a
    # shot diet from recent basketball - a 2022 shot chart is not this player.
    pool = set(s.strip() for s in (a.pool_seasons or '').split(',') if s.strip())
    if not pool:
        # DEFAULT: the two most recent seasons read, not all of them. Asking for five seasons means
        # a persistence fit, and the pooled blocks that ship to the page must still describe recent
        # basketball - a 2022 shot chart is not this player and a 2022 defence is not this team.
        # Relying on the caller to remember this is exactly how quarters.json came to average Luka
        # Doncic over 361 games.
        pool = set(sorted(seasons)[-2:]) if len(seasons) > 2 else set(seasons)
    unknown = pool - set(seasons)
    if unknown:
        print('--pool-seasons names seasons that are not being read: %s' % ', '.join(sorted(unknown)))
        sys.exit(1)
    print('zones: reading %s | pooling %s into zones.json'
          % (', '.join(seasons), ', '.join(sorted(pool))))

    ZONES = ['rim', 'paint', 'midRange', 'cornerThree', 'armsThree']
    names, acc, lg, done = {}, {}, {}, []
    # what each DEFENCE concedes by zone. Player zones say where he shoots; this says who lets him.
    # The shooting team is on the play and both clubs are on the row, so the defence is the other one.
    dfn = {}
    # and the same thing kept per season, which is what --persist needs. The pooled block above is
    # untouched, so zones.json comes out identical whether or not this is asked for.
    dfn_season, lg_season = {}, {}

    for season in seasons:
        try:
            d = pd.read_parquet(grab(PBP % season), columns=[
                'type_text', 'text', 'score_value', 'scoring_play', 'shooting_play',
                'coordinate_x_raw', 'coordinate_y_raw', 'athlete_id_1',
                'team_id', 'home_team_id', 'home_team_abbrev', 'away_team_abbrev'])
        except Exception as e:
            print('  %s: play-by-play not available (%s)' % (season, str(e)[:60])); continue
        try:
            box = pd.read_parquet(grab(BOX % season),
                                  columns=['athlete_id', 'athlete_display_name', 'team_abbreviation'])
            for _, r in box.drop_duplicates(subset=['athlete_id']).iterrows():
                k = pid(r['athlete_id'])
                if k:
                    names[k] = {'name': str(r['athlete_display_name']),
                                'team': str(r.get('team_abbreviation') or '')}
        except Exception:
            pass

        sh = d[d.shooting_play.fillna(False).astype(bool)].copy()
        sh = sh[~sh.type_text.astype(str).str.startswith('Free Throw')]
        sh = sh[sh.coordinate_x_raw.notna() & sh.coordinate_y_raw.notna()]
        sh['dist'] = np.hypot(sh.coordinate_x_raw - HOOP_X, sh.coordinate_y_raw - HOOP_Y)
        made = sh.scoring_play.fillna(False).astype(bool)
        # A make carries score_value; a MISS does not, and guessing from distance put every missed
        # corner three (about 22 feet, and some charted nearer) into mid-range - which is why
        # mid-range came out at 23% shooting and the paint at 57%. The description says what the
        # shot was: "misses 26-foot three point jumper".
        txt = sh.text.astype(str).str.lower()
        is3 = (sh.score_value == 3) | txt.str.contains('three point', regex=False)
        print('  %s: %d field-goal attempts' % (season, len(sh)))

        shooting_home = (sh.team_id.astype('Int64') == sh.home_team_id.astype('Int64'))
        for (x, y, dist, three, hit, who, is_home, hab, aab) in zip(
                sh.coordinate_x_raw, sh.coordinate_y_raw, sh.dist, is3, made, sh.athlete_id_1,
                shooting_home, sh.home_team_abbrev, sh.away_team_abbrev):
            z0 = zone_of(float(x), float(y), float(dist), bool(three))
            # the defending club is whichever side did not take the shot
            dteam = (str(aab) if bool(is_home) else str(hab))
            in_pool = season in pool
            if dteam and dteam != 'nan':
                if in_pool:
                    e2 = dfn.setdefault(dteam, {}).setdefault(z0, {'att': 0, 'made': 0})
                    e2['att'] += 1
                    if hit:
                        e2['made'] += 1
                e2s = dfn_season.setdefault(season, {}).setdefault(dteam, {}) \
                                .setdefault(z0, {'att': 0, 'made': 0})
                e2s['att'] += 1
                if hit:
                    e2s['made'] += 1
            ls = lg_season.setdefault(season, {}).setdefault(z0, {'att': 0, 'made': 0})
            ls['att'] += 1
            if hit:
                ls['made'] += 1
            k = pid(who)
            if not k or not in_pool:
                continue
            z = z0
            e = acc.setdefault(k, {})
            d2 = e.setdefault(z, {'att': 0, 'made': 0, 'dist': 0.0})
            d2['att'] += 1
            d2['dist'] += float(dist)
            if hit:
                d2['made'] += 1
            l = lg.setdefault(z, {'att': 0, 'made': 0})
            l['att'] += 1
            if hit:
                l['made'] += 1
        done.append(season)

    if not acc:
        print('nothing aggregated - no file written'); sys.exit(1)

    league = {z: {'att': v['att'], 'pct': round(v['made'] / v['att'], 4) if v['att'] else None}
              for z, v in lg.items()}
    total_att = sum(v['att'] for v in lg.values()) or 1

    rows = []
    for who, zs in acc.items():
        tot = sum(z['att'] for z in zs.values())
        if tot < a.min_shots:
            continue
        who_name = names.get(who, {})
        row = {'playerId': who, 'name': who_name.get('name', ''), 'team': who_name.get('team', ''),
               'shots': tot}
        for z in ZONES:
            v = zs.get(z)
            if not v or not v['att']:
                row[z] = {'att': 0, 'share': 0, 'pct': None, 'dist': None}
                continue
            row[z] = {'att': v['att'],
                      'share': round(v['att'] / tot, 3),
                      'pct': round(v['made'] / v['att'], 3),
                      'dist': round(v['dist'] / v['att'], 1)}
        rows.append(row)
    rows.sort(key=lambda r: -r['shots'])

    os.makedirs(a.out, exist_ok=True)
    # defences, with a rank per zone: 1 concedes least, 30 concedes most
    defence = {}
    real_def = {t for t, v in dfn.items() if sum(e['att'] for e in v.values()) >= MIN_DEF_ATT}
    dropped = sorted(set(dfn) - real_def)
    if dropped:
        print('  not defences, left out: %s' % ', '.join(dropped))
    for z in ZONES:
        vals = {t: (v[z]['made'] / v[z]['att']) for t, v in dfn.items()
                if t in real_def and v.get(z, {}).get('att', 0) >= 50}
        order = sorted(vals.items(), key=lambda kv: kv[1])
        for i, (t, pct) in enumerate(order):
            e3 = defence.setdefault(t, {'team': t})
            e3[z] = {'pct': round(pct, 3), 'rank': i + 1, 'of': len(order),
                     'att': dfn[t][z]['att']}

    out = {'seasons': [x for x in done if x in pool], 'readSeasons': done, 'zones': ZONES, 'minShots': a.min_shots, 'defence': defence,
           'updated': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'),
           'source': 'ESPN play-by-play via sportsdataverse-data',
           'league': league, 'leagueShots': total_att, 'players': rows}
    with open(os.path.join(a.out, 'zones.json'), 'w') as fh:
        json.dump(out, fh, separators=(',', ':'))
    print('  wrote zones.json - %d players, %d defences' % (len(rows), len(defence)))

    if a.per_season and len(done) < 2:
        # A one-season file is useless for the fit AND it would overwrite a good one. That is the
        # worst possible outcome: the run that asked for five seasons but silently got one would
        # destroy the history it was meant to extend. This happened - the default window asks for
        # the season about to start, which has no play-by-play until opening night, so a dispatch
        # with the history box left empty read 2026 alone.
        print('  --per-season asked for, but only %d season(s) had play-by-play (%s).'
              % (len(done), ', '.join(done) or 'none'))
        print('  NOT writing zones_by_season.json - a one-season file cannot be fitted and would')
        print('  replace a good one. Pass --seasons with the years you want, e.g.')
        print('    --seasons 2022,2023,2024,2025,2026 --per-season')
    elif a.per_season:
        by = {}
        for season, teams in dfn_season.items():
            lgz = {z: (v['made'] / v['att']) for z, v in lg_season.get(season, {}).items() if v['att']}
            blk = {}
            for t, zs in teams.items():
                # exhibition sides are not defences - see MIN_DEF_ATT above
                if sum(v['att'] for v in zs.values()) < MIN_DEF_ATT:
                    continue
                # the same 50-attempt floor the pooled block uses: below it a percentage is noise
                row = {z: {'att': v['att'], 'pct': round(v['made'] / v['att'], 4)}
                       for z, v in zs.items() if v['att'] >= 50}
                if row:
                    blk[t] = row
            by[season] = {'teams': blk,
                          'league': {z: round(v, 4) for z, v in lgz.items()}}
        path = os.path.join(a.out, 'zones_by_season.json')
        with open(path, 'w') as fh:
            json.dump({'zones': ZONES, 'seasons': done, 'bySeason': by,
                       'updated': datetime.datetime.now(datetime.timezone.utc)
                                   .strftime('%Y-%m-%dT%H:%MZ')}, fh, separators=(',', ':'))
        print('  wrote zones_by_season.json - %s' % ', '.join(
            '%s:%d teams' % (k, len(v['teams'])) for k, v in sorted(by.items())))
        print('  now run:  python3 nba/build_nba_zones.py --out %s --persist' % a.out)
    print('  league: ' + '  '.join('%s %d%% (%d%% of shots)'
          % (z, round((league[z]['pct'] or 0) * 100), round(league[z]['att'] / total_att * 100))
          for z in ZONES if z in league))


if __name__ == '__main__':
    main()
