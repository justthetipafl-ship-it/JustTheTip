#!/usr/bin/env python3
"""
build_ncaaf_data.py - JTT NCAAF split-data generator (advanced tool, NFL-port).

Mirrors nfl/build_nfl_data.py's role: one script writes the split
ncaaf/data/*.json files the shell + scoring.js consume. College has no bulk
player-box release, so this pipeline BUILDS AND OWNS its own box store:

  SOURCES (validated 2026-07):
    schedules   cfbfastR-data parquet (raw.githubusercontent.com, no key)
    rosters     cfbfastR-data parquet - position, class year, headshot
    player box  ESPN summary API, one call per COMPLETED game, cached in
                ncaaf/data/raw/player_box_{yyyy}.json (committed = the store).
                PRIMARY source for all box lines incl. TDs - the free
                player_stats parquet undercounts TD flags (validated).
    enrichment  cfbfastR-data player_stats parquet (play-level, ESPN athlete
                ids, 795/798 2024 FBS games present): red-zone/goal-line
                usage, longest + explosive plays. YARDAGE-based fields only -
                TD flags in this file are NOT trusted.
    first TD    ESPN summary scoringPlays (ordered) - not the parquet.
    closing ln  ESPN summary pickcenter (per-game, stored in the box cache)
    calibration betting/cfb_line_odds.parquet (2006-2019 only!) joined to
                schedule margins -> blowout_calib.json for Garbage Time.
    fixtures+   ESPN scoreboard (odds, AP ranks, venue) for the upcoming week
    injuries    ESPN injuries endpoint, guarded - old file kept on failure

  OUTPUT FILES (ncaaf/data/):
    meta.json             version, week, gamelogFiles, password_hash, summary
    players.json          per-game averages + rz/gl usage + class year
    teams.json            season offense /game + "_a" allowed + conference
    teams_form.json       same, last FORM_N (=4) games
    dvp.json              defense-vs-position, FBS-vs-FBS games only, + conf
    gamelogs_{yyyy}.json  weekly logs, split per season (25 MiB Pages cap)
    fixture.json          next unplayed week + spread/total/ranks/flags
    results.json          completed games
    injury.json           latest ESPN report (guarded)
    weather.json          Open-Meteo for outdoor fixtures (guarded)
    firsttd.json          first-TD log per team per game (+ gameFirst)
    blowout_calib.json    |spread| buckets -> blowout probabilities
    odds.json             stub ONLY if missing (worker owns the real file)
    raw/player_box_{yyyy}.json   committed ESPN box cache (the store)
    raw/teams_espn.json          FBS team identity cache (id/abbr/name/logo)
    raw/venue_coords.json        geocode cache for weather

  CANONICAL TEAM KEY: ESPN numeric team id AS A STRING, everywhere
  (schedules home_id/away_id == ESPN ids == logo CDN key). The shell renders
  abbreviations via teams.json. School-name strings never join anything.

  CANONICAL WEEK: regular week as-is (source folds Week 0 into 1, runs to
  16 = Army-Navy); postseason week n -> 16 + n (all FBS bowls/CFP = 17).

  SANDBOX / FIRST-RUN NOTE: ESPN endpoints are unreachable from the dev
  sandbox - the parser is built against the documented summary schema with
  STRICT, LOUD assertions (SchemaError lists exactly what was missing) and a
  --selftest that runs synthetic summaries through the full pipeline. First
  live ESPN validation happens on the first Actions run; failures are
  designed to be readable straight from the run log.

Run (GitHub Actions):
    pip install -r ncaaf/requirements.txt
    python ncaaf/build_ncaaf_data.py --out ncaaf/data \
        --seasons 2024,2025,2026 --current 2026 --password "$PW"

Backfill (chunkable/resumable - fetches at most --max-fetch new games, the
committed raw cache is the resume point):
    python ncaaf/build_ncaaf_data.py --out ncaaf/data --backfill 2024 \
        --weeks 1-8 --max-fetch 120

Self-test (no network; every transform on synthetic frames + summaries):
    python ncaaf/build_ncaaf_data.py --selftest
"""

import argparse, datetime as dt, hashlib, io, json, math, os, re, sys, time
from collections import defaultdict

# -- constants ----------------------------------------------------------------
POSITIONS = ["QB", "RB", "WR", "TE"]
POS_MAP = {"QB": "QB", "RB": "RB", "FB": "RB", "HB": "RB", "TB": "RB",
           "WR": "WR", "SE": "WR", "FL": "WR", "TE": "TE", "H": "TE"}
CLASS_MAP = {1: "FR", 2: "SO", 3: "JR", 4: "SR"}
FORM_N = 4                       # 12-game seasons; 5 was too much of one
P4_CONFS = {"SEC", "Big Ten", "Big 12", "ACC"}
P4_INDEPENDENTS = {"87"}         # Notre Dame's ESPN team id

STAT_KEYS = ["passYds","passAtt","passComp","passTds","passInt","sacks",
             "rushYds","rushAtt","rushTds","receptions","recYds","recTds",
             "rushRecYds","totalTds","fanPts"]
ENRICH_KEYS = ["longRec","longRush","longComp","expRec","expRush"]
TEAM_KEYS = ["points","plays","passYds","passAtt","passComp","passTds",
             "passInt","sacks","rushYds","rushAtt","rushTds","receptions"]

CFBDATA = "https://raw.githubusercontent.com/sportsdataverse/cfbfastR-data/main"
ESPN_SITE = "https://site.api.espn.com/apis/site/v2/sports/football/college-football"
ESPN_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.espn.com", "Referer": "https://www.espn.com/",
}
FETCH_SLEEP = 0.7                # politeness between ESPN summary calls

# Known indoor/retractable venues that host FBS games (incl. bowls/neutrals).
DOME_VENUES = {"jma wireless dome","carrier dome","alamodome","caesars superdome",
    "mercedes-benz superdome","mercedes-benz stadium","lucas oil stadium",
    "ford field","at&t stadium","allegiant stadium","nrg stadium",
    "state farm stadium","u.s. bank stadium","tropicana field","unlv thomas & mack"}

class SchemaError(RuntimeError):
    """ESPN payload didn't look like the schema this parser was built against.
    The message says exactly what was expected vs found - fix from the run log."""

# -- generic helpers ----------------------------------------------------------
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
        return d if f != f else f          # NaN guard
    except (ValueError, TypeError):
        return v

def r1(x): return round(float(x), 1)
def r3(x): return round(float(x), 3)
def _norm(s): return str(s or "").strip().lower()

def _int0(v):
    try:
        f = float(v)
        return 0 if f != f else int(f)
    except (TypeError, ValueError):
        return 0

def _f(v, d=0.0):
    try:
        x = float(v)
        return d if x != x else x
    except (TypeError, ValueError):
        return d

def _clean(o):
    """NaN/Inf -> None recursively; json.dump(allow_nan=False) then guarantees validity."""
    if isinstance(o, dict):  return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, list):  return [_clean(v) for v in o]
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)): return None
    return o

def wjson(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(_clean(obj), f, separators=(",", ":"), allow_nan=False)
    print(f"  wrote {path} ({os.path.getsize(path)/1024:.0f} KB)")

def rjson(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default

def fan_pts(r):
    """PPR fantasy points (books/DFS convention): pass .04/yd +4TD -2INT;
    rush/rec .1/yd +6TD; +1/reception."""
    return r1(r["passYds"]*0.04 + r["passTds"]*4 - r["passInt"]*2
              + (r["rushYds"]+r["recYds"])*0.1 + (r["rushTds"]+r["recTds"])*6
              + r["receptions"])

# -- network (parquet via raw.githubusercontent, ESPN via site API) -----------
def fetch_bytes(url, tries=3, timeout=90, headers=None):
    import urllib.request
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            last = e; time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"fetch failed after {tries} tries: {url} ({last})")

def fetch_json(url, **kw):
    return json.loads(fetch_bytes(url, headers=ESPN_HEADERS, **kw).decode("utf-8"))

def load_parquet(url):
    import pyarrow.parquet as pq
    return pq.read_table(io.BytesIO(fetch_bytes(url))).to_pandas()

def load_schedules(seasons):
    out = {}
    for s in seasons:
        try:
            out[s] = load_parquet(f"{CFBDATA}/schedules/parquet/cfb_schedules_{s}.parquet")
            print(f"  schedules {s}: {len(out[s])} rows")
        except Exception as e:
            print(f"  (schedules {s} unavailable: {e})")
    if not out:
        raise RuntimeError("no schedules loaded - cannot build anything")
    return out

def schedules_from_espn(season, identity, conf_hint):
    """Fallback when a season's parquet hasn't published yet (e.g. next season
    pre-kickoff): synthesize the same frame from ESPN scoreboards, one call per
    regular week + one postseason sweep. Division from the FBS identity cache;
    conference inherited from the newest parquet season (overridden once the
    real parquet lands)."""
    import pandas as pd
    rows = []
    fbs_ids = set(identity.keys())
    def _ev_rows(j, stype_label):
        for ev in j.get("events") or []:
            comp = (ev.get("competitions") or [{}])[0]
            wk = _int0(((ev.get("week") or {}).get("number")) or 0) or 1
            home = away = None
            for c in comp.get("competitors", []):
                if c.get("homeAway") == "home": home = c
                else: away = c
            if not home or not away: continue
            ht, at = home.get("team") or {}, away.get("team") or {}
            done = bool(((ev.get("status") or {}).get("type") or {}).get("completed"))
            hid, aid = str(ht.get("id", "")), str(at.get("id", ""))
            rows.append(dict(
                game_id=str(ev.get("id", "")), week=wk, season_type=stype_label,
                home_id=_int0(hid), away_id=_int0(aid),
                home_team=ht.get("location") or ht.get("displayName") or "",
                away_team=at.get("location") or at.get("displayName") or "",
                home_division="fbs" if hid in fbs_ids else "fcs",
                away_division="fbs" if aid in fbs_ids else "fcs",
                home_conference=conf_hint.get(hid, ""), away_conference=conf_hint.get(aid, ""),
                home_points=_int0(home.get("score")) if done else None,
                away_points=_int0(away.get("score")) if done else None,
                completed=done, start_date=ev.get("date") or "",
                neutral_site=bool(comp.get("neutralSite")),
                conference_game=bool(comp.get("conferenceCompetition")),
                venue=((comp.get("venue") or {}).get("fullName") or ""),
                home_pregame_elo=None, away_pregame_elo=None))
    for wk in range(1, 17):
        try:
            _ev_rows(fetch_json(f"{ESPN_SITE}/scoreboard?groups=80&dates={season}"
                                f"&seasontype=2&week={wk}&limit=400", timeout=45), "regular")
        except Exception as e:
            print(f"    (scoreboard {season} wk{wk} failed: {e})")
        time.sleep(0.3)
    try:
        _ev_rows(fetch_json(f"{ESPN_SITE}/scoreboard?groups=80&dates={season}"
                            f"&seasontype=3&limit=400", timeout=45), "postseason")
    except Exception:
        pass
    df = pd.DataFrame(rows).drop_duplicates(subset=["game_id"])
    print(f"  schedules {season}: {len(df)} rows (ESPN scoreboard fallback)")
    return df

def load_rosters(season):
    for s in (season, season - 1):           # next season's file lags in Aug
        try:
            df = load_parquet(f"{CFBDATA}/rosters/parquet/cfb_rosters_{s}.parquet")
            print(f"  rosters {s}: {len(df)} rows")
            return df, s
        except Exception as e:
            print(f"  (rosters {s} unavailable: {e})")
    return None, None

def load_player_stats(seasons):
    out = {}
    for s in seasons:
        try:
            out[s] = load_parquet(f"{CFBDATA}/player_stats/parquet/player_stats_{s}.parquet")
            print(f"  player_stats {s}: {len(out[s])} rows")
        except Exception as e:
            print(f"  (player_stats {s} unavailable - enrichment skipped: {e})")
    return out

# -- schedules -> canonical game index / results / fixture --------------------
def canon_week(week, season_type):
    """Regular week as-is (source: 1-16, wk16 = Army-Navy); postseason week
    n -> 16+n. FBS bowls/CFP are all postseason week 1 -> canonical 17."""
    w = _int0(week)
    return 16 + w if str(season_type or "").lower().startswith("post") else w

def sched_rows(sch_frames):
    """Yield one normalised dict per schedule row across seasons."""
    for season, df in sorted(sch_frames.items()):
        gid = col(df, "game_id"); wk = col(df, "week"); st = col(df, "season_type")
        hi = col(df, "home_id"); ai = col(df, "away_id")
        ht = col(df, "home_team"); at = col(df, "away_team")
        hd = col(df, "home_division"); ad = col(df, "away_division")
        hc = col(df, "home_conference"); ac = col(df, "away_conference")
        hp = col(df, "home_points"); ap = col(df, "away_points")
        sd = col(df, "start_date"); cm = col(df, "completed")
        ns = col(df, "neutral_site"); cg = col(df, "conference_game")
        vn = col(df, "venue")
        he = col(df, "home_pregame_elo"); ae = col(df, "away_pregame_elo")
        for _, r in df.iterrows():
            hpts, apts = r.get(hp), r.get(ap)
            done = bool(r.get(cm)) if cm else (hpts == hpts and hpts is not None)
            yield {
                "season": season, "gameId": str(_int0(g(r, gid))),
                "week": canon_week(g(r, wk), g(r, st, "")),
                "seasonType": str(g(r, st, "") or "regular"),
                "homeId": str(_int0(g(r, hi))), "awayId": str(_int0(g(r, ai))),
                "homeName": str(g(r, ht, "") or ""), "awayName": str(g(r, at, "") or ""),
                "homeDiv": _norm(g(r, hd, "")), "awayDiv": _norm(g(r, ad, "")),
                "homeConf": str(g(r, hc, "") or ""), "awayConf": str(g(r, ac, "") or ""),
                "hs": _int0(hpts) if done else None, "as": _int0(apts) if done else None,
                "date": str(g(r, sd, "") or ""), "completed": done,
                "neutral": bool(r.get(ns)) if ns else False,
                "confGame": bool(r.get(cg)) if cg else False,
                "venue": str(g(r, vn, "") or ""),
                "homeElo": _f(g(r, he, None), None) if he else None,
                "awayElo": _f(g(r, ae, None), None) if ae else None,
            }

def build_game_index(sch_frames):
    """(season, gameId) -> game dict; plus (season, week, teamId) -> side info."""
    games, by_team = {}, {}
    for gm in sched_rows(sch_frames):
        games[(gm["season"], gm["gameId"])] = gm
        fbs_v_fbs = gm["homeDiv"] == "fbs" and gm["awayDiv"] == "fbs"
        for tid, opp, home in ((gm["homeId"], gm["awayId"], 1),
                               (gm["awayId"], gm["homeId"], 0)):
            by_team[(gm["season"], gm["week"], tid)] = {
                "matchId": gm["gameId"], "opp": opp, "home": home,
                "date": gm["date"], "fbsVfbs": fbs_v_fbs}
    return games, by_team

def build_results(games):
    out = []
    for (_, _), gm in sorted(games.items()):
        if not gm["completed"] or gm["hs"] is None:
            continue
        if gm["homeDiv"] != "fbs" and gm["awayDiv"] != "fbs":
            continue                      # drop DII/DIII noise
        out.append({"season": gm["season"], "week": gm["week"],
                    "home": gm["homeId"], "away": gm["awayId"],
                    "homeName": gm["homeName"], "awayName": gm["awayName"],
                    "hs": gm["hs"], "as": gm["as"], "date": gm["date"],
                    "neutral": 1 if gm["neutral"] else 0,
                    "fbsVfbs": 1 if (gm["homeDiv"] == "fbs" and gm["awayDiv"] == "fbs") else 0})
    out.sort(key=lambda x: (x["season"], x["week"], x["date"]))
    return out

def team_is_p4(team_id, conf):
    return conf in P4_CONFS or str(team_id) in P4_INDEPENDENTS

def build_fixture(games, scoreboard_odds=None):
    """Next week with unplayed FBS-involved games, from the newest season that
    has any. scoreboard_odds: gameId -> {spread, total, homeRank, awayRank}."""
    horizon = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)).strftime("%Y-%m-%d")
    pend = [gm for gm in games.values()
            if not gm["completed"] and (gm["homeDiv"] == "fbs" or gm["awayDiv"] == "fbs")
            and (not gm["date"] or gm["date"][:10] >= horizon)]   # cancelled games linger forever
    if not pend:
        return [], None, None
    season = max(gm["season"] for gm in pend)
    pend = [gm for gm in pend if gm["season"] == season]
    week = min(gm["week"] for gm in pend)
    fx = []
    sbo = scoreboard_odds or {}
    for gm in sorted((x for x in pend if x["week"] == week), key=lambda x: x["date"]):
        o = sbo.get(gm["gameId"], {})
        spread = o.get("spread")                       # home-negative = home favored
        if spread is None and gm["homeElo"] and gm["awayElo"]:
            spread = r1(-(gm["homeElo"] - gm["awayElo"]) / 25.0)   # Elo fallback
        row = {"home": gm["homeId"], "away": gm["awayId"],
               "homeName": gm["homeName"], "awayName": gm["awayName"],
               "week": week, "season": season, "utc": gm["date"], "date": gm["date"][:10],
               "venue": gm["venue"], "neutral": 1 if gm["neutral"] else 0,
               "confGame": 1 if gm["confGame"] else 0,
               "spread": spread, "total": o.get("total"),
               "hasLine": 1 if o.get("spread") is not None else 0,
               "homeRank": o.get("homeRank"), "awayRank": o.get("awayRank"),
               "p4": 1 if (team_is_p4(gm["homeId"], gm["homeConf"])
                           or team_is_p4(gm["awayId"], gm["awayConf"])) else 0,
               "fbsVfbs": 1 if (gm["homeDiv"] == "fbs" and gm["awayDiv"] == "fbs") else 0}
        row["realistic"] = 1 if (row["hasLine"] or row["homeRank"] or row["awayRank"]
                                 or row["p4"]) else 0
        fx.append(row)
    return fx, week, season

# -- ESPN team identity (id -> abbr / displayName / logo), cached -------------
def _synth_abbr(name):
    """Deterministic placeholder abbr until the ESPN teams cache exists."""
    words = re.sub(r"[^A-Za-z ]", "", str(name or "")).split()
    if not words: return "UNK"
    if len(words) == 1: return words[0][:4].upper()
    return "".join(w[0] for w in words).upper()[:5]

def load_team_identity(raw_dir, skip_espn):
    """raw/teams_espn.json: {id: {abbr, school, displayName, logo}}. Refresh
    from ESPN when reachable; keep the cache on any failure."""
    path = os.path.join(raw_dir, "teams_espn.json")
    cache = rjson(path, {}) or {}
    if skip_espn:
        return cache, False
    try:
        j = fetch_json(f"{ESPN_SITE}/teams?limit=500&groups=80")
        leagues = j.get("sports", [{}])[0].get("leagues", [{}])
        items = leagues[0].get("teams", []) if leagues else []
        if not items:
            raise SchemaError("teams endpoint: sports[0].leagues[0].teams empty/missing "
                              f"(top-level keys: {sorted(j.keys())})")
        fresh = {}
        for it in items:
            t = it.get("team", {})
            tid = str(t.get("id", ""))
            if not tid: continue
            fresh[tid] = {"abbr": t.get("abbreviation") or _synth_abbr(t.get("location")),
                          "school": t.get("location") or t.get("name") or "",
                          "displayName": t.get("displayName") or "",
                          "logo": (t.get("logos") or [{}])[0].get("href", "")}
        if len(fresh) < 100:
            raise SchemaError(f"teams endpoint returned only {len(fresh)} FBS teams - "
                              "expected ~134; refusing to overwrite the cache")
        cache = fresh
        wjson(path, cache)
        return cache, True
    except Exception as e:
        print(f"  (ESPN teams identity refresh failed - keeping cache of {len(cache)}: {e})")
        return cache, False

def team_meta(identity, tid, school):
    m = identity.get(str(tid), {})
    return {"abbr": m.get("abbr") or _synth_abbr(school),
            "school": m.get("school") or school,
            "displayName": m.get("displayName") or school,
            "logo": m.get("logo") or ""}

# -- ESPN scoreboard -> upcoming odds / ranks ---------------------------------
def fetch_scoreboard_odds(season, week):
    """gameId -> {spread(home-neg), total, homeRank, awayRank}. week is the
    canonical week; postseason maps back to seasontype=3."""
    stype, wk = (3, week - 15) if week > 15 else (2, week)
    j = fetch_json(f"{ESPN_SITE}/scoreboard?groups=80&dates={season}"
                   f"&seasontype={stype}&week={wk}&limit=400")
    events = j.get("events")
    if events is None:
        raise SchemaError(f"scoreboard: no 'events' key (keys: {sorted(j.keys())})")
    out, venues = {}, {}
    for ev in events:
        gid = str(ev.get("id", ""))
        comp = (ev.get("competitions") or [{}])[0]
        rec = {}
        # odds: details like "UGA -7.5", overUnder numeric; homeTeamOdds side flags
        odds = (comp.get("odds") or [{}])[0]
        sp = odds.get("spread")
        if sp is None:
            det = str(odds.get("details") or "")
            m = re.search(r"(-?\d+(\.\d+)?)", det)
            sp_val = float(m.group(1)) if m else None
            if sp_val is not None:
                # details name the favourite; sign it home-negative
                fav_home = None
                for c in comp.get("competitors", []):
                    ab = ((c.get("team") or {}).get("abbreviation") or "").upper()
                    if ab and ab in det.upper():
                        fav_home = (c.get("homeAway") == "home")
                        break
                sp = -abs(sp_val) if fav_home else (abs(sp_val) if fav_home is not None else None)
        rec["spread"] = _f(sp, None) if sp is not None else None
        ou = odds.get("overUnder")
        rec["total"] = _f(ou, None) if ou is not None else None
        for c in comp.get("competitors", []):
            rk = ((c.get("curatedRank") or {}).get("current"))
            if rk and _int0(rk) and _int0(rk) <= 25:
                rec["homeRank" if c.get("homeAway") == "home" else "awayRank"] = _int0(rk)
        out[gid] = rec
        v = comp.get("venue") or {}
        addr = v.get("address") or {}
        venues[gid] = {"name": v.get("fullName") or "", "city": addr.get("city") or "",
                       "state": addr.get("state") or "", "indoor": bool(v.get("indoor"))}
    return out, venues

# -- ESPN summary -> player box + scoring plays + closing line ----------------
# Label-driven category parsing: map labels to fields by NAME, never position.
CAT_LABELS = {
    "passing":   {"C/ATT": "catt", "YDS": "passYds", "TD": "passTds", "INT": "passInt",
                  "SACKS-YDSLOST": "sackyl", "SACKS": "sackyl"},
    "rushing":   {"CAR": "rushAtt", "ATT": "rushAtt", "YDS": "rushYds",
                  "TD": "rushTds", "LONG": "longRushBox"},
    "receiving": {"REC": "receptions", "YDS": "recYds", "TD": "recTds",
                  "LONG": "longRecBox", "TGTS": "targetsBox"},
}
REQUIRED_LABELS = {"passing": {"C/ATT", "YDS", "TD", "INT"},
                   "rushing": {"YDS", "TD"},
                   "receiving": {"REC", "YDS", "TD"}}

def parse_espn_summary(j, game_id):
    """-> {gameId, players:{athleteId:{id,name,teamId,stats...}}, scoring:[...],
    line:{spread,total,provider}|None}. Raises SchemaError loudly on shape drift."""
    box = j.get("boxscore") or {}
    pteams = box.get("players")
    if pteams is None:
        raise SchemaError(f"summary {game_id}: boxscore.players missing "
                          f"(boxscore keys: {sorted(box.keys())})")
    players = {}
    for tb in pteams:
        team = tb.get("team") or {}
        tid = str(team.get("id", ""))
        for cat in tb.get("statistics", []):
            cname = str(cat.get("name", "")).lower()
            if cname not in CAT_LABELS:
                continue
            labels = [str(x) for x in (cat.get("labels") or cat.get("keys") or [])]
            lmap = CAT_LABELS[cname]
            idx = {lmap[lb]: i for i, lb in enumerate(labels) if lb in lmap}
            missing = {lb for lb in REQUIRED_LABELS[cname]
                       if lmap.get(lb) not in idx and lb in lmap}
            aths = cat.get("athletes") or []
            if aths and missing:
                raise SchemaError(f"summary {game_id}: category '{cname}' labels {labels} "
                                  f"missing required {sorted(missing)}")
            unknown = [lb for lb in labels if lb not in lmap
                       and lb not in ("AVG", "RTG", "QBR", "LONG", "TGTS")]
            if unknown:
                print(f"    (summary {game_id}: '{cname}' unrecognised labels {unknown} - ignored)")
            for a in aths:
                ath = a.get("athlete") or {}
                aid = str(ath.get("id", ""))
                if not aid:
                    continue
                stats = a.get("stats") or []
                P = players.setdefault(aid, {"id": aid,
                                             "name": ath.get("displayName") or "",
                                             "teamId": tid})
                def sv(field):
                    i = idx.get(field)
                    return stats[i] if (i is not None and i < len(stats)) else None
                if cname == "passing":
                    catt = str(sv("catt") or "")
                    if "/" in catt:
                        c_, a_ = catt.split("/", 1)
                        P["passComp"] = _int0(c_); P["passAtt"] = _int0(a_)
                    P["passYds"] = _f(sv("passYds"))
                    P["passTds"] = _int0(sv("passTds"))
                    P["passInt"] = _int0(sv("passInt"))
                    syl = str(sv("sackyl") or "")
                    P["sacks"] = _int0(syl.split("-", 1)[0]) if syl else 0
                elif cname == "rushing":
                    P["rushAtt"] = _int0(sv("rushAtt"))
                    P["rushYds"] = _f(sv("rushYds"))
                    P["rushTds"] = _int0(sv("rushTds"))
                elif cname == "receiving":
                    P["receptions"] = _int0(sv("receptions"))
                    P["recYds"] = _f(sv("recYds"))
                    P["recTds"] = _int0(sv("recTds"))
    scoring = []
    for sp in j.get("scoringPlays") or []:
        scoring.append({"teamId": str((sp.get("team") or {}).get("id", "")),
                        "period": _int0(sp.get("period", {}).get("number")
                                        if isinstance(sp.get("period"), dict)
                                        else sp.get("period")),
                        "clock": (sp.get("clock") or {}).get("displayValue", "")
                                 if isinstance(sp.get("clock"), dict) else str(sp.get("clock") or ""),
                        "type": ((sp.get("type") or {}).get("text") or ""),
                        "text": sp.get("text") or ""})
    line = None
    for pc in j.get("pickcenter") or []:
        sp, ou = pc.get("spread"), pc.get("overUnder")
        if sp is not None or ou is not None:
            line = {"spread": _f(sp, None) if sp is not None else None,
                    "total": _f(ou, None) if ou is not None else None,
                    "provider": ((pc.get("provider") or {}).get("name") or "")}
            break
    return {"gameId": str(game_id), "players": players, "scoring": scoring, "line": line}

def update_box_store(raw_dir, season, games, max_fetch, weeks=None):
    """Fetch ESPN summaries for completed FBS-involved games missing from the
    cache. Commits nothing itself - the workflow commits raw/. Resumable."""
    path = os.path.join(raw_dir, f"player_box_{season}.json")
    store = rjson(path, {}) or {}
    want = [gm for (s, _), gm in sorted(games.items())
            if s == season and gm["completed"]
            and (gm["homeDiv"] == "fbs" or gm["awayDiv"] == "fbs")
            and (weeks is None or gm["week"] in weeks)
            and gm["gameId"] not in store]
    if not want:
        print(f"  box store {season}: up to date ({len(store)} games)")
        return store, 0
    n = 0
    print(f"  box store {season}: {len(want)} games to fetch (cap {max_fetch})")
    for gm in want[:max_fetch]:
        gid = gm["gameId"]
        try:
            j = fetch_json(f"{ESPN_SITE}/summary?event={gid}", timeout=45)
            rec = parse_espn_summary(j, gid)
            rec["week"] = gm["week"]; rec["fetchedAt"] = _now()
            store[gid] = rec; n += 1
            if n % 25 == 0:
                wjson(path, store)         # checkpoint - resumable mid-run
                print(f"    ...{n} fetched (checkpoint)")
        except SchemaError:
            raise                          # loud by design: fix from the log
        except Exception as e:
            print(f"    (summary {gid} failed, will retry next run: {e})")
        time.sleep(FETCH_SLEEP)
    wjson(path, store)
    print(f"  box store {season}: +{n} games -> {len(store)} cached")
    return store, n

def _now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# -- box store -> gamelogs -----------------------------------------------------
def box_to_gamelogs(stores, games, by_team, roster_idx):
    """stores: {season: {gameId: rec}} -> NFL-shaped gamelog rows."""
    gamelogs = []
    for season, store in sorted(stores.items()):
        for gid, rec in sorted(store.items()):
            gm = games.get((season, gid))
            if gm is None:
                continue
            fbs_v_fbs = gm["homeDiv"] == "fbs" and gm["awayDiv"] == "fbs"
            # team plays for touchShare
            tp = defaultdict(float)
            for P in rec["players"].values():
                tp[P["teamId"]] += (_int0(P.get("passAtt")) + _int0(P.get("rushAtt"))
                                    + _int0(P.get("sacks")))
            for aid, P in rec["players"].items():
                tid = P["teamId"]
                home = 1 if tid == gm["homeId"] else 0
                opp = gm["awayId"] if home else gm["homeId"]
                row = {"Year": str(season), "Week": rec.get("week", gm["week"]),
                       "MatchId": gid, "Player": P["name"], "PlayerId": aid,
                       "Team": tid, "Opp": opp, "home": home,
                       "vsFcs": 0 if fbs_v_fbs else 1}
                for k in ("passYds","passAtt","passComp","passTds","passInt","sacks",
                          "rushYds","rushAtt","rushTds","receptions","recYds","recTds"):
                    row[k] = r1(_f(P.get(k), 0.0))
                row["rushRecYds"] = r1(row["rushYds"] + row["recYds"])
                row["totalTds"] = r1(row["rushTds"] + row["recTds"])   # books' anytime scope
                row["anytimeTd"] = 1 if row["totalTds"] > 0 else 0
                row["fanPts"] = fan_pts(row)
                touches = (row["passAtt"] + row["sacks"] + row["rushAtt"]
                           + row["receptions"])
                row["touchShare"] = r1(touches / tp[tid] * 100) if tp.get(tid) else 0.0
                gamelogs.append(row)
    gamelogs.sort(key=lambda x: (x["Year"], x["Week"], x["MatchId"], x["Player"]))
    return gamelogs

# -- enrichment: player_stats parquet -> rz/gl usage + long/explosive ---------
def build_enrichment(ps_frames):
    """-> per-game {(season, gameId, athleteId): {longRec,longRush,longComp,
    expRec,expRush, rzTgt,rzCarry,glCarry}}. Yardage/position fields only -
    TD flags in this file are unreliable (validated) and never read."""
    per_game = {}
    for season, df in sorted(ps_frames.items()):
        need = ["game_id","yards_to_goal","rush_player_id","rush_yds",
                "reception_player_id","reception_yds","completion_player_id",
                "completion_yds","target_player_id"]
        missing = [c for c in need if c not in df.columns]
        if missing:
            print(f"  (player_stats {season}: columns {missing} missing - enrichment skipped)")
            continue
        gid_a = df["game_id"].astype(str)
        ytg_a = df["yards_to_goal"]
        def cell(gid, aid):
            key = (season, gid, str(_int0(aid)))
            c = per_game.get(key)
            if c is None:
                c = per_game[key] = {"longRec": None, "longRush": None, "longComp": None,
                                     "expRec": 0, "expRush": 0,
                                     "rzTgt": 0, "rzCarry": 0, "glCarry": 0}
            return c
        rush_ok = df["rush_player_id"].notna()
        for gid, aid, yds, ytg in zip(gid_a[rush_ok], df.loc[rush_ok, "rush_player_id"],
                                      df.loc[rush_ok, "rush_yds"], ytg_a[rush_ok]):
            c = cell(gid, aid); y = _f(yds, None)
            if y is not None:
                if c["longRush"] is None or y > c["longRush"]: c["longRush"] = y
                if y >= 10: c["expRush"] += 1
            t = _f(ytg, None)
            if t is not None:
                if t <= 20: c["rzCarry"] += 1
                if t <= 5:  c["glCarry"] += 1
        rec_ok = df["reception_player_id"].notna()
        for gid, aid, yds in zip(gid_a[rec_ok], df.loc[rec_ok, "reception_player_id"],
                                 df.loc[rec_ok, "reception_yds"]):
            c = cell(gid, aid); y = _f(yds, None)
            if y is not None:
                if c["longRec"] is None or y > c["longRec"]: c["longRec"] = y
                if y >= 20: c["expRec"] += 1
        cmp_ok = df["completion_player_id"].notna()
        for gid, aid, yds in zip(gid_a[cmp_ok], df.loc[cmp_ok, "completion_player_id"],
                                 df.loc[cmp_ok, "completion_yds"]):
            c = cell(gid, aid); y = _f(yds, None)
            if y is not None and (c["longComp"] is None or y > c["longComp"]):
                c["longComp"] = y
        # rz targets: receiver targeted inside the 20. Target attribution is
        # sparse in this file, so take reception ?? target per play (one count
        # per play, never both).
        any_rcv = df["reception_player_id"].notna() | df["target_player_id"].notna()
        rcv = df.loc[any_rcv, "reception_player_id"].where(
            df.loc[any_rcv, "reception_player_id"].notna(),
            df.loc[any_rcv, "target_player_id"])
        for gid, aid, ytg in zip(gid_a[any_rcv], rcv, ytg_a[any_rcv]):
            t = _f(ytg, None)
            if t is not None and t <= 20:
                cell(gid, aid)["rzTgt"] += 1
    print(f"  enrichment: {len(per_game)} player-game cells")
    return per_game

def merge_enrichment(gamelogs, enrich):
    hit = 0
    for row in gamelogs:
        key = (int(row["Year"]), row["MatchId"], row["PlayerId"])
        e = enrich.get(key)
        for k in ENRICH_KEYS:
            row[k] = (r1(e[k]) if e and e[k] is not None else None) if k.startswith("long") \
                     else (e[k] if e else None)
        if e:
            hit += 1
            row["rzTgt_g"] = e["rzTgt"]; row["rzCarry_g"] = e["rzCarry"]; row["glCarry_g"] = e["glCarry"]
        else:
            row["rzTgt_g"] = row["rzCarry_g"] = row["glCarry_g"] = None
    if gamelogs:
        print(f"  enrichment merged onto {hit}/{len(gamelogs)} gamelog rows "
              f"({hit/len(gamelogs)*100:.0f}%)")
    return gamelogs

# -- first TD from scoringPlays -----------------------------------------------
_TD_RX = re.compile(r"touchdown", re.I)
_NAME_RX = re.compile(r"^([A-Z][\w'.-]+(?: [A-Z][\w'.-]+){0,3}?) "
                      r"(?:\d+ ?(?:Yd|Yrd|Yard))", re.I)

def build_firsttd(stores, games):
    out = []
    for season, store in sorted(stores.items()):
        for gid, rec in sorted(store.items()):
            gm = games.get((season, gid))
            if not gm: continue
            seen_team, game_first = set(), False
            for sp in rec.get("scoring") or []:
                if not (_TD_RX.search(sp.get("type", "")) or _TD_RX.search(sp.get("text", ""))):
                    continue
                m = _NAME_RX.match(sp.get("text", "").strip())
                player = m.group(1) if m else ""
                tid = sp.get("teamId", "")
                if tid in seen_team:
                    continue
                seen_team.add(tid)
                out.append({"season": season, "week": rec.get("week", gm["week"]),
                            "matchId": gid, "teamId": tid, "player": player,
                            "text": sp.get("text", "")[:120],
                            "gameFirst": 0 if game_first else 1})
                game_first = True
    return out

# -- players / teams / dvp (NFL parity, college keys) -------------------------
def build_roster_idx(ros):
    """athleteId -> {pos, classYear, headshot, team(school)}."""
    idx = {}
    if ros is None or getattr(ros, "empty", True):
        return idx
    aid = col(ros, "athlete_id"); pos = col(ros, "position"); yr = col(ros, "year")
    hs = col(ros, "headshot_url"); tm = col(ros, "team")
    fn = col(ros, "first_name"); ln = col(ros, "last_name")
    for _, r in ros.iterrows():
        i = str(_int0(g(r, aid)))
        if not i or i == "0": continue
        y = _int0(g(r, yr))
        idx[i] = {"pos": str(g(r, pos, "") or "").upper(),
                  "classYear": CLASS_MAP.get(y, ""),
                  "headshot": str(g(r, hs, "") or ""),
                  "school": str(g(r, tm, "") or ""),
                  "name": (str(g(r, fn, "") or "") + " " + str(g(r, ln, "") or "")).strip()}
    return idx

def infer_position(rows, roster_pos):
    p = POS_MAP.get(roster_pos)
    if p: return p
    # usage inference for ATH/unknown
    pa = sum(x["passAtt"] for x in rows); ra = sum(x["rushAtt"] for x in rows)
    rc = sum(x["receptions"] for x in rows)
    if pa >= max(ra, rc, 1): return "QB"
    if ra >= rc: return "RB"
    return "WR"

def build_players(gamelogs, roster_idx, current):
    agg = {}
    for r in gamelogs:
        P = agg.setdefault(r["PlayerId"], {"name": r["Player"], "team": r["Team"],
                                           "rows": [], "id": r["PlayerId"]})
        P["rows"].append(r)
        if r["Year"] == str(current):          # latest-team-wins within current
            P["team"] = r["Team"]; P["name"] = r["Player"]
    players = []
    names_seen = defaultdict(int)
    for pid, P in agg.items():
        rows = P["rows"]
        cur = [x for x in rows if x["Year"] == str(current)]
        src = cur or [x for x in rows if x["Year"] == str(current - 1)] or rows
        rep = [x for x in src if not x["vsFcs"]] or src   # FCS games out of averages
        n = len(rep)
        rmeta = roster_idx.get(pid, {})
        pos = infer_position(rep, rmeta.get("pos", ""))
        p = {"name": P["name"], "playerId": pid, "team": P["team"],
             "position": pos, "posDetail": rmeta.get("pos", pos),
             "classYear": rmeta.get("classYear", ""),
             "headshot": rmeta.get("headshot", ""), "matches": n}
        for k in STAT_KEYS:
            p[k] = r1(sum(x[k] for x in rep) / n)
        p["touchShare"] = r1(sum(x["touchShare"] for x in rep) / n)
        p["ypr"] = r1(p["recYds"] / p["receptions"]) if p["receptions"] else 0.0
        p["ypc"] = r1(p["rushYds"] / p["rushAtt"]) if p["rushAtt"] else 0.0
        for k in ("longRec", "longRush", "longComp"):
            vals = [x[k] for x in rep if x.get(k) is not None]
            p[k] = r1(sum(vals) / len(vals)) if vals else 0.0
        for k in ("expRec", "expRush"):
            vals = [x[k] for x in rep if x.get(k) is not None]
            p[k] = r3(sum(vals) / len(vals)) if vals else 0.0
        gcells = [x for x in rep if x.get("rzTgt_g") is not None]
        gn = max(len(gcells), 1)
        p["rzTgt"] = r3(sum(x["rzTgt_g"] for x in gcells) / gn) if gcells else 0.0
        p["rzCarry"] = r3(sum(x["rzCarry_g"] for x in gcells) / gn) if gcells else 0.0
        p["glCarry"] = r3(sum(x["glCarry_g"] for x in gcells) / gn) if gcells else 0.0
        players.append(p)
        names_seen[_norm(p["name"])] += 1
    players.sort(key=lambda x: -x["fanPts"])
    dupes = sum(1 for v in names_seen.values() if v > 1)
    if dupes:
        print(f"  players: {dupes} duplicate display names across FBS "
              f"(playerId is the join key; shell disambiguates by team)")
    return players

def _team_games(gamelogs, results, current):
    tg = defaultdict(lambda: defaultdict(float)); meta = {}
    for r in gamelogs:
        if r["Year"] != str(current): continue
        key = (r["Team"], r["MatchId"])
        for k in ["passYds","passAtt","passComp","passTds","passInt","sacks",
                  "rushYds","rushAtt","rushTds","receptions"]:
            tg[key][k] += r[k]
        meta[key] = {"opp": r["Opp"], "week": r["Week"], "vsFcs": r["vsFcs"]}
    pts = {}
    for x in results:
        if str(x["season"]) != str(current): continue
        pts[(x["home"], x["week"])] = x["hs"]; pts[(x["away"], x["week"])] = x["as"]
    for key, m in meta.items():
        team, _ = key
        tg[key]["points"] = float(pts.get((team, m["week"]), 0))
        tg[key]["plays"] = tg[key]["passAtt"] + tg[key]["rushAtt"] + tg[key]["sacks"]
    return tg, meta

def build_teams(gamelogs, results, current, confs, identity, form_n=None):
    tg, meta = _team_games(gamelogs, results, current)
    by_team = defaultdict(list)
    for (team, mid), row in tg.items():
        m = meta[(team, mid)]
        by_team[team].append((m["week"], row, m["opp"], mid, m["vsFcs"]))
    out = []
    for team, games in sorted(by_team.items()):
        games.sort(key=lambda x: x[0])
        if form_n:
            games = games[-form_n:]
        n = len(games)
        cf = confs.get(team, "")
        tm = team_meta(identity, team, "")
        rec = {"team": team, "abbr": tm["abbr"], "school": tm["school"],
               "displayName": tm["displayName"], "conference": cf,
               "p4": 1 if team_is_p4(team, cf) else 0, "matches": n}
        for k in TEAM_KEYS:
            rec[k] = r1(sum(x[1][k] for x in games) / n)
        for k in TEAM_KEYS:
            vals = [tg[(opp, mid)][k] for _, _, opp, mid, _ in games if (opp, mid) in tg]
            rec[k + "_a"] = r1(sum(vals) / len(vals)) if vals else 0.0
        out.append(rec)
    return out

def build_dvp(gamelogs, players, current, confs):
    """defense teamId x pos -> per-game allowed. FBS-vs-FBS games only."""
    pos_by_id = {p["playerId"]: p["position"] for p in players}
    sums = defaultdict(lambda: defaultdict(float)); games = defaultdict(set)
    for r in gamelogs:
        if r["Year"] != str(current) or r["vsFcs"]:
            continue
        pos = pos_by_id.get(r["PlayerId"])
        if pos not in POSITIONS:
            continue
        key = (r["Opp"], pos)
        for k in STAT_KEYS:
            sums[key][k] += r[k]
        sums[key]["anytimeTd"] += r["anytimeTd"]
        games[key].add(r["MatchId"])
        for k in ENRICH_KEYS:
            v = r.get(k)
            if v is None: continue
            if k.startswith("long"):
                mkey = k + "|" + r["MatchId"]
                sums[key][mkey] = max(sums[key].get(mkey, 0.0), v)
            else:
                sums[key][k + "|" + r["MatchId"]] = sums[key].get(k + "|" + r["MatchId"], 0.0) + v
    out = []
    for (team, pos), s in sorted(sums.items()):
        n = max(len(games[(team, pos)]), 1)
        rec = {"team": team, "pos": pos, "conf": confs.get(team, ""), "games": n}
        for k in STAT_KEYS + ["anytimeTd"]:
            rec[k] = r3(s[k] / n)
        for k in ENRICH_KEYS:
            vals = [v for kk, v in s.items() if kk.startswith(k + "|")]
            if vals:
                rec[k] = r3(sum(vals) / len(vals))
        out.append(rec)
    return out

# -- blowout calibration (2006-2019 closing spreads x schedule margins) -------
CALIB_BUCKETS = [(0, 3), (3, 7), (7, 10), (10, 14), (14, 17), (17, 21),
                 (21, 28), (28, 99)]

def build_blowout_calib(bet_df, old_sched_frames):
    """|closing spread| bucket -> P(margin>=17), P(margin>=24), median margin, n.
    Powers Garbage Time: given today's ESPN spread, how blowout-prone is this?"""
    if bet_df is None or getattr(bet_df, "empty", True):
        return []
    gid = col(bet_df, "game_id"); mt = col(bet_df, "market_type"); ln = col(bet_df, "lines")
    sub = bet_df[(bet_df[mt] == "spread")]
    fav = {}
    for _, r in sub.iterrows():
        v = _f(g(r, ln, None), None)
        if v is None or v >= 0: continue                 # favourite rows only
        gk = str(_int0(g(r, gid)))
        fav.setdefault(gk, []).append(abs(v))
    margins = {}
    for gm in sched_rows(old_sched_frames):
        if gm["hs"] is None: continue
        margins[gm["gameId"]] = abs(gm["hs"] - gm["as"])
    rows = [(sorted(v)[len(v)//2], margins[k]) for k, v in fav.items() if k in margins]
    out = []
    for lo, hi in CALIB_BUCKETS:
        b = [m for s, m in rows if lo <= s < hi]
        if len(b) < 25: continue
        b.sort()
        out.append({"spreadLo": lo, "spreadHi": hi, "n": len(b),
                    "pBlowout17": r3(sum(1 for m in b if m >= 17) / len(b)),
                    "pBlowout24": r3(sum(1 for m in b if m >= 24) / len(b)),
                    "medianMargin": b[len(b)//2]})
    print(f"  blowout calib: {len(rows)} joined games -> {len(out)} buckets")
    return out

# -- injuries (ESPN, guarded) -------------------------------------------------
def fetch_injuries(identity):
    j = fetch_json(f"{ESPN_SITE}/injuries", timeout=45)
    blocks = j.get("injuries")
    if blocks is None:
        raise SchemaError(f"injuries: no 'injuries' key (keys: {sorted(j.keys())})")
    out = []
    for tb in blocks:
        tid = str((tb.get("team") or {}).get("id", "") or tb.get("id", ""))
        tmeta = identity.get(tid, {})
        for it in tb.get("injuries", []):
            ath = it.get("athlete") or {}
            out.append({"TeamId": tid, "Team": tmeta.get("abbr", ""),
                        "Player": ath.get("displayName") or "",
                        "Position": ((ath.get("position") or {}).get("abbreviation") or ""),
                        "Injury": ((it.get("details") or {}).get("type") or ""),
                        "Status": it.get("status") or ""})
    return out

# -- weather (Open-Meteo, geocode cache, guarded) -----------------------------
def build_weather(fixture, venues, raw_dir):
    coords_path = os.path.join(raw_dir, "venue_coords.json")
    coords = rjson(coords_path, {}) or {}
    out = []
    for fx in fixture:
        v = venues.get(next((k for k in venues), ""), {}) if not venues else venues.get(fx.get("home"), {})
        v = venues.get(fx["home"] + "|" + fx["away"], v) if venues else {}
        name = (v.get("name") or fx.get("venue") or "").strip()
        if not name:
            continue
        if _norm(name) in DOME_VENUES or v.get("indoor"):
            out.append({"home": fx["home"], "away": fx["away"], "indoor": 1}); continue
        key = _norm(name)
        c = coords.get(key)
        if c is None:
            q = ", ".join(x for x in (v.get("city"), v.get("state")) if x) or name
            try:
                j = fetch_json("https://geocoding-api.open-meteo.com/v1/search?name="
                               + re.sub(r"\s+", "+", q) + "&count=1", timeout=20)
                res = (j.get("results") or [{}])[0]
                c = {"lat": res.get("latitude"), "lon": res.get("longitude")}
                coords[key] = c
            except Exception:
                continue
        if not c or c.get("lat") is None:
            continue
        try:
            day = fx["utc"][:10]
            w = fetch_json(f"https://api.open-meteo.com/v1/forecast?latitude={c['lat']}"
                           f"&longitude={c['lon']}&daily=windspeed_10m_max,"
                           f"precipitation_probability_max,temperature_2m_max"
                           f"&start_date={day}&end_date={day}&timezone=UTC", timeout=20)
            d = w.get("daily") or {}
            out.append({"home": fx["home"], "away": fx["away"], "indoor": 0,
                        "windKph": (d.get("windspeed_10m_max") or [None])[0],
                        "rainPct": (d.get("precipitation_probability_max") or [None])[0],
                        "tempC": (d.get("temperature_2m_max") or [None])[0]})
        except Exception:
            continue
    if coords:
        wjson(coords_path, coords)
    return out

# -- main build ---------------------------------------------------------------
def run_build(out_dir, seasons, current, password, args, frames=None):
    raw_dir = os.path.join(out_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    if frames:                                            # selftest injection
        sch_frames, ros, ps_frames, bet_df, stores, identity = frames
        sb_odds, sb_venues, injuries_json = {}, {}, None
    else:
        sch_frames = load_schedules(seasons)
        identity, _ = load_team_identity(raw_dir, args.skip_espn)
        # parquet lags for the newest season (publishes late pre-season):
        # synthesize from ESPN scoreboards so the fixture still ships
        for s in seasons:
            if s in sch_frames or args.skip_espn:
                continue
            conf_hint = {}
            for prev in sorted(sch_frames, reverse=True):
                for gm in sched_rows({prev: sch_frames[prev]}):
                    conf_hint.setdefault(gm["homeId"], gm["homeConf"])
                    conf_hint.setdefault(gm["awayId"], gm["awayConf"])
                break
            try:
                sch_frames[s] = schedules_from_espn(s, identity, conf_hint)
            except Exception as e:
                print(f"  (ESPN schedule fallback for {s} failed: {e})")
        ros, ros_season = load_rosters(current)
        ps_frames = load_player_stats([s for s in seasons])
        bet_df = None
        if not args.skip_calib:
            calib_path = os.path.join(out_dir, "blowout_calib.json")
            if os.path.exists(calib_path) and not args.rebuild_calib:
                print("  blowout_calib.json exists - skipping (use --rebuild-calib to redo)")
            else:
                try:
                    bet_df = load_parquet(f"{CFBDATA}/betting/parquet/cfb_line_odds.parquet")
                except Exception as e:
                    print(f"  (betting parquet unavailable - calib skipped: {e})")
        stores = None

    games, by_team = build_game_index(sch_frames)
    results = build_results(games)

    # conference lookup: teamId -> conference (latest season wins)
    confs = {}
    for gm in sched_rows(sch_frames):
        if gm["homeConf"]: confs[gm["homeId"]] = gm["homeConf"]
        if gm["awayConf"]: confs[gm["awayId"]] = gm["awayConf"]

    # ---- box store (ESPN primary) ----
    fetched = 0
    if stores is None:
        stores = {}
        for s in seasons:
            if args.skip_espn:
                stores[s] = rjson(os.path.join(raw_dir, f"player_box_{s}.json"), {}) or {}
                print(f"  box store {s}: {len(stores[s])} cached games (--skip-espn)")
            else:
                weeks = None
                if args.backfill and s == args.backfill and args.weeks:
                    a, _, b = args.weeks.partition("-")
                    weeks = set(range(int(a), int(b or a) + 1))
                target = (s == args.backfill) if args.backfill else (s == current)
                if target:
                    stores[s], n = update_box_store(raw_dir, s, games, args.max_fetch, weeks)
                    fetched += n
                else:
                    stores[s] = rjson(os.path.join(raw_dir, f"player_box_{s}.json"), {}) or {}
                    print(f"  box store {s}: {len(stores[s])} cached games (not target season)")

    roster_idx = build_roster_idx(ros)
    gamelogs = box_to_gamelogs(stores, games, by_team, roster_idx)
    enrich = build_enrichment(ps_frames)
    gamelogs = merge_enrichment(gamelogs, enrich)
    players = build_players(gamelogs, roster_idx, current)
    firsttd = build_firsttd(stores, games)

    # ---- fixture (+ scoreboard odds/ranks when reachable) ----
    fixture, next_week, fx_season = build_fixture(games)
    sb_venues = {}
    if fixture and not args.skip_espn:
        try:
            sbo, sbv = fetch_scoreboard_odds(fx_season, next_week)
            fixture, next_week, fx_season = build_fixture(games, sbo)
            sb_venues = {fx["home"] + "|" + fx["away"]: sbv.get(fx_gid, {})
                         for fx, fx_gid in zip(fixture, [f2["gameId"] for f2 in
                            sorted((gm for gm in games.values()
                                    if not gm["completed"] and gm["season"] == fx_season
                                    and gm["week"] == next_week
                                    and (gm["homeDiv"] == "fbs" or gm["awayDiv"] == "fbs")),
                                   key=lambda x: x["date"])])}
        except Exception as e:
            print(f"  (scoreboard odds unavailable - fixture ships without lines: {e})")

    # Aggregate teams/DVP over the latest season actually in the gamelogs. In the off-season
    # `current` (calendar) can be a season with no games yet (e.g. 2026 before kickoff), which
    # would zero out teams/DVP - so fall back to the newest season the data actually has.
    _gl_years = [int(r["Year"]) for r in gamelogs if r.get("Year")]
    agg_current = max(_gl_years) if _gl_years else current
    if agg_current != current:
        print(f"  [teams/dvp] current={current} has no gamelogs; aggregating over {agg_current}")
    teams = build_teams(gamelogs, results, agg_current, confs, identity)
    teams_form = build_teams(gamelogs, results, agg_current, confs, identity, form_n=FORM_N)
    dvp = build_dvp(gamelogs, players, agg_current, confs)

    # ---- guarded extras ----
    injuries = None
    if not args.skip_espn:
        try:
            injuries = fetch_injuries(identity)
        except Exception as e:
            print(f"  (injuries fetch failed - keeping existing file: {e})")
    weather = []
    if fixture and not args.skip_weather and not args.skip_espn:
        try:
            weather = build_weather(fixture, sb_venues, raw_dir)
        except Exception as e:
            print(f"  (weather failed - shipping empty: {e})")
    calib = None
    if frames:
        calib = build_blowout_calib(bet_df, sch_frames)
    elif bet_df is not None:
        old = {}
        for s in sorted(set(int(x) for x in bet_df["season"].dropna().unique())):
            try:
                old[s] = load_parquet(f"{CFBDATA}/schedules/parquet/cfb_schedules_{s}.parquet")
            except Exception:
                pass
        calib = build_blowout_calib(bet_df, old)

    # ---- writes ----
    week = next_week
    if week is None:
        cur = [x for x in results if x["season"] == current]
        week = max((x["week"] for x in cur), default=0)

    glog_files = defaultdict(list)
    for r in gamelogs:
        glog_files[r["Year"]].append(r)
    for yr, recs in sorted(glog_files.items()):
        wjson(f"{out_dir}/gamelogs_{yr}.json", recs)

    wjson(f"{out_dir}/players.json", players)
    wjson(f"{out_dir}/teams.json", teams)
    wjson(f"{out_dir}/teams_form.json", teams_form)
    wjson(f"{out_dir}/dvp.json", dvp)
    wjson(f"{out_dir}/fixture.json", fixture)
    wjson(f"{out_dir}/results.json", results)
    wjson(f"{out_dir}/firsttd.json", firsttd)
    if injuries is not None:
        wjson(f"{out_dir}/injury.json", injuries)
    elif not os.path.exists(f"{out_dir}/injury.json"):
        wjson(f"{out_dir}/injury.json", [])
    wjson(f"{out_dir}/weather.json", weather)
    if calib is not None:
        wjson(f"{out_dir}/blowout_calib.json", calib)
    if not os.path.exists(f"{out_dir}/odds.json"):
        wjson(f"{out_dir}/odds.json", {"_sample": True, "updated": _now(),
              "source": "stub (worker owns this file)", "lines": [], "alt": [],
              "books": [], "matchOdds": []})

    box_counts = {str(s): len(st) for s, st in sorted(stores.items())}
    meta = {"version": str(int(time.time())), "created": _now(),
            "week": week, "seasons": [str(s) for s in seasons],
            "currentSeason": str(current),
            "gamelogFiles": [f"gamelogs_{yr}.json" for yr in sorted(glog_files)],
            "boxStore": box_counts, "fetchedThisRun": fetched,
            "summary": {"players": len(players), "teams": len(teams),
                        "dvp": len(dvp), "gamelogs": len(gamelogs),
                        "fixtures": len(fixture), "results": len(results),
                        "firsttd": len(firsttd)},
            "derivedNote": ("box lines from ESPN summaries (owned store); rz/gl usage + "
                            "long/explosive from cfbfastR player_stats parquet (yardage only); "
                            "first-TD from scoringPlays; FCS games excluded from averages/DVP; "
                            "canonical team key = ESPN team id; postseason week = 16+n (bowls/CFP = 17)")}
    if password:
        meta["password_hash"] = hashlib.sha256(password.encode()).hexdigest()
    else:
        old = rjson(f"{out_dir}/meta.json", {}) or {}
        if old.get("password_hash"):
            meta["password_hash"] = old["password_hash"]
    wjson(f"{out_dir}/meta.json", meta)
    print("done.", meta["summary"])
    return meta

# -- selftest -----------------------------------------------------------------
def selftest():
    import pandas as pd
    print("SELFTEST - synthetic frames + synthetic ESPN summaries, full pipeline")
    ok = lambda c, m: (_ for _ in ()).throw(AssertionError(m)) if not c else print(f"  ok - {m}")

    # -- synthetic schedule: 4 FBS teams (ids 1..4), 1 FCS (id 99), season 2025
    def S(gid, wk, st, hid, aid, hn, an, hdv, adv, hc, ac, hs, as_, done, dt_, ne=False):
        return dict(game_id=gid, week=wk, season_type=st, home_id=hid, away_id=aid,
                    home_team=hn, away_team=an, home_division=hdv, away_division=adv,
                    home_conference=hc, away_conference=ac, home_points=hs, away_points=as_,
                    completed=done, start_date=dt_, neutral_site=ne, conference_game=True,
                    venue="Test Stadium", home_pregame_elo=1600.0, away_pregame_elo=1500.0)
    sch = pd.DataFrame([
        S("9001", 1, "regular", 1, 2, "Alpha", "Beta", "fbs", "fbs", "SEC", "SEC", 28, 21, True, "2025-08-30T16:00:00.000Z"),
        S("9002", 1, "regular", 3, 99, "Gamma", "Fcs U", "fbs", "fcs", "MAC", "Big Sky", 45, 3, True, "2025-08-30T20:00:00.000Z"),
        S("9003", 2, "regular", 2, 1, "Beta", "Alpha", "fbs", "fbs", "SEC", "SEC", 14, 31, True, "2025-09-06T16:00:00.000Z"),
        S("9004", 1, "postseason", 1, 3, "Alpha", "Gamma", "fbs", "fbs", "SEC", "MAC", None, None, False,
          (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=5)).strftime("%Y-%m-%dT16:00:00.000Z"), ne=True),
    ])
    games, by_team = build_game_index({2025: sch})
    ok(games[(2025, "9004")]["week"] == 17, "postseason week 1 -> canonical 17")
    ok(by_team[(2025, 1, "2")]["opp"] == "1" and by_team[(2025, 1, "2")]["home"] == 0,
       "game index sides resolve by team id")
    results = build_results(games)
    ok(len(results) == 3 and results[0]["hs"] == 28, "results built from completed games")
    ok([r for r in results if r["home"] == "3"][0]["fbsVfbs"] == 0, "FCS game flagged in results")

    fixture, wk, ssn = build_fixture(games)
    ok(wk == 17 and fixture[0]["neutral"] == 1, "fixture = next unplayed (bowl), neutral flagged")
    ok(fixture[0]["spread"] == -4.0, "Elo fallback spread = -(1600-1500)/25 home-negative")
    ok(fixture[0]["p4"] == 1 and fixture[0]["realistic"] == 1, "P4 flag drives props-realistic")
    fixture2, _, _ = build_fixture(games, {"9004": {"spread": -6.5, "total": 51.5, "homeRank": 8}})
    ok(fixture2[0]["spread"] == -6.5 and fixture2[0]["hasLine"] == 1 and fixture2[0]["homeRank"] == 8,
       "scoreboard odds override Elo fallback + set hasLine/rank")

    # -- synthetic ESPN summary: QB1 (id 11) + WR (id 12) on team 1; RB (21) team 2
    def summary(gid):
        return {"boxscore": {"players": [
            {"team": {"id": "1"}, "statistics": [
                {"name": "passing", "labels": ["C/ATT","YDS","AVG","TD","INT","SACKS-YDSLOST","QBR"],
                 "athletes": [{"athlete": {"id": "11", "displayName": "Quinn Beta"},
                               "stats": ["18/28","280","10.0","3","1","2-14","88"]}]},
                {"name": "rushing", "labels": ["CAR","YDS","AVG","TD","LONG"],
                 "athletes": [{"athlete": {"id": "11", "displayName": "Quinn Beta"},
                               "stats": ["9","64","7.1","1","22"]}]},
                {"name": "receiving", "labels": ["REC","YDS","AVG","TD","LONG"],
                 "athletes": [{"athlete": {"id": "12", "displayName": "Wes Route"},
                               "stats": ["7","121","17.3","2","44"]}]}]},
            {"team": {"id": "2"}, "statistics": [
                {"name": "rushing", "labels": ["CAR","YDS","AVG","TD","LONG"],
                 "athletes": [{"athlete": {"id": "21", "displayName": "Rex Ground"},
                               "stats": ["22","118","5.4","1","31"]}]}]}]},
            "scoringPlays": [
                {"team": {"id": "2"}, "period": {"number": 1}, "clock": {"displayValue": "10:12"},
                 "type": {"text": "Rushing Touchdown"}, "text": "Rex Ground 5 Yd Run"},
                {"team": {"id": "1"}, "period": {"number": 1}, "clock": {"displayValue": "6:03"},
                 "type": {"text": "Passing Touchdown"}, "text": "Wes Route 44 Yd pass from Quinn Beta"}],
            "pickcenter": [{"provider": {"name": "testbook"}, "spread": -7.5, "overUnder": 52.5}]}
    rec1 = parse_espn_summary(summary("9001"), "9001"); rec1["week"] = 1
    ok(rec1["players"]["11"]["passComp"] == 18 and rec1["players"]["11"]["passAtt"] == 28,
       "C/ATT split")
    ok(rec1["players"]["11"]["sacks"] == 2, "SACKS-YDSLOST '2-14' -> 2 sacks taken")
    ok(rec1["players"]["11"]["rushYds"] == 64.0 and rec1["players"]["12"]["recTds"] == 2,
       "categories merge onto one player record; receiving parsed")
    ok(rec1["line"]["spread"] == -7.5, "pickcenter closing line captured")
    try:
        bad = summary("9001"); bad["boxscore"]["players"][0]["statistics"][0]["labels"] = ["X","Y"]
        parse_espn_summary(bad, "9001"); raise AssertionError("SchemaError not raised")
    except SchemaError as e:
        ok("missing required" in str(e), "schema drift raises loud SchemaError")

    rec3 = parse_espn_summary(summary("9003"), "9003"); rec3["week"] = 2
    recF = parse_espn_summary(summary("9002"), "9002"); recF["week"] = 1   # FCS game
    stores = {2025: {"9001": rec1, "9003": rec3, "9002": recF}}

    ros = pd.DataFrame([
        dict(athlete_id=11, first_name="Quinn", last_name="Beta", team="Alpha",
             position="QB", year=2, headshot_url="hs://11", season=2025),
        dict(athlete_id=12, first_name="Wes", last_name="Route", team="Alpha",
             position="ATH", year=1, headshot_url="", season=2025),
        dict(athlete_id=21, first_name="Rex", last_name="Ground", team="Beta",
             position="RB", year=4, headshot_url="", season=2025)])
    roster_idx = build_roster_idx(ros)
    gamelogs = box_to_gamelogs(stores, games, by_team, roster_idx)
    q = [r for r in gamelogs if r["PlayerId"] == "11" and r["MatchId"] == "9001"][0]
    ok(q["passYds"] == 280.0 and q["rushRecYds"] == 64.0 and q["totalTds"] == 1.0,
       "gamelog row: pass box + rush box + anytime scope (rush+rec only)")
    ok(q["fanPts"] == r1(280*.04+3*4-2+64*.1+6), "fanPts formula")
    ok(q["vsFcs"] == 0 and [r for r in gamelogs if r["MatchId"] == "9002"][0]["vsFcs"] == 1,
       "vsFcs flag from schedule divisions")
    ok(q["Opp"] == "2" and q["home"] == 1, "opp/home joined by team id")

    # -- synthetic player_stats parquet frame (enrichment)
    ps = pd.DataFrame([
        dict(game_id="9001", yards_to_goal=18.0, rush_player_id=11, rush_yds=12.0,
             reception_player_id=None, reception_yds=None, completion_player_id=None,
             completion_yds=None, target_player_id=None),
        dict(game_id="9001", yards_to_goal=4.0, rush_player_id=11, rush_yds=4.0,
             reception_player_id=None, reception_yds=None, completion_player_id=None,
             completion_yds=None, target_player_id=None),
        dict(game_id="9001", yards_to_goal=60.0, rush_player_id=None, rush_yds=None,
             reception_player_id=12, reception_yds=44.0, completion_player_id=11,
             completion_yds=44.0, target_player_id=12),
        dict(game_id="9001", yards_to_goal=15.0, rush_player_id=None, rush_yds=None,
             reception_player_id=12, reception_yds=9.0, completion_player_id=11,
             completion_yds=9.0, target_player_id=12)])
    enrich = build_enrichment({2025: ps})
    gamelogs = merge_enrichment(gamelogs, enrich)
    ok(q["longRush"] == 12.0 and q["expRush"] == 1 and q["rzCarry_g"] == 2 and q["glCarry_g"] == 1,
       "enrichment: long/explosive rush + rz/gl carries")
    w = [r for r in gamelogs if r["PlayerId"] == "12" and r["MatchId"] == "9001"][0]
    ok(w["longRec"] == 44.0 and w["expRec"] == 1 and w["rzTgt_g"] == 1,
       "enrichment: receiver long/explosive + rz targets")
    ok(w["longComp"] is None and q["longComp"] == 44.0, "longComp lands on the passer")

    players = build_players(gamelogs, roster_idx, 2025)
    qp = [p for p in players if p["playerId"] == "11"][0]
    ok(qp["position"] == "QB" and qp["classYear"] == "SO" and qp["matches"] == 2,
       "players: roster join (pos/class), current-season games")
    wp = [p for p in players if p["playerId"] == "12"][0]
    ok(wp["position"] == "WR", "ATH position inferred from usage")
    gp = [p for p in players if p["team"] == "3"]
    ok(all(x["matches"] >= 1 for x in gp), "FCS-only opponents still get rows (rep fallback)")

    confs = {"1": "SEC", "2": "SEC", "3": "MAC"}
    identity = {"1": {"abbr": "ALP", "school": "Alpha", "displayName": "Alpha As", "logo": ""}}
    teams = build_teams(gamelogs, results, 2025, confs, identity)
    t1 = [t for t in teams if t["team"] == "1"][0]
    ok(t1["abbr"] == "ALP" and t1["conference"] == "SEC" and t1["p4"] == 1,
       "teams: identity cache + conference + P4")
    ok(t1["rushYds_a"] > 0, "allowed columns populated from opponents")
    tf = build_teams(gamelogs, results, 2025, confs, identity, form_n=FORM_N)
    ok(len(tf) == len(teams), "teams_form same team set")

    dvp = build_dvp(gamelogs, players, 2025, confs)
    ok(all(r["team"] != "99" for r in dvp), "FCS games excluded from DVP")
    d2qb = [r for r in dvp if r["team"] == "2" and r["pos"] == "QB"]
    ok(d2qb and d2qb[0]["passYds"] == 280.0 and d2qb[0]["conf"] == "SEC",
       "DVP allowed per game + defense conference")

    ftd = build_firsttd(stores, games)
    f1 = [x for x in ftd if x["matchId"] == "9001"]
    ok(len(f1) == 2 and f1[0]["teamId"] == "2" and f1[0]["gameFirst"] == 1
       and f1[0]["player"] == "Rex Ground" and f1[1]["gameFirst"] == 0,
       "first-TD: per-team first + gameFirst + scorer name from text")

    # calibration needs >=25 games per bucket: synthesize 30 games at -8.5
    # (17 of 30 end as 17+ blowouts) and 30 at -24 (all blowouts)
    bet_rows, cal_sched = [], []
    for i in range(30):
        gid_b = f"8{i:03d}"; gid_c = f"7{i:03d}"
        bet_rows += [dict(game_id=gid_b, market_type="spread", lines=-8.5),
                     dict(game_id=gid_c, market_type="spread", lines=-24.0)]
        cal_sched.append(S(gid_b, 3, "regular", 1, 2, "Alpha", "Beta", "fbs", "fbs",
                           "SEC", "SEC", 30, 30 - (20 if i < 17 else 3), True, "2025-09-13"))
        cal_sched.append(S(gid_c, 3, "regular", 1, 2, "Alpha", "Beta", "fbs", "fbs",
                           "SEC", "SEC", 45, 10, True, "2025-09-13"))
    bet = pd.DataFrame(bet_rows)
    calib = build_blowout_calib(bet, {2025: pd.DataFrame(cal_sched)})
    b7 = [b for b in calib if b["spreadLo"] == 7][0]
    b21 = [b for b in calib if b["spreadLo"] == 21][0]
    ok(abs(b7["pBlowout17"] - 17/30) < 0.001 and b21["pBlowout17"] == 1.0,
       "blowout calibration buckets from favourite-side medians")

    # -- full run_build with injected frames + JSON validity
    import tempfile, argparse as ap
    args = ap.Namespace(skip_espn=True, skip_weather=True, skip_calib=True,
                        rebuild_calib=False, backfill=None, weeks=None, max_fetch=0)
    with tempfile.TemporaryDirectory() as td:
        meta = run_build(td, [2025], 2025, "pw-test",
                         args, frames=({2025: sch}, ros, {2025: ps}, bet, stores, identity))
        ok(meta["gamelogFiles"] == ["gamelogs_2025.json"], "meta.gamelogFiles split")
        ok(meta["password_hash"] == hashlib.sha256(b"pw-test").hexdigest(), "password hashed")
        for fn in ("players.json", "teams.json", "dvp.json", "gamelogs_2025.json",
                   "fixture.json", "results.json", "firsttd.json", "meta.json",
                   "blowout_calib.json", "odds.json"):
            j = json.load(open(os.path.join(td, fn)))
            json.dumps(j, allow_nan=False)
            ok(True, f"{fn} valid NaN-free JSON")
    print("SELFTEST PASSED")

# -- cli ----------------------------------------------------------------------
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="ncaaf/data")
    ap.add_argument("--seasons", default="2024,2025,2026")
    ap.add_argument("--current", type=int, default=2026)
    ap.add_argument("--password", default=None)
    ap.add_argument("--max-fetch", type=int, default=150,
                    help="cap on new ESPN summary fetches this run")
    ap.add_argument("--backfill", type=int, default=None,
                    help="fetch this season's store instead of --current")
    ap.add_argument("--weeks", default=None, help="backfill week range, e.g. 1-8")
    ap.add_argument("--skip-espn", action="store_true",
                    help="no ESPN calls (cache-only build; sandbox mode)")
    ap.add_argument("--skip-weather", action="store_true")
    ap.add_argument("--skip-calib", action="store_true")
    ap.add_argument("--rebuild-calib", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest(); sys.exit(0)
    seasons = [int(s) for s in a.seasons.split(",") if s.strip()]
    run_build(a.out, seasons, a.current, a.password, a)
