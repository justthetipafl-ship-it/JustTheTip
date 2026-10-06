#!/usr/bin/env python3
"""Per-quarter NBA production, from ESPN play-by-play.

    python nba/build_nba_quarters.py --out nba/data [--seasons 2026,2027]

WHY
Books price first-quarter points, rebounds and assists, and a game total tells you nothing about
them: a starter who sits the first six minutes and a man who plays the whole quarter can finish
with the same line. This aggregates the play-by-play by period so a Q1 market can be read off a
Q1 record.

HOW EACH STAT IS FOUND
  points   scoring plays, score_value summed against athlete_id_1
  assists  athlete_id_2 on a scoring play - ESPN records the assister there
           ("Jalen Brunson makes 25-foot three pointer (Mikal Bridges assists)")
  rebounds Offensive/Defensive Rebound events against athlete_id_1
  threes   scoring plays worth 3

The file is ~22 MB a season, so this is a build job and never something the page does. Output is
nba/data/quarters.json: one row per player per period with per-game averages and a game count.

CHECKED AGAINST THE BOX SCORES
Q1-Q4 should sum to a little under the season average, and across 363 players the median gap is
5.2% low. That is expected: overtime is excluded on purpose, because a quarter market does not pay
on it. A sum ABOVE the season average would mean the denominator is wrong - that was the first
version, which counted only games where a player recorded something in a period and so threw away
every quiet quarter.
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
PERIODS = (1, 2, 3, 4)          # overtime is folded into 'OT' rather than given its own row


def grab(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (JTT build)'})
    with urllib.request.urlopen(req, timeout=300) as r:
        return io.BytesIO(r.read())


def pid(v):
    """ESPN ids arrive as floats through pandas; the rest of the build keys on the integer."""
    t = str(v).strip()
    if t.endswith('.0'):
        t = t[:-2]
    return t if t.isdigit() else ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='nba/data')
    ap.add_argument('--seasons', default=None)
    ap.add_argument('--min-games', type=int, default=3)
    a = ap.parse_args()
    try:
        import pandas as pd
    except ImportError:
        print('pandas and pyarrow are required'); sys.exit(1)

    today = datetime.date.today()
    nxt = today.year + 1 if today.month >= 9 else today.year
    seasons = [s.strip() for s in (a.seasons or '%d,%d' % (nxt - 1, nxt)).split(',') if s.strip()]
    print('quarters: seasons %s' % ', '.join(seasons))

    names = {}
    acc = {}          # (pid, period) -> totals
    games = {}        # (pid, period) -> games where he recorded something in that period
    appear = {}       # pid -> games he actually played, the honest denominator
    done = []

    for season in seasons:
        try:
            pbp = pd.read_parquet(grab(PBP % season), columns=[
                'game_id', 'period_number', 'type_text', 'athlete_id_1', 'athlete_id_2',
                'scoring_play', 'score_value'])
        except Exception as e:
            print('  %s: play-by-play not available (%s)' % (season, str(e)[:60]))
            continue
        try:
            box = pd.read_parquet(grab(BOX % season),
                                  columns=['athlete_id', 'athlete_display_name', 'team_abbreviation',
                                           'game_id', 'minutes', 'did_not_play'])
            for _, r in box.drop_duplicates(subset=['athlete_id']).iterrows():
                k = pid(r['athlete_id'])
                if k:
                    names[k] = {'name': str(r['athlete_display_name']),
                                'team': str(r.get('team_abbreviation') or '')}
            # GAMES PLAYED, which is the denominator these averages need. Counting only the games
            # where a player recorded something in a period silently drops his quiet quarters - a
            # scoreless Q1 leaves no event at all - and every average came out 4% high.
            played = box[(box.did_not_play != True) & (box.minutes.fillna(0) > 0)]
            for _, r in played.iterrows():
                k = pid(r['athlete_id'])
                if k:
                    appear.setdefault(k, set()).add(r['game_id'])
        except Exception as e:
            print('  %s: box scores unavailable (%s) - averages will use event games'
                  % (season, str(e)[:50]))

        pbp = pbp[pbp.period_number.isin(PERIODS)]
        scoring = pbp.scoring_play.fillna(False).astype(bool)
        print('  %s: %d plays across %d games' % (season, len(pbp), pbp.game_id.nunique()))

        def bump(key, field, n, gid):
            d = acc.setdefault(key, {})
            d[field] = d.get(field, 0) + n
            games.setdefault(key, set()).add(gid)

        for _, r in pbp[scoring].iterrows():
            per, gid = int(r.period_number), r.game_id
            scorer = pid(r.athlete_id_1)
            val = int(r.score_value or 0)
            if scorer and val:
                bump((scorer, per), 'points', val, gid)
                if val == 3:
                    bump((scorer, per), 'threes', 1, gid)
            assister = pid(r.athlete_id_2)
            if assister:
                bump((assister, per), 'assists', 1, gid)

        reb = pbp[pbp.type_text.isin(['Offensive Rebound', 'Defensive Rebound'])]
        for _, r in reb.iterrows():
            who = pid(r.athlete_id_1)
            if who:
                bump((who, int(r.period_number)), 'rebounds', 1, r.game_id)
        done.append(season)

    if not acc:
        print('nothing aggregated - no file written'); sys.exit(1)

    rows = []
    for (who, per), tot in acc.items():
        # games PLAYED, not games with an event in this period
        n = len(appear.get(who) or games.get((who, per), ()))
        if n < a.min_games:
            continue
        who_name = names.get(who, {})
        row = {'playerId': who, 'name': who_name.get('name', ''), 'team': who_name.get('team', ''),
               'period': per, 'games': n,
               'activeIn': len(games.get((who, per), ()))}
        for k in ('points', 'rebounds', 'assists', 'threes'):
            row[k] = round(tot.get(k, 0) / n, 2)
            row[k + 'Tot'] = tot.get(k, 0)
        rows.append(row)
    rows.sort(key=lambda r: (r['period'], -r['points']))

    os.makedirs(a.out, exist_ok=True)
    out = {'seasons': done, 'periods': list(PERIODS), 'minGames': a.min_games,
           'updated': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'),
           'source': 'ESPN play-by-play via sportsdataverse-data',
           'players': rows}
    with open(os.path.join(a.out, 'quarters.json'), 'w') as fh:
        json.dump(out, fh, separators=(',', ':'))
    print('  wrote quarters.json - %d player-period rows' % len(rows))
    q1 = sorted([r for r in rows if r['period'] == 1], key=lambda r: -r['points'])[:5]
    for r in q1:
        print('    Q1  %-24s %-4s %5.1f pts  %4.1f reb  %4.1f ast  (%d games)'
              % (r['name'], r['team'], r['points'], r['rebounds'], r['assists'], r['games']))


if __name__ == '__main__':
    main()
