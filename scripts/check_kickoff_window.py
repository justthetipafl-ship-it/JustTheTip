#!/usr/bin/env python3
"""
Gate script for pre-match data refresh.

Exits 0 if any WC fixture's kickoff is 50-65 mins from now.
Exits 1 otherwise.

Used by .github/workflows/wc-prematch-refresh.yml to decide whether
to run the fetchers. The 50-65 min window targets lineup announcement
time (typically T-60 to T-75 min for WC) while absorbing GitHub Actions
cron jitter (scheduled runs can be 5-15 min late).
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Try common fixtures-file paths. The first one that exists is used.
# Add your actual path here if none of these match.
FIXTURES_PATHS = [
    Path("wc/data/worldcup_fixtures_2026.json"),
]

WINDOW_START_MIN = 30
WINDOW_END_MIN   = 120


def load_fixtures():
    for p in FIXTURES_PATHS:
        if p.exists():
            with p.open() as f:
                data = json.load(f)
            return data, p
    return None, None


def extract_kickoff(fx):
    """Try common keys for kickoff timestamp. Returns ISO string or None."""
    for key in ("commence_time", "kickoff", "date", "datetime", "utcDate"):
        v = fx.get(key)
        if v:
            return v
    # API-Football style nested shape
    nested = fx.get("fixture") or {}
    return nested.get("date")


def extract_team_names(fx):
    """Try common shapes for home/away team names. Returns (home, away)."""
    home = fx.get("home_team") or fx.get("home") or fx.get("homeTeam")
    away = fx.get("away_team") or fx.get("away") or fx.get("awayTeam")
    if home and away:
        return home, away
    teams = fx.get("teams") or {}
    h = teams.get("home") or {}
    a = teams.get("away") or {}
    return (h.get("name") if isinstance(h, dict) else h,
            a.get("name") if isinstance(a, dict) else a)


def main():
    data, src = load_fixtures()
    if data is None:
        print(f"ERROR: no fixtures file found. Tried: {[str(p) for p in FIXTURES_PATHS]}",
              file=sys.stderr)
        sys.exit(1)

    # Normalise to a list of fixture dicts.
    if isinstance(data, list):
        fixtures = data
    elif isinstance(data, dict):
        fixtures = (data.get("fixtures") or data.get("matches")
                    or data.get("response") or data.get("byFixture") or [])
        if isinstance(fixtures, dict):
            # If keyed by id, just take the values
            fixtures = list(fixtures.values())
    else:
        fixtures = []

    now = datetime.now(timezone.utc)
    window_start = now + timedelta(minutes=WINDOW_START_MIN)
    window_end   = now + timedelta(minutes=WINDOW_END_MIN)

    matching = []
    for fx in fixtures:
        if not isinstance(fx, dict):
            continue
        ts = extract_kickoff(fx)
        if not ts:
            continue
        try:
            kickoff = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except ValueError:
            continue
        if window_start <= kickoff <= window_end:
            matching.append((fx, kickoff))

    gh_output = os.environ.get("GITHUB_OUTPUT")
    if matching:
        print(f"REFRESH NEEDED (source: {src}) — {len(matching)} fixture(s) in T-{WINDOW_START_MIN}..{WINDOW_END_MIN} window:")
        for fx, ko in matching:
            home, away = extract_team_names(fx)
            mins = int((ko - now).total_seconds() / 60)
            print(f"  - {home} vs {away}  (kickoff in {mins} min)")
        if gh_output:
            with open(gh_output, "a") as f:
                f.write("refresh=true\n")
        sys.exit(0)

    print(f"No fixtures in T-{WINDOW_START_MIN}..{WINDOW_END_MIN} window (source: {src}). Skipping refresh.")
    if gh_output:
        with open(gh_output, "a") as f:
            f.write("refresh=false\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
