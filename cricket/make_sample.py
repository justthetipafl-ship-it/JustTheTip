#!/usr/bin/env python3
"""
make_sample.py — JTT Cricket sample bundle generator.

Produces the same JSON schema the real fetch_cricket.py emits, but with
synthetic-yet-plausible data so the tool runs end-to-end with no live pull.

Outputs (./data/):
  cricket_logs.json      per-innings batting+bowling player rows (engine fuel)
  cricket_stats.json     per-team / per-format rate profiles
  cricket_fixtures.json  upcoming matches (Cricsheet is historical-only; this
                         feed is manual/secondary in production)
  cricket_ratings.json   name aliases + pace/spin lookup + ICC tier bands
  version.txt            ms timestamp for cache-busting + freshness pill
"""
import json, os, random, time, math
from datetime import datetime, timedelta, timezone

random.seed(7)
OUT = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(OUT, exist_ok=True)

# ------------------------------------------------------------------ teams
# (team, ICC tier band 1/2/3, batting strength 0-1, bowling strength 0-1)
INTL = [
    ("Australia", 1, .82, .80), ("India", 1, .85, .78), ("England", 1, .80, .74),
    ("South Africa", 1, .76, .77), ("New Zealand", 1, .72, .73),
    ("Pakistan", 2, .74, .79), ("Sri Lanka", 2, .68, .66), ("West Indies", 2, .71, .62),
    ("Bangladesh", 2, .60, .63), ("Afghanistan", 2, .62, .70),
    ("Ireland", 3, .52, .50), ("Netherlands", 3, .50, .48), ("Zimbabwe", 3, .51, .49),
]
LEAGUE_T20 = {
    "Big Bash League": ["Perth Scorchers", "Sydney Sixers", "Brisbane Heat",
                        "Melbourne Stars", "Adelaide Strikers", "Sydney Thunder"],
    "Indian Premier League": ["Mumbai Indians", "Chennai Super Kings",
                              "Royal Challengers Bengaluru", "Kolkata Knight Riders",
                              "Gujarat Titans", "Rajasthan Royals"],
}
VENUES = {
    "Australia": [("Melbourne Cricket Ground", "Melbourne", 1.02),
                  ("Sydney Cricket Ground", "Sydney", .98),
                  ("The Gabba", "Brisbane", 1.00), ("Perth Stadium", "Perth", 1.05)],
    "India": [("Wankhede Stadium", "Mumbai", 1.08), ("M Chinnaswamy Stadium", "Bengaluru", 1.12),
              ("Eden Gardens", "Kolkata", 1.00), ("MA Chidambaram Stadium", "Chennai", .92)],
    "England": [("Lord's", "London", .99), ("The Oval", "London", 1.01),
                ("Old Trafford", "Manchester", .97)],
}
ALL_VENUES = [v for vs in VENUES.values() for v in vs]

FORMATS = {  # name -> (base_team_runs, overs, level)
    "T20": (165, 20, None), "ODI": (270, 50, "INTL"), "TEST": (320, 90, "INTL"),
}

# squad archetypes: (role, bat_order, bowl_type or None)
ARCHETYPES = [
    ("opener", 1, None), ("opener", 2, None), ("top", 3, None), ("top", 4, None),
    ("mid", 5, None), ("mid", 6, "spin"), ("allrounder", 7, "pace"),
    ("bowler", 8, "spin"), ("bowler", 9, "pace"), ("bowler", 10, "pace"),
    ("bowler", 11, "spin"),
]

FIRST = ["Marcus", "Travis", "Steve", "David", "Glenn", "Pat", "Josh", "Mitchell",
         "Cameron", "Adam", "Virat", "Rohit", "Jasprit", "Hardik", "Rishabh", "Joe",
         "Ben", "Jos", "Harry", "Kane", "Trent", "Babar", "Shaheen", "Rashid",
         "Wanindu", "Nicholas", "Quinton", "Kagiso", "Heinrich", "Shai"]
LAST = ["Smith", "Head", "Warner", "Maxwell", "Cummins", "Hazlewood", "Marsh",
        "Green", "Carey", "Zampa", "Kohli", "Sharma", "Bumrah", "Pandya", "Pant",
        "Root", "Stokes", "Buttler", "Brook", "Williamson", "Boult", "Azam",
        "Afridi", "Khan", "Hasaranga", "Pooran", "de Kock", "Rabada", "Klaasen", "Hope"]

used_names = set()
def make_name():
    while True:
        n = f"{random.choice(FIRST)} {random.choice(LAST)}"
        if n not in used_names:
            used_names.add(n); return n

# ------------------------------------------------------------ build squads
def build_squad(team, bat_str, bowl_str):
    sq = []
    for role, order, btype in ARCHETYPES:
        nm = make_name()
        sq.append({"name": nm, "team": team, "role": role, "batOrder": order,
                   "bowlType": btype, "bat_str": bat_str, "bowl_str": bowl_str})
    return sq

SQUADS = {}
for t, tier, bs, bw in INTL:
    SQUADS[t] = build_squad(t, bs, bw)
for comp, teams in LEAGUE_T20.items():
    for t in teams:
        SQUADS[t] = build_squad(t, random.uniform(.6, .8), random.uniform(.58, .78))

# pace/spin lookup + tiers + aliases
bowlType, tiers_t20i, tiers_odi = {}, {}, {}
for t, tier, bs, bw in INTL:
    tiers_t20i[t] = tier; tiers_odi[t] = tier
for sq in SQUADS.values():
    for p in sq:
        if p["bowlType"]:
            bowlType[p["name"]] = p["bowlType"]

# ------------------------------------------------------- per-innings sim
def sim_batting(p, fmt, venue_factor, opp_bowl):
    base, overs, _ = FORMATS[fmt]
    # expected balls faced by order slot
    slot = p["batOrder"]
    bf_mean = {1: 28, 2: 28, 3: 26, 4: 24, 5: 20, 6: 16, 7: 12, 8: 8, 9: 5, 10: 3, 11: 2}.get(slot, 10)
    if fmt == "ODI": bf_mean *= 1.9
    if fmt == "TEST": bf_mean *= 3.2
    balls = max(0, int(random.gauss(bf_mean, bf_mean * .55)))
    if balls == 0:
        return None
    # strike rate driven by batting strength, venue, opposition bowling
    sr_base = (118 + p["bat_str"] * 55) * venue_factor * (1.05 - opp_bowl * .12)
    if fmt == "ODI": sr_base *= .72
    if fmt == "TEST": sr_base *= .52
    runs = max(0, int(balls * sr_base / 100 + random.gauss(0, 6)))
    # boundaries
    fours = min(runs // 4, int(balls * 0.10 * (0.8 + p["bat_str"] * 0.6) + random.gauss(0, 1.2)))
    fours = max(0, fours)
    sixes = max(0, int(balls * 0.035 * (0.5 + p["bat_str"]) + random.gauss(0, 0.8)))
    out = random.random() < (0.62 if fmt == "T20" else 0.5)
    # phase split (T20 only meaningful) by over of dismissal-ish
    if fmt == "T20":
        pp = random.uniform(.2, .45) if slot <= 3 else random.uniform(0, .15)
        death = random.uniform(.25, .5) if slot >= 5 else random.uniform(.05, .2)
        mid = max(0, 1 - pp - death)
    else:
        pp = mid = death = 0
    dismissals = ["caught", "bowled", "lbw", "run out", "caught", "caught behind", "stumped"]
    return {
        "bat": True, "batOrder": slot, "runs": runs, "balls": balls,
        "fours": fours, "sixes": sixes, "out": out,
        "dismissal": random.choice(dismissals) if out else None,
        "sr": round(runs / balls * 100, 1) if balls else 0,
        "runsPP": round(runs * pp), "runsMid": round(runs * mid), "runsDeath": round(runs * death),
    }

def sim_bowling(p, fmt, venue_factor, opp_bat):
    if not p["bowlType"]:
        return None
    overs = {"T20": 4, "ODI": 9, "TEST": 18}[fmt]
    ov = max(1, int(random.gauss(overs * .8, overs * .25)))
    ov = min(ov, overs)
    balls = ov * 6
    econ_base = (7.4 - p["bowl_str"] * 2.0) * venue_factor * (0.95 + opp_bat * .15)
    if fmt == "ODI": econ_base *= .82
    if fmt == "TEST": econ_base *= .55
    runs_c = max(0, int(balls / 6 * econ_base + random.gauss(0, 4)))
    wkt_mean = (balls / 6) * (0.18 + p["bowl_str"] * 0.16)
    wkts = max(0, min(5, int(random.gauss(wkt_mean, 0.9))))
    dots = min(balls, int(balls * (0.35 + p["bowl_str"] * 0.18) + random.gauss(0, 3)))
    maidens = max(0, int(random.gauss(0.6 if fmt != "T20" else 0.15, 0.5)))
    pp_w = random.randint(0, max(0, wkts)) if fmt == "T20" else 0
    return {
        "bowl": True, "oversBowled": ov, "ballsBowled": balls,
        "runsConceded": runs_c, "wickets": wkts, "maidens": maidens,
        "econ": round(runs_c / (balls / 6), 2) if balls else 0,
        "dots": max(0, dots), "wktsPP": pp_w, "wktsDeath": max(0, wkts - pp_w),
        "bowlType": p["bowlType"],
    }

# ---------------------------------------------------- generate matches
rows = []
match_seq = 1000000
now = datetime.now(timezone.utc)

def play_match(home, away, fmt, comp, level, days_ago):
    global match_seq
    match_seq += 1
    mid = str(match_seq)
    date = (now - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    vh = VENUES.get(home, ALL_VENUES)
    venue, city, vf = random.choice(vh)
    for bat_team, bowl_team, inn in [(home, away, 1), (away, home, 2)]:
        opp_bowl = sum(p["bowl_str"] for p in SQUADS[bowl_team]) / len(SQUADS[bowl_team])
        opp_bat = sum(p["bat_str"] for p in SQUADS[bat_team]) / len(SQUADS[bat_team])
        for p in SQUADS[bat_team]:
            b = sim_batting(p, fmt, vf, opp_bowl)
            if not b: continue
            row = {"matchId": mid, "date": date, "format": fmt, "comp": comp,
                   "level": level, "venue": venue, "city": city,
                   "name": p["name"], "team": bat_team, "opp": bowl_team, "innings": inn,
                   "bowl": False}
            row.update(b); rows.append(row)
        for p in SQUADS[bowl_team]:
            bw = sim_bowling(p, fmt, vf, opp_bat)
            if not bw: continue
            row = {"matchId": mid, "date": date, "format": fmt, "comp": comp,
                   "level": level, "venue": venue, "city": city,
                   "name": p["name"], "team": bowl_team, "opp": bat_team, "innings": inn,
                   "bat": False}
            row.update(bw); rows.append(row)

# international fixtures across formats, last ~18 months
for d in range(1, 540, 6):
    a, b = random.sample([t[0] for t in INTL], 2)
    fmt = random.choices(["T20", "ODI", "TEST"], weights=[5, 3, 2])[0]
    comp = {"T20": "ICC Men's T20 World Cup", "ODI": "ODI Series", "TEST": "Test Series"}[fmt]
    play_match(a, b, fmt, comp, "INTL", d)

# league T20 (BBL / IPL)
for comp, teams in LEAGUE_T20.items():
    for d in range(2, 200, 4):
        a, b = random.sample(teams, 2)
        play_match(a, b, "T20", comp, "LEAGUE", d)

# ---------------------------------------------------- aggregate stats
def fmt_level(fmt, level):
    return f"{fmt}/{level}"

teams_stat = {}
for r in rows:
    key = (r["team"], r["format"], r["level"])
    s = teams_stat.setdefault(key, {"bat_runs": 0, "bat_balls": 0, "fours": 0, "sixes": 0,
                                    "bowl_runs": 0, "bowl_balls": 0, "wkts": 0, "matches": set()})
    s["matches"].add(r["matchId"])
    if r.get("bat"):
        s["bat_runs"] += r["runs"]; s["bat_balls"] += r["balls"]
        s["fours"] += r["fours"]; s["sixes"] += r["sixes"]
    if r.get("bowl"):
        s["bowl_runs"] += r["runsConceded"]; s["bowl_balls"] += r["ballsBowled"]
        s["wkts"] += r["wickets"]

teams_out = {}
for (team, fmt, level), s in teams_stat.items():
    n = max(1, len(s["matches"]))
    teams_out.setdefault(team, {})[fmt_level(fmt, level)] = {
        "matches": n,
        "batSR": round(s["bat_runs"] / max(1, s["bat_balls"]) * 100, 1),
        "foursPM": round(s["fours"] / n, 1), "sixesPM": round(s["sixes"] / n, 1),
        "bowlEcon": round(s["bowl_runs"] / max(1, s["bowl_balls"]) * 6, 2),
        "wktsPM": round(s["wkts"] / n, 1),
    }

# ---------------------------------------------------- upcoming fixtures
fixtures = []
fx_seq = 9000
for i in range(8):
    fx_seq += 1
    a, b = random.sample([t[0] for t in INTL], 2)
    fmt = random.choice(["T20", "ODI"])
    venue, city, _ = random.choice(VENUES.get(a, ALL_VENUES))
    ko = now + timedelta(days=i + 1, hours=random.randint(2, 9))
    fixtures.append({
        "matchId": f"FX{fx_seq}", "format": fmt, "level": "INTL",
        "comp": {"T20": "T20I Series", "ODI": "ODI Series"}[fmt],
        "date": ko.strftime("%Y-%m-%d"), "utc": ko.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "home": a, "away": b, "venue": venue, "city": city, "status": "upcoming",
    })
# a couple of league fixtures
for comp, teams in LEAGUE_T20.items():
    fx_seq += 1
    a, b = random.sample(teams, 2)
    venue, city, _ = random.choice(ALL_VENUES)
    ko = now + timedelta(days=random.randint(1, 6), hours=random.randint(2, 9))
    fixtures.append({
        "matchId": f"FX{fx_seq}", "format": "T20", "level": "LEAGUE", "comp": comp,
        "date": ko.strftime("%Y-%m-%d"), "utc": ko.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "home": a, "away": b, "venue": venue, "city": city, "status": "upcoming",
    })

# ---------------------------------------------------- write bundle
ver = str(int(time.time() * 1000))
def dump(name, obj):
    with open(os.path.join(OUT, name), "w") as f:
        json.dump(obj, f, separators=(",", ":"))

dump("cricket_logs.json", {"playerRows": len(rows), "version": ver, "rows": rows})
dump("cricket_stats.json", {"matchCount": len({r["matchId"] for r in rows}),
                            "version": ver, "teams": teams_out})
dump("cricket_fixtures.json", {"fixtureCount": len(fixtures), "version": ver, "fixtures": fixtures})
dump("cricket_ratings.json", {
    "byName": {}, "bowlType": bowlType,
    "tiers": {"T20I": tiers_t20i, "ODI": tiers_odi, "T20": tiers_t20i},
})
with open(os.path.join(OUT, "version.txt"), "w") as f:
    f.write(ver)

print(f"rows={len(rows)} matches={len({r['matchId'] for r in rows})} "
      f"teams={len(teams_out)} fixtures={len(fixtures)} bowlType={len(bowlType)}")
