#!/usr/bin/env python3
"""
fetch_wc_lineups.py — matchday lineup poller. Runs every ~10-15 minutes during
matchday windows to catch lineup announcements (~60-90 mins before kick-off).

WHAT IT DOES:
  1. Reads wc/data/worldcup_fixtures_2026.json (the WC tool's fixtures file).
  2. Filters to fixtures starting in the next N hours (so we catch lineups
     as soon as the API has them, but don't waste calls on next-week games).
  3. For each, calls /fixtures/lineups?fixture=X.
  4. Merges results into wc/data/wc_lineups.json (preserving historical data).
  5. Bumps wc/data/version.txt for cache busting.

CALL BUDGET:
  Per matchday window, ~8 fixtures × ~9 polls (every 10 mins over ~90 mins)
  = ~72 calls per matchday. Daily quota is 7500, no concern.

USAGE (typically via GitHub Action cron):
    export APIFOOTBALL_KEY=your_key
    python fetch_wc_lineups.py
"""

import os, sys, json, time, requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = "https://v3.football.api-sports.io"
WINDOW_HOURS = 6  # how far ahead to poll for lineups
DATA_DIR = Path("wc/data")             # where the WC tool reads its JSON
CACHE_DIR = Path(".cache/wc-lineups")  # outside wc/ so it isn't committed
CACHE_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)


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


def find_fixtures_path():
    """Canonical path is wc/data/worldcup_fixtures_2026.json. Falls back to
    root for backward compatibility with older deployments."""
    candidates = [
        DATA_DIR / "worldcup_fixtures_2026.json",
        Path("worldcup_fixtures_2026.json"),
    ]
    for p in candidates:
        if p.exists():
            return p
    sys.exit(f"ERROR: fixtures file not found in: {[str(p) for p in candidates]}")


def parse_lineup_response(data):
    """Identical to backfill_lineups.py — kept duplicated so this script is
    self-contained for the matchday cron."""
    teams = data.get("response", []) or []
    if not teams:
        return None
    out = {}
    for t in teams:
        team_info = t.get("team", {}) or {}
        team_id = team_info.get("id")
        if not team_id:
            continue

        def _flatten(entries):
            arr = []
            for e in (entries or []):
                p = e.get("player") or {}
                arr.append({
                    "id":     p.get("id"),
                    "name":   p.get("name"),
                    "number": p.get("number"),
                    "pos":    p.get("pos"),
                    "grid":   p.get("grid"),
                })
            return arr

        coach = t.get("coach") or {}
        out[str(team_id)] = {
            "teamId":      team_id,
            "teamName":    team_info.get("name"),
            "formation":   t.get("formation"),
            "coach":       coach.get("name"),
            "startXI":     _flatten(t.get("startXI")),
            "substitutes": _flatten(t.get("substitutes")),
        }
    return out if out else None


def main():
    key = get_key()
    window_hours = int(get_arg("window", str(WINDOW_HOURS)))
    print(f"Polling lineups for fixtures starting in next {window_hours} hours...")

    fixtures_path = find_fixtures_path()
    print(f"  reading fixtures from: {fixtures_path}")

    with fixtures_path.open() as f:
        fixtures_data = json.load(f)

    fixtures = fixtures_data.get("fixtures") or []
    now = int(time.time())
    horizon = now + window_hours * 3600
    upcoming = [f for f in fixtures
                if now <= (f.get("timestamp") or 0) <= horizon
                and f.get("status") in ("NS", "TBD")]

    print(f"Found {len(upcoming)} fixtures in window")
    if not upcoming:
        print("Nothing to poll. Exiting cleanly.")
        return

    # Load existing lineups file (we MERGE into it, not overwrite)
    out_path = DATA_DIR / "wc_lineups.json"
    if out_path.exists():
        with out_path.open() as f:
            existing = json.load(f)
        by_match = existing.get("byMatch", {})
    else:
        by_match = {}

    updated = 0
    for fx in upcoming:
        mid = fx["matchId"]
        hour_stamp = datetime.now().strftime("%Y%m%d_%H")
        cache_path = CACHE_DIR / f"matchday_{mid}_{hour_stamp}.json"

        if cache_path.exists():
            with cache_path.open() as f:
                data = json.load(f)
        else:
            try:
                r = requests.get(BASE + "/fixtures/lineups",
                                 params={"fixture": mid},
                                 headers={"x-apisports-key": key}, timeout=30)
                r.raise_for_status()
                data = r.json()
            except requests.RequestException as e:
                print(f"  match {mid} — HTTP error: {e}")
                continue
            with cache_path.open("w") as f:
                json.dump(data, f)

        parsed = parse_lineup_response(data)
        if parsed:
            by_match[str(mid)] = parsed
            updated += 1
            print(f"  ✓ {fx.get('homeTeam')} vs {fx.get('awayTeam')} — lineups available")
        else:
            print(f"  · {fx.get('homeTeam')} vs {fx.get('awayTeam')} — not yet")

    if updated > 0:
        version_ms = int(time.time() * 1000)
        output = {
            "version":   version_ms,
            "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "byMatch":   by_match,
        }
        with out_path.open("w") as f:
            json.dump(output, f, separators=(",", ":"))

        # Bump version.txt so the HTML cache-busts the new file
        version_path = DATA_DIR / "version.txt"
        with version_path.open("w") as f:
            f.write(str(version_ms))

        print(f"\nUpdated {updated} fixtures with lineups. Saved {out_path}.")
    else:
        print("\nNo new lineups available this poll.")


if __name__ == "__main__":
    main()
