#!/usr/bin/env python3
"""
probe_wc_player_odds.py — discover what bet markets API-Football actually
returns for WC fixtures, broken down by bookmaker.

Run this against a single upcoming WC fixture (or a handful) to find out:
  1. Which bookmakers are even returning odds for that fixture
  2. What bet markets each bookmaker offers
  3. Specifically whether Bet365 (id 8) returns any PLAYER markets
     (Anytime Goalscorer, Shots on Target, Player Cards, etc.)

Output is a printed report you can paste back so we can decide whether
extending the odds fetcher is worth it.

Usage:
    export APIFOOTBALL_KEY=your_key
    python probe_wc_player_odds.py                # samples 3 fixtures
    python probe_wc_player_odds.py --fixture 1489369   # single fixture
"""

import argparse
import json
import os
import sys
from urllib import request, parse, error

API_BASE = "https://v3.football.api-sports.io"


def call(endpoint: str, params: dict, key: str) -> dict | None:
    url = f"{API_BASE}/{endpoint}?{parse.urlencode(params)}"
    req = request.Request(url, headers={"x-apisports-key": key})
    try:
        with request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except (error.URLError, error.HTTPError, TimeoutError) as e:
        print(f"  ! API call failed: {e}")
        return None


def report_fixture(fixture_id: int, key: str):
    print(f"\n{'='*60}")
    print(f"FIXTURE {fixture_id}")
    print(f"{'='*60}")

    # First, who's playing?
    info = call("fixtures", {"id": fixture_id}, key)
    if info and info.get("response"):
        fx = info["response"][0]
        home = fx["teams"]["home"]["name"]
        away = fx["teams"]["away"]["name"]
        date = fx["fixture"]["date"]
        print(f"  {home} vs {away} ({date[:10]})")

    # === Query 1: ALL bookmakers, NO bookmaker filter ===
    # This shows the full universe of markets for this fixture across every
    # book API-Football tracks. If player markets exist anywhere, they'll
    # appear here even if Bet365 doesn't have them.
    print(f"\n  [1/2] Querying ALL bookmakers ...")
    all_odds = call("odds", {"fixture": fixture_id}, key)
    if not all_odds or not all_odds.get("response"):
        print("  ✗ No odds available at all for this fixture")
        return

    bookmaker_market_map = {}  # { bookmaker_name: set(market_names) }
    for entry in all_odds["response"]:
        for bm in entry.get("bookmakers", []):
            name = bm["name"]
            bookmaker_market_map.setdefault(name, set())
            for bet in bm.get("bets", []):
                bookmaker_market_map[name].add(bet["name"])

    print(f"  Bookmakers with odds: {len(bookmaker_market_map)}")
    for name, markets in sorted(bookmaker_market_map.items()):
        # Heuristic: flag any market whose name looks player-level
        player_markets = sorted(m for m in markets if _looks_like_player_market(m))
        flag = f"  ⭐ {len(player_markets)} PLAYER markets" if player_markets else ""
        print(f"    • {name}: {len(markets)} markets{flag}")
        for pm in player_markets:
            print(f"        → {pm}")

    # === Query 2: Bet365 (id=8) specifically ===
    print(f"\n  [2/2] Querying Bet365 only (id=8) ...")
    bet365 = call("odds", {"fixture": fixture_id, "bookmaker": 8}, key)
    if not bet365 or not bet365.get("response"):
        print("  ✗ Bet365 has no odds for this fixture")
        return

    bet365_markets = set()
    for entry in bet365["response"]:
        for bm in entry.get("bookmakers", []):
            for bet in bm.get("bets", []):
                bet365_markets.add((bet.get("id"), bet["name"]))

    print(f"  Bet365 markets ({len(bet365_markets)}):")
    has_player = False
    for bet_id, name in sorted(bet365_markets, key=lambda x: x[1]):
        player = _looks_like_player_market(name)
        if player: has_player = True
        flag = " ⭐ PLAYER" if player else ""
        print(f"    [{bet_id}] {name}{flag}")
    if not has_player:
        print(f"\n  >>> Bet365 has NO player markets for this fixture <<<")


def _looks_like_player_market(name: str) -> bool:
    """Heuristic — flags market names that target individual players."""
    n = name.lower()
    keywords = [
        "goalscorer", "scorer", "shots", "cards", "assists",
        "anytime", "first goal", "last goal", "hat-trick", "hat trick",
        "player", "to score", "to be carded", "saves", "tackles",
    ]
    return any(k in n for k in keywords)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixture", type=int, action="append",
        help="Fixture ID to probe. Pass multiple --fixture flags for several.")
    args = ap.parse_args()

    key = os.environ.get("APIFOOTBALL_KEY")
    if not key:
        print("ERROR: set APIFOOTBALL_KEY env var")
        sys.exit(1)

    # Default sample: 3 upcoming WC matches if no specific fixture given.
    # Replace with actual matchIds from worldcup_fixtures_2026.json if needed.
    fixtures = args.fixture or [1489369, 1538999, 1539000]
    for fid in fixtures:
        report_fixture(fid, key)


if __name__ == "__main__":
    main()
