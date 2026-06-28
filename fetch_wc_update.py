#!/usr/bin/env python3
"""
fetch_wc_update.py
==================
Daily incremental updater for the JTT WC 2026 dataset.

Run this each day during the tournament (or every 2-3 days during friendlies).
It pulls only NEW completed matches since your last run, merges them into the
existing JSON files, and writes the updates.

What it updates:
  worldcup_intl_logs.json   — adds new player rows
  worldcup_team_stats.json  — adds new match team stats
  worldcup_fixtures_2026.json — flips fixture status NS → FT, adds scores
  version.txt               — bumps the timestamp so the tool busts browser cache

What it does NOT touch:
  team_ratings.json — only changes when FIFA publishes new rankings (monthly-ish)

Usage on Windows:
    python fetch_wc_update.py --key=your-api-football-key --data=wc\\data

Or via env var (Cmd Prompt):
    set APIFOOTBALL_KEY=your-key
    python fetch_wc_update.py --data=wc\\data

The script is idempotent. Running it twice is free (cached responses).
"""

import json
import os
import sys
import time
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: pip install requests")
    sys.exit(1)


# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

API_KEY = os.environ.get("APIFOOTBALL_KEY")
DATA_DIR = Path("./wc/data")  # default, override with --data=

for arg in sys.argv[1:]:
    if arg.startswith("--key="):
        API_KEY = arg.split("=", 1)[1].strip().strip('"').strip("'")
    elif arg.startswith("--data="):
        DATA_DIR = Path(arg.split("=", 1)[1].strip())

if not API_KEY:
    print("ERROR: API-Football key required.")
    print("  Pass --key=YOUR_KEY  OR  set APIFOOTBALL_KEY=YOUR_KEY")
    sys.exit(1)

BASE = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}
SEASON = 2026
SLEEP_BETWEEN_CALLS = 0.3

DATA_DIR.mkdir(parents=True, exist_ok=True)
CACHE = DATA_DIR / ".cache"
CACHE.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# HTTP with response cache
# ---------------------------------------------------------------------------

def _cache_key(endpoint, params):
    qs = urllib.parse.urlencode(sorted(params.items()))
    safe = endpoint.replace("/", "_") + "__" + qs
    safe = safe.replace("?", "").replace("&", "_").replace("=", "-")
    return CACHE / (safe[:180] + ".json")


def api(endpoint, params, force=False):
    cf = _cache_key(endpoint, params)
    if cf.exists() and not force:
        return json.loads(cf.read_text())
    r = requests.get(f"{BASE}{endpoint}", headers=HEADERS, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("errors"):
        print(f"   API errors for {endpoint}: {data['errors']}")
    cf.write_text(json.dumps(data))
    time.sleep(SLEEP_BETWEEN_CALLS)
    return data


# ---------------------------------------------------------------------------
# Schema converters (same as fetch_ucl_data.py)
# ---------------------------------------------------------------------------

def competition_tier(comp_name):
    if not comp_name:
        return "league"
    n = comp_name.lower()
    if "friendly" in n or "pre-season" in n:
        return "friendly"
    if "world cup" in n and "qual" not in n:
        return "world_cup"
    if "qualif" in n:
        return "qualifying"
    if any(s in n for s in ["uefa", "copa", "afcon", "africa cup", "asian cup",
                             "concacaf", "gold cup", "nations league", "euro"]):
        return "tournament"
    if "cup" in n:
        return "cup"
    return "competitive"


def map_position(pos):
    if not pos:
        return None
    p = pos.upper().strip()
    if p.startswith("G"): return "G"
    if p.startswith("D"): return "D"
    if p.startswith("M"): return "M"
    if p.startswith("F") or p.startswith("A"): return "F"
    return None


def safe_int(v, default=0):
    if v is None or v == "":
        return default
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


def safe_float(v, default=None):
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def determine_result(team_score, opp_score):
    if team_score is None or opp_score is None:
        return None
    if team_score > opp_score: return "W"
    if team_score < opp_score: return "L"
    return "D"


STAT_TYPE_MAP = {
    "Ball Possession":   ("possession", lambda v: safe_int(str(v).replace("%", ""))),
    "Total Shots":       ("shotsTotal", safe_int),
    "Shots on Goal":     ("shotsOn", safe_int),
    "Shots off Goal":    ("shotsOff", safe_int),
    "Shots insidebox":   ("shotsInsideBox", safe_int),
    "Shots outsidebox":  ("shotsOutsideBox", safe_int),
    "Corner Kicks":      ("corners", safe_int),
    "Offsides":          ("offsides", safe_int),
    "Fouls":             ("fouls", safe_int),
    "Yellow Cards":      ("yellowCards", safe_int),
    "Red Cards":         ("redCards", safe_int),
    "Goalkeeper Saves":  ("saves", safe_int),
    "Total passes":      ("passesTotal", safe_int),
    "Passes accurate":   ("passesAccurate", safe_int),
    "Passes %":          ("passAccuracy", lambda v: safe_int(str(v).replace("%", ""))),
    "expected_goals":    ("xg", safe_float),
}


def build_row(fixture, team_block, player_block, opp_team_id, opp_team_name):
    fxt = fixture["fixture"]
    league = fixture["league"]
    teams = fixture["teams"]
    goals = fixture["goals"]
    team = team_block["team"]
    player = player_block["player"]
    stats = player_block["statistics"][0] if player_block["statistics"] else {}

    games = stats.get("games", {})
    shots = stats.get("shots", {})
    pgoals = stats.get("goals", {})
    passes = stats.get("passes", {})
    tackles = stats.get("tackles", {})
    duels = stats.get("duels", {})
    fouls = stats.get("fouls", {})
    cards = stats.get("cards", {})
    penalty = stats.get("penalty", {})

    is_home = teams["home"]["id"] == team["id"]
    team_score = goals.get("home") if is_home else goals.get("away")
    opp_score = goals.get("away") if is_home else goals.get("home")
    home_away = "H" if is_home else "A"

    return {
        "matchId": fxt["id"],
        "date": fxt["date"][:10],
        "competition": league.get("name"),
        "competitionTier": competition_tier(league.get("name")),
        "team": team["name"],
        "teamId": team["id"],
        "opponent": opp_team_name,
        "opponentId": opp_team_id,
        "venue": (fxt.get("venue") or {}).get("name"),
        "homeAway": home_away,
        "result": determine_result(team_score, opp_score),
        "teamScore": safe_int(team_score),
        "oppScore": safe_int(opp_score),
        "playerId": player["id"],
        "playerName": player["name"],
        "position": map_position(games.get("position")),
        "shirtNumber": safe_int(games.get("number"), 0),
        "minutes": safe_int(games.get("minutes"), 0),
        "rating": safe_float(games.get("rating")),
        "started": not games.get("substitute", True),
        "goals": safe_int(pgoals.get("total")),
        "assists": safe_int(pgoals.get("assists")),
        "shots": safe_int(shots.get("total")),
        "shotsOn": safe_int(shots.get("on")),
        "passes": safe_int(passes.get("total")),
        "passAccuracy": safe_int(passes.get("accuracy")),
        "keyPasses": safe_int(passes.get("key")),
        "tackles": safe_int(tackles.get("total")),
        "interceptions": safe_int(tackles.get("interceptions")),
        "duelsTotal": safe_int(duels.get("total")),
        "duelsWon": safe_int(duels.get("won")),
        "foulsCommitted": safe_int(fouls.get("committed")),
        "foulsDrawn": safe_int(fouls.get("drawn")),
        "yellowCard": safe_int(cards.get("yellow")),
        "penaltyScored": safe_int(penalty.get("scored")),
        "penaltyMissed": safe_int(penalty.get("missed")),
    }


def build_match_team_stats(stats_response):
    out = {}
    for team_block in stats_response:
        team_id = team_block["team"]["id"]
        out[team_id] = {}
        for st in team_block.get("statistics", []):
            t = st.get("type")
            v = st.get("value")
            if t in STAT_TYPE_MAP:
                key, conv = STAT_TYPE_MAP[t]
                try:
                    out[team_id][key] = conv(v) if v is not None else None
                except Exception:
                    out[team_id][key] = None
    return out


# ---------------------------------------------------------------------------
# THE UPDATE LOGIC
# ---------------------------------------------------------------------------

def load_existing():
    """Load current data files; return empty skeletons if missing."""
    logs_path = DATA_DIR / "worldcup_intl_logs.json"
    stats_path = DATA_DIR / "worldcup_team_stats.json"
    fixtures_path = DATA_DIR / "worldcup_fixtures_2026.json"

    logs = json.loads(logs_path.read_text()) if logs_path.exists() else {
        "dateFrom": "2022-11-20", "dateTo": "2022-11-20",
        "teams": {}, "rows": [], "matchesProcessed": 0,
    }
    stats = json.loads(stats_path.read_text()) if stats_path.exists() else {"matches": {}}
    fixtures = json.loads(fixtures_path.read_text()) if fixtures_path.exists() else {"fixtures": []}
    return logs, stats, fixtures


def write_updated(logs, stats, fixtures):
    """Write updated data files (minified) and bump version."""
    (DATA_DIR / "worldcup_intl_logs.json").write_text(json.dumps(logs, separators=(",", ":")))
    (DATA_DIR / "worldcup_team_stats.json").write_text(json.dumps(stats, separators=(",", ":")))
    (DATA_DIR / "worldcup_fixtures_2026.json").write_text(json.dumps(fixtures, separators=(",", ":")))
    # version.txt — tool reads this on load to cache-bust the data fetches.
    # Using a millisecond-precision timestamp guarantees a new value every run.
    version = str(int(time.time() * 1000))
    (DATA_DIR / "version.txt").write_text(version)
    print(f"\n   version: {version}")


def fetch_team_fixtures_since(team_id, since_date):
    """Pull all fixtures for a team played from since_date forward."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    return api("/fixtures", {
        "team": team_id, "season": SEASON,
        "from": since_date, "to": today,
    }, force=True)["response"]


def fetch_fixture_detail(fixture_id):
    """Fetch players + team statistics for one fixture."""
    p = api("/fixtures/players", {"fixture": fixture_id})
    s = api("/fixtures/statistics", {"fixture": fixture_id})
    return p["response"], s["response"]


def main():
    print("=" * 60)
    print("JTT WC update — incremental fetch")
    print("=" * 60)
    print(f"   data dir: {DATA_DIR.resolve()}")

    logs, stats, fixtures_data = load_existing()
    print(f"   loaded {len(logs['rows']):,} existing rows · {len(stats['matches'])} matches · {len(fixtures_data['fixtures'])} fixtures")

    # 0. Backfill any missing WC fixtures from the API (knockout rounds get
    #    added by FIFA after group stage / R16 / QF draws complete). This is
    #    additive only — existing entries (including their status/scores) are
    #    never modified. Cost: 1 API call per day.
    try:
        resp = api("/fixtures", {"league": 1, "season": 2026})
        api_fixtures = resp.get("response", [])
        existing_ids = {int(f["matchId"]) for f in fixtures_data["fixtures"]}
        new_entries = []
        for fx in api_fixtures:
            fid = fx["fixture"]["id"]
            if fid in existing_ids:
                continue
            new_entries.append({
                "matchId":     fid,
                "date":        fx["fixture"]["date"],
                "timestamp":   fx["fixture"]["timestamp"],
                "status":      fx["fixture"]["status"]["short"],
                "venue":       (fx["fixture"].get("venue") or {}).get("name"),
                "city":        (fx["fixture"].get("venue") or {}).get("city"),
                "league":      fx["league"]["name"],
                "leagueId":    fx["league"]["id"],
                "season":      fx["league"]["season"],
                "round":       fx["league"]["round"],
                "homeTeam":    fx["teams"]["home"]["name"],
                "homeTeamId":  fx["teams"]["home"]["id"],
                "awayTeam":    fx["teams"]["away"]["name"],
                "awayTeamId":  fx["teams"]["away"]["id"],
                "homeScore":   fx["goals"]["home"],
                "awayScore":   fx["goals"]["away"],
            })
        if new_entries:
            from collections import Counter
            rounds = Counter(e["round"] for e in new_entries)
            print(f"\n   + {len(new_entries)} new fixtures added to schedule:")
            for r_name, cnt in sorted(rounds.items()):
                print(f"     · {r_name}: +{cnt}")
            fixtures_data["fixtures"].extend(new_entries)
            fixtures_data["fixtures"].sort(key=lambda f: f.get("timestamp") or 0)
            fixtures_data["fixtureCount"] = len(fixtures_data["fixtures"])
    except Exception as e:
        print(f"   ! schedule backfill failed (continuing): {e}")

    # Build a set of matchIds we already have so we can skip them
    have_match_ids = {int(mid) for mid in stats["matches"].keys()}
    # And a set of fixtureIds in the WC schedule so we can update their status
    wc_fixture_index = {int(f["matchId"]): f for f in fixtures_data["fixtures"]}

    # Determine fetch window — last 7 days back from dateTo, to catch any late-published stats
    since_date_raw = logs.get("dateTo", "2022-11-20")
    since = (datetime.strptime(since_date_raw, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
    print(f"   fetch window: {since} → today")

    # WC team IDs from the fixtures file (48 teams)
    team_ids = sorted({f["homeTeamId"] for f in fixtures_data["fixtures"]} |
                      {f["awayTeamId"] for f in fixtures_data["fixtures"]})
    print(f"   teams: {len(team_ids)}")

    # 1. Pull fixtures for each team in the window
    candidate_fixtures = {}  # fixtureId → API-Football fixture object
    print(f"\n   scanning team fixtures…")
    for i, tid in enumerate(team_ids):
        try:
            for f in fetch_team_fixtures_since(tid, since):
                fid = f["fixture"]["id"]
                if fid not in candidate_fixtures:
                    candidate_fixtures[fid] = f
                # Also update WC fixture status if this is a tournament fixture we track
                if fid in wc_fixture_index:
                    wcf = wc_fixture_index[fid]
                    status = f["fixture"]["status"]["short"]
                    if status in ("FT", "AET", "PEN") and wcf.get("status") != "FT":
                        wcf["status"] = "FT"
                        wcf["homeScore"] = f["goals"]["home"]
                        wcf["awayScore"] = f["goals"]["away"]
        except Exception as e:
            print(f"   ! team {tid} fixtures fetch failed: {e}")
        if (i + 1) % 10 == 0:
            print(f"      [{i+1}/{len(team_ids)}]")

    # 2. Filter to NEW completed fixtures we haven't processed
    new_fixtures = [
        f for f in candidate_fixtures.values()
        if f["fixture"]["id"] not in have_match_ids
        and f["fixture"]["status"]["short"] in ("FT", "AET", "PEN")
    ]
    new_fixtures.sort(key=lambda x: x["fixture"]["date"])
    print(f"\n   {len(new_fixtures)} new completed fixtures to process")

    if not new_fixtures:
        print("\n   nothing new — writing version file anyway to bust browser cache")
        write_updated(logs, stats, fixtures_data)
        print("\nDONE")
        return

    # 3. For each new fixture, fetch player & team stats, append to data
    new_rows = []
    new_team_stats = {}
    new_dates = []
    # Defensive: older data files had `teams` as a list of codes rather than a
    # dict of {id: {name}}. If it's not already a dict, start fresh — we'll
    # rebuild it from scratch from the fixtures we process, which is fine
    # since the HTML reads team names from the row data, not the teams index.
    existing_teams = logs.get("teams", {})
    teams_seen = dict(existing_teams) if isinstance(existing_teams, dict) else {}

    for i, fixture in enumerate(new_fixtures):
        fid = fixture["fixture"]["id"]
        date = fixture["fixture"]["date"][:10]
        h = fixture["teams"]["home"]
        a = fixture["teams"]["away"]
        new_dates.append(date)
        teams_seen[str(h["id"])] = {"name": h["name"]}
        teams_seen[str(a["id"])] = {"name": a["name"]}
        print(f"   [{i+1}/{len(new_fixtures)}] {date} {h['name']} vs {a['name']}")

        try:
            players_resp, stats_resp = fetch_fixture_detail(fid)
        except Exception as e:
            print(f"      ! skipped: {e}")
            continue

        for team_block in players_resp:
            tid = team_block["team"]["id"]
            opp_id, opp_name = (a["id"], a["name"]) if tid == h["id"] else (h["id"], h["name"])
            for player_block in team_block.get("players", []):
                if not player_block.get("statistics"):
                    continue
                games = player_block["statistics"][0].get("games", {})
                if not games.get("minutes"):
                    continue
                new_rows.append(build_row(fixture, team_block, player_block, opp_id, opp_name))

        team_stats = build_match_team_stats(stats_resp)
        if team_stats:
            new_team_stats[str(fid)] = {"teams": team_stats}

    # 4. Merge into existing data
    logs["rows"].extend(new_rows)
    logs["teams"] = teams_seen
    if new_dates:
        logs["dateTo"] = max(new_dates)
    logs["matchesProcessed"] = logs.get("matchesProcessed", 0) + len(new_team_stats)
    stats["matches"].update(new_team_stats)

    print(f"\n   merged: +{len(new_rows):,} rows · +{len(new_team_stats)} matches")
    print(f"   total now: {len(logs['rows']):,} rows · {len(stats['matches'])} matches")
    print(f"   dateTo: {logs['dateTo']}")

    # 5. Write everything out
    write_updated(logs, stats, fixtures_data)
    print("\nDONE")


if __name__ == "__main__":
    main()
