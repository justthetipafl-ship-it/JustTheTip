#!/usr/bin/env python3
"""
build_afl_data.py — JTT AFL data builder (rebuild pipeline)
============================================================
Replaces the single 25 MB bundle.json load with a set of lean, split
data files (WC/MLB pattern) and derives the player + team tables
*directly from the Champion Data game logs* so the whole pipeline is
automatable from one source instead of an external 311-column Excel/R blob.

INPUT  (either, autodetected):
  - data/dvp.raw.json     game-log rows (Champion Data fields, preferred)
  - bundle.json           legacy mega-bundle (we read its "dvp"/"player"/etc.)

OUTPUT (data/):
  version.txt    unix-seconds string — cache-bust token + freshness clock
  meta.json      {version, created, round, password_hash, summary}
  players.json   season-average rows DERIVED from game logs (+ pos/team join)
  teams.json     per-team "for" and "allowed" averages DERIVED from game logs
  gamelogs.json  pruned game logs (only the columns the UI reads)
  fixture.json   upcoming matches (passthrough)
  fgs.json       first-goal-scorer (passthrough)
  injury.json    injury list (passthrough)

Usage:  python build_afl_data.py [--src bundle.json] [--out data] \
                                 [--seasons 2025,2026] [--password BEACH]
"""
import argparse, hashlib, json, os, sys, time
from collections import defaultdict

# --- stat contract: internal camelCase key  <-  Champion Data dvp field -----
# Only what the UI actually reads. Add a row here to surface a new stat;
# nothing else in the pipeline needs to change.
STAT_MAP = {
    "disposals":         "Disposals",
    "kicks":             "Kicks",
    "handballs":         "Handballs",
    "marks":             "Marks",
    "contestedMarks":    "ContestedMarks",
    "interceptMarks":    "InterceptMarks",
    "tackles":           "Tackles",
    "pressureActs":      "PressureActs",
    "goals":             "Goals",
    "behinds":           "Behinds",
    "shotsAtGoal":       "ShotsAtGoal",
    "goalAssists":       "GoalAssists",
    "scoreInvolvements": "ScoreInvolvements",
    "clearances":        "TotalClearances",
    "hitouts":           "Hitouts",
    "inside50s":         "Inside50s",
    "contested":         "ContestedPossessions",
    "groundBallGets":    "GroundBallGets",
    "intercepts":        "Intercepts",
    "xScore":            "xScore",
    "postClearGBG":      "PostClearanceGroundBallGets",
    "postClearCont":     "PostClearanceContestedPossessions",
    "handballReceives":  "HandballReceives",
    "metresGained":      "MetresGained",
    "cba":               "CentreBounceAttendancePercentage",
    "tog":               "TimeOnGround",
    "dreamteam":         "DreamTeamPoints",
    "supercoach":        "Supercoach",
    "ratingPoints":      "RatingPoints",
    "disposalEff":       "DisposalEfficiency",
}
# identity columns kept verbatim on each pruned game-log row
ID_COLS = ["Year", "RoundName", "MatchId", "Player", "Team"]


def to_num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_source(src):
    """Return (gamelogs, legacy_player, fixture, fgs, injury, legacy_meta)."""
    if os.path.isdir(src) and os.path.exists(os.path.join(src, "dvp.raw.json")):
        dvp = json.load(open(os.path.join(src, "dvp.raw.json")))
        return dvp, [], [], [], [], {}
    d = json.load(open(src))
    return (
        d.get("dvp", []),
        d.get("player", []),
        d.get("fixture", []),
        d.get("fgs", []),
        d.get("injury", []),
        {k: d.get(k) for k in ("version", "created", "round", "summary", "formats")},
    )


def prune_gamelogs(dvp, seasons):
    out = []
    for r in dvp:
        if seasons and str(r.get("Year")) not in seasons:
            continue
        row = {c: r.get(c) for c in ID_COLS}
        for k, src in STAT_MAP.items():
            row[k] = to_num(r.get(src))
        out.append(row)
    return out


def build_demo(legacy_player):
    demo = {}
    for p in legacy_player:
        nm = p.get("Player")
        if nm:
            demo[nm] = {
                "team": p.get("Team"),
                "position": p.get("Position"),
                "age": to_num(p.get("Age_Decimal")) or to_num(p.get("Age")),
            }
    return demo


def derive_players(logs, demo, current_season):
    """Season averages per player, derived from the current-season game logs.
    Position/Age are joined from the legacy player blob (demographic columns
    that still need an external source — flagged in meta.derivedNote)."""
    agg = defaultdict(lambda: defaultdict(float))
    cnt = defaultdict(int)
    teamOf = {}
    for r in logs:
        if str(r.get("Year")) != current_season:
            continue
        nm = r.get("Player")
        if not nm:
            continue
        cnt[nm] += 1
        teamOf[nm] = r.get("Team")
        for k in STAT_MAP:
            v = r.get(k)
            if v is not None:
                agg[nm][k] += v
    players = []
    for nm, n in cnt.items():
        row = {"name": nm, "matches": n,
               "team": teamOf.get(nm) or demo.get(nm, {}).get("team"),
               "position": demo.get(nm, {}).get("position") or "",
               "age": demo.get(nm, {}).get("age")}
        for k in STAT_MAP:
            row[k] = round(agg[nm][k] / n, 3) if n else None
        players.append(row)
    players.sort(key=lambda p: -(p.get("disposals") or 0))
    return players


def derive_teams(logs, current_season):
    """Per-team 'for' (own players) and 'allowed' (opponent vs them) season
    averages + W/L form, all derived from game logs. Replaces the 311-col blob.
    Allowed = sum of opponent team totals in matches the team played."""
    # team totals per match
    match_team = defaultdict(lambda: defaultdict(float))   # (mid,team) -> stat sums
    match_teams = defaultdict(set)                          # mid -> {teams}
    games_played = defaultdict(set)                         # team -> {mid}
    for r in logs:
        if str(r.get("Year")) != current_season:
            continue
        mid, tm = r.get("MatchId"), r.get("Team")
        if not mid or not tm:
            continue
        match_teams[mid].add(tm)
        games_played[tm].add(mid)
        for k in STAT_MAP:
            v = r.get(k)
            if v is not None:
                match_team[(mid, tm)][k] += v
    for_sum = defaultdict(lambda: defaultdict(float))
    all_sum = defaultdict(lambda: defaultdict(float))
    n_team = defaultdict(int)
    for mid, teams in match_teams.items():
        teams = list(teams)
        if len(teams) != 2:
            continue
        a, b = teams
        for me, opp in ((a, b), (b, a)):
            n_team[me] += 1
            for k in STAT_MAP:
                for_sum[me][k] += match_team[(mid, me)][k]
                all_sum[me][k] += match_team[(mid, opp)][k]
    teams = []
    for tm, n in n_team.items():
        if not n:
            continue
        row = {"team": tm, "matches": n}
        for k in STAT_MAP:
            row[k] = round(for_sum[tm][k] / n, 2)
            row[k + "_a"] = round(all_sum[tm][k] / n, 2)
        teams.append(row)
    teams.sort(key=lambda t: t["team"])
    return teams


def _match_index(logs, current_season):
    """(match_team sums, match->teams). Shared by team/form/dvp derivations."""
    match_team = defaultdict(lambda: defaultdict(float))
    match_teams = defaultdict(set)
    order = []
    for r in logs:
        if str(r.get("Year")) != current_season:
            continue
        mid, tm = r.get("MatchId"), r.get("Team")
        if not mid or not tm:
            continue
        if mid not in match_teams:
            order.append(mid)
        match_teams[mid].add(tm)
        for k in STAT_MAP:
            v = r.get(k)
            if v is not None:
                match_team[(mid, tm)][k] += v
    return match_team, match_teams, sorted(set(order))


def derive_teams_form(logs, current_season, last_n=4):
    """Same shape as derive_teams but only the most recent `last_n` matches
    per team — feeds tfPctFor (the matrix prefers form over season)."""
    match_team, match_teams, order = _match_index(logs, current_season)
    recent = {}  # team -> ordered list of mids
    for mid in order:
        for tm in match_teams[mid]:
            recent.setdefault(tm, []).append(mid)
    for_sum = defaultdict(lambda: defaultdict(float))
    all_sum = defaultdict(lambda: defaultdict(float))
    n_team = defaultdict(int)
    for tm, mids in recent.items():
        for mid in mids[-last_n:]:
            teams = list(match_teams[mid])
            if len(teams) != 2:
                continue
            opp = teams[0] if teams[1] == tm else teams[1]
            n_team[tm] += 1
            for k in STAT_MAP:
                for_sum[tm][k] += match_team[(mid, tm)][k]
                all_sum[tm][k] += match_team[(mid, opp)][k]
    out = []
    for tm, n in n_team.items():
        if not n:
            continue
        row = {"team": tm, "matches": n}
        for k in STAT_MAP:
            row[k] = round(for_sum[tm][k] / n, 2)
            row[k + "_a"] = round(all_sum[tm][k] / n, 2)
        out.append(row)
    return out


def derive_dvp(logs, demo, current_season):
    """Team x position x stat 'allowed' table: for each team, the per-game
    average that OPPONENT players of each position produce against them.
    Derived from game logs + player positions — replaces the external DVP blob.
    getDVPPct() compares a cell to the league average for that position."""
    # opponent lookup per match
    match_teams = defaultdict(set)
    for r in logs:
        if str(r.get("Year")) == current_season and r.get("MatchId") and r.get("Team"):
            match_teams[r["MatchId"]].add(r["Team"])
    agg = defaultdict(lambda: defaultdict(float))   # (team,pos) -> stat sums
    games = defaultdict(set)                         # (team,pos) -> matches seen
    for r in logs:
        if str(r.get("Year")) != current_season:
            continue
        mid, tm = r.get("MatchId"), r.get("Team")
        if not mid or not tm:
            continue
        teams = match_teams.get(mid)
        if not teams or len(teams) != 2:
            continue
        opp = next(t for t in teams if t != tm)        # team that ALLOWED this
        pos = (demo.get(r.get("Player"), {}) or {}).get("position")
        if not pos:
            continue
        key = (opp, pos)
        games[key].add((mid, r.get("Player")))         # player-games counted
        for k in STAT_MAP:
            v = r.get(k)
            if v is not None:
                agg[key][k] += v
    # convert sums to per-MATCH allowance (sum over players / matches the team played)
    team_matches = defaultdict(set)
    for mid, teams in match_teams.items():
        for tm in teams:
            team_matches[tm].add(mid)
    out = []
    for (team, pos), sums in agg.items():
        nm = len(team_matches.get(team, set())) or 1
        row = {"team": team, "pos": pos}
        for k in STAT_MAP:
            row[k] = round(sums[k] / nm, 3)
        out.append(row)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="bundle.json")
    ap.add_argument("--out", default="data")
    ap.add_argument("--seasons", default="2025,2026",
                    help="game-log seasons to keep (comma sep), '' = all")
    ap.add_argument("--current", default="2026", help="season for averages")
    ap.add_argument("--password", default=None,
                    help="weekly password -> stored as SHA-256 (never plaintext)")
    args = ap.parse_args()

    dvp, legacy_player, fixture, fgs, injury, legacy_meta = load_source(args.src)
    seasons = set(s.strip() for s in args.seasons.split(",") if s.strip())

    demo = build_demo(legacy_player)
    logs = prune_gamelogs(dvp, seasons)
    players = derive_players(logs, demo, args.current)
    teams = derive_teams(logs, args.current)
    teams_form = derive_teams_form(logs, args.current)
    dvp_table = derive_dvp(logs, demo, args.current)

    os.makedirs(args.out, exist_ok=True)
    version = str(int(time.time()))
    pw = args.password
    pw_hash = (hashlib.sha256(pw.encode()).hexdigest() if pw else None)

    meta = {
        "version": version,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "round": legacy_meta.get("round"),
        "password_hash": pw_hash,
        "seasons": sorted(seasons),
        "currentSeason": args.current,
        "summary": {
            "players": len(players), "teams": len(teams),
            "teamsForm": len(teams_form), "dvp": len(dvp_table),
            "gamelogs": len(logs), "fixtures": len(fixture),
        },
        "derivedNote": "players/teams derived from Champion Data game logs; "
                       "position/age joined from legacy player blob.",
    }

    def w(name, obj):
        p = os.path.join(args.out, name)
        json.dump(obj, open(p, "w"), separators=(",", ":"))
        return os.path.getsize(p)

    sizes = {
        "meta.json":     w("meta.json", meta),
        "players.json":  w("players.json", players),
        "teams.json":    w("teams.json", teams),
        "teams_form.json": w("teams_form.json", teams_form),
        "dvp.json":      w("dvp.json", dvp_table),
        "gamelogs.json": w("gamelogs.json", logs),
        "fixture.json":  w("fixture.json", fixture),
        "fgs.json":      w("fgs.json", fgs),
        "injury.json":   w("injury.json", injury),
    }
    open(os.path.join(args.out, "version.txt"), "w").write(version)

    total = sum(sizes.values())
    print(f"[build] version {version}  round {meta['round']}")
    print(f"[build] players={len(players)} teams={len(teams)} "
          f"gamelogs={len(logs)} (seasons {sorted(seasons)})")
    for k, v in sizes.items():
        print(f"        {k:16} {v/1024:8.1f} KB")
    print(f"[build] total split size {total/1024/1024:.2f} MB "
          f"(was ~25 MB single bundle)")
    if pw_hash:
        print(f"[build] password hashed (sha256 …{pw_hash[-8:]})")


if __name__ == "__main__":
    main()
