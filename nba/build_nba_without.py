#!/usr/bin/env python3
"""What a player does with a team-mate ON the floor, and with him OFF it - from ESPN play-by-play.

    python nba/build_nba_without.py --out nba/data [--season 2027]
    python nba/build_nba_without.py --selftest          # no network

WHY
"Curry is out - who picks it up?" is the most-asked question on any slate with an injury report,
and the usual answer is a guess from the games he missed: three or four of them, against whoever
happened to be on the schedule. But a starter sits for a third of every game he plays. Those
minutes are the same question asked hundreds of times, with the same team-mates, in the same
games, against the same opponents - and nothing in a box score records them.

HOW
build_nba_stints.py already reconstructs who was on the floor, second by second, and checks it
against the box score (8,704 player-games, every one within a minute). This walks the same games
and files every shot, free throw, rebound, assist and turnover under two headings for each pair
of team-mates: he was on the floor with me, or he was not. A game the team-mate missed entirely
is all "not".

WHAT IT IS AND IS NOT
Counts and seconds, nothing fitted. The page turns them into per-36 rates and shows the minutes
beside every one, because the minutes ARE the finding as often as the rate is: forty minutes
without a team-mate is an anecdote, four hundred is a pattern. Nothing here is adjusted for who
else was on the floor or who the opponent was, and it says so on the page.

Measured once, across every pair in two seasons (see the note on the Rebounding card): the
average team-mate sitting moves a player's per-minute rates by almost nothing. The table is for
finding the pairs where it is NOT nothing, and the minutes column is how to tell.

WHAT IS WRITTEN
nba/data/without.json

    {"season":"2027","stats":["pts","reb","ast","fga","tpa","fta","tov","poss","oreb","dreb","tpm"],
     "teams":{"GS":{
        "names":{"3975":"Stephen Curry", ...},
        "minutes":{"3975":412.5, ...},                    # season minutes for this team
        "pairs":{"<player id>":{"<mate id>":[secWith, secWithout, [..with..], [..without..]]}}
     }}}

"poss" is the TEAM's shots + 0.44 free throws + turnovers while the player was on the floor in
that state, so the page can show his share of them (usage) as well as his raw rates.
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_nba_stints as ST          # noqa: E402  - the floor reconstruction, already validated

# Appended to, never reordered: the page finds each stat by NAME in the file's own "stats" list, so
# a file built before a stat was added still reads correctly and simply lacks the new column.
STATS = ['pts', 'reb', 'ast', 'fga', 'tpa', 'fta', 'tov', 'poss', 'oreb', 'dreb', 'tpm']
IDX = {k: i for i, k in enumerate(STATS)}
MIN_PLAYER_MIN = 60      # season minutes for this team before a player gets a row at all
MIN_MATE_MIN = 120       # ... and before a team-mate can be the one who is "without"
MAX_MATES = 10           # per team, by minutes: the players whose absence is worth asking about
MIN_SIDE_SEC = 12 * 60   # a pair is only written with 12 minutes on BOTH sides; the page asks more


def on_at(spans, t):
    """Was he on the floor at game second t? A substitution AT t counts for the man coming on."""
    for a, b in spans:
        if a <= t < b:
            return True
    return False


def overlap(a_spans, b_spans):
    tot = 0
    for a0, a1 in a_spans:
        for b0, b1 in b_spans:
            lo, hi = max(a0, b0), min(a1, b1)
            if hi > lo:
                tot += hi - lo
    return tot


def classify(type_text, text, scoring, score_value, a1, a2):
    """One play -> [(player id, stat, amount)], plus the possession weight for the acting team."""
    tt = (type_text or '')
    tx = (text or '')
    out, poss = [], 0.0
    if not a1:
        return out, poss
    low = tt.lower()
    if low.startswith('free throw'):
        out.append((a1, 'fta', 1))
        poss = 0.44
        if scoring:
            out.append((a1, 'pts', 1))
    elif 'rebound' in low:
        out.append((a1, 'reb', 1))
        # which end. The four cards read this file too, and a team-mate sitting moves a player's
        # DEFENSIVE boards and barely touches his offensive ones - a total would hide that.
        out.append((a1, 'oreb' if 'offensive' in low else 'dreb', 1))
    elif 'turnover' in low:
        out.append((a1, 'tov', 1))
        poss = 1.0
    elif ' makes ' in tx or ' misses ' in tx or 'blocks' in tx:
        # a field goal attempt. A blocked shot is written "X blocks Y's shot": the shooter is the
        # one named second, and the feed still puts him first in athlete_id_1
        out.append((a1, 'fga', 1))
        poss = 1.0
        if 'three point' in tx:
            out.append((a1, 'tpa', 1))
            if scoring:
                out.append((a1, 'tpm', 1))
        if scoring:
            out.append((a1, 'pts', int(score_value or 0)))
            if a2 and 'assist' in tx:
                out.append((a2, 'ast', 1))
    return out, poss


def tally_game(spans, team_of, events, roster, acc, secs):
    """Add one game to the running totals.

    spans   {pid: [[on, off], ...]}      who was on the floor
    team_of {pid: team}                  for everyone who played in this game
    events  [(t, team, [(pid, stat, n)], poss)]
    roster  {team: set(pid)}             the season roster - a mate who did not dress is "off"
    acc     {team: {pid: {mate: [with[], without[]]}}}
    secs    {team: {pid: {mate: [secWith, secWithout]}}}
    """
    played = {}
    for pid, tm in team_of.items():
        if pid in spans:
            played.setdefault(tm, []).append(pid)

    for tm, here in played.items():
        mates_all = roster.get(tm, set())
        for p in here:
            p_sec = sum(b - a for a, b in spans[p])
            for m in mates_all:
                if m == p:
                    continue
                w = overlap(spans[p], spans[m]) if m in spans and team_of.get(m) == tm else 0
                s = secs.setdefault(tm, {}).setdefault(p, {}).setdefault(m, [0, 0])
                s[0] += w
                s[1] += p_sec - w

    for t, tm, items, poss in events:
        here = played.get(tm) or []
        mates_all = roster.get(tm, set())
        floor = [p for p in here if on_at(spans[p], t)]
        floor_set = set(floor)
        # the team's possession, credited to everyone on the floor, split by each mate's state
        if poss:
            for p in floor:
                for m in mates_all:
                    if m == p:
                        continue
                    a = acc.setdefault(tm, {}).setdefault(p, {}).setdefault(
                        m, [[0.0] * len(STATS), [0.0] * len(STATS)])
                    a[0 if m in floor_set else 1][IDX['poss']] += poss
        for pid, stat, n in items:
            if team_of.get(pid) != tm:
                continue
            for m in mates_all:
                if m == pid:
                    continue
                a = acc.setdefault(tm, {}).setdefault(pid, {}).setdefault(
                    m, [[0.0] * len(STATS), [0.0] * len(STATS)])
                a[0 if m in floor_set else 1][IDX[stat]] += n


def harvest(season):
    pbp = ST._read_parquet(ST.PBP % season)
    box = ST._read_parquet(ST.BOX % season)
    return build(pbp, box)


def build(pbp, box):
    if 'did_not_play' in box.columns:
        box = box[box.did_not_play != True]            # noqa: E712
    if 'season_type' in pbp.columns:
        pbp = pbp[pbp.season_type == 2]                # regular season: no pre-season rotations
    full = pbp.groupby('game_id')['period_number'].max()
    full = set(full[full >= 4].index)
    pbp = pbp[pbp.game_id.isin(full)]

    names, roster, season_min = {}, {}, {}
    box = box[box.game_id.isin(set(pbp.game_id.unique()))]
    for r in box.itertuples():
        pid = ST._pid(r.athlete_id)
        tm = str(r.team_abbreviation)
        if not pid or not tm:
            continue
        try:
            mins = float(r.minutes or 0)
        except Exception:
            mins = 0.0
        if not mins > 0:          # also catches NaN, which is neither <= 0 nor > 0
            continue
        names[pid] = str(r.athlete_display_name)
        roster.setdefault(tm, set()).add(pid)
        season_min.setdefault(tm, {})[pid] = season_min.setdefault(tm, {}).get(pid, 0.0) + mins

    acc, secs = {}, {}
    stats = {'games': 0, 'bad_starters': 0, 'no_box': 0, 'wobble': 0}
    check = {'pts': [0, 0], 'reb': [0, 0], 'ast': [0, 0], 'fga': [0, 0]}

    for gid, g in pbp.groupby('game_id'):
        g = g.sort_values(['period_number', 'game_play_number'])
        bx = box[box.game_id == gid]
        if bx.empty:
            stats['no_box'] += 1
            continue
        starters = [ST._pid(a) for a in bx[bx.starter == True].athlete_id]   # noqa: E712
        if len(starters) != 10:
            stats['bad_starters'] += 1
            continue
        team_of = {ST._pid(r.athlete_id): str(r.team_abbreviation) for r in bx.itertuples()}
        abbr_of = {}
        row0 = g.iloc[-1]
        abbr_of[ST._pid(row0.home_team_id)] = str(row0.home_team_abbrev)
        abbr_of[ST._pid(row0.away_team_id)] = str(row0.away_team_abbrev)

        plays, events = [], []
        for r in g.itertuples():
            t = ST.elapsed(r.period_number, r.clock_minutes, r.clock_seconds)
            if r.type_text == 'Substitution':
                plays.append({'type': 'sub', 't': t, 'period': int(r.period_number),
                              'in_': ST._pid(r.athlete_id_1), 'out': ST._pid(r.athlete_id_2)})
                continue
            a1, a2 = ST._pid(r.athlete_id_1), ST._pid(r.athlete_id_2)
            items, poss = classify(r.type_text, r.text, bool(r.scoring_play), r.score_value, a1, a2)
            if not items:
                continue
            # the acting team is the ACTOR's, from the box score - the feed's team_id is the
            # team in possession, which for a defensive rebound is not always the rebounder's
            tm = team_of.get(a1) or abbr_of.get(ST._pid(r.team_id))
            if tm:
                events.append((t, tm, items, poss))
        if not plays:
            continue
        rec, bad = ST.build_game(plays, starters, {})
        if bad:
            # the floor is wrong somewhere in this game; minutes survive it (see the stints
            # builder) but WHO was on with whom does not, and that is all this file is
            stats['wobble'] += 1
            continue
        spans = rec['pl']
        tally_game(spans, team_of, events, roster, acc, secs)
        stats['games'] += 1

        # the receipt: what the play-by-play adds up to, against the box score it never saw
        for _, tm, items, _p in events:
            for pid, stat, n in items:
                if stat in check and team_of.get(pid) == tm:
                    check[stat][0] += n
        for r in bx.itertuples():
            check['pts'][1] += int(r.points or 0)
            check['reb'][1] += int(r.rebounds or 0)
            check['ast'][1] += int(r.assists or 0)
            check['fga'][1] += int(r.field_goals_attempted or 0)

    teams = {}
    for tm, players in secs.items():
        mins = season_min.get(tm, {})
        mates = sorted([p for p in mins if mins[p] >= MIN_MATE_MIN], key=lambda p: -mins[p])[:MAX_MATES]
        pairs = {}
        for p, by_mate in players.items():
            if mins.get(p, 0) < MIN_PLAYER_MIN:
                continue
            row = {}
            for m in mates:
                if m == p or m not in by_mate:
                    continue
                s = by_mate[m]
                if s[0] < MIN_SIDE_SEC or s[1] < MIN_SIDE_SEC:
                    continue
                a = (acc.get(tm, {}).get(p, {}).get(m)) or [[0.0] * len(STATS), [0.0] * len(STATS)]
                row[m] = [int(s[0]), int(s[1]),
                          [round(v, 2) for v in a[0]], [round(v, 2) for v in a[1]]]
            if row:
                pairs[p] = row
        if pairs:
            ids = set(pairs) | {m for r in pairs.values() for m in r}
            teams[tm] = {'names': {i: names.get(i, i) for i in sorted(ids)},
                         'minutes': {i: round(mins.get(i, 0), 1) for i in sorted(ids)},
                         'mates': mates, 'pairs': pairs}
    return teams, stats, check


def write(out_dir, season, teams, stats, check):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, 'without.json')
    if not teams and os.path.exists(path):
        print('  built 0 teams - keeping the file already at %s' % path)
        return
    payload = {
        'updated': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'),
        'source': 'ESPN play-by-play via sportsdataverse',
        'season': str(season), 'games': stats['games'], 'stats': STATS,
        'minSideSeconds': MIN_SIDE_SEC, 'teams': teams,
    }
    # allow_nan=False: a NaN is not JSON, the page would reject the whole file in a .catch and the
    # pane would silently never appear. Better to fail here and keep yesterday's file.
    body = json.dumps(payload, separators=(',', ':'), allow_nan=False)
    with open(path, 'w') as f:
        f.write(body)
    print('  wrote %s (%.0f KB) - %d games, %d teams, %d pairs  skipped: %s'
          % (path, os.path.getsize(path) / 1024, stats['games'], len(teams),
             sum(len(r) for t in teams.values() for r in t['pairs'].values()),
             {k: v for k, v in stats.items() if k != 'games' and v}))
    print('  play-by-play against the box score: ' + ', '.join(
        '%s %d/%d (%.1f%%)' % (k, v[0], v[1], 100.0 * v[0] / v[1] if v[1] else 0)
        for k, v in check.items()))


def selftest():
    fails = []

    def ok(name, cond, note=''):
        print(('  ok   ' if cond else '  FAIL ') + name + (('  ' + note) if note else ''))
        if not cond:
            fails.append(name)

    ok('on the floor at the start of a span, off it at the end',
       on_at([[0, 100]], 0) and on_at([[0, 100]], 99) and not on_at([[0, 100]], 100))
    ok('overlap counts shared seconds only', overlap([[0, 100], [200, 300]], [[50, 250]]) == 100)

    it, poss = classify('Jump Shot', 'A makes 24-foot three point jumper (B assists)', True, 3, 'a', 'b')
    ok('a made three is 3 points, a shot, a three attempt, a three made and an assist',
       sorted(it) == sorted([('a', 'fga', 1), ('a', 'tpa', 1), ('a', 'tpm', 1), ('a', 'pts', 3),
                             ('b', 'ast', 1)])
       and poss == 1.0)
    it, poss = classify('Free Throw - 1 of 2', 'A misses free throw 1 of 2', False, 0, 'a', '')
    ok('a missed free throw is an attempt and 0.44 of a possession', it == [('a', 'fta', 1)] and poss == 0.44)
    it, poss = classify('Defensive Rebound', 'A defensive rebound', False, 0, 'a', '')
    ok('a defensive rebound is a rebound at that end, and not a possession',
       it == [('a', 'reb', 1), ('a', 'dreb', 1)] and poss == 0)
    it, poss = classify('Offensive Rebound', 'A offensive rebound', False, 0, 'a', '')
    ok('an offensive rebound is filed at the other end', it == [('a', 'reb', 1), ('a', 'oreb', 1)])
    it, _ = classify('Jump Shot', 'A misses 25-foot three point jumper', False, 0, 'a', '')
    ok('a missed three is an attempt and not a make', ('a', 'tpa', 1) in it and ('a', 'tpm', 1) not in it)
    it, poss = classify('Defensive Rebound', 'Team rebound', False, 0, '', '')
    ok('a team rebound belongs to nobody', it == [] and poss == 0)

    # ONE GAME, BY HAND. P plays 0-200. M plays 0-100, so P has 100 seconds with him and 100
    # without. P scores 2 with M on (t=50) and 3 with M off (t=150). X never dresses.
    spans = {'p': [[0, 200]], 'm': [[0, 100]], 'q': [[100, 200]]}
    team_of = {'p': 'T', 'm': 'T', 'q': 'T'}
    roster = {'T': {'p', 'm', 'q', 'x'}}
    events = [(50, 'T', [('p', 'fga', 1), ('p', 'pts', 2)], 1.0),
              (150, 'T', [('p', 'fga', 1), ('p', 'tpa', 1), ('p', 'pts', 3)], 1.0),
              (160, 'T', [('q', 'fga', 1)], 1.0)]
    acc, secs = {}, {}
    tally_game(spans, team_of, events, roster, acc, secs)
    ok('seconds split by whether the mate was on', secs['T']['p']['m'] == [100, 100])
    ok('a mate who did not dress is all "without"', secs['T']['p']['x'] == [0, 200])
    w, wo = acc['T']['p']['m']
    ok('points land on the right side', w[IDX['pts']] == 2 and wo[IDX['pts']] == 3)
    ok('the three attempt is on the without side only', w[IDX['tpa']] == 0 and wo[IDX['tpa']] == 1)
    ok('team possessions are split the same way (1 with, 2 without)',
       w[IDX['poss']] == 1.0 and wo[IDX['poss']] == 2.0)
    ok('nothing is counted for a player against himself', 'p' not in acc['T']['p'])
    # the two sides must always add back up to the whole
    ok('with + without = everything he did', w[IDX['pts']] + wo[IDX['pts']] == 5
       and sum(secs['T']['p']['m']) == 200)

    print(('\nFAILED: ' + ', '.join(fails)) if fails else '\nall good')
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='nba/data')
    ap.add_argument('--season', default=None, help='ESPN season year, e.g. 2027 for 2026-27')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    season = a.season
    if not season:
        try:
            season = str(json.load(open(os.path.join(a.out, 'meta.json'))).get('currentSeason') or '')
        except Exception:
            season = ''
    if not season:
        print('no season given and none in meta.json')
        return 1
    teams, stats, check = harvest(season)
    write(a.out, season, teams, stats, check)
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
