#!/usr/bin/env python3
"""
fix_missing_scores.py — one-off repair for rows where teamScore/oppScore were
not written. This is the fallout from an earlier version of
backfill_missing_teams.py that omitted those fields.

What it does:
  1. Loads wc/data/worldcup_intl_logs.json
  2. Finds rows where teamScore is absent
  3. Collects the unique matchIds from those rows
  4. Batch-fetches /fixtures?ids=… (20 per call) to get goals + venue
  5. Patches each affected row in-place with:
       teamScore, oppScore, result, homeAway, venue
  6. Saves the file and bumps version.txt for cache busting

Idempotent: re-runs that find no missing rows exit cleanly with no commits.

Usage:
  export APIFOOTBALL_KEY=your_key
  python fix_missing_scores.py
  # or with custom data dir:
  python fix_missing_scores.py --data=wc/data
"""
import json
import os
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("ERROR: pip install requests")

BASE = "https://v3.football.api-sports.io"
BATCH_SIZE = 20  # API-Football allows up to 20 ids per /fixtures call


def get_arg(name, default=None):
    pre = f"--{name}="
    for a in sys.argv[1:]:
        if a.startswith(pre):
            return a[len(pre):]
    return default


def get_key():
    k = get_arg("key") or os.environ.get("APIFOOTBALL_KEY")
    if not k:
        sys.exit("ERROR: APIFOOTBALL_KEY env var or --key=XXX required")
    return k


API_KEY  = get_key()
DATA_DIR = Path(get_arg("data", "./wc/data"))


def api_get(path, params=None):
    """Wrapper with rate limiting."""
    time.sleep(0.15)  # under 10 req/sec
    r = requests.get(BASE + path, params=params or {},
                     headers={"x-apisports-key": API_KEY}, timeout=30)
    r.raise_for_status()
    return r.json()


def main():
    logs_path = DATA_DIR / "worldcup_intl_logs.json"
    if not logs_path.exists():
        sys.exit(f"ERROR: {logs_path} not found")

    print("=" * 60)
    print("JTT WC — fix missing scores")
    print("=" * 60)
    print(f"  data dir: {DATA_DIR.resolve()}")

    with logs_path.open() as f:
        logs = json.load(f)
    rows = logs["rows"]
    total = len(rows)
    print(f"  total rows: {total:,}")

    # Find rows missing teamScore. We treat ABSENT key as the marker — None is
    # also broken but might exist for unfinished fixtures, which we leave alone.
    broken = [r for r in rows if "teamScore" not in r]
    print(f"  rows missing teamScore: {len(broken):,}")
    if not broken:
        print("  Nothing to fix. Exiting.")
        return

    # Collect unique matchIds to fetch
    match_ids = sorted({r["matchId"] for r in broken})
    print(f"  unique matches to fetch: {len(match_ids)}")

    # Batch-fetch fixtures
    match_data = {}  # matchId → {homeId, homeGoals, awayId, awayGoals, venue}
    for i in range(0, len(match_ids), BATCH_SIZE):
        batch = match_ids[i:i + BATCH_SIZE]
        ids_param = "-".join(str(m) for m in batch)
        print(f"  [{i//BATCH_SIZE + 1}/{(len(match_ids) + BATCH_SIZE - 1)//BATCH_SIZE}] "
              f"fetching {len(batch)} fixtures...")
        try:
            resp = api_get("/fixtures", {"ids": ids_param})
        except requests.RequestException as e:
            print(f"      ! batch failed: {e}")
            continue
        for f in resp.get("response") or []:
            fid = f["fixture"]["id"]
            teams = f["teams"]
            goals = f.get("goals") or {}
            match_data[fid] = {
                "homeId":     teams["home"]["id"],
                "awayId":     teams["away"]["id"],
                "homeGoals":  goals.get("home"),
                "awayGoals":  goals.get("away"),
                "venue":      (f["fixture"].get("venue") or {}).get("name"),
            }

    print(f"  fixtures resolved: {len(match_data)} / {len(match_ids)}")
    missing_fixtures = [m for m in match_ids if m not in match_data]
    if missing_fixtures:
        print(f"  ! {len(missing_fixtures)} fixtures couldn't be resolved "
              f"(maybe deleted from API): {missing_fixtures[:5]}...")

    # Patch rows
    patched = 0
    unpatched = 0
    for r in broken:
        md = match_data.get(r["matchId"])
        if not md:
            unpatched += 1
            continue
        is_home    = (r["teamId"] == md["homeId"])
        team_score = md["homeGoals"] if is_home else md["awayGoals"]
        opp_score  = md["awayGoals"] if is_home else md["homeGoals"]
        if team_score is None or opp_score is None:
            # Fixture exists but no score (postponed / cancelled / unplayed)
            unpatched += 1
            continue
        if team_score > opp_score:   result = "W"
        elif team_score < opp_score: result = "L"
        else:                        result = "D"
        r["teamScore"] = int(team_score)
        r["oppScore"]  = int(opp_score)
        r["result"]    = result
        r["homeAway"]  = "H" if is_home else "A"
        if md.get("venue") and "venue" not in r:
            r["venue"] = md["venue"]
        patched += 1

    print(f"\n  patched:   {patched:,} rows")
    print(f"  unpatched: {unpatched:,} rows (no fixture data or fixture has no score)")

    if not patched:
        print("\n  No rows updated. Exiting.")
        return

    # Save
    with logs_path.open("w") as f:
        json.dump(logs, f, separators=(",", ":"))
    print(f"  wrote: {logs_path}")

    # Bump version.txt so the HTML cache-busts
    version_ms = int(time.time() * 1000)
    (DATA_DIR / "version.txt").write_text(str(version_ms))
    print(f"  bumped version: {version_ms}")
    print("\nDONE")


if __name__ == "__main__":
    main()
