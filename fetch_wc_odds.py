#!/usr/bin/env python3
"""
fetch_wc_odds.py — pulls bookmaker odds for WC 2026 fixtures from API-Football.

WHAT IT DOES:
    1. Calls /odds?league=1&season=2026&bookmaker=8 (Bet365 default).
    2. Walks all pages, extracts the 5 markets we render on fixture cards:
         matchWinner, btts, goals (O/U), corners (O/U), cards (O/U).
    3. Writes worldcup_odds_2026.json + bumps version.txt for cache busting.

WHY A SEPARATE FETCHER:
    Odds change continuously and only the last 7 days are retained by the API
    (no historical query). Player/team stats only need updating after games
    finish — odds need refreshing more like daily (or hourly on matchday).
    Keeping it separate lets the cron schedules diverge later.

USAGE:
    export APIFOOTBALL_KEY=your_key
    python fetch_wc_odds.py
    # or:
    python fetch_wc_odds.py --key=your_key --bookmaker=8

LIMITATIONS:
    - API-Football only retains last 7 days of odds. To track line movement
      over time we'd need to capture and store the deltas locally (not done
      here — out of scope; this fetcher snapshots current state only).
    - Coverage varies per league; if the WC league's /leagues coverage.odds
      field is false, this will silently produce an empty JSON. Verify with:
          curl ".../v3/leagues?id=1" -H "x-apisports-key: KEY"
    - Bet IDs below are the documented ones at time of writing. If a bet
      shows up with unexpected name/structure the fetcher logs a WARN and
      continues — no hard fail.
"""
import os, sys, json, time, requests
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://v3.football.api-sports.io"
LEAGUE_ID = 1     # FIFA World Cup
SEASON    = 2026
DEFAULT_BOOKMAKER_ID = 8  # Bet365 in API-Football's catalog

# Bet ID → our normalised market key. Anything not in this map is ignored
# (the API returns dozens of esoteric markets we don't surface in the UI).
MARKET_MAP = {
    1:  "matchWinner",   # 1X2
    5:  "goals",         # Goals Over/Under — multiple lines, we pick closest to 2.5
    8:  "btts",          # Both Teams To Score
    45: "corners",       # Corners Over/Under — verify ID against the API if empty
    25: "cards",         # Cards Over/Under  — verify ID against the API if empty
}

# Target lines for the O/U markets — we pick the line closest to these so the
# display matches our projection cards (which use these defaults).
TARGET_LINES = {"goals": 2.5, "corners": 9.5, "cards": 4.5}

CACHE_DIR = Path("data/.cache/odds")
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def get_arg(name, default=None):
    """Read --name=value from sys.argv."""
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


def api_get(params, key):
    """GET /odds with the given params. Caches by parameter hash so reruns
    within a session are cheap; cache key includes the date so daily reruns
    still fetch fresh data."""
    cache_key = "_".join(f"{k}-{v}" for k, v in sorted(params.items()))
    cache_key = cache_key.replace("/", "_") + f"_{datetime.now().strftime('%Y%m%d')}.json"
    cache_path = CACHE_DIR / cache_key
    if cache_path.exists():
        with cache_path.open() as f:
            return json.load(f)

    r = requests.get(BASE + "/odds", params=params,
                     headers={"x-apisports-key": key}, timeout=30)
    r.raise_for_status()
    data = r.json()
    errors = data.get("errors") or []
    if errors:
        # API returns errors as either list or dict — handle both
        msg = errors if isinstance(errors, list) else list(errors.values())
        if msg:
            print(f"  API errors: {msg}")
    with cache_path.open("w") as f:
        json.dump(data, f)
    return data


def parse_bookmaker(bookmaker):
    """Extract our 5 markets from one bookmaker's bet list. Returns a dict
    keyed by our market names. Missing markets are simply absent from output."""
    out = {}
    for bet in bookmaker.get("bets", []):
        key = MARKET_MAP.get(bet.get("id"))
        if not key:
            continue
        values = bet.get("values", [])
        try:
            if key == "matchWinner":
                d = {}
                for v in values:
                    if v["value"] == "Home": d["home"] = float(v["odd"])
                    elif v["value"] == "Draw": d["draw"] = float(v["odd"])
                    elif v["value"] == "Away": d["away"] = float(v["odd"])
                if {"home", "draw", "away"}.issubset(d):
                    out[key] = d
            elif key == "btts":
                d = {}
                for v in values:
                    if v["value"] == "Yes": d["yes"] = float(v["odd"])
                    elif v["value"] == "No":  d["no"]  = float(v["odd"])
                if "yes" in d and "no" in d:
                    out[key] = d
            elif key in ("goals", "corners", "cards"):
                # Multiple lines — collect all into {line: {over, under}} then
                # pick the one closest to our target line.
                by_line = {}
                for v in values:
                    parts = v["value"].split()
                    if len(parts) != 2: continue
                    side, line_str = parts
                    try:
                        line = float(line_str)
                    except ValueError:
                        continue
                    by_line.setdefault(line, {})[side.lower()] = float(v["odd"])
                # Filter to lines that have BOTH over + under priced
                complete = {ln: o for ln, o in by_line.items() if "over" in o and "under" in o}
                if not complete:
                    continue
                target = TARGET_LINES[key]
                best_line = min(complete.keys(), key=lambda ln: abs(ln - target))
                out[key] = {"line": best_line,
                            "over":  complete[best_line]["over"],
                            "under": complete[best_line]["under"]}
        except (KeyError, ValueError, TypeError) as e:
            print(f"  WARN: skipped bet {bet.get('id')} '{bet.get('name')}': {e}")
            continue
    return out


def main():
    key = get_key()
    bookmaker_id = int(get_arg("bookmaker", str(DEFAULT_BOOKMAKER_ID)))
    print(f"Fetching WC 2026 odds — league={LEAGUE_ID}, season={SEASON}, bookmaker={bookmaker_id}")

    by_fixture = {}
    bookmaker_name = None
    page = 1
    total_pages = 1

    while page <= total_pages:
        params = {"league": LEAGUE_ID, "season": SEASON,
                  "bookmaker": bookmaker_id, "page": page}
        resp = api_get(params, key)
        total_pages = resp.get("paging", {}).get("total", 1) or 1
        rows = resp.get("response", []) or []
        print(f"  page {page}/{total_pages} — {len(rows)} fixtures returned")

        for row in rows:
            fid = (row.get("fixture") or {}).get("id")
            bks = row.get("bookmakers") or []
            if not fid or not bks:
                continue
            # We requested a specific bookmaker so there should only be one,
            # but be defensive in case the filter returns multiple.
            chosen = next((b for b in bks if b.get("id") == bookmaker_id), bks[0])
            bookmaker_name = bookmaker_name or chosen.get("name")
            markets = parse_bookmaker(chosen)
            if markets:
                by_fixture[str(fid)] = markets
        page += 1

    print(f"\nCompiled odds for {len(by_fixture)} fixtures · bookmaker: {bookmaker_name}")

    version_ms = int(time.time() * 1000)
    # Append the current snapshot to the rolling history. We keep the last 48
    # snapshots (~2 days at hourly cadence) so the HTML can compute the line
    # drift between then-and-now without storing every reading forever.
    MAX_SNAPSHOTS = 48
    existing = {}
    out_path = "worldcup_odds_2026.json"
    if os.path.exists(out_path):
        try:
            with open(out_path) as f:
                existing = json.load(f) or {}
        except (OSError, ValueError):
            existing = {}
    history = existing.get("history") or []
    history.append({"ts": version_ms, "byFixture": by_fixture})
    history = history[-MAX_SNAPSHOTS:]

    output = {
        "fetchedAt":     datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "version":       version_ms,
        "bookmakerName": bookmaker_name or "Bet365",
        "byFixture":     by_fixture,     # current snapshot (what the UI reads by default)
        "history":       history,        # rolling snapshots for line-movement display
    }
    with open(out_path, "w") as f:
        json.dump(output, f, separators=(",", ":"))  # compact — history grows
    # Bump version.txt so the HTML cache-busts the odds (and any other) file
    with open("version.txt", "w") as f:
        f.write(str(version_ms))
    print(f"Saved {out_path} ({len(history)} snapshots in history) and bumped version.txt")


if __name__ == "__main__":
    main()
