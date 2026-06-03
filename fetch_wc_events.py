#!/usr/bin/env python3
"""
fetch_wc_events.py
==================
Fetches match events (goals, cards, subs) for every match in the WC dataset.
Used by the By The Halves dropdown in the JTT WC tool — we need each goal's
minute to split scoring/conceding patterns into 1st-half vs 2nd-half.

Storage:
    worldcup_events.json — { matchId: [{minute, teamId, type, detail, player}] }

This is a one-shot backfill — run it once to populate ~1300 historical matches,
then the matchday poll workflow will incrementally add events for new fixtures.

Usage on Windows:
    python fetch_wc_events.py --key=your-api-football-key --data=wc\\data

Or via env var:
    set APIFOOTBALL_KEY=your-key
    python fetch_wc_events.py --data=wc\\data

API budget:
    1 call per match × ~1330 matches = ~1330 calls (well under 7500/day Pro).
    Idempotent — cached responses mean re-runs are free.
"""

import json
import os
import sys
import time
import urllib.parse
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
# Default to current working directory — matches the convention of
# fetch_wc_lineups.py / fetch_wc_live.py. The GitHub Actions matchday
# poll sets `working-directory: wc/data` so the script just reads from
# `.`. Daily-update runs from repo root and passes --data=wc/data.
DATA_DIR = Path(".")
FORCE_REFRESH = False
MAX_MATCHES = None  # cap for testing

for arg in sys.argv[1:]:
    if arg.startswith("--key="):
        API_KEY = arg.split("=", 1)[1].strip().strip('"').strip("'")
    elif arg.startswith("--data="):
        DATA_DIR = Path(arg.split("=", 1)[1].strip())
    elif arg == "--force":
        FORCE_REFRESH = True
    elif arg.startswith("--max="):
        MAX_MATCHES = int(arg.split("=", 1)[1])

if not API_KEY:
    print("ERROR: API-Football key required.")
    print("  Pass --key=YOUR_KEY  OR  set APIFOOTBALL_KEY=YOUR_KEY")
    sys.exit(1)

BASE = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}
SLEEP_BETWEEN_CALLS = 0.3

DATA_DIR.mkdir(parents=True, exist_ok=True)
CACHE = DATA_DIR / ".cache"
CACHE.mkdir(exist_ok=True)

STATS_FILE    = DATA_DIR / "worldcup_team_stats.json"
FIXTURES_FILE = DATA_DIR / "worldcup_fixtures_2026.json"
EVENTS_FILE   = DATA_DIR / "worldcup_events.json"


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
# Event extraction
# ---------------------------------------------------------------------------

def extract_events(api_response):
    """Pull a tidy event list from /fixtures/events response.

    Each event: { minute, extra, teamId, playerId, playerName, type, detail }
      type   = 'goal' | 'card' | 'subst' | 'var'
      detail = e.g. 'Normal Goal', 'Own Goal', 'Penalty', 'Missed Penalty',
               'Yellow Card', 'Red Card', etc.
    """
    out = []
    for ev in api_response.get("response", []) or []:
        t = ev.get("time", {}) or {}
        minute = t.get("elapsed")
        extra  = t.get("extra")  # added time minutes, may be None
        team   = ev.get("team", {}) or {}
        player = ev.get("player", {}) or {}
        out.append({
            "minute": minute,
            "extra":  extra,
            "teamId": team.get("id"),
            "playerId":   player.get("id"),
            "playerName": player.get("name"),
            "type":   (ev.get("type") or "").lower(),
            "detail": ev.get("detail"),
        })
    return out


# ---------------------------------------------------------------------------
# Match ID collection — read from BOTH team_stats AND fixtures.json so we
# catch newly-finished matches even before they hit team_stats.
# ---------------------------------------------------------------------------

def collect_match_ids():
    ids = set()
    if STATS_FILE.exists():
        stats = json.loads(STATS_FILE.read_text())
        for mid in (stats.get("matches", {}) or {}).keys():
            ids.add(str(mid))
    if FIXTURES_FILE.exists():
        fx = json.loads(FIXTURES_FILE.read_text())
        for f in (fx.get("fixtures", []) or []):
            # Only finished or in-play matches — pre-match has no events yet
            status = (f.get("status") or "").upper()
            if status in ("FT", "AET", "PEN", "1H", "2H", "ET", "HT", "BT", "P"):
                mid = f.get("matchId") or f.get("fixtureId")
                if mid is not None:
                    ids.add(str(mid))
    return ids


# ---------------------------------------------------------------------------
# Main backfill loop
# ---------------------------------------------------------------------------

def main():
    match_ids = collect_match_ids()
    if not match_ids:
        print(f"No match IDs found in {STATS_FILE} or {FIXTURES_FILE}")
        sys.exit(1)
    print(f"Found {len(match_ids)} candidate matches across team_stats + FT fixtures.")

    # Load existing events file (so we can resume mid-backfill)
    if EVENTS_FILE.exists():
        events_doc = json.loads(EVENTS_FILE.read_text())
    else:
        events_doc = {"generated": None, "matchCount": 0, "events": {}}
    existing = events_doc.get("events", {})
    print(f"Already have events for {len(existing)} matches.")

    todo = [mid for mid in match_ids if mid not in existing or FORCE_REFRESH]
    if MAX_MATCHES is not None:
        todo = todo[:MAX_MATCHES]
    print(f"Fetching events for {len(todo)} matches...")

    new_count = 0
    for i, mid in enumerate(todo, 1):
        try:
            resp = api("/fixtures/events", {"fixture": int(mid)})
            ev_list = extract_events(resp)
            existing[str(mid)] = ev_list
            new_count += 1
            if i % 25 == 0 or i == len(todo):
                # Save incrementally so a crash doesn't lose progress
                events_doc["events"] = existing
                events_doc["matchCount"] = len(existing)
                events_doc["generated"] = int(time.time())
                EVENTS_FILE.write_text(json.dumps(events_doc, separators=(",", ":")))
                print(f"  [{i}/{len(todo)}] saved checkpoint — {len(existing)} total matches with events")
        except requests.HTTPError as e:
            print(f"  [{i}/{len(todo)}] HTTP error on match {mid}: {e}")
        except Exception as e:
            print(f"  [{i}/{len(todo)}] ERROR on match {mid}: {e}")

    # Final write
    events_doc["events"] = existing
    events_doc["matchCount"] = len(existing)
    events_doc["generated"] = int(time.time())
    EVENTS_FILE.write_text(json.dumps(events_doc, separators=(",", ":")))
    print(f"\nDone. Wrote events for {len(existing)} matches to {EVENTS_FILE}")
    print(f"  ({new_count} newly fetched this run)")


if __name__ == "__main__":
    main()
