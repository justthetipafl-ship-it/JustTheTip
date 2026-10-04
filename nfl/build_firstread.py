#!/usr/bin/env python3
"""First-read share per receiver, from FTN charting joined to play-by-play.

    python nfl/build_firstread.py [--season 2026] [--out nfl/data]

WHY THIS STAT
Targets tell you how often the ball arrived; they do not tell you whether the quarterback was
looking for him. FTN charts which read each throw went to, so a receiver who is the FIRST read on
a third of his team's dropbacks has a floor that target count alone hides - and one living on
checkdowns and scramble drill has a ceiling that target count flatters.

HOW
FTN charting carries no receiver, only a play id, so it is joined to nflverse play-by-play on
(game_id, play_id) to attach the target. Both come from nflverse GitHub releases, which download
fine from Actions - unlike stats.nba.com and friends, nothing here blocks datacentre IPs.

ATTRIBUTION: FTN charting data is CC-BY-SA 4.0 and must be credited to FTN Data via nflverse
wherever it is shown. The tool prints that line under the First Read section.
"""
import argparse
import datetime
import io
import json
import os
import sys
import urllib.request

REL = 'https://github.com/nflverse/nflverse-data/releases/download'
FTN = REL + '/ftn_charting/ftn_charting_%s.parquet'
PBP = REL + '/pbp/play_by_play_%s.parquet'
ROS = REL + '/rosters/roster_%s.parquet'
# read_thrown codes. '1' is the first read; the rest are what a quarterback does when it is not
# there. They are kept apart rather than lumped as "not first" because a checkdown merchant and a
# second-read possession receiver are different players.
FIRST, SECOND = '1', '2'
BAILOUT = {'CHK', 'SD'}          # checkdown, scramble drill
DESIGNED = 'DES'


def grab(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (JTT build)'})
    with urllib.request.urlopen(req, timeout=120) as r:
        return io.BytesIO(r.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', default=str(datetime.date.today().year if datetime.date.today().month >= 3
                                            else datetime.date.today().year - 1))
    ap.add_argument('--out', default='nfl/data')
    ap.add_argument('--min-targets', type=int, default=5)
    a = ap.parse_args()

    try:
        import pandas as pd
    except ImportError:
        print('pandas and pyarrow are required'); sys.exit(1)

    season = a.season
    print('first read: season %s' % season)
    try:
        ftn = pd.read_parquet(grab(FTN % season),
                              columns=['nflverse_game_id', 'nflverse_play_id', 'week', 'read_thrown'])
        pbp = pd.read_parquet(grab(PBP % season),
                              columns=['game_id', 'play_id', 'week', 'posteam',
                                       'receiver_player_name', 'receiver_player_id',
                                       'pass_attempt', 'complete_pass', 'yards_gained', 'air_yards'])
    except Exception as e:
        print('  could not load nflverse data: %s' % str(e)[:120]); sys.exit(1)

    # Play-by-play abbreviates the receiver ("J.Smith-Njigba"), which matches nothing in the
    # gamelogs. The roster carries the GSIS id against the full name, so names come out in the
    # form the rest of the tool uses.
    full = {}
    try:
        ros = pd.read_parquet(grab(ROS % season), columns=['gsis_id', 'full_name'])
        full = {str(a): str(b) for a, b in zip(ros.gsis_id, ros.full_name) if a and b}
        print('  roster: %d names' % len(full))
    except Exception as e:
        print('  roster unavailable (%s) - names stay abbreviated' % str(e)[:60])

    m = pbp.merge(ftn, left_on=['game_id', 'play_id'],
                  right_on=['nflverse_game_id', 'nflverse_play_id'], how='inner')
    tg = m[(m.pass_attempt == 1) & (m.receiver_player_name.notna())].copy()
    if not len(tg):
        print('  no charted targets - nothing written'); sys.exit(1)
    print('  %d charted targets across %d games' % (len(tg), tg.game_id.nunique()))

    # a team's charted dropbacks, so a share can mean "of what his offence threw"
    team_db = m[m.pass_attempt == 1].groupby('posteam').size().to_dict()

    rows = []
    for (name, team), g in tg.groupby(['receiver_player_name', 'posteam']):
        n = len(g)
        if n < a.min_targets:
            continue
        first = int((g.read_thrown == FIRST).sum())
        second = int((g.read_thrown == SECOND).sum())
        bail = int(g.read_thrown.isin(BAILOUT).sum())
        des = int((g.read_thrown == DESIGNED).sum())
        pid = str(g.receiver_player_id.dropna().iloc[0]) if g.receiver_player_id.notna().any() else None
        rows.append({
            'name': full.get(pid) or str(name), 'short': str(name), 'team': str(team),
            'pid': pid,
            'targets': n,
            'first': first,
            'firstShare': round(first / n, 3),                       # of HIS targets
            'firstOfTeam': round(first / team_db[team], 3) if team_db.get(team) else None,
            'second': second, 'checkdown': bail, 'designed': des,
            'catch': round(float((g.complete_pass == 1).mean()), 3),
            'ypt': round(float(g.yards_gained.fillna(0).mean()), 2),
            'games': int(g.game_id.nunique()),
        })
    rows.sort(key=lambda r: -r['first'])
    out = {'season': season, 'updated': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'),
           'source': 'FTN Data via nflverse (CC-BY-SA 4.0)', 'players': rows}
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, 'firstread.json'), 'w') as fh:
        json.dump(out, fh, separators=(',', ':'))
    print('  wrote %s/firstread.json - %d receivers' % (a.out, len(rows)))
    for r in rows[:5]:
        print('    %-22s %-4s %3d first reads of %3d targets (%.0f%%)'
              % (r['name'], r['team'], r['first'], r['targets'], r['firstShare'] * 100))


if __name__ == '__main__':
    main()
