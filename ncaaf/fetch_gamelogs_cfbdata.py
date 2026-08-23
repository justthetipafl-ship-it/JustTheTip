#!/usr/bin/env python3
"""
fetch_gamelogs_cfbdata.py — NCAAF player gamelogs from the sportsdataverse ESPN player_box
release (bulk, keyless, ESPN-keyed = matches teams.json / logos / the fixture). One load
gets the whole season, replacing the slow per-game ESPN summary backfill.

Source (keyless):
  player_box: sportsdataverse-data releases, tag espn_cfb_player_box, player_box_{yr}.csv.gz
  schedule  : same repo, tag espn_cfb_schedules, cfb_schedule_{yr}.csv.gz  (for week/opp/home)

Writes ncaaf/data/gamelogs_{yr}.json in the tool's schema. Non-destructive on a bad fetch.
"""
import os, sys, gzip, csv, io, json, urllib.request, collections

SEASON = int(os.environ.get("NCAAF_SEASON") or 2025)
OUT    = os.environ.get("NCAAF_GAMELOGS", "ncaaf/data/gamelogs_%d.json" % SEASON)
PB_URL = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_cfb_player_box/player_box_%d.csv.gz" % SEASON
SC_URL = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_cfb_schedules/cfb_schedule_%d.csv.gz" % SEASON

def get_csv(url):
    with urllib.request.urlopen(url, timeout=90) as r:
        raw = r.read()
    return list(csv.DictReader(io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(raw)), encoding="utf-8")))

def _i(v):
    try: return int(float(v))
    except: return 0
def _split(v):
    # "11/17" -> (11,17)
    if v and "/" in str(v):
        a, b = str(v).split("/")[:2]
        return _i(a), _i(b)
    return 0, 0

print("[ncaaf-gamelogs] loading player_box + schedule for %d ..." % SEASON)
try:
    pb = get_csv(PB_URL); sch = get_csv(SC_URL)
except Exception as e:
    print("[ncaaf-gamelogs] fetch failed (%s) -> leaving gamelogs untouched" % str(e)[:120]); sys.exit(0)
if not pb or not sch:
    print("[ncaaf-gamelogs] empty source -> leaving gamelogs untouched"); sys.exit(0)

# game_id -> week / home / away
ginfo = {}
for g in sch:
    gid = str(g.get("game_id"))
    ginfo[gid] = {"week": _i(g.get("week")), "home": str(g.get("home_id")), "away": str(g.get("away_id"))}
print("[ncaaf-gamelogs] %d games in schedule, %d player-box rows" % (len(ginfo), len(pb)))

# group category rows by (game_id, athlete_id)
by_pg = collections.defaultdict(dict)   # (gid, aid) -> {category: row}
meta  = {}
for r in pb:
    gid = str(r.get("game_id")); aid = str(r.get("athlete_id")); cat = r.get("category")
    if not gid or not aid or not cat: continue
    by_pg[(gid, aid)][cat] = r
    meta[(gid, aid)] = r   # any row for name/team

def S(row, n):  # stat_n
    return (row.get("stat_%d" % n) or "").strip() if row else ""

out = []
for (gid, aid), cats in by_pg.items():
    gi = ginfo.get(gid)
    if not gi: continue
    base = meta[(gid, aid)]
    team = str(base.get("team_id"))
    opp  = gi["away"] if team == gi["home"] else gi["home"]
    home = 1 if team == gi["home"] else 0

    p = cats.get("passing"); ru = cats.get("rushing"); re = cats.get("receiving"); de = cats.get("defensive")
    passComp, passAtt = _split(S(p, 1))
    passYds = _i(S(p, 2)); passTds = _i(S(p, 4)); passInt = _i(S(p, 5))
    rushAtt = _i(S(ru, 1)); rushYds = _i(S(ru, 2)); rushTds = _i(S(ru, 4)); longRush = _i(S(ru, 5))
    rec = _i(S(re, 1)); recYds = _i(S(re, 2)); recTds = _i(S(re, 4)); longRec = _i(S(re, 5))
    sacks = _i(S(de, 3))
    totalTds = passTds + rushTds + recTds
    anytimeTd = rushTds + recTds
    fanPts = round(passYds/25.0 + passTds*4 - passInt*2 + rushYds/10.0 + rushTds*6 + recYds/10.0 + recTds*6 + rec*0.5, 2)

    out.append({
        "MatchId": gid, "Player": base.get("athlete_name"), "PlayerId": aid,
        "Team": team, "Opp": opp, "Week": gi["week"], "Year": SEASON, "home": home,
        "passYds": passYds, "passTds": passTds, "passInt": passInt, "passComp": passComp, "passAtt": passAtt,
        "rushYds": rushYds, "rushTds": rushTds, "rushAtt": rushAtt, "longRush": longRush,
        "recYds": recYds, "recTds": recTds, "receptions": rec, "longRec": longRec,
        "rushRecYds": rushYds + recYds, "sacks": sacks, "totalTds": totalTds, "anytimeTd": anytimeTd,
        "fanPts": fanPts,
        # advanced fields the ESPN box doesn't carry (build derives these) -> 0 for now
        "expRec": 0, "expRush": 0, "glCarry_g": 0, "rzCarry_g": 0, "rzTgt_g": 0,
        "touchShare": 0, "longComp": 0, "vsFcs": 0,
    })

games = len(set(r["MatchId"] for r in out))
print("[ncaaf-gamelogs] built %d player-game rows across %d games" % (len(out), games))
if games < 100:
    print("[ncaaf-gamelogs] suspiciously few games -> leaving gamelogs untouched"); sys.exit(0)

tmp = OUT + ".tmp"
json.dump(out, open(tmp, "w"), separators=(",", ":"))
os.replace(tmp, OUT)
print("[ncaaf-gamelogs] wrote %s (%d rows, %d games)" % (OUT, len(out), games))
