#!/usr/bin/env python3
"""The ground and air game: what a skill player does with the ball, and what a defence allows.

    python nfl/build_ground.py [--season 2026] [--out nfl/data] [--min-att 40]
    python nfl/build_ground.py --selftest        # no download; checks the maths

WHAT THIS IS
Three things the tool could not say about a running back, all of them derivable from feeds the
NFL pipeline already pulls:

  RUNNER EVIDENCE   what he does with the ball - yards after contact, broken tackles, rush yards
                    over expected, success rate, how often he breaks 10 and 15, how often he is
                    stopped at or behind the line - each against every other qualified back.
  RECEIVER EVIDENCE what he does with a target - the separation he wins, the cushion he is given,
                    how far downfield he works, yards after the catch against what was expected of
                    it, drops, broken tackles - each against every other qualified receiver.
  DEFENCE MATCHUP   the same questions asked of the defence he is about to face, run and pass.
  RUSH PATH         where his carries go, lane by lane, against how that defence holds each lane.
  TARGET MAP        where he is thrown the ball - side of the field by depth - against how that
                    defence holds up in each of those nine squares.

WHY ONE FILE AND NOT TWO
The file is still called ground.json, which is a rushing phrase for a payload that now carries the
passing game too. Renaming it would cost a new fetch, a config edit and a workflow edit for no
gain the reader can see, so the name stayed and this paragraph exists instead.

WHERE EACH NUMBER COMES FROM, AND WHY IT MATTERS THAT THEY DIFFER
  play-by-play (nflverse)   success, 10+, 15+, stuff avoidance, rush TDs, lanes, short yardage;
                            targets, catch rate, air yards, yards after catch, the target map
  PFR advanced rush/rec     yards after contact, broken tackles, drops  (and the same, allowed)
  Next Gen Stats            rush yards over expected, 8+ box share; separation, cushion, intended
                            air yards, yards after catch above expectation

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
MIN_TGT_DEFAULT = 25     # a receiver's rates settle sooner than a back's; 25 targets is the bar
MIN_DB_DEFAULT = 60      # a starting quarterback passes this inside two games; a backup does not
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

# The target map. Depth bands are the ones the sport itself talks in: behind or at the line, the
# short stuff, the intermediate throw, and the shot downfield.
DEPTHS = [("screen", -99, 0), ("short", 0, 10), ("mid", 10, 20), ("deep", 20, 999)]
SIDES = ["left", "middle", "right"]
ZONES = [s[0].upper() + "-" + d[0] for s in SIDES for d in DEPTHS]


def zone_of(loc, air):
    """pass_location x air_yards -> one of twelve squares, or None when either is missing."""
    if loc not in SIDES or air is None:
        return None
    for name, lo, hi in DEPTHS:
        if lo <= air < hi:
            return loc[0].upper() + "-" + name
    return None


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


def build(season, out_dir, min_att, min_tgt, min_db):
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

    # ---- RECEIVERS ---------------------------------------------------------------------------
    # A target is the unit here, not a catch: a man thrown at six times and catching one has been
    # used six times, and a catch-only view would hide exactly the thing worth knowing.
    tg = pbp.filter(pl.col("receiver_player_id").is_not_null()).with_columns([
        pl.struct(["pass_location", "air_yards"]).map_elements(
            lambda d: zone_of(d["pass_location"], d["air_yards"]), return_dtype=pl.Utf8).alias("zone"),
    ])
    team_tg = {a: b for a, b in zip(*[tg.group_by("posteam").len()[c] for c in ("posteam", "len")])}
    team_air = {a: b for a, b in zip(*[tg.group_by("posteam").agg(
        pl.col("air_yards").sum().alias("ay"))[c] for c in ("posteam", "ay")])}

    by_rec = tg.group_by(["receiver_player_id", "receiver_player_name"]).agg([
        pl.len().alias("tgt"),
        pl.col("complete_pass").mean().alias("catchRate"),
        pl.col("air_yards").mean().alias("adot"),
        pl.col("air_yards").sum().alias("airYds"),
        pl.col("yards_gained").sum().alias("yds"),
        pl.col("yards_after_catch").sum().alias("yacSum"),
        pl.col("complete_pass").sum().alias("rec"),
        pl.col("pass_touchdown").sum().alias("tds"),
        (pl.col("air_yards") >= 20).mean().alias("deepRate"),
        pl.col("posteam").last().alias("team"),
    ])

    pfr_rec = nfl.load_pfr_advstats(seasons=[season], stat_type="rec", summary_level="week")
    pr = pfr_rec.group_by("pfr_player_name").agg([
        pl.col("receiving_broken_tackles").sum().alias("brk"),
        pl.col("receiving_drop").sum().alias("drops"),
        pl.col("receiving_drop_pct").mean().alias("dropPct")])
    pfrrec_by_name = {norm_name(r["pfr_player_name"]): r for r in pr.iter_rows(named=True)}

    ngs_rec = nfl.load_nextgen_stats(seasons=[season], stat_type="receiving").filter(pl.col("week") == 0)
    ngsrec_by_id, ngsrec_name = {}, {}
    for row in ngs_rec.iter_rows(named=True):
        rec = {"sep": row.get("avg_separation"), "cush": row.get("avg_cushion"),
               "iay": row.get("avg_intended_air_yards"),
               "airShare": row.get("percent_share_of_intended_air_yards"),
               "yacOE": row.get("avg_yac_above_expectation"),
               "name": row.get("player_display_name")}
        if row.get("player_gsis_id"):
            ngsrec_by_id[row["player_gsis_id"]] = rec
        ngsrec_name[norm_name(row.get("player_display_name"))] = rec

    receivers = {}
    for row in by_rec.iter_rows(named=True):
        pid = row["receiver_player_id"]
        ng = ngsrec_by_id.get(pid)
        full = (ng or {}).get("name")
        pfrrec = pfrrec_by_name.get(norm_name(full)) if full else None
        rec_n = int(row["rec"] or 0)
        zones = {}
        sub = tg.filter(pl.col("receiver_player_id") == pid)
        zc = sub.group_by("zone").len()
        for a, b in zip(zc["zone"], zc["len"]):
            if a is not None:
                zones[str(a)] = int(b)
        tm = row["team"]
        receivers[pid] = {
            "name": full or row["receiver_player_name"], "short": row["receiver_player_name"],
            "team": tm, "tgt": int(row["tgt"]), "rec": rec_n, "tds": int(row["tds"] or 0),
            "catchRate": row["catchRate"], "adot": row["adot"],
            "ypt": (row["yds"] or 0) / max(1, row["tgt"]),
            "yacRec": (row["yacSum"] or 0) / rec_n if rec_n else None,
            "deepRate": row["deepRate"],
            # share of his own side's targets and air yards - the usage half of the question
            "tgtShare": row["tgt"] / team_tg[tm] if team_tg.get(tm) else None,
            "airShare": ((row["airYds"] or 0) / team_air[tm]) if team_air.get(tm) else None,
            "sep": (ng or {}).get("sep"), "cush": (ng or {}).get("cush"),
            "yacOE": (ng or {}).get("yacOE"),
            "brkRec": ((pfrrec or {}).get("brk") / rec_n) if (pfrrec and rec_n) else None,
            "dropPct": ((pfrrec or {}).get("drops") / max(1, row["tgt"])) if pfrrec else None,
            "zones": zones, "mapped": sum(zones.values()),
            "qualified": int(row["tgt"]) >= min_tgt,
        }
    rpool = [x for x in receivers.values() if x["qualified"]]
    print(f"  {len(receivers)} receivers, {len(rpool)} with {min_tgt}+ targets")
    # Cushion is how much room the corner gives him at the snap. More room is an EASIER job
    # underneath, so it ranks upward - the first version had it the other way, which marked a man
    # being pressed as the one in the good spot.
    RMETRICS = [("sep", True), ("cush", True), ("adot", True), ("catchRate", True),
                ("ypt", True), ("yacRec", True), ("yacOE", True), ("brkRec", True),
                ("dropPct", False), ("tgtShare", True), ("airShare", True), ("deepRate", True)]
    for keym, higher in RMETRICS:
        vals = [x[keym] for x in rpool if x.get(keym) is not None]
        for x in receivers.values():
            x.setdefault("pct", {})
            x["pct"][keym] = pct_rank(x.get(keym), vals, higher) if x["qualified"] else None

    # ---- PASSERS -----------------------------------------------------------------------------
    # A dropback is the unit, not an attempt: a sack is a dropback that went wrong, and counting
    # only attempts hides the thing that decides whether a passing prop lands.
    db = pbp.filter(pl.col("passer_player_id").is_not_null() & (pl.col("qb_dropback") == 1))
    by_qb = db.group_by(["passer_player_id", "passer_player_name"]).agg([
        pl.len().alias("db"),
        pl.col("sack").mean().alias("sackRate"),
        pl.col("qb_hit").mean().alias("hitRate"),
        pl.col("interception").mean().alias("intRate"),
        pl.col("pass_touchdown").mean().alias("tdRate"),
        pl.col("cpoe").mean().alias("cpoePbp"),
        pl.col("air_yards").mean().alias("adot"),
        (pl.col("air_yards") >= 20).mean().alias("deepRate"),
        pl.col("shotgun").mean().alias("shotgun"),
        pl.col("posteam").last().alias("team"),
    ])
    pfr_pass = nfl.load_pfr_advstats(seasons=[season], stat_type="pass", summary_level="week")
    pp = pfr_pass.group_by("pfr_player_name").agg([
        pl.col("times_pressured").sum().alias("pressed"),
        pl.col("times_blitzed").sum().alias("blitzed"),
        pl.col("times_hurried").sum().alias("hurried"),
        pl.col("passing_bad_throws").sum().alias("bad"),
        pl.col("passing_drops").sum().alias("drops")])
    pfrpass_by_name = {norm_name(r["pfr_player_name"]): r for r in pp.iter_rows(named=True)}

    ngs_pass = nfl.load_nextgen_stats(seasons=[season], stat_type="passing").filter(pl.col("week") == 0)
    ngsp_by_id, = ({},)
    for row in ngs_pass.iter_rows(named=True):
        if row.get("player_gsis_id"):
            ngsp_by_id[row["player_gsis_id"]] = row

    passers = {}
    for row in by_qb.iter_rows(named=True):
        pid = row["passer_player_id"]
        ng = ngsp_by_id.get(pid) or {}
        full = ng.get("player_display_name")
        pfrrec = pfrpass_by_name.get(norm_name(full)) if full else None
        dbn = int(row["db"])
        zones = {}
        sub = db.filter((pl.col("passer_player_id") == pid) & pl.col("air_yards").is_not_null())
        zc = sub.with_columns(
            pl.struct(["pass_location", "air_yards"]).map_elements(
                lambda d: zone_of(d["pass_location"], d["air_yards"]), return_dtype=pl.Utf8).alias("z")
        ).group_by("z").len()
        for a, b in zip(zc["z"], zc["len"]):
            if a is not None:
                zones[str(a)] = int(b)
        passers[pid] = {
            "name": full or row["passer_player_name"], "short": row["passer_player_name"],
            "team": row["team"], "db": dbn,
            "sackRate": row["sackRate"], "hitRate": row["hitRate"],
            "intRate": row["intRate"], "tdRate": row["tdRate"],
            "adot": row["adot"], "deepRate": row["deepRate"], "shotgun": row["shotgun"],
            # NGS carries the tracking-derived half: how long he holds it, how tight the windows
            # he throws into, and completion against what the throw deserved
            "ttt": ng.get("avg_time_to_throw"),
            "aggr": ng.get("aggressiveness"),
            "toSticks": ng.get("avg_air_yards_to_sticks"),
            "cpoe": ng.get("completion_percentage_above_expectation"),
            "xcmp": ng.get("expected_completion_percentage"),
            "rating": ng.get("passer_rating"),
            # PFR counts the pressure. Rates are against DROPBACKS from play-by-play rather than
            # an average of weekly percentages, which would weight a four-dropback game the same
            # as a forty-dropback one.
            "pressRate": ((pfrrec or {}).get("pressed") / dbn) if (pfrrec and dbn) else None,
            "blitzRate": ((pfrrec or {}).get("blitzed") / dbn) if (pfrrec and dbn) else None,
            "badThrow": ((pfrrec or {}).get("bad") / dbn) if (pfrrec and dbn) else None,
            "zones": zones, "mapped": sum(zones.values()),
            "qualified": dbn >= min_db,
        }
    qpool = [x for x in passers.values() if x["qualified"]]
    print(f"  {len(passers)} passers, {len(qpool)} with {min_db}+ dropbacks")
    QMETRICS = [("cpoe", True), ("rating", True), ("adot", True), ("deepRate", True),
                ("toSticks", True), ("aggr", True), ("ttt", True), ("tdRate", True),
                ("intRate", False), ("sackRate", False), ("pressRate", False),
                ("blitzRate", False), ("badThrow", False)]
    for keym, higher in QMETRICS:
        vals = [x[keym] for x in qpool if x.get(keym) is not None]
        for x in passers.values():
            x.setdefault("pct", {})
            x["pct"][keym] = pct_rank(x.get(keym), vals, higher) if x["qualified"] else None

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
    # the same defence, asked about the pass
    by_pd = tg.group_by("defteam").agg([
        pl.len().alias("tgtFaced"),
        pl.col("complete_pass").mean().alias("cmpAllowed"),
        pl.col("yards_gained").mean().alias("yptAllowed"),
        pl.col("air_yards").mean().alias("adotFaced"),
        pl.col("pass_touchdown").mean().alias("passTdAllowed"),
        pl.col("interception").mean().alias("intRate"),
    ])
    pfr_pd = pfr_rec.group_by("opponent").agg([
        pl.col("receiving_broken_tackles").sum().alias("brk"),
        pl.col("receiving_drop").sum().alias("drops")])
    pd_map = {r["opponent"]: r for r in pfr_pd.iter_rows(named=True)}
    yac_pd = tg.filter(pl.col("complete_pass") == 1).group_by("defteam").agg(
        pl.col("yards_after_catch").mean().alias("yacAllowedRec"))
    yac_map = {a: b for a, b in zip(yac_pd["defteam"], yac_pd["yacAllowedRec"])}
    for row in by_pd.iter_rows(named=True):
        t = row["defteam"]
        d = defence.setdefault(t, {})
        d.update({
            "tgtFaced": int(row["tgtFaced"]),
            "cmpAllowed": row["cmpAllowed"], "yptAllowed": row["yptAllowed"],
            "adotFaced": row["adotFaced"], "passTdAllowed": row["passTdAllowed"],
            "intRate": row["intRate"], "yacAllowedRec": yac_map.get(t),
            "recBrkAllowed": ((pd_map.get(t) or {}).get("brk")),
        })
        zones = {}
        sub = tg.filter(pl.col("defteam") == t)
        zc = sub.group_by("zone").agg([pl.len().alias("n"),
                                       pl.col("yards_gained").mean().alias("ypt"),
                                       pl.col("complete_pass").mean().alias("cmp")])
        for a, n, y, c in zip(zc["zone"], zc["n"], zc["ypt"], zc["cmp"]):
            if a is not None:
                zones[str(a)] = {"n": int(n), "ypt": y, "cmp": c}
        d["zones"] = zones
    # the pass rush, from the defence's side of the same dropbacks
    by_rush = db.group_by("defteam").agg([
        pl.len().alias("dbFaced"),
        pl.col("sack").mean().alias("sackRateD"),
        pl.col("qb_hit").mean().alias("hitRateD"),
    ])
    # PFR ships def_times_blitzed, def_times_hurried and def_times_hitqb, and in this season
    # every one of those is null on all 213 rows - the columns exist and carry nothing. Summing
    # nulls gave 0, which then RANKED as "softest in the league", so a defence we know nothing
    # about was being presented as one that never blitzes. The same facts are on the offensive
    # side of the very same table: times_blitzed is what that quarterback faced, and `opponent`
    # is the defence that did it. 
    # PFR ships def_times_blitzed, def_times_hurried and def_times_hitqb, and this season every
    # one of those is null on all 213 rows - the columns exist and carry nothing. Summing nulls
    # gave 0, which then RANKED as "softest in the league", so a defence we know nothing about was
    # being presented as one that never blitzes. The same facts sit on the offensive side of the
    # very same table: times_blitzed is what that quarterback faced, and `opponent` is the defence
    # that did it to him.
    pfr_rush_d = pfr_pass.group_by("opponent").agg([
        pl.col("times_blitzed").sum().alias("blitz"),
        pl.col("times_hurried").sum().alias("hurry")])
    prd = {r["opponent"]: r for r in pfr_rush_d.iter_rows(named=True)}
    for row in by_rush.iter_rows(named=True):
        t = row["defteam"]
        d = defence.setdefault(t, {})
        n = int(row["dbFaced"])
        d.update({"dbFaced": n, "sackRateD": row["sackRateD"], "hitRateD": row["hitRateD"],
                  "blitzRateD": ((prd.get(t) or {}).get("blitz") / n) if (prd.get(t) and n) else None,
                  "hurryRateD": ((prd.get(t) or {}).get("hurry") / n) if (prd.get(t) and n) else None})

    # ranks: 1 = softest, which is the direction the reader cares about for an over
    DEF_SOFTEST_HIGH = ["succAllowed", "r10Allowed", "ypcAllowed", "tdAllowed",
                        "yacAllowed", "brkAllowed",
                        "cmpAllowed", "yptAllowed", "passTdAllowed", "yacAllowedRec"]
    # a defence that intercepts more is a HARDER one to throw at, so rank 1 (softest) is the lowest
    # a defence that sacks, hits, blitzes and hurries more is a HARDER one to throw from, so the
    # softest (rank 1) is the one that does least of it
    DEF_SOFTEST_LOW = ["stuffCreated", "shortStop", "intRate",
                       "sackRateD", "hitRateD", "blitzRateD", "hurryRateD"]
    for key in DEF_SOFTEST_HIGH + DEF_SOFTEST_LOW:
        vals = [d[key] for d in defence.values() if d.get(key) is not None]
        for d in defence.values():
            d.setdefault("rank", {})
            r, n = rank_of(d.get(key), vals, softest_first=(key in DEF_SOFTEST_HIGH))
            d["rank"][key] = r
            d["of"] = n
    # and a square-by-square rank, so "he works the deep left and they are soft there" is answerable
    for z in ZONES:
        vals = [d["zones"][z]["ypt"] for d in defence.values()
                if d.get("zones", {}).get(z) and d["zones"][z]["n"] >= 5]
        for d in defence.values():
            cell = d.get("zones", {}).get(z)
            if not cell:
                continue
            if cell["n"] < 5:
                cell["rank"] = None
            else:
                cell["rank"], cell["of"] = rank_of(cell["ypt"], vals, softest_first=True)
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
        "season": season, "throughWeek": through, "minAtt": min_att, "minTgt": min_tgt,
        "minDb": min_db,
        "lanes": LANES, "zones": ZONES, "sides": SIDES,
        "depths": [d[0] for d in DEPTHS],
        "source": ("nflverse play-by-play (charting CC-BY-SA 4.0, credit FTN Data via nflverse); "
                   "Pro Football Reference advanced rushing; NFL Next Gen Stats"),
        "runners": runners, "receivers": receivers, "passers": passers, "defence": defence,
    }
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "ground.json")
    with open(path, "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"  wrote {path} ({os.path.getsize(path)/1024:.0f} KB) - "
          f"{len(runners)} runners, {len(receivers)} receivers, {len(passers)} passers, "
          f"{len(defence)} defences")
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

    check("a screen behind the line maps to the screen band", zone_of("left", -3) == "L-screen")
    check("a 5-yard target is short", zone_of("middle", 5) == "M-short")
    check("a 15-yard target is intermediate", zone_of("right", 15) == "R-mid")
    check("a 20-yard target is deep", zone_of("right", 20) == "R-deep")
    check("the band boundary belongs to the band above", zone_of("left", 10) == "L-mid")
    check("no air yards means no square", zone_of("left", None) is None)
    check("no side means no square", zone_of(None, 5) is None)
    check("twelve squares in all", len(ZONES) == 12)

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
    ap.add_argument("--min-tgt", type=int, default=MIN_TGT_DEFAULT)
    ap.add_argument("--min-db", type=int, default=MIN_DB_DEFAULT)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())
    build(a.season, a.out, a.min_att, a.min_tgt, a.min_db)
