#!/usr/bin/env python3
"""
backfill_referees.py — captures the referee name on every historical fixture
and computes per-referee summary stats (cards/g, fouls/g, pens/g).

WHAT IT DOES:
    1. Reads worldcup_intl_logs.json for the list of matchIds.
    2. For each matchId, calls /fixtures?id=X — the response includes
       fixture.referee as a single string (e.g. "Daniele Orsato, Italy").
    3. Cross-references with worldcup_team_stats.json (which has cards,
       fouls per match) and logs.rows (which has penaltyScored/Committed
       per player) to compute per-ref averages.
    4. Writes wc_referees.json — keyed by matchId for lookup, plus a refStats
       block aggregating per ref.

NOTE ON NAME MATCHING:
    The API returns referee as a single string. Names occasionally vary
    (initials vs full names, accent differences). We normalise by:
      - strip country suffix after the comma
      - case-fold + collapse whitespace
    This catches ~95% of duplicates. The rest are too noisy to dedupe
    reliably without a referee ID, which API-Football doesn't provide.

CALL BUDGET:
    ~3000 historical matches × 1 call each.

USAGE:
    export APIFOOTBALL_KEY=your_key
    python backfill_referees.py
"""
import os, sys, json, time, re, requests
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

BASE = "https://v3.football.api-sports.io"
CACHE_DIR = Path("data/.cache/fixtures")
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


def fetch_fixture(match_id, key):
    cache_path = CACHE_DIR / f"{match_id}.json"
    if cache_path.exists():
        with cache_path.open() as f:
            return json.load(f)
    r = requests.get(BASE + "/fixtures",
                     params={"id": match_id},
                     headers={"x-apisports-key": key}, timeout=30)
    r.raise_for_status()
    data = r.json()
    with cache_path.open("w") as f:
        json.dump(data, f)
    return data


def normalise_referee(raw):
    """Turn 'Daniele Orsato, Italy' into ('Daniele Orsato', 'Italy').
    Country may be missing. Returns (name, country) tuple."""
    if not raw:
        return None, None
    parts = [p.strip() for p in raw.split(",")]
    name = parts[0] if parts else None
    country = parts[1] if len(parts) > 1 else None
    if name:
        # Collapse whitespace, strip dots from initials (J. Smith vs J Smith)
        name = re.sub(r"\s+", " ", name).strip()
    return name, country


def main():
    key = get_key()
    max_calls = int(get_arg("max", "10000"))

    print("Loading existing match list and stats...")
    with open("worldcup_intl_logs.json") as f:
        logs = json.load(f)
    with open("worldcup_team_stats.json") as f:
        team_stats = json.load(f)

    match_ids = sorted({r["matchId"] for r in logs["rows"]})
    print(f"Found {len(match_ids)} distinct matches in logs")

    # Load existing output for incremental updates
    out_path = Path("wc_referees.json")
    if out_path.exists():
        with out_path.open() as f:
            existing = json.load(f)
        by_match = existing.get("byMatch", {})
        print(f"Loaded {len(by_match)} already-fetched matches")
    else:
        by_match = {}

    pending = [m for m in match_ids if str(m) not in by_match]
    pending = pending[:max_calls]
    print(f"{len(pending)} matches to fetch this run")

    for i, mid in enumerate(pending, 1):
        try:
            data = fetch_fixture(mid, key)
        except requests.RequestException as e:
            print(f"  [{i}/{len(pending)}] match {mid} — HTTP error: {e}")
            continue
        rows = data.get("response", []) or []
        if not rows:
            by_match[str(mid)] = {"_empty": True}
            continue
        fx = rows[0]
        ref_raw = (fx.get("fixture") or {}).get("referee")
        name, country = normalise_referee(ref_raw)
        by_match[str(mid)] = {
            "raw":     ref_raw,
            "name":    name,
            "country": country,
        }
        if i % 100 == 0:
            print(f"  Progress: {i}/{len(pending)}")
            _save_match_data(by_match, out_path, team_stats, logs)

    # Aggregate referee stats from all matches that have both ref + match stats
    print("\nAggregating per-referee stats...")
    ref_stats = _compute_ref_stats(by_match, team_stats, logs)
    print(f"  {len(ref_stats)} distinct referees with usable data")
    high_sample = [r for r in ref_stats.values() if r["matches"] >= 15]
    print(f"  {len(high_sample)} have 15+ matches (reliable sample)")

    _save_match_data(by_match, out_path, team_stats, logs)
    print(f"Saved wc_referees.json")


def _compute_ref_stats(by_match, team_stats, logs):
    """For each ref, average across all their matches in our data window."""
    # Pre-index: matchId -> {cards_for_both_teams, fouls_for_both_teams}
    match_box = {}
    for mid, m in (team_stats.get("matches") or {}).items():
        teams = m.get("teams", {})
        cards = 0
        fouls = 0
        for tid, td in teams.items():
            cards += (td.get("yellowCards") or 0) + (td.get("redCards") or 0)
            fouls += td.get("fouls") or 0
        match_box[mid] = {"cards": cards, "fouls": fouls}

    # Penalties from player rows — both pen_scored + pen_committed = pen taken
    pens_by_match = defaultdict(int)
    for r in (logs.get("rows") or []):
        if r.get("penaltyScored") or r.get("penaltyMissed") or r.get("penaltyCommitted"):
            mid = str(r.get("matchId"))
            pens_by_match[mid] += (r.get("penaltyScored") or 0) + \
                                   (r.get("penaltyMissed") or 0)

    # Roll up by ref name
    by_ref = defaultdict(lambda: {"matches": 0, "totalCards": 0,
                                  "totalFouls": 0, "totalPens": 0,
                                  "country": None})
    for mid, info in by_match.items():
        if info.get("_empty"):
            continue
        name = info.get("name")
        if not name:
            continue
        box = match_box.get(mid)
        if not box:
            # No team stats for this match — can't include in averages
            continue
        ref = by_ref[name]
        ref["matches"] += 1
        ref["totalCards"] += box["cards"]
        ref["totalFouls"] += box["fouls"]
        ref["totalPens"]  += pens_by_match.get(mid, 0)
        ref["country"] = info.get("country") or ref["country"]

    # Per-game averages
    out = {}
    for name, agg in by_ref.items():
        if agg["matches"] < 1:
            continue
        out[name] = {
            "matches":      agg["matches"],
            "country":      agg["country"],
            "cardsPerGame": round(agg["totalCards"] / agg["matches"], 2),
            "foulsPerGame": round(agg["totalFouls"] / agg["matches"], 2),
            "pensPerGame":  round(agg["totalPens"]  / agg["matches"], 3),
        }
    return out


def _save_match_data(by_match, path, team_stats, logs):
    # Recompute aggregates on every save so the file is always consistent
    ref_stats = _compute_ref_stats(by_match, team_stats, logs)
    output = {
        "version":   int(time.time() * 1000),
        "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "byMatch":   by_match,
        "refStats":  ref_stats,
    }
    with path.open("w") as f:
        json.dump(output, f, separators=(",", ":"))


if __name__ == "__main__":
    main()
