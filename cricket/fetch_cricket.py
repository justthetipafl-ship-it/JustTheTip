#!/usr/bin/env python3
"""
fetch_cricket.py — JTT Cricket data pipeline (runs in GitHub Actions).

Pulls Cricsheet ball-by-ball JSON bundles, derives per-innings batting +
bowling rows, aggregates team rate profiles, and writes the JTT Cricket
data bundle. Emits the SAME schema as make_sample.py.

Cricsheet has no upcoming fixtures (historical only). cricket_fixtures.json
is therefore preserved if a hand-maintained file already exists; otherwise an
empty scaffold is written. fetch_fixtures.py owns that file in Actions.

SCOPE (Phase 2, July 2026):
  - Men's cricket only (male-only Cricsheet zips; gender check kept as belt+braces).
  - Internationals: kept ONLY when BOTH teams are ICC full members (FULL_MEMBERS).
    This drops associate qualifiers/tri-series (was ~69% of INTL rows).
  - Leagues: IPL, BBL, The Hundred, CPL, SA20, PSL.
  - The Hundred is tagged format "T100" (100-ball) so it never pollutes T20
    baselines. Phases: overs 0-4 = PP (first 25 balls), 15-19 = Death.
  - NOTE: Cricsheet withholds Afghanistan matches, so AFG stays in scope for
    fixtures but has no historical rows here.

Env:
  CRICKET_MONTHS   recency window in months (default 24)
"""
import json, os, io, zipfile, urllib.request, time, sys
from datetime import datetime, timezone
from collections import defaultdict

OUT = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(OUT, exist_ok=True)
MONTHS = int(os.environ.get("CRICKET_MONTHS", "24"))

# ICC full members. INTL matches are kept only when BOTH sides are in this set.
FULL_MEMBERS = {
    "Australia", "England", "India", "Pakistan", "South Africa",
    "New Zealand", "West Indies", "Sri Lanka", "Bangladesh",
    "Afghanistan", "Zimbabwe", "Ireland",
}

# Cricsheet competition zips → (level, comp display name, format override).
# Male-only zips where Cricsheet provides them (t20s/odis/tests/hnd); the
# league zips (ipl/bbl/cpl/psl/sat) are men-only competitions already.
# format override of None = derive from match_type via MT.
SOURCES = {
    "https://cricsheet.org/downloads/t20s_male_json.zip":  ("INTL",   None,                        None),
    "https://cricsheet.org/downloads/odis_male_json.zip":  ("INTL",   None,                        None),
    "https://cricsheet.org/downloads/tests_male_json.zip": ("INTL",   None,                        None),
    "https://cricsheet.org/downloads/ipl_json.zip":        ("LEAGUE", "Indian Premier League",     None),
    "https://cricsheet.org/downloads/bbl_json.zip":        ("LEAGUE", "Big Bash League",           None),
    "https://cricsheet.org/downloads/cpl_json.zip":        ("LEAGUE", "Caribbean Premier League",  None),
    "https://cricsheet.org/downloads/psl_json.zip":        ("LEAGUE", "Pakistan Super League",     None),
    "https://cricsheet.org/downloads/sat_json.zip":        ("LEAGUE", "SA20",                      None),
    "https://cricsheet.org/downloads/hnd_male_json.zip":   ("LEAGUE", "The Hundred",               "T100"),
}

# match_type → (format, default level). League level overridden by SOURCES.
MT = {"IT20": ("T20", "INTL"), "T20": ("T20", "LEAGUE"),
      "ODI": ("ODI", "INTL"), "ODM": ("ODI", "LEAGUE"),
      "Test": ("TEST", "INTL"), "MDM": ("TEST", "LEAGUE")}

def log(*a): print(*a, file=sys.stderr, flush=True)

def fetch_zip(url):
    log("downloading", url)
    req = urllib.request.Request(url, headers={"User-Agent": "jtt-cricket/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return zipfile.ZipFile(io.BytesIO(r.read()))

def within_window(date_str, cutoff):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc) >= cutoff
    except Exception:
        return False

def in_scope(info, level):
    """Scope filter: men only; INTL needs both sides to be full members."""
    if info.get("gender", "male") != "male":
        return False
    if level == "INTL":
        teams = info.get("teams", [])
        if len(teams) < 2:
            return False
        if not all(t in FULL_MEMBERS for t in teams):
            return False
    return True

def phase_of(ovn, fmt):
    """Phase bucket per format. T20: 0-5 PP / 16+ Death. T100 (5-ball overs,
    20 per innings): 0-4 PP (first 25 balls) / 15+ Death (last 25). Else Mid."""
    if fmt == "T20":
        return "PP" if ovn < 6 else ("Death" if ovn >= 16 else "Mid")
    if fmt == "T100":
        return "PP" if ovn < 5 else ("Death" if ovn >= 15 else "Mid")
    return "Mid"

def parse_match(info, innings, mid, src_level, src_comp, src_fmt):
    mt = info.get("match_type", "")
    if src_fmt:
        fmt, lvl = src_fmt, src_level or "LEAGUE"
    else:
        if mt not in MT:
            return []
        fmt, lvl = MT[mt]
    level = src_level or lvl
    comp = src_comp or (info.get("event", {}) or {}).get("name") or f"{fmt} {level}"
    dates = info.get("dates") or []
    date = dates[0] if dates else ""
    venue = info.get("venue", ""); city = info.get("city", "")
    teams = info.get("teams", [])
    out = []
    for inn_i, inn in enumerate(innings, start=1):
        bat_team = inn.get("team", "")
        bowl_team = next((t for t in teams if t != bat_team), "")
        # accumulators
        bat = defaultdict(lambda: {"runs": 0, "balls": 0, "fours": 0, "sixes": 0,
                                   "out": False, "dismissal": None, "dismissedBy": None, "order": None,
                                   "runsPP": 0, "runsMid": 0, "runsDeath": 0})
        bowl = defaultdict(lambda: {"runs": 0, "balls": 0, "wkts": 0, "dots": 0,
                                    "maidens": 0, "wktsPP": 0, "wktsDeath": 0,
                                    "ballsPP": 0, "ballsDeath": 0, "concPP": 0, "concDeath": 0,
                                    "overs_runs": defaultdict(int), "overs_balls": defaultdict(int)})
        order_seen = []
        for over in inn.get("overs", []):
            ovn = over.get("over", 0)
            phase = phase_of(ovn, fmt)
            for d in over.get("deliveries", []):
                bat_p = d.get("batter"); bowl_p = d.get("bowler")
                if bat_p and bat_p not in order_seen:
                    order_seen.append(bat_p)
                ns = d.get("non_striker")
                if ns and ns not in order_seen:
                    order_seen.append(ns)
                runs = d.get("runs", {})
                br = runs.get("batter", 0); extras = runs.get("extras", 0); tot = runs.get("total", 0)
                # batting
                b = bat[bat_p]
                b["runs"] += br
                # ball faced unless wides
                wides = (d.get("extras", {}) or {}).get("wides", 0)
                if not wides:
                    b["balls"] += 1
                if br == 4: b["fours"] += 1
                if br == 6: b["sixes"] += 1
                if phase == "PP": b["runsPP"] += br
                elif phase == "Death": b["runsDeath"] += br
                else: b["runsMid"] += br
                # bowling
                w = bowl[bowl_p]
                w["runs"] += (br + (d.get("extras", {}) or {}).get("wides", 0)
                              + (d.get("extras", {}) or {}).get("noballs", 0))
                if not wides:
                    w["balls"] += 1
                if tot == 0 and not extras:
                    w["dots"] += 1
                w["overs_runs"][ovn] += tot
                w["overs_balls"][ovn] += (0 if wides else 1)
                if phase == "PP":
                    w["concPP"] += tot
                    if not wides: w["ballsPP"] += 1
                elif phase == "Death":
                    w["concDeath"] += tot
                    if not wides: w["ballsDeath"] += 1
                # wickets
                for wk in d.get("wickets", []):
                    po = wk.get("player_out")
                    kind = wk.get("kind", "")
                    if po in bat:
                        bat[po]["out"] = True; bat[po]["dismissal"] = kind
                        if kind not in ("run out", "retired hurt", "retired out", "obstructing the field"):
                            bat[po]["dismissedBy"] = bowl_p
                    if kind not in ("run out", "retired hurt", "retired out", "obstructing the field"):
                        w["wkts"] += 1
                        if phase == "PP": w["wktsPP"] += 1
                        elif phase == "Death": w["wktsDeath"] += 1
        order_map = {nm: i + 1 for i, nm in enumerate(order_seen)}
        # emit batting rows
        for nm, b in bat.items():
            if b["balls"] == 0 and b["runs"] == 0:
                continue
            out.append({
                "matchId": mid, "date": date, "format": fmt, "comp": comp, "level": level,
                "venue": venue, "city": city, "name": nm, "team": bat_team, "opp": bowl_team,
                "innings": inn_i, "bat": True, "bowl": False,
                "batOrder": order_map.get(nm), "runs": b["runs"], "balls": b["balls"],
                "fours": b["fours"], "sixes": b["sixes"], "out": b["out"],
                "dismissal": b["dismissal"], "dismissedBy": b["dismissedBy"],
                "sr": round(b["runs"] / b["balls"] * 100, 1) if b["balls"] else 0,
                "runsPP": b["runsPP"], "runsMid": b["runsMid"], "runsDeath": b["runsDeath"],
            })
        # emit bowling rows
        for nm, w in bowl.items():
            if w["balls"] == 0:
                continue
            balls_per_over = 5 if fmt == "T100" else 6
            maidens = sum(1 for ov, rb in w["overs_balls"].items()
                          if rb == balls_per_over and w["overs_runs"][ov] == 0)
            out.append({
                "matchId": mid, "date": date, "format": fmt, "comp": comp, "level": level,
                "venue": venue, "city": city, "name": nm, "team": bowl_team, "opp": bat_team,
                "innings": inn_i, "bat": False, "bowl": True,
                "oversBowled": round(w["balls"] / balls_per_over, 1), "ballsBowled": w["balls"],
                "runsConceded": w["runs"], "wickets": w["wkts"], "maidens": maidens,
                "econ": round(w["runs"] / (w["balls"] / balls_per_over), 2) if w["balls"] else 0,
                "dots": w["dots"], "wktsPP": w["wktsPP"], "wktsDeath": w["wktsDeath"],
                "ballsPP": w["ballsPP"], "ballsDeath": w["ballsDeath"],
                "concPP": w["concPP"], "concDeath": w["concDeath"],
            })
    return out

def main():
    cutoff = datetime.now(timezone.utc).replace(microsecond=0)
    cutoff = cutoff.replace(year=cutoff.year - (MONTHS // 12), month=((cutoff.month - 1 - MONTHS % 12) % 12) + 1)
    rows = []
    skipped_scope = 0
    for url, (level, comp, fmt_override) in SOURCES.items():
        try:
            z = fetch_zip(url)
        except Exception as e:
            log("FAILED", url, e); continue
        names = [n for n in z.namelist() if n.endswith(".json") and not n.startswith("README")]
        kept = 0; scoped_out = 0
        for n in names:
            try:
                m = json.loads(z.read(n))
            except Exception:
                continue
            info = m.get("info", {})
            dates = info.get("dates") or []
            if not dates or not within_window(dates[0], cutoff):
                continue
            if not in_scope(info, level):
                scoped_out += 1
                continue
            mid = os.path.splitext(os.path.basename(n))[0]
            rows.extend(parse_match(info, m.get("innings", []), mid, level, comp, fmt_override))
            kept += 1
        skipped_scope += scoped_out
        log(f"  {url.split('/')[-1]}: {kept} matches in window ({scoped_out} dropped by scope)")

    # team aggregates
    teams_stat = defaultdict(lambda: {"br": 0, "bb": 0, "f": 0, "s": 0, "wr": 0, "wb": 0, "wk": 0, "m": set()})
    for r in rows:
        s = teams_stat[(r["team"], r["format"], r["level"])]
        s["m"].add(r["matchId"])
        if r.get("bat"):
            s["br"] += r["runs"]; s["bb"] += r["balls"]; s["f"] += r["fours"]; s["s"] += r["sixes"]
        if r.get("bowl"):
            s["wr"] += r["runsConceded"]; s["wb"] += r["ballsBowled"]; s["wk"] += r["wickets"]
    teams_out = {}
    for (team, fmt, level), s in teams_stat.items():
        n = max(1, len(s["m"]))
        bpo = 5 if fmt == "T100" else 6
        teams_out.setdefault(team, {})[f"{fmt}/{level}"] = {
            "matches": n,
            "batSR": round(s["br"] / max(1, s["bb"]) * 100, 1),
            "foursPM": round(s["f"] / n, 1), "sixesPM": round(s["s"] / n, 1),
            "bowlEcon": round(s["wr"] / max(1, s["wb"]) * bpo, 2),
            "wktsPM": round(s["wk"] / n, 1),
        }

    ver = str(int(time.time() * 1000))
    def dump(name, obj):
        with open(os.path.join(OUT, name), "w") as f:
            json.dump(obj, f, separators=(",", ":"))
    dump("cricket_logs.json", {"playerRows": len(rows), "version": ver, "rows": rows})
    dump("cricket_stats.json", {"matchCount": len({r["matchId"] for r in rows}),
                                "version": ver, "teams": teams_out})
    # preserve hand-maintained fixtures if present
    fx_path = os.path.join(OUT, "cricket_fixtures.json")
    if not os.path.exists(fx_path):
        dump("cricket_fixtures.json", {"fixtureCount": 0, "version": ver, "fixtures": []})
    # ratings: pipeline-owned tiers (per format, 12-month window, min 8 innings).
    # Percentile-tiered so tiers stay meaningful as the sample shifts:
    #   ELITE = top decile, STRONG = next 15%, everyone else MID.
    win_cut = datetime.now(timezone.utc)
    win_cut = f"{win_cut.year-1:04d}-{win_cut.month:02d}-{win_cut.day:02d}"
    per = defaultdict(lambda: defaultdict(lambda: {"bat": [0, 0], "bowl": [0, 0]}))
    for r in rows:
        if r["date"] < win_cut:
            continue
        e = per[r["format"]][r["name"]]
        if r.get("bat"):
            e["bat"][0] += r.get("runs", 0); e["bat"][1] += 1
        if r.get("bowl"):
            e["bowl"][0] += r.get("wickets", 0); e["bowl"][1] += 1
    tiers = {}
    for fmt, names in per.items():
        for kind in ("bat", "bowl"):
            vals = sorted(((v[kind][0] / v[kind][1], n) for n, v in names.items()
                           if v[kind][1] >= 8), reverse=True)
            if len(vals) < 10:
                continue
            n = len(vals)
            cut_e, cut_s = max(1, n // 10), max(2, n // 4)
            d = tiers.setdefault(fmt, {}).setdefault(kind, {})
            for i, (_, nm) in enumerate(vals):
                d[nm] = "ELITE" if i < cut_e else ("STRONG" if i < cut_s else "MID")
    dump("cricket_ratings.json", {"version": ver, "tiers": tiers})
    log("ratings: " + ", ".join(f"{f}:{sum(len(k) for k in t.values())}" for f, t in tiers.items()))
    with open(os.path.join(OUT, "version.txt"), "w") as f:
        f.write(ver)
    log(f"DONE rows={len(rows)} matches={len({r['matchId'] for r in rows})} "
        f"teams={len(teams_out)} scope-dropped={skipped_scope}")

if __name__ == "__main__":
    main()
