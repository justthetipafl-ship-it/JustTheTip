#!/usr/bin/env python3
"""
build_aflw_data.py - AFLW split-data generator (Phase 2)

Reads the fitzRoy fetch outputs:
    aflw/raw_gamelogs.json   per-player, per-game Champion Data stat lines (all cols)
    aflw/bundle.json         fixtures / results / lineups (already in shell format)

Emits the same file set + formats the AFL flagship uses, so AFLW can reuse the AFL
scoring + signals modules unchanged:
    aflw/data/gamelogs.json  players.json  teams.json  dvp.json  meta.json
             fixture.json  results.json  lineups.json  fgs.json  injury.json

Position: AFLW gives game-day field positions (FB, CHB, C, RK, FF, ...) rather than
Champion Data's season role groups, so we map field position -> role group and take
each player's dominant group across the 3 seasons (Mid-Forward when a player splits
midfield and forward roughly evenly).
"""
import argparse, hashlib, json, os, sys, time
from collections import defaultdict, Counter

# ---- 18 clubs: raw AFLW name -> shared JTT/AFL team key (matches AFL logos + config) ----
TEAM_MAP = {
    "Adelaide Crows":"Adelaide","Adelaide":"Adelaide","Brisbane Lions":"Brisbane","Brisbane":"Brisbane",
    "Carlton":"Carlton","Collingwood":"Collingwood","Essendon":"Essendon","Fremantle":"Fremantle",
    "Geelong Cats":"Geelong","Geelong":"Geelong","Gold Coast SUNS":"Gold Coast","Gold Coast Suns":"Gold Coast","Gold Coast":"Gold Coast",
    "GWS GIANTS":"Greater Western Sydney","GWS Giants":"Greater Western Sydney","Greater Western Sydney":"Greater Western Sydney",
    "Hawthorn":"Hawthorn","Melbourne":"Melbourne","North Melbourne":"North Melbourne",
    "Port Adelaide":"Port Adelaide","Richmond":"Richmond","St Kilda":"St Kilda",
    "Sydney Swans":"Sydney","Sydney":"Sydney","West Coast Eagles":"West Coast","West Coast":"West Coast",
    "Western Bulldogs":"Western Bulldogs",
}
def norm_team(x):
    if x is None: return None
    x = str(x)
    return TEAM_MAP.get(x, x)

# ---- internal gamelog field -> AFLW source column (None => stat not in the AFLW feed, 0-filled) ----
STAT_SRC = {
    "disposals":"disposals","kicks":"kicks","handballs":"handballs","marks":"marks",
    "contestedMarks":"contestedMarks","interceptMarks":None,"tackles":"tackles",
    "pressureActs":None,"goals":"goals","behinds":"behinds","shotsAtGoal":"shotsAtGoal",
    "goalAssists":"goalAssists","scoreInvolvements":"scoreInvolvements",
    "clearances":"clearances.totalClearances","hitouts":"hitouts","inside50s":"inside50s",
    "contested":"contestedPossessions","groundBallGets":None,"intercepts":"intercepts",
    "xScore":None,"postClearGBG":None,"postClearCont":None,"handballReceives":None,
    "metresGained":"metresGained","cba":None,"tog":"timeOnGroundPercentage",
    "dreamteam":"dreamTeamPoints","supercoach":None,"ratingPoints":"ratingPoints",
    "disposalEff":"disposalEfficiency","marksOnLead":None,"marksInside50":"marksInside50",
    "tacklesInside50":"tacklesInside50","forward50Poss":None,"rebound50s":"rebound50s",
}
STAT_FIELDS = list(STAT_SRC.keys())

# ---- game-day field position -> Champion Data role group ----
POS_GROUP = {
    "FB":"Key Defender","CHB":"Key Defender",
    "BPL":"Gen. Defender","BPR":"Gen. Defender","HBFL":"Gen. Defender","HBFR":"Gen. Defender",
    "C":"Midfielder","WL":"Midfielder","WR":"Midfielder","RR":"Midfielder","R":"Midfielder",
    "RK":"Ruck",
    "CHF":"Key Forward","FF":"Key Forward",
    "HFFL":"Gen. Forward","HFFR":"Gen. Forward","FPL":"Gen. Forward","FPR":"Gen. Forward",
}

def num(v):
    try:
        f = float(v)
        return 0.0 if f != f else f
    except (TypeError, ValueError):
        return 0.0

def season_pos(cnts):
    """Dominant role group across the player's games; Mid-Forward when split ~evenly."""
    pos = {g: c for g, c in cnts.items() if g}
    if not pos: return ""
    mid  = pos.get("Midfielder", 0)
    fwd  = pos.get("Key Forward", 0) + pos.get("Gen. Forward", 0)
    dfd  = pos.get("Key Defender", 0) + pos.get("Gen. Defender", 0)
    ruck = pos.get("Ruck", 0)
    if mid > 0 and fwd > 0 and min(mid, fwd) / max(mid, fwd) >= 0.4 and (mid + fwd) >= max(dfd, ruck):
        return "Mid-Forward"
    return max(pos, key=pos.get)

def match_id(year, rnd, a, b):
    lo, hi = sorted([a, b])
    return "%s-R%s-%s-v-%s" % (year, rnd, lo, hi)

def utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gamelogs", default="aflw/raw_gamelogs.json")
    ap.add_argument("--bundle",   default="aflw/bundle.json")
    ap.add_argument("--out",      default="aflw/data")
    ap.add_argument("--password", default=None)
    a = ap.parse_args()

    gl = json.load(open(a.gamelogs, encoding="utf-8"))
    b  = json.load(open(a.bundle, encoding="utf-8"))

    # ---- pass 1: map rows + collect per-player role groups ----
    rows = []
    player_groups = defaultdict(Counter)
    skipped = 0
    for r in gl:
        team = norm_team(r.get("team.name"))
        if not team or team == "None":
            skipped += 1; continue
        home = norm_team(r.get("home.team.name")); away = norm_team(r.get("away.team.name"))
        opp = away if team == home else home
        given = (r.get("player.givenName") or "").strip()
        surn  = (r.get("player.surname") or "").strip()
        name = (given + " " + surn).strip()
        if not name:
            skipped += 1; continue
        year = str(r.get("jttSeason"))
        rnd  = int(num(r.get("round.roundNumber")))
        grp  = POS_GROUP.get(r.get("player.player.position"))
        if grp:
            player_groups[name][grp] += 1
        row = {"Year": year, "RoundName": ("Round %d" % rnd),   # shell parses "Round N"; AFLW feed says "Week N"
               "MatchId": match_id(year, rnd, team, opp), "Player": name, "Team": team, "_opp": opp}
        for f, src in STAT_SRC.items():
            row[f] = round(num(r.get(src)), 2) if src else 0.0
        rows.append(row)

    player_pos = {name: season_pos(c) for name, c in player_groups.items()}

    # ---- players.json ----
    pacc = defaultdict(lambda: {"g": 0, "team": Counter(), "sum": defaultdict(float)})
    for row in rows:
        p = pacc[row["Player"]]; p["g"] += 1; p["team"][row["Team"]] += 1
        for f in STAT_FIELDS: p["sum"][f] += row[f]
    players = []
    for name, p in pacc.items():
        g = max(1, p["g"])
        rec = {"name": name, "matches": p["g"], "team": p["team"].most_common(1)[0][0],
               "position": player_pos.get(name, ""), "age": None}
        for f in STAT_FIELDS: rec[f] = round(p["sum"][f] / g, 3)
        players.append(rec)
    players.sort(key=lambda x: -x["disposals"])

    # ---- teams.json (match-level team totals -> for / against averages) ----
    mteam = defaultdict(lambda: defaultdict(float)); mteams = defaultdict(set)
    for row in rows:
        k = (row["MatchId"], row["Team"]); mteams[row["MatchId"]].add(row["Team"])
        for f in STAT_FIELDS: mteam[k][f] += row[f]
    tfor = defaultdict(lambda: defaultdict(float)); tag = defaultdict(lambda: defaultdict(float)); tg = Counter()
    for (mid, team), s in mteam.items():
        tg[team] += 1
        for f in STAT_FIELDS: tfor[team][f] += s[f]
        opps = [t for t in mteams[mid] if t != team]
        if opps:
            os_ = mteam[(mid, opps[0])]
            for f in STAT_FIELDS: tag[team][f] += os_[f]
    teams = []
    for team in sorted(tg):
        g = max(1, tg[team]); rec = {"team": team, "matches": tg[team]}
        for f in STAT_FIELDS:
            rec[f] = round(tfor[team][f] / g, 2); rec[f + "_a"] = round(tag[team][f] / g, 2)
        teams.append(rec)

    # ---- dvp.json ((opponent team, player season role) -> avg stats allowed) ----
    dsum = defaultdict(lambda: defaultdict(float)); dg = Counter()
    for row in rows:
        grp = player_pos.get(row["Player"], "")
        if not grp: continue
        k = (row["_opp"], grp); dg[k] += 1
        for f in STAT_FIELDS: dsum[k][f] += row[f]
    dvp = []
    for (team, pos), g in sorted(dg.items()):
        rec = {"team": team, "pos": pos}
        for f in STAT_FIELDS: rec[f] = round(dsum[(team, pos)][f] / max(1, g), 3)
        dvp.append(rec)

    # strip internal field before writing gamelogs
    for row in rows: row.pop("_opp", None)

    # ---- write ----
    os.makedirs(a.out, exist_ok=True)
    def dump(name, obj):
        with open(os.path.join(a.out, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    dump("gamelogs.json", rows)
    dump("players.json", players)
    dump("teams.json", teams)
    dump("dvp.json", dvp)
    dump("fixture.json", b.get("fixture", []))
    dump("results.json", b.get("results", []))
    dump("lineups.json", b.get("lineups", []))
    dump("fgs.json", [])      # AFLW feed has no first-goal timing yet
    dump("injury.json", [])   # not sourced for AFLW v1

    seasons = sorted(set(r["Year"] for r in rows))
    meta = {
        "version": str(int(time.time())), "created": utcnow(), "round": b.get("round"),
        "seasons": seasons, "currentSeason": (seasons[-1] if seasons else None),
        "summary": {"players": len(players), "teams": len(teams), "teamsForm": len(teams),
                    "dvp": len(dvp), "gamelogs": len(rows), "fixtures": len(b.get("fixture", []))},
        "derivedNote": "AFLW: players/teams/dvp derived from fitzRoy Champion Data game logs; position mapped from game-day field roles.",
    }
    if a.password:
        meta["password_hash"] = hashlib.sha256(a.password.encode()).hexdigest()
    dump("meta.json", meta)

    print("[build_aflw] rows=%d (skipped %d) | players=%d teams=%d dvp=%d | seasons=%s round=%s"
          % (len(rows), skipped, len(players), len(teams), len(dvp), ",".join(seasons), b.get("round")))

if __name__ == "__main__":
    main()
