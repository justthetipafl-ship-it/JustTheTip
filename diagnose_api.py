#!/usr/bin/env python3
"""
diagnose_api.py — Probe API-Football for a specific fixture and dump what
it returns for /fixtures, /fixtures/lineups, /fixtures/players.

USAGE:
    export APIFOOTBALL_KEY=your_key
    python diagnose_api.py --fixture=1489371

Run via GitHub Actions workflow_dispatch with the fixture_id input.
"""
import json
import os
import sys
import requests

BASE = "https://v3.football.api-sports.io"


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


def call(path, params, key):
    print(f"\n{'='*60}")
    print(f"GET {path}?{'&'.join(f'{k}={v}' for k,v in params.items())}")
    print('='*60)
    try:
        r = requests.get(BASE + path, params=params,
                         headers={"x-apisports-key": key}, timeout=30)
        print(f"HTTP {r.status_code}")
        data = r.json()
    except Exception as e:
        print(f"ERROR: {e}")
        return None

    # API-Football metadata
    print(f"results:   {data.get('results')}")
    print(f"errors:    {data.get('errors')}")
    print(f"paging:    {data.get('paging')}")
    return data


def main():
    fixture_id = get_arg("fixture")
    if not fixture_id:
        sys.exit("ERROR: pass --fixture=<MATCH_ID>")
    key = get_key()

    # 1. Fixture details (referee lives here)
    data = call("/fixtures", {"id": fixture_id}, key)
    if data and data.get("response"):
        fx = data["response"][0].get("fixture") or {}
        teams = data["response"][0].get("teams") or {}
        print(f"\n→ Fixture metadata:")
        print(f"   date:     {fx.get('date')}")
        print(f"   status:   {fx.get('status', {}).get('long')}")
        print(f"   referee:  {fx.get('referee')!r}    ← this is what we extract")
        print(f"   venue:    {(fx.get('venue') or {}).get('name')}")
        print(f"   teams:    {teams.get('home', {}).get('name')} vs {teams.get('away', {}).get('name')}")
    else:
        print("→ Empty response — API-Football has no record of this fixture ID")

    # 2. Lineups
    data = call("/fixtures/lineups", {"fixture": fixture_id}, key)
    if data and data.get("response"):
        for team in data["response"]:
            team_info = team.get("team", {}) or {}
            print(f"\n→ Lineup for {team_info.get('name')} (id={team_info.get('id')}):")
            print(f"   formation:   {team.get('formation')}")
            print(f"   coach:       {(team.get('coach') or {}).get('name')}")
            print(f"   startXI:     {len(team.get('startXI') or [])} players")
            print(f"   substitutes: {len(team.get('substitutes') or [])} players")
            xi = team.get('startXI') or []
            if xi:
                print(f"   sample player: {xi[0]}")
    else:
        print("→ Empty lineups response — API has no lineup data for this fixture yet")

    # 3. Players (sometimes refs/lineups missing but players present)
    data = call("/fixtures/players", {"fixture": fixture_id}, key)
    if data and data.get("response"):
        print(f"→ Player stats present ({len(data['response'])} teams worth)")
    else:
        print("→ No player stats yet (expected pre-match)")

    print("\n" + "="*60)
    print("DIAGNOSIS COMPLETE")
    print("="*60)


if __name__ == "__main__":
    main()
