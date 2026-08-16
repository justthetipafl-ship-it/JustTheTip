#!/usr/bin/env python3
"""Fetch player-prop odds from RapidOddsAPI and write a JTT-shaped odds.json.

Usage: fetch_odds.py <SPORT_ID> <output_path>
  SPORT_ID: WNBA | MLB   (extend SPORTS below for more)
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
    if len(sys.argv) < 3:
        print('usage: fetch_odds.py <SPORT_ID> <output_path>'); sys.exit(1)
    sport, out_path = sys.argv[1].upper(), sys.argv[2]
    if sport not in SPORTS:
        print('unknown sport:', sport); sys.exit(1)
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
