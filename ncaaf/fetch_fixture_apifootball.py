#!/usr/bin/env python3
"""
fetch_fixture_apifootball.py — NCAAF fixture from the API-Football *American Football*
API, mapped to the ESPN team IDs the tool already uses (teams.json / logo CDN), so the
shell renders it unchanged. Writes the same fixture.json row shape build_ncaaf_data.py
produces (home/away = ESPN ids). Non-destructive: only writes on a good fetch.

Auth : header x-apisports-key = your api-sports.io key (repo secret APIFOOTBALL_KEY).
League: 2 = NCAA in the American Football API. Override with NCAAF_AF_LEAGUE if needed.
Season: NCAAF_SEASON (defaults to the current calendar year).
"""
import os, re, json, sys, datetime, urllib.request

KEY    = os.environ.get("NCAAF_APIFOOTBALL_KEY") or os.environ.get("APIFOOTBALL_KEY") or ""
SEASON = int(os.environ.get("NCAAF_SEASON") or datetime.datetime.utcnow().year)   # empty env -> current year
LEAGUE = int(os.environ.get("NCAAF_AF_LEAGUE") or 2)          # 2 = NCAA; empty env -> 2
BASE   = "https://v1.american-football.api-sports.io"
OUT    = os.environ.get("NCAAF_FIXTURE", "ncaaf/data/fixture.json")
TEAMS  = os.environ.get("NCAAF_TEAMS", "ncaaf/data/teams.json")

if not KEY:
    raise SystemExit("APIFOOTBALL_KEY (or NCAAF_APIFOOTBALL_KEY) not set — add it as a repo secret")

def _get(path):
    req = urllib.request.Request(BASE + path, headers={"x-apisports-key": KEY, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def _norm(s):
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\b(university|univ|the)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()

# ESPN id map from teams.json (displayName / school / abbr, all normalised)
teams = json.load(open(TEAMS))
tlist = teams if isinstance(teams, list) else list(teams.values())
name2id = {}
for t in tlist:
    tid = str(t.get("team"))
    for field in ("displayName", "school", "abbr"):
        v = t.get(field)
        if v:
            name2id.setdefault(_norm(v), tid)

def resolve(name):
    n = _norm(name)
    if n in name2id:
        return name2id[n]
    parts = n.split()                                  # drop mascot: "alabama crimson tide" -> "alabama crimson" -> "alabama"
    for cut in range(len(parts) - 1, 0, -1):
        cand = " ".join(parts[:cut])
        if cand in name2id:
            return name2id[cand]
    return None

def _wknum(w):
    m = re.search(r"(\d+)", str(w or ""))
    return int(m.group(1)) if m else 0

j = _get("/games?league=%d&season=%d" % (LEAGUE, SEASON))
games = j.get("response") or []
print("[ncaaf-fixture] season=%d league=%d -> %d games from API-Football" % (SEASON, LEAGUE, len(games)))
if not games:
    print("[ncaaf-fixture] no games (check the key/plan covers NCAA + season, or the league id). errors:", j.get("errors"))
    sys.exit(0)

rows, unmatched = [], set()
for g in games:
    gm = g.get("game") or {}
    tm = g.get("teams") or {}
    st = ((gm.get("status") or {}).get("short") or "").upper()
    dt = (gm.get("date") or {})
    iso = ""
    if dt.get("date"):
        iso = dt["date"] + ("T" + dt["time"] + ":00Z" if dt.get("time") else "T00:00:00Z")
    hn = (tm.get("home") or {}).get("name"); an = (tm.get("away") or {}).get("name")
    hid, aid = resolve(hn), resolve(an)
    if not hid: unmatched.add(hn)
    if not aid: unmatched.add(an)
    if not hid or not aid:
        continue
    finished = st in ("FT", "AOT", "AET", "FINAL")
    rows.append({
        "home": hid, "away": aid, "homeName": hn, "awayName": an,
        "week": _wknum(gm.get("week")), "season": SEASON,
        "utc": iso, "date": iso[:10], "venue": (gm.get("venue") or {}).get("name") or "TBC",
        "neutral": 0, "confGame": 0, "spread": None, "total": None, "hasLine": 0,
        "homeRank": None, "awayRank": None, "p4": 0, "fbsVfbs": 1, "realistic": 1,
        "_finished": finished,
    })

if unmatched:
    print("[ncaaf-fixture] %d teams unmatched to ESPN ids (add to teams.json aliases if FBS):" % len(unmatched))
    for n in sorted(x for x in unmatched if x)[:40]:
        print("    -", n)

# next unplayed week = earliest week that still has an unfinished, today-or-later game
today = datetime.date.today().isoformat()
upcoming = [r for r in rows if not r["_finished"] and r["date"] >= today and r["week"]]
if upcoming:
    nxt = min(r["week"] for r in upcoming)
    fx = [r for r in rows if r["week"] == nxt and not r["_finished"]]
else:
    fx = []
for r in fx:
    r.pop("_finished", None)

if not fx:
    print("[ncaaf-fixture] no upcoming unplayed games — leaving fixture untouched")
    sys.exit(0)

fx.sort(key=lambda r: r["utc"])
tmp = OUT + ".tmp"
json.dump(fx, open(tmp, "w"), separators=(",", ":"))
os.replace(tmp, OUT)
print("[ncaaf-fixture] wrote %s — %d games (week %d)" % (OUT, len(fx), fx[0]["week"]))
