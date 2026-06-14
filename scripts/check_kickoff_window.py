#!/usr/bin/env python3
"""
Gate script for pre-match data refresh.

Writes refresh=true|false to $GITHUB_OUTPUT. Always exits 0.

Fires (refresh=true) if ANY fixture is either:
  - upcoming within the next 6 hours (status NS/TBD), OR
  - currently live (1H, HT, 2H, ET, BT, P, LIVE)

This wider criteria ensures the lineup fetcher runs during the actual
window in which API-Football publishes lineups — which can be anywhere
from T-90 minutes pre-kickoff up to a few minutes after kickoff.

Used by .github/workflows/wc-prematch-refresh.yml.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

FIXTURES_PATHS = [
    Path("wc/data/worldcup_fixtures_2026.json"),
]

UPCOMING_WINDOW_HOURS = 6
LIVE_STATUSES = {"1H", "HT", "2H", "ET", "BT", "P", "LIVE", "SUSP", "INT"}
UPCOMING_STATUSES = {"NS", "TBD"}


def load_fixtures():
    for p in FIXTURES_PATHS:
        if p.exists():
            with p.open() as f:
                data = json.load(f)
            return data, p
    return None, None


def extract_kickoff(fx):
    for key in ("timestamp", "ts", "unix", "kickoff_ts"):
        v = fx.get(key)
        if isinstance(v, (int, float)) and v > 0:
            ts_sec = v / 1000 if v > 1e12 else v
            try:
                return datetime.fromtimestamp(ts_sec, tz=timezone.utc)
            except (OSError, ValueError, OverflowError):
                continue
    for key in ("commence_time", "kickoff", "date", "datetime", "utcDate"):
        v = fx.get(key)
        if v and isinstance(v, str):
            try:
                return datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                continue
    nested = fx.get("fixture") or {}
    if isinstance(nested, dict):
        d = nested.get("date")
        if isinstance(d, str):
            try:
                return datetime.fromisoformat(d.replace("Z", "+00:00"))
            except ValueError:
                pass
        ts = nested.get("timestamp")
        if isinstance(ts, (int, float)) and ts > 0:
            ts_sec = ts / 1000 if ts > 1e12 else ts
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
    if isinstance(teams, dict):
        h = teams.get("home"); a = teams.get("away")
        return (h.get("name") if isinstance(h, dict) else (h or "?"),
                a.get("name") if isinstance(a, dict) else (a or "?"))
    return "?", "?"


def main():
    data, src = load_fixtures()
    gh_output = os.environ.get("GITHUB_OUTPUT")

    def write_output(refresh):
        if gh_output:
            with open(gh_output, "a") as f:
                f.write(f"refresh={'true' if refresh else 'false'}\n")

    if data is None:
        print(f"ERROR: no fixtures file found.", file=sys.stderr)
        write_output(False)
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
    upcoming_horizon = now + timedelta(hours=UPCOMING_WINDOW_HOURS)

    live = []
    upcoming = []
    parsed_count = 0

    for fx in fixtures:
        if not isinstance(fx, dict):
            continue
        status = fx.get("status", "")
        kickoff = extract_kickoff(fx)
        if kickoff:
            parsed_count += 1
        # Live match — fire immediately
        if status in LIVE_STATUSES:
            live.append((fx, kickoff))
            continue
        # Upcoming within window
        if status in UPCOMING_STATUSES and kickoff and now <= kickoff <= upcoming_horizon:
            upcoming.append((fx, kickoff))

    print(f"Gate: parsed {parsed_count} fixtures with valid kickoff times (source: {src}).")
    print(f"  - live: {len(live)} matches")
    print(f"  - upcoming within {UPCOMING_WINDOW_HOURS}h: {len(upcoming)} matches")

    if live or upcoming:
        for fx, ko in live:
            home, away = extract_team_names(fx)
            print(f"  LIVE: {home} vs {away} ({fx.get('status')})")
        for fx, ko in upcoming:
            home, away = extract_team_names(fx)
            mins = int((ko - now).total_seconds() / 60)
            print(f"  UPCOMING: {home} vs {away} (KO in {mins} min)")
        write_output(True)
        sys.exit(0)

    print("No active or imminent matches. Skipping refresh.")
    write_output(False)
    sys.exit(0)


if __name__ == "__main__":
    main()
