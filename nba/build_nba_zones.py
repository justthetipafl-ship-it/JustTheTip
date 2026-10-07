#!/usr/bin/env python3
"""NBA shot zones, from ESPN play-by-play coordinates.

    python nba/build_nba_zones.py --out nba/data [--seasons 2026,2027]

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='nba/data')
    ap.add_argument('--seasons', default=None)
    ap.add_argument('--min-shots', type=int, default=25)
    a = ap.parse_args()
    try:
        import pandas as pd
        import numpy as np
    except ImportError:
        print('pandas, numpy and pyarrow are required'); sys.exit(1)

    today = datetime.date.today()
    nxt = today.year + 1 if today.month >= 9 else today.year
    seasons = [s.strip() for s in (a.seasons or '%d,%d' % (nxt - 1, nxt)).split(',') if s.strip()]
    print('zones: seasons %s' % ', '.join(seasons))

    ZONES = ['rim', 'paint', 'midRange', 'cornerThree', 'armsThree']
    names, acc, lg, done = {}, {}, {}, []
    # what each DEFENCE concedes by zone. Player zones say where he shoots; this says who lets him.
    # The shooting team is on the play and both clubs are on the row, so the defence is the other one.
    dfn = {}

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
            if dteam and dteam != 'nan':
                e2 = dfn.setdefault(dteam, {}).setdefault(z0, {'att': 0, 'made': 0})
                e2['att'] += 1
                if hit:
                    e2['made'] += 1
            k = pid(who)
            if not k:
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
    for z in ZONES:
        vals = {t: (v[z]['made'] / v[z]['att']) for t, v in dfn.items() if v.get(z, {}).get('att', 0) >= 50}
        order = sorted(vals.items(), key=lambda kv: kv[1])
        for i, (t, pct) in enumerate(order):
            e3 = defence.setdefault(t, {'team': t})
            e3[z] = {'pct': round(pct, 3), 'rank': i + 1, 'of': len(order),
                     'att': dfn[t][z]['att']}

    out = {'seasons': done, 'zones': ZONES, 'minShots': a.min_shots, 'defence': defence,
           'updated': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'),
           'source': 'ESPN play-by-play via sportsdataverse-data',
           'league': league, 'leagueShots': total_att, 'players': rows}
    with open(os.path.join(a.out, 'zones.json'), 'w') as fh:
        json.dump(out, fh, separators=(',', ':'))
    print('  wrote zones.json - %d players, %d defences' % (len(rows), len(defence)))
    print('  league: ' + '  '.join('%s %d%% (%d%% of shots)'
          % (z, round((league[z]['pct'] or 0) * 100), round(league[z]['att'] / total_att * 100))
          for z in ZONES if z in league))


if __name__ == '__main__':
    main()
