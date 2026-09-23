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


def should_pull(out_path, force, window_hours, within_hours=None, every_mins=None):
    """Decide whether this run pulls.

    --force                     always (the daily sweep)
    --window N                  once, when the window opens N hours before the first game
    --within H --every M        REFRESH MODE: pull whenever the next game is within H hours and the
                                stored file is older than M minutes.

    Refresh mode is what keeps prices current through a slate. --window latches ("already pulled
    this window"), so extra crons under it do nothing; refresh mode re-pulls on a timer while games
    are near and spends nothing on days with none. _first_game only returns games still to start,
    so as a slate progresses the target moves to the next game and the refresh follows it.
    """
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    if force:
        return True, 'forced (sweep)'
    base = os.path.dirname(out_path) or '.'
    fg = _first_game(_load(os.path.join(base, 'fixture.json')) or [], now)
    if fg is None:
        return False, 'no upcoming games'
    prev = _load(out_path)
    upd = _parse_dt(prev.get('updated')) if isinstance(prev, dict) else None
    if within_hours is not None:
        if now < fg - datetime.timedelta(hours=within_hours):
            return False, 'next game %s UTC is more than %gh away' % (fg.strftime('%Y-%m-%d %H:%M'), within_hours)
        if every_mins and upd is not None:
            age = (now - upd).total_seconds() / 60.0
            if age < every_mins:
                return False, 'refreshed %.0f min ago (every %g)' % (age, every_mins)
        return True, 'refresh (next game %s UTC)' % fg.strftime('%Y-%m-%d %H:%M')
    window_start = fg - datetime.timedelta(hours=window_hours)
    if now < window_start:
        return False, 'before window (first game %s UTC)' % fg.strftime('%Y-%m-%d %H:%M')
    if upd and upd >= window_start:
        return False, 'already pulled this window'
    return True, 'window open (first game %s UTC)' % fg.strftime('%Y-%m-%d %H:%M')


BOOKMAKERS = ['Sportsbet', 'TAB', 'Pointsbet', 'Ladbrokes', 'Unibet', 'BetRight', 'Dabble', 'Bet365']

# per-sport: rapidoddsapi main market key -> JTT market key (config.js uses the JTT keys).
# milestone (X+) ladders are the same base key + '_milestones'; they feed the `alt` array.
SPORTS = {
    # NHL: keys are the gamelog fields in nhl/data/gamelogs_*.json. Only four player props exist
    # for hockey - no saves, blocks, hits or PP points - so those config markets stay unpriced.
    'NHL': {
        'player_goals': 'goals', 'player_points': 'points',
        'player_shots_on_goal': 'shots', 'player_assists': 'assists',
    },
    # Keys are the gamelog field names in nbl/data/gamelogs_*.json, so every market joins to its
    # stat with no mapping in the shell. pr/pa/ra/pra are pre-summed on each gamelog row.
    'NBL': {
        'player_points': 'points', 'player_rebounds': 'rebounds', 'player_assists': 'assists',
        'player_made_threes': 'threes', 'player_steals': 'steals', 'player_blocks': 'blocks',
        'player_points_rebounds_assists': 'pra', 'player_points_rebounds': 'pr',
        'player_points_assists': 'pa', 'player_rebounds_assists': 'ra',
    },
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
    'EPL': {
        'player_goals': 'goals', 'player_shots': 'shots', 'player_shots_on_target': 'shotsOn',
        'player_assists': 'assists', 'player_tackles': 'tackles',
        'player_fouls_committed': 'foulsCommitted', 'player_cards': 'cards',
        'goalkeeper_saves': 'saves',
    },
    'NFL': {
        'player_passing_yds': 'passYds', 'player_passing_tds': 'passTds',
        'player_passing_attempts': 'passAtt', 'player_rushing_yds': 'rushYds',
        'player_rushing_attempts': 'rushAtt', 'player_receptions': 'receptions',
        'player_receiving_yds': 'recYds', 'player_rushing_receiving_yds': 'rushRecYds',
        'player_touchdowns': 'anytimeTd', 'player_tackles_assists': 'tackles',
        'player_sacks': 'sacks', 'player_kicking_points': 'kickingPts', 'player_field_goals_made': 'fgMade',
        'player_passing_interceptions': 'passInt',
        # keys match CHUNK_DEFS in nfl/signals.js exactly so Chunk Plays' existing oddsFor()
        # hook picks up a real posted line instead of its estimated threshold
        'player_longest_reception': 'longRec', 'player_longest_rush': 'longRush', 'player_longest_pass': 'longComp',
    },
}

# some props only exist as ONE of {flat over/under, milestone ladder} -> requesting the
# other side just wastes a credit for zero rows, so handle these two shapes explicitly
MILESTONE_ONLY_MARKETS = {'player_touchdowns'}        # anytime/2+/3+ has no flat market
BASE_ONLY_MARKETS      = {'player_tackles_assists'}   # no milestones ladder is offered for this one

# soccer props are milestone-only (X+) ladders -> skip the base-market request to halve credits
MILESTONES_ONLY = {'EPL'}

# game-level markets to also request per sport (feed the matchOdds h2h/total the shell renders)
GAME_MARKETS = {
    'EPL': ['head_to_head_3_way', 'alternate_lines',
            'alternate_total_goals', 'alternate_total_corners', 'alternate_total_cards',
            'alternate_total_shots', 'alternate_total_shots_on_target',
            'alternate_team_total_goals',
            'head_to_head_3_way_1st_half', 'alternate_total_goals_1st_half'],
    'NFL': ['head_to_head', 'alternate_lines', 'alternate_total_points', 'alternate_total_touchdowns',
            'alternate_team_total_points', 'head_to_head_1st_half', 'alternate_lines_1st_half',
            'alternate_total_points_1st_half', 'alternate_team_total_points_1st_half'],
    # period splits exist too, but transform() only knows full-game and first-half keys, so
    # requesting them would spend credits on rows that get thrown away
    'NHL': ['head_to_head', 'alternate_lines', 'alternate_total_goals', 'alternate_team_total_goals'],
    'NBL': ['head_to_head', 'alternate_lines', 'alternate_total_points', 'alternate_team_total_points',
            'head_to_head_1st_half', 'alternate_lines_1st_half', 'alternate_total_points_1st_half',
            'alternate_team_total_points_1st_half'],
}
H2H_2WAY = {'NFL', 'NBL', 'NHL'}   # 2-way moneyline (no draw) vs soccer's 3-way

# matchOdds must use the same team codes as fixture.json / teams.json, or the shell can't
# join them (it keys on [home,away]). ROA returns full names for NFL, so map them here.
NFL_ABBR = {
    'arizona cardinals':'ARI','atlanta falcons':'ATL','baltimore ravens':'BAL','buffalo bills':'BUF',
    'carolina panthers':'CAR','chicago bears':'CHI','cincinnati bengals':'CIN','cleveland browns':'CLE',
    'dallas cowboys':'DAL','denver broncos':'DEN','detroit lions':'DET','green bay packers':'GB',
    'houston texans':'HOU','indianapolis colts':'IND','jacksonville jaguars':'JAX','kansas city chiefs':'KC',
    'las vegas raiders':'LV','los angeles chargers':'LAC','los angeles rams':'LA','miami dolphins':'MIA',
    'minnesota vikings':'MIN','new england patriots':'NE','new orleans saints':'NO','new york giants':'NYG',
    'new york jets':'NYJ','philadelphia eagles':'PHI','pittsburgh steelers':'PIT','san francisco 49ers':'SF',
    'seattle seahawks':'SEA','tampa bay buccaneers':'TB','tennessee titans':'TEN','washington commanders':'WAS',
}

# NBL fixture.json / teams.json use three-letter codes; books use full names, and the Breakers
# appear as both "New Zealand Breakers" and "NZ Breakers" - both have to land on NZL.
NBL_ABBR = {
    'adelaide 36ers':'ADL','brisbane bullets':'BRI','cairns taipans':'CNS','illawarra hawks':'ILL',
    'melbourne united':'MEL','new zealand breakers':'NZL','nz breakers':'NZL','perth wildcats':'PER',
    'south east melbourne phoenix':'SEM','s.e. melbourne phoenix':'SEM','se melbourne phoenix':'SEM',
    'sydney kings':'SYD','tasmania jackjumpers':'TAS','tasmania jack jumpers':'TAS',
}

# NHL fixture.json / teams.json use tricodes; books use full names. Utah has had two names.
NHL_ABBR = {
    'anaheim ducks':'ANA','boston bruins':'BOS','buffalo sabres':'BUF','calgary flames':'CGY',
    'carolina hurricanes':'CAR','chicago blackhawks':'CHI','colorado avalanche':'COL',
    'columbus blue jackets':'CBJ','dallas stars':'DAL','detroit red wings':'DET',
    'edmonton oilers':'EDM','florida panthers':'FLA','los angeles kings':'LAK','minnesota wild':'MIN',
    'montreal canadiens':'MTL','montréal canadiens':'MTL','nashville predators':'NSH',
    'new jersey devils':'NJD','new york islanders':'NYI','new york rangers':'NYR',
    'ottawa senators':'OTT','philadelphia flyers':'PHI','pittsburgh penguins':'PIT',
    'san jose sharks':'SJS','seattle kraken':'SEA','st louis blues':'STL','st. louis blues':'STL',
    'tampa bay lightning':'TBL','toronto maple leafs':'TOR','utah mammoth':'UTA',
    'utah hockey club':'UTA','vancouver canucks':'VAN','vegas golden knights':'VGK',
    'washington capitals':'WSH','winnipeg jets':'WPG',
}

def team_code(name, sport):
    if sport == 'NFL':
        return NFL_ABBR.get((name or '').strip().lower(), name)
    if sport == 'NBL':
        return NBL_ABBR.get((name or '').strip().lower(), name)
    if sport == 'NHL':
        return NHL_ABBR.get((name or '').strip().lower(), name)
    return name

# NOTE on the markets below `alternate_total_points` in NFL's list: team totals and the
# 1st-half splits are a market shape we haven't seen a real ROA payload for yet (unlike
# everything above, which was verified against actual returned data). They're wired using
# the same outcome-naming convention that h2h/spread/totals turned out to use - but if they
# come back empty or wrong after the first run, send a sample matchOdds/game entry and it's
# a quick fix, same as every other market so far.
H2H_1H_KEYS  = ('head_to_head_1st_half', 'head_to_head_3_way_1st_half')
LINE_1H_KEY  = 'alternate_lines_1st_half'
TEAM_TOTAL_KEYS = {'alternate_team_total_points': 'teamTotal', 'alternate_team_total_points_1st_half': 'teamTotal1H',
                   'alternate_team_total_goals': 'teamTotal'}
FIELD_MARKETS = {'player_1st_touchdown_scorer': 'firstScorer',   # "player X to do Y": no line, one price per player
                 'player_1st_goalscorer': 'firstScorer'}
FIELD_BY_SPORT = {'NFL': ['player_1st_touchdown_scorer'], 'EPL': ['player_1st_goalscorer']}
TOTAL_KEYS = {
    'alternate_total_goals': 'total', 'alternate_total_corners': 'totalCorners', 'alternate_total_cards': 'totalCards',
    'alternate_total_points': 'total', 'alternate_total_touchdowns': 'totalTds',
    'alternate_total_points_1st_half': 'total1H',
    'alternate_total_shots': 'totalShots', 'alternate_total_shots_on_target': 'totalSOT',
    'alternate_total_goals_1st_half': 'total1H',
}


def jtt_market(key, mkmap):
    base = key[:-len('_milestones')] if key.endswith('_milestones') else key
    return mkmap.get(base), key.endswith('_milestones')


def transform(resp, mkmap, sport):
    games = group_games(resp)
    lines_map, alt_map, books, match_odds = {}, {}, set(), []
    for g in games:
        game = g.get('game', {})
        home_raw, away_raw = game.get('home_team', ''), game.get('away_team', '')
        # NOTE: outcome names in h2h / spread markets use the API's FULL team names, so keep
        # them for matching. The abbreviations are only for the output (they must line up with
        # fixture.json). Converting before matching silently killed h2h and spreads.
        home, away = team_code(home_raw, sport), team_code(away_raw, sport)
        mo = {'home': home, 'away': away}
        totals = {}
        spreads = {}       # (book) -> {'home':{point,price}, 'away':{point,price}}
        spreads1h = {}     # same shape, for the 1st-half line
        team_totals = {}   # jk -> {(side,book): {'points','over','under'}}
        h2h1h = {}
        for bk in g.get('bookmakers', []):
            book = bk.get('name', '')
            books.add(book)
            for mkt in bk.get('markets', []):
                key = mkt.get('key', '')
                outs = mkt.get('outcomes', [])
                if key in FIELD_MARKETS:
                    jk = FIELD_MARKETS[key]
                    for o in outs:
                        player, price = o.get('player_name') or o.get('name'), o.get('price')
                        if not player or not price:
                            continue
                        rec = alt_map.setdefault((player, jk, 0.0, book), {'over': None, 'under': None})
                        rec['over'] = price   # a field bet: one price per player, no line
                    continue
                if key in H2H_1H_KEYS:
                    if 'h2h1H' not in mo:
                        h = {}
                        for o in outs:
                            nm, pr = (o.get('name') or ''), o.get('price')
                            if not pr:
                                continue
                            low = nm.lower()
                            if low == 'home' or nm == home_raw or nm == home:
                                h['home'] = pr
                            elif low == 'away' or nm == away_raw or nm == away:
                                h['away'] = pr
                            elif low == 'draw':
                                h['draw'] = pr
                        if h.get('home') and h.get('away'):
                            h['book'] = book
                            mo['h2h1H'] = h
                    continue
                if key == LINE_1H_KEY:
                    rec = spreads1h.setdefault(book, {})
                    for o in outs:
                        pt, pr = o.get('point'), o.get('price')
                        nm = (o.get('name') or '')
                        if pt is None or not pr:
                            continue
                        if nm in (home_raw, home) or nm.lower() == 'home':
                            rec['home'] = (float(pt), pr)
                        elif nm in (away_raw, away) or nm.lower() == 'away':
                            rec['away'] = (float(pt), pr)
                    continue
                if key in TEAM_TOTAL_KEYS:
                    jk = TEAM_TOTAL_KEYS[key]
                    for o in outs:
                        pt, pr = o.get('point'), o.get('price')
                        nm = (o.get('name') or '')
                        side_src = o.get('team') or o.get('description') or nm
                        low_side = (side_src or '').lower()
                        if pt is None or not pr:
                            continue
                        if side_src in (home_raw, home) or 'home' in low_side or home_raw.lower() in low_side:
                            side = 'home'
                        elif side_src in (away_raw, away) or 'away' in low_side or away_raw.lower() in low_side:
                            side = 'away'
                        else:
                            continue
                        uo = nm.lower()
                        rec = team_totals.setdefault((jk, side, book), {'points': float(pt), 'over': None, 'under': None})
                        if uo.startswith('u'):
                            rec['under'] = pr
                        else:
                            rec['over'] = pr
                    continue
                if key in ('head_to_head', 'head_to_head_3_way'):
                    if 'h2h' not in mo:                       # first book that carries it
                        h = {}
                        for o in outs:
                            nm, pr = (o.get('name') or ''), o.get('price')
                            if not pr:
                                continue
                            low = nm.lower()
                            if low == 'home' or nm == home_raw or nm == home:
                                h['home'] = pr
                            elif low == 'away' or nm == away_raw or nm == away:
                                h['away'] = pr
                            elif low == 'draw':
                                h['draw'] = pr
                        if h.get('home') and h.get('away'):
                            h['book'] = book
                            mo['h2h'] = h
                    continue
                if key == 'alternate_lines':
                    rec = spreads.setdefault(book, {})
                    for o in outs:
                        pt, pr = o.get('point'), o.get('price')
                        nm = (o.get('name') or '')
                        if pt is None or not pr:
                            continue
                        if nm in (home_raw, home) or nm.lower() == 'home':
                            rec['home'] = (float(pt), pr)
                        elif nm in (away_raw, away) or nm.lower() == 'away':
                            rec['away'] = (float(pt), pr)
                    continue
                if key in TOTAL_KEYS:
                    tl = totals.setdefault(TOTAL_KEYS[key], {})
                    for o in outs:
                        pt, pr, nm = o.get('point'), o.get('price'), (o.get('name') or '').lower()
                        if pt is None or not pr:
                            continue
                        rec = tl.setdefault((float(pt), book), {'over': None, 'under': None})
                        if nm.startswith('u'):
                            rec['under'] = pr
                        else:
                            rec['over'] = pr
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
        best_sp = None
        for book, rec in spreads.items():
            if 'home' in rec and 'away' in rec:
                d = abs(rec['home'][1] - 1.90)
                if best_sp is None or d < best_sp[0]:
                    best_sp = (d, {'home': rec['home'][0], 'homeOdds': rec['home'][1],
                                    'away': rec['away'][0], 'awayOdds': rec['away'][1], 'book': book})
        if best_sp:
            mo['line'] = best_sp[1]
        best_sp1h = None
        for book, rec in spreads1h.items():
            if 'home' in rec and 'away' in rec:
                d = abs(rec['home'][1] - 1.90)
                if best_sp1h is None or d < best_sp1h[0]:
                    best_sp1h = (d, {'home': rec['home'][0], 'homeOdds': rec['home'][1],
                                      'away': rec['away'][0], 'awayOdds': rec['away'][1], 'book': book})
        if best_sp1h:
            mo['line1H'] = best_sp1h[1]
        team_best = {}   # jk -> {'home':{...}, 'away':{...}}
        for (jk, side, book), rec in team_totals.items():
            if rec['over'] and rec['under']:
                d = abs(rec['over'] - 1.90)
                cur = team_best.setdefault(jk, {})
                if side not in cur or d < cur[side][0]:
                    cur[side] = (d, {'points': rec['points'], 'over': rec['over'], 'under': rec['under'], 'book': book})
        for jk, sides in team_best.items():
            if 'home' in sides and 'away' in sides:
                mo[jk] = {'home': sides['home'][1], 'away': sides['away'][1]}
        for tk, tl in totals.items():
            best = None
            for (pt, book), rec in tl.items():
                if rec['over'] and rec['under']:
                    d = abs(rec['over'] - 1.90)               # the balanced main line
                    if best is None or d < best[0]:
                        best = (d, {'points': pt, 'over': rec['over'], 'under': rec['under'], 'book': book})
            if best:
                mo[tk] = best[1]
        if mo.get('h2h') or mo.get('line') or mo.get('total') or mo.get('totalCorners') or mo.get('totalCards') or mo.get('h2h1H') or mo.get('line1H') or mo.get('total1H') or mo.get('totalTds') or mo.get('teamTotal') or mo.get('teamTotal1H') or mo.get('totalShots') or mo.get('totalSOT'):
            match_odds.append(mo)

    def emit(m):
        return [{'player': p, 'market': mk, 'line': ln, 'over': pr['over'],
                 'under': pr['under'], 'book': bk} for (p, mk, ln, bk), pr in m.items()]

    # `books` must be per-book PRICE ROWS, not bookmaker names: the shell builds bookIdx from
    # it, and bookIdx is what the Radar (_radarTwoWayLines) and the arb/middle/EV tools read.
    # Emitting a bare name list left bookIdx empty, so the Radar saw no each-way markets at all.
    # Two-way rows only (both sides priced) - that's what those tools need.
    book_rows = [r for r in emit(lines_map)
                 if r.get('over') is not None and r.get('under') is not None]

    return {
        'updated': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'),
        'source': 'rapidoddsapi (%s fetcher)' % sport,
        'bookNames': sorted(books),
        'books': book_rows, 'lines': emit(lines_map), 'alt': emit(alt_map),
        'matchOdds': match_odds,
    }


def main():
    argv = sys.argv[1:]
    force, window, pos, i = False, 3, [], 0
    within, every = None, None
    while i < len(argv):
        a = argv[i]
        if a == '--force':
            force = True
        elif a == '--window':
            window = int(argv[i + 1]); i += 1
        elif a == '--within':
            within = float(argv[i + 1]); i += 1
        elif a == '--every':
            every = float(argv[i + 1]); i += 1
        else:
            pos.append(a)
        i += 1
    if len(pos) < 2:
        print('usage: fetch_odds.py <SPORT_ID> <output_path> [--force] [--window N] '
              '[--within HOURS --every MINUTES]'); sys.exit(1)
    sport, out_path = pos[0].upper(), pos[1]
    if sport not in SPORTS:
        print('unknown sport:', sport); sys.exit(1)
    go, why = should_pull(out_path, force, window, within, every)
    if not go:
        print('%s skip: %s' % (sport, why)); return
    print('%s pull: %s' % (sport, why))
    key = os.environ.get('ROA_API_KEY', '')
    if not key:
        print('ROA_API_KEY not set'); sys.exit(1)
    mkmap = SPORTS[sport]
    if sport in MILESTONES_ONLY:
        markets = [k + '_milestones' for k in mkmap]
    else:
        markets = []
        for k in mkmap:
            if k in MILESTONE_ONLY_MARKETS:
                markets.append(k + '_milestones')
            elif k in BASE_ONLY_MARKETS:
                markets.append(k)
            else:
                markets.append(k)
                markets.append(k + '_milestones')
    markets += GAME_MARKETS.get(sport, [])   # game markets requested for every sport that defines them, not just milestones-only ones
    markets += FIELD_BY_SPORT.get(sport, [])  # first-scorer style field bets
    client = RapidOddsAPI(api_key=key)
    resp = client.get_odds(sport, markets, BOOKMAKERS)
    data = transform(resp, mkmap, sport)
    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    with open(out_path, 'w') as fh:
        json.dump(data, fh, separators=(',', ':'))
    print('%s odds: %d lines, %d alt, %d books, %d games (credits ~%d)' % (
        sport, len(data['lines']), len(data['alt']), len(data['bookNames']), len(data['matchOdds']),
        len(markets) * math.ceil(len(BOOKMAKERS) / 5)))


if __name__ == '__main__':
    main()
