#!/usr/bin/env python3
"""
fetch_wc_referees.py — pre-match referee fetcher.

Refs are appointed 1-3 days before kickoff for WC fixtures. The HTML
expects them in wc/data/wc_referees.json keyed by matchId. There was no
pre-match fetcher previously (only backfill_referees.py which runs on
completed matches), so refs only ever appeared post-game.

This script:
  1. Reads wc/data/worldcup_fixtures_2026.json.
  2. Filters to upcoming fixtures in the next 7 days, status NS/TBD.
  3. Calls /fixtures?id=X for each and extracts the referee field.
  4. Merges into wc/data/wc_referees.json (PRESERVING refStats built
     by backfill_referees.py — only byMatch is updated).
  5. Bumps wc/data/version.txt.

USAGE (typically via GitHub Action cron):
    export APIFOOTBALL_KEY=your_key
    python fetch_wc_referees.py
"""

import os, sys, json, time, requests
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://v3.football.api-sports.io"
WINDOW_DAYS = 7  # look ahead 7 days for ref appointments
DATA_DIR = Path("wc/data")
CACHE_DIR = Path(".cache/wc-referees")
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
    candidates = [
        DATA_DIR / "worldcup_fixtures_2026.json",
        Path("worldcup_fixtures_2026.json"),
    ]
    for p in candidates:
        if p.exists():
            return p
    sys.exit(f"ERROR: fixtures file not found in: {[str(p) for p in candidates]}")


def extract_referee(fixture_response):
    """API-Football returns the referee as a string under fixture.referee.
    Typical formats: 'John Smith, England' or just 'John Smith'.
    Returns just the name (the bit before the comma) to match the HTML's
    refRow.name lookup against refStats."""
    if not fixture_response:
        return None
    resp = fixture_response.get("response") or []
    if not resp:
        return None
    fx = resp[0].get("fixture") or {}
    ref = fx.get("referee")
    if not ref or not isinstance(ref, str):
        return None
    return ref.split(",")[0].strip()


def main():
    key = get_key()
    window_days = int(get_arg("window", str(WINDOW_DAYS)))
    print(f"Fetching referees for fixtures in next {window_days} days...")

    fixtures_path = find_fixtures_path()
    print(f"  reading fixtures from: {fixtures_path}")

    with fixtures_path.open() as f:
        fixtures_data = json.load(f)

    fixtures = fixtures_data.get("fixtures") or []
    now = int(time.time())
    horizon = now + window_days * 86400
    upcoming = [f for f in fixtures
                if now <= (f.get("timestamp") or 0) <= horizon
                and f.get("status") in ("NS", "TBD")]

    print(f"Found {len(upcoming)} upcoming fixtures")
    if not upcoming:
        print("Nothing to fetch. Exiting cleanly.")
        return

    # Load existing referees file (preserve refStats built by backfill_referees.py)
    out_path = DATA_DIR / "wc_referees.json"
    if out_path.exists():
        with out_path.open() as f:
            existing = json.load(f)
    else:
        existing = {}
    by_match = existing.get("byMatch", {}) or {}
    ref_stats = existing.get("refStats", {}) or {}

    updated = 0
    skipped = 0
    for fx in upcoming:
        mid = fx["matchId"]
        mid_key = str(mid)

        # Skip if we already have a ref for this match (refs change rarely
        # once appointed; if you need to force a refresh, delete the entry
        # from wc_referees.json and re-run).
        if mid_key in by_match and by_match[mid_key].get("name"):
            skipped += 1
            continue

        # Cache by (matchId, day) — refs don't change intra-day
        day_stamp = datetime.now().strftime("%Y%m%d")
        cache_path = CACHE_DIR / f"ref_{mid}_{day_stamp}.json"

        if cache_path.exists():
            with cache_path.open() as f:
                data = json.load(f)
        else:
            try:
                r = requests.get(BASE + "/fixtures",
                                 params={"id": mid},
                                 headers={"x-apisports-key": key}, timeout=30)
                r.raise_for_status()
                data = r.json()
            except requests.RequestException as e:
                print(f"  match {mid} — HTTP error: {e}")
                continue
            with cache_path.open("w") as f:
                json.dump(data, f)

        ref_name = extract_referee(data)
        if ref_name:
            by_match[mid_key] = {"name": ref_name}
            updated += 1
            print(f"  ✓ {fx.get('homeTeam')} vs {fx.get('awayTeam')} — referee: {ref_name}")
        else:
            print(f"  · {fx.get('homeTeam')} vs {fx.get('awayTeam')} — not yet appointed")

    print(f"\nFetched {updated} new ref appointments. ({skipped} already known.)")

    if updated > 0:
        version_ms = int(time.time() * 1000)
        output = {
            "version":   version_ms,
            "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "byMatch":   by_match,
            "refStats":  ref_stats,  # preserved as-is
        }
        with out_path.open("w") as f:
            json.dump(output, f, separators=(",", ":"))

        # Bump version.txt so the HTML cache-busts the new file
        version_path = DATA_DIR / "version.txt"
        with version_path.open("w") as f:
            f.write(str(version_ms))

        print(f"Saved {out_path} and bumped version.")


if __name__ == "__main__":
    main()
