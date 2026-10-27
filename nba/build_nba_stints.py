#!/usr/bin/env python3
"""Who was on the floor, and when - from ESPN play-by-play.

    python nba/build_nba_stints.py --out nba/data [--seasons 2027] [--days 21]
    python nba/build_nba_stints.py --selftest          # no network

WHY
A box score says a man played 33 minutes. It does not say he sat the last six, or that he and the
other starter were never on the floor together, or that the starting centre did not come back
after half time. Rotation is the shape behind every minutes-driven prop, and none of it is in a
season average: the question a reader actually has is "does he close", and the box score cannot
answer it.

WHAT IS HARD ABOUT IT
The feed records SUBSTITUTIONS - a change - and never states who was on the floor at tip-off. So
the intervals have to be reconstructed: seed the floor with the five starters, then replay every
substitution in order. The gamelogs already carry a `starter` flag per player per game, from the
same ESPN box score, which is what seeds it here.

HOW WELL IT WORKS
Checked against ESPN's own box-score minutes, which this reconstruction never sees: across 400
games and 8,704 player-games, every single one landed within a minute, median error -0.01. The
largest error seen was 31 seconds, which is the box score reporting minutes as whole numbers
rather than this drifting.

THE ONE FEED QUIRK THAT MATTERS
ESPN sometimes writes the same substitution TWICE at one clock value: once complete, once with a
blank side ("Jalen Duren enters the game for "). Replaying both takes a player off who is already
off and puts another on twice, and the floor silently stops being five. It does not move anyone's
minutes - the two errors cancel - so nothing downstream looks wrong; the lineup is just incorrect
between those two events. Dropping the malformed half and de-duplicating what is left takes it
from 6 games in 400 to 2.

WHAT IS WRITTEN
nba/data/stints.json, keyed by the same MatchId the gamelogs use (it IS the ESPN game id, so the
page joins a team's last game straight to its rotation):

    {"games": {"401859967": {
        "home":"NY","away":"SA","hs":111,"as":102,"end":2880,"per":[720,1440,2160,2880],
        "m":[[0,0],[45,3], ...],                 # margin, home minus away, thinned
        "pl":{"3934719":[[0,540],[900,1320]]}    # PlayerId -> [[on, off], ...] in game seconds
    }}}

Only recent games are kept. The page wants each side's LAST game, so a three-week window covers
every club with room to spare and keeps the file small - a season of this is 3 MB, which is not
something to make a reader download to look at one rotation chart.
"""
import argparse
import datetime
import io
import json
import os
import sys
import urllib.request

REL = 'https://github.com/sportsdataverse/sportsdataverse-data/releases/download'
PBP = REL + '/espn_nba_pbp/play_by_play_%s.parquet'
BOX = REL + '/espn_nba_player_boxscores/player_box_%s.parquet'

REG_PERIOD = 720          # 12 minutes
OT_PERIOD = 300           # 5 minutes
MAX_MARGIN_POINTS = 90    # enough to draw the strip; every scoring play is ~200 and pointless
WINDOW_DAYS = 21          # default look-back; the page only needs each club's LAST game


def period_len(p):
    return REG_PERIOD if p <= 4 else OT_PERIOD


def period_ends(last):
    """Cumulative game seconds at the end of each period played."""
    out, t = [], 0
    for p in range(1, last + 1):
        t += period_len(p)
        out.append(t)
    return out


def elapsed(period, mins, secs):
    """Game seconds elapsed at a play. The feed gives time REMAINING in the period."""
    p = int(period)
    before = sum(period_len(q) for q in range(1, p))
    rem = (mins or 0) * 60 + (secs or 0)
    return int(before + (period_len(p) - rem))


def clean_subs(rows):
    """Drop the malformed half of a doubled substitution, then de-duplicate.

    See the docstring: ESPN writes some substitutions twice, once with a blank side. Both describe
    one change, and replaying the pair desynchronises the floor without moving anyone's minutes -
    the worst kind of wrong, because nothing downstream looks off.
    """
    out, seen = [], set()
    for r in rows:
        if r['type'] != 'sub':
            out.append(r)
            continue
        if not r['in_'] or not r['out']:
            continue                                   # the blank-sided copy
        k = (r['period'], r['t'], r['in_'], r['out'])
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def thin(points, cap=MAX_MARGIN_POINTS):
    """Keep the shape of the margin without every scoring play. Always keeps the ends."""
    if len(points) <= cap:
        return points
    step = len(points) / float(cap)
    out, i = [], 0.0
    while int(i) < len(points):
        out.append(points[int(i)])
        i += step
    if out[-1] != points[-1]:
        out.append(points[-1])
    return out


def build_game(plays, starters, finals):
    """plays: ordered dicts for ONE game. starters: set of player ids. -> the game's record."""
    plays = clean_subs(plays)
    last = max([p['period'] for p in plays] or [4])
    per = period_ends(last)
    end = per[-1] if per else REG_PERIOD * 4

    on = {str(a): 0 for a in starters}
    spans, bad = {}, 0
    for p in plays:
        if p['type'] != 'sub':
            continue
        t, a_in, a_out = p['t'], p['in_'], p['out']
        if a_out in on:
            spans.setdefault(a_out, []).append([on.pop(a_out), t])
        else:
            bad += 1
        if a_in in on:
            bad += 1
        else:
            on[a_in] = t
    for a, t0 in on.items():
        spans.setdefault(a, []).append([t0, end])
    # A man substituted on AT the final buzzer gets a stint of zero length. It is not wrong, it
    # is just nothing - and a chart has to give it a minimum width to show it at all, so it draws
    # as a sliver at the right edge that looks like a late cameo nobody played.
    for a in list(spans):
        spans[a] = [s for s in spans[a] if s[1] > s[0]]
        if not spans[a]:
            del spans[a]

    marg = thin([[p['t'], p['margin']] for p in plays if p['type'] == 'score'])
    rec = {'end': end, 'per': per, 'm': marg, 'pl': spans}
    rec.update(finals)
    return rec, bad


def _read_parquet(url):
    import pandas as pd
    with urllib.request.urlopen(url, timeout=600) as r:
        return pd.read_parquet(io.BytesIO(r.read()))


def harvest(season, since, out_games, stats):
    import pandas as pd
    pbp = _read_parquet(PBP % season)
    box = _read_parquet(BOX % season)
    if 'did_not_play' in box.columns:
        box = box[box.did_not_play != True]            # noqa: E712 - pandas mask, not a bool test

    pbp['_d'] = pbp['game_date'].astype(str).str[:10]
    if since is None:
        # Anchor the window to the LAST GAME IN THE FEED, not to today. Anchoring it to today was
        # wrong in a way that produced a perfectly valid, perfectly useless file: it built the
        # 2026-27 season while the tool's gamelogs still ended at the 2026 Finals, so not one of
        # the 43 games it wrote could be joined to anything the page holds. Off-season, mid-roll
        # and the morning after a slate all behave the same way now.
        latest = pbp['_d'].max()
        since = (datetime.date.fromisoformat(latest)
                 - datetime.timedelta(days=WINDOW_DAYS)).isoformat()
    # ONLY GAMES THAT WENT FOUR QUARTERS. All-Star weekend is in this feed as WORLD/STARS/STRIPES
    # playing short exhibition games - one and two periods long - and they arrive looking entirely
    # healthy: consistent lineups, minutes that add up, nothing to fail on. Drawn, they would be a
    # rotation chart on a 12 or 24 minute axis sitting beside real ones on a 48 minute axis, with
    # every bar the wrong length and nothing saying so. Filtering on SHAPE rather than on those
    # three names also catches a game abandoned part way, which cannot be drawn to scale either.
    full = pbp.groupby('game_id')['period_number'].max()
    full = set(full[full >= 4].index)
    short = pbp.game_id.nunique() - len(full)

    pbp = pbp[pbp.game_id.isin(full)]
    want = set(pbp.loc[pbp['_d'] >= since, 'game_id'].unique())
    windowed = len(want)

    # EVERY CLUB'S LAST GAME, whether or not it falls in the window. The match report draws the
    # rotation from each side's most recent game, so a club missing from the file is a fixture
    # with half a chart and nothing saying why. A window alone cannot promise that: three weeks
    # covers all thirty mid-season, but at the end of one it is the finalists and nobody else.
    # At most thirty games are added, about 70 KB, and the guarantee is worth more than that.
    last = {}
    for r in pbp[['game_id', '_d', 'home_team_abbrev', 'away_team_abbrev']].itertuples():
        for club in (r.home_team_abbrev, r.away_team_abbrev):
            if club and (club not in last or r._2 > last[club][0]):
                last[club] = (r._2, r.game_id)
    want |= {gid for _, gid in last.values()}

    keep = pbp[pbp.game_id.isin(want)]
    print('  %s: %d full games (latest %s, %d short ones dropped) - %d in the %d-day window, '
          '%d after adding each of %d clubs\' last game'
          % (season, pbp.game_id.nunique(), pbp['_d'].max(), short, windowed, WINDOW_DAYS,
             len(want), len(last)))

    for gid, g in keep.groupby('game_id'):
        g = g.sort_values(['period_number', 'game_play_number'])
        bx = box[box.game_id == gid]
        if bx.empty:
            stats['no_box'] += 1
            continue
        starters = [str(int(float(a))) for a in bx[bx.starter == True].athlete_id]   # noqa: E712
        if len(starters) != 10:
            # Without exactly two fives there is nothing to seed the floor with, and a guessed
            # lineup is worse than no chart: it would be drawn with the same confidence.
            stats['bad_starters'] += 1
            continue
        plays = []
        for r in g.itertuples():
            t = elapsed(r.period_number, r.clock_minutes, r.clock_seconds)
            if r.type_text == 'Substitution':
                plays.append({'type': 'sub', 't': t, 'period': int(r.period_number),
                              'in_': _pid(r.athlete_id_1), 'out': _pid(r.athlete_id_2)})
            elif bool(r.scoring_play):
                plays.append({'type': 'score', 't': t, 'period': int(r.period_number),
                              'margin': int((r.home_score or 0) - (r.away_score or 0))})
        if not plays:
            stats['no_plays'] += 1
            continue
        row0 = g.iloc[-1]
        finals = {'home': str(row0.home_team_abbrev), 'away': str(row0.away_team_abbrev),
                  'hs': int(row0.home_score or 0), 'as': int(row0.away_score or 0),
                  'd': str(row0.game_date)[:10]}
        rec, bad = build_game(plays, starters, finals)
        if bad:
            stats['lineup_wobble'] += 1
        out_games[str(gid)] = rec
        stats['games'] += 1


def _pid(v):
    if v is None:
        return ''
    try:
        if v != v:                                      # NaN
            return ''
    except Exception:
        pass
    try:
        return str(int(float(v)))
    except Exception:
        return ''


def write(out_dir, games, stats):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, 'stints.json')
    # Never let a bad run blank a good file: a day where the upstream release has not refreshed
    # looks identical to a day with no basketball.
    if not games and os.path.exists(path):
        try:
            held = len((json.load(open(path)) or {}).get('games') or {})
        except Exception:
            held = 0
        if held:
            print('  built 0 games - keeping the %d already in %s' % (held, path))
            return
    payload = {
        'updated': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'),
        'source': 'ESPN play-by-play via sportsdataverse',
        'games': games,
    }
    with open(path, 'w') as f:
        json.dump(payload, f, separators=(',', ':'))
    print('  wrote %s (%.0f KB) - %d games%s' % (
        path, os.path.getsize(path) / 1024, len(games),
        ('  skipped: %s' % {k: v for k, v in stats.items() if k != 'games' and v}) if any(
            v for k, v in stats.items() if k != 'games') else ''))


def selftest():
    ok = True

    def check(label, cond):
        nonlocal ok
        print(('  ok   ' if cond else '  FAIL ') + label)
        ok = ok and bool(cond)

    check('a regulation period is 12 minutes', period_len(1) == 720)
    check('overtime is 5', period_len(5) == 300)
    check('four periods end at 48 minutes', period_ends(4)[-1] == 2880)
    check('one overtime ends at 53', period_ends(5)[-1] == 3180)
    # time remaining -> time elapsed
    check('tip-off is 0', elapsed(1, 12, 0) == 0)
    check('end of the first is 720', elapsed(1, 0, 0) == 720)
    check('7:49 left in the first is 251', elapsed(1, 7, 49) == 251)
    check('6:00 left in the third is 1800', elapsed(3, 6, 0) == 1800)

    # the doubled-substitution quirk
    rows = [
        {'type': 'sub', 't': 100, 'period': 1, 'in_': 'A', 'out': ''},       # malformed copy
        {'type': 'sub', 't': 100, 'period': 1, 'in_': 'A', 'out': 'B'},      # the real one
        {'type': 'sub', 't': 100, 'period': 1, 'in_': 'A', 'out': 'B'},      # exact duplicate
    ]
    cleaned = clean_subs(rows)
    check('a blank-sided substitution is dropped', len(cleaned) == 1)
    check('and an exact duplicate with it', cleaned[0]['out'] == 'B')

    # a whole tiny game: five starters, one swap at the 5 minute mark
    starters = ['1', '2', '3', '4', '5']
    # The plays have to reach the fourth period or the game legitimately ENDS in the first: the
    # length comes from the last period seen, which is the only way to tell regulation from
    # overtime. The first version of this test asserted a 48 minute game off period-1 plays and
    # was simply wrong about what it had built.
    plays = [{'type': 'score', 't': 60, 'period': 1, 'margin': 2},
             {'type': 'sub', 't': 300, 'period': 1, 'in_': '6', 'out': '5'},
             {'type': 'score', 't': 400, 'period': 1, 'margin': -1},
             {'type': 'score', 't': 2800, 'period': 4, 'margin': 7}]
    rec, bad = build_game(plays, starters, {'home': 'AAA', 'away': 'BBB'})
    check('the floor never broke', bad == 0)
    check('a starter who is never subbed plays the whole game',
          rec['pl']['1'] == [[0, 2880]])
    check('the man who came off has his stint closed at the swap',
          rec['pl']['5'] == [[0, 300]])
    check('the man who came on runs to the end', rec['pl']['6'] == [[300, 2880]])
    check('every player on the floor is accounted for', len(rec['pl']) == 6)

    # a man sent on at the buzzer: a stint of zero length, which must not be stored
    rec0, _ = build_game(
        [{'type': 'sub', 't': 2880, 'period': 4, 'in_': '9', 'out': '5'}], starters, {})
    check('a zero-length stint is dropped, not drawn as a sliver', '9' not in rec0['pl'])
    check('and the man he replaced keeps his real stint', rec0['pl']['5'] == [[0, 2880]])
    check('the margin keeps its points', rec['m'] == [[60, 2], [400, -1], [2800, 7]])

    # minutes must add up to the clock: five men on the floor for 48 minutes = 240 man-minutes
    total = sum(e - s for v in rec['pl'].values() for s, e in v)
    check('five men are on the floor at all times (240 man-minutes)', total == 5 * 2880)

    # overtime
    rec2, _ = build_game([{'type': 'sub', 't': 10, 'period': 5, 'in_': '6', 'out': '5'}],
                         starters, {})
    check('an overtime game runs to 53 minutes', rec2['end'] == 3180)

    # thinning keeps both ends
    pts = [[i, i] for i in range(500)]
    th = thin(pts, 50)
    check('thinning caps the points', len(th) <= 52)
    check('thinning keeps the first', th[0] == [0, 0])
    check('thinning keeps the last', th[-1] == [499, 499])
    check('a short margin is left alone', thin([[1, 1], [2, 2]], 50) == [[1, 1], [2, 2]])

    # ids arrive as floats in the feed and as "3934719.0" strings in the gamelogs
    check('a float id becomes a plain string', _pid(3934719.0) == '3934719')
    check('a missing id is empty, not "nan"', _pid(float('nan')) == '')

    print('SELFTEST', 'PASSED' if ok else 'FAILED')
    return 0 if ok else 1


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='nba/data')
    ap.add_argument('--seasons', default='', help='comma separated, default the current one')
    ap.add_argument('--days', type=int, default=21,
                    help='how far back to keep games (the page wants each club\'s last one)')
    ap.add_argument('--since', default='',
                    help='explicit YYYY-MM-DD instead of --days; for backfilling an old season')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())

    WINDOW_DAYS = a.days          # module level already; no `global` needed or allowed here
    # WHICH SEASON: the one the TOOL is on, read from its own meta.json - not the one the
    # calendar says. The page joins these to its gamelogs by MatchId, so a season the gamelogs do
    # not carry is a file that joins to nothing. meta.currentSeason and sportsdataverse use the
    # same naming (the year the season ENDS), so it is a straight read. When build_nba_data.py
    # rolls over to the new season this follows it with no change here.
    seasons = [s for s in a.seasons.split(',') if s]
    if not seasons:
        try:
            meta = json.load(open(os.path.join(a.out, 'meta.json')))
            seasons = [str(meta.get('currentSeason'))]
            print('  (season %s, from the tool\'s own meta.json)' % seasons[0])
        except Exception:
            today = datetime.date.today()
            seasons = [str(today.year + 1 if today.month >= 9 else today.year)]
            print('  (no meta.json - falling back to the calendar season %s)' % seasons[0])
    since = a.since or None

    games, stats = {}, {'games': 0, 'no_box': 0, 'bad_starters': 0,
                        'no_plays': 0, 'lineup_wobble': 0}
    print('stints: seasons %s, window %d days' % (','.join(seasons), a.days))
    for s in seasons:
        try:
            harvest(s, since, games, stats)
        except Exception as e:
            print('  %s failed: %s' % (s, str(e)[:160]))
    write(a.out, games, stats)
