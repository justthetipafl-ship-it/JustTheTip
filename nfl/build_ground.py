#!/usr/bin/env python3
"""The ground game: what a back actually does with a carry, and what a defence allows.

    python nfl/build_ground.py [--season 2026] [--out nfl/data] [--min-att 40]
    python nfl/build_ground.py --selftest        # no download; checks the maths

WHAT THIS IS
Three things the tool could not say about a running back, all of them derivable from feeds the
NFL pipeline already pulls:

  RUNNER EVIDENCE   what he does with the ball - yards after contact, broken tackles, rush yards
                    over expected, success rate, how often he breaks 10 and 15, how often he is
                    stopped at or behind the line - each against every other qualified back.
  DEFENCE MATCHUP   the same questions asked of the defence he is about to face.
  RUSH PATH         where his carries actually go, lane by lane, against how that defence holds
                    up in each lane.

WHERE EACH NUMBER COMES FROM, AND WHY IT MATTERS THAT THEY DIFFER
  play-by-play (nflverse)   success, 10+, 15+, stuff avoidance, rush TDs, lanes, short yardage
  PFR advanced rushing      yards after contact, broken tackles  (and the same, allowed)
  Next Gen Stats rushing    rush yards over expected per attempt, share of carries into 8+ box

The three sources do not agree, and should not be expected to. Yards after contact is a charting
judgement: PFR's number for a back can sit a yard below a tracking-based one because the two draw
the contact point differently. So each figure is labelled with the source it came from rather than
blended into one "yards after contact" that belongs to nobody.

WHAT IS NOT HERE
Run scheme - zone against gap, inside zone against outside zone. nflverse does not carry it, FTN's
charting frame has no scheme column, and inventing a classifier from down, distance and personnel
would be a guess presented as a fact. The lane split below is measured; a scheme split would not be.

QUALIFYING
A back needs --min-att carries before he gets a percentile. Below that the rate statistics are
mostly noise - one broken tackle in six carries is 0.17 per attempt and means nothing - so he is
written with his raw figures and no ranking, and the page says so rather than ranking him anyway.

ATTRIBUTION: play-by-play from nflverse; the charting under it is CC-BY-SA 4.0 and must be
credited to FTN Data via nflverse wherever these numbers are shown.
"""
import argparse, datetime, json, os, re, sys

MIN_ATT_DEFAULT = 40
# Short yardage: 3rd or 4th down needing 2 or less. That is the down the "will he be stopped"
# question is actually asked on, rather than every carry inside 2 yards of a first.
SHORT_YDS = 2


def norm_name(s):
    """PFR <-> nflverse name key: lowercase, letters only, suffixes stripped.

    PFR writes "Brian Robinson Jr." where nflverse has "Brian Robinson"; the join is on the name
    because the two carry different id spaces. Suffixes are dropped LAST so "Jr" inside a surname
    is not eaten - and the result is only ever used to join, never shown.
    """
    s = re.sub(r"[^a-z ]", "", str(s or "").lower())
    s = re.sub(r"\s+(jr|sr|ii|iii|iv|v)$", "", s.strip())
    return s.replace(" ", "")


def lane_of(loc, gap):
    """nflverse run_location (left/middle/right) + run_gap (guard/tackle/end) -> one of seven lanes.

    A middle run carries no gap, which is correct rather than missing: there is no guard/tackle/end
    distinction up the gut. Seven lanes is what the data supports; collapsing to four would throw
    away the end runs.
    """
    if loc == "middle":
        return "MID"
    if loc in ("left", "right") and gap in ("guard", "tackle", "end"):
        return ("L" if loc == "left" else "R") + {"guard": "G", "tackle": "T", "end": "E"}[gap]
    return None


LANES = ["LE", "LT", "LG", "MID", "RG", "RT", "RE"]


def pct_rank(value, pool, higher_is_better=True):
    """Where this value sits among the pool, 0-100. None when there is nothing to compare against."""
    vals = [v for v in pool if v is not None]
    if value is None or len(vals) < 2:
        return None
    beaten = sum(1 for v in vals if (v < value if higher_is_better else v > value))
    return round(beaten / len(vals) * 100)


def rank_of(value, pool, softest_first=True):
    """1-based rank. softest_first means rank 1 is the one that allows the most."""
    vals = sorted([v for v in pool if v is not None], reverse=softest_first)
    if value is None or not vals:
        return None, len(vals)
    # ties take the best position, as a league table does
    return vals.index(value) + 1, len(vals)


def build(season, out_dir, min_att):
    import nflreadpy as nfl
    import polars as pl

    print(f"loading nflverse frames for {season} ...")
    pbp = nfl.load_pbp(seasons=[season])
    runs = pbp.filter((pl.col("rush_attempt") == 1) & pl.col("rusher_player_id").is_not_null())
    if runs.shape[0] == 0:
        raise SystemExit(f"no rushing plays for {season} - nothing to build")
    through = int(runs.select(pl.col("week").max()).item() or 0)
    print(f"  {runs.shape[0]} rush attempts through week {through}")

    runs = runs.with_columns([
        pl.struct(["run_location", "run_gap"]).map_elements(
            lambda d: lane_of(d["run_location"], d["run_gap"]), return_dtype=pl.Utf8).alias("lane"),
        # Stuffed: no gain. nflverse has tackled_for_loss but that misses the carry stopped for
        # exactly nothing, which is the same failure from the runner's point of view.
        (pl.col("yards_gained") <= 0).alias("stuffed"),
        ((pl.col("down") >= 3) & (pl.col("ydstogo") <= SHORT_YDS)).alias("short"),
    ])

    # ---- per runner, from play-by-play ------------------------------------------------------
    by_run = runs.group_by(["rusher_player_id", "rusher_player_name"]).agg([
        pl.len().alias("att"),
        pl.col("success").mean().alias("succ"),
        (pl.col("yards_gained") >= 10).mean().alias("r10"),
        (pl.col("yards_gained") >= 15).mean().alias("r15"),
        (~pl.col("stuffed")).mean().alias("stuffAvoid"),
        pl.col("yards_gained").mean().alias("ypc"),
        pl.col("rush_touchdown").sum().alias("tds"),
        pl.col("posteam").last().alias("team"),
    ])

    # ---- PFR advanced: contact and broken tackles -------------------------------------------
    pfr = nfl.load_pfr_advstats(seasons=[season], stat_type="rush", summary_level="week")
    pf = pfr.group_by("pfr_player_name").agg([
        pl.col("carries").sum().alias("c"),
        pl.col("rushing_yards_after_contact").sum().alias("yac"),
        pl.col("rushing_broken_tackles").sum().alias("brk"),
    ])
    pfr_by_name = {}
    for row in pf.iter_rows(named=True):
        c = row["c"] or 0
        if c <= 0:
            continue
        pfr_by_name[norm_name(row["pfr_player_name"])] = {
            "carries": int(c),
            "yacAtt": (row["yac"] or 0) / c,
            "brkAtt": (row["brk"] or 0) / c,
        }

    # ---- Next Gen Stats: over expected, and the box he runs into ----------------------------
    ngs_all = nfl.load_nextgen_stats(seasons=[season], stat_type="rushing")
    # week 0 is the season-to-date row
    ngs = ngs_all.filter(pl.col("week") == 0)
    ngs_by_id, ngs_by_name = {}, {}
    for row in ngs.iter_rows(named=True):
        rec = {"ryoeAtt": row.get("rush_yards_over_expected_per_att"),
               "box8": row.get("percent_attempts_gte_eight_defenders"),
               "att": row.get("rush_attempts")}
        if row.get("player_gsis_id"):
            ngs_by_id[row["player_gsis_id"]] = rec
        ngs_by_name[norm_name(row.get("player_display_name"))] = rec

    # ---- stitch, keyed by the gsis id play-by-play already uses ------------------------------
    runners = {}
    for row in by_run.iter_rows(named=True):
        pid = row["rusher_player_id"]
        short = row["rusher_player_name"]          # pbp writes "W.Marks"
        ng = ngs_by_id.get(pid)
        # pbp's abbreviated name cannot be normalised to PFR's full one, so reach the full name
        # through NGS (which carries both the gsis id and the display name) and join on that.
        full = None
        for r2 in ngs.iter_rows(named=True):
            if r2.get("player_gsis_id") == pid:
                full = r2.get("player_display_name"); break
        pfrrec = pfr_by_name.get(norm_name(full)) if full else None
        lanes = {}
        sub = runs.filter(pl.col("rusher_player_id") == pid)
        lc = sub.group_by("lane").len()
        for a, b in zip(lc["lane"], lc["len"]):
            if a is not None:
                lanes[str(a)] = int(b)
        mapped = sum(lanes.values())
        runners[pid] = {
            "name": full or short, "short": short, "team": row["team"],
            "att": int(row["att"]), "ypc": row["ypc"], "tds": int(row["tds"] or 0),
            "succ": row["succ"], "r10": row["r10"], "r15": row["r15"],
            "stuffAvoid": row["stuffAvoid"],
            "yacAtt": (pfrrec or {}).get("yacAtt"),
            "brkAtt": (pfrrec or {}).get("brkAtt"),
            "ryoeAtt": (ng or {}).get("ryoeAtt"),
            "box8": (ng or {}).get("box8"),
            "lanes": lanes, "mapped": mapped,
            "qualified": int(row["att"]) >= min_att,
        }

    # ---- percentiles, over the QUALIFIED pool only ------------------------------------------
    pool = [r for r in runners.values() if r["qualified"]]
    print(f"  {len(runners)} runners, {len(pool)} with {min_att}+ carries")
    METRICS = [("yacAtt", True), ("brkAtt", True), ("ryoeAtt", True), ("succ", True),
               ("r10", True), ("r15", True), ("stuffAvoid", True), ("ypc", True),
               ("box8", False)]     # a heavier box faced is a harder job, not a better back
    for key, higher in METRICS:
        vals = [r[key] for r in pool if r.get(key) is not None]
        for r in runners.values():
            r.setdefault("pct", {})
            r["pct"][key] = pct_rank(r.get(key), vals, higher) if r["qualified"] else None

    # ---- per defence -------------------------------------------------------------------------
    by_def = runs.group_by("defteam").agg([
        pl.len().alias("att"),
        pl.col("success").mean().alias("succAllowed"),
        (pl.col("yards_gained") >= 10).mean().alias("r10Allowed"),
        pl.col("stuffed").mean().alias("stuffCreated"),
        pl.col("yards_gained").mean().alias("ypcAllowed"),
        pl.col("rush_touchdown").mean().alias("tdAllowed"),
    ])
    shorts = runs.filter(pl.col("short")).group_by("defteam").agg([
        pl.len().alias("shortAtt"), pl.col("stuffed").mean().alias("shortStop")])
    short_map = {a: {"n": int(b), "stop": c}
                 for a, b, c in zip(shorts["defteam"], shorts["shortAtt"], shorts["shortStop"])}

    pfr_def = pfr.group_by("opponent").agg([
        pl.col("carries").sum().alias("c"),
        pl.col("rushing_yards_after_contact").sum().alias("yac"),
        pl.col("rushing_broken_tackles").sum().alias("brk")])
    pdef = {}
    for row in pfr_def.iter_rows(named=True):
        c = row["c"] or 0
        if c > 0:
            pdef[row["opponent"]] = {"yacAllowed": (row["yac"] or 0) / c,
                                     "brkAllowed": (row["brk"] or 0) / c}

    defence = {}
    for row in by_def.iter_rows(named=True):
        t = row["defteam"]
        sm = short_map.get(t, {})
        lanes = {}
        sub = runs.filter(pl.col("defteam") == t)
        lg = sub.group_by("lane").agg([pl.len().alias("n"),
                                       pl.col("yards_gained").mean().alias("ypc"),
                                       pl.col("success").mean().alias("succ")])
        for a, n, y, s in zip(lg["lane"], lg["n"], lg["ypc"], lg["succ"]):
            if a is not None:
                lanes[str(a)] = {"n": int(n), "ypc": y, "succ": s}
        defence[t] = {
            "att": int(row["att"]),
            "succAllowed": row["succAllowed"], "r10Allowed": row["r10Allowed"],
            "stuffCreated": row["stuffCreated"], "ypcAllowed": row["ypcAllowed"],
            "tdAllowed": row["tdAllowed"],
            "shortStop": sm.get("stop"), "shortAtt": sm.get("n", 0),
            "yacAllowed": (pdef.get(t) or {}).get("yacAllowed"),
            "brkAllowed": (pdef.get(t) or {}).get("brkAllowed"),
            "lanes": lanes,
        }

    # ranks: 1 = softest, which is the direction the reader cares about for an over
    DEF_SOFTEST_HIGH = ["succAllowed", "r10Allowed", "ypcAllowed", "tdAllowed",
                        "yacAllowed", "brkAllowed"]
    DEF_SOFTEST_LOW = ["stuffCreated", "shortStop"]
    for key in DEF_SOFTEST_HIGH + DEF_SOFTEST_LOW:
        vals = [d[key] for d in defence.values() if d.get(key) is not None]
        for d in defence.values():
            d.setdefault("rank", {})
            r, n = rank_of(d.get(key), vals, softest_first=(key in DEF_SOFTEST_HIGH))
            d["rank"][key] = r
            d["of"] = n
    # and a lane-by-lane rank, so "he runs right and they are soft right" is answerable
    for lane in LANES:
        vals = [d["lanes"][lane]["ypc"] for d in defence.values()
                if d["lanes"].get(lane) and d["lanes"][lane]["n"] >= 5]
        for d in defence.values():
            cell = d["lanes"].get(lane)
            if not cell:
                continue
            if cell["n"] < 5:
                cell["rank"] = None            # too few carries down that lane to rank it
            else:
                cell["rank"], cell["of"] = rank_of(cell["ypc"], vals, softest_first=True)

    payload = {
        "updated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "season": season, "throughWeek": through, "minAtt": min_att,
        "lanes": LANES,
        "source": ("nflverse play-by-play (charting CC-BY-SA 4.0, credit FTN Data via nflverse); "
                   "Pro Football Reference advanced rushing; NFL Next Gen Stats"),
        "runners": runners, "defence": defence,
    }
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "ground.json")
    with open(path, "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"  wrote {path} ({os.path.getsize(path)/1024:.0f} KB) - "
          f"{len(runners)} runners, {len(defence)} defences")
    return payload


def selftest():
    ok = True

    def check(label, cond):
        nonlocal ok
        print(("  ok   " if cond else "  FAIL ") + label)
        ok = ok and bool(cond)

    check("middle runs carry no gap and still map", lane_of("middle", None) == "MID")
    check("left guard maps to LG", lane_of("left", "guard") == "LG")
    check("right end maps to RE", lane_of("right", "end") == "RE")
    check("an unlocated run maps to nothing", lane_of(None, None) is None)
    check("a left run with no gap maps to nothing", lane_of("left", None) is None)

    check("suffix stripped for the PFR join", norm_name("Brian Robinson Jr.") == "brianrobinson")
    check("punctuation stripped", norm_name("Ja'Marr Chase") == "jamarrchase")
    # the suffix rule must not eat a surname that merely ends in one of those letters
    check("a surname ending in v survives", norm_name("Nikola Jokiv") == "nikolajokiv")

    # "or True" would make this pass whatever the function did; the assertion has to bite
    check("a value above three of four sits at 75", pct_rank(10, [1, 2, 3, 10]) == 75)
    check("the bottom of the pool sits at 0", pct_rank(1, [1, 2, 3, 10]) == 0)
    check("higher is better ranks above the pool", pct_rank(9, [1, 5, 9]) == 67)
    check("lower is better inverts", pct_rank(1, [1, 5, 9], higher_is_better=False) == 67)
    check("no pool means no percentile", pct_rank(5, []) is None)
    check("no value means no percentile", pct_rank(None, [1, 2, 3]) is None)

    r, n = rank_of(0.5, [0.5, 0.4, 0.3], softest_first=True)
    check("softest-first puts the highest at rank 1", (r, n) == (1, 3))
    r, n = rank_of(0.3, [0.5, 0.4, 0.3], softest_first=True)
    check("and the lowest last", (r, n) == (3, 3))
    r, n = rank_of(0.3, [0.5, 0.4, 0.3], softest_first=False)
    check("toughest-first flips it", (r, n) == (1, 3))

    print("SELFTEST", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=datetime.date.today().year)
    ap.add_argument("--out", default="nfl/data")
    ap.add_argument("--min-att", type=int, default=MIN_ATT_DEFAULT)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())
    build(a.season, a.out, a.min_att)
