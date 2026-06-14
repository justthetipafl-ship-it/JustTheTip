#!/usr/bin/env python3
"""
Gate script for pre-match data refresh.

Writes refresh=true|false to $GITHUB_OUTPUT and always exits 0.
Used by .github/workflows/wc-prematch-refresh.yml to decide whether
to run the fetchers when a fixture is within the T-30..120 pre-match
window.

Supports both integer Unix timestamps (the JTT format) and ISO-format
date strings. Integer timestamps are tried FIRST because that's what
worldcup_fixtures_2026.json uses; ISO strings are a fallback.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    """Return a UTC datetime, or None if no usable kickoff time found.
    Tries integer Unix timestamps (seconds OR milliseconds) first, then
    common ISO string fields, then API-Football's nested fixture.date."""

    # 1) Integer/float Unix timestamp — the JTT fixtures file uses this
    for key in ("timestamp", "ts", "unix", "kickoff_ts"):
        v = fx.get(key)
        if isinstance(v, (int, float)) and v > 0:
            ts_sec = v / 1000 if v > 1e12 else v  # auto-detect ms vs s
            try:
                return datetime.fromtimestamp(ts_sec, tz=timezone.utc)
            except (OSError, ValueError, OverflowError):
                continue

    # 2) ISO-format string
    for key in ("commence_time", "kickoff", "date", "datetime", "utcDate"):
        v = fx.get(key)
        if v and isinstance(v, str):
            try:
                return datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                continue

    # 3) API-Football nested shape: {"fixture": {"date": "..."}}
    nested = fx.get("fixture") or {}
    nested_date = nested.get("date") if isinstance(nested, dict) else None
    if isinstance(nested_date, str):
        try:
            return datetime.fromisoformat(nested_date.replace("Z", "+00:00"))
        except ValueError:
            pass
    nested_ts = nested.get("timestamp") if isinstance(nested, dict) else None
    if isinstance(nested_ts, (int, float)) and nested_ts > 0:
        ts_sec = nested_ts / 1000 if nested_ts > 1e12 else nested_ts
        try:
            return datetime.fromtimestamp(ts_sec, tz=timezone.utc)
        except (OSError, ValueError, OverflowError):
            pass

    return None


def extract_team_names(fx):
    home = fx.get("home_team") or fx.get("home") or fx.get("homeTeam")
    away = fx.get("away_team") or fx.get("away") or fx.get("awayTeam")
    if home and away:
        return home, away
    teams = fx.get("teams") or {}
    h = teams.get("home") if isinstance(teams, dict) else None
    a = teams.get("away") if isinstance(teams, dict) else None
    return (h.get("name") if isinstance(h, dict) else (h or "?"),
            a.get("name") if isinstance(a, dict) else (a or "?"))


def main():
    data, src = load_fixtures()
    if data is None:
        print(f"ERROR: no fixtures file found. Tried: {[str(p) for p in FIXTURES_PATHS]}",
              file=sys.stderr)
        gh_output = os.environ.get("GITHUB_OUTPUT")
        if gh_output:
            with open(gh_output, "a") as f:
                f.write("refresh=false\n")
        sys.exit(0)

    if isinstance(data, list):
        fixtures = data
    elif isinstance(data, dict):
        fixtures = (data.get("fixtures") or data.get("matches")
                    or data.get("response") or data.get("byFixture") or [])
        if isinstance(fixtures, dict):
            fixtures = list(fixtures.values())
    else:
        fixtures = []

    now = datetime.now(timezone.utc)
    window_start = now + timedelta(minutes=WINDOW_START_MIN)
    window_end   = now + timedelta(minutes=WINDOW_END_MIN)

    parsed_count = 0
    matching = []
    upcoming_within_day = []  # for diagnostics

    for fx in fixtures:
        if not isinstance(fx, dict):
            continue
        kickoff = extract_kickoff(fx)
        if not kickoff:
            continue
        parsed_count += 1
        if window_start <= kickoff <= window_end:
            matching.append((fx, kickoff))
        elif now <= kickoff <= now + timedelta(hours=24):
            upcoming_within_day.append((fx, kickoff))

    gh_output = os.environ.get("GITHUB_OUTPUT")

    # Diagnostic line — shows fixtures parsed (catches the silent zero-match bug)
    print(f"Gate: parsed {parsed_count} fixtures with valid kickoff times (source: {src}).")

    if matching:
        print(f"REFRESH NEEDED — {len(matching)} fixture(s) in T-{WINDOW_START_MIN}..{WINDOW_END_MIN} window:")
        for fx, ko in matching:
            home, away = extract_team_names(fx)
            mins = int((ko - now).total_seconds() / 60)
            print(f"  - {home} vs {away}  (kickoff in {mins} min, at {ko.isoformat()})")
        if gh_output:
            with open(gh_output, "a") as f:
                f.write("refresh=true\n")
        sys.exit(0)

    print(f"No fixtures in T-{WINDOW_START_MIN}..{WINDOW_END_MIN} window. Skipping refresh.")
    if upcoming_within_day:
        print(f"  (Diagnostic: {len(upcoming_within_day)} fixture(s) within the next 24h:)")
        for fx, ko in upcoming_within_day[:5]:
            home, away = extract_team_names(fx)
            mins = int((ko - now).total_seconds() / 60)
            print(f"    - {home} vs {away}  (kickoff in {mins} min)")
    if gh_output:
        with open(gh_output, "a") as f:
            f.write("refresh=false\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
