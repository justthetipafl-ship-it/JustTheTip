#!/usr/bin/env python3
"""
Fetch WC 2026 squad lists for every team that appears in the fixtures file.

Reads:  wc/data/worldcup_fixtures_2026.json
Writes: wc/data/worldcup_squads_2026.json

Output shape (keyed by string teamId):
{
  "20":  [{"id": 12345, "name": "Raul Jimenez", "position": "F", "number": 9, ...}, ...],
  "22":  [...],
  ...
}

The output is consumed by the WC tool's eligibility guard:
  state.data.squads[teamId]  →  Set of player IDs that may surface as signals.

API source: API-Football v3 /players/squads endpoint. One call per team
(~48 calls total for the WC). Resume-safe — if the output file already has
entries for some teams they are skipped, so re-running after a partial
failure only fetches the missing teams.

Usage (locally or in a GitHub Action):
    API_FOOTBALL_KEY=xxxxxxxxxxxxxxx python fetch_wc_squads.py

To force a full refresh (e.g. once squads are officially named), pass --force:
    python fetch_wc_squads.py --force
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


API_KEY  = os.environ.get("API_FOOTBALL_KEY")
API_BASE = "https://v3.football.api-sports.io"

FIXTURES_PATH = Path("wc/data/worldcup_fixtures_2026.json")
OUTPUT_PATH   = Path("wc/data/worldcup_squads_2026.json")

# Gentle rate-limit between API calls.
SLEEP_BETWEEN_CALLS = 0.3


def die(msg, code=1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def api_get(path, params=None):
    """GET an API-Football endpoint. Returns parsed JSON."""
    url = f"{API_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "x-apisports-key": API_KEY,
        "Accept":          "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def collect_team_ids(fixtures_data):
    """
    Extract unique team IDs + names from the fixtures JSON.
    Handles several common shapes the fetch_wc_events.py output might use.
    """
    if isinstance(fixtures_data, list):
        fixtures = fixtures_data
    elif isinstance(fixtures_data, dict):
        fixtures = (
            fixtures_data.get("fixtures")
            or fixtures_data.get("matches")
            or fixtures_data.get("response")
            or []
        )
    else:
        return {}

    teams = {}  # tid -> name

    for fx in fixtures:
        if not isinstance(fx, dict):
            continue

        # API-Football nested shape: {"teams": {"home": {"id":..., "name":...}, "away": {...}}}
        ftxteams = fx.get("teams")
        if isinstance(ftxteams, dict):
            for side in ("home", "away"):
                t = ftxteams.get(side)
                if isinstance(t, dict) and t.get("id") is not None:
                    teams[t["id"]] = t.get("name", "")

        # Flat shapes commonly seen in our data
        for id_key, name_key in (
            ("homeTeamId", "homeTeam"),
            ("awayTeamId", "awayTeam"),
            ("home_id",    "home_team"),
            ("away_id",    "away_team"),
            ("homeId",     "homeName"),
            ("awayId",     "awayName"),
        ):
            tid = fx.get(id_key)
            if tid is not None:
                teams.setdefault(tid, fx.get(name_key) or teams.get(tid, ""))

    return teams


def fetch_squad(team_id):
    """
    Fetch a team's current squad. Returns a list of normalised player dicts.
    Each player has at minimum {id, name}.
    """
    data = api_get("/players/squads", {"team": team_id})
    response = data.get("response", [])
    if not response:
        return []

    # The endpoint returns a list of {"team": {...}, "players": [...]} entries.
    # For a team query there's typically one entry.
    players_in = response[0].get("players", []) or []

    out = []
    for p in players_in:
        pid = p.get("id")
        if pid is None:
            continue
        # Normalise position to single letter where possible (matches the rest
        # of the JTT WC pipeline — F / M / D / G).
        raw_pos = (p.get("position") or "").strip().lower()
        if raw_pos.startswith("attack") or raw_pos.startswith("forward"):
            pos = "F"
        elif raw_pos.startswith("midfield"):
            pos = "M"
        elif raw_pos.startswith("defen"):
            pos = "D"
        elif raw_pos.startswith("goal"):
            pos = "G"
        else:
            pos = p.get("position", "")

        out.append({
            "id":       pid,
            "name":     p.get("name", ""),
            "position": pos,
            "number":   p.get("number"),
            "age":      p.get("age"),
            "photo":    p.get("photo", ""),
        })
    return out


def main():
    parser = argparse.ArgumentParser(description="Fetch WC 2026 squads.")
    parser.add_argument("--force", action="store_true",
                        help="Refetch every team even if already in output file.")
    args = parser.parse_args()

    if not API_KEY:
        die("API_FOOTBALL_KEY env var is required.")

    if not FIXTURES_PATH.exists():
        die(f"Fixtures file not found at {FIXTURES_PATH}. "
            f"Run fetch_wc_events.py first.")

    with FIXTURES_PATH.open() as f:
        fixtures_data = json.load(f)

    teams = collect_team_ids(fixtures_data)
    if not teams:
        die(f"No team IDs found in {FIXTURES_PATH}.")

    print(f"Found {len(teams)} unique teams in fixtures.")

    # Load existing squads file (for resume) unless --force.
    existing = {}
    if OUTPUT_PATH.exists() and not args.force:
        try:
            with OUTPUT_PATH.open() as f:
                existing = json.load(f)
            if not isinstance(existing, dict):
                existing = {}
        except Exception:
            existing = {}

    squads = dict(existing)
    fetched = 0
    skipped = 0
    failed  = 0

    for tid in sorted(teams.keys()):
        tname  = teams[tid] or "?"
        tidstr = str(tid)

        if not args.force and tidstr in squads and squads[tidstr]:
            print(f"  [skip] {tname} (id={tid}) — {len(squads[tidstr])} players already cached")
            skipped += 1
            continue

        try:
            print(f"  [fetch] {tname} (id={tid}) …", end=" ", flush=True)
            players = fetch_squad(tid)
            squads[tidstr] = players
            print(f"{len(players)} players")
            fetched += 1
            time.sleep(SLEEP_BETWEEN_CALLS)
        except urllib.error.HTTPError as e:
            print(f"HTTP {e.code}")
            failed += 1
        except Exception as e:
            print(f"FAILED ({type(e).__name__}: {e})")
            failed += 1

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w") as f:
        json.dump(squads, f, ensure_ascii=False, indent=2, sort_keys=True)

    total_players = sum(len(v) for v in squads.values() if isinstance(v, list))
    print()
    print(f"Wrote {OUTPUT_PATH}")
    print(f"  teams:    {len(squads)}")
    print(f"  players:  {total_players}")
    print(f"  fetched:  {fetched}")
    print(f"  skipped:  {skipped}")
    print(f"  failed:   {failed}")


if __name__ == "__main__":
    main()
