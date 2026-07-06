#!/usr/bin/env python3
"""
dedup_bundle.py  —  one-time cleanup for the AFL game-log bundle.

Why: the older game-log rows used STRING MatchIds (e.g. "2026-R14-Port Adelaide-v-Sydney")
and carried CBA as a raw COUNT. The newer wheelo CSVs use NUMERIC MatchIds (e.g. "20261405")
and carry the real CentreBounceAttendancePercentage. Because the ingest upsert keys on
Year+MatchId+Player, the two never matched — so every 2026 player-round ended up with BOTH
a stale count row and a correct % row (~5,800 dupes), and the build picked the stale one.

This drops the stale duplicate rows, keeping the numeric-MatchId (real %) row per
player-round. If a player-round has ONLY a string-MatchId row (nothing to replace it),
it's left untouched so no data is lost.

Run once, then rebuild + commit:
    python AFL/dedup_bundle.py
    python AFL/build_afl_data.py --src AFL/bundle.json --out AFL/data \
        --seasons 2023,2024,2025,2026 --current 2026
    # commit AFL/bundle.json + AFL/data/
"""
import json, re, os, sys
from collections import defaultdict

BUNDLE = os.environ.get("AFL_BUNDLE", "AFL/bundle.json")

def round_num(s):
    s = str(s or "")
    if "pening" in s:            # "Opening Round" -> 0
        return 0
    m = re.search(r"(\d+)", s)
    return int(m.group(1)) if m else None

def is_numeric_id(mid):
    return bool(re.fullmatch(r"\d+", str(mid or "")))

def main():
    b = json.load(open(BUNDLE, encoding="utf-8"))

    # locate the game-log list: the big array carrying MatchId + RoundName + Player
    key = None
    for k, v in b.items():
        if isinstance(v, list) and len(v) > 10000 and v and isinstance(v[0], dict) \
           and {"MatchId", "RoundName", "Player"} <= set(v[0].keys()):
            key = k
            break
    if key is None:
        sys.exit("could not find the game-log list in the bundle")
    logs = b[key]

    groups = defaultdict(list)
    for i, r in enumerate(logs):
        groups[(str(r.get("Year")), r.get("Player"), round_num(r.get("RoundName")))].append(i)

    keep, dropped = [], 0
    for _, idxs in groups.items():
        has_numeric = any(is_numeric_id(logs[i].get("MatchId")) for i in idxs)
        for i in idxs:
            # keep the numeric-MatchId row; keep string-only rows when there's no numeric replacement
            if is_numeric_id(logs[i].get("MatchId")) or not has_numeric:
                keep.append(i)
            else:
                dropped += 1

    new_logs = [logs[i] for i in sorted(keep)]

    if dropped == 0:
        print("nothing to de-dupe — bundle already clean.")
        return

    # safety backup, then write
    bak = BUNDLE + ".predup.bak"
    if not os.path.exists(bak):
        os.replace(BUNDLE, bak)          # move original aside once
        json.dump(b, open(bak, "w"))     # (b still references old logs list; fine as a backup)
    b[key] = new_logs
    json.dump(b, open(BUNDLE, "w"))
    print(f"game logs {len(logs)} -> {len(new_logs)} (dropped {dropped} stale count rows)")
    print(f"backup written to {bak}")
    print("now run build_afl_data.py and commit.")

if __name__ == "__main__":
    main()
