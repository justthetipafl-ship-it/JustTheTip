#!/usr/bin/env python3
"""
fetch_mlb.py — JTT MLB daily data pipeline.

Pulls today's slate from the free MLB Stats API (statsapi.mlb.com) and writes
data/mlb_bundle.json + data/version.txt in the schema JTT_MLB.html expects.

No API key required. Run daily (see mlb-daily-update.yml). Phase 1 = Stats API
only (no Statcast). Run locally:  python3 fetch_mlb.py [YYYY-MM-DD]

Dependency: requests  (pip install requests)
"""
import sys, os, json, time, datetime
import requests

BASE = "https://statsapi.mlb.com/api/v1"
HERE = os.path.dirname(os.path.abspath(__file__))
SLEEP = 0.10                     # polite throttle between calls
LG_OPPAVG, LG_KRATE, LG_HR9 = .245, .225, 1.15

# MLBAM teamId -> park-factor key (matches park_factors.json). Home park drives factors.
TEAM_PARK = {
    108:"LAA",109:"ARI",110:"BAL",111:"BOS",112:"CHC",113:"CIN",114:"CLE",115:"COL",
    116:"DET",117:"HOU",118:"KC",119:"LAD",120:"WSH",121:"NYM",133:"OAK",134:"PIT",
    135:"SD",136:"SEA",137:"SF",138:"STL",139:"TB",140:"TEX",141:"TOR",142:"MIN",
    143:"PHI",144:"ATL",145:"CWS",146:"MIA",147:"NYY",158:"MIL",
}

# Populated in main() from the teams endpoint: teamId -> official abbreviation.
TEAM_ABBR = {}

_session = requests.Session()
_session.headers.update({"User-Agent": "JTT-MLB/1.0 (research tool)"})

def api(path, **params):
    """GET BASE/path with simple retry/backoff. Returns parsed JSON or {}."""
    url = f"{BASE}/{path}"
    for attempt in range(4):
        try:
            r = _session.get(url, params=params, timeout=25)
            if r.status_code == 200:
                time.sleep(SLEEP)
                return r.json()
            if r.status_code in (429, 500, 502, 503):
                time.sleep(1.5 * (attempt + 1)); continue
            return {}
        except requests.RequestException:
            time.sleep(1.0 * (attempt + 1))
    return {}

def parse_ip(v):
    """MLB innings-pitched notation '6.1' = 6 IP + 1 out. Returns (outs, true_ip)."""
    try:
        s = str(v); whole, _, frac = s.partition(".")
        outs = int(whole) * 3 + (int(frac) if frac else 0)
        return outs, round(outs / 3, 2)
    except Exception:
        return 0, 0.0

def f(x, d=0.0):
    try: return float(x)
    except (TypeError, ValueError): return d

def i(x, d=0):
    try: return int(x)
    except (TypeError, ValueError): return d

# ---------------------------------------------------------------- teams
def load_teams():
    """All MLB teams: id -> {abbr,name,div,park,kRate(filled later)}."""
    data = api("teams", sportId=1)
    out = {}
    for t in data.get("teams", []):
        tid = t.get("id")
        if tid not in TEAM_PARK:
            continue
        out[tid] = {
            "id": tid,
            "abbr": t.get("abbreviation", "?"),
            "name": t.get("name", "?"),
            "div": (t.get("division") or {}).get("nameShort", ""),
            "park": TEAM_PARK[tid],
            "kRate": LG_KRATE,
        }
    return out

def team_krate(tid, season):
    d = api(f"teams/{tid}/stats", stats="season", group="hitting", season=season, sportId=1)
    try:
        st = d["stats"][0]["splits"][0]["stat"]
        pa = f(st.get("plateAppearances")); so = f(st.get("strikeOuts"))
        return round(so / pa, 3) if pa else LG_KRATE
    except Exception:
        return LG_KRATE

# ---------------------------------------------------------------- schedule
def load_schedule(date):
    d = api("schedule", sportId=1, date=date,
            hydrate="probablePitcher,lineups,team,venue")
    games = []
    for day in d.get("dates", []):
        for g in day.get("games", []):
            games.append(g)
    return games

def people_hands(ids):
    """Batch fetch batSide/pitchHand for a set of person ids."""
    if not ids: return {}
    out = {}
    ids = list(ids)
    for k in range(0, len(ids), 40):
        chunk = ids[k:k+40]
        d = api("people", personIds=",".join(map(str, chunk)))
        for p in d.get("people", []):
            out[p["id"]] = {
                "bats": (p.get("batSide") or {}).get("code", "R"),
                "throws": (p.get("pitchHand") or {}).get("code", "R"),
                "name": p.get("fullName", "?"),
                "pos": (p.get("primaryPosition") or {}).get("abbreviation", ""),
            }
    return out

# ---------------------------------------------------------------- batters
def batter_season(pid, season):
    d = api(f"people/{pid}/stats", stats="season", group="hitting", season=season, gameType="R")
    try: st = d["stats"][0]["splits"][0]["stat"]
    except Exception: return None
    return {
        "G": i(st.get("gamesPlayed")), "PA": i(st.get("plateAppearances")),
        "AB": i(st.get("atBats")), "H": i(st.get("hits")), "2B": i(st.get("doubles")),
        "3B": i(st.get("triples")), "HR": i(st.get("homeRuns")), "RBI": i(st.get("rbi")),
        "R": i(st.get("runs")), "SB": i(st.get("stolenBases")), "BB": i(st.get("baseOnBalls")),
        "SO": i(st.get("strikeOuts")), "AVG": f(st.get("avg")), "OBP": f(st.get("obp")),
        "SLG": f(st.get("slg")), "TB": i(st.get("totalBases")), "OPS": f(st.get("ops")),
    }

def hand_splits_bat(pid, season):
    d = api(f"people/{pid}/stats", stats="statSplits", sitCodes="vl,vr",
            group="hitting", season=season)
    res = {"splitVsL": None, "splitVsR": None}
    try:
        for sp in d["stats"][0]["splits"]:
            code = sp.get("split", {}).get("code"); st = sp["stat"]
            obj = {"PA": i(st.get("plateAppearances")), "AVG": f(st.get("avg")),
                   "SLG": f(st.get("slg")), "HR": i(st.get("homeRuns")), "OPS": f(st.get("ops"))}
            if code == "vl": res["splitVsL"] = obj
            elif code == "vr": res["splitVsR"] = obj
    except Exception:
        pass
    # fall back to season-ish numbers if a split is missing
    return res

def gamelog_bat(pid, season, idmap, limit=20):
    d = api(f"people/{pid}/stats", stats="gameLog", group="hitting", season=season)
    log = []
    try: splits = d["stats"][0]["splits"]
    except Exception: splits = []
    for sp in reversed(splits):  # API is oldest-first; we want newest-first
        st = sp["stat"]; opp = (sp.get("opponent") or {}).get("id")
        log.append({
            "date": sp.get("date", ""), "opp": idmap.get(opp, "?"),
            "H": i(st.get("hits")), "TB": i(st.get("totalBases")), "HR": i(st.get("homeRuns")),
            "RBI": i(st.get("rbi")), "R": i(st.get("runs")), "SB": i(st.get("stolenBases")),
            "BB": i(st.get("baseOnBalls")), "SO": i(st.get("strikeOuts")),
        })
        if len(log) >= limit: break
    return log

def bvp(pid, opp_pitcher_id, season):
    d = api(f"people/{pid}/stats", stats="vsPlayerTotal",
            group="hitting", opposingPlayerId=opp_pitcher_id, season=season)
    try:
        st = d["stats"][0]["splits"][0]["stat"]
        return {"PA": i(st.get("plateAppearances")), "H": i(st.get("hits")),
                "HR": i(st.get("homeRuns")), "TB": i(st.get("totalBases")), "AVG": f(st.get("avg"))}
    except Exception:
        return None

# ---------------------------------------------------------------- pitchers
def pitcher_season(pid, season):
    d = api(f"people/{pid}/stats", stats="season", group="pitching", season=season, gameType="R")
    try: st = d["stats"][0]["splits"][0]["stat"]
    except Exception: return None
    outs, ip = parse_ip(st.get("inningsPitched", "0"))
    return {
        "GS": i(st.get("gamesStarted")), "IP": ip, "K": i(st.get("strikeOuts")),
        "BB": i(st.get("baseOnBalls")), "H": i(st.get("hits")), "ER": i(st.get("earnedRuns")),
        "HR": i(st.get("homeRuns")), "ERA": f(st.get("era")), "WHIP": f(st.get("whip")),
        "K9": f(st.get("strikeoutsPer9Inn")), "BB9": f(st.get("walksPer9Inn")),
        "HR9": f(st.get("homeRunsPer9")), "oppAVG": f(st.get("avg")),
    }

def hand_splits_pit(pid, season):
    d = api(f"people/{pid}/stats", stats="statSplits", sitCodes="vl,vr",
            group="pitching", season=season)
    res = {"splitVsL": None, "splitVsR": None}
    try:
        for sp in d["stats"][0]["splits"]:
            code = sp.get("split", {}).get("code"); st = sp["stat"]
            obj = {"K9": f(st.get("strikeoutsPer9Inn")), "oppAVG": f(st.get("avg")),
                   "HR9": f(st.get("homeRunsPer9"))}
            if code == "vl": res["splitVsL"] = obj
            elif code == "vr": res["splitVsR"] = obj
    except Exception:
        pass
    return res

def gamelog_pit(pid, season, idmap, limit=20):
    d = api(f"people/{pid}/stats", stats="gameLog", group="pitching", season=season)
    log = []
    try: splits = d["stats"][0]["splits"]
    except Exception: splits = []
    for sp in reversed(splits):
        st = sp["stat"]; opp = (sp.get("opponent") or {}).get("id")
        outs, ip = parse_ip(st.get("inningsPitched", "0"))
        log.append({
            "date": sp.get("date", ""), "opp": idmap.get(opp, "?"), "IP": ip,
            "K": i(st.get("strikeOuts")), "BB": i(st.get("baseOnBalls")), "H": i(st.get("hits")),
            "ER": i(st.get("earnedRuns")), "HR": i(st.get("homeRuns")),
            "outs": outs, "win": i(st.get("wins")) == 1,
        })
        if len(log) >= limit: break
    return log

# ---------------------------------------------------------------- league extras
DIV_NAMES = {200:"AL West",201:"AL East",202:"AL Central",
             203:"NL West",204:"NL East",205:"NL Central"}

def load_standings(season):
    d = api("standings", leagueId="103,104", season=season, standingsTypes="regularSeason")
    out = []
    for rec in d.get("records", []):
        div_id = (rec.get("division") or {}).get("id")
        rows = []
        for tr in rec.get("teamRecords", []):
            t = tr.get("team", {})
            l10 = ""
            for sr in (tr.get("records", {}).get("splitRecords", []) or []):
                if sr.get("type") == "lastTen":
                    l10 = f"{sr.get('wins',0)}-{sr.get('losses',0)}"
            rows.append({
                "abbr": TEAM_ABBR.get(t.get("id"), t.get("abbreviation", "?")),
                "name": t.get("name", "?"),
                "w": i(tr.get("wins")), "l": i(tr.get("losses")),
                "pct": f(tr.get("winningPercentage")),
                "gb": str(tr.get("gamesBack", "—")),
                "rs": i(tr.get("runsScored")), "ra": i(tr.get("runsAllowed")),
                "diff": i(tr.get("runDifferential")),
                "l10": l10 or "—",
                "streak": (tr.get("streak") or {}).get("streakCode", "—"),
            })
        out.append({"div": DIV_NAMES.get(div_id, "Division"), "teams": rows})
    # order AL then NL, East/Central/West
    order = ["AL East","AL Central","AL West","NL East","NL Central","NL West"]
    out.sort(key=lambda d: order.index(d["div"]) if d["div"] in order else 99)
    return out

LEADER_CATS_BAT = {"AVG":"battingAverage","HR":"homeRuns","RBI":"rbi",
                   "OPS":"onBasePlusSlugging","SB":"stolenBases"}
LEADER_CATS_PIT = {"ERA":"earnedRunAverage","K":"strikeouts","WHIP":"walksAndHitsPerInningPitched"}

def _leader_rows(cat, group, season, n=8):
    d = api("stats/leaders", leaderCategories=cat, season=season,
            sportId=1, statGroup=group, limit=n)
    rows = []
    for cat_block in d.get("leagueLeaders", []):
        for L in cat_block.get("leaders", [])[:n]:
            p = L.get("person", {}); team = L.get("team", {})
            val = L.get("value")
            try: val = float(val) if "." in str(val) else int(val)
            except (TypeError, ValueError): pass
            rows.append({"playerId": p.get("id"), "name": p.get("fullName", "?"),
                         "abbr": TEAM_ABBR.get(team.get("id"), "?"), "val": val})
    return rows

def load_league_leaders(season):
    return {
        "batting":  {k: _leader_rows(v, "hitting", season)  for k, v in LEADER_CATS_BAT.items()},
        "pitching": {k: _leader_rows(v, "pitching", season) for k, v in LEADER_CATS_PIT.items()},
    }

def load_league_trends(season, standings):
    all_rows = [t for d in standings for t in d["teams"]]
    tot_gp = sum(t["w"] + t["l"] for t in all_rows)
    tot_rs = sum(t["rs"] for t in all_rows)
    trends = {"games": tot_gp // 2 if tot_gp else 0, "league": "MLB",
              "avgRunsPerGame": round(tot_rs / tot_gp, 2) if tot_gp else None,
              "avgTotalPerGame": round(2 * tot_rs / tot_gp, 2) if tot_gp else None,
              "avgHRPerGame": None}
    # league HR/game from one aggregate hitting call
    d = api("stats", stats="season", group="hitting", season=season, sportId=1)
    try:
        st = d["stats"][0]["splits"][0]["stat"]
        hr = f(st.get("homeRuns")); g = f(st.get("gamesPlayed"))
        if g: trends["avgHRPerGame"] = round(hr / g, 2)
    except Exception:
        pass
    return trends

def team_recent(tid, date, season, n=8):
    end = date
    start = (datetime.date.fromisoformat(date) - datetime.timedelta(days=18)).isoformat()
    d = api("schedule", sportId=1, teamId=tid, startDate=start, endDate=end,
            gameType="R", hydrate="team")
    games = []
    for day in d.get("dates", []):
        for g in day.get("games", []):
            if (g.get("status") or {}).get("abstractGameState") != "Final":
                continue
            gt = g.get("teams", {})
            home, away = gt.get("home", {}), gt.get("away", {})
            is_home = (home.get("team") or {}).get("id") == tid
            me, opp = (home, away) if is_home else (away, home)
            rf = i(me.get("score")); ra = i(opp.get("score"))
            games.append({"date": g.get("officialDate", g.get("gameDate", "")[:10]),
                          "opp": TEAM_ABBR.get((opp.get("team") or {}).get("id"), "?"),
                          "rf": rf, "ra": ra, "res": "W" if rf > ra else "L"})
    games.sort(key=lambda x: x["date"], reverse=True)
    return games[:n]

# ---------------------------------------------------------------- build
def main():
    date = sys.argv[1] if len(sys.argv) > 1 else \
        datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-4))).strftime("%Y-%m-%d")
    season = int(date[:4])
    print(f"[JTT MLB] fetching slate for {date} (season {season})")

    with open(os.path.join(HERE, "park_factors.json")) as fh:
        parks = {k: v for k, v in json.load(fh).items() if not k.startswith("_")}

    teams = load_teams()
    idmap = {tid: t["abbr"] for tid, t in teams.items()}
    TEAM_ABBR.update(idmap)

    games = load_schedule(date)
    print(f"[JTT MLB] {len(games)} games on schedule")
    if not games:
        print("[JTT MLB] no games — writing empty slate")

    slate, starter_ids, team_ids_playing = [], {}, set()
    for g in games:
        gt = g.get("teams", {})
        away_t, home_t = gt.get("away", {}), gt.get("home", {})
        atid = (away_t.get("team") or {}).get("id"); htid = (home_t.get("team") or {}).get("id")
        if atid not in teams or htid not in teams:
            continue
        team_ids_playing.update([atid, htid])
        asp = (away_t.get("probablePitcher") or {})
        hsp = (home_t.get("probablePitcher") or {})
        gd = g.get("gameDate", "")
        try:
            tloc = datetime.datetime.fromisoformat(gd.replace("Z", "+00:00")).strftime("%H:%M")
        except Exception:
            tloc = ""
        slate.append({
            "gamePk": g.get("gamePk"), "date": date, "time": tloc,
            "status": (g.get("status") or {}).get("detailedState", "Scheduled"),
            "parkId": TEAM_PARK.get(htid),
            "away": {"teamId": atid, "abbr": teams[atid]["abbr"],
                     "probablePitcher": {"id": asp.get("id"), "name": asp.get("fullName", "TBD"),
                                         "throws": "R"}},
            "home": {"teamId": htid, "abbr": teams[htid]["abbr"],
                     "probablePitcher": {"id": hsp.get("id"), "name": hsp.get("fullName", "TBD"),
                                         "throws": "R"}},
            "lines": None,  # Stats API has no odds; wire a book feed in Phase 2 if desired
            "lineups": {"away": [], "home": []},
        })
        if asp.get("id"): starter_ids[asp["id"]] = htid   # this pitcher faces the HOME lineup
        if hsp.get("id"): starter_ids[hsp["id"]] = atid

    # hands for probable pitchers
    hands = people_hands(set(starter_ids.keys()))
    for game in slate:
        for side in ("away", "home"):
            pid = game[side]["probablePitcher"]["id"]
            if pid in hands:
                game[side]["probablePitcher"]["throws"] = hands[pid]["throws"]

    # team K-rates
    for tid in team_ids_playing:
        teams[tid]["kRate"] = team_krate(tid, season)

    # which probable starter does each TEAM face today?
    opp_starter_for_team = {}     # teamId -> opposing pitcher id
    for game in slate:
        a, h = game["away"], game["home"]
        if h["probablePitcher"]["id"]: opp_starter_for_team[a["teamId"]] = h["probablePitcher"]["id"]
        if a["probablePitcher"]["id"]: opp_starter_for_team[h["teamId"]] = a["probablePitcher"]["id"]

    # roster hitters per playing team (active roster; lineup overrides order when posted)
    batters, pitchers = {}, {}
    for tid in sorted(team_ids_playing):
        roster = api(f"teams/{tid}/roster", rosterType="active")
        hitters = [r for r in roster.get("roster", [])
                   if (r.get("position") or {}).get("type") != "Pitcher"]
        # batch batter hands for this team in one call
        hitter_ids = [(r.get("person") or {}).get("id") for r in hitters if (r.get("person") or {}).get("id")]
        bat_hands = people_hands(hitter_ids)
        opp_pid = opp_starter_for_team.get(tid)
        order = 1
        ids_for_lineup = []
        for r in hitters:
            person = r.get("person", {}); pid = person.get("id")
            if not pid: continue
            season_s = batter_season(pid, season)
            if not season_s or season_s["PA"] < 20:   # skip tiny-sample bench arms-up
                continue
            splits = hand_splits_bat(pid, season)
            # default missing splits to season-equivalent
            seas_eq = {"PA": season_s["PA"], "AVG": season_s["AVG"], "SLG": season_s["SLG"],
                       "HR": season_s["HR"], "OPS": season_s["OPS"]}
            handinfo = bat_hands.get(pid, {})
            prof = {
                "id": pid, "name": person.get("fullName", "?"),
                "bats": (handinfo.get("bats") if handinfo else "R") or "R",
                "pos": (r.get("position") or {}).get("abbreviation", ""),
                "tier": 1 if season_s["HR"] >= 15 else 2 if season_s["HR"] >= 7 else 3,
                "teamId": tid, "abbr": teams[tid]["abbr"], "order": order,
                "season": season_s,
                "splitVsL": splits["splitVsL"] or seas_eq,
                "splitVsR": splits["splitVsR"] or seas_eq,
                "gameLog": gamelog_bat(pid, season, idmap),
                "bvp": {},
            }
            if opp_pid:
                v = bvp(pid, opp_pid, season)
                if v and v["PA"] > 0:
                    prof["bvp"][str(opp_pid)] = v
            batters[str(pid)] = prof
            ids_for_lineup.append(pid)
            order += 1
        # attach lineup ids to the matching slate game/side
        for game in slate:
            if game["away"]["teamId"] == tid: game["lineups"]["away"] = ids_for_lineup
            if game["home"]["teamId"] == tid: game["lineups"]["home"] = ids_for_lineup
        teams[tid]["recent"] = team_recent(tid, date, season)
        print(f"[JTT MLB]   {teams[tid]['abbr']}: {len(ids_for_lineup)} hitters")

    # probable starters full profiles
    for pid, opp_tid in starter_ids.items():
        season_s = pitcher_season(pid, season)
        if not season_s: continue
        splits = hand_splits_pit(pid, season)
        seas_eq = {"K9": season_s["K9"], "oppAVG": season_s["oppAVG"], "HR9": season_s["HR9"]}
        # find this pitcher's own team
        own_tid = None
        for game in slate:
            if game["away"]["probablePitcher"]["id"] == pid: own_tid = game["away"]["teamId"]
            if game["home"]["probablePitcher"]["id"] == pid: own_tid = game["home"]["teamId"]
        pitchers[str(pid)] = {
            "id": pid, "name": (hands.get(pid) or {}).get("name", "?"),
            "throws": (hands.get(pid) or {}).get("throws", "R"), "role": "SP",
            "tier": 1 if season_s["K9"] >= 9.5 else 2 if season_s["K9"] >= 8 else 3,
            "teamId": own_tid, "abbr": teams.get(own_tid, {}).get("abbr", "?"),
            "season": season_s,
            "splitVsL": splits["splitVsL"] or seas_eq,
            "splitVsR": splits["splitVsR"] or seas_eq,
            "gameLog": gamelog_pit(pid, season, idmap),
        }
    print(f"[JTT MLB]   {len(pitchers)} probable starters profiled")

    # league-wide extras for the League page
    standings = load_standings(season)
    league_leaders = load_league_leaders(season)
    trends = load_league_trends(season, standings)
    print(f"[JTT MLB]   standings divisions={len(standings)} "
          f"trends avgTotal={trends.get('avgTotalPerGame')}")

    bundle = {
        "generated": int(time.time()), "season": season, "asOf": date,
        "teams": {str(tid): t for tid, t in teams.items() if tid in team_ids_playing},
        "parks": parks, "slate": slate, "batters": batters, "pitchers": pitchers,
        "standings": standings, "leagueLeaders": league_leaders, "trends": trends,
    }
    os.makedirs(os.path.join(HERE, "data"), exist_ok=True)
    with open(os.path.join(HERE, "data", "mlb_bundle.json"), "w") as fh:
        json.dump(bundle, fh, separators=(",", ":"))
    with open(os.path.join(HERE, "data", "version.txt"), "w") as fh:
        fh.write(str(bundle["generated"]))
    sz = os.path.getsize(os.path.join(HERE, "data", "mlb_bundle.json")) / 1024
    print(f"[JTT MLB] wrote data/mlb_bundle.json  games={len(slate)} "
          f"batters={len(batters)} pitchers={len(pitchers)}  {sz:.0f}KB")

if __name__ == "__main__":
    main()
