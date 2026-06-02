#!/usr/bin/env python3
"""
backfill_missing_teams.py
==========================
One-time backfill for teams that were missed by the original historical scrape.

Why this exists:
  The original WC backfill ran before some teams had qualified, so they don't
  have any rows in worldcup_intl_logs.json from their own perspective. They
  appear only as opponents in other teams' rows.

  Teams currently missing historical data:
    - Iraq (id 1567)
    - Sweden (id 5)
    - Cape Verde Islands (id 1533)
    - Bosnia & Herzegovina (id 1113)

What it does:
  For each missing team, fetch ALL completed fixtures since SINCE_DATE, then
  for each fixture fetch player + team stats and merge into the existing
  logs/stats JSON files. Idempotent — re-runs skip already-processed matches.

Call budget:
  ~4 teams × ~30-50 fixtures × 2 API calls per fixture = ~300-500 calls.
  Comfortably fits in a single 7500/day quota.

Usage (locally or from GitHub Actions):
  export APIFOOTBALL_KEY=your_key
  python backfill_missing_teams.py
  # or with custom team IDs:
  python backfill_missing_teams.py --teams=1567,5,1533,1113
  # or with custom data dir:
  python backfill_missing_teams.py --data=wc/data
"""

import os
import sys
import json
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: pip install requests")
    sys.exit(1)


BASE = "https://v3.football.api-sports.io"
SINCE_DATE = "2023-01-01"  # how far back to pull. ~3 years is plenty for form work.

# Default teams missing from the original backfill. Override via --teams=
# Audit found 6 WC teams with <200 rows in worldcup_intl_logs.json
DEFAULT_TEAMS = {
    1567: "Iraq",
    5:    "Sweden",
    1533: "Cape Verde Islands",
    1113: "Bosnia & Herzegovina",
    1508: "Congo DR",
    1532: "Algeria",
}


def get_arg(name, default=None):
    pre = f"--{name}="
    for arg in sys.argv[1:]:
        if arg.startswith(pre):
            return arg[len(pre):]
    return default


def get_key():
    k = get_arg("key") or os.environ.get("APIFOOTBALL_KEY")
    if not k:
        sys.exit("ERROR: Set APIFOOTBALL_KEY env var or pass --key=XXX")
    return k


def get_data_dir():
    return Path(get_arg("data", "./wc/data"))


def parse_teams():
    """Return dict of {teamId: teamName} from --teams= or defaults."""
    teams_arg = get_arg("teams")
    if not teams_arg:
        return DEFAULT_TEAMS
    return {int(t.strip()): f"Team_{t.strip()}" for t in teams_arg.split(",") if t.strip()}


API_KEY = get_key()
DATA_DIR = get_data_dir()
CACHE_DIR = DATA_DIR / ".cache" / "missing_teams"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def api_get(path, params=None):
    """Wrapper with rate limiting + persistent cache."""
    cache_key = path + "?" + urllib.parse.urlencode(sorted((params or {}).items()))
    safe = "".join(c if c.isalnum() else "_" for c in cache_key)[:200]
    cache_file = CACHE_DIR / f"{safe}.json"
    if cache_file.exists():
        with cache_file.open() as f:
            return json.load(f)
    # API-Football rate limits at ~10 req/sec — sleep before each call to stay
    # well under it and avoid 429s.
    time.sleep(0.15)
    r = requests.get(BASE + path, params=params,
                     headers={"x-apisports-key": API_KEY}, timeout=30)
    r.raise_for_status()
    data = r.json()
    with cache_file.open("w") as f:
        json.dump(data, f)
    return data


def fetch_team_fixtures_since(team_id, since_date):
    """Return list of completed fixtures for this team since since_date.

    API-Football's /fixtures endpoint requires `season` when querying by
    `team` — `from`/`to` alone returns nothing. So we iterate seasons and
    filter client-side.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    since_year   = int(since_date[:4])
    current_year = int(today[:4])

    all_fixtures = []
    for season in range(since_year, current_year + 1):
        resp = api_get("/fixtures", {
            "team":   team_id,
            "season": season,
        })
        for f in resp.get("response") or []:
            status = ((f.get("fixture") or {}).get("status") or {}).get("short", "")
            if status not in ("FT", "AET", "PEN"):
                continue  # skip not-yet-played / postponed / cancelled
            date = f["fixture"]["date"][:10]
            if date < since_date or date > today:
                continue
            all_fixtures.append(f)
    return all_fixtures


def fetch_fixture_detail(fid):
    """Get players + team stats for one fixture. Returns (players_resp, stats_resp)."""
    players = api_get("/fixtures/players", {"fixture": fid}).get("response") or []
    stats   = api_get("/fixtures/statistics", {"fixture": fid}).get("response") or []
    return players, stats


def build_row(fixture, team_block, player_block, opp_id, opp_name):
    """Mirror of the build_row in fetch_wc_update.py — produces the same shape
    of player-row that the rest of the pipeline expects.

    IMPORTANT: must include teamScore/oppScore/result/homeAway/venue or
    getTeamRecord() in the HTML will report 0 goals scored/conceded.
    """
    fxt   = fixture["fixture"]
    teams = fixture["teams"]
    goals = fixture.get("goals") or {}
    team  = team_block["team"]
    p     = player_block["player"]
    s     = player_block["statistics"][0]
    games    = s.get("games", {})    or {}
    shots    = s.get("shots", {})    or {}
    pgoals   = s.get("goals", {})    or {}
    passes   = s.get("passes", {})   or {}
    tackles  = s.get("tackles", {})  or {}
    duels    = s.get("duels", {})    or {}
    dribbles = s.get("dribbles", {}) or {}
    fouls    = s.get("fouls", {})    or {}
    cards    = s.get("cards", {})    or {}
    penalty  = s.get("penalty", {})  or {}

    is_home    = teams["home"]["id"] == team["id"]
    team_score = goals.get("home") if is_home else goals.get("away")
    opp_score  = goals.get("away") if is_home else goals.get("home")
    home_away  = "H" if is_home else "A"
    if team_score is None or opp_score is None:
        result = None
    elif team_score > opp_score:
        result = "W"
    elif team_score < opp_score:
        result = "L"
    else:
        result = "D"

    # Determine competition tier from league info
    league_id = (fixture.get("league") or {}).get("id")
    league_name = (fixture.get("league") or {}).get("name") or ""
    tier = "other"
    if league_id == 1:
        tier = "world_cup"
    elif "qualification" in league_name.lower() or "qualifier" in league_name.lower():
        tier = "qualifying"
    elif "friendly" in league_name.lower() or "friendlies" in league_name.lower():
        tier = "friendly"
    elif league_id in (4, 9, 13, 6, 7, 35, 36):  # Euros, Copa America, AFC Cup, etc.
        tier = "tournament"

    return {
        "matchId":          fxt["id"],
        "date":             fxt["date"][:10],
        "team":             team["name"],
        "teamId":           team["id"],
        "opponent":         opp_name,
        "opponentId":       opp_id,
        "venue":            (fxt.get("venue") or {}).get("name"),
        "homeAway":         home_away,
        "result":           result,
        "teamScore":        int(team_score) if team_score is not None else None,
        "oppScore":         int(opp_score)  if opp_score  is not None else None,
        "competition":      league_name,
        "competitionTier":  tier,
        "playerId":         p.get("id"),
        "playerName":       p.get("name"),
        "shirtNumber":      games.get("number"),
        "position":         games.get("position"),
        "started":          (games.get("substitute") is False),
        "minutes":          games.get("minutes") or 0,
        "rating":           float(games["rating"]) if games.get("rating") else None,
        "shots":            shots.get("total") or 0,
        "shotsOn":          shots.get("on") or 0,
        "goals":            pgoals.get("total") or 0,
        "assists":          pgoals.get("assists") or 0,
        "saves":            pgoals.get("saves") or 0,
        "conceded":         pgoals.get("conceded") or 0,
        "passes":           passes.get("total") or 0,
        "keyPasses":        passes.get("key") or 0,
        "passAccuracy":     passes.get("accuracy"),
        "tackles":          tackles.get("total") or 0,
        "blocks":           tackles.get("blocks") or 0,
        "interceptions":    tackles.get("interceptions") or 0,
        "duelsTotal":       duels.get("total") or 0,
        "duelsWon":         duels.get("won") or 0,
        "dribblesAttempted":dribbles.get("attempts") or 0,
        "dribblesSuccess":  dribbles.get("success") or 0,
        "foulsDrawn":       fouls.get("drawn") or 0,
        "foulsCommitted":   fouls.get("committed") or 0,
        "yellowCard":       cards.get("yellow") or 0,
        "redCard":          cards.get("red") or 0,
        "penaltyWon":       penalty.get("won") or 0,
        "penaltyCommitted": penalty.get("commited") or 0,  # api typo
        "penaltyScored":    penalty.get("scored") or 0,
        "penaltyMissed":    penalty.get("missed") or 0,
        "penaltySaved":     penalty.get("saved") or 0,
    }


def build_match_team_stats(stats_resp):
    """Convert /fixtures/statistics response into our compact per-team format."""
    if not stats_resp:
        return None
    out = {}
    for team_block in stats_resp:
        tid = team_block.get("team", {}).get("id")
        if not tid:
            continue
        team_data = {"name": team_block["team"]["name"]}
        for stat in team_block.get("statistics", []):
            k = stat.get("type", "").lower().replace(" ", "_").replace("%", "pct")
            v = stat.get("value")
            if isinstance(v, str) and v.endswith("%"):
                try:
                    v = int(v.rstrip("%"))
                except ValueError:
                    pass
            team_data[k] = v
        # Map common stat names to consistent keys for the HTML to read
        team_data["shots"]       = team_data.get("total_shots") or team_data.get("shots") or 0
        team_data["shotsOnGoal"] = team_data.get("shots_on_goal") or 0
        team_data["corners"]     = team_data.get("corner_kicks") or 0
        team_data["fouls"]       = team_data.get("fouls") or 0
        team_data["yellowCards"] = team_data.get("yellow_cards") or 0
        team_data["redCards"]    = team_data.get("red_cards") or 0
        team_data["possession"]  = team_data.get("ball_possession") or 0
        out[str(tid)] = team_data
    return out


def main():
    teams = parse_teams()
    print("=" * 60)
    print("JTT WC — Backfill missing teams")
    print("=" * 60)
    print(f"  data dir: {DATA_DIR.resolve()}")
    print(f"  teams:    {teams}")
    print(f"  since:    {SINCE_DATE}")
    print()

    # Load existing data files
    logs_path  = DATA_DIR / "worldcup_intl_logs.json"
    stats_path = DATA_DIR / "worldcup_team_stats.json"
    with logs_path.open()  as f: logs  = json.load(f)
    with stats_path.open() as f: stats = json.load(f)

    print(f"  existing rows:    {len(logs['rows']):,}")
    print(f"  existing matches: {len(stats.get('matches', {}))}")

    # Track which matches we already have so we don't re-process
    have_match_ids = {int(mid) for mid in (stats.get("matches") or {}).keys()}

    # 1) Pull fixtures for each target team
    candidate_fixtures = {}
    for tid, tname in teams.items():
        print(f"\n  Fetching fixtures for {tname} (id={tid})...")
        try:
            fxs = fetch_team_fixtures_since(tid, SINCE_DATE)
            print(f"    found {len(fxs)} completed fixtures since {SINCE_DATE}")
            for f in fxs:
                fid = f["fixture"]["id"]
                if fid not in candidate_fixtures:
                    candidate_fixtures[fid] = f
        except requests.RequestException as e:
            print(f"    ! error fetching: {e}")

    # 2) Filter to NEW fixtures we haven't already processed
    new_fixtures = [f for f in candidate_fixtures.values()
                    if f["fixture"]["id"] not in have_match_ids]
    new_fixtures.sort(key=lambda x: x["fixture"]["date"])
    print(f"\n  {len(new_fixtures)} new fixtures to process "
          f"({len(candidate_fixtures) - len(new_fixtures)} already have stats)")

    if not new_fixtures:
        print("\n  Nothing to do. Exiting.")
        return

    # 3) For each new fixture, pull player + team stats and append
    new_rows = []
    new_team_stats = {}
    teams_seen_in_logs = logs.get("teams", {})
    if not isinstance(teams_seen_in_logs, dict):
        teams_seen_in_logs = {}

    for i, fixture in enumerate(new_fixtures, 1):
        fid = fixture["fixture"]["id"]
        date = fixture["fixture"]["date"][:10]
        h = fixture["teams"]["home"]
        a = fixture["teams"]["away"]
        teams_seen_in_logs[str(h["id"])] = {"name": h["name"]}
        teams_seen_in_logs[str(a["id"])] = {"name": a["name"]}
        league = fixture.get("league", {}).get("name", "?")
        print(f"  [{i}/{len(new_fixtures)}] {date} {h['name']} vs {a['name']} ({league})")
        try:
            players_resp, stats_resp = fetch_fixture_detail(fid)
        except requests.RequestException as e:
            print(f"      ! skipped: {e}")
            continue
        # Player rows for both teams
        for team_block in players_resp:
            tid = team_block.get("team", {}).get("id")
            if not tid:
                continue
            opp_id, opp_name = (a["id"], a["name"]) if tid == h["id"] else (h["id"], h["name"])
            for player_block in team_block.get("players") or []:
                if not player_block.get("statistics"):
                    continue
                mins = (player_block["statistics"][0].get("games") or {}).get("minutes") or 0
                if not mins:
                    continue
                new_rows.append(build_row(fixture, team_block, player_block, opp_id, opp_name))
        # Per-match team stats
        team_stats_block = build_match_team_stats(stats_resp)
        if team_stats_block:
            new_team_stats[str(fid)] = {"teams": team_stats_block}

        # Periodic checkpoint save every 25 fixtures, so a mid-run crash
        # doesn't lose progress.
        if i % 25 == 0:
            _save(logs, stats, new_rows, new_team_stats, teams_seen_in_logs,
                  logs_path, stats_path)
            new_rows, new_team_stats = [], {}

    # 4) Final merge + save
    _save(logs, stats, new_rows, new_team_stats, teams_seen_in_logs,
          logs_path, stats_path)
    # 5) Bump version.txt so the HTML cache-busts the fresh data
    version_ms = int(time.time() * 1000)
    (DATA_DIR / "version.txt").write_text(str(version_ms))
    print(f"\n  total rows now:    {len(logs['rows']):,}")
    print(f"  total matches now: {len(stats.get('matches', {}))}")
    print(f"  version bumped:    {version_ms}")
    print("\nDONE")


def _save(logs, stats, new_rows, new_team_stats, teams_dict, logs_path, stats_path):
    """Merge in-progress batch into the existing JSON files."""
    if new_rows:
        logs["rows"].extend(new_rows)
        # Bump dateTo if any new rows are more recent than current dateTo
        if new_rows:
            latest = max(r["date"] for r in new_rows)
            if latest > logs.get("dateTo", "1970-01-01"):
                logs["dateTo"] = latest
    logs["teams"] = teams_dict
    if new_team_stats:
        stats.setdefault("matches", {}).update(new_team_stats)

    with logs_path.open("w")  as f: json.dump(logs,  f, separators=(",", ":"))
    with stats_path.open("w") as f: json.dump(stats, f, separators=(",", ":"))
    print(f"      [checkpoint saved: +{len(new_rows)} rows, +{len(new_team_stats)} matches]")


if __name__ == "__main__":
    main()
