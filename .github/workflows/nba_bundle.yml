#!/usr/bin/env python3
"""
JTT NBA — bundle builder
Pulls player game logs + schedule from the NBA Stats API (via nba_api) and writes
NBA/bundle.json in the shape index.html's applyBundle() expects:
  { round, password, player:[...], fixture:[...], injury:[...] }

Player rows are raw game-log rows; the front-end aggregates windows (L5/L10/L20)
and derives DVP itself, so this script stays thin.

USAGE
  pip install nba_api requests
  python fetch_nba_bundle.py --season 2025-26 --out NBA/bundle.json

THE IP-BLOCKING PROBLEM (read this before wiring to GitHub Actions)
  stats.nba.com silently throttles / 403s datacenter IPs (AWS, GitHub runners).
  It works fine from a home connection. Mitigations baked in below:
    * browser-like headers (nba_api sets some; we reinforce)
    * timeout + exponential backoff retry
    * optional proxy via NBA_PROXY env var (residential proxy = most reliable on CI)
  If runs fail on GitHub Actions with timeouts, either:
    (a) set NBA_PROXY to a residential proxy, or
    (b) run this on a self-hosted runner / cron on your own box and push the JSON, or
    (c) switch SOURCE to 'balldontlie' (paid tier) — stub left at bottom.
  SMOKE TEST FIRST: run `python fetch_nba_bundle.py --smoke` from the SAME environment
  that will run it in production before building anything on top.
"""
import argparse, json, os, sys, time, datetime as dt

HEADERS = {
    'Host': 'stats.nba.com',
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/120.0 Safari/537.36'),
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.nba.com/',
    'Origin': 'https://www.nba.com',
    'x-nba-stats-origin': 'stats',
    'x-nba-stats-token': 'true',
    'Connection': 'keep-alive',
}
PROXY = os.environ.get('NBA_PROXY')  # e.g. http://user:pass@host:port
TIMEOUT = 30
RETRIES = 4

# position map: nba_api gives 'G'/'F'/'C' or 'G-F' etc. Normalise to PG/SG/SF/PF/C-ish.
POS_NORM = {'G':'PG','F':'SF','C':'C','G-F':'SG','F-G':'SF','F-C':'PF','C-F':'C'}

def _call(endpoint_fn, **kw):
    """Run an nba_api endpoint with retry/backoff. endpoint_fn is the class."""
    last = None
    for attempt in range(RETRIES):
        try:
            kwargs = dict(timeout=TIMEOUT, headers=HEADERS)
            if PROXY:
                kwargs['proxy'] = PROXY
            ep = endpoint_fn(**kw, **kwargs)
            return ep.get_normalized_dict()
        except Exception as e:  # noqa
            last = e
            wait = 2 ** attempt
            print(f"  retry {attempt+1}/{RETRIES} after {wait}s ({type(e).__name__}: {e})", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"NBA Stats API failed after {RETRIES} tries: {last}")

def fetch_player_logs(season):
    """One row per player per game for the season (regular season)."""
    from nba_api.stats.endpoints import leaguegamelog, commonallplayers
    # player game logs
    print(f"[player logs] season={season} ...")
    data = _call(leaguegamelog.LeagueGameLog,
                 season=season, season_type_all_star='Regular Season',
                 player_or_team_abbreviation='P')
    rows = data.get('LeagueGameLog', [])
    print(f"  {len(rows)} player-game rows")

    # position lookup
    print("[positions] commonallplayers ...")
    cap = _call(commonallplayers.CommonAllPlayers, is_only_current_season=1, season=season)
    pos_by_id = {}
    for p in cap.get('CommonAllPlayers', []):
        pid = p.get('PERSON_ID')
        # CommonAllPlayers has no POSITION; fall back to G/F/C via roster later if needed
        pos_by_id[pid] = ''
    # Better positions come from commonteamroster; keep simple for P1 — default SF, refine later.

    out = []
    for r in rows:
        opp = _opp_from_matchup(r.get('MATCHUP', ''))
        out.append({
            'name': r.get('PLAYER_NAME', '').strip(),
            'team': r.get('TEAM_ABBREVIATION', '').strip(),
            'pos':  POS_NORM.get(pos_by_id.get(r.get('PLAYER_ID'), ''), 'SF'),
            'opp':  opp,
            'date': r.get('GAME_DATE', ''),
            'min':  _to_min(r.get('MIN')),
            'pts':  r.get('PTS', 0) or 0,
            'reb':  r.get('REB', 0) or 0,
            'ast':  r.get('AST', 0) or 0,
            'fg3m': r.get('FG3M', 0) or 0,
            'stl':  r.get('STL', 0) or 0,
            'blk':  r.get('BLK', 0) or 0,
            'tov':  r.get('TOV', 0) or 0,
        })
    return out

def enrich_positions(player_rows, season):
    """Pull real positions from each team roster and overwrite the SF defaults."""
    from nba_api.stats.endpoints import commonteamroster
    from nba_api.stats.static import teams as static_teams
    pos_by_name = {}
    for t in static_teams.get_teams():
        try:
            data = _call(commonteamroster.CommonTeamRoster, team_id=t['id'], season=season)
            for p in data.get('CommonTeamRoster', []):
                raw = (p.get('POSITION') or '').strip()
                pos_by_name[p.get('PLAYER', '').strip()] = POS_NORM.get(raw, _norm_loose(raw))
            time.sleep(0.6)  # be polite
        except Exception as e:
            print(f"  roster {t['abbreviation']} failed: {e}", file=sys.stderr)
    for r in player_rows:
        if r['name'] in pos_by_name and pos_by_name[r['name']]:
            r['pos'] = pos_by_name[r['name']]
    return player_rows

def _norm_loose(raw):
    raw = raw.upper()
    if 'G' in raw and 'F' in raw: return 'SG'
    if raw.startswith('G'): return 'PG'
    if 'C' in raw: return 'C'
    if 'F' in raw: return 'SF'
    return 'SF'

def fetch_fixture(date=None):
    """Tonight's slate (or a given YYYY-MM-DD) -> [{gid, home, away}]."""
    from nba_api.stats.endpoints import scoreboardv2
    date = date or dt.date.today().strftime('%Y-%m-%d')
    print(f"[fixture] {date} ...")
    data = _call(scoreboardv2.ScoreboardV2, game_date=date)
    games = data.get('GameHeader', [])
    id2abbr = _team_id_map()
    out = []
    for g in games:
        out.append({
            'gid': str(g.get('GAME_ID')),
            'home': id2abbr.get(g.get('HOME_TEAM_ID'), ''),
            'away': id2abbr.get(g.get('VISITOR_TEAM_ID'), ''),
        })
    print(f"  {len(out)} games")
    return [g for g in out if g['home'] and g['away']]

def _team_id_map():
    from nba_api.stats.static import teams as static_teams
    return {t['id']: t['abbreviation'] for t in static_teams.get_teams()}

def _opp_from_matchup(m):
    # "BOS vs. LAL" or "BOS @ LAL" -> opponent abbr
    if not m: return ''
    parts = m.replace('vs.', '@').split('@')
    return parts[-1].strip() if len(parts) > 1 else ''

def _to_min(v):
    if v is None: return 0
    try: return round(float(v))
    except Exception:
        if isinstance(v, str) and ':' in v:
            mm, ss = v.split(':'); return int(mm)
        return 0

def build(season, out_path, password, fixture_date):
    players = fetch_player_logs(season)
    players = enrich_positions(players, season)
    fixture = fetch_fixture(fixture_date)
    bundle = {
        'round': dt.datetime.utcnow().strftime('Updated %Y-%m-%d %H:%MZ'),
        'password': password,           # set None to disable the gate
        'player': players,
        'fixture': fixture,
        'injury': [],                   # P1.5: wire NBA injury report scrape here
    }
    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(bundle, f, separators=(',', ':'))
    kb = os.path.getsize(out_path) / 1024
    print(f"\nWROTE {out_path} — {len(players)} player-rows, {len(fixture)} games, {kb:.0f} KB")

def smoke():
    """Minimal reachability test from the CURRENT environment. Run this on the runner."""
    print("SMOKE: hitting stats.nba.com from this environment...")
    try:
        from nba_api.stats.endpoints import scoreboardv2
        d = _call(scoreboardv2.ScoreboardV2, game_date=dt.date.today().strftime('%Y-%m-%d'))
        n = len(d.get('GameHeader', []))
        print(f"SMOKE PASS — reached NBA Stats API, {n} games found today.")
        print("Safe to run the full build from this environment.")
        return 0
    except Exception as e:
        print(f"SMOKE FAIL — {e}")
        print("This environment is likely IP-blocked. Use NBA_PROXY, a self-hosted runner, or balldontlie.")
        return 1

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', default='2025-26')
    ap.add_argument('--out', default='NBA/bundle.json')
    ap.add_argument('--password', default=os.environ.get('JTT_NBA_PASSWORD'))
    ap.add_argument('--date', default=None, help='fixture date YYYY-MM-DD (default: today)')
    ap.add_argument('--smoke', action='store_true', help='reachability test only')
    a = ap.parse_args()
    if a.smoke:
        sys.exit(smoke())
    build(a.season, a.out, a.password, a.date)
