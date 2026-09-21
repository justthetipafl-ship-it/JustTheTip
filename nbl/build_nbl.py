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
import csv, json, os, re, unicodedata, datetime
from collections import defaultdict

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# The source renames clubs between seasons and even between feeds: the Breakers are "Breakers" in
# the 2024-25 results, "NZL" in 2025-26, and "NZ Breakers" in the 2026-27 box scores. Left alone
# that splits one club into three, so teams.json lists 11 clubs for a 10-team league and every
# rank and DVP average is computed against a phantom opponent.
CODE_ALIAS = {
    "nzbreakers": "NZL", "breakers": "NZL", "newzealandbreakers": "NZL",
    "southeastmelbournephoenix": "SEM", "phoenix": "SEM",
    "tasmaniajackjumpers": "TAS", "jackjumpers": "TAS",
}
def canon(code):
    if not code:
        return code
    k = "".join(ch for ch in str(code).lower() if ch.isalnum())
    return CODE_ALIAS.get(k, code)


def fold(name):
    # books and box scores disagree on accents and punctuation (Dell'Orso, accented imports)
    import unicodedata
    n = unicodedata.normalize("NFD", str(name or ""))
    return "".join(ch for ch in n if unicodedata.category(ch) != "Mn" and ch.isalnum()).lower()


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
                tmap[norm(full)] = {"code": canon(nick), "full": full, "logo": logo or None}
    def team_of(full, short):
        t = tmap.get(norm(full)) or tmap.get(norm(short)) or {"code": (short or full or "?"), "full": full or short, "logo": None}
        return dict(t, code=canon(t["code"]))

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
    GL_N, AGG_N = 3, 2
    gl_seasons = seasons[-GL_N:] if len(seasons) >= GL_N else seasons   # gamelog history (>= 2 seasons)
    recent = seasons[-AGG_N:] if len(seasons) >= AGG_N else seasons     # player/team aggregates = current form
    # Keyed by player, not player+club. Keying by both meant anyone who changed clubs - or is back
    # from overseas with one game so far - had fewer than 3 games under each key and vanished,
    # which is how 7 players with posted odds (Taran Armstrong, Skylar Mays, Jacob Rigoni...)
    # were missing from players.json. His club is wherever he played most recently.
    pacc = {}
    cur_season = seasons[-1] if seasons else None
    for yr in recent:
        for r in sorted(by_season[yr], key=lambda x: x["Date"]):
            k = r["PlayerId"] or r["Player"]
            d = pacc.setdefault(k, {"name": r["Player"], "team": r["Team"], "pid": r["PlayerId"],
                                    "pos": [], "g": 0, "st": 0, "sum": defaultdict(float),
                                    "last": "", "cur": 0})
            d["g"] += 1; d["st"] += r["starter"]; d["pos"].append(r["pos"])
            if r["Date"] >= d["last"]:
                d["last"] = r["Date"]; d["team"] = r["Team"]; d["name"] = r["Player"]
            if yr == cur_season:
                d["cur"] += 1
            for s_ in ("points", "rebounds", "assists", "threes", "threesAtt", "fgm", "fga", "ftm", "fta",
                       "oreb", "dreb", "steals", "blocks", "turnovers", "minutes"):
                if r.get(s_) is not None: d["sum"][s_] += r[s_]
    tfull = {v["code"]: v["full"] for v in tmap.values()}
    players = []
    for d in pacc.values():
        g = max(1, d["g"])
        pos5 = max(set(d["pos"]), key=d["pos"].count) if d["pos"] else "F"
        row = {"playerId": d["pid"], "name": d["name"], "team": d["team"], "teamFull": tfull.get(d["team"], d["team"]),
               "position": pos5, "pos5": pos5, "games": d["g"], "starterPct": round(d["st"] / g, 2),
               "role": "starter" if d["st"] / g >= 0.5 else "bench"}
        for s_ in ("points", "rebounds", "assists", "threes", "threesAtt", "fgm", "fga", "ftm", "fta", "oreb", "dreb", "steals", "blocks", "turnovers", "minutes"):
            row[s_] = round(d["sum"][s_] / g, 2)
        # 3+ games across the window, OR on a roster this season - a player who has taken the
        # court this year is on a team sheet and the books will price him
        if row["games"] >= 3 or d["cur"] >= 1:
            players.append(row)

    # Anyone the books are pricing is, by definition, expected to play. A player back from
    # overseas (Taran Armstrong: 20 games for Cairns in 2024-25, none since) has no rows in the
    # two-season window, so without this he is priced but unknown to every signal. Keep him on
    # his full history, flagged, with his last known club.
    try:
        odds = json.load(open(os.path.join(DATA, "odds.json")))
        priced = set()
        for arr in (odds.get("books") or odds.get("lines") or [], odds.get("alt") or []):
            for o in arr:
                if o.get("player") and o.get("over") is not None:
                    priced.add(fold(o["player"]))
    except Exception:
        priced = set()
    have = {fold(p["name"]) for p in players}
    back = {}
    for yr in gl_seasons:
        for r in sorted(by_season[yr], key=lambda x: x["Date"]):
            f = fold(r["Player"])
            if f not in priced or f in have:
                continue
            d = back.setdefault(f, {"name": r["Player"], "team": r["Team"], "pid": r["PlayerId"],
                                    "pos": [], "g": 0, "st": 0, "sum": defaultdict(float), "yr": yr})
            d["g"] += 1; d["st"] += r["starter"]; d["pos"].append(r["pos"]); d["team"] = r["Team"]; d["yr"] = yr
            for s_ in ("points", "rebounds", "assists", "threes", "threesAtt", "fgm", "fga", "ftm", "fta",
                       "oreb", "dreb", "steals", "blocks", "turnovers", "minutes"):
                if r.get(s_) is not None: d["sum"][s_] += r[s_]
    for d in back.values():
        if d["g"] < 3:
            continue
        g = d["g"]
        pos5 = max(set(d["pos"]), key=d["pos"].count) if d["pos"] else "F"
        row = {"playerId": d["pid"], "name": d["name"], "team": d["team"], "teamFull": tfull.get(d["team"], d["team"]),
               "position": pos5, "pos5": pos5, "games": g, "starterPct": round(d["st"] / g, 2),
               "role": "starter" if d["st"] / g >= 0.5 else "bench", "lastSeason": d["yr"], "returning": True}
        for s_ in ("points", "rebounds", "assists", "threes", "threesAtt", "fgm", "fga", "ftm", "fta", "oreb", "dreb", "steals", "blocks", "turnovers", "minutes"):
            row[s_] = round(d["sum"][s_] / g, 2)
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
        row["logo"] = (tmap.get(norm(tfull.get(tm, tm))) or {}).get("logo")
        teams.append(row)

    # ---- DVP: each defence's most recent DVP_GAMES games, across seasons ----
    # Built from the current season alone this was ONE game per team at the start of a season -
    # 20 rows of noise that made the model measurably worse (Brier 0.2436 -> 0.2573 when added).
    # A rolling window of the defence's last 22 games (roughly a season) keeps it current without
    # collapsing to a single result every October.
    DVP_STATS = ("points", "rebounds", "assists", "threes", "steals", "blocks", "turnovers", "pra", "pr", "pa", "ra", "stocks")
    DVP_GAMES = 22
    all_rows = [r for yr in gl_seasons for r in by_season.get(yr, [])]
    match_date = {}
    for r in all_rows:
        match_date[r["MatchId"]] = r["Date"]
    defended = defaultdict(set)                # defending team -> match ids
    for r in all_rows:
        defended[r["Opp"]].add(r["MatchId"])
    keep = {}
    for T, mids in defended.items():
        recent_mids = sorted(mids, key=lambda m: match_date.get(m, ""), reverse=True)[:DVP_GAMES]
        keep[T] = set(recent_mids)
    dvp_acc = {}
    for r in all_rows:
        T, pos, mid = r["Opp"], r["pos"], r["MatchId"]
        if mid not in keep.get(T, ()):
            continue
        d = dvp_acc.setdefault((T, pos), defaultdict(float))
        for st in DVP_STATS:
            if r.get(st) is not None:
                d[st] += r[st]
    dvp = []
    for (T, pos), d in dvp_acc.items():
        g = max(1, len(keep.get(T, ())))
        row = {"team": T, "pos": pos, "games": len(keep.get(T, ()))}
        for st in DVP_STATS:
            row[st] = round(d[st] / g, 2)
        dvp.append(row)

    # ---- results + fixtures ----
    results, fixtures = [], []
    rec_set = set(gl_seasons)
    fx_cutoff = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()   # drop stale games stuck in SCHEDULED
    for r in res:
        if r.get("match_status") == "COMPLETE":
            if end_year(r.get("season")) not in rec_set:   # keep completed results to recent seasons only
                continue
            results.append({"season": r.get("season"), "gameId": r.get("match_id"),
                            "date": (r.get("match_time_utc") or "")[:10],
                            "home": canon(r.get("home_team_nickname")), "away": canon(r.get("away_team_nickname")),
                            "hs": num(r.get("home_score_string")), "as": num(r.get("away_score_string"))})
        elif r.get("match_status") == "SCHEDULED":   # upcoming fixtures — exclude stale games stuck in SCHEDULED (nblR quirk)
            _fd = (r.get("match_time_utc") or "")[:10]
            if _fd and _fd >= fx_cutoff:
                fixtures.append({"gameId": r.get("match_id"), "home": canon(r.get("home_team_nickname")), "away": canon(r.get("away_team_nickname")),
                                 "utc": r.get("match_time_utc"), "date": _fd,
                                 "venue": r.get("venue_name"), "gw": r.get("round_number")})
    fixtures.sort(key=lambda x: x["date"] or "")

    # ---- write ----
    os.makedirs(DATA, exist_ok=True)
    cur = gl_seasons[-1] if gl_seasons else "0"
    gl_files = []
    for yr in gl_seasons:
        n = "gamelogs_%s.json" % yr
        json.dump(by_season[yr], open(os.path.join(DATA, n), "w"), separators=(",", ":"))
        gl_files.append(n)
    meta = {"league": "nbl", "label": "NBL", "sportKey": "nbl", "seasons": gl_seasons, "currentSeason": cur,
            "gamelogFiles": gl_files, "day": None,
            "summary": {"players": len(players), "teams": len(teams), "gamelogs": sum(len(by_season[y]) for y in gl_seasons),
                        "results": len(results), "fixtures": len(fixtures), "dvp": len(dvp)}}
    for n, obj in [("players.json", players), ("teams.json", teams), ("dvp.json", dvp),
                   ("results.json", results), ("fixture.json", fixtures), ("meta.json", meta)]:
        json.dump(obj, open(os.path.join(DATA, n), "w"), separators=(",", ":"))
    print("NBL build: seasons %s | players %d | teams %d | gamelogs %s | results %d | fixtures %d"
          % (gl_seasons, len(players), len(teams), {y: len(by_season[y]) for y in gl_seasons}, len(results), len(fixtures)))


if __name__ == "__main__":
    main()
