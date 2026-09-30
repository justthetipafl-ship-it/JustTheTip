#!/usr/bin/env python3
"""Pull the NHL context the gamelogs cannot give: starting goalies, and special-teams rates.

    python nhl/fetch_nhl_extras.py --out nhl/data [--date 2026-10-01] [--dry-run]

WHY
The build already has every skater's game log, but three things a prop bettor needs are only
available live:

  * WHO IS IN GOAL. A backup starting instead of the number one moves a game by half a goal, and
    the tool currently has to guess from who started recently. The gamecenter landing carries the
    matchup's goalies once the team names them.
  * THE PENALTY KILL. Power Play Merchants derives what a team concedes from the gamelogs, which
    works but lags. club-stats gives the real rate.
  * HIGH-DANGER SHOTS (NHL Edge). Shot location split into high-danger, mid and long range is the
    closest thing the public API has to expected goals, and it is exactly what separates a shooter
    who will score from one who is padding attempts from the point.

CAUTION: these endpoints are undocumented and change without notice. Every fetch is wrapped, every
shape is checked, and a section that cannot be parsed is SKIPPED rather than written half-formed -
a missing file is obvious, a silently wrong one is not. Run with --dry-run first and read what it
found before letting it write.
"""
import argparse
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request

WEB = 'https://api-web.nhle.com/v1'
STATS = 'https://api.nhle.com/stats/rest/en'
PACE = 0.4
UA = {'User-Agent': 'Mozilla/5.0 (JTT data build)'}


def api(url, tries=2):
    for n in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode())
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError) as e:
            if n == tries - 1:
                print('  ! %s -> %s' % (url.replace(WEB, '').replace(STATS, ''), str(e)[:60]))
                return None
            time.sleep(1)
    return None


def dig(d, *path, default=None):
    """Walk a nested response without assuming any level exists."""
    cur = d
    for k in path:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        elif isinstance(cur, list) and isinstance(k, int) and len(cur) > k:
            cur = cur[k]
        else:
            return default
    return cur if cur is not None else default


def goalie_name(node):
    """Goalie names come back as {'default': 'Name'} or a plain string, depending on the field."""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        first = dig(node, 'firstName', 'default') or dig(node, 'firstName') or ''
        last = dig(node, 'lastName', 'default') or dig(node, 'lastName') or ''
        full = (str(first) + ' ' + str(last)).strip()
        return full or (dig(node, 'name', 'default') or dig(node, 'default') or None)
    return None


def roster_teams(out_dir):
    """playerId and name -> team, from the build's own players.json, to sanity-check a goalie."""
    by_name = {}
    try:
        with open(os.path.join(out_dir, 'players.json')) as fh:
            for p in json.load(fh) or []:
                nm = str(p.get('name') or '').strip().lower()
                if nm and p.get('team'):
                    by_name[nm] = p['team']
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return by_name


def starting_goalies(day, rosters=None):
    """Named starters for a day's games, from the gamecenter landing.

    The landing only carries goalies once a team has named them, so a game with nothing yet is
    left out entirely rather than filled with a guess.
    """
    sched = api('%s/schedule/%s' % (WEB, day))
    games = []
    for d in dig(sched, 'gameWeek', default=[]) or []:
        if str(dig(d, 'date')) == day:
            games = dig(d, 'games', default=[]) or []
            break
    if not games:
        games = dig(sched, 'games', default=[]) or []
    out, named, blank = [], 0, 0
    for g in games:
        gid = dig(g, 'id')
        if not gid:
            continue
        land = api('%s/gamecenter/%s/landing' % (WEB, gid)); time.sleep(PACE)
        # Take the teams from the LANDING, not the schedule row: a dry run showed "NYI @ TOR:
        # Sorokin v Bobrovsky", and Bobrovsky is Florida - the two responses were being lined up
        # against each other by position. The landing knows its own game.
        row = {
            'gameId': gid,
            'date': day,
            'home': dig(land, 'homeTeam', 'abbrev') or dig(g, 'homeTeam', 'abbrev'),
            'away': dig(land, 'awayTeam', 'abbrev') or dig(g, 'awayTeam', 'abbrev'),
            'homeGoalie': None, 'awayGoalie': None, 'confirmed': False,
        }
        # the field has moved between seasons, so try the shapes that have carried it
        for path, side in ((('matchup', 'goalieComparison', 'homeTeam'), 'homeGoalie'),
                           (('matchup', 'goalieComparison', 'awayTeam'), 'awayGoalie')):
            node = dig(land, *path)
            leaders = dig(node, 'leaders', default=[]) or []
            cand = leaders[0] if leaders else node
            nm = goalie_name(cand)
            if nm:
                row[side] = nm
        if not row['homeGoalie'] and not row['awayGoalie']:
            for side, key in (('homeGoalie', 'homeTeam'), ('awayGoalie', 'awayTeam')):
                nm = goalie_name(dig(land, 'summary', 'iceSurface', key, 'goalies', 0))
                if nm:
                    row[side] = nm
        # A goalie whose stored team differs from the club he is named for is usually a TRANSFER,
        # not an error: players.json takes a player's team from his last gamelog row, so a summer
        # move does not show up until he plays. The live API is the fresher source, so keep what it
        # says and flag the difference - it is a useful signal that the roster file is behind.
        if rosters:
            for side, team in (('homeGoalie', row['home']), ('awayGoalie', row['away'])):
                nm = row.get(side)
                if not nm:
                    continue
                on = rosters.get(str(nm).strip().lower())
                if on and team and str(on).upper() != str(team).upper():
                    print('  note: %s is named for %s, players.json still has him at %s '
                          '(moved, or the roster file is stale)' % (nm, team, on))
                    row.setdefault('moved', []).append(nm)
        row['confirmed'] = bool(row['homeGoalie'] and row['awayGoalie'])
        if row['confirmed']:
            named += 1
        else:
            blank += 1
        out.append(row)
    print('  goalies: %d game(s) with both named, %d still to be announced' % (named, blank))
    return out


def special_teams(season, name_to_abbrev=None):
    """Power-play and penalty-kill rates per team, which the gamelogs only approximate."""
    url = ('%s/team/summary?cayenneExp=seasonId=%s%%20and%%20gameTypeId=2&limit=-1'
           % (STATS, season))
    j = api(url)
    rows = dig(j, 'data', default=[]) or []
    out = {}
    for r in rows:
        # the summary report returns a full name; the tool joins on the three-letter code, so a
        # file keyed by name would never have matched anything
        ab = r.get('teamAbbrev')
        if not ab:
            full = r.get('teamFullName')
            ab = abbrev_for(full, name_to_abbrev)
            if not ab:
                print('  ! no abbreviation for "%s" - skipped' % full)
                continue
        out[ab] = {
            'pp': r.get('powerPlayPct'),
            'pk': r.get('penaltyKillPct'),
            'shotsFor': r.get('shotsForPerGame'),
            'shotsAgainst': r.get('shotsAgainstPerGame'),
            'goalsFor': r.get('goalsForPerGame'),
            'goalsAgainst': r.get('goalsAgainstPerGame'),
        }
    print('  special teams: %d team(s)' % len(out))
    return out


def high_danger(season, player_ids, cap=0):
    """NHL Edge shot location: high-danger, mid and long range shots per skater.

    This is the nearest public stand-in for expected goals - a shooter taking six from the slot is
    a different bet from one taking six from the point, and nothing in the box score says which.
    """
    out, done = {}, 0
    for pid in player_ids:
        if cap and done >= cap:
            break
        j = api('%s/edge/skater-shot-location-detail/%s/%s/2' % (WEB, pid, season)); time.sleep(PACE)
        if not j:
            continue
        hd = dig(j, 'shotLocation', 'highDanger') or dig(j, 'highDanger')
        md = dig(j, 'shotLocation', 'midRange') or dig(j, 'midRange')
        ld = dig(j, 'shotLocation', 'longRange') or dig(j, 'longRange')
        if hd is None and md is None:
            continue
        pick = lambda n: (dig(n, 'shots') if isinstance(n, dict) else n)
        out[str(pid)] = {'hd': pick(hd), 'mid': pick(md), 'long': pick(ld)}
        done += 1
    print('  high danger: %d skater(s)' % len(out))
    return out


def team_abbrevs(out_dir):
    """Team name -> three-letter code, from the build's own teams.json.

    teams.json holds the NICKNAME ("Ducks", "Golden Knights") while the stats API returns the full
    name ("Vegas Golden Knights"), so the match is on the end of the string, longest nickname first
    so "Kings" cannot steal "Los Angeles Kings" from a longer name that also ends in it.
    """
    pairs = []
    try:
        with open(os.path.join(out_dir, 'teams.json')) as fh:
            for t in json.load(fh) or []:
                ab, nick = t.get('team'), t.get('teamFull')
                if ab and nick:
                    pairs.append((str(nick).strip().lower(), ab))
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    pairs.sort(key=lambda kv: -len(kv[0]))
    return pairs


def abbrev_for(full, pairs):
    f = str(full or '').strip().lower()
    if not f:
        return None
    for nick, ab in pairs or []:
        if f == nick or f.endswith(' ' + nick):
            return ab
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='nhl/data')
    ap.add_argument('--date', default=None)
    ap.add_argument('--season', default=None)
    ap.add_argument('--edge-cap', type=int, default=0, help='how many skaters to pull Edge data for (0 = none)')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    day = a.date or datetime.date.today().isoformat()
    season = a.season
    if not season:
        y = datetime.date.today().year
        m = datetime.date.today().month
        season = '%d%d' % (y, y + 1) if m >= 9 else '%d%d' % (y - 1, y)

    print('NHL extras for %s (season %s)%s' % (day, season, ' [dry run]' if a.dry_run else ''))

    rosters = roster_teams(a.out)
    abbrev = team_abbrevs(a.out)
    goalies = starting_goalies(day, rosters)
    st = special_teams(season, abbrev)
    edge = {}
    if a.edge_cap:
        ids = []
        pf = os.path.join(a.out, 'players.json')
        try:
            with open(pf) as fh:
                for p in json.load(fh):
                    if p.get('playerId') and str(p.get('position', '')).upper() != 'G':
                        ids.append(p['playerId'])
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        edge = high_danger(season, ids, a.edge_cap)

    if a.dry_run:
        for g in goalies[:5]:
            print('   %s @ %s: %s v %s%s' % (g['away'], g['home'], g['awayGoalie'] or '-',
                                             g['homeGoalie'] or '-', '' if g['confirmed'] else '  (not confirmed)'))
        for t, v in list(st.items())[:5]:
            print('   %s  PP %s  PK %s' % (t, v['pp'], v['pk']))
        print('nothing written (dry run)')
        return

    # a section that came back empty is NOT written over a good file from an earlier run
    os.makedirs(a.out, exist_ok=True)
    if goalies:
        with open(os.path.join(a.out, 'goalies.json'), 'w') as fh:
            json.dump({'date': day, 'games': goalies}, fh, separators=(',', ':'))
        print('  wrote goalies.json')
    if st:
        with open(os.path.join(a.out, 'special_teams.json'), 'w') as fh:
            json.dump({'season': season, 'teams': st}, fh, separators=(',', ':'))
        print('  wrote special_teams.json')
    if edge:
        with open(os.path.join(a.out, 'edge_shots.json'), 'w') as fh:
            json.dump({'season': season, 'skaters': edge}, fh, separators=(',', ':'))
        print('  wrote edge_shots.json')


if __name__ == '__main__':
    main()
