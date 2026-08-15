#!/usr/bin/env python3
"""Fetch WNBA player-prop odds from RapidOddsAPI and write a JTT-shaped odds.json.

Usage: fetch_wnba_odds.py <output_path>
Reads the API key from the ROA_API_KEY environment variable.

Output matches the AFL odds.json shape the shell already parses:
  {updated, source, books, lines:[{player,market,line,over,under,book}],
   alt:[...same shape, X+ ladders...], matchOdds:{...}}

Credits per run = len(MARKET_TYPES) * ceil(len(BOOKMAKERS)/5)  (odds)  + 1 (results).
Keep BOOKMAKERS short to keep WNBA cheap.
"""
import json
import math
import os
import sys
import datetime

from rapidoddsapi import RapidOddsAPI
from rapidoddsapi.helpers import group_games

# --- what to pull (kept lean for low credit cost) ---
MAIN = ['player_points', 'player_rebounds', 'player_assists', 'player_made_threes',
        'player_points_rebounds_assists', 'player_steals', 'player_blocks']
ALT = [m + '_milestones' for m in MAIN]
MARKET_TYPES = MAIN + ALT
BOOKMAKERS = ['Sportsbet', 'TAB', 'Pointsbet', 'Ladbrokes', 'Unibet', 'BetRight', 'Dabble']

# rapidoddsapi market key -> JTT market key (config.js uses these)
MKT = {
    'player_points': 'points', 'player_rebounds': 'rebounds', 'player_assists': 'assists',
    'player_made_threes': 'threes', 'player_points_rebounds_assists': 'pra',
    'player_steals': 'steals', 'player_blocks': 'blocks',
}


def jtt_market(key):
    base = key[:-len('_milestones')] if key.endswith('_milestones') else key
    return MKT.get(base), key.endswith('_milestones')


def transform(resp):
    games = group_games(resp)
    lines_map = {}   # (player, market, line, book) -> {over, under}
    alt_map = {}
    books = set()
    match_odds = []

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
                jm, is_alt = jtt_market(key)
                if not jm:
                    continue
                for o in mkt.get('outcomes', []):
                    player = o.get('player_name')
                    point = o.get('point')
                    price = o.get('price')
                    name = (o.get('name') or '').lower()
                    if player is None or point is None or not price:
                        continue
                    line = float(point)
                    if is_alt and line == int(line):   # "25+" milestone -> over 24.5
                        line -= 0.5
                    target = alt_map if is_alt else lines_map
                    k = (player, jm, line, book)
                    rec = target.setdefault(k, {'over': None, 'under': None})
                    if name.startswith('u'):
                        rec['under'] = price
                    else:                       # Over, "X+", "Yes" -> treat as over
                        rec['over'] = price
        if mo['books']:
            match_odds.append(mo)

    def emit(m):
        out = []
        for (player, market, line, book), pr in m.items():
            out.append({'player': player, 'market': market, 'line': line,
                        'over': pr['over'], 'under': pr['under'], 'book': book})
        return out

    return {
        'updated': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'),
        'source': 'rapidoddsapi (WNBA fetcher)',
        'books': sorted(books),
        'lines': emit(lines_map),
        'alt': emit(alt_map),
        'matchOdds': match_odds,
    }


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else 'wnba/data/odds.json'
    key = os.environ.get('ROA_API_KEY', '')
    if not key:
        print('ROA_API_KEY not set'); sys.exit(1)
    client = RapidOddsAPI(api_key=key)
    resp = client.get_odds('WNBA', MARKET_TYPES, BOOKMAKERS)
    data = transform(resp)
    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    with open(out_path, 'w') as fh:
        json.dump(data, fh, separators=(',', ':'))
    print('wnba odds: %d lines, %d alt, %d books, %d games (credits ~%d)' % (
        len(data['lines']), len(data['alt']), len(data['books']), len(data['matchOdds']),
        len(MARKET_TYPES) * math.ceil(len(BOOKMAKERS) / 5)))


if __name__ == '__main__':
    main()
