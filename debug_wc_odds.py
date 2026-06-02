#!/usr/bin/env python3
"""
debug_wc_odds.py — figure out why /odds is returning empty for WC 2026.

Runs four diagnostic queries and prints findings so we can locate the gap.
"""
import os, sys, json, requests
from collections import Counter

BASE = "https://v3.football.api-sports.io"


def get_key():
    for arg in sys.argv[1:]:
        if arg.startswith("--key="):
            return arg.split("=", 1)[1]
    k = os.environ.get("APIFOOTBALL_KEY")
    if not k:
        sys.exit("ERROR: APIFOOTBALL_KEY env var or --key=XXX required")
    return k


def api(path, params=None):
    r = requests.get(BASE + path, params=params or {},
                     headers={"x-apisports-key": API_KEY}, timeout=30)
    r.raise_for_status()
    return r.json()


API_KEY = get_key()

print("=" * 70)
print("DIAGNOSTIC 1 — League info & odds-coverage flag")
print("=" * 70)
try:
    resp = api("/leagues", {"id": 1})
    if resp.get("response"):
        info = resp["response"][0]
        print(f"League: {info['league']['name']} (id {info['league']['id']})")
        # Find the 2026 season entry
        seasons = info.get("seasons", [])
        s2026 = next((s for s in seasons if s.get("year") == 2026), None)
        if s2026:
            print(f"2026 season FOUND")
            print(f"  start: {s2026.get('start')}  end: {s2026.get('end')}")
            cov = s2026.get("coverage", {})
            print(f"  coverage.fixtures.events:        {cov.get('fixtures', {}).get('events')}")
            print(f"  coverage.fixtures.lineups:       {cov.get('fixtures', {}).get('lineups')}")
            print(f"  coverage.fixtures.statistics:    {cov.get('fixtures', {}).get('statistics_fixtures')}")
            print(f"  coverage.odds:                    {cov.get('odds')}")  # THE KEY FLAG
            print(f"  coverage.injuries:                {cov.get('injuries')}")
            print(f"  coverage.standings:               {cov.get('standings')}")
        else:
            print("** 2026 SEASON NOT IN LEAGUE 1 **")
            print(f"  Available seasons: {[s.get('year') for s in seasons[-6:]]}")
    else:
        print(f"** /leagues?id=1 returned nothing: {resp}")
except Exception as e:
    print(f"!! error: {e}")

print()
print("=" * 70)
print("DIAGNOSTIC 2 — /odds with NO bookmaker filter")
print("=" * 70)
try:
    resp = api("/odds", {"league": 1, "season": 2026})
    paging = resp.get("paging", {})
    print(f"  results: {resp.get('results', 0)}")
    print(f"  paging:  current={paging.get('current')} total={paging.get('total')}")
    fixtures = resp.get("response", [])
    if fixtures:
        # Show one fixture's bookmaker list
        sample = fixtures[0]
        print(f"  sample fixture id: {sample['fixture']['id']}")
        print(f"  sample date: {sample['fixture']['date']}")
        bookmakers = sample.get("bookmakers", [])
        print(f"  bookmakers offering odds on this fixture:")
        for b in bookmakers:
            bet_ids = sorted({bet["id"] for bet in b.get("bets", [])})
            print(f"    [{b['id']:>3}] {b['name']:25} — bet IDs: {bet_ids}")
    else:
        print("  ** zero odds entries returned for league=1, season=2026 **")
except Exception as e:
    print(f"!! error: {e}")

print()
print("=" * 70)
print("DIAGNOSTIC 3 — /odds/mapping (what HAS odds available right now)")
print("=" * 70)
try:
    resp = api("/odds/mapping")
    mapping = resp.get("response", [])
    print(f"  total entries: {len(mapping)}")
    # Filter to anything that mentions World Cup / WC / etc
    wc_entries = [m for m in mapping if m.get("league") and "world" in m["league"]["name"].lower()]
    if wc_entries:
        print(f"  World Cup-flavoured leagues with priced fixtures:")
        for m in wc_entries[:20]:
            lg = m.get("league", {})
            print(f"    league id {lg.get('id')}, season {lg.get('season')} — {lg.get('name')}")
    else:
        print("  no World Cup leagues found in odds mapping")
    # Also check if league=1 appears at all
    league1 = [m for m in mapping if m.get("league", {}).get("id") == 1]
    print(f"  league=1 entries in mapping: {len(league1)}")
    if league1:
        seasons = sorted({m["league"]["season"] for m in league1 if m["league"].get("season")})
        print(f"    seasons present: {seasons}")
except Exception as e:
    print(f"!! error: {e}")

print()
print("=" * 70)
print("DIAGNOSTIC 4 — Are upcoming WC fixtures actually under league=1, season=2026?")
print("=" * 70)
try:
    resp = api("/fixtures", {"league": 1, "season": 2026, "next": 3})
    fxs = resp.get("response", [])
    print(f"  next-3 fixtures: {len(fxs)}")
    for f in fxs:
        print(f"    {f['fixture']['date'][:10]}  "
              f"{f['teams']['home']['name']} vs {f['teams']['away']['name']}  "
              f"(fixture id {f['fixture']['id']})")
        # Check if THIS specific fixture has odds
        odds = api("/odds", {"fixture": f["fixture"]["id"]})
        ofx = odds.get("response", [])
        if ofx:
            books = ofx[0].get("bookmakers", [])
            print(f"      → odds available from {len(books)} bookmakers: " +
                  ", ".join(b["name"] for b in books[:6]))
        else:
            print("      → no odds returned for this fixture")
except Exception as e:
    print(f"!! error: {e}")

print()
print("=" * 70)
print("DONE — share this output and we'll figure out the gap")
print("=" * 70)
