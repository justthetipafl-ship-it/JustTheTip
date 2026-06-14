#!/usr/bin/env python3
"""
fetch_wc_lineups.py — lineup poller. Polls API-Football for fixtures that
are upcoming, live, or recently kicked off but not yet in our cache.

WHAT'S NEW:
  Previously this only polled fixtures with status in ("NS", "TBD"). That
  missed lineups announced near kickoff because once status transitions to
  "1H" the fixture got filtered out. Now we also poll live matches AND
  skip any fixture we already have lineups for (saving credits).

WRITES TO:
  wc/data/wc_lineups.json     ← merged, keyed by matchId then teamId
  wc/data/version.txt         ← bumped on any successful update

USAGE:
    export APIFOOTBALL_KEY=your_key
    python fetch_wc_lineups.py
"""

import os, sys, json, time, requests
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://v3.football.api-sports.io"
WINDOW_HOURS_AHEAD = 6
WINDOW_HOURS_BEHIND = 4   # also poll matches that kicked off in the last 4h
DATA_DIR = Path("wc/data")
CACHE_DIR = Path(".cache/wc-lineups")
CACHE_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Statuses where lineup data might be available or relevant.
# Excludes FT/AET/PEN (handled by backfill_lineups.py) and PST/CANC/ABD (done).
POLL_STATUSES = {"NS", "TBD", "1H", "HT", "2H", "ET", "BT", "P", "LIVE", "SUSP", "INT"}


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


def parse_lineup_response(data):
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


def has_cached_lineup(by_match, mid):
    """True if we already have a non-empty lineup for this match."""
    entry = by_match.get(str(mid)) or by_match.get(mid)
    if not entry or not isinstance(entry, dict):
        return False
    # Must have at least one team with a startXI
    for team_id, team_data in entry.items():
        if isinstance(team_data, dict) and team_data.get("startXI"):
            return True
    return False


def main():
    key = get_key()
    print("Polling lineups for ongoing fixtures...")

    fixtures_path = find_fixtures_path()
    print(f"  reading fixtures from: {fixtures_path}")

    with fixtures_path.open() as f:
        fixtures_data = json.load(f)

    fixtures = fixtures_data.get("fixtures") or []
    now = int(time.time())
    behind = now - WINDOW_HOURS_BEHIND * 3600
    ahead  = now + WINDOW_HOURS_AHEAD * 3600

    # Filter to fixtures within our time window AND in a status worth polling
    candidates = [
        f for f in fixtures
        if behind <= (f.get("timestamp") or 0) <= ahead
        and f.get("status") in POLL_STATUSES
    ]
    print(f"Found {len(candidates)} fixtures in window (status in {sorted(POLL_STATUSES)})")

    # Load existing lineups file — we MERGE into it, never overwrite
    out_path = DATA_DIR / "wc_lineups.json"
    if out_path.exists():
        with out_path.open() as f:
            existing = json.load(f)
        by_match = existing.get("byMatch", {}) or {}
    else:
        by_match = {}

    # Filter out fixtures we already have lineups for (no need to re-poll)
    to_poll = []
    for fx in candidates:
        mid = fx["matchId"]
        if has_cached_lineup(by_match, mid):
            print(f"  · {fx.get('homeTeam')} vs {fx.get('awayTeam')} — already cached, skipping")
            continue
        to_poll.append(fx)

    if not to_poll:
        print("Nothing new to poll. Exiting cleanly.")
        return

    print(f"Polling {len(to_poll)} fixtures...")

    updated = 0
    for fx in to_poll:
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
            status = fx.get("status", "?")
            print(f"  ✓ {fx.get('homeTeam')} vs {fx.get('awayTeam')} ({status}) — captured")
        else:
            status = fx.get("status", "?")
            print(f"  · {fx.get('homeTeam')} vs {fx.get('awayTeam')} ({status}) — API empty")

    if updated > 0:
        version_ms = int(time.time() * 1000)
        output = {
            "version":   version_ms,
            "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "byMatch":   by_match,
        }
        with out_path.open("w") as f:
            json.dump(output, f, separators=(",", ":"))

        (DATA_DIR / "version.txt").write_text(str(version_ms))
        print(f"\nUpdated {updated} fixtures with lineups. Saved {out_path}.")
    else:
        print("\nNo new lineups available this poll.")


if __name__ == "__main__":
    main()
