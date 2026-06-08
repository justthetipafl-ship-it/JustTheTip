#!/usr/bin/env python3
"""
make_sample.py — generates a realistic data/mlb_bundle.json for JTT MLB.

This is ONLY for local dev / demo so the tool renders before the real
pipeline is wired. fetch_mlb.py produces the identical schema from the
live MLB Stats API. Deterministic (seeded) so output is stable.
"""
import json, random, time, datetime, os

random.seed(2026)
SEASON = 2026
TODAY = datetime.date(2026, 6, 8)

# --- park factors (load the static file we ship) ---
with open(os.path.join(os.path.dirname(__file__), "park_factors.json")) as f:
    PARKS = {k: v for k, v in json.load(f).items() if not k.startswith("_")}

TEAMS = {
    147: {"abbr": "NYY", "name": "New York Yankees",  "div": "AL East", "park": "NYY", "kRate": 0.215},
    111: {"abbr": "BOS", "name": "Boston Red Sox",    "div": "AL East", "park": "BOS", "kRate": 0.228},
    119: {"abbr": "LAD", "name": "Los Angeles Dodgers","div": "NL West", "park": "LAD", "kRate": 0.205},
    137: {"abbr": "SF",  "name": "San Francisco Giants","div": "NL West", "park": "SF",  "kRate": 0.232},
    144: {"abbr": "ATL", "name": "Atlanta Braves",    "div": "NL East", "park": "ATL", "kRate": 0.241},
    143: {"abbr": "PHI", "name": "Philadelphia Phillies","div": "NL East","park": "PHI","kRate": 0.219},
    117: {"abbr": "HOU", "name": "Houston Astros",    "div": "AL West", "park": "HOU", "kRate": 0.198},
    136: {"abbr": "SEA", "name": "Seattle Mariners",  "div": "AL West", "park": "SEA", "kRate": 0.258},
}

# rosters: (id, name, bats, pos, tier 1=elite..3=role)
ROSTERS = {
    147: [(592450,"Aaron Judge","R","RF",1),(665487,"Juan Soto","L","LF",1),(519317,"Giancarlo Stanton","R","DH",2),
          (650402,"Anthony Volpe","R","SS",3),(642708,"Austin Wells","L","C",3),(683011,"Anthony Rizzo","L","1B",2),
          (656775,"Jazz Chisholm Jr.","L","2B",2),(595281,"Alex Verdugo","L","CF",3),(665828,"Oswaldo Cabrera","S","3B",3)],
    111: [(646240,"Rafael Devers","L","DH",1),(671213,"Jarren Duran","L","CF",2),(680776,"Triston Casas","L","1B",2),
          (657136,"Wilyer Abreu","L","RF",3),(683734,"Ceddanne Rafaela","R","SS",3),(646242,"Connor Wong","R","C",3),
          (593428,"Trevor Story","R","SS",2),(680777,"Masataka Yoshida","L","LF",2),(671221,"David Hamilton","L","2B",3)],
    119: [(605141,"Mookie Betts","R","SS",1),(660271,"Shohei Ohtani","L","DH",1),(518692,"Freddie Freeman","L","1B",1),
          (608369,"Will Smith","R","C",2),(571970,"Max Muncy","L","3B",2),(669257,"Teoscar Hernandez","R","RF",2),
          (681546,"Andy Pages","R","CF",3),(666158,"Gavin Lux","L","2B",3),(682668,"Tommy Edman","S","LF",3)],
    137: [(605412,"Wilmer Flores","R","1B",3),(680573,"Heliot Ramos","R","LF",2),(669758,"Matt Chapman","R","3B",2),
          (596059,"Mike Yastrzemski","L","RF",3),(643217,"Patrick Bailey","S","C",3),(673357,"Tyler Fitzgerald","R","SS",3),
          (664023,"Jung Hoo Lee","L","CF",2),(516782,"LaMonte Wade Jr.","L","DH",3),(671032,"Casey Schmitt","R","2B",3)],
    144: [(660670,"Ronald Acuna Jr.","R","RF",1),(621566,"Matt Olson","L","1B",1),(645277,"Austin Riley","R","3B",1),
          (663586,"Ozzie Albies","S","2B",2),(671739,"Michael Harris II","L","CF",2),(700022,"Sean Murphy","R","C",3),
          (645302,"Marcell Ozuna","R","DH",1),(682829,"Jarred Kelenic","L","LF",3),(671277,"Orlando Arcia","R","SS",3)],
    143: [(656941,"Kyle Schwarber","L","DH",1),(547180,"Bryce Harper","L","1B",1),(664761,"Trea Turner","R","SS",1),
          (592663,"J.T. Realmuto","R","C",2),(641584,"Nick Castellanos","R","RF",2),(681082,"Bryson Stott","L","2B",3),
          (657077,"Alec Bohm","R","3B",2),(624641,"Brandon Marsh","L","CF",3),(669016,"Johan Rojas","R","CF",3)],
    117: [(514888,"Jose Altuve","R","2B",1),(608324,"Alex Bregman","R","3B",2),(670541,"Yordan Alvarez","L","DH",1),
          (663728,"Kyle Tucker","L","RF",1),(676801,"Jeremy Pena","R","SS",2),(665161,"Yainer Diaz","R","C",3),
          (642715,"Jake Meyers","R","CF",3),(676694,"Chas McCormick","R","LF",3),(670750,"Mauricio Dubon","R","2B",3)],
    136: [(677594,"Julio Rodriguez","R","CF",1),(572233,"Mitch Garver","R","DH",3),(641487,"Cal Raleigh","S","C",2),
          (663898,"J.P. Crawford","L","SS",2),(677951,"Josh Rojas","L","3B",3),(682985,"Dominic Canzone","L","LF",3),
          (664034,"Luke Raley","L","1B",3),(668942,"Victor Robles","R","RF",3),(676914,"Ryan Bliss","R","2B",3)],
}

# probable starters: (id, name, throws, team, tier 1=ace..3=back)
STARTERS = {
    147: (543037,"Gerrit Cole","R",1),
    111: (605483,"Brayan Bello","R",3),
    119: (477132,"Clayton Kershaw","L",2),
    137: (592332,"Logan Webb","R",1),
    144: (621381,"Max Fried","L",1),
    143: (605400,"Aaron Nola","R",2),
    117: (664353,"Framber Valdez","L",1),
    136: (642547,"Luis Castillo","R",2),
}

SLATE = [
    (147, 111, "19:10"),  # NYY @ BOS  (home BOS)
    (119, 137, "21:45"),  # LAD @ SF
    (144, 143, "18:40"),  # ATL @ PHI
    (117, 136, "21:40"),  # HOU @ SEA
]

def rnd(a, b, nd=3):
    return round(random.uniform(a, b), nd)

def batter_profile(pid, name, bats, pos, tier):
    g = random.randint(55, 64)
    pa = int(g * random.uniform(3.8, 4.4))
    ab = int(pa * 0.88)
    avg = {1: rnd(.285,.330), 2: rnd(.250,.290), 3: rnd(.220,.260)}[tier]
    h = int(ab * avg)
    hr = {1: random.randint(14,26), 2: random.randint(7,15), 3: random.randint(2,9)}[tier]
    db = random.randint(8, 20); tr = random.randint(0, 3)
    rbi = int(hr * random.uniform(2.2, 3.2)) + random.randint(8, 20)
    r = int(h * random.uniform(0.5, 0.8))
    sb = {1: random.randint(2,18), 2: random.randint(0,12), 3: random.randint(0,8)}[tier]
    bb = int(pa * rnd(.07,.14)); so = int(pa * rnd(.16,.28))
    singles = h - db - tr - hr
    tb = singles + 2*db + 3*tr + 4*hr
    obp = round(avg + bb/pa + .02, 3)
    slg = round(tb/ab, 3)
    def split(mult_avg, mult_pow, pa_share):
        spa = int(pa * pa_share)
        return {"PA": spa, "AVG": round(min(.420, avg*mult_avg),3),
                "SLG": round(min(.850, slg*mult_pow),3),
                "HR": max(0,int(hr*pa_share*mult_pow)),
                "OPS": round(min(1.30,(obp*mult_avg)+(slg*mult_pow)),3)}
    # platoon: lefties hit RHP better, righties hit LHP better; switch neutral
    if bats == "L":
        vsL, vsR = split(.90,.85,.28), split(1.06,1.10,.72)
    elif bats == "R":
        vsL, vsR = split(1.08,1.12,.30), split(.94,.92,.70)
    else:
        vsL, vsR = split(1.0,1.0,.45), split(1.0,1.0,.55)
    return {"id": pid, "name": name, "bats": bats, "pos": pos, "tier": tier,
            "season": {"G":g,"PA":pa,"AB":ab,"H":h,"2B":db,"3B":tr,"HR":hr,"RBI":rbi,
                       "R":r,"SB":sb,"BB":bb,"SO":so,"AVG":round(avg,3),"OBP":obp,"SLG":slg,"TB":tb,
                       "OPS":round(obp+slg,3)},
            "splitVsL": vsL, "splitVsR": vsR}

def batter_gamelog(prof, opp_abbr):
    log = []
    s = prof["season"]; hpg = s["H"]/s["G"]; tbpg = s["TB"]/s["G"]
    for i in range(15):
        d = TODAY - datetime.timedelta(days=(i+1)*1)
        h = max(0, int(round(random.gauss(hpg, 0.9))))
        h = min(h, 4)
        hr = 1 if random.random() < (s["HR"]/s["G"]) else 0
        db = 1 if (h >= 2 and random.random() < .35) else 0
        singles = max(0, h - hr - db)
        tb = singles + 2*db + 4*hr
        rbi = hr*random.randint(1,3) + (random.randint(0,2) if h else 0)
        r = (1 if hr else 0) + (1 if (h and random.random()<.4) else 0)
        sb = 1 if (h and random.random() < (s["SB"]/max(1,s["H"]))) else 0
        bb = 1 if random.random() < (s["BB"]/s["PA"]) else 0
        so = 1 if random.random() < (s["SO"]/s["PA"]) else 0
        log.append({"date": d.isoformat(), "opp": opp_abbr, "H":h,"TB":tb,"HR":hr,
                    "RBI":rbi,"R":r,"SB":sb,"BB":bb,"SO":so})
    return log

def bvp(prof, starter):
    # batter-vs-pitcher career sample — deliberately small + noisy
    pa = random.randint(3, 28)
    base = prof["season"]["AVG"]
    # exaggerate randomly to create bunnies/bogeys
    avg = max(0.0, min(.600, random.gauss(base, .120)))
    h = int(round(pa * 0.88 * avg))
    hr = 1 if (pa > 10 and random.random() < .18) else 0
    tb = h + (3 if hr else random.randint(0, h))
    return {"PA": pa, "H": h, "HR": hr, "TB": tb, "AVG": round(avg,3)}

def pitcher_profile(pid, name, throws, tier, opp_team):
    gs = random.randint(11, 14)
    ip = round(gs * random.uniform(5.2, 6.4), 1)
    k9 = {1: rnd(9.5,11.8,1), 2: rnd(8.0,9.6,1), 3: rnd(6.5,8.2,1)}[tier]
    bb9 = {1: rnd(1.8,2.6,1), 2: rnd(2.4,3.2,1), 3: rnd(3.0,3.9,1)}[tier]
    hr9 = {1: rnd(0.7,1.1,2), 2: rnd(1.0,1.4,2), 3: rnd(1.3,1.8,2)}[tier]
    k = int(ip*k9/9); bb = int(ip*bb9/9); hr = int(ip*hr9/9)
    h = int(ip * random.uniform(0.85, 1.05))
    er = int(ip * {1:rnd(.30,.42), 2:rnd(.42,.55), 3:rnd(.55,.72)}[tier])
    era = round(er*9/ip, 2); whip = round((h+bb)/ip, 2)
    oppavg = {1: rnd(.205,.230), 2: rnd(.235,.258), 3: rnd(.260,.285)}[tier]
    def psplit(m):
        return {"K9": round(k9*m,1), "oppAVG": round(min(.330,oppavg*(2-m)),3), "HR9": round(hr9*(2-m),2)}
    # RHP tougher on RHB; LHP tougher on LHB (approximate)
    if throws == "R":
        vsL, vsR = psplit(0.92), psplit(1.08)
    else:
        vsL, vsR = psplit(1.08), psplit(0.92)
    log = []
    for i in range(gs):
        d = TODAY - datetime.timedelta(days=(i+1)*5)
        gip = round(random.uniform(4.0, 7.0), 1); outs = int(gip)*3 + int(round((gip%1)*10))
        gk = max(0, int(round(random.gauss(k9*gip/9, 1.5))))
        gbb = max(0, int(round(random.gauss(bb9*gip/9, 1.0))))
        gh = max(0, int(round(random.gauss(gip, 1.2))))
        ger = max(0, int(round(random.gauss(era*gip/9, 1.3))))
        ghr = 1 if random.random() < hr9*gip/9 else 0
        win = random.random() < (0.55 if tier==1 else 0.45 if tier==2 else 0.35)
        log.append({"date": d.isoformat(), "opp": TEAMS[opp_team]["abbr"], "IP":gip,
                    "K":gk,"BB":gbb,"H":gh,"ER":ger,"HR":ghr,"outs":outs,"win":win})
    return {"id":pid,"name":name,"throws":throws,"role":"SP","tier":tier,
            "season":{"GS":gs,"IP":ip,"K":k,"BB":bb,"H":h,"ER":er,"HR":hr,
                      "ERA":era,"WHIP":whip,"K9":k9,"BB9":bb9,"HR9":hr9,"oppAVG":round(oppavg,3)},
            "splitVsL":vsL,"splitVsR":vsR,"gameLog":log}

# ---- build ----
teams_out, batters, pitchers, slate = {}, {}, {}, []
for tid, t in TEAMS.items():
    teams_out[str(tid)] = {"id":tid,"abbr":t["abbr"],"name":t["name"],
                           "div":t["div"],"park":t["park"],"kRate":t["kRate"]}

# map team -> opponent starter (for bvp + logs) from slate
opp_starter = {}
opp_of = {}
for away, home, _ in SLATE:
    opp_starter[away] = STARTERS[home]; opp_starter[home] = STARTERS[away]
    opp_of[away] = home; opp_of[home] = away

for tid, roster in ROSTERS.items():
    if tid not in opp_of:
        continue
    opp_abbr = TEAMS[opp_of[tid]]["abbr"]
    st = STARTERS[opp_of[tid]]
    for order,(pid,name,bats,pos,tier) in enumerate(roster, 1):
        prof = batter_profile(pid,name,bats,pos,tier)
        prof.update({"teamId":tid,"abbr":TEAMS[tid]["abbr"],"order":order})
        prof["gameLog"] = batter_gamelog(prof, opp_abbr)
        prof["bvp"] = {str(st[0]): bvp(prof, st)}
        batters[str(pid)] = prof

for tid,(pid,name,throws,tier) in STARTERS.items():
    if tid not in opp_of:
        continue
    p = pitcher_profile(pid,name,throws,tier,opp_of[tid])
    p.update({"teamId":tid,"abbr":TEAMS[tid]["abbr"]})
    pitchers[str(pid)] = p

gamepk = 718500
for away, home, t in SLATE:
    asp, hsp = STARTERS[away], STARTERS[home]
    park = TEAMS[home]["park"]
    # toy lines
    total = round(random.uniform(7.0, 10.5)*2)/2
    slate.append({
        "gamePk": gamepk, "date": TODAY.isoformat(), "time": t, "status": "Scheduled",
        "parkId": park,
        "away": {"teamId": away, "abbr": TEAMS[away]["abbr"],
                 "probablePitcher": {"id": asp[0], "name": asp[1], "throws": asp[2]}},
        "home": {"teamId": home, "abbr": TEAMS[home]["abbr"],
                 "probablePitcher": {"id": hsp[0], "name": hsp[1], "throws": hsp[2]}},
        "lines": {"total": total, "homeRunLine": -1.5,
                  "homeML": random.choice([-150,-130,-120,110]),
                  "awayML": random.choice([-110,100,110,130])},
        "lineups": {"away": [r[0] for r in ROSTERS[away]],
                    "home": [r[0] for r in ROSTERS[home]]},
    })
    gamepk += 1

bundle = {"generated": int(time.mktime(TODAY.timetuple())) + 6*3600,
          "season": SEASON, "asOf": TODAY.isoformat(),
          "teams": teams_out, "parks": PARKS, "slate": slate,
          "batters": batters, "pitchers": pitchers}

out = os.path.join(os.path.dirname(__file__), "data", "mlb_bundle.json")
with open(out, "w") as f:
    json.dump(bundle, f, separators=(",", ":"))
with open(os.path.join(os.path.dirname(__file__), "data", "version.txt"), "w") as f:
    f.write(str(bundle["generated"]))

print(f"wrote {out}")
print(f"  teams={len(teams_out)} games={len(slate)} batters={len(batters)} pitchers={len(pitchers)}")
print(f"  size={os.path.getsize(out)/1024:.0f}KB")
