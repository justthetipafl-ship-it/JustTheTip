#!/usr/bin/env python3
"""
JTT NHL build — fetches from the free official NHL API (api-web.nhle.com +
api.nhle.com/stats/rest, no keys, runner-friendly) and writes the JTT shell data
format into nhl/data/.

  Sources (NEVER stats.nba-style IP blocks; NHL endpoints are open):
    api-web.nhle.com/v1/roster/{TEAM}/{season}                 rosters -> player ids + positions
    api-web.nhle.com/v1/player/{id}/game-log/{season}/2        per-game skater/goalie logs
    api-web.nhle.com/v1/club-schedule-season/{TEAM}/{season}   schedule -> fixtures + results
    api.nhle.com/stats/rest/en/team/summary?cayenneExp=...     team for/against

  Emits (shell shapes) into nhl/data/:
    gamelogs_YYYY.json  per-season rows [{Year,Date,MatchId,Player,PlayerId,Team,Opp,home,pos,goals,assists,points,shots,ppPoints,pim,toiMin,saves,...}]
    players.json        aggregated [{playerId,name,team,teamFull,position,pos5,games,role,goals,assists,points,shots,...}]
    teams.json          per team for/against [{team,teamFull,games,goalsFor,goalsAgainst,shotsFor,shotsAgainst,...}]
    dvp.json            team x position stat allowed per game
    results.json        completed games ; fixture.json upcoming games ; meta.json tool meta

  Run:   python nhl/build_nhl_data.py --out nhl/data --seasons 20242025,20252026 --current 20252026
  Test:  python nhl/build_nhl_data.py --selftest
"""
import argparse, json, os, sys, time, urllib.request, urllib.error
from collections import defaultdict

WEB = "https://api-web.nhle.com/v1"
STATS = "https://api.nhle.com/stats/rest/en"
PACE = 0.12
TEAMS = ["ANA","BOS","BUF","CGY","CAR","CHI","COL","CBJ","DAL","DET","EDM","FLA",
         "LAK","MIN","MTL","NSH","NJD","NYI","NYR","OTT","PHI","PIT","SJS","SEA",
         "STL","TBL","TOR","VAN","VGK","WSH","WPG","UTA"]
TEAM_FULL = {}  # filled from schedule/team summary when available

def api(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "jtt-nhl/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if i == tries - 1:
                print(f"  [warn] {url} -> {e}")
                return None
            time.sleep(1.0 + i)
        except Exception as e:
            if i == tries - 1:
                print(f"  [warn] {url} -> {e}")
                return None
            time.sleep(1.0 + i)
    return None

def end_year(season):  # "20242025" -> "2025"
    s = str(season); return s[4:8] if len(s) == 8 else s

def toi_min(v):  # "22:15" -> 22.25
    try:
        mm, ss = str(v).split(":")[:2]; return round(int(mm) + int(ss) / 60.0, 2)
    except Exception:
        return None

def num(v):
    try:
        f = float(v); return int(f) if f == int(f) else round(f, 3)
    except Exception:
        return None

POS_MAP = {"C": "C", "L": "L", "R": "R", "D": "D", "G": "G"}
def pos_of(code):
    return POS_MAP.get(str(code or "").upper()[:1], "C")

# ---------------- fetch ----------------
def fetch_rosters(seasons):
    """team -> list of (playerId, name, positionCode, is_goalie)."""
    roster = {}
    for tm in TEAMS:
        players = []
        seen = set()
        for season in seasons:
            j = api(f"{WEB}/roster/{tm}/{season}"); time.sleep(PACE)
            if not j:
                continue
            for grp, goalie in (("forwards", False), ("defensemen", False), ("goalies", True)):
                for p in (j.get(grp) or []):
                    pid = p.get("id")
                    if pid is None or pid in seen:
                        continue
                    seen.add(pid)
                    nm = ((p.get("firstName") or {}).get("default", "") + " " +
                          (p.get("lastName") or {}).get("default", "")).strip()
                    players.append((pid, nm, p.get("positionCode"), goalie))
        roster[tm] = players
    return roster

def fetch_gamelogs(roster, seasons):
    by_season = defaultdict(list)
    for tm, players in roster.items():
        for pid, name, poscode, goalie in players:
            pos = "G" if goalie else pos_of(poscode)
            for season in seasons:
                j = api(f"{WEB}/player/{pid}/game-log/{season}/2"); time.sleep(PACE)
                if not j:
                    continue
                yr = end_year(season)
                for g in (j.get("gameLog") or []):
                    row = {"Year": yr, "Date": (g.get("gameDate") or "")[:10],
                           "MatchId": g.get("gameId"), "PlayerId": pid, "Player": name,
                           "Team": g.get("teamAbbrev"), "Opp": g.get("opponentAbbrev"),
                           "home": 1 if g.get("homeRoadFlag") == "H" else 0, "pos": pos,
                           "toiMin": toi_min(g.get("toi"))}
                    if goalie:
                        sa, ga = num(g.get("shotsAgainst")), num(g.get("goalsAgainst"))
                        row.update({"shotsAgainst": sa, "goalsAgainst": ga,
                                    "saves": (sa - ga) if (sa is not None and ga is not None) else None,
                                    "shutouts": num(g.get("shutouts"))})
                    else:
                        row.update({"goals": num(g.get("goals")), "assists": num(g.get("assists")),
                                    "points": num(g.get("points")), "shots": num(g.get("shots")),
                                    "ppPoints": num(g.get("powerPlayPoints")), "ppGoals": num(g.get("powerPlayGoals")),
                                    "pim": num(g.get("pim")), "plusMinus": num(g.get("plusMinus"))})
                    if row["Date"]:
                        by_season[yr].append(row)
    return by_season

def fetch_boxscores(game_ids):
    """Per-game boxscore -> {(gameId, playerId): {hits, blocks}}. Blocks/hits aren't in the
    player game-log endpoint, so we pull them here (current season only to bound API calls)."""
    bx = {}
    ids = sorted(set(g for g in game_ids if g is not None))
    for i, gid in enumerate(ids):
        j = api(f"{WEB}/gamecenter/{gid}/boxscore"); time.sleep(PACE)
        if not j:
            continue
        pbs = j.get("playerByGameStats") or {}
        for side in ("homeTeam", "awayTeam"):
            grp = pbs.get(side) or {}
            for cat in ("forwards", "defense", "goalies"):
                for p in (grp.get(cat) or []):
                    pid = p.get("playerId")
                    if pid is None:
                        continue
                    bx[(gid, pid)] = {"hits": num(p.get("hits")), "blocks": num(p.get("blockedShots"))}
    print(f"  boxscores: {len(ids)} games -> {len(bx)} player-game block/hit rows")
    return bx

def merge_boxscores(by_season, cur_yr, bx):
    for r in by_season.get(cur_yr, []):
        b = bx.get((r.get("MatchId"), r.get("PlayerId")))
        if b:
            r["blocks"] = b.get("blocks"); r["hits"] = b.get("hits")

def fetch_schedule(seasons):
    results, fixtures = [], []
    today = time.strftime("%Y-%m-%d", time.gmtime())
    seen = set()
    for tm in TEAMS:
        for season in seasons:
            j = api(f"{WEB}/club-schedule-season/{tm}/{season}"); time.sleep(PACE)
            if not j:
                continue
            for g in (j.get("games") or []):
                gid = g.get("id")
                if gid in seen:
                    continue
                seen.add(gid)
                home = (g.get("homeTeam") or {}).get("abbrev")
                away = (g.get("awayTeam") or {}).get("abbrev")
                for side in ("homeTeam", "awayTeam"):
                    fn = (g.get(side) or {}).get("commonName", {})
                    if isinstance(fn, dict) and fn.get("default"):
                        TEAM_FULL[(g.get(side) or {}).get("abbrev")] = fn["default"]
                date = (g.get("gameDate") or "")[:10]
                state = g.get("gameState")
                if state in ("OFF", "FINAL"):
                    results.append({"season": end_year(season), "gameId": gid, "date": date,
                                    "home": home, "away": away,
                                    "hs": num((g.get("homeTeam") or {}).get("score")),
                                    "as": num((g.get("awayTeam") or {}).get("score"))})
                elif date >= today:
                    fixtures.append({"gameId": gid, "home": home, "away": away,
                                     "utc": g.get("startTimeUTC"), "date": date,
                                     "venue": (g.get("venue") or {}).get("default")})
    fixtures.sort(key=lambda x: x["date"] or "")
    return results, fixtures

def fetch_team_summary(current):
    out = {}
    j = api(f"{STATS}/team/summary?cayenneExp=seasonId={current}"); time.sleep(PACE)
    for t in ((j or {}).get("data") or []):
        ab = t.get("teamAbbrev") or t.get("triCode")
        full = t.get("teamFullName")
        if full and ab:
            TEAM_FULL[ab] = full
        out[ab] = t
    return out

# ---------------- transform ----------------
SK_STATS = ("goals", "assists", "points", "shots", "ppPoints", "ppGoals", "pim", "blocks", "hits", "toiMin")
GO_STATS = ("saves", "shotsAgainst", "goalsAgainst", "shutouts", "toiMin")

def build(by_season, results, current):
    seasons = sorted(by_season.keys())
    gl_seasons = seasons[-3:] if len(seasons) >= 3 else seasons
    agg_seasons = seasons[-2:] if len(seasons) >= 2 else seasons

    # players aggregate
    pacc = {}
    for yr in agg_seasons:
        for r in by_season[yr]:
            k = (r["PlayerId"], r["Team"])
            d = pacc.setdefault(k, {"pid": r["PlayerId"], "name": r["Player"], "team": r["Team"],
                                    "pos": r["pos"], "g": 0, "sum": defaultdict(float)})
            d["g"] += 1
            for s in (GO_STATS if r["pos"] == "G" else SK_STATS):
                if r.get(s) is not None:
                    d["sum"][s] += r[s]
    players = []
    for d in pacc.values():
        g = max(1, d["g"]); row = {"playerId": d["pid"], "name": d["name"], "team": d["team"],
                                   "teamFull": TEAM_FULL.get(d["team"], d["team"]),
                                   "position": d["pos"], "pos5": d["pos"], "games": d["g"],
                                   "role": "goalie" if d["pos"] == "G" else "skater"}
        for s in (GO_STATS if d["pos"] == "G" else SK_STATS):
            row[s] = round(d["sum"][s] / g, 2)
        if row["games"] >= 3:
            players.append(row)

    # teams for/against (from skater gamelogs: goals/shots per team per match)
    tacc = {}
    for yr in agg_seasons:
        bm = defaultdict(lambda: defaultdict(float)); mt = defaultdict(set)
        for r in by_season[yr]:
            if r["pos"] == "G":
                continue
            key = (r["MatchId"], r["Team"]); mt[r["MatchId"]].add(r["Team"])
            for s in ("goals", "shots"):
                if r.get(s) is not None:
                    bm[key][s] += r[s]
        for (mid, tm), tot in bm.items():
            opp = [x for x in mt.get(mid, []) if x != tm]; opp = opp[0] if opp else None
            d = tacc.setdefault(tm, {"g": 0, "for": defaultdict(float), "ag": defaultdict(float)})
            d["g"] += 1
            for s, v in tot.items():
                d["for"][s] += v
            if opp:
                for s, v in bm.get((mid, opp), {}).items():
                    d["ag"][s] += v
    teams = []
    for tm, d in tacc.items():
        g = max(1, d["g"]); row = {"team": tm, "teamFull": TEAM_FULL.get(tm, tm), "games": d["g"]}
        row["goalsFor"] = round(d["for"]["goals"] / g, 2); row["goalsAgainst"] = round(d["ag"]["goals"] / g, 2)
        row["shotsFor"] = round(d["for"]["shots"] / g, 2); row["shotsAgainst"] = round(d["ag"]["shots"] / g, 2)
        teams.append(row)

    # DVP: per (defending team, position) stat allowed per game, current season
    DVP_STATS = ("goals", "assists", "points", "shots", "ppPoints")
    dvp_acc = {}; team_matches = defaultdict(set)
    for r in by_season.get(current if current in by_season else (gl_seasons[-1] if gl_seasons else None), []):
        if r["pos"] == "G":
            continue
        T, pos, mid = r["Opp"], r["pos"], r["MatchId"]
        d = dvp_acc.setdefault((T, pos), defaultdict(float))
        for s in DVP_STATS:
            if r.get(s) is not None:
                d[s] += r[s]
        team_matches[T].add(mid)
    dvp = []
    for (T, pos), d in dvp_acc.items():
        g = max(1, len(team_matches.get(T, set())))
        row = {"team": T, "pos": pos, "games": len(team_matches.get(T, set()))}
        for s in DVP_STATS:
            row[s] = round(d[s] / g, 2)
        dvp.append(row)

    return gl_seasons, players, teams, dvp

def write_all(out_dir, gl_seasons, by_season, players, teams, dvp, results, fixtures, current):
    os.makedirs(out_dir, exist_ok=True)
    def wj(name, obj):
        json.dump(obj, open(os.path.join(out_dir, name), "w"), separators=(",", ":"))
    gl_files = []
    for yr in gl_seasons:
        n = f"gamelogs_{yr}.json"; wj(n, by_season[yr]); gl_files.append(n)
    rec = set(gl_seasons)
    results = [r for r in results if str(r.get("season")) in rec]
    cur_yr = end_year(current)
    meta = {"league": "nhl", "label": "NHL", "sportKey": "nhl", "seasons": gl_seasons,
            "currentSeason": cur_yr, "gamelogFiles": gl_files,
            "summary": {"players": len(players), "teams": len(teams), "dvp": len(dvp),
                        "gamelogs": sum(len(by_season[y]) for y in gl_seasons),
                        "results": len(results), "fixtures": len(fixtures)}}
    for n, o in [("players.json", players), ("teams.json", teams), ("dvp.json", dvp),
                 ("results.json", results), ("fixture.json", fixtures), ("meta.json", meta)]:
        wj(n, o)
    print(f"NHL build: seasons {gl_seasons} | players {len(players)} | teams {len(teams)} "
          f"| dvp {len(dvp)} | results {len(results)} | fixtures {len(fixtures)}")

def selftest():
    # synthetic 2-season data -> exercise the transform with no network
    print("selftest: synthetic frames…")
    by = defaultdict(list)
    for yr in ("2024", "2025"):
        for i in range(8):
            by[yr].append({"Year": yr, "Date": f"{yr}-11-{i+1:02d}", "MatchId": 100 + i,
                           "PlayerId": 1, "Player": "Test Skater", "Team": "TOR", "Opp": "MTL",
                           "home": 1, "pos": "C", "goals": 1, "assists": 1, "points": 2,
                           "shots": 4, "ppPoints": 1, "ppGoals": 0, "pim": 0, "blocks": 1, "hits": 2, "toiMin": 20.0})
            by[yr].append({"Year": yr, "Date": f"{yr}-11-{i+1:02d}", "MatchId": 100 + i,
                           "PlayerId": 2, "Player": "Test Skater 2", "Team": "MTL", "Opp": "TOR",
                           "home": 0, "pos": "D", "goals": 0, "assists": 1, "points": 1,
                           "shots": 2, "ppPoints": 0, "ppGoals": 0, "pim": 2, "blocks": 3, "hits": 4, "toiMin": 24.0})
    gl_seasons, players, teams, dvp = build(by, [], "20252026")
    assert players and teams and dvp, "transform produced empty output"
    assert any(p["shots"] for p in players), "shots not aggregated"
    print(f"  ok: players {len(players)} teams {len(teams)} dvp {len(dvp)}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="nhl/data")
    ap.add_argument("--seasons", default="20232024,20242025,20252026")
    ap.add_argument("--current", default="20252026")
    ap.add_argument("--skip-boxscore", action="store_true", help="skip per-game block/hit fetch (faster)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest(); return
    seasons = [s.strip() for s in a.seasons.split(",") if s.strip()]
    print(f"NHL fetch: seasons={seasons} current={a.current}")
    fetch_team_summary(a.current)
    roster = fetch_rosters(seasons)
    print(f"  rosters: {sum(len(v) for v in roster.values())} players across {len(roster)} teams")
    by_season = fetch_gamelogs(roster, seasons)
    print(f"  gamelogs: {sum(len(v) for v in by_season.values())} rows")
    cur_yr = end_year(a.current)
    if not a.skip_boxscore and cur_yr in by_season:
        gids = [r.get("MatchId") for r in by_season[cur_yr]]
        bx = fetch_boxscores(gids)
        merge_boxscores(by_season, cur_yr, bx)
    results, fixtures = fetch_schedule(seasons)
    gl_seasons, players, teams, dvp = build(by_season, results, a.current)
    write_all(a.out, gl_seasons, by_season, players, teams, dvp, results, fixtures, a.current)

if __name__ == "__main__":
    main()
