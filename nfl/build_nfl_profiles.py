#!/usr/bin/env python3
"""NFL player profiles: how a receiver is used, how a back is run, how a passer throws.

    python nfl/build_nfl_profiles.py --out nfl/data [--season 2026]

WHAT THIS IS
The cheat-sheet view of a player - not his counting stats, but the shape of his usage and the
matchup he walks into. Built from nflverse play-by-play, FTN charting and Next Gen Stats, all free.

WHAT IT DELIBERATELY DOES NOT CONTAIN
Route participation and coverage shells (1-High / 2-High splits), so no Route %, TPRR or YPRR.
Routes run requires deciding, on every pass snap, whether each eligible man ran a route or stayed
in to block - a charting judgement nobody publishes free; PFF and Fantasy Points sell it. Coverage
shell is the same. Everything below is a real measurement rather than a guess at those.

RECEIVING   adot, deep target rate, target share, air-yards share, catch rate, screen share,
            play-action share, yards per target
RUSHING     carries, yards per carry, stacked-box rate faced, share of team carries, goal-line work
PASSING     adot, deep rate, play-action rate, blitz rate faced, pressure-ish (out of pocket)
TEAM        pass rate over expected, and the same allowed by each defence, with league ranks
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
FTN = REL + '/ftn_charting/ftn_charting_%s.parquet'
ROS = REL + '/rosters/roster_%s.parquet'
DEEP = 20.0          # air yards at or beyond this is a deep target, the usual definition
STACKED = 8          # defenders in the box at or above this is a stacked front


def grab(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (JTT build)'})
    with urllib.request.urlopen(req, timeout=240) as r:
        return io.BytesIO(r.read())


def rank_map(d, higher_is_more=True):
    """1 = least, N = most, matching how the opponent ranks read on a cheat sheet."""
    items = sorted(d.items(), key=lambda kv: kv[1], reverse=not higher_is_more)
    return {k: i + 1 for i, (k, v) in enumerate(items)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='nfl/data')
    ap.add_argument('--season', default=None)
    ap.add_argument('--min-targets', type=int, default=8)
    ap.add_argument('--min-carries', type=int, default=15)
    a = ap.parse_args()
    try:
        import pandas as pd
        import numpy as np
    except ImportError:
        print('pandas, numpy and pyarrow are required'); sys.exit(1)

    today = datetime.date.today()
    season = a.season or str(today.year if today.month >= 3 else today.year - 1)
    print('profiles: season %s' % season)

    try:
        pbp = pd.read_parquet(grab(PBP % season))
    except Exception as e:
        print('  play-by-play unavailable (%s)' % str(e)[:70]); sys.exit(1)
    try:
        ftn = pd.read_parquet(grab(FTN % season), columns=[
            'nflverse_game_id', 'nflverse_play_id', 'n_defense_box', 'n_blitzers',
            'is_play_action', 'is_screen_pass', 'is_motion', 'is_qb_out_of_pocket'])
        pbp = pbp.merge(ftn, left_on=['game_id', 'play_id'],
                        right_on=['nflverse_game_id', 'nflverse_play_id'], how='left')
        print('  charting joined on %d plays' % int(pbp.n_defense_box.notna().sum()))
    except Exception as e:
        print('  charting unavailable (%s) - box and play-action left out' % str(e)[:60])
        for c in ['n_defense_box', 'n_blitzers', 'is_play_action', 'is_screen_pass',
                  'is_motion', 'is_qb_out_of_pocket']:
            pbp[c] = None

    # Play-by-play abbreviates a name to "J.Smith-Njigba", which joins to nothing in the gamelogs.
    # The roster carries the id against the full name.
    full = {}
    try:
        ros = pd.read_parquet(grab(ROS % season), columns=['gsis_id', 'full_name'])
        full = {str(x): str(y) for x, y in zip(ros.gsis_id, ros.full_name) if x and y}
        print('  roster: %d names' % len(full))
    except Exception as e:
        print('  roster unavailable (%s) - names stay abbreviated' % str(e)[:50])

    def nm(idcol, g, fallback):
        ids = g[idcol].dropna() if idcol in g else []
        for i in ids:
            if str(i) in full:
                return full[str(i)]
        return str(fallback)

    passes = pbp[pbp.pass_attempt == 1].copy()
    runs = pbp[pbp.rush_attempt == 1].copy()
    print('  %d pass attempts, %d rushes' % (len(passes), len(runs)))

    # --- team pass rate over expected, and what each defence allows ------------------------------
    # pass_oe is already "how much more often than expected this team threw", play by play. The
    # average over a team's plays is its PROE; the average over plays faced is what it allows.
    off = pbp[pbp.pass_oe.notna()]
    team_proe = off.groupby('posteam').pass_oe.mean().round(2).to_dict()
    def_proe = off.groupby('defteam').pass_oe.mean().round(2).to_dict()
    # deep rate allowed: share of passes faced thrown 20+ yards downfield
    dp = passes[passes.air_yards.notna()]
    deep_allowed = (dp.assign(d=(dp.air_yards >= DEEP).astype(float))
                      .groupby('defteam').d.mean().round(4).to_dict())
    proe_rank = rank_map(team_proe)
    dproe_rank = rank_map(def_proe)
    deep_rank = rank_map(deep_allowed)

    # team totals, for shares
    team_targets = passes.groupby('posteam').size().to_dict()
    team_air = dp.groupby('posteam').air_yards.sum().to_dict()
    team_carries = runs.groupby('posteam').size().to_dict()

    rec, rush, pas = {}, {}, {}

    tg = passes[passes.receiver_player_name.notna()]
    for (name, team), g in tg.groupby(['receiver_player_name', 'posteam']):
        n = len(g)
        if n < a.min_targets:
            continue
        ay = g.air_yards.dropna()
        rec[name] = {
            'name': nm('receiver_player_id', g, name), 'short': str(name), 'team': str(team), 'targets': n,
            'adot': round(float(ay.mean()), 2) if len(ay) else None,
            'deepRate': round(float((ay >= DEEP).mean()), 3) if len(ay) else None,
            'targetShare': round(n / team_targets.get(team, n), 3),
            'airShare': round(float(ay.sum()) / team_air[team], 3) if team_air.get(team) else None,
            'catchRate': round(float((g.complete_pass == 1).mean()), 3),
            'ypt': round(float(g.yards_gained.fillna(0).mean()), 2),
            'screenShare': round(float(g.is_screen_pass.fillna(False).astype(bool).mean()), 3),
            'paShare': round(float(g.is_play_action.fillna(False).astype(bool).mean()), 3),
            'games': int(g.game_id.nunique()),
        }

    rn = runs[runs.rusher_player_name.notna()]
    for (name, team), g in rn.groupby(['rusher_player_name', 'posteam']):
        n = len(g)
        if n < a.min_carries:
            continue
        box = g.n_defense_box.dropna()
        rush[name] = {
            'name': nm('rusher_player_id', g, name), 'short': str(name), 'team': str(team), 'carries': n,
            'ypc': round(float(g.yards_gained.fillna(0).mean()), 2),
            'carryShare': round(n / team_carries.get(team, n), 3),
            'stackedRate': round(float((box >= STACKED).mean()), 3) if len(box) else None,
            'avgBox': round(float(box.mean()), 2) if len(box) else None,
            'insideFive': int(((g.yardline_100 <= 5)).sum()),
            'games': int(g.game_id.nunique()),
        }

    qb = passes[passes.passer_player_name.notna()]
    for (name, team), g in qb.groupby(['passer_player_name', 'posteam']):
        n = len(g)
        if n < 30:
            continue
        ay = g.air_yards.dropna()
        blz = g.n_blitzers.dropna()
        pas[name] = {
            'name': nm('passer_player_id', g, name), 'short': str(name), 'team': str(team), 'attempts': n,
            'adot': round(float(ay.mean()), 2) if len(ay) else None,
            'deepRate': round(float((ay >= DEEP).mean()), 3) if len(ay) else None,
            'paRate': round(float(g.is_play_action.fillna(False).astype(bool).mean()), 3),
            'outOfPocket': round(float(g.is_qb_out_of_pocket.fillna(False).astype(bool).mean()), 3),
            'blitzRate': round(float((blz >= 5).mean()), 3) if len(blz) else None,
            'games': int(g.game_id.nunique()),
        }

    teams = {}
    for t in set(list(team_proe) + list(def_proe)):
        teams[t] = {
            'team': t,
            'proe': team_proe.get(t), 'proeRank': proe_rank.get(t),
            'proeAllowed': def_proe.get(t), 'proeAllowedRank': dproe_rank.get(t),
            'deepAllowed': deep_allowed.get(t), 'deepAllowedRank': deep_rank.get(t),
        }

    os.makedirs(a.out, exist_ok=True)
    out = {'season': season,
           'updated': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'),
           'source': 'nflverse play-by-play + FTN charting (FTN data CC-BY-SA 4.0)',
           'notes': 'no routes run or coverage shells - neither is published free',
           'receiving': list(rec.values()), 'rushing': list(rush.values()),
           'passing': list(pas.values()), 'teams': teams}
    with open(os.path.join(a.out, 'profiles.json'), 'w') as fh:
        json.dump(out, fh, separators=(',', ':'))
    print('  wrote profiles.json - %d receivers, %d backs, %d passers, %d teams'
          % (len(rec), len(rush), len(pas), len(teams)))
    top = sorted(rec.values(), key=lambda r: -(r['targetShare'] or 0))[:3]
    for r in top:
        print('    %-22s %-4s %2d%% target share, ADOT %4.1f, %2d%% deep'
              % (r['name'], r['team'], round(r['targetShare'] * 100),
                 r['adot'] or 0, round((r['deepRate'] or 0) * 100)))


if __name__ == '__main__':
    main()
