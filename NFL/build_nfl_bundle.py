#!/usr/bin/env python3
"""
build_nfl_bundle.py — JTT NFL bundle generator (Phase 1)

Pulls NFL data from nflverse (via nflreadpy) and writes a single bundle.json
that the JTT NFL shell loads through applyBundle().

Locked decisions:
  - Source: nflreadpy (nflverse). Free.
  - Seasons: 2024 + 2025 (2025 = headline averages; 2024 retained in gamelog
    for H2H depth — division opponents meet 2x/yr → up to 4 H2H samples/pair).
  - Output shape: { _meta, password, season, week, players, teams, dvp, fixture }
  - Team codes: nflverse abbreviations (KC, BUF, ...). No full-name normalisation.

Run (Ant's machine / GitHub Action):
    pip install nflreadpy pandas
    python build_nfl_bundle.py --password "weekly-pw" --out NFL/data/bundle.json

Notes:
  - nflreadpy returns polars; we convert to pandas for the transforms.
  - DVP is DERIVED here (nflverse has no clean defence-vs-position table):
    for each (defense, position) we average what that defense allowed per game.
"""

import argparse, json, sys, datetime as dt
from collections import defaultdict

SEASONS = [2024, 2025]
HEADLINE_SEASON = 2025          # season used for headline per-game averages
POSITIONS = ["QB", "RB", "WR", "TE"]

# ── column resolver ─────────────────────────────────────────────────────────
# nflverse renames columns across releases; resolve defensively.
def col(df, *cands, default=None):
    for c in cands:
        if c in df.columns:
            return c
    return default

def g(row, c, d=0.0):
    if c is None: return d
    v = row.get(c, d)
    if v is None: return d
    try:
        f = float(v)
        return d if f != f else f   # NaN guard
    except (ValueError, TypeError):
        return v

def r1(x): return round(float(x), 1)
def r2(x): return round(float(x), 2)


# ── load ────────────────────────────────────────────────────────────────────
def load_frames(with_injuries=True):
    import nflreadpy as nfl
    ps = nfl.load_player_stats(seasons=SEASONS, summary_level="week").to_pandas()
    sc = nfl.load_snap_counts(seasons=SEASONS).to_pandas()
    sch = nfl.load_schedules(seasons=SEASONS).to_pandas()
    inj = None
    if with_injuries:
        try:
            inj = nfl.load_injuries(seasons=[HEADLINE_SEASON]).to_pandas()
        except Exception as e:
            print(f"  (injuries unavailable: {e})")
    return ps, sc, sch, inj


# ── injuries: latest-week report for the headline season ────────────────────
def build_injuries(inj):
    if inj is None or getattr(inj, "empty", True):
        return []
    se = col(inj, "season"); wk = col(inj, "week")
    nm = col(inj, "full_name", "player_name", "player_display_name", "gsis_id")
    tm = col(inj, "team"); pos = col(inj, "position")
    st = col(inj, "report_status", "game_status", "status")
    pr = col(inj, "report_primary_injury", "primary_injury", "injury")
    # latest week present for the headline season
    sub = inj[inj[se] == HEADLINE_SEASON] if se else inj
    if sub.empty:
        return []
    latest = int(sub[wk].max()) if wk else None
    if latest is not None:
        sub = sub[sub[wk] == latest]
    out, seen = [], set()
    for _, row in sub.iterrows():
        name = str(g(row, nm, "")).strip()
        status = str(g(row, st, "")).strip()
        if not name or not status:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "name": name,
            "team": str(g(row, tm, "")).strip().upper(),
            "pos": str(g(row, pos, "")).strip().upper(),
            "status": status,                         # Out / Doubtful / Questionable
            "detail": str(g(row, pr, "")).strip(),
            "week": latest,
        })
    return out


# ── snap share lookup: (player_id|name, season, week) -> offense_pct ─────────
def build_snap_idx(sc):
    if sc is None or sc.empty:
        return {}, {}
    pid = col(sc, "pfr_player_id", "player_id")
    nm  = col(sc, "player", "player_name", "pfr_player_name")
    se  = col(sc, "season"); wk = col(sc, "week")
    pct = col(sc, "offense_pct", "off_pct")
    by_name = {}
    for _, row in sc.iterrows():
        key = (str(g(row, nm, "")).strip().lower(), int(g(row, se)), int(g(row, wk)))
        p = g(row, pct)
        # nflverse offense_pct is 0..1 in some releases, 0..100 in others
        if p and p <= 1.0: p *= 100.0
        by_name[key] = round(p, 1)
    return by_name, {}


# ── players + gamelog ───────────────────────────────────────────────────────
def build_players(ps, snap_by_name):
    name_c = col(ps, "player_display_name", "player_name")
    id_c   = col(ps, "player_id", "gsis_id")
    pos_c  = col(ps, "position", "position_group")
    team_c = col(ps, "team", "recent_team")
    opp_c  = col(ps, "opponent_team", "opponent")
    se_c   = col(ps, "season"); wk_c = col(ps, "week")
    st_c   = col(ps, "season_type")

    # stat columns (resolve once)
    C = {
        "passYds":  col(ps, "passing_yards"),
        "passTds":  col(ps, "passing_tds"),
        "passAtt":  col(ps, "attempts", "passing_attempts"),
        "passComp": col(ps, "completions"),
        "passInt":  col(ps, "interceptions", "passing_interceptions"),
        "rushYds":  col(ps, "rushing_yards"),
        "rushAtt":  col(ps, "carries", "rushing_attempts"),
        "rushTds":  col(ps, "rushing_tds"),
        "rec":      col(ps, "receptions"),
        "recYds":   col(ps, "receiving_yards"),
        "recTds":   col(ps, "receiving_tds"),
        "targets":  col(ps, "targets"),
        "fanPts":   col(ps, "fantasy_points_ppr", "fantasy_points"),
    }

    players = {}   # id -> aggregate
    for _, row in ps.iterrows():
        pos = str(g(row, pos_c, "")).strip().upper()
        if pos not in POSITIONS:
            continue
        st = str(g(row, st_c, "REG")).upper()
        if st not in ("REG", "POST"):
            continue
        pid  = str(g(row, id_c, ""))
        name = str(g(row, name_c, "")).strip()
        if not name:
            continue
        key = pid or name
        season = int(g(row, se_c)); week = int(g(row, wk_c))
        team = str(g(row, team_c, "")).strip().upper()
        opp  = str(g(row, opp_c, "")).strip().upper()

        snap = snap_by_name.get((name.lower(), season, week))

        glog = {"season": season, "week": week, "team": team, "opp": opp,
                "st": "P" if st == "POST" else "R"}
        for k, c in C.items():
            glog[k] = round(g(row, c), 1)
        if snap is not None:
            glog["snapPct"] = snap

        P = players.setdefault(key, {
            "id": key, "name": name, "position": pos, "team": team,
            "gamelog": []
        })
        # keep most-recent team as headline team
        if season > 0:
            P["team"] = team
        P["position"] = pos
        P["gamelog"].append(glog)

    # headline per-game averages from HEADLINE_SEASON (fallback to prior season)
    out = []
    STAT_KEYS = ["passYds","passTds","passAtt","passComp","passInt",
                 "rushYds","rushAtt","rushTds","rec","recYds","recTds",
                 "targets","fanPts"]
    for P in players.values():
        gl = sorted(P["gamelog"], key=lambda r: (r["season"], r["week"]))
        P["gamelog"] = gl
        cur = [r for r in gl if r["season"] == HEADLINE_SEASON]
        src = cur if cur else [r for r in gl if r["season"] == HEADLINE_SEASON - 1]
        if not src:
            src = gl
        gms = len(src)
        P["g"] = gms
        for k in STAT_KEYS:
            vals = [r.get(k, 0) for r in src]
            P[k] = r1(sum(vals) / gms) if gms else 0.0
        snaps = [r["snapPct"] for r in src if "snapPct" in r]
        P["snapPct"] = r1(sum(snaps) / len(snaps)) if snaps else None
        # usage trend: last-3 snap share vs season snap share (Usage Watch signal)
        l3 = [r["snapPct"] for r in src[-3:] if "snapPct" in r]
        if snaps and l3:
            P["snapTrend"] = r1(sum(l3)/len(l3) - sum(snaps)/len(snaps))
        out.append(P)
    return out


# ── teams: for / against per game ───────────────────────────────────────────
def build_teams(ps):
    name_c = col(ps, "player_display_name", "player_name")
    team_c = col(ps, "team", "recent_team")
    opp_c  = col(ps, "opponent_team", "opponent")
    se_c   = col(ps, "season"); wk_c = col(ps, "week")
    st_c   = col(ps, "season_type")
    C = {
        "passYds": col(ps,"passing_yards"), "passTds": col(ps,"passing_tds"),
        "rushYds": col(ps,"rushing_yards"), "rushTds": col(ps,"rushing_tds"),
        "passAtt": col(ps,"attempts","passing_attempts"),
        "rushAtt": col(ps,"carries","rushing_attempts"),
        "passInt": col(ps,"interceptions","passing_interceptions"),
        "sacks":   col(ps,"sacks"),
    }
    # tally team-game totals: (team, season, week) accumulates offence;
    # opponent of that team accumulates the same as 'against'.
    fo = defaultdict(lambda: defaultdict(float))   # for[team] += offence
    ag = defaultdict(lambda: defaultdict(float))   # against[team] += offence faced
    games_for = defaultdict(set); games_ag = defaultdict(set)
    for _, row in ps.iterrows():
        st = str(g(row, st_c, "REG")).upper()
        if st != "REG":   # team rates from regular season only
            continue
        if int(g(row, se_c)) != HEADLINE_SEASON:
            continue
        team = str(g(row, team_c, "")).strip().upper()
        opp  = str(g(row, opp_c, "")).strip().upper()
        wk   = int(g(row, wk_c))
        if not team or not opp:
            continue
        for k, c in C.items():
            v = g(row, c)
            fo[team][k] += v
            ag[opp][k]  += v
        games_for[team].add(wk); games_ag[opp].add(wk)

    teams = []
    allt = set(fo) | set(ag)
    for t in sorted(allt):
        gf = max(len(games_for[t]), 1); ga = max(len(games_ag[t]), 1)
        rec = {"team": t, "g": gf}
        for k in ["passYds","passTds","rushYds","rushTds","passAtt","rushAtt","passInt","sacks"]:
            rec[k + "_f"] = r1(fo[t][k] / gf)   # offence (for)
            rec[k + "_a"] = r1(ag[t][k] / ga)   # defence (allowed/against)
        teams.append(rec)
    return teams


# ── DVP: defence vs position (per-game allowances) ──────────────────────────
def build_dvp(ps):
    pos_c  = col(ps, "position", "position_group")
    opp_c  = col(ps, "opponent_team", "opponent")
    team_c = col(ps, "team", "recent_team")
    se_c   = col(ps, "season"); wk_c = col(ps, "week")
    st_c   = col(ps, "season_type")
    C = {
        "passYds": col(ps,"passing_yards"), "passTds": col(ps,"passing_tds"),
        "rushYds": col(ps,"rushing_yards"), "rushTds": col(ps,"rushing_tds"),
        "rec": col(ps,"receptions"), "recYds": col(ps,"receiving_yards"),
        "recTds": col(ps,"receiving_tds"), "targets": col(ps,"targets"),
        "fanPts": col(ps,"fantasy_points_ppr","fantasy_points"),
    }
    # accumulate what each (defence, position) allowed, per game-week
    acc = defaultdict(lambda: defaultdict(float))
    wks = defaultdict(set)
    for _, row in ps.iterrows():
        if str(g(row, st_c, "REG")).upper() != "REG": continue
        if int(g(row, se_c)) != HEADLINE_SEASON: continue
        pos = str(g(row, pos_c, "")).strip().upper()
        if pos not in POSITIONS: continue
        deff = str(g(row, opp_c, "")).strip().upper()   # the defence faced
        if not deff: continue
        wk = int(g(row, wk_c))
        key = (deff, pos)
        for k, c in C.items():
            acc[key][k] += g(row, c)
        wks[key].add(wk)

    dvp = []
    for (deff, pos), stats in acc.items():
        gms = max(len(wks[(deff, pos)]), 1)
        rec = {"team": deff, "pos": pos, "g": gms}
        for k in ["passYds","passTds","rushYds","rushTds","rec","recYds","recTds","targets","fanPts"]:
            rec[k] = r1(stats[k] / gms)
        dvp.append(rec)

    # rank within position (1 = allows most = softest matchup)
    by_pos = defaultdict(list)
    for d in dvp: by_pos[d["pos"]].append(d)
    rank_keys = {"QB":"passYds","RB":"rushYds","WR":"recYds","TE":"recYds"}
    for pos, rows in by_pos.items():
        rk = rank_keys.get(pos, "fanPts")
        for i, d in enumerate(sorted(rows, key=lambda x: -x[rk]), 1):
            d["rank"] = i
            d["rankN"] = len(rows)
    return dvp


# ── fixture (schedule) ──────────────────────────────────────────────────────
def build_fixture(sch):
    if sch is None or sch.empty:
        return []
    se = col(sch,"season"); wk = col(sch,"week")
    h  = col(sch,"home_team"); a = col(sch,"away_team")
    gd = col(sch,"gameday","gameday_dt"); gt = col(sch,"gametime")
    hs = col(sch,"home_score"); as_ = col(sch,"away_score")
    out = []
    for _, row in sch.iterrows():
        if int(g(row, se)) != HEADLINE_SEASON: continue
        home = str(g(row, h, "")).strip().upper(); away = str(g(row, a, "")).strip().upper()
        if not home or not away: continue
        out.append({
            "season": int(g(row, se)), "week": int(g(row, wk)),
            "home": home, "away": away,
            "date": str(g(row, gd, "")), "time": str(g(row, gt, "")),
            "hs": g(row, hs, None), "as": g(row, as_, None),
        })
    return out


# ── main ────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="bundle.json")
    ap.add_argument("--password", default=None, help="weekly access password (omit = open)")
    ap.add_argument("--week", type=int, default=None, help="display week label")
    ap.add_argument("--no-injuries", action="store_true", help="skip injury report")
    args = ap.parse_args()

    print("Loading nflverse frames (2024+2025)…")
    ps, sc, sch, inj = load_frames(with_injuries=not args.no_injuries)
    print(f"  player_stats rows: {len(ps):,}  snap rows: {len(sc):,}  schedule rows: {len(sch):,}")

    snap_by_name, _ = build_snap_idx(sc)
    players  = build_players(ps, snap_by_name)
    teams    = build_teams(ps)
    dvp      = build_dvp(ps)
    fixture  = build_fixture(sch)
    injuries = build_injuries(inj)

    week = args.week
    if week is None:
        try:
            import nflreadpy as nfl
            week = int(nfl.get_current_week())
        except Exception:
            week = None

    bundle = {
        "_meta": {
            "source": "nflverse / nflreadpy",
            "seasons": SEASONS,
            "headline_season": HEADLINE_SEASON,
            "built": dt.datetime.utcnow().isoformat() + "Z",
            "players": len(players), "teams": len(teams),
            "dvp": len(dvp), "fixture": len(fixture), "injury": len(injuries),
        },
        "season": HEADLINE_SEASON,
        "week": week,
        "players": players,
        "teams": teams,
        "dvp": dvp,
        "fixture": fixture,
        "injury": injuries,
    }
    if args.password:
        bundle["password"] = args.password

    with open(args.out, "w") as f:
        json.dump(bundle, f, separators=(",", ":"))
    mb = round(len(json.dumps(bundle)) / 1e6, 2)
    print(f"Wrote {args.out}  ({mb} MB)")
    print(f"  players={len(players)} teams={len(teams)} dvp={len(dvp)} "
          f"fixture={len(fixture)} injury={len(injuries)}")


if __name__ == "__main__":
    main()
