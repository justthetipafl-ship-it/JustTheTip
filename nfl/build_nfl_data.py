#!/usr/bin/env python3
"""
build_nfl_data.py - JTT NFL split-data generator (advanced tool, AFL-parity).

Mirrors AFL/build_afl_data.py's role: one script that writes the split
nfl/data/*.json files the shell + scoring.js consume.

  meta.json        version, week, password_hash, seasons, currentSeason, summary
  players.json     per-game averages + usage (snap/target/carry/red-zone shares)
  teams.json       season offense per game + "_a" allowed columns + division
  teams_form.json  same, last FORM_N games only
  dvp.json         defense-vs-position: per-game allowed by (team, pos, stat)
  gamelogs.json    weekly player logs: Year/Week/MatchId/Player/Team/Opp/home + stats
  fixture.json     next unplayed week (home, away, utc, venue, roof, week)
  results.json     completed games (season, week, home, away, hs, as, date)
  injury.json      latest report  (Team, Player, Position, Injury, Status)
  lineups.json     depth charts   (team, player, position, depth, status)
  weather.json     forecast for outdoor fixture games (Open-Meteo), domes flagged
  firsttd.json     first-TD log per team per game (+ gameFirst flag)   [pbp]
  redzone.json     Tuddy Targets season splits (rz/i10/i5 + team share %) [pbp]
  dbs.json         Clamp Watch CB coverage grades (PFR adv defense via nflverse)
  odds.json        stub written ONLY if missing (worker owns the real file)

Sources: nflreadpy (nflverse). Red-zone/goal-line usage + first-TD need pbp,
which is heavy - restricted to --pbp-seasons (default: two most recent).

Run (GitHub Actions):
    pip install -r nfl/requirements.txt
    python nfl/build_nfl_data.py --out nfl/data \
        --seasons 2023,2024,2025 --current 2025 \
        --password "$NFL_WEEKLY_PASSWORD"

Self-test (no network; validates every transform on synthetic frames):
    python nfl/build_nfl_data.py --selftest
"""

import argparse, datetime as dt, hashlib, json, os, sys, time
from collections import defaultdict

POSITIONS = ["QB", "RB", "WR", "TE"]          # offense (lineups/depth-chart scope)
DEF_GROUP = {"LB": "LB", "ILB": "LB", "OLB": "LB", "MLB": "LB",
             "DE": "DL", "DT": "DL", "NT": "DL", "DL": "DL", "EDGE": "DL",
             "CB": "DB", "S": "DB", "FS": "DB", "SS": "DB", "DB": "DB", "SAF": "DB"}
DEF_POS = ["LB", "DL", "DB"]
KEEP_POS = POSITIONS + DEF_POS
FORM_N = 5                      # teams_form window (games)
SNAP_PCT_SCALE_CUTOFF = 1.01    # nflverse offense_pct is 0..1 in some releases

STAT_KEYS = ["passYds","passAtt","passComp","passTds","passInt","sacks",
             "rushYds","rushAtt","rushTds","receptions","targets","recYds",
             "recTds","rushRecYds","totalTds","fanPts",
             "tackles","soloTk","astTk","tfl","defSacks"]

# Team-level keys aggregated per game (offense; "_a" = allowed by defense).
TEAM_KEYS = ["points","plays","passYds","passAtt","passComp","passTds","passInt",
             "sacks","rushYds","rushAtt","rushTds","receptions","targets"]

# NFL home-stadium coordinates for the weather step (lat, lon).
STADIUMS = {
 "ARI":(33.5276,-112.2626),"ATL":(33.7554,-84.4009),"BAL":(39.2780,-76.6227),
 "BUF":(42.7738,-78.7870),"CAR":(35.2258,-80.8528),"CHI":(41.8623,-87.6167),
 "CIN":(39.0954,-84.5160),"CLE":(41.5061,-81.6995),"DAL":(32.7473,-97.0945),
 "DEN":(39.7439,-105.0201),"DET":(42.3400,-83.0456),"GB":(44.5013,-88.0622),
 "HOU":(29.6847,-95.4107),"IND":(39.7601,-86.1639),"JAX":(30.3240,-81.6373),
 "KC":(39.0489,-94.4839),"LA":(33.9535,-118.3392),"LAC":(33.9535,-118.3392),
 "LV":(36.0909,-115.1833),"MIA":(25.9580,-80.2389),"MIN":(44.9735,-93.2575),
 "NE":(42.0909,-71.2643),"NO":(29.9511,-90.0812),"NYG":(40.8135,-74.0745),
 "NYJ":(40.8135,-74.0745),"PHI":(39.9008,-75.1675),"PIT":(40.4468,-80.0158),
 "SEA":(47.5952,-122.3316),"SF":(37.4030,-121.9700),"TB":(27.9759,-82.5033),
 "TEN":(36.1665,-86.7713),"WAS":(38.9078,-76.8645),
}

# ?? generic helpers ?????????????????????????????????????????????????????????
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

def _short(full):
    """'Puka Nacua' -> 'p.nacua' (pbp short-name form, normalised)."""
    parts = str(full or "").strip().split()
    if len(parts) < 2: return _norm(full)
    return _norm(parts[0][0] + "." + " ".join(parts[1:]))

def write_json(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, separators=(",", ":"))
    print(f"  wrote {path} ({os.path.getsize(path)/1024:.0f} KB)")

# ?? loaders (network) ???????????????????????????????????????????????????????
def _safe_seasons_load(fn, seasons, label, **kw):
    """Load a game-derived frame; if the newest season isn't published yet
    (e.g. the current preseason 404s the whole call), drop it and retry."""
    try:
        return fn(seasons=seasons, **kw).to_pandas()
    except Exception as e:
        reduced = [s for s in seasons if s != max(seasons)]
        if reduced and reduced != list(seasons):
            print(f"  ({label}: {max(seasons)} not published yet - retrying {reduced})")
            return fn(seasons=reduced, **kw).to_pandas()
        raise


def load_frames(seasons, pbp_seasons, current):
    import nflreadpy as nfl
    print("loading nflverse frames ...")
    ps  = _safe_seasons_load(nfl.load_player_stats, seasons, "player_stats", summary_level="week")
    sc  = _safe_seasons_load(nfl.load_snap_counts, seasons, "snap_counts")
    sch = nfl.load_schedules(seasons=sorted(set(seasons + [current + 1]))).to_pandas()
    try:
        inj = nfl.load_injuries(seasons=[current]).to_pandas()
    except Exception as e:
        print(f"  (injuries unavailable: {e})"); inj = None
    try:
        dc = nfl.load_depth_charts(seasons=[current]).to_pandas()
    except Exception as e:
        print(f"  (depth charts unavailable: {e})"); dc = None
    try:
        tm = nfl.load_teams().to_pandas()
    except Exception as e:
        print(f"  (teams meta unavailable: {e})"); tm = None
    pbp = None
    if pbp_seasons:
        try:
            pbp = nfl.load_pbp(seasons=pbp_seasons).to_pandas()
        except Exception as e:
            print(f"  (pbp unavailable - red-zone/first-TD skipped: {e})")
    adv = ros = None
    try:
        adv = nfl.load_pfr_advstats(seasons=[current], stat_type="def",
                                    summary_level="week").to_pandas()
        print("  advstats def cols:", list(adv.columns)[:16], "...")
    except Exception as e:
        print(f"  (pfr advstats unavailable - Clamp Watch feed skipped: {e})")
    try:
        ros = nfl.load_rosters(seasons=[current]).to_pandas()
    except Exception as e:
        print(f"  (rosters unavailable: {e})")
    return ps, sc, sch, inj, dc, tm, pbp, adv, ros

# ?? schedules ? game index, results, fixture ????????????????????????????????
def build_game_index(sch):
    """(season, week, team) -> {matchId, home, away, opp, isHome, date}"""
    gid = col(sch, "game_id"); se = col(sch, "season"); wk = col(sch, "week")
    ht = col(sch, "home_team"); at = col(sch, "away_team")
    gd = col(sch, "gameday", "game_date")
    idx = {}
    for _, r in sch.iterrows():
        season, week = int(g(r, se)), int(g(r, wk))
        h, a = str(g(r, ht, "")).upper(), str(g(r, at, "")).upper()
        mid = str(g(r, gid, "")) or f"{season}_{week:02d}_{a}_{h}"
        date = str(g(r, gd, ""))
        idx[(season, week, h)] = {"matchId": mid, "opp": a, "home": 1, "date": date}
        idx[(season, week, a)] = {"matchId": mid, "opp": h, "home": 0, "date": date}
    return idx

def build_results(sch):
    se = col(sch, "season"); wk = col(sch, "week")
    ht = col(sch, "home_team"); at = col(sch, "away_team")
    hs = col(sch, "home_score"); as_ = col(sch, "away_score")
    gd = col(sch, "gameday", "game_date")
    out = []
    for _, r in sch.iterrows():
        h_s, a_s = r.get(hs), r.get(as_)
        if h_s is None or a_s is None or h_s != h_s or a_s != a_s:
            continue
        out.append({"season": int(g(r, se)), "week": int(g(r, wk)),
                    "home": str(g(r, ht, "")).upper(), "away": str(g(r, at, "")).upper(),
                    "hs": int(g(r, hs)), "as": int(g(r, as_)),
                    "date": str(g(r, gd, ""))})
    out.sort(key=lambda x: (x["season"], x["week"]))
    return out

def build_fixture(sch):
    """Next week with unplayed games, from the newest season that has any."""
    se = col(sch, "season"); wk = col(sch, "week")
    ht = col(sch, "home_team"); at = col(sch, "away_team")
    hs = col(sch, "home_score")
    gd = col(sch, "gameday", "game_date"); gt = col(sch, "gametime")
    rf = col(sch, "roof"); st = col(sch, "stadium")
    pend = sch[sch[hs].isna()] if hs else sch.iloc[0:0]
    if pend.empty:
        return [], None
    season = int(pend[se].max())
    pend = pend[pend[se] == season]
    week = int(pend[wk].min())
    pend = pend[pend[wk] == week]
    fx = []
    for _, r in pend.iterrows():
        date = str(g(r, gd, "")); tme = str(g(r, gt, "") or "")
        utc = f"{date}T{tme}:00Z" if (date and tme) else date  # ET stored raw; shell formats
        fx.append({"home": str(g(r, ht, "")).upper(), "away": str(g(r, at, "")).upper(),
                   "week": week, "season": season, "date": date, "time": tme, "utc": utc,
                   "venue": str(g(r, st, "") or ""), "roof": str(g(r, rf, "") or "").lower()})
    return fx, week

# ?? snap index ??????????????????????????????????????????????????????????????
def build_snap_idx(sc):
    if sc is None or getattr(sc, "empty", True):
        return {}
    nm = col(sc, "player", "player_name", "pfr_player_name")
    se = col(sc, "season"); wk = col(sc, "week")
    pct = col(sc, "offense_pct", "off_pct")
    dpct = col(sc, "defense_pct", "def_pct")
    out = {}
    for _, r in sc.iterrows():
        p = g(r, pct); d = g(r, dpct)
        vals = []
        for v in (p, d):
            if v is None: continue
            try: v = float(v)
            except (TypeError, ValueError): continue
            if v <= SNAP_PCT_SCALE_CUTOFF: v *= 100.0
            vals.append(v)
        out[(_norm(g(r, nm, "")), int(g(r, se)), int(g(r, wk)))] = r1(max(vals)) if vals else None
    return out

# ?? PFR advanced defense ? Clamp Watch CB feed (dbs.json) ???????????????????
PFR_TEAM = {"GNB": "GB", "KAN": "KC", "NWE": "NE", "NOR": "NO", "SFO": "SF",
            "TAM": "TB", "LVR": "LV", "SDG": "LAC", "STL": "LA", "OAK": "LV",
            "CRD": "ARI", "RAV": "BAL", "HTX": "HOU", "CLT": "IND", "JAX": "JAX",
            "OTI": "TEN", "RAI": "LV", "RAM": "LA"}
def _nfl_team(t):
    t = str(t or "").upper()
    return PFR_TEAM.get(t, t)

def _passer_rating(cmp_, att, yds, td, ints):
    if not att: return None
    a = max(0.0, min(2.375, (cmp_ / att - 0.3) * 5))
    b = max(0.0, min(2.375, (yds / att - 3) * 0.25))
    c = max(0.0, min(2.375, (td / att) * 20))
    d = max(0.0, min(2.375, 2.375 - (ints / att) * 25))
    return round((a + b + c + d) / 6 * 100, 1)

def _pname(s):
    """PFR<->nflverse name key: lowercase, alpha-only, suffixes stripped."""
    import re as _re
    toks = [t for t in _re.sub(r"[^a-z ]", "", str(s or "").lower()).split()
            if t not in ("jr", "sr", "ii", "iii", "iv", "v")]
    return " ".join(toks)

def build_dbs(adv, ros, current, players=None):
    """Clamp Watch feed: CBs graded on coverage - passer rating allowed when
    targeted, recomputed from weekly component sums. role='top' (elite corner);
    true shadow charting is paywalled, so this flags coverage QUALITY."""
    if adv is None or getattr(adv, "empty", True):
        return []
    se = col(adv, "season"); tmv = col(adv, "team", "tm")
    nm = col(adv, "pfr_player_name", "player", "player_name")
    pid = col(adv, "pfr_player_id", "pfr_id")
    tgt = col(adv, "def_targets", "tgt", "targets")
    cmp_ = col(adv, "def_completions_allowed", "cmp", "completions_allowed")
    yds = col(adv, "def_yards_allowed", "yds", "yards_allowed")
    td = col(adv, "def_receiving_td_allowed", "td", "tds_allowed")
    ints = col(adv, "def_ints", "int", "ints")
    # position lookup chain: roster pfr_id -> roster name -> our own weekly-stats
    # raw positions (players.json posDetail) via cleaned name
    pos_by_id, pos_by_name = {}, {}
    if ros is not None and not getattr(ros, "empty", True):
        rp = col(ros, "position", "depth_chart_position")
        rid = col(ros, "pfr_id", "pfr_player_id")
        rnm = col(ros, "full_name", "player_name", "football_name")
        for _, r in ros.iterrows():
            p = str(g(r, rp, "") or "").upper()
            i = g(r, rid); n = _pname(g(r, rnm, ""))
            if i and str(i) != "nan": pos_by_id[str(i)] = p
            if n: pos_by_name[n] = p
    for pl in (players or []):
        k = _pname(pl.get("name"))
        if k and k not in pos_by_name:
            pos_by_name[k] = str(pl.get("posDetail") or pl.get("position") or "").upper()
    agg = {}
    for _, r in adv.iterrows():
        try:
            if int(g(r, se, 0)) != int(current): continue
        except (TypeError, ValueError): continue
        name = str(g(r, nm, "") or "").strip()
        if not name: continue
        key = str(g(r, pid) or _norm(name))
        A = agg.setdefault(key, {"player": name, "team": None,
                                 "tgt": 0.0, "cmp": 0.0, "yds": 0.0, "td": 0.0, "int": 0.0})
        A["team"] = _nfl_team(g(r, tmv, A["team"]))     # latest team wins
        for fld, ccol in (("tgt", tgt), ("cmp", cmp_), ("yds", yds), ("td", td), ("int", ints)):
            v = g(r, ccol, 0)
            try: A[fld] += float(v or 0)
            except (TypeError, ValueError): pass
    out = []
    max_tgt = max((a["tgt"] for a in agg.values()), default=0)
    min_tgt = 30 if max_tgt >= 60 else max(10, max_tgt * 0.4)    # early-season scaling
    n_cb = n_vol = 0
    unmatched = []
    for key, a in agg.items():
        pos = pos_by_id.get(key) or pos_by_name.get(_pname(a["player"])) or ""
        if not pos and len(unmatched) < 5: unmatched.append(a["player"])
        if pos not in ("CB", "DB"): continue
        n_cb += 1
        if a["tgt"] < min_tgt: continue
        n_vol += 1
        rat = _passer_rating(a["cmp"], a["tgt"], a["yds"], a["td"], a["int"])
        if rat is None: continue
        ypt = round(a["yds"] / a["tgt"], 1)
        out.append({"team": a["team"], "player": a["player"], "role": "top",
                    "grade": rat, "tgt": int(a["tgt"]), "cmpPct": round(a["cmp"] / a["tgt"] * 100, 1),
                    "ypt": ypt})
    n_pool = len(out)
    # clamp corners: elite coverage - low rating allowed, capped at the best 16
    out = [x for x in out if x["grade"] <= 80.0 and x["ypt"] <= 7.5]
    out.sort(key=lambda x: x["grade"])
    print(f"  dbs sieve: {len(agg)} defenders -> {n_cb} CBs -> {n_vol} vol-qualified "
          f"-> {n_pool} rated -> {min(len(out),16)} clamp corners (min_tgt {min_tgt:.0f})")
    if unmatched:
        print(f"  dbs unmatched-position sample: {unmatched}")
    return out[:16]

# ?? pbp ? Tuddy Targets red-zone splits (season totals, zone splits, team share %) ??
def build_redzone(pbp, short_idx, current):
    """Season totals for the Tuddy Targets boards. Zones: rz (<=20), i10 (<=10),
    i5 (<=5, rushing only). Shares are the player's slice of his TEAM's zone volume."""
    if pbp is None or getattr(pbp, "empty", True):
        return []
    se = col(pbp, "season"); yl = col(pbp, "yardline_100"); pt = col(pbp, "play_type")
    rusher = col(pbp, "rusher_player_name", "rusher")
    recv = col(pbp, "receiver_player_name", "receiver")
    posteam = col(pbp, "posteam")
    comp = col(pbp, "complete_pass"); ptd = col(pbp, "pass_touchdown"); rtd = col(pbp, "rush_touchdown")
    P = {}          # full name -> zone counters
    T = {}          # team -> zone volume {rzTgt, i10Tgt, rzAtt, i10Att, i5Att}
    def pl(full, team):
        return P.setdefault(full, {"player": full, "team": team,
            "rzTgt": 0, "rzRec": 0, "rzRecTd": 0, "i10Tgt": 0, "i10Rec": 0, "i10RecTd": 0,
            "rzAtt": 0, "rzRushTd": 0, "i10Att": 0, "i10RushTd": 0, "i5Att": 0, "i5RushTd": 0})
    def tv(team):
        return T.setdefault(team, {"rzTgt": 0, "i10Tgt": 0, "rzAtt": 0, "i10Att": 0, "i5Att": 0})
    unresolved = set()
    for _, r in pbp.iterrows():
        try: season = int(g(r, se, 0))
        except (TypeError, ValueError): continue
        if season != int(current): continue
        try: y = float(g(r, yl))
        except (TypeError, ValueError): continue
        if not (y == y) or y > 20: continue
        ptv = str(g(r, pt, "") or "").lower()
        team = str(g(r, posteam, "") or "").upper()
        if not team or ptv not in ("run", "pass"): continue
        tt = tv(team)
        def onev(cname):
            v = g(r, cname, 0)
            try: return int(float(v or 0)) == 1
            except (TypeError, ValueError): return False
        if ptv == "pass":
            sn = _norm(g(r, recv, ""))
            if not sn: continue
            full = short_idx.get((team, sn))
            if full is None:
                unresolved.add((team, sn)); continue
            p = pl(full, team)
            caught = onev(comp); scored = onev(ptd)
            tt["rzTgt"] += 1
            p["rzTgt"] += 1; p["rzRec"] += 1 if caught else 0; p["rzRecTd"] += 1 if scored else 0
            if y <= 10:
                tt["i10Tgt"] += 1
                p["i10Tgt"] += 1; p["i10Rec"] += 1 if caught else 0; p["i10RecTd"] += 1 if scored else 0
        else:
            sn = _norm(g(r, rusher, ""))
            if not sn: continue
            full = short_idx.get((team, sn))
            if full is None:
                unresolved.add((team, sn)); continue
            p = pl(full, team)
            scored = onev(rtd)
            tt["rzAtt"] += 1
            p["rzAtt"] += 1; p["rzRushTd"] += 1 if scored else 0
            if y <= 10:
                tt["i10Att"] += 1
                p["i10Att"] += 1; p["i10RushTd"] += 1 if scored else 0
            if y <= 5:
                tt["i5Att"] += 1
                p["i5Att"] += 1; p["i5RushTd"] += 1 if scored else 0
    if unresolved:
        print(f"  (redzone: {len(unresolved)} short names unresolved - skipped)")
    out = []
    for p in P.values():
        t = T.get(p["team"], {})
        pct = lambda a, b: round(a / b * 100, 1) if b else 0.0
        p["rzTgtPct"] = pct(p["rzTgt"], t.get("rzTgt", 0))
        p["i10TgtPct"] = pct(p["i10Tgt"], t.get("i10Tgt", 0))
        p["rzRushPct"] = pct(p["rzAtt"], t.get("rzAtt", 0))
        p["i10RushPct"] = pct(p["i10Att"], t.get("i10Att", 0))
        p["i5RushPct"] = pct(p["i5Att"], t.get("i5Att", 0))
        out.append(p)
    out.sort(key=lambda x: -(x["rzTgt"] + x["rzAtt"]))
    return out

# ?? pbp ? red-zone / goal-line usage + first-TD log ?????????????????????????
def build_pbp_derived(pbp, short_idx):
    """pbp names are short ('P.Nacua'); resolve to full names via short_idx
    {(TEAM, short_norm): full_name}. Returns (usage: fullname->rates, firsttd,
    longest: (matchId, TEAM, fullname) -> {longRec, longRush, longComp})."""
    usage = defaultdict(lambda: {"rzTgt": 0, "rzCarry": 0, "glCarry": 0,
                                 "games": set()})
    firsttd = []
    longest = {}
    unresolved = set()
    if pbp is None or getattr(pbp, "empty", True):
        return {}, firsttd, longest
    se = col(pbp, "season"); wk = col(pbp, "week"); gid = col(pbp, "game_id")
    yl = col(pbp, "yardline_100"); pt = col(pbp, "play_type")
    rusher = col(pbp, "rusher_player_name", "rusher")
    recv = col(pbp, "receiver_player_name", "receiver")
    tdp = col(pbp, "td_player_name"); tdt = col(pbp, "td_team")
    istd = col(pbp, "touchdown"); qtr = col(pbp, "qtr")
    posteam = col(pbp, "posteam")
    ygain = col(pbp, "yards_gained"); comp = col(pbp, "complete_pass")
    passer = col(pbp, "passer_player_name", "passer")
    def _bump(gidv, team, full, key, yards):
        d = longest.setdefault((gidv, team, full),
                               {"longRec": None, "longRush": None, "longComp": None,
                                "expRec": 0, "expRush": 0})
        if d[key] is None or yards > d[key]:
            d[key] = yards
        # explosive plays: 20+ yd catch / 10+ yd rush (standard NFL definitions)
        if key == "longRec" and yards >= 20: d["expRec"] += 1
        if key == "longRush" and yards >= 10: d["expRush"] += 1
    seen_first = {}          # game_id -> set(teams w/ first td logged) + '_game'
    for _, r in pbp.iterrows():
        gidv = str(g(r, gid, ""))
        season = int(g(r, se, 0)); week = int(g(r, wk, 0))
        y100 = g(r, yl, None); ptv = str(g(r, pt, "") or "")
        # usage
        team_now = str(g(r, posteam, "") or "").upper()
        def _resolve(short_raw):
            sn = _norm(short_raw)
            if not sn: return None
            full = short_idx.get((team_now, sn))
            if full is None:
                unresolved.add((team_now, sn))
            return full
        # longest reception / rush / completion per game (any field position)
        try: yg = float(g(r, ygain))
        except (TypeError, ValueError): yg = None
        if yg is not None and yg == yg:
            if ptv == "run":
                nm = _resolve(g(r, rusher, ""))
                if nm: _bump(gidv, team_now, nm, "longRush", yg)
            elif ptv == "pass":
                cv = g(r, comp, 0)
                try: caught = int(float(cv or 0)) == 1
                except (TypeError, ValueError): caught = False
                if caught:
                    nm = _resolve(g(r, recv, ""))
                    if nm: _bump(gidv, team_now, nm, "longRec", yg)
                    qb = _resolve(g(r, passer, ""))
                    if qb: _bump(gidv, team_now, qb, "longComp", yg)
        if isinstance(y100, float) and y100 <= 20:
            if ptv == "run":
                nm = _resolve(g(r, rusher, ""))
                if nm:
                    usage[nm]["rzCarry"] += 1; usage[nm]["games"].add(gidv)
                    if y100 <= 5: usage[nm]["glCarry"] += 1
            elif ptv == "pass":
                nm = _resolve(g(r, recv, ""))
                if nm:
                    usage[nm]["rzTgt"] += 1; usage[nm]["games"].add(gidv)
        # first TD log
        if g(r, istd, 0) == 1:
            team = str(g(r, tdt, "") or g(r, posteam, "")).upper()
            player = str(g(r, tdp, "") or "").strip()
            if not (team and player):
                continue
            sf = seen_first.setdefault(gidv, set())
            game_first = "_game" not in sf
            if team in sf:
                continue
            sf.add(team); sf.add("_game")
            full = short_idx.get((team, _norm(player)), player)
            firsttd.append({"season": season, "week": week, "matchId": gidv,
                            "team": team, "player": full,
                            "qtr": int(g(r, qtr, 0)), "gameFirst": game_first})
    if unresolved:
        print(f"  (pbp: {len(unresolved)} short names unresolved - usage skipped for them)")
    # collapse usage to per-game rates
    out = {}
    for nm, u in usage.items():
        n = max(len(u["games"]), 1)
        out[nm] = {"rzTgt": r3(u["rzTgt"] / n), "rzCarry": r3(u["rzCarry"] / n),
                   "glCarry": r3(u["glCarry"] / n)}
    return out, firsttd, longest

# ?? players + gamelogs ??????????????????????????????????????????????????????
def build_players_gamelogs(ps, snap_idx, game_idx, current):
    name_c = col(ps, "player_display_name", "player_name")
    pos_c = col(ps, "position", "position_group")
    team_c = col(ps, "team", "recent_team")
    opp_c = col(ps, "opponent_team", "opponent")
    se_c = col(ps, "season"); wk_c = col(ps, "week")
    st_c = col(ps, "season_type")
    C = {
        "passYds":  col(ps, "passing_yards"),
        "passAtt":  col(ps, "attempts", "passing_attempts"),
        "passComp": col(ps, "completions", "passing_completions"),
        "passTds":  col(ps, "passing_tds"),
        "passInt":  col(ps, "interceptions", "passing_interceptions"),
        "sacks":    col(ps, "sacks", "sacks_suffered"),
        "rushYds":  col(ps, "rushing_yards"),
        "rushAtt":  col(ps, "carries", "rushing_attempts"),
        "rushTds":  col(ps, "rushing_tds"),
        "receptions": col(ps, "receptions"),
        "targets":  col(ps, "targets"),
        "recYds":   col(ps, "receiving_yards"),
        "recTds":   col(ps, "receiving_tds"),
        "fanPts":   col(ps, "fantasy_points_ppr", "fantasy_points"),
        "soloTk":   col(ps, "def_tackles_solo", "def_tackles"),
        "astTk":    col(ps, "def_tackle_assists", "def_tackles_with_assist"),
        "tfl":      col(ps, "def_tackles_for_loss"),
        "defSacks": col(ps, "def_sacks"),
    }
    tgt_share_c = col(ps, "target_share")
    airyds_c = col(ps, "receiving_air_yards")

    gamelogs = []
    agg = {}
    short_idx = {}          # (TEAM, short_norm) -> full name; None = ambiguous
    for _, r in ps.iterrows():
        pos = str(g(r, pos_c, "")).strip().upper()
        pos = pos if pos in POSITIONS else DEF_GROUP.get(pos)
        if pos not in KEEP_POS:
            continue
        if str(g(r, st_c, "REG")).upper() != "REG":
            continue
        name = str(g(r, name_c, "")).strip()
        if not name:
            continue
        season, week = int(g(r, se_c)), int(g(r, wk_c))
        team = str(g(r, team_c, "")).upper()
        opp = str(g(r, opp_c, "")).upper()
        gi = game_idx.get((season, week, team), {})
        row = {"Year": str(season), "Week": week,
               "MatchId": gi.get("matchId", f"{season}_{week:02d}_{team}"),
               "Player": name, "Team": team,
               "Opp": gi.get("opp", opp), "home": gi.get("home", 0)}
        for k, c in C.items():
            row[k] = r1(g(r, c))
        row["rushRecYds"] = r1(row["rushYds"] + row["recYds"])
        # tackles + assists (the books' combined-tackles market)
        row["tackles"] = r1(row["soloTk"] + row["astTk"])
        if pos in POSITIONS:                      # offense: def keys stay 0, no false signal
            row["soloTk"] = row["astTk"] = row["tfl"] = row["defSacks"] = row["tackles"] = 0.0
        # TD convention matches the books' "anytime TD": rushing + receiving only
        row["totalTds"] = r1(row["rushTds"] + row["recTds"])
        row["anytimeTd"] = 1 if (row["rushTds"] + row["recTds"]) > 0 else 0
        snap = snap_idx.get((_norm(name), season, week))
        row["snapPct"] = snap if snap is not None else 0.0
        ts = g(r, tgt_share_c, 0.0)
        row["tgtShare"] = r3(ts * 100 if ts <= 1.0 else ts)
        row["airYds"] = r1(g(r, airyds_c))
        gamelogs.append(row)

        P = agg.setdefault(_norm(name), {"name": name, "position": pos,
                                         "team": team, "rows": []})
        P["team"] = team; P["position"] = pos
        P["rawPos"] = str(g(r, pos_c, "")).strip().upper()
        P["rows"].append(row)
        sk = (team, _short(name))
        prev = short_idx.get(sk, "__unset__")
        if prev == "__unset__": short_idx[sk] = name
        elif prev != name: short_idx[sk] = None   # ambiguous on this team

    # headline averages (current season; fall back one season, then all)
    players = []
    for P in agg.values():
        rows = P["rows"]
        cur = [x for x in rows if x["Year"] == str(current)]
        src = cur or [x for x in rows if x["Year"] == str(current - 1)] or rows
        n = len(src)
        p = {"name": P["name"], "team": P["team"], "position": P["position"],
             "posDetail": P.get("rawPos", P["position"]), "matches": n}
        for k in STAT_KEYS:
            p[k] = r1(sum(x[k] for x in src) / n)
        p["snapPct"] = r1(sum(x["snapPct"] for x in src) / n)
        p["tgtShare"] = r3(sum(x["tgtShare"] for x in src) / n)
        p["ypr"] = r1(p["recYds"] / p["receptions"]) if p["receptions"] else 0.0
        p["ypc"] = r1(p["rushYds"] / p["rushAtt"]) if p["rushAtt"] else 0.0
        tg = sum(x["targets"] for x in src)
        p["aDot"] = r1(sum(x["airYds"] for x in src) / tg) if tg else 0.0
        p["rzTgt"] = 0.0; p["rzCarry"] = 0.0; p["glCarry"] = 0.0
        players.append(p)
    players.sort(key=lambda x: -x["fanPts"])
    short_idx = {k: v for k, v in short_idx.items() if v}
    return players, gamelogs, short_idx

# ?? team aggregation (offense + allowed) ????????????????????????????????????
def _team_games(gamelogs, results, current):
    """(team, matchId) -> summed offense row; plus opp + points from results."""
    tg = defaultdict(lambda: defaultdict(float))
    meta = {}
    for r in gamelogs:
        if r["Year"] != str(current):
            continue
        key = (r["Team"], r["MatchId"])
        for k in ["passYds","passAtt","passComp","passTds","passInt","sacks",
                  "rushYds","rushAtt","rushTds","receptions","targets"]:
            tg[key][k] += r[k]
        meta[key] = {"opp": r["Opp"], "week": r["Week"]}
    # points from results
    pts = {}
    for x in results:
        if x["season"] != current:
            continue
        pts[(x["home"], x["season"], x["week"])] = x["hs"]
        pts[(x["away"], x["season"], x["week"])] = x["as"]
    for key, m in meta.items():
        team, _ = key
        tg[key]["points"] = float(pts.get((team, current, m["week"]), 0))
        tg[key]["plays"] = tg[key]["passAtt"] + tg[key]["rushAtt"] + tg[key]["sacks"]
    return tg, meta

def build_teams(gamelogs, results, current, divisions, form_n=None):
    tg, meta = _team_games(gamelogs, results, current)
    by_team = defaultdict(list)   # team -> [(week, offense_row, opp)]
    for (team, mid), row in tg.items():
        m = meta[(team, mid)]
        by_team[team].append((m["week"], row, m["opp"], mid))
    out = []
    for team, games in sorted(by_team.items()):
        games.sort(key=lambda x: x[0])
        if form_n:
            games = games[-form_n:]
        n = len(games)
        rec = {"team": team, "matches": n}
        d = divisions.get(team, {})
        rec["division"] = d.get("division", ""); rec["conference"] = d.get("conference", "")
        for k in TEAM_KEYS:
            rec[k] = r1(sum(x[1][k] for x in games) / n)
        # allowed = what opponents produced in the same games
        for k in TEAM_KEYS:
            vals = []
            for wk_, _row, opp, mid in games:
                orow = tg.get((opp, mid))
                if orow: vals.append(orow[k])
            rec[k + "_a"] = r1(sum(vals) / len(vals)) if vals else 0.0
        out.append(rec)
    return out

def build_dvp(gamelogs, players, current):
    """defense team ? pos ? per-game allowed (position totals / games faced)."""
    pos_by_name = {p["name"]: p["position"] for p in players}
    sums = defaultdict(lambda: defaultdict(float))   # (def, pos) -> stat totals
    games = defaultdict(set)                         # (def, pos) -> matchIds
    for r in gamelogs:
        if r["Year"] != str(current):
            continue
        pos = pos_by_name.get(r["Player"])
        if pos not in KEEP_POS:
            continue
        key = (r["Opp"], pos)
        for k in STAT_KEYS:
            sums[key][k] += r[k]
        sums[key]["anytimeTd"] += r["anytimeTd"]
        games[key].add(r["MatchId"])
    # longest-play allowed: max per game per (def, pos), averaged over games w/ data.
    # explosive-plays allowed: SUM per game per (def, pos), averaged the same way.
    LONG_KEYS = ("longRec", "longRush", "longComp")
    EXP_KEYS = ("expRec", "expRush")
    lmax = defaultdict(dict)                         # (def, pos, matchId) -> {k: max|sum}
    for r in gamelogs:
        if r["Year"] != str(current):
            continue
        pos = pos_by_name.get(r["Player"])
        if pos not in POSITIONS:
            continue
        cell = lmax[(r["Opp"], pos, r["MatchId"])]
        for k in LONG_KEYS:
            v = r.get(k)
            if v is not None and (k not in cell or v > cell[k]):
                cell[k] = v
        for k in EXP_KEYS:
            v = r.get(k)
            if v is not None:
                cell[k] = cell.get(k, 0) + v
    lagg = defaultdict(lambda: {k: [] for k in LONG_KEYS + EXP_KEYS})
    for (team, pos, _mid), cell in lmax.items():
        for k, v in cell.items():
            lagg[(team, pos)][k].append(v)
    out = []
    for (team, pos), s in sorted(sums.items()):
        n = max(len(games[(team, pos)]), 1)
        rec = {"team": team, "pos": pos}
        for k in STAT_KEYS + ["anytimeTd"]:
            rec[k] = r3(s[k] / n)
        la = lagg.get((team, pos))
        for k in LONG_KEYS + EXP_KEYS:
            vals = la[k] if la else []
            if vals:
                rec[k] = r3(sum(vals) / len(vals))
        out.append(rec)
    return out

# ?? injuries / depth charts / teams meta ????????????????????????????????????
def build_injuries(inj, current):
    if inj is None or getattr(inj, "empty", True):
        return []
    se = col(inj, "season"); wk = col(inj, "week")
    nm = col(inj, "full_name", "player_name", "player_display_name")
    tm = col(inj, "team"); pos = col(inj, "position")
    st = col(inj, "report_status", "game_status", "status")
    pr = col(inj, "report_primary_injury", "primary_injury", "injury")
    sub = inj[inj[se] == current] if se else inj
    if getattr(sub, "empty", True):
        return []
    latest = int(sub[wk].max()) if wk else None
    if latest is not None:
        sub = sub[sub[wk] == latest]
    out, seen = [], set()
    for _, r in sub.iterrows():
        name = str(g(r, nm, "")).strip(); status = str(g(r, st, "")).strip()
        if not name or not status or _norm(name) in seen:
            continue
        seen.add(_norm(name))
        out.append({"Team": str(g(r, tm, "")).upper(), "Player": name,
                    "Position": str(g(r, pos, "")).upper(),
                    "Injury": str(g(r, pr, "")).strip(), "Status": status})
    return out

def build_lineups(dc, week):
    if dc is None or getattr(dc, "empty", True):
        return []
    print("  depth-chart cols:", list(dc.columns))
    tm = col(dc, "club_code", "team", "team_abbr")
    nm = col(dc, "full_name", "player_name", "football_name", "last_name")
    pos = col(dc, "position", "depth_position", "pos_abb", "pos_abbr")
    depth = col(dc, "depth_team", "depth_chart_order", "pos_rank", "depth_rank",
                "rank", "depth", "order")
    wk = col(dc, "week"); se = col(dc, "season")
    sub = dc
    if wk and not dc[wk].isna().all():
        latest = int(dc[wk].max()); sub = dc[dc[wk] == latest]
    out, seen = [], set()
    for _, r in sub.iterrows():
        p = str(g(r, pos, "")).upper()
        if p not in POSITIONS:
            continue
        name = str(g(r, nm, "")).strip()
        team = str(g(r, tm, "")).upper()
        rawd = g(r, depth, None)
        try: dep = int(float(rawd))
        except (TypeError, ValueError):
            m = __import__("re").search(r"\d+", str(rawd or ""))
            dep = int(m.group(0)) if m else 9
        key = (team, _norm(name))
        if not name or key in seen:
            continue
        seen.add(key)
        out.append({"week": week, "team": team, "player": name, "position": p,
                    "depth": dep, "status": "STARTER" if dep == 1 else "DEPTH"})
    out.sort(key=lambda x: (x["team"], x["position"], x["depth"]))
    if out and all(x["depth"] == 9 for x in out):
        print("  WARNING: no depth values resolved - check depth-chart cols above")
    return out

def build_divisions(tm):
    if tm is None or getattr(tm, "empty", True):
        return {}
    ab = col(tm, "team_abbr", "team", "abbr")
    dv = col(tm, "team_division", "division")
    cf = col(tm, "team_conf", "conference", "team_conference")
    out = {}
    for _, r in tm.iterrows():
        out[str(g(r, ab, "")).upper()] = {"division": str(g(r, dv, "") or ""),
                                          "conference": str(g(r, cf, "") or "")}
    return out

# ?? weather (Open-Meteo; outdoor fixtures only) ?????????????????????????????
def build_weather(fixture):
    import requests
    WMO = {0:"Clear",1:"Mostly clear",2:"Partly cloudy",3:"Overcast",
           45:"Fog",51:"Drizzle",61:"Rain",63:"Rain",65:"Heavy rain",
           71:"Snow",73:"Snow",75:"Heavy snow",80:"Showers",95:"Storm"}
    out = []
    for f in fixture:
        roof = f.get("roof", "")
        base = {"home": f["home"], "away": f["away"], "venue": f.get("venue", ""),
                "roof": roof}
        if roof in ("dome", "closed"):
            out.append({**base, "temp": None, "rainProb": 0, "wind": 0,
                        "code": -1, "desc": "Indoors"})
            continue
        ll = STADIUMS.get(f["home"])
        if not ll or not f.get("date"):
            out.append({**base, "temp": None, "rainProb": None, "wind": None,
                        "code": None, "desc": ""})
            continue
        try:
            rsp = requests.get("https://api.open-meteo.com/v1/forecast", params={
                "latitude": ll[0], "longitude": ll[1],
                "daily": "temperature_2m_max,precipitation_probability_max,wind_speed_10m_max,weather_code",
                "start_date": f["date"], "end_date": f["date"], "timezone": "auto"},
                timeout=15).json()
            d = rsp.get("daily", {})
            code = (d.get("weather_code") or [None])[0]
            out.append({**base,
                        "temp": (d.get("temperature_2m_max") or [None])[0],
                        "rainProb": (d.get("precipitation_probability_max") or [None])[0],
                        "wind": (d.get("wind_speed_10m_max") or [None])[0],
                        "code": code, "desc": WMO.get(code, "")})
        except Exception as e:
            print(f"  (weather fetch failed for {f['home']}: {e})")
            out.append({**base, "temp": None, "rainProb": None, "wind": None,
                        "code": None, "desc": ""})
    return out

# ?? main build ??????????????????????????????????????????????????????????????
def run_build(frames, out_dir, seasons, current, password, skip_weather=False):
    if len(frames) == 7:                      # selftest / older callers: no advstats
        frames = tuple(frames) + (None, None)
    ps, sc, sch, inj, dc, tm, pbp, adv, ros = frames
    os.makedirs(out_dir, exist_ok=True)
    game_idx = build_game_index(sch)
    results = build_results(sch)
    fixture, next_week = build_fixture(sch)
    snap_idx = build_snap_idx(sc)
    players, gamelogs, short_idx = build_players_gamelogs(ps, snap_idx, game_idx, current)
    rz_usage, firsttd, longest = build_pbp_derived(pbp, short_idx)
    redzone = build_redzone(pbp, short_idx, current)
    dbs = build_dbs(adv, ros, current, players)
    for p in players:
        u = rz_usage.get(p["name"])
        if u:
            p["rzTgt"] = u["rzTgt"]; p["rzCarry"] = u["rzCarry"]; p["glCarry"] = u["glCarry"]
    # longest reception / rush / completion: per-game values onto gamelogs (None when
    # pbp doesn't cover that season), season averages onto players
    LONG_KEYS = ("longRec", "longRush", "longComp", "expRec", "expRush")
    lacc = defaultdict(lambda: {k: [] for k in LONG_KEYS})
    for row in gamelogs:
        d = longest.get((row["MatchId"], row["Team"], row["Player"]))
        for k in LONG_KEYS:
            v = d.get(k) if d else None
            row[k] = r3(v) if v is not None else None
            if row["Year"] == str(current) and v is not None:
                lacc[row["Player"]][k].append(v)
    for p in players:
        a = lacc.get(p["name"])
        for k in LONG_KEYS:
            vals = a[k] if a else []
            p[k] = r3(sum(vals) / len(vals)) if vals else 0.0
    divisions = build_divisions(tm)
    teams = build_teams(gamelogs, results, current, divisions)
    teams_form = build_teams(gamelogs, results, current, divisions, form_n=FORM_N)
    dvp = build_dvp(gamelogs, players, current)
    injuries = build_injuries(inj, current)
    lineups = build_lineups(dc, next_week)
    weather = [] if skip_weather else build_weather(fixture)

    # completed-season summary week if fixture empty (off-season)
    week = next_week
    if week is None:
        cur_res = [x for x in results if x["season"] == current]
        week = max((x["week"] for x in cur_res), default=0)

    meta = {"version": str(int(time.time())),
            "created": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "week": week, "seasons": [str(s) for s in seasons],
            "currentSeason": str(current),
            "summary": {"players": len(players), "teams": len(teams),
                        "dvp": len(dvp), "gamelogs": len(gamelogs),
                        "fixtures": len(fixture), "results": len(results),
                        "firsttd": len(firsttd), "redzone": len(redzone), "dbs": len(dbs)},
            "derivedNote": "players/teams/dvp derived from nflverse weekly stats; "
                           "red-zone usage, first-TD, longest rec/rush/comp from pbp; snapPct from snap counts."}
    if password:
        meta["password_hash"] = hashlib.sha256(password.encode()).hexdigest()

    write_json(f"{out_dir}/meta.json", meta)
    write_json(f"{out_dir}/players.json", players)
    write_json(f"{out_dir}/teams.json", teams)
    write_json(f"{out_dir}/teams_form.json", teams_form)
    write_json(f"{out_dir}/dvp.json", dvp)
    write_json(f"{out_dir}/gamelogs.json", gamelogs)
    write_json(f"{out_dir}/fixture.json", fixture)
    write_json(f"{out_dir}/results.json", results)
    write_json(f"{out_dir}/injury.json", injuries)
    write_json(f"{out_dir}/lineups.json", lineups)
    write_json(f"{out_dir}/weather.json", weather)
    write_json(f"{out_dir}/firsttd.json", firsttd)
    write_json(f"{out_dir}/redzone.json", redzone)
    write_json(f"{out_dir}/dbs.json", dbs)
    odds_path = f"{out_dir}/odds.json"
    if not os.path.exists(odds_path):
        write_json(odds_path, {"_sample": True, "updated": meta["created"],
                               "source": "stub (worker owns this file)",
                               "lines": [], "alt": [], "books": [], "matchOdds": []})
    print("done.")
    return meta

# ?? selftest: synthetic nflverse-shaped frames, full pipeline + assertions ??
def selftest():
    import pandas as pd
    print("SELFTEST - synthetic frames through the full pipeline")
    teams = ["KC", "BUF"]
    rows, snaps = [], []
    sched = []
    # two seasons, 3 weeks each, KC v BUF every game (divisional? no-AFC West vs East,
    # synthetic divisions set below make them same-division to test that path)
    for season in (2024, 2025):
        for week in (1, 2, 3):
            gid = f"{season}_{week:02d}_BUF_KC"
            sched.append({"game_id": gid, "season": season, "week": week,
                          "home_team": "KC", "away_team": "BUF",
                          "home_score": 27, "away_score": 24,
                          "gameday": f"{season}-09-{6+week:02d}", "gametime": "20:20",
                          "roof": "outdoors" if week != 2 else "dome",
                          "stadium": "GEHA Field"})
            for team, opp in (("KC", "BUF"), ("BUF", "KC")):
                qb = {"player_display_name": f"{team} QB1", "position": "QB",
                      "team": team, "opponent_team": opp, "season": season,
                      "week": week, "season_type": "REG",
                      "passing_yards": 280 + week, "attempts": 34, "completions": 24,
                      "passing_tds": 2, "interceptions": 1, "sacks": 2,
                      "carries": 4, "rushing_yards": 18, "rushing_tds": 0,
                      "receptions": 0, "targets": 0, "receiving_yards": 0,
                      "receiving_tds": 0, "fantasy_points_ppr": 22.5,
                      "target_share": 0.0, "receiving_air_yards": 0}
                wr = {**qb, "player_display_name": f"{team} WR1", "position": "WR",
                      "passing_yards": 0, "attempts": 0, "completions": 0,
                      "passing_tds": 0, "interceptions": 0, "sacks": 0,
                      "carries": 1, "rushing_yards": 6, "rushing_tds": 0,
                      "receptions": 6, "targets": 9, "receiving_yards": 88 + week,
                      "receiving_tds": 1, "fantasy_points_ppr": 21.0,
                      "target_share": 0.26, "receiving_air_yards": 102}
                rb = {**qb, "player_display_name": f"{team} RB1", "position": "RB",
                      "passing_yards": 0, "attempts": 0, "completions": 0,
                      "passing_tds": 0, "interceptions": 0, "sacks": 0,
                      "carries": 18, "rushing_yards": 84, "rushing_tds": 1,
                      "receptions": 3, "targets": 4, "receiving_yards": 22,
                      "receiving_tds": 0, "fantasy_points_ppr": 19.1,
                      "target_share": 0.11, "receiving_air_yards": -2}
                lb = {**qb, "player_display_name": f"{team} LB1", "position": "ILB",
                      "passing_yards": 0, "attempts": 0, "completions": 0,
                      "passing_tds": 0, "interceptions": 0, "sacks": 0,
                      "carries": 0, "rushing_yards": 0, "rushing_tds": 0,
                      "receptions": 0, "targets": 0, "receiving_yards": 0,
                      "receiving_tds": 0, "fantasy_points_ppr": 0.0,
                      "target_share": 0.0, "receiving_air_yards": 0,
                      "def_tackles_solo": 6 + week, "def_tackle_assists": 3,
                      "def_tackles_for_loss": 1, "def_sacks": 0.5}
                rows += [qb, wr, rb, lb]
                for nm in ("QB1", "WR1", "RB1"):
                    snaps.append({"player": f"{team} {nm}", "season": season,
                                  "week": week, "offense_pct": 0.87})
                snaps.append({"player": f"{team} LB1", "season": season,
                              "week": week, "offense_pct": 0.0, "defense_pct": 0.94})
    # one future unplayed game for fixture
    sched.append({"game_id": "2025_04_BUF_KC", "season": 2025, "week": 4,
                  "home_team": "KC", "away_team": "BUF", "home_score": None,
                  "away_score": None, "gameday": "2025-09-28", "gametime": "20:20",
                  "roof": "outdoors", "stadium": "GEHA Field"})
    ps = pd.DataFrame(rows); sc = pd.DataFrame(snaps); sch = pd.DataFrame(sched)
    inj = pd.DataFrame([{"season": 2025, "week": 3, "full_name": "KC WR1",
                         "team": "KC", "position": "WR",
                         "report_status": "Questionable",
                         "report_primary_injury": "Hamstring"}])
    dc = pd.DataFrame([{"club_code": t, "full_name": f"{t} {p}1", "position": p,
                        "depth_team": 1, "week": 3, "season": 2025}
                       for t in teams for p in POSITIONS[:3]])
    tm = pd.DataFrame([{"team_abbr": "KC", "team_division": "AFC West", "team_conf": "AFC"},
                       {"team_abbr": "BUF", "team_division": "AFC West", "team_conf": "AFC"}])
    pbp = pd.DataFrame([
        {"season": 2025, "week": 1, "game_id": "2025_01_BUF_KC", "yardline_100": 4.0,
         "play_type": "run", "rusher_player_name": "K.RB1", "receiver_player_name": None,
         "td_player_name": "K.RB1", "td_team": "KC", "touchdown": 1, "qtr": 1,
         "posteam": "KC", "complete_pass": 0, "pass_touchdown": 0, "rush_touchdown": 1,
         "yards_gained": 4.0, "passer_player_name": None},
        {"season": 2025, "week": 1, "game_id": "2025_01_BUF_KC", "yardline_100": 12.0,
         "play_type": "pass", "rusher_player_name": None, "receiver_player_name": "B.WR1",
         "td_player_name": "B.WR1", "td_team": "BUF", "touchdown": 1, "qtr": 2,
         "posteam": "BUF", "complete_pass": 1, "pass_touchdown": 1, "rush_touchdown": 0,
         "yards_gained": 12.0, "passer_player_name": "B.QB1"},
        {"season": 2025, "week": 1, "game_id": "2025_01_BUF_KC", "yardline_100": 60.0,
         "play_type": "pass", "rusher_player_name": None, "receiver_player_name": "K.WR1",
         "td_player_name": None, "td_team": None, "touchdown": 0, "qtr": 3,
         "posteam": "KC", "complete_pass": 1, "pass_touchdown": 0, "rush_touchdown": 0,
         "yards_gained": 24.0, "passer_player_name": "K.QB1"},
    ])
    out = "/tmp/nfl_selftest"
    meta = run_build((ps, sc, sch, inj, dc, tm, pbp), out, [2024, 2025], 2025,
                     password="testpw", skip_weather=True)
    # ?? assertions ??????????????????????????????????????????????????????????
    J = lambda f: json.load(open(f"{out}/{f}"))
    players, gl, dvp, teams_j, tf, fx, res, lu, itd, ij = (
        J("players.json"), J("gamelogs.json"), J("dvp.json"), J("teams.json"),
        J("teams_form.json"), J("fixture.json"), J("results.json"),
        J("lineups.json"), J("firsttd.json"), J("injury.json"))
    assert len(players) == 8 and all(p["matches"] == 3 for p in players)
    lbp = next(p for p in players if p["name"] == "KC LB1")
    assert lbp["position"] == "LB"                                   # ILB normalised to group
    assert lbp["tackles"] == r1((10 + 11 + 12) / 3)                  # solo(7,8,9)+ast(3) combined
    assert lbp["soloTk"] == 8.0 and lbp["astTk"] == 3.0 and lbp["defSacks"] == 0.5
    assert lbp["snapPct"] == 94.0                                    # defense_pct picked up
    qbp = next(p for p in players if p["name"] == "KC QB1")
    assert qbp["tackles"] == 0.0                                     # offense rows zeroed
    wr = next(p for p in players if p["name"] == "KC WR1")
    assert wr["recYds"] == r1((89 + 90 + 91) / 3) and wr["rushRecYds"] == r1(wr["recYds"] + wr["rushYds"])
    assert wr["snapPct"] == 87.0 and wr["tgtShare"] == 26.0
    assert wr["aDot"] == r1(102 * 3 / 27)                    # air yards / targets
    rb = next(p for p in players if p["name"] == "KC RB1")
    assert rb["glCarry"] == 1.0 and rb["rzCarry"] == 1.0     # 1 game of pbp
    assert len(gl) == 48 and gl[0]["MatchId"].startswith("202")
    assert all(x["Opp"] in teams and x["home"] in (0, 1) for x in gl)
    # dvp: BUF defense v WR must equal KC WR1's per-game line (only WR they face)
    dvp_buf_wr = next(d for d in dvp if d["team"] == "BUF" and d["pos"] == "WR")
    assert dvp_buf_wr["recYds"] == r3((89 + 90 + 91) / 3)
    assert dvp_buf_wr["anytimeTd"] == 1.0
    # tackle funnel: KC's offence concedes BUF LB1's combined line per game
    dvp_kc_lb = next(d for d in dvp if d["team"] == "KC" and d["pos"] == "LB")
    assert dvp_kc_lb["tackles"] == r3((10 + 11 + 12) / 3)
    # teams: KC offense passYds/g = QB 281.. avg + WR/RB 0; allowed = BUF's same
    kc = next(t for t in teams_j if t["team"] == "KC")
    assert kc["passYds"] == r1((281 + 282 + 283) / 3)
    assert kc["passYds_a"] == kc["passYds"]                  # symmetric synth
    assert kc["division"] == "AFC West" and kc["plays"] == r1(34 + 2 + 4 + 1 + 18)
    assert tf[0]["matches"] == 3                             # form window ? 5
    assert fx and fx[0]["week"] == 4 and fx[0]["roof"] == "outdoors"
    assert res and res[-1]["hs"] == 27
    assert len(lu) == 6 and all(x["status"] == "STARTER" for x in lu)
    assert len(itd) == 2 and itd[0]["gameFirst"] and not itd[1]["gameFirst"]
    assert itd[0]["player"] == "KC RB1" and itd[1]["player"] == "BUF WR1"  # short names resolved to full
    rz = J("redzone.json")
    rzrb = next(x for x in rz if x["player"] == "KC RB1")
    rzwr = next(x for x in rz if x["player"] == "BUF WR1")
    assert rzrb["rzAtt"] == 1 and rzrb["i10Att"] == 1 and rzrb["i5Att"] == 1 and rzrb["rzRushTd"] == 1
    assert rzrb["rzRushPct"] == 100.0 and rzrb["i5RushPct"] == 100.0
    assert rzwr["rzTgt"] == 1 and rzwr["rzRec"] == 1 and rzwr["rzRecTd"] == 1
    assert rzwr["i10Tgt"] == 0 and rzwr["rzTgtPct"] == 100.0        # 12-yd line: rz yes, i10 no
    gl = J("gamelogs.json")
    glrb = next(x for x in gl if x["Player"] == "KC RB1" and x["Year"] == "2025")
    glwr = next(x for x in gl if x["Player"] == "BUF WR1" and x["Year"] == "2025")
    glqb = next(x for x in gl if x["Player"] == "BUF QB1" and x["Year"] == "2025")
    assert glrb["longRush"] == 4.0 and glrb["longRec"] is None
    assert glwr["longRec"] == 12.0 and glqb["longComp"] == 12.0
    prb = next(x for x in J("players.json") if x["name"] == "KC RB1")
    assert prb["longRush"] == 4.0
    dvp = J("dvp.json")
    dwr = next(x for x in dvp if x["team"] == "KC" and x["pos"] == "WR")   # KC defended BUF WR1
    assert dwr.get("longRec") == 12.0 and dwr.get("expRec") == 0.0
    glkw = next(x for x in gl if x["Player"] == "KC WR1" and x["Year"] == "2025")
    assert glkw["longRec"] == 24.0 and glkw["expRec"] == 1                 # 24-yd catch = 1 explosive
    dbw = next(x for x in dvp if x["team"] == "BUF" and x["pos"] == "WR")  # BUF conceded it
    assert dbw.get("expRec") == 1.0 and dbw.get("longRec") == 24.0
    assert ij[0]["Player"] == "KC WR1" and ij[0]["Status"] == "Questionable"
    m = J("meta.json")
    assert m["week"] == 4 and m["password_hash"] == hashlib.sha256(b"testpw").hexdigest()
    # Clamp Watch feed: elite CB kept + graded, sieve drops torched CB, PFR code mapped
    adv_rows = []
    for wk_ in (1, 2, 3):
        adv_rows.append({"season": 2025, "week": wk_, "team": "GNB",
                         "pfr_player_name": "Elite Corner", "pfr_player_id": "CornEl00",
                         "def_targets": 12, "def_completions_allowed": 5,
                         "def_yards_allowed": 48, "def_receiving_td_allowed": 0, "def_ints": 1})
        adv_rows.append({"season": 2025, "week": wk_, "team": "KC",
                         "pfr_player_name": "Torched Corner", "pfr_player_id": "CornTo00",
                         "def_targets": 12, "def_completions_allowed": 10,
                         "def_yards_allowed": 150, "def_receiving_td_allowed": 2, "def_ints": 0})
        adv_rows.append({"season": 2025, "week": wk_, "team": "BUF",
                         "pfr_player_name": "Safety Guy", "pfr_player_id": "SafeGy00",
                         "def_targets": 12, "def_completions_allowed": 4,
                         "def_yards_allowed": 40, "def_receiving_td_allowed": 0, "def_ints": 1})
    ros_rows = [
        {"position": "CB", "pfr_id": "CornEl00", "full_name": "Elite Corner"},
        {"position": "CB", "pfr_id": "CornTo00", "full_name": "Torched Corner"},
        {"position": "S",  "pfr_id": "SafeGy00", "full_name": "Safety Guy"},
    ]
    dbs_t = build_dbs(pd.DataFrame(adv_rows), pd.DataFrame(ros_rows), 2025, [])
    # posDetail fallback: no roster hit, resolved via our own players table
    dbs_t2 = build_dbs(pd.DataFrame(adv_rows), None, 2025,
                       [{"name": "Elite Corner", "posDetail": "CB", "position": "DB"}])
    assert len(dbs_t2) == 1 and dbs_t2[0]["player"] == "Elite Corner"
    assert len(dbs_t) == 1 and dbs_t[0]["player"] == "Elite Corner"          # sieve + position filter
    assert dbs_t[0]["team"] == "GB" and dbs_t[0]["role"] == "top"            # GNB -> GB mapping
    assert dbs_t[0]["tgt"] == 36 and dbs_t[0]["grade"] < 60                  # components aggregated, elite rating
    print("SELFTEST PASSED - all schema + numeric assertions hold.")

# ?? cli ?????????????????????????????????????????????????????????????????????
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="nfl/data")
    ap.add_argument("--seasons", default="2023,2024,2025")
    ap.add_argument("--current", type=int, default=2025)
    ap.add_argument("--pbp-seasons", default="2024,2025")
    ap.add_argument("--password", default=None)
    ap.add_argument("--skip-weather", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest(); sys.exit(0)
    seasons = [int(s) for s in a.seasons.split(",") if s.strip()]
    pbp_seasons = [int(s) for s in a.pbp_seasons.split(",") if s.strip()]
    frames = load_frames(seasons, pbp_seasons, a.current)
    run_build(frames, a.out, seasons, a.current, a.password,
              skip_weather=a.skip_weather)
