#!/usr/bin/env python3
"""ESPN injury report -> <sport>/data/injury.json, in the same shape NFL's already uses:
     [{Team, Player, Position, Injury, Status}]

   usage:  python3 scripts/fetch_injuries.py NHL nhl
           python3 scripts/fetch_injuries.py MLB mlb

   Team is written as OUR club code, matched by nickname against <dir>/data/teams.json
   ("Anaheim Ducks" -> ANA, "Athletics" -> ATH). The lab ignores an injury whose club code doesn't
   match the player's - silently - so ESPN's own abbreviations (LA, NJ, TB...) must never leak in.

   Status is ESPN's word (Out / Day-To-Day / Injured Reserve / Suspension). The lab reads Out,
   Injured Reserve and Suspension as OUT (excluded from every signal) and Day-To-Day as doubtful.

   A failed fetch writes nothing: the last good injury.json stays, rather than an empty report
   that would clear everyone as fit.                                                              """
import json, os, sys, unicodedata
import urllib.request

ENDPOINT = {
    'NHL': 'https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries',
    'MLB': 'https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/injuries',
}

def fold(s):
    s = unicodedata.normalize('NFD', str(s or ''))
    return ''.join(c for c in s if unicodedata.category(c) != 'Mn').lower()

def our_teams(dir_):
    try:
        rows = json.load(open(os.path.join(dir_, 'data', 'teams.json')))
    except Exception as e:
        sys.exit(f'[injuries] cannot read {dir_}/data/teams.json: {e}')
    if isinstance(rows, dict):
        rows = list(rows.values())
    out = []
    for t in rows:
        code = t.get('team')
        nick = t.get('teamFull') or t.get('name') or ''
        if code:
            out.append((code, fold(nick).strip()))
    return out

def match_team(espn_name, espn_abbr, teams):
    n = fold(espn_name).strip()
    for code, nick in teams:                      # nickname is the reliable key
        if nick and (n == nick or n.endswith(' ' + nick)):
            return code
    for code, _ in teams:                         # then an exact code match
        if espn_abbr and code.upper() == str(espn_abbr).upper():
            return code
    return None

def parse(payload, teams):
    rows, unmatched = [], set()
    for tm in (payload.get('injuries') or []):
        tname = tm.get('displayName') or (tm.get('team') or {}).get('displayName') or ''
        tabbr = tm.get('abbreviation') or (tm.get('team') or {}).get('abbreviation')
        for it in (tm.get('injuries') or []):
            ath = it.get('athlete') or {}
            name = ath.get('displayName') or ath.get('fullName')
            if not name:
                continue
            a_team = ath.get('team') or {}
            code = match_team(tname or a_team.get('displayName', ''), tabbr or a_team.get('abbreviation'), teams)
            if not code:
                unmatched.add(tname or a_team.get('displayName', '?'))
                continue
            det = it.get('details') or {}
            injury = det.get('type') or (it.get('type') or {}).get('description') or it.get('shortComment') or ''
            pos = (ath.get('position') or {}).get('abbreviation') or ''
            rows.append({'Team': code, 'Player': name, 'Position': pos, 'Injury': injury,
                         'Status': it.get('status') or (it.get('type') or {}).get('name') or ''})
    return rows, unmatched

def main():
    if len(sys.argv) < 3 or sys.argv[1].upper() not in ENDPOINT:
        sys.exit('usage: fetch_injuries.py NHL|MLB <sport dir>')
    sport, dir_ = sys.argv[1].upper(), sys.argv[2]
    teams = our_teams(dir_)
    try:
        req = urllib.request.Request(ENDPOINT[sport], headers={'User-Agent': 'Mozilla/5.0 JTT'})
        payload = json.load(urllib.request.urlopen(req, timeout=30))
    except Exception as e:
        print(f'[injuries] {sport} fetch failed ({e}) - keeping the previous injury.json')
        return
    rows, unmatched = parse(payload, teams)
    if unmatched:
        print(f'[injuries] WARNING {len(unmatched)} ESPN team(s) not matched to teams.json: {sorted(unmatched)}')
    if not rows and (payload.get('injuries') or []):
        print('[injuries] parsed 0 rows from a non-empty report - ESPN may have changed shape; keeping the previous file')
        return
    path = os.path.join(dir_, 'data', 'injury.json')
    json.dump(rows, open(path, 'w'), separators=(',', ':'))
    by = {}
    for r in rows:
        by[r['Status']] = by.get(r['Status'], 0) + 1
    print(f'[injuries] {sport}: {len(rows)} rows across {len({r["Team"] for r in rows})} clubs -> {path} | {by}')

if __name__ == '__main__':
    if '--selftest' in sys.argv:
        teams = [('ANA', 'ducks'), ('LAK', 'kings'), ('ATH', 'athletics'), ('UTA', 'mammoth')]
        sample = {'injuries': [
            {'displayName': 'Anaheim Ducks', 'injuries': [{'status': 'Out', 'athlete': {'displayName': 'Trevor Zegras',
              'position': {'abbreviation': 'C'}}, 'details': {'type': 'Knee'}}]},
            {'displayName': 'Los Angeles Kings', 'abbreviation': 'LA', 'injuries': [{'status': 'Day-To-Day',
              'athlete': {'displayName': 'Adrian Kempe', 'position': {'abbreviation': 'RW'}}, 'details': {'type': 'Upper Body'}}]},
            {'displayName': 'Athletics', 'injuries': [{'status': 'Injured Reserve', 'athlete': {'displayName': 'Luis Severino'}}]},
            {'displayName': 'Utah Mammoth', 'injuries': [{'status': 'Suspension', 'athlete': {'displayName': 'X Player'}}]},
            {'displayName': 'Nowhere Nobodies', 'injuries': [{'status': 'Out', 'athlete': {'displayName': 'Ghost'}}]}]}
        rows, un = parse(sample, teams)
        codes = [(r['Player'], r['Team'], r['Status']) for r in rows]
        assert codes == [('Trevor Zegras', 'ANA', 'Out'), ('Adrian Kempe', 'LAK', 'Day-To-Day'),
                         ('Luis Severino', 'ATH', 'Injured Reserve'), ('X Player', 'UTA', 'Suspension')], codes
        assert un == {'Nowhere Nobodies'}, un
        print('selftest ok:', codes, '| unmatched reported:', un)
    else:
        main()
