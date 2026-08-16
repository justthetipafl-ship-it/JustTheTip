#!/usr/bin/env python3
"""Fetch player-prop odds from RapidOddsAPI and write a JTT-shaped odds.json.

Usage: fetch_odds.py <SPORT_ID> <output_path> [--force] [--window N]
  SPORT_ID: WNBA | MLB   (extend SPORTS below for more)
  --force     pull now regardless of timing (the pre-ladder run uses this)
  --window N  pull once when within N hours before the first game (default 3)
Reads the API key from the ROA_API_KEY environment variable.

Output matches the AFL odds.json shape the shell already parses:
  {updated, source, books, lines:[{player,market,line,over,under,book}],
   alt:[...X+ milestone ladders...], matchOdds:{...}}

Credits per run = len(market_types) * ceil(len(BOOKMAKERS)/5) + nothing on empty slates.
"""
import json
import math
import os
import sys
import datetime

from rapidoddsapi import RapidOddsAPI
from rapidoddsapi.helpers import group_games

def _load(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return None


def _parse_dt(v):
    if not v:
        return None
    try:
        return datetime.datetime.fromisoformat(str(v).replace('Z', '').replace('+00:00', ''))
    except Exception:
        return None


def _first_game(fixture, now):
    times = []
    for g in (fixture or []):
        for k in ('gameTimeUTC', 'utc', 'commence_time', 'commence', 'start'):
            dt = _parse_dt(g.get(k))
            if dt:
                times.append(dt)
                break
    up = [t for t in times if t > now]
    return min(up) if up else None


def should_pull(out_path, force, window_hours):
    """Pull if forced (pre-ladder run), else pull once when the pre-game window opens."""
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    if force:
        return True, 'forced (pre-ladder)'
    base = os.path.dirname(out_path) or '.'
    fg = _first_game(_load(os.path.join(base, 'fixture.json')) or [], now)
    if fg is None:
        return False, 'no upcoming games'
    window_start = fg - datetime.timedelta(hours=window_hours)
    if now < window_start:
        return False, 'before window (first game %s UTC)' % fg.strftime('%Y-%m-%d %H:%M')
    prev = _load(out_path)
    upd = _parse_dt(prev.get('updated')) if isinstance(prev, dict) else None
    if upd and upd >= window_start:
        return False, 'already pulled this window'
    return True, 'window open (first game %s UTC)' % fg.strftime('%Y-%m-%d %H:%M')


BOOKMAKERS = ['Sportsbet', 'TAB', 'Pointsbet', 'Ladbrokes', 'Unibet', 'BetRight', 'Dabble']

# per-sport: rapidoddsapi main market key -> JTT market key (config.js uses the JTT keys).
# milestone (X+) ladders are the same base key + '_milestones'; they feed the `alt` array.
SPORTS = {
    'WNBA': {
        'player_points': 'points', 'player_rebounds': 'rebounds', 'player_assists': 'assists',
        'player_made_threes': 'threes', 'player_points_rebounds_assists': 'pra',
        'player_steals': 'steals', 'player_blocks': 'blocks',
    },
    'MLB': {
        'batter_hits': 'H', 'batter_total_bases': 'TB', 'batter_home_runs': 'HR',
        'batter_rbis': 'RBI', 'batter_runs': 'R', 'batter_walks': 'BB',
        'pitcher_strikeouts': 'K',
    },
}


def jtt_market(key, mkmap):
    base = key[:-len('_milestones')] if key.endswith('_milestones') else key
    return mkmap.get(base), key.endswith('_milestones')


def transform(resp, mkmap, sport):
    games = group_games(resp)
    lines_map, alt_map, books, match_odds = {}, {}, set(), []
    for g in games:
        game = g.get('game', {})
        home, away = game.get('home_team', ''), game.get('away_team', '')
        mo = {'home': home, 'away': away, 'commence': game.get('commence_time'), 'books': {}}
        for bk in g.get('bookmakers', []):
            book = bk.get('name', '')
            books.add(book)
            for mkt in bk.get('markets', []):
                key = mkt.get('key', '')
                if key == 'head_to_head':
                    for o in mkt.get('outcomes', []):
                        if o.get('name') and o.get('price'):
                            mo['books'].setdefault(book, {})[o['name']] = o['price']
                    continue
                jm, is_alt = jtt_market(key, mkmap)
                if not jm:
                    continue
                for o in mkt.get('outcomes', []):
                    player, point, price = o.get('player_name'), o.get('point'), o.get('price')
                    name = (o.get('name') or '').lower()
                    if player is None or point is None or not price:
                        continue
                    line = float(point)
                    if is_alt and line == int(line):     # "5+" milestone -> over 4.5
                        line -= 0.5
                    target = alt_map if is_alt else lines_map
                    rec = target.setdefault((player, jm, line, book), {'over': None, 'under': None})
                    if name.startswith('u'):
                        rec['under'] = price
                    else:
                        rec['over'] = price
        if mo['books']:
            match_odds.append(mo)

    def emit(m):
        return [{'player': p, 'market': mk, 'line': ln, 'over': pr['over'],
                 'under': pr['under'], 'book': bk} for (p, mk, ln, bk), pr in m.items()]

    return {
        'updated': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'),
        'source': 'rapidoddsapi (%s fetcher)' % sport,
        'books': sorted(books), 'lines': emit(lines_map), 'alt': emit(alt_map),
        'matchOdds': match_odds,
    }


def main():
    argv = sys.argv[1:]
    force, window, pos, i = False, 3, [], 0
    while i < len(argv):
        a = argv[i]
        if a == '--force':
            force = True
        elif a == '--window':
            window = int(argv[i + 1]); i += 1
        else:
            pos.append(a)
        i += 1
    if len(pos) < 2:
        print('usage: fetch_odds.py <SPORT_ID> <output_path> [--force] [--window N]'); sys.exit(1)
    sport, out_path = pos[0].upper(), pos[1]
    if sport not in SPORTS:
        print('unknown sport:', sport); sys.exit(1)
    go, why = should_pull(out_path, force, window)
    if not go:
        print('%s skip: %s' % (sport, why)); return
    print('%s pull: %s' % (sport, why))
    key = os.environ.get('ROA_API_KEY', '')
    if not key:
        print('ROA_API_KEY not set'); sys.exit(1)
    mkmap = SPORTS[sport]
    markets = list(mkmap.keys()) + [k + '_milestones' for k in mkmap]
    client = RapidOddsAPI(api_key=key)
    resp = client.get_odds(sport, markets, BOOKMAKERS)
    data = transform(resp, mkmap, sport)
    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    with open(out_path, 'w') as fh:
        json.dump(data, fh, separators=(',', ':'))
    print('%s odds: %d lines, %d alt, %d books, %d games (credits ~%d)' % (
        sport, len(data['lines']), len(data['alt']), len(data['books']), len(data['matchOdds']),
        len(markets) * math.ceil(len(BOOKMAKERS) / 5)))


if __name__ == '__main__':
    main()
