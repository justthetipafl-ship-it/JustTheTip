#!/usr/bin/env python3
"""
JTT NBL build — turns the nblR CSV dumps (box_player.csv, results.csv) into the NBA-tool data
format so the NBL reuses the basketball shell/scoring/signals.

Reads (written by fetch_nbl.R):
  NBL/data/box_player.csv   per-player per-match box scores (nblR nbl_box_player)
  NBL/data/results.csv      match results + schedule (nblR nbl_results wide)

Emits (NBA-tool shapes) into NBL/data/:
  gamelogs_YYYY.json  per-season flat rows [{Year,Date,MatchId,PlayerId,Player,Team,Opp,home,starter,pos,points,...,pra,stocks}]
  players.json        aggregated per player [{playerId,name,team,teamFull,position,pos5,games,role,points,rebounds,...}]
  teams.json          per team for/against averages [{team,teamFull,games,points,points_a,...}]
  results.json        completed games [{season,gameId,date,home,away,hs,as}]
  fixture.json        upcoming games [{home,away,date,venue,gw}]
  meta.json           tool meta {league,label,seasons,currentSeason,gamelogFiles,...}
"""
import csv, json, os, re, unicodedata
from collections import defaultdict

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode("ascii")
    return "".join(c for c in s.lower() if c.isalnum())

def end_year(season):  # "2025-2026" -> "2026"
    m = re.findall(r"\d{4}", str(season or ""))
    return m[-1] if m else "0"

def to_min(v):  # "28:37" -> 28.6
    v = str(v or "").strip()
    if ":" in v:
        try:
            mm, ss = v.split(":")[:2]; return round(int(mm) + int(ss) / 60.0, 1)
        except Exception: return None
    try: return round(float(v), 1)
    except Exception: return None

def num(v):
    try:
        f = float(v); return int(f) if f == int(f) else round(f, 2)
    except Exception: return None

POS5 = {"PG": "G", "SG": "G", "G": "G", "GRD": "G", "GUARD": "G",
        "SF": "F", "PF": "F", "F": "F", "FWD": "F", "FORWARD": "F",
        "C": "C", "CEN": "C", "CENTRE": "C", "CENTER": "C"}
def pos_norm(p):
    p = str(p or "").upper().replace("/", "").strip()
    for k in (p, p[:2], p[:1]):
        if k in POS5: return POS5[k]
    return "F"

def load_csv(name):
    p = os.path.join(DATA, name)
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def main():
    box = load_csv("box_player.csv")
    res = load_csv("results.csv")

    # canonical team map from results (full name -> nickname/logo), the cleanest source
    tmap = {}   # norm(full) -> {code, full, logo}
    for r in res:
        for side in ("home", "away"):
            full = r.get(side + "_team_name"); nick = r.get(side + "_team_nickname")
            logo = r.get(side + "_team_team_logo") or r.get(side + "_team_external_team_logo")
            if full and nick:
                tmap[norm(full)] = {"code": nick, "full": full, "logo": logo or None}
    def team_of(full, short):
        return tmap.get(norm(full)) or tmap.get(norm(short)) or {"code": (short or full or "?"), "full": full or short, "logo": None}

    # match_id -> date + status
    mmeta = {}
    for r in res:
        mid = r.get("match_id")
        if mid:
            mmeta[mid] = {"date": (r.get("match_time_utc") or "")[:10], "status": r.get("match_status"),
                          "venue": r.get("venue_name"), "season": r.get("season")}

    # ---- gamelogs ----
    by_season = defaultdict(list)
    for b in box:
        mid = b.get("match_id"); mm = mmeta.get(mid, {})
        yr = end_year(b.get("season"))
        t = team_of(b.get("team_name"), b.get("team_short_name"))
        o = team_of(b.get("opp_name"), b.get("opp_short_name"))
        pts, reb, ast = num(b.get("points")), num(b.get("rebounds_total")), num(b.get("assists"))
        stl, blk = num(b.get("steals")), num(b.get("blocks"))
        fn = (b.get("first_name") or "").strip(); ln = (b.get("family_name") or "").strip()
        name = (fn + " " + ln).strip() or (b.get("name") or b.get("scoreboard_name") or "").strip()
        row = {
            "Year": yr, "Date": mm.get("date") or "", "MatchId": mid,
            "PlayerId": b.get("player_id"), "Player": name,
            "Team": t["code"], "Opp": o["code"],
            "home": 1 if str(b.get("home_away")).startswith("1") else 0,
            "starter": 1 if str(b.get("starter")) in ("1", "1.0", "True", "true") else 0,
            "pos": pos_norm(b.get("playing_position")),
            "points": pts, "rebounds": reb, "assists": ast,
            "threes": num(b.get("three_pointers_made")), "threesAtt": num(b.get("three_pointers_attempted")),
            "fgm": num(b.get("field_goals_made")), "fga": num(b.get("field_goals_attempted")),
            "ftm": num(b.get("free_throws_made")), "fta": num(b.get("free_throws_attempted")),
            "oreb": num(b.get("rebounds_offensive")), "dreb": num(b.get("rebounds_defensive")),
            "steals": stl, "blocks": blk, "turnovers": num(b.get("turnovers")),
            "fouls": num(b.get("fouls_personal")), "plusMinus": num(b.get("plus_minus")),
            "minutes": to_min(b.get("minutes")),
        }
        p, r_, a = pts or 0, reb or 0, ast or 0
        row["pra"] = p + r_ + a; row["pr"] = p + r_; row["pa"] = p + a; row["ra"] = r_ + a
        row["stocks"] = (stl or 0) + (blk or 0)
        if row["Date"]:
            by_season[yr].append(row)

    # ---- players aggregate (from the most recent 2 seasons) ----
    seasons = sorted(by_season.keys())
    recent = seasons[-2:] if len(seasons) >= 2 else seasons
    pacc = {}
    for yr in recent:
        for r in by_season[yr]:
            k = r["Player"] + "|" + r["Team"]
            d = pacc.setdefault(k, {"name": r["Player"], "team": r["Team"], "pid": r["PlayerId"],
                                    "pos": [], "g": 0, "st": 0, "sum": defaultdict(float)})
            d["g"] += 1; d["st"] += r["starter"]; d["pos"].append(r["pos"])
            for s in ("points", "rebounds", "assists", "threes", "threesAtt", "fgm", "fga", "ftm", "fta",
                      "oreb", "dreb", "steals", "blocks", "turnovers", "minutes"):
                if r.get(s) is not None: d["sum"][s] += r[s]
    tfull = {v["code"]: v["full"] for v in tmap.values()}
    players = []
    for d in pacc.values():
        g = max(1, d["g"])
        pos5 = max(set(d["pos"]), key=d["pos"].count) if d["pos"] else "F"
        row = {"playerId": d["pid"], "name": d["name"], "team": d["team"], "teamFull": tfull.get(d["team"], d["team"]),
               "position": pos5, "pos5": pos5, "games": d["g"], "starterPct": round(d["st"] / g, 2),
               "role": "starter" if d["st"] / g >= 0.5 else "bench"}
        for s in ("points", "rebounds", "assists", "threes", "threesAtt", "fgm", "fga", "ftm", "fta", "oreb", "dreb", "steals", "blocks", "turnovers", "minutes"):
            row[s] = round(d["sum"][s] / g, 2)
        if row["games"] >= 3:
            players.append(row)

    # ---- teams aggregate (for/against) ----
    tacc = {}
    for yr in recent:
        # team totals per match
        by_match_team = defaultdict(lambda: defaultdict(float))
        match_team = defaultdict(set)
        for r in by_season[yr]:
            key = (r["MatchId"], r["Team"])
            match_team[r["MatchId"]].add(r["Team"])
            for s in ("points", "rebounds", "assists", "threes", "threesAtt", "fgm", "fga", "ftm", "fta", "oreb"):
                if r.get(s) is not None: by_match_team[key][s] += r[s]
        for (mid, tm), tot in by_match_team.items():
            opp = [x for x in match_team.get(mid, []) if x != tm]
            opp = opp[0] if opp else None
            d = tacc.setdefault(tm, {"g": 0, "for": defaultdict(float), "ag": defaultdict(float)})
            d["g"] += 1
            for s, v in tot.items(): d["for"][s] += v
            if opp:
                for s, v in by_match_team.get((mid, opp), {}).items(): d["ag"][s] += v
    teams = []
    for tm, d in tacc.items():
        g = max(1, d["g"]); row = {"team": tm, "teamFull": tfull.get(tm, tm), "games": d["g"]}
        for s in ("points", "rebounds", "assists", "threes", "threesAtt", "fgm", "fga", "ftm", "fta", "oreb"):
            row[s] = round(d["for"][s] / g, 2); row[s + "_a"] = round(d["ag"][s] / g, 2)
        teams.append(row)

    # ---- results + fixtures ----
    results, fixtures = [], []
    rec_set = set(recent)
    for r in res:
        if r.get("match_status") == "COMPLETE":
            if end_year(r.get("season")) not in rec_set:   # keep completed results to recent seasons only
                continue
            results.append({"season": r.get("season"), "gameId": r.get("match_id"),
                            "date": (r.get("match_time_utc") or "")[:10],
                            "home": r.get("home_team_nickname"), "away": r.get("away_team_nickname"),
                            "hs": num(r.get("home_score_string")), "as": num(r.get("away_score_string"))})
        elif r.get("match_status") == "SCHEDULED":   # keep ALL upcoming fixtures (may be a future season)
            fixtures.append({"home": r.get("home_team_nickname"), "away": r.get("away_team_nickname"),
                             "date": (r.get("match_time_utc") or "")[:10] or (r.get("match_time_utc") or ""),
                             "venue": r.get("venue_name"), "gw": r.get("round_number")})
    fixtures.sort(key=lambda x: x["date"] or "")

    # ---- write ----
    os.makedirs(DATA, exist_ok=True)
    cur = recent[-1] if recent else "0"
    gl_files = []
    for yr in recent:
        n = "gamelogs_%s.json" % yr
        json.dump(by_season[yr], open(os.path.join(DATA, n), "w"), separators=(",", ":"))
        gl_files.append(n)
    meta = {"league": "nbl", "label": "NBL", "sportKey": "nbl", "seasons": recent, "currentSeason": cur,
            "gamelogFiles": gl_files, "day": None,
            "summary": {"players": len(players), "teams": len(teams), "results": len(results), "fixtures": len(fixtures)}}
    for n, obj in [("players.json", players), ("teams.json", teams),
                   ("results.json", results), ("fixture.json", fixtures), ("meta.json", meta)]:
        json.dump(obj, open(os.path.join(DATA, n), "w"), separators=(",", ":"))
    print("NBL build: seasons %s | players %d | teams %d | gamelogs %s | results %d | fixtures %d"
          % (recent, len(players), len(teams), {y: len(by_season[y]) for y in recent}, len(results), len(fixtures)))


if __name__ == "__main__":
    main()
