#!/usr/bin/env python3
"""
backfill_lineups.py — one-time pull of historical lineups + formations for
every match in our existing logs.

WHAT IT DOES:
    1. Reads worldcup_intl_logs.json to get the full list of matchIds.
    2. For each matchId, calls /fixtures/lineups?fixture=X.
    3. Extracts: startXI (player IDs + positions + grid), substitutes,
       formation string, coach, team colors.
    4. Writes wc_lineups.json keyed by matchId.

THE LINEUPS JSON IS ALSO THE FORMATIONS JSON:
    No separate formations file. wc_lineups.json carries the formation string
    per team per match. HTML aggregates "last 5 shapes" at runtime by querying
    this same file.

CALL BUDGET:
    ~3000 historical matches × 1 call each.
    At 7500/day quota this fits in a single run with room to spare.
    Cached locally — re-runs skip already-fetched matches.

USAGE:
    export APIFOOTBALL_KEY=your_key
    python backfill_lineups.py
    # or to limit per run (testing):
    python backfill_lineups.py --max=100
"""
import os, sys, json, time, requests
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://v3.football.api-sports.io"
CACHE_DIR = Path("data/.cache/lineups")
CACHE_DIR.mkdir(parents=True, exist_ok=True)


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


def fetch_lineup(match_id, key):
    """Fetch one match's lineups with persistent cache. Returns parsed JSON
    or None if API returned nothing useful."""
    cache_path = CACHE_DIR / f"{match_id}.json"
    if cache_path.exists():
        with cache_path.open() as f:
            return json.load(f)
    # API-Football enforces ~10 req/sec. Sleep before each network call so we
    # stay well under that ceiling and don't trigger 429 rate-limit responses.
    time.sleep(0.15)
    r = requests.get(BASE + "/fixtures/lineups",
                     params={"fixture": match_id},
                     headers={"x-apisports-key": key}, timeout=30)
    r.raise_for_status()
    data = r.json()
    with cache_path.open("w") as f:
        json.dump(data, f)
    return data


def parse_lineup_response(data):
    """The /fixtures/lineups response shape is a list of 0–2 team-blocks.
    Each block has: team (id, name, colors), coach, formation, startXI[]
    and substitutes[]. We normalise it into our home/away structure."""
    teams = data.get("response", []) or []
    if not teams:
        return None
    out = {}
    for t in teams:
        team_info = t.get("team", {}) or {}
        team_id = team_info.get("id")
        if not team_id:
            continue
        # Each startXI entry wraps a player dict
        def _flatten(entries):
            arr = []
            for e in (entries or []):
                p = e.get("player") or {}
                arr.append({
                    "id":     p.get("id"),
                    "name":   p.get("name"),
                    "number": p.get("number"),
                    "pos":    p.get("pos"),   # F/M/D/G
                    "grid":   p.get("grid"),  # e.g. "4:2" — column:row
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


def main():
    key = get_key()
    max_calls = int(get_arg("max", "10000"))

    print("Loading existing match list from worldcup_intl_logs.json...")
    with open("worldcup_intl_logs.json") as f:
        logs = json.load(f)

    # Distinct matchIds — sorted oldest first so partial runs cover earliest
    # matches first (cheapest to "lock in" since they're historical).
    match_ids = sorted({r["matchId"] for r in logs["rows"]})
    print(f"Found {len(match_ids)} distinct matches in logs")

    # Load existing output if present — incremental updates
    out_path = Path("wc_lineups.json")
    if out_path.exists():
        with out_path.open() as f:
            existing = json.load(f)
        by_match = existing.get("byMatch", {})
        print(f"Loaded {len(by_match)} already-fetched matches from previous run")
    else:
        by_match = {}

    pending = [m for m in match_ids if str(m) not in by_match]
    pending = pending[:max_calls]
    print(f"{len(pending)} matches to fetch this run")

    fetched = 0
    skipped = 0
    for i, mid in enumerate(pending, 1):
        try:
            data = fetch_lineup(mid, key)
        except requests.RequestException as e:
            print(f"  [{i}/{len(pending)}] match {mid} — HTTP error: {e}")
            continue
        errors = data.get("errors") or []
        if errors and isinstance(errors, (list, dict)) and len(errors):
            print(f"  [{i}/{len(pending)}] match {mid} — API errors: {errors}")
            continue
        parsed = parse_lineup_response(data)
        if parsed:
            by_match[str(mid)] = parsed
            fetched += 1
        else:
            # No lineup data for this fixture — store sentinel so we don't
            # repeatedly retry (some old friendlies just don't have it)
            by_match[str(mid)] = {"_empty": True}
            skipped += 1

        # Polite progress + checkpoint every 100 fetches
        if i % 100 == 0:
            print(f"  Progress: {i}/{len(pending)} (fetched {fetched}, skipped {skipped})")
            _save(by_match, out_path)

    _save(by_match, out_path)
    print(f"\nDone. Total in file: {len(by_match)} matches "
          f"({fetched} new with data, {skipped} new with no data)")


def _save(by_match, path):
    output = {
        "version":   int(time.time() * 1000),
        "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "byMatch":   by_match,
    }
    with path.open("w") as f:
        json.dump(output, f, separators=(",", ":"))  # compact for size


if __name__ == "__main__":
    main()
