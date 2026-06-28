#!/usr/bin/env python3
"""
backfill_wc_knockout.py — additive merge of missing WC fixtures.

WHY THIS EXISTS:
  worldcup_fixtures_2026.json was originally populated with the 72 group
  stage matches. The daily fetcher (fetch_wc_update.py) only UPDATES
  existing fixtures' status — it never ADDS new ones. Result: R32 / QF /
  SF / Final fixtures never appeared in the schedule, even after FIFA
  drew them.

WHAT IT DOES:
  1. Pulls all 104 WC 2026 fixtures from API-Football (league=1, season=2026)
  2. Compares matchIds with the local worldcup_fixtures_2026.json
  3. Appends any missing fixtures in the existing JSON shape
  4. NEVER modifies existing entries — status/scores are preserved
  5. Writes back if anything changed

USAGE:
    export APIFOOTBALL_KEY=your_key
    python backfill_wc_knockout.py

Cost: 1 API call.
"""

import os, sys, json, requests
from datetime import datetime
from pathlib import Path

BASE = "https://v3.football.api-sports.io"
DATA_DIR = Path("wc/data")
FIXTURES_PATH = DATA_DIR / "worldcup_fixtures_2026.json"
LEAGUE_ID = 1
SEASON = 2026


def get_key():
    k = os.environ.get("APIFOOTBALL_KEY")
    if not k:
        sys.exit("ERROR: Set APIFOOTBALL_KEY env var")
    return k


def main():
    print("=" * 60)
    print("WC 2026 Knockout Fixture Backfill")
    print("=" * 60)

    if not FIXTURES_PATH.exists():
        sys.exit(f"ERROR: {FIXTURES_PATH} not found. Run fetch_wc_fixtures.py first.")

    existing = json.loads(FIXTURES_PATH.read_text())
    existing_fixtures = existing.get("fixtures", [])
    existing_ids = {int(f["matchId"]) for f in existing_fixtures}
    print(f"   loaded: {len(existing_fixtures)} existing fixtures")

    # Fetch the full WC 2026 schedule from API-Football
    print(f"   fetching league={LEAGUE_ID} season={SEASON}…")
    r = requests.get(
        f"{BASE}/fixtures",
        headers={"x-apisports-key": get_key()},
        params={"league": LEAGUE_ID, "season": SEASON},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    api_fixtures = data.get("response", [])
    print(f"   API returned: {len(api_fixtures)} fixtures")

    # Build new entries for any fixtures missing locally
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

    if not new_entries:
        print("\n   nothing new — local file is up to date.")
        print("DONE")
        return

    # Group by round for the log
    from collections import Counter
    rounds = Counter(e["round"] for e in new_entries)
    print(f"\n   {len(new_entries)} new fixtures to add:")
    for r_name, cnt in sorted(rounds.items()):
        print(f"     · {r_name}: +{cnt}")

    # Merge — additive, preserving existing entries
    merged = existing_fixtures + new_entries
    merged.sort(key=lambda f: f.get("timestamp") or 0)

    existing["fixtures"] = merged
    existing["fixtureCount"] = len(merged)
    existing["lastBackfillAt"] = datetime.utcnow().isoformat() + "Z"

    FIXTURES_PATH.write_text(json.dumps(existing, separators=(",", ":")))
    print(f"\n   saved: {len(merged)} total fixtures to {FIXTURES_PATH}")

    # Bump version.txt so subscribers' browsers see the change
    version_path = DATA_DIR / "version.txt"
    version = int(datetime.utcnow().timestamp() * 1000)
    version_path.write_text(str(version))
    print(f"   version: {version}")

    print("\nDONE")


if __name__ == "__main__":
    main()
