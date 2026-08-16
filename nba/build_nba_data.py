#!/usr/bin/env python3
"""
build_nba_data.py - JTT NBA + WNBA split-data generator (advanced tool, AFL-parity).

One pipeline, two leagues. Mirrors nfl/build_nfl_data.py's role: writes the
split nba/data/{league}/*.json files the shell + scoring.js consume.

  meta.json        version, day, password_hash, seasons, currentSeason, summary
  players.json     current-season per-game averages + role + roster bio join
  teams.json       season per-game for + "_a" allowed columns + pace + conference
  teams_form.json  same, last FORM_N games only
  dvp.json         defense-vs-position: per-game allowed by (team, posBucket, stat)
  gamelogs.json    player logs: Year/Date/MatchId/Player/Team/Opp/home + stats
  fixture.json     upcoming games (gameId, home, away, utc, venue, seasonType)
  results.json     completed games (season, date, home, away, hs, as, gameId)
  injury.json      ESPN injury report  (Team, Player, Position, Injury, Status)
  lineups.json     projected rotation (team, player, pos, starterPct, minAvg)
  odds.json        stub written ONLY if missing (worker owns the real file)
  version.txt      unix-seconds cache-bust token

Sources (no API keys, datacenter-friendly, NEVER stats.nba.com):
  * sportsdataverse data repos via raw.githubusercontent.com (primary):
      hoopR-nba-data   /nba/{player_box,team_box,schedules,rosters}/parquet/
      wehoop-wnba-data /wnba/{...}/parquet/
  * sportsdataverse-data GitHub release assets (fallback, same files)
  * site.api.espn.com injuries endpoint (guarded; keeps old file on failure)

League differences live ONLY in LEAGUES below (AFL lesson: no hidden contracts).

Run (GitHub Actions):
    pip install -r nba/requirements.txt
    python nba/build_nba_data.py --out nba/data --league both \
        --nba-seasons 2024,2025,2026 --wnba-seasons 2025,2026 \
        --password "$NBA_WEEKLY_PASSWORD"

Self-test (no network; validates every transform on synthetic frames):
    python nba/build_nba_data.py --selftest

Local-source debugging (point at extracted data repos, skips network):
    python nba/build_nba_data.py --out /tmp/out --league wnba \
        --src-local /path/containing/{nba,wnba}/...
"""

import argparse, datetime as dt, hashlib, io, json, os, sys, time
from collections import defaultdict

import pandas as pd

# ----------------------------------------------------------------------------
# League config - the ONLY place NBA and WNBA differ.
# ----------------------------------------------------------------------------
NBA_EAST = {"BOS","BKN","NY","NYK","PHI","TOR","CHI","CLE","DET","IND","MIL",
            "ATL","CHA","MIA","ORL","WSH","WAS"}
WNBA_EAST = {"ATL","CHI","CON","IND","NY","NYL","WSH","WAS","TOR"}

LEAGUES = {
    "nba": dict(
        label="NBA",
        repo="hoopR-nba-data", prefix="nba", sched_file="nba_schedule",
        game_min=48, minutes_floor=15.0, form_n=5, lineup_window=10,
        sport_key="basketball_nba",
        espn_slug="nba",
        east=NBA_EAST,
    ),
    "wnba": dict(
        label="WNBA",
        repo="wehoop-wnba-data", prefix="wnba", sched_file="wnba_schedule",
        game_min=40, minutes_floor=12.0, form_n=5, lineup_window=10,
        sport_key="basketball_wnba",
        espn_slug="wnba",
        east=WNBA_EAST,
    ),
}

RAW_URL = ("https://raw.githubusercontent.com/sportsdataverse/{repo}/main/"
           "{prefix}/{ds}/parquet/{fname}.parquet")
REL_URL = ("https://github.com/sportsdataverse/sportsdataverse-data/releases/"
           "download/espn_{prefix}_{tag}/{fname}.parquet")
REL_TAG = {"player_box": "player_boxscores", "team_box": "team_boxscores",
           "schedules": "schedules", "rosters": "rosters"}

# ----------------------------------------------------------------------------
# Stat contract: internal camelCase key <- source box-score column.
# Add a row here to surface a new stat; nothing else needs to change.
# ----------------------------------------------------------------------------
STAT_MAP = [
    ("points",    "points"),
    ("rebounds",  "rebounds"),
    ("assists",   "assists"),
    ("threes",    "three_point_field_goals_made"),
    ("threesAtt", "three_point_field_goals_attempted"),
    ("fgm",       "field_goals_made"),
    ("fga",       "field_goals_attempted"),
    ("ftm",       "free_throws_made"),
    ("fta",       "free_throws_attempted"),
    ("oreb",      "offensive_rebounds"),
    ("dreb",      "defensive_rebounds"),
    ("steals",    "steals"),
    ("blocks",    "blocks"),
    ("turnovers", "turnovers"),
    ("fouls",     "fouls"),
    ("plusMinus", "plus_minus"),
    ("minutes",   "minutes"),
]
# Combo markets derived after mapping (order matters: inputs first).
COMBOS = [
    ("pra",    ["points", "rebounds", "assists"]),
    ("pr",     ["points", "rebounds"]),
    ("pa",     ["points", "assists"]),
    ("ra",     ["rebounds", "assists"]),
    ("stocks", ["steals", "blocks"]),
]
# Stats aggregated in dvp.json / teams "_a" columns (the bettable surface).
DVP_STATS  = ["points","rebounds","assists","threes","steals","blocks",
              "turnovers","pra","pr","pa","ra","stocks"]
# Team-level per-game keys ("for"; "_a" = allowed by the defense).
TEAM_KEYS  = ["points","rebounds","assists","threes","threesAtt","fgm","fga",
              "ftm","fta","oreb","dreb","steals","blocks","turnovers"]

POS_BUCKET = {"G":"G","PG":"G","SG":"G",
              "F":"F","SF":"F","PF":"F","GF":"F","SG/SF":"G",
              "C":"C","FC":"C","PF/C":"C"}
POSITIONS  = ["G","F","C"]

SEASON_TYPES = {2, 3, 5}          # regular, playoffs, play-in - all kept
UA = {"User-Agent": "jtt-nba-pipeline/1.0"}


# ============================================================================
# Loading
# ============================================================================
def _fetch_parquet(url):
    import requests
    r = requests.get(url, headers=UA, timeout=120)
    r.raise_for_status()
    return pd.read_parquet(io.BytesIO(r.content))


def load_frame(lg, ds, season, src_local=None):
    """Load one dataset for one season. ds in {player_box, team_box, schedules, rosters}."""
    fname = f"{LEAGUES[lg]['sched_file']}_{season}" if ds == "schedules" else f"{ds}_{season}"
    if ds == "rosters":
        fname = f"rosters_{season}"
    if src_local:
        p = os.path.join(src_local, LEAGUES[lg]["prefix"], ds, "parquet", fname + ".parquet")
        return pd.read_parquet(p)
    raw = RAW_URL.format(repo=LEAGUES[lg]["repo"], prefix=LEAGUES[lg]["prefix"], ds=ds, fname=fname)
    try:
        return _fetch_parquet(raw)
    except Exception as e:
        print(f"  [warn] raw fetch failed ({e}); trying release asset...", flush=True)
        rel = REL_URL.format(prefix=LEAGUES[lg]["prefix"], tag=REL_TAG[ds], fname=fname)
        return _fetch_parquet(rel)


# ============================================================================
# Transforms (pure - everything below is selftest-covered)
# ============================================================================
def normalize_player_box(pb):
    """Source player_box -> internal per-game log frame with the stat contract."""
    df = pd.DataFrame()
    df["Year"]     = pb["season"].astype(int).astype(str)
    df["st"]       = pb["season_type"].astype(int)
    df["Date"]     = pb["game_date"].astype(str).str[:10]
    df["MatchId"]  = pb["game_id"].astype(str)
    df["PlayerId"] = pb["athlete_id"].astype(str)
    df["Player"]   = pb["athlete_display_name"].astype(str)
    df["Team"]     = pb["team_abbreviation"].astype(str)
    df["TeamFull"] = pb["team_display_name"].astype(str)
    df["Opp"]      = pb["opponent_team_abbreviation"].astype(str)
    df["home"]     = (pb["home_away"].astype(str).str.lower() == "home").astype(int)
    df["starter"]  = pb["starter"].fillna(False).astype(bool)
    df["dnp"]      = pb["did_not_play"].fillna(False).astype(bool)
    pos = pb["athlete_position_abbreviation"].astype(str).str.upper()
    df["pos"]      = pos.map(POS_BUCKET).fillna("F")
    for key, col in STAT_MAP:
        df[key] = pd.to_numeric(pb[col], errors="coerce")
    for key, parts in COMBOS:
        df[key] = sum(df[p].fillna(0) for p in parts)
        df.loc[df[parts[0]].isna(), key] = pd.NA
    df = df[df["st"].isin(SEASON_TYPES)].copy()
    df = df[~df["dnp"]].reset_index(drop=True)      # DNP rows carry no stats
    return df


def representative(df, floor):
    """AFL TOG>=50 analogue: minutes >= league floor. Unknown minutes -> keep."""
    m = pd.to_numeric(df["minutes"], errors="coerce")
    return df[(m.isna()) | (m >= floor)]


MIN_TEAM_GAMES = 8       # per season - filters All-Star squads (STARS/STRIPES/WORLD
                         # etc. arrive mislabelled as regular season with 1-2 games)

def valid_teams_by_year(team_rows):
    """{Year: set(teams with >= MIN_TEAM_GAMES games)} from per-game team frame."""
    ok = {}
    g = team_rows.groupby(["Year", "Team"])["MatchId"].nunique().reset_index()
    for year, d in g.groupby("Year"):
        ok[year] = set(d[d["MatchId"] >= MIN_TEAM_GAMES]["Team"])
    return ok


def filter_real_teams(df, ok):
    """Drop rows where Team or Opp isn't a real franchise for that Year."""
    keep = df.apply(lambda r: (r["Team"] in ok.get(r["Year"], set()) and
                               r["Opp"]  in ok.get(r["Year"], set())), axis=1)
    return df[keep].reset_index(drop=True)


def build_players(logs, rosters, cfg, current):
    """Current-season rep-gated averages + role + roster bio join."""
    cur = logs[logs["Year"] == str(current)]
    rep = representative(cur, cfg["minutes_floor"])
    if rep.empty:
        return []
    statkeys = [k for k, _ in STAT_MAP] + [k for k, _ in COMBOS]
    g = rep.groupby(["PlayerId", "Player"], as_index=False)
    avg = g[statkeys].mean(numeric_only=True)
    meta = g.agg(games=("MatchId", "nunique"),
                 starterPct=("starter", "mean"),
                 lastDate=("Date", "max"))
    # latest team + bucket position by most recent game
    latest = (cur.sort_values("Date").groupby("PlayerId").tail(1)
                 [["PlayerId", "Team", "TeamFull", "pos"]])
    out = avg.merge(meta, on=["PlayerId", "Player"]).merge(latest, on="PlayerId", how="left")

    # roster bio join (five-position, age, height, experience, headshot)
    bio = {}
    if rosters is not None and len(rosters):
        for _, r in rosters.iterrows():
            bio[str(r.get("athlete_id"))] = dict(
                pos5=str(r.get("position_abbreviation") or "") or None,
                age=(int(r["age"]) if pd.notna(r.get("age")) else None),
                height=(str(r.get("height")) if pd.notna(r.get("height")) else None),
                exp=(int(r["experience_years"]) if pd.notna(r.get("experience_years")) else None),
                headshot=(str(r.get("headshot_href")) if pd.notna(r.get("headshot_href")) else None),
            )

    # league percentile thresholds for role classification (rate per minute)
    rates = {}
    minutes = out["minutes"].replace(0, pd.NA)
    for k in ["fga", "assists", "rebounds", "blocks", "stocks"]:
        rates[k] = (out[k] / minutes).fillna(0)
    thr = {k: rates[k].quantile(0.80) for k in rates}
    tpa_share = (out["threesAtt"] / out["fga"].replace(0, pd.NA)).fillna(0)

    players = []
    for i, r in out.iterrows():
        role = []
        if rates["fga"][i]      >= thr["fga"]:      role.append("Volume Scorer")
        if tpa_share[i] >= 0.55 and r["threesAtt"] >= 4: role.append("Sniper")
        if rates["assists"][i]  >= thr["assists"]:  role.append("Playmaker")
        if rates["rebounds"][i] >= thr["rebounds"]: role.append("Glass Cleaner")
        if rates["blocks"][i]   >= thr["blocks"]:   role.append("Rim Protector")
        if rates["stocks"][i]   >= thr["stocks"] and "Rim Protector" not in role:
            role.append("Two-Way")
        if r["starterPct"] < 0.4 and (r["minutes"] or 0) >= cfg["game_min"] * 0.4:
            role.append("Sixth Man")
        b = bio.get(str(r["PlayerId"]), {})
        row = dict(playerId=str(r["PlayerId"]), name=r["Player"],
                   team=r["Team"], teamFull=r["TeamFull"],
                   position=r["pos"], pos5=b.get("pos5"),
                   games=int(r["games"]), starterPct=round(float(r["starterPct"]), 3),
                   role=(role[0] if role else "Rotation"), roles=role,
                   age=b.get("age"), height=b.get("height"), exp=b.get("exp"),
                   headshot=b.get("headshot"))
        for k in statkeys:
            v = r[k]
            row[k] = round(float(v), 2) if pd.notna(v) else None
        players.append(row)
    players.sort(key=lambda p: -(p.get("points") or 0))
    return players


def _team_game_rows(tb):
    """Source team_box -> internal per-game team frame (+ opponent join for _a)."""
    t = pd.DataFrame()
    t["MatchId"] = tb["game_id"].astype(str)
    t["Date"]    = tb["game_date"].astype(str).str[:10]
    t["Year"]    = tb["season"].astype(int).astype(str)
    t["Team"]    = tb["team_abbreviation"].astype(str)
    t["TeamFull"]= tb["team_display_name"].astype(str)
    _lgc = "team_logo" if "team_logo" in tb.columns else ("team_logo_url" if "team_logo_url" in tb.columns else None)
    t["TeamLogo"] = tb[_lgc].astype(str) if _lgc else ""
    t["Opp"]     = tb["opponent_team_abbreviation"].astype(str)
    t["points"]  = pd.to_numeric(tb["team_score"], errors="coerce")
    src = {"rebounds":"total_rebounds","assists":"assists",
           "threes":"three_point_field_goals_made","threesAtt":"three_point_field_goals_attempted",
           "fgm":"field_goals_made","fga":"field_goals_attempted",
           "ftm":"free_throws_made","fta":"free_throws_attempted",
           "oreb":"offensive_rebounds","dreb":"defensive_rebounds",
           "steals":"steals","blocks":"blocks"}
    for k, c in src.items():
        t[k] = pd.to_numeric(tb[c], errors="coerce")
    to_col = "total_turnovers" if "total_turnovers" in tb.columns else "turnovers"
    t["turnovers"] = pd.to_numeric(tb[to_col], errors="coerce")
    # possessions estimate (per game): FGA - OREB + TO + 0.44*FTA
    t["pace"] = t["fga"] - t["oreb"] + t["turnovers"] + 0.44 * t["fta"]
    return t


def build_teams(tb_frames, cfg, current, form_only=False):
    """teams.json / teams_form.json: per-game for + _a allowed (+pace, conference)."""
    t = pd.concat(tb_frames, ignore_index=True)
    t = t[t["Year"] == str(current)]
    if t.empty:
        return []
    # opponent join by MatchId gives the allowed side
    opp = t[["MatchId", "Team"] + TEAM_KEYS + ["pace"]].copy()
    opp.columns = ["MatchId", "Opp"] + [k + "_a" for k in TEAM_KEYS] + ["pace_a"]
    g = t.merge(opp, on=["MatchId", "Opp"], how="left")
    if form_only:
        g = g.sort_values("Date").groupby("Team", group_keys=False).tail(cfg["form_n"])
    rows = []
    for team, d in g.groupby("Team"):
        row = dict(team=team, teamFull=d["TeamFull"].iloc[-1], games=len(d),
                   conference=("East" if team in cfg["east"] else "West"))
        _logo = str(d["TeamLogo"].iloc[-1]) if "TeamLogo" in d.columns else ""
        if _logo and _logo.lower() != "nan":
            row["logo"] = _logo
        for k in TEAM_KEYS + ["pace"]:
            row[k] = round(float(d[k].mean()), 2)
            row[k + "_a"] = (round(float(d[k + "_a"].mean()), 2)
                             if (k + "_a") in d else None)
        rows.append(row)
    rows.sort(key=lambda r: -r["points"])
    return rows


def build_dvp(logs, current):
    """Per (defTeam, posBucket): per-game totals allowed for each DVP stat.
    No minutes gate - everything conceded to the bucket counts."""
    cur = logs[logs["Year"] == str(current)]
    if cur.empty:
        return []
    per_game = (cur.groupby(["Opp", "pos", "MatchId"], as_index=False)[DVP_STATS]
                   .sum(min_count=1))
    agg = per_game.groupby(["Opp", "pos"], as_index=False).agg(
        **{k: (k, "mean") for k in DVP_STATS}, games=("MatchId", "nunique"))
    rows = []
    for _, r in agg.iterrows():
        row = dict(team=r["Opp"], pos=r["pos"], games=int(r["games"]))
        for k in DVP_STATS:
            row[k] = round(float(r[k]), 2) if pd.notna(r[k]) else None
        rows.append(row)
    rows.sort(key=lambda r: (r["team"], r["pos"]))
    return rows


def prune_gamelogs(logs):
    keep = ["Year","st","Date","MatchId","PlayerId","Player","Team","Opp",
            "home","starter","pos"] + [k for k,_ in STAT_MAP] + [k for k,_ in COMBOS]
    out = logs[keep].copy()
    for c in out.columns:
        if out[c].dtype == "float64":
            out[c] = out[c].round(2)
    recs = out.to_dict(orient="records")
    for r in recs:                              # NaN -> null for lean JSON
        for k, v in list(r.items()):
            if isinstance(v, float) and pd.isna(v):
                r[k] = None
            elif isinstance(v, bool):
                r[k] = int(v)
    return recs


def build_fixture_results(sched_frames):
    s = pd.concat(sched_frames, ignore_index=True)
    s = s.drop_duplicates(subset=["id"])
    completed = s["status_type_completed"].fillna(False).astype(bool)
    fixture, results = [], []
    now = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=12)
    for _, r in s.iterrows():
        rec = dict(gameId=str(r["id"]),
                   utc=str(r.get("date") or r.get("start_date")),
                   home=str(r["home_abbreviation"]), away=str(r["away_abbreviation"]),
                   homeFull=str(r["home_display_name"]), awayFull=str(r["away_display_name"]),
                   venue=(str(r["venue_full_name"]) if pd.notna(r.get("venue_full_name")) else None),
                   seasonType=int(r["type_id"]) if pd.notna(r.get("type_id")) else None)
        if completed[_]:
            results.append(dict(season=int(r["season"]) if "season" in s.columns and pd.notna(r.get("season")) else None,
                                gameId=rec["gameId"], date=rec["utc"][:10],
                                home=rec["home"], away=rec["away"],
                                hs=(int(r["home_score"]) if pd.notna(r.get("home_score")) else None),
                                as_=(int(r["away_score"]) if pd.notna(r.get("away_score")) else None)))
        else:
            try:
                gdt = dt.datetime.fromisoformat(rec["utc"].replace("Z", "+00:00"))
            except Exception:
                gdt = None
            if gdt is None or gdt >= now:
                fixture.append(rec)
    fixture.sort(key=lambda r: r["utc"])
    results.sort(key=lambda r: r["date"])
    for r in results:                            # `as` is reserved in JS-land docs; keep key 'as'
        r["as"] = r.pop("as_")
    return fixture, results


def build_lineups(logs, cfg, current):
    """Projected rotation from starter frequency + minutes over the last N games."""
    cur = logs[logs["Year"] == str(current)]
    rows = []
    for team, d in cur.groupby("Team"):
        last_ids = (d[["MatchId","Date"]].drop_duplicates()
                      .sort_values("Date").tail(cfg["lineup_window"])["MatchId"])
        w = d[d["MatchId"].isin(last_ids)]
        if w.empty:
            continue
        g = w.groupby(["PlayerId","Player","pos"], as_index=False).agg(
            starterPct=("starter","mean"), minAvg=("minutes","mean"),
            games=("MatchId","nunique"))
        g = g.sort_values(["starterPct","minAvg"], ascending=False)
        for depth, (_, r) in enumerate(g.iterrows(), 1):
            mv = r["minAvg"]
            if mv != mv or (mv or 0) < 5:         # NaN (all-null minutes) or deep bench noise
                continue
            rows.append(dict(team=team, player=r["Player"], playerId=str(r["PlayerId"]),
                             position=r["pos"], depth=depth,
                             starterPct=round(float(r["starterPct"]), 3),
                             minAvg=round(float(r["minAvg"]), 1),
                             games=int(r["games"])))
    return rows


def fetch_injuries(lg):
    """ESPN injuries endpoint - guarded; caller keeps the old file on failure."""
    import requests
    url = (f"https://site.api.espn.com/apis/site/v2/sports/basketball/"
           f"{LEAGUES[lg]['espn_slug']}/injuries")
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    data = r.json()
    rows = []
    for team in data.get("injuries", []):
        tname = team.get("displayName") or team.get("team", {}).get("displayName", "")
        for inj in team.get("injuries", []):
            ath = inj.get("athlete", {})
            rows.append(dict(Team=tname,
                             Player=ath.get("displayName", ""),
                             Position=(ath.get("position", {}) or {}).get("abbreviation", ""),
                             Injury=(inj.get("details", {}) or {}).get("type", inj.get("type", {}).get("description", "")),
                             Status=inj.get("status", "")))
    return rows


# ============================================================================
# Writing
# ============================================================================
def _clean(o):
    """NaN/Inf -> None recursively; json.dump(allow_nan=False) then guarantees validity."""
    if isinstance(o, float):
        return o if o == o and abs(o) != float("inf") else None
    if isinstance(o, dict):  return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, list):  return [_clean(v) for v in o]
    return o


def wjson(outdir, name, obj):
    p = os.path.join(outdir, name)
    with open(p, "w") as f:
        json.dump(_clean(obj), f, separators=(",", ":"), allow_nan=False)
    print(f"  wrote {name:16s} {os.path.getsize(p):>10,} bytes")


# ============================================================================
# SportsBlaze top-up (no-auth cache API) - fills recent games when the
# sportsdataverse parquet lags. https://cache.sportsblaze.com/boxscores/{lg}/{date}
# ============================================================================
SB_CACHE = "https://cache.sportsblaze.com/boxscores"
SB_STYPE = {"Regular Season": 2, "Playoffs": 3, "Preseason": 1, "In-Season Tournament": 2}


def _sb_json(url):
    import requests
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    return r.json()


def _mmss_to_min(v):
    s = str(v if v is not None else "0")
    if ":" in s:
        parts = s.split(":")
        try:
            return int(parts[0]) + int(parts[1]) / 60.0
        except Exception:
            return 0.0
    try:
        return float(s)
    except Exception:
        return 0.0


def sportsblaze_player_box(lg, start_date, end_date):
    """Return recent player-box rows in the wehoop source schema, from the free cache API."""
    import datetime as _dt
    rows, d = [], start_date
    while d <= end_date:
        ds = d.isoformat()
        try:
            js = _sb_json(f"{SB_CACHE}/{lg}/{ds}")
        except Exception as e:
            print(f"    (sportsblaze {lg} {ds} failed: {e})")
            d += _dt.timedelta(days=1)
            continue
        for ev in (js.get("events") or []):
            if ev.get("status") != "Final":
                continue
            seas = ev.get("season", {}) or {}
            yr, st = seas.get("year"), SB_STYPE.get(seas.get("type"), 2)
            gid = ev.get("id")
            gdate = (ev.get("date") or ds)[:10]
            teams = ev.get("teams", {}) or {}
            players = ev.get("players", {}) or {}
            for side in ("away", "home"):
                tm = teams.get(side, {}) or {}
                opp = teams.get("home" if side == "away" else "away", {}) or {}
                for p in (players.get(side) or []):
                    stt = ((p.get("statistics") or {}).get("total") or {})
                    rows.append({
                        "season": yr, "season_type": st, "game_date": gdate, "game_id": gid,
                        "athlete_id": p.get("id"), "athlete_display_name": p.get("name"),
                        "team_abbreviation": tm.get("abbreviation"), "team_display_name": tm.get("name"),
                        "opponent_team_abbreviation": opp.get("abbreviation"),
                        "home_away": side, "starter": bool(p.get("starter")),
                        "did_not_play": not bool(p.get("played")),
                        "athlete_position_abbreviation": p.get("position"),
                        "points": stt.get("points"), "rebounds": stt.get("rebounds"),
                        "assists": stt.get("assists"),
                        "three_point_field_goals_made": stt.get("three_points_made"),
                        "three_point_field_goals_attempted": stt.get("three_points_attempted"),
                        "field_goals_made": stt.get("field_goals_made"),
                        "field_goals_attempted": stt.get("field_goals_attempted"),
                        "free_throws_made": stt.get("free_throws_made"),
                        "free_throws_attempted": stt.get("free_throws_attempted"),
                        "offensive_rebounds": stt.get("rebounds_offensive"),
                        "defensive_rebounds": stt.get("rebounds_defensive"),
                        "steals": stt.get("steals"), "blocks": stt.get("blocks"),
                        "turnovers": stt.get("turnovers"), "fouls": stt.get("fouls"),
                        "plus_minus": stt.get("plus_minus"),
                        "minutes": _mmss_to_min(stt.get("minutes")),
                    })
        d += _dt.timedelta(days=1)
    return pd.DataFrame(rows)


def augment_from_sportsblaze(lg, raw_pb):
    """Top up the current-season parquet with SportsBlaze rows for any days it's missing."""
    import datetime as _dt
    try:
        latest = pd.to_datetime(raw_pb["game_date"]).max().date()
    except Exception:
        return raw_pb
    today = _dt.date.today()
    if latest >= today - _dt.timedelta(days=1):
        return raw_pb
    extra = sportsblaze_player_box(lg, latest + _dt.timedelta(days=1), today)
    if len(extra):
        print(f"  [sportsblaze] {lg}: +{len(extra)} player-rows since {latest} (parquet lagged)")
        return pd.concat([raw_pb, extra], ignore_index=True)
    return raw_pb


def run_league(lg, seasons, current, outroot, password, src_local):
    cfg = LEAGUES[lg]
    outdir = os.path.join(outroot, lg)
    os.makedirs(outdir, exist_ok=True)
    print(f"[{cfg['label']}] seasons={seasons} current={current}")

    pb_frames, tb_frames, sc_frames = [], [], []
    rosters = None
    probe = seasons + [max(seasons) + 1]     # next season flows in automatically
    for s in probe:
        optional = s not in seasons
        if optional:
            print(f"  probing {s} (next season)...", flush=True)
            try:
                sc_frames.append(load_frame(lg, "schedules", s, src_local))
                pb_frames.append(normalize_player_box(load_frame(lg, "player_box", s, src_local)))
                tb_frames.append(_team_game_rows(load_frame(lg, "team_box", s, src_local)))
            except Exception:
                print(f"  [info] {s} not published yet - skipped")
            continue
        print(f"  loading {s}...", flush=True)
        _rawpb = load_frame(lg, "player_box", s, src_local)
        if s == current and not src_local:
            _rawpb = augment_from_sportsblaze(lg, _rawpb)
        pb_frames.append(normalize_player_box(_rawpb))
        tb_frames.append(_team_game_rows(load_frame(lg, "team_box", s, src_local)))
        sc_frames.append(load_frame(lg, "schedules", s, src_local))
        if s == current:
            try:
                rosters = load_frame(lg, "rosters", s, src_local)
            except Exception as e:
                print(f"  [warn] rosters unavailable: {e}")
    logs = pd.concat(pb_frames, ignore_index=True)
    tall = pd.concat(tb_frames, ignore_index=True)
    ok = valid_teams_by_year(tall)
    logs = filter_real_teams(logs, ok)
    tb_frames = [filter_real_teams(t, ok) for t in tb_frames]

    players = build_players(logs, rosters, cfg, current)
    teams   = build_teams(tb_frames, cfg, current, form_only=False)
    teamsF  = build_teams(tb_frames, cfg, current, form_only=True)
    dvp     = build_dvp(logs, current)
    glog_files = {}
    for yr, d in logs.groupby("Year"):
        glog_files[yr] = prune_gamelogs(d)
    fixture, results = build_fixture_results(sc_frames)
    lineups = build_lineups(logs, cfg, current)

    wjson(outdir, "players.json", players)
    wjson(outdir, "teams.json", teams)
    wjson(outdir, "teams_form.json", teamsF)
    wjson(outdir, "dvp.json", dvp)
    total_logs = 0
    for yr, recs in sorted(glog_files.items()):
        wjson(outdir, f"gamelogs_{yr}.json", recs)
        total_logs += len(recs)
    wjson(outdir, "fixture.json", fixture)
    wjson(outdir, "results.json", results)
    wjson(outdir, "lineups.json", lineups)

    try:
        inj = fetch_injuries(lg)
        wjson(outdir, "injury.json", inj)
    except Exception as e:
        print(f"  [warn] injuries fetch failed ({e}); keeping existing file")
        if not os.path.exists(os.path.join(outdir, "injury.json")):
            wjson(outdir, "injury.json", [])

    op = os.path.join(outdir, "odds.json")
    if not os.path.exists(op):
        wjson(outdir, "odds.json", {"note": "stub - worker owns this file", "events": []})

    version = str(int(time.time()))
    nextDay = fixture[0]["utc"][:10] if fixture else None
    meta = dict(version=version,
                created=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                league=lg, label=cfg["label"], day=nextDay,
                gameMin=cfg["game_min"], sportKey=cfg["sport_key"],
                seasons=[str(s) for s in seasons], currentSeason=str(current),
                gamelogFiles=[f"gamelogs_{yr}.json" for yr in sorted(glog_files)],
                summary=dict(players=len(players), teams=len(teams),
                             dvp=len(dvp), gamelogs=total_logs,
                             fixtures=len(fixture), results=len(results),
                             lineups=len(lineups)),
                derivedNote=("players/teams/dvp derived from sportsdataverse ESPN box scores; "
                             "bio joined from rosters; rep-gate minutes>=%s" % cfg["minutes_floor"]))
    if password:
        meta["password_hash"] = hashlib.sha256(password.encode()).hexdigest()
    else:                                        # preserve existing hash if present
        try:
            old = json.load(open(os.path.join(outdir, "meta.json")))
            if old.get("password_hash"):
                meta["password_hash"] = old["password_hash"]
        except Exception:
            pass
    wjson(outdir, "meta.json", meta)
    with open(os.path.join(outdir, "version.txt"), "w") as f:
        f.write(version)
    print(f"  [{cfg['label']}] done: {meta['summary']}")


# ============================================================================
# Self-test - synthetic frames through every transform, no network.
# ============================================================================
def selftest():
    print("selftest: building synthetic frames...")
    def _pbrow(gid, date, aid, name, team, opp, ha, pos, st, mins, pts, reb, ast,
               tpm=1, dnp=False, starter=True):
        return dict(season=2026, season_type=2, game_id=gid, game_date=date,
                    game_date_time=date, athlete_id=aid, athlete_display_name=name,
                    team_abbreviation=team, team_display_name=team + " Full",
                    opponent_team_abbreviation=opp, home_away=ha,
                    starter=starter, did_not_play=dnp, ejected=False, reason=None, active=True,
                    athlete_position_abbreviation=pos,
                    minutes=mins, points=pts, rebounds=reb, assists=ast,
                    three_point_field_goals_made=tpm, three_point_field_goals_attempted=tpm + 3,
                    field_goals_made=pts // 2, field_goals_attempted=pts // 2 + 6,
                    free_throws_made=2, free_throws_attempted=3,
                    offensive_rebounds=1, defensive_rebounds=max(reb - 1, 0),
                    steals=1, blocks=1, turnovers=2, fouls=2, plus_minus=3,
                    season2=None)
    rows = [
        _pbrow("g1","2026-01-01","1","Alpha One","AAA","BBB","home","G",2,34,30,5,8),
        _pbrow("g1","2026-01-01","2","Beta Two","AAA","BBB","home","C",2,30,18,12,2),
        _pbrow("g1","2026-01-01","3","Gam Three","BBB","AAA","away","G",2,36,25,4,9),
        _pbrow("g1","2026-01-01","4","Del Four","BBB","AAA","away","F",2,8,4,2,0),   # < floor
        _pbrow("g2","2026-01-03","1","Alpha One","AAA","BBB","away","G",2,36,40,6,10),
        _pbrow("g2","2026-01-03","2","Beta Two","AAA","BBB","away","C",2,0,0,0,0,dnp=True),
        _pbrow("g2","2026-01-03","3","Gam Three","BBB","AAA","home","G",2,38,20,5,11),
    ]
    pb = pd.DataFrame(rows)
    logs = normalize_player_box(pb)
    assert len(logs) == 6, "dnp row must be dropped"
    assert logs.iloc[0]["pra"] == 30 + 5 + 8, "combo math"
    cfg = LEAGUES["nba"]
    rep = representative(logs, cfg["minutes_floor"])
    assert len(rep) == 5, "8-minute game must be rep-gated out"
    players = build_players(logs, None, cfg, 2026)
    a1 = next(p for p in players if p["name"] == "Alpha One")
    assert a1["points"] == 35.0 and a1["games"] == 2, "average over rep games"
    assert a1["position"] == "G"
    dvp = build_dvp(logs, 2026)
    bbbG = next(r for r in dvp if r["team"] == "BBB" and r["pos"] == "G")
    # BBB allowed to G: g1 Alpha 30 (Beta is C), g2 Alpha 40 -> 35.0
    assert bbbG["points"] == 35.0, f"dvp allowed math ({bbbG['points']})"
    aaaG = next(r for r in dvp if r["team"] == "AAA" and r["pos"] == "G")
    assert aaaG["points"] == 22.5, "dvp both games (25, 20)"
    # team box synth
    def _tbrow(gid, date, team, opp, score, fga, oreb, to, fta):
        return dict(game_id=gid, season=2026, season_type=2, game_date=date,
                    team_abbreviation=team, team_display_name=team + " Full",
                    opponent_team_abbreviation=opp, team_score=score,
                    total_rebounds=40, assists=25, three_point_field_goals_made=12,
                    three_point_field_goals_attempted=30, field_goals_made=40,
                    field_goals_attempted=fga, free_throws_made=15, free_throws_attempted=fta,
                    offensive_rebounds=oreb, defensive_rebounds=30, steals=7, blocks=5,
                    total_turnovers=to, turnovers=to)
    tb = pd.DataFrame([
        _tbrow("g1","2026-01-01","AAA","BBB",110,88,10,14,20),
        _tbrow("g1","2026-01-01","BBB","AAA",104,90,12,12,18),
        _tbrow("g2","2026-01-03","AAA","BBB",120,92,11,10,22),
        _tbrow("g2","2026-01-03","BBB","AAA",99,85,9,15,16),
    ])
    t = _team_game_rows(tb)
    teams = build_teams([t], cfg, 2026)
    aaa = next(r for r in teams if r["team"] == "AAA")
    assert aaa["points"] == 115.0 and aaa["points_a"] == 101.5, "for/allowed join"
    exp_pace_g1 = 88 - 10 + 14 + 0.44 * 20
    assert abs(t[t.MatchId.eq('g1') & t.Team.eq('AAA')]['pace'].iloc[0] - exp_pace_g1) < 1e-9, "pace formula"
    # schedule synth
    sc = pd.DataFrame([
        dict(id="g1", date="2026-01-01T00:00Z", season=2026, type_id=2,
             status_type_completed=True, home_abbreviation="AAA", away_abbreviation="BBB",
             home_display_name="AAA Full", away_display_name="BBB Full",
             venue_full_name="Synth Arena", home_score=110, away_score=104),
        dict(id="g9", date="2099-01-01T00:00Z", season=2026, type_id=2,
             status_type_completed=False, home_abbreviation="BBB", away_abbreviation="AAA",
             home_display_name="BBB Full", away_display_name="AAA Full",
             venue_full_name="Synth Arena", home_score=None, away_score=None),
    ])
    fixture, results = build_fixture_results([sc])
    assert len(fixture) == 1 and len(results) == 1
    assert results[0]["hs"] == 110 and results[0]["as"] == 104
    lineups = build_lineups(logs, cfg, 2026)
    assert any(l["player"] == "Alpha One" and l["starterPct"] == 1.0 for l in lineups)
    glogs = prune_gamelogs(logs)
    assert glogs[0]["home"] in (0, 1) and "pra" in glogs[0]
    for name, obj in [("players", players), ("dvp", dvp), ("teams", teams),
                      ("fixture", fixture), ("results", results),
                      ("lineups", lineups), ("gamelogs", glogs)]:
        json.loads(json.dumps(_clean(obj), allow_nan=False))   # raises on NaN leak
    print("selftest: ALL PASS ok")


# ============================================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="nba/data")
    ap.add_argument("--league", default="both", choices=["nba", "wnba", "both"])
    ap.add_argument("--nba-seasons", default="2024,2025,2026")
    ap.add_argument("--nba-current", default="2026")
    ap.add_argument("--wnba-seasons", default="2025,2026")
    ap.add_argument("--wnba-current", default="2026")
    ap.add_argument("--password", default="")
    ap.add_argument("--src-local", default=None,
                    help="dir containing {nba,wnba}/{ds}/parquet - skips network")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        selftest()
        return

    leagues = ["nba", "wnba"] if args.league == "both" else [args.league]
    for lg in leagues:
        seasons = [int(s) for s in getattr(args, f"{lg}_seasons").split(",")]
        current = int(getattr(args, f"{lg}_current"))
        run_league(lg, seasons, current, args.out, args.password, args.src_local)


if __name__ == "__main__":
    main()
