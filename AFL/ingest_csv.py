#!/usr/bin/env python3
# ============================================================
# ingest_csv.py — wheeloratings game-log CSV -> bundle.json
# ============================================================
# Champion Data game-log rows (one player-match per row) are the master
# source: players, team for/against and DVP all DERIVE from them downstream
# in build_afl_data.py, advanced stats included. This script ingests one or
# many CSVs and UPSERTS them into bundle["dvp"] (the game-log array).
#
# Weekly flow (Windows + GitHub web UI friendly):
#   1. download the latest wheeloratings game-log CSV
#   2. drop / replace it under AFL/csv/  (commit via the web UI)
#   3. workflow runs ingest_csv.py -> build_afl_data.py -> split data
#
#  * UPSERT by (Year, MatchId, Player): re-uploading a fuller season CSV only
#    adds the new games and fixes any corrected rows — safe to re-run.
#  * Preserves EVERY column (advanced metrics like Equity_*, xScore, Rating
#    points ride along even if STAT_MAP doesn't map them yet).
#  * Header-tolerant: "Time On Ground" / "TimeOnGround" / "time_on_ground"
#    all resolve to the canonical bundle key.
#  * Non-destructive: a parse failure or empty input leaves bundle untouched;
#    other bundle keys (player, fixture, fgs, injury, meta) are preserved.
#  * Self-diagnosing: logs headers seen, matched vs preserved, new vs updated.
# ============================================================
import os, sys, csv, json, glob, re, hashlib, datetime

BUNDLE  = os.environ.get("AFL_BUNDLE", "AFL/bundle.json")
CSV_DIR = os.environ.get("CSV_DIR", "AFL/csv")
CSV_FILE = os.environ.get("CSV_FILE", "").strip()
# optional separate player export (wheelo player page) carrying Position/Age
PLAYER_DIR = os.environ.get("PLAYER_DIR", "AFL/csv_players")
PLAYER_CSV = os.environ.get("PLAYER_CSV", "").strip()
# optional wheelo team CSVs: Team averages -> teamform, Opposition averages -> teamdef
TEAMFORM_DIR = os.environ.get("TEAMFORM_DIR", "AFL/csv_team_for")
TEAMFORM_CSV = os.environ.get("TEAMFORM_CSV", "").strip()
TEAMDEF_DIR  = os.environ.get("TEAMDEF_DIR", "AFL/csv_team_against")
TEAMDEF_CSV  = os.environ.get("TEAMDEF_CSV", "").strip()

# Canonical Champion Data column names the bundle/build expect (PascalCase).
# Any CSV header that normalises to one of these is renamed to it; unknown
# headers are preserved verbatim so nothing is ever silently dropped.
CANONICAL = [
    "Year","RoundName","MatchId","Player","Team","Opponent","Position","Age","Age_Decimal",
    "CoachesVotes","RatingPoints","EstimatedRating",
    "Equity_PreClearance","Equity_PostClearance","Equity_Possession","Equity_BallUse",
    "Supercoach","DreamTeamPoints","TimeOnGround","Kicks","Handballs","Disposals",
    "DisposalEfficiency","MetresGained","AssistedMetresGained","Inside50s",
    "ContestedPossessions","GroundBallGets","PostClearanceContestedPossessions",
    "PostClearanceGroundBallGets","HandballReceives","Intercepts",
    "CentreBounceAttendancePercentage","TotalClearances","Marks","ContestedMarks",
    "InterceptMarks","ShotsAtGoal","Goals","Behinds","xScore","xScoreRating",
    "GoalAssists","ScoreInvolvements","ScoreLaunches","Tackles","PressureActs","Hitouts",
]
# common header aliases -> canonical (checked after generic normalisation)
ALIASES = {
    "round":"RoundName","roundname":"RoundName","rd":"RoundName",
    "match":"MatchId","matchid":"MatchId","gameid":"MatchId","game":"MatchId",
    "season":"Year","year":"Year","name":"Player","player":"Player","team":"Team",
    "opp":"Opponent","opponent":"Opponent","against":"Opponent",
    "clearances":"TotalClearances","totalclearances":"TotalClearances",
    "contestedpossessions":"ContestedPossessions","contested":"ContestedPossessions",
    "tog":"TimeOnGround","timeonground":"TimeOnGround",
    "cba":"CentreBounceAttendancePercentage",
    "sc":"Supercoach","supercoach":"Supercoach",
    "dt":"DreamTeamPoints","dreamteam":"DreamTeamPoints","dreamteampoints":"DreamTeamPoints",
    "i50":"Inside50s","inside50s":"Inside50s","inside50":"Inside50s",
    "pos":"Position","position":"Position","age":"Age","agedecimal":"Age_Decimal",
}

def _norm(h):
    return re.sub(r"[^a-z0-9]", "", (h or "").lower())

# build normalised -> canonical lookup
_CANON_BY_NORM = {_norm(c): c for c in CANONICAL}
for a, c in ALIASES.items():
    _CANON_BY_NORM.setdefault(_norm(a), c)

def resolve_header(h):
    n = _norm(h)
    return _CANON_BY_NORM.get(n, h)  # keep verbatim if unknown (preserve)

def dedup_key(row):
    yr = str(row.get("Year", "")).strip()
    pl = str(row.get("Player", "")).strip()
    mid = str(row.get("MatchId", "")).strip()
    if mid:
        return (yr, mid, pl)
    # no MatchId: derive a stable one if we know the opponent, else fall back
    rd = str(row.get("RoundName", "")).strip()
    tm = str(row.get("Team", "")).strip()
    opp = str(row.get("Opponent", "")).strip()
    if opp:
        mid = "WO" + hashlib.md5(("|".join([yr, rd] + sorted([tm, opp]))).encode()).hexdigest()[:8]
        row["MatchId"] = mid          # backfill so downstream match-pairing works
        return (yr, mid, pl)
    return (yr, rd, pl)               # last resort (no match grouping possible)

def load_bundle():
    if os.path.exists(BUNDLE):
        try:
            with open(BUNDLE, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[ingest] WARNING: bundle.json unreadable ({e}); starting a fresh one")
    return {}

def csv_files():
    files = []
    if CSV_FILE and os.path.exists(CSV_FILE):
        files.append(CSV_FILE)
    if os.path.isdir(CSV_DIR):
        files += sorted(glob.glob(os.path.join(CSV_DIR, "*.csv")))
    # de-dupe while keeping order
    seen, out = set(), []
    for f in files:
        if f not in seen:
            seen.add(f); out.append(f)
    return out

def read_csv(path, need_match=True):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        rdr = csv.reader(f)
        try:
            header = next(rdr)
        except StopIteration:
            return [], [], []
        resolved = [resolve_header(h) for h in header]
        matched = sorted({r for h, r in zip(header, resolved) if r in CANONICAL})
        preserved = sorted({h for h, r in zip(header, resolved) if r not in CANONICAL})
        for raw in rdr:
            if not any(c.strip() for c in raw):
                continue
            row = {}
            for col, val in zip(resolved, raw):
                v = (val or "").strip()
                if v != "":
                    row[col] = v
            ok = (row.get("Player") or row.get("Team")) and \
                 ((not need_match) or row.get("Year") or row.get("RoundName"))
            if ok:
                rows.append(row)
    return rows, matched, preserved


def player_csv_files():
    return _collect(PLAYER_CSV, PLAYER_DIR)


def _collect(single, folder):
    files = []
    if single and os.path.exists(single):
        files.append(single)
    if os.path.isdir(folder):
        files += sorted(glob.glob(os.path.join(folder, "*.csv")))
    seen, out = set(), []
    for f in files:
        if f not in seen:
            seen.add(f); out.append(f)
    return out


def ingest_team_table(bundle, key, files):
    """Upsert wheelo team CSV rows (keyed by Team) into bundle[key]. Headers are
    preserved verbatim; the build maps them to internal stats via WHEELO_MAP."""
    if not files:
        return 0
    existing = bundle.get(key, []) or []
    idx = {t.get("Team"): t for t in existing if t.get("Team")}
    n = 0
    for path in files:
        rows, mt, prv = read_csv(path, need_match=False)
        for r in rows:
            tm = r.get("Team")
            if not tm:
                continue
            if tm in idx:
                idx[tm].update(r)
            else:
                idx[tm] = r; existing.append(r)
            n += 1
        print(f"[ingest] {key} CSV {os.path.basename(path)}: {len(rows)} rows")
    bundle[key] = existing
    return n


def upsert_players(bundle, logs, player_rows):
    """Populate bundle['player'] (name -> Position/Age/Team) so build_afl_data's
    position join — and therefore DVP + position features — work. Sources, in
    increasing priority: game-log Position columns, then dedicated player CSVs."""
    info = {}
    for r in logs:                          # log-derived (most-recent wins via iteration order)
        if r.get("Position") and r.get("Player"):
            d = info.setdefault(r["Player"], {})
            d["Position"] = r["Position"]; d["Team"] = r.get("Team", d.get("Team"))
            if r.get("Age"):
                d["Age"] = r["Age"]
    for r in player_rows:                   # dedicated player export (priority + extra cols)
        nm = r.get("Player")
        if not nm:
            continue
        d = info.setdefault(nm, {})
        for k, v in r.items():
            if v not in (None, ""):
                d[k] = v
    if not info:
        return 0, 0
    existing = bundle.get("player", []) or []
    idx = {p.get("Player"): p for p in existing if p.get("Player")}
    added = updated = 0
    for nm, d in info.items():
        row = idx.get(nm)
        if row is None:
            row = {"Player": nm}; existing.append(row); idx[nm] = row; added += 1
        else:
            updated += 1
        for k, v in d.items():
            if v not in (None, ""):
                row[k] = v
    bundle["player"] = existing
    return added, updated


def main():
    files = csv_files()
    if not files:
        print(f"[ingest] no CSVs found (looked in {CSV_DIR}/ and CSV_FILE) — bundle untouched")
        return 0
    bundle = load_bundle()
    existing = bundle.get("dvp", []) or []
    index = {}
    for r in existing:
        index[dedup_key(dict(r))] = r

    before = len(index)
    new_ct = upd_ct = parsed = 0
    all_matched, all_preserved = set(), set()
    for path in files:
        rows, matched, preserved = read_csv(path)
        all_matched |= set(matched); all_preserved |= set(preserved)
        parsed += len(rows)
        for row in rows:
            k = dedup_key(row)
            if k in index:
                # upsert: merge new columns over the existing row
                index[k].update(row); upd_ct += 1
            else:
                index[k] = row; new_ct += 1
        print(f"[ingest] {os.path.basename(path)}: {len(rows)} rows "
              f"| matched {len(matched)} cols, preserved {len(preserved)}")

    if parsed == 0:
        print("[ingest] every CSV was empty — bundle untouched")
        return 0

    merged = list(index.values())
    bundle["dvp"] = merged

    # ---- positions: feed bundle['player'] so DVP + position features work ----
    prows = []
    for path in player_csv_files():
        r, mtc, prv = read_csv(path, need_match=False)
        prows += r
        print(f"[ingest] player CSV {os.path.basename(path)}: {len(r)} rows")
    p_add, p_upd = upsert_players(bundle, merged, prows)
    pos_known = sum(1 for p in (bundle.get("player") or []) if p.get("Position"))

    # ---- optional wheelo team CSVs: for (Team avgs) + against (Opp avgs) ----
    tf_n = ingest_team_table(bundle, "teamform", _collect(TEAMFORM_CSV, TEAMFORM_DIR))
    td_n = ingest_team_table(bundle, "teamdef",  _collect(TEAMDEF_CSV, TEAMDEF_DIR))

    # stamp ingest metadata without disturbing existing meta
    meta = bundle.get("meta", {}) or {}
    meta["gamelogIngest"] = {
        "at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "files": [os.path.basename(p) for p in files],
        "rows": len(merged),
    }
    bundle["meta"] = meta

    os.makedirs(os.path.dirname(BUNDLE) or ".", exist_ok=True)
    tmp = BUNDLE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(bundle, f)
    os.replace(tmp, BUNDLE)

    print(f"\n[ingest] matched canonical cols : {sorted(all_matched)}")
    if all_preserved:
        print(f"[ingest] preserved (unmapped)   : {sorted(all_preserved)}")
    print(f"[ingest] game logs {before} -> {len(merged)}  (+{new_ct} new, {upd_ct} updated)")
    print(f"[ingest] player blob: +{p_add} new, {p_upd} updated · {pos_known} with a position")
    if tf_n or td_n:
        print(f"[ingest] team tables: teamform {tf_n} rows, teamdef {td_n} rows")
    if pos_known == 0:
        print("[ingest] NOTE: no Position found in game logs or player CSVs — DVP and "
              "position features need positions. Add a Position column, or drop a "
              "player export into AFL/csv_players/.")
    print(f"[ingest] wrote {BUNDLE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
