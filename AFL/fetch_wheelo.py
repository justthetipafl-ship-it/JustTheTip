#!/usr/bin/env python3
# ============================================================
# fetch_wheelo.py — pull wheeloratings match-stats JSON -> AFL/csv/*.csv
# ============================================================
# Replaces the manual "export wheelo CSV + commit" step. The site serves each
# round's player game logs as a static, column-oriented JSON:
#
#   https://www.wheeloratings.com/src/match_stats/table_data/{YYYY}{RR}.json
#       e.g. .../202616.json  =  2026 season, round 16, ALL games in the round
#       shape: {"Data":[{"MatchId":[...],"Player":[...], <stat cols...> }]}
#       MatchId encodes  YYYY RR GG  (e.g. 20261603 = 2026, round 16, game 3)
#
# This flattens the columns to one player-match row per line and writes a CSV
# under AFL/csv/ — exactly what ingest_csv.py already upserts by
# (Year, MatchId, Player). No browser, no manual export.
#
# Rounds:
#   * WHEELO_ROUNDS="15,16,17"  -> fetch exactly those
#   * otherwise auto: read AFL/data/meta.json "round" R and fetch [R-2 .. R],
#     a self-healing window (a missed week is caught next run; upsert dedupes).
# A round file that 404s (not played yet) is skipped, not fatal. A failed fetch
# never touches existing data — the build/commit steps just see no new CSV.
# ============================================================
import os, sys, json, csv, datetime, urllib.request, urllib.error

BASE     = "https://www.wheeloratings.com/src/match_stats/table_data/{ymd}.json"
OUT_DIR  = os.environ.get("CSV_DIR", "AFL/csv")
META     = os.environ.get("AFL_META", "AFL/data/meta.json")
YEAR     = int(os.environ.get("WHEELO_YEAR") or datetime.date.today().year)
UA       = "JTT-AFL-bot/1.0 (+https://justthetipaus.com; weekly game-log refresh)"
TIMEOUT  = 30


def _round_window():
    """Explicit WHEELO_ROUNDS, else [R-2..R] derived from meta.json's round."""
    env = os.environ.get("WHEELO_ROUNDS", "").strip()
    if env:
        return [int(x) for x in env.replace(" ", "").split(",") if x.strip().isdigit()]
    try:
        with open(META, encoding="utf-8") as f:
            r = int(json.load(f).get("round", 0))
        if r:
            return [n for n in (r - 2, r - 1, r) if n >= 1]
    except Exception as e:
        print(f"  (could not read {META}: {e})")
    return []


def fetch_round(year, rnd):
    url = BASE.format(ymd=f"{year}{rnd:02d}")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"round {rnd}: not posted yet (404) — skipping")
        else:
            print(f"round {rnd}: HTTP {e.code} — skipping")
        return None
    except Exception as e:
        print(f"round {rnd}: fetch failed ({e}) — skipping")
        return None

    block = payload.get("Data")
    cols = block[0] if isinstance(block, list) and block else block
    if not isinstance(cols, dict) or not cols:
        print(f"round {rnd}: unexpected JSON shape — skipping")
        return None

    keys = list(cols.keys())
    n = len(cols[keys[0]])
    # column-oriented -> row-oriented; stamp canonical Year/RoundName for the upsert key
    rows = []
    for i in range(n):
        row = {k: cols[k][i] for k in keys}
        row.setdefault("Year", year)
        row.setdefault("RoundName", f"Round {rnd}")
        rows.append(row)
    # header: canonical Year, RoundName first (the upsert key), then all source
    # columns, de-duped in case the JSON already carries Year/RoundName itself
    seen, ordered = set(), []
    for h in (["Year", "RoundName"] + keys):
        if h not in seen:
            seen.add(h); ordered.append(h)
    return ordered, rows


def main():
    rounds = _round_window()
    if not rounds:
        print("No rounds to fetch (set WHEELO_ROUNDS=15,16 or ensure AFL/data/meta.json has a round).")
        return 0
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"Season {YEAR} · rounds {rounds} · -> {OUT_DIR}")
    wrote = 0
    for rnd in rounds:
        res = fetch_round(YEAR, rnd)
        if not res:
            continue
        header, rows = res
        path = os.path.join(OUT_DIR, f"wheelo_{YEAR}_r{rnd:02d}.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
            w.writeheader(); w.writerows(rows)
        print(f"round {rnd}: {len(rows)} rows · {len(header)} cols -> {path}")
        print(f"  cols: {', '.join(header[:18])}{' …' if len(header) > 18 else ''}")
        wrote += 1
    print(f"done: {wrote}/{len(rounds)} round file(s) written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
