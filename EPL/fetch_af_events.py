"""JTT EPL - per-fixture goal/card/sub events from API-Football (/fixtures/events).
Caches raw events incrementally in af_events_raw.json (never re-fetches a done fixture),
then aggregates per team into events.json for the shell:
  goal-timing 15-min buckets (scored/conceded), opens-scoring / concedes-first / carded-first %,
  avg first-goal minute, first / last / late (>=76') scorers, first-to-be-carded, hooked-early subs.
Reads APIFOOTBALL_KEY. Season(s) via EPL_EVENTS_SEASONS (default '2025,2026'); per-run cap EPL_EVENTS_MAX.
"""
import os, json, time, urllib.request, datetime

BASE = "https://v3.football.api-sports.io"
LEAGUE = 39
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
PACE = float(os.environ.get("APIFOOTBALL_PACE") or 1.0)
MAX_NEW = int(os.environ.get("EPL_EVENTS_MAX") or 300)
SEASONS = [s.strip() for s in (os.environ.get("EPL_EVENTS_SEASONS") or "2025,2026").split(",") if s.strip()]

def af(path):
    req = urllib.request.Request(BASE + path, headers={"x-apisports-key": os.environ["APIFOOTBALL_KEY"], "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def _load(p, d):
    try:
        return json.load(open(p))
    except Exception:
        return d

def _bucket(mn):
    return 0 if mn <= 15 else 1 if mn <= 30 else 2 if mn <= 45 else 3 if mn <= 60 else 4 if mn <= 75 else 5

def main():
    if not os.environ.get("APIFOOTBALL_KEY"):
        raise SystemExit("APIFOOTBALL_KEY not set")
    rawp = os.path.join(DATA, "af_events_raw.json")
    raw = _load(rawp, {})

    fixtures = []
    for s in SEASONS:
        try:
            resp = af("/fixtures?league=%d&season=%s" % (LEAGUE, s)).get("response", [])
        except Exception as e:
            print("fixtures %s failed: %s" % (s, e)); resp = []
        for f in resp:
            if (f["fixture"]["status"]["short"] or "") in ("FT", "AET", "PEN"):
                fixtures.append(f)
        time.sleep(PACE)

    new = 0
    for f in fixtures:
        fid = str(f["fixture"]["id"])
        if fid in raw:
            continue
        if new >= MAX_NEW:
            break
        try:
            ev = af("/fixtures/events?fixture=%s" % fid).get("response", [])
        except Exception as e:
            print("events %s failed: %s" % (fid, e)); continue
        goals, cards, subs = [], [], []
        for e in ev:
            t = e.get("type") or ""
            tm = (e.get("time") or {})
            mn = (tm.get("elapsed") or 0) + (tm.get("extra") or 0)
            team = (e.get("team") or {}).get("name")
            pl = (e.get("player") or {}).get("name")
            det = e.get("detail") or ""
            if t == "Goal" and "Missed" not in det:
                goals.append({"min": mn, "team": team, "player": pl})
            elif t == "Card":
                cards.append({"min": mn, "team": team, "player": pl, "kind": "R" if "Red" in det else "Y"})
            elif t == "subst":
                subs.append({"min": mn, "team": team, "off": (e.get("assist") or {}).get("name"), "on": pl})
        raw[fid] = {"home": f["teams"]["home"]["name"], "away": f["teams"]["away"]["name"],
                    "date": (f["fixture"]["date"] or "")[:10], "goals": goals, "cards": cards, "subs": subs}
        new += 1
        time.sleep(PACE)
    json.dump(raw, open(rawp, "w"), separators=(",", ":"))

    # ---- aggregate per team ----
    agg = {}
    def T(team):
        if team not in agg:
            agg[team] = {"matches": 0, "scored": [0]*6, "conceded": [0]*6, "opens": 0, "concFirst": 0,
                         "no1h": 0, "cardFirst": 0, "fsMin": 0, "fsN": 0, "fcMin": 0, "fcN": 0,
                         "firstScorers": {}, "lastScorers": {}, "lateScorers": {}, "firstCarded": {}, "subOff": {}}
        return agg[team]
    def _inc(d, k):
        if k: d[k] = d.get(k, 0) + 1

    for fid, m in raw.items():
        h, a = m["home"], m["away"]
        if not h or not a: continue
        T(h)["matches"] += 1; T(a)["matches"] += 1
        gs = sorted([g for g in m["goals"] if g.get("min") is not None and g.get("team") in (h, a)], key=lambda x: x["min"])
        scored1h = {h: False, a: False}
        firstMin = {}
        for g in gs:
            tm = g["team"]; opp = a if tm == h else h; b = _bucket(g["min"])
            T(tm)["scored"][b] += 1; T(opp)["conceded"][b] += 1
            if g["min"] <= 45: scored1h[tm] = True
            if tm not in firstMin: firstMin[tm] = g["min"]
            if g["min"] >= 76: _inc(T(tm)["lateScorers"], g.get("player"))
        # per-team first scored / conceded minute
        for tm in (h, a):
            if tm in firstMin: T(tm)["fsMin"] += firstMin[tm]; T(tm)["fsN"] += 1
            opp = a if tm == h else h
            if opp in firstMin: T(tm)["fcMin"] += firstMin[opp]; T(tm)["fcN"] += 1
            if not scored1h[tm]: T(tm)["no1h"] += 1
        # match first / last goal
        if gs:
            fg = gs[0]; T(fg["team"])["opens"] += 1; T(a if fg["team"] == h else h)["concFirst"] += 1
            _inc(T(fg["team"])["firstScorers"], fg.get("player"))
            lg = gs[-1]; _inc(T(lg["team"])["lastScorers"], lg.get("player"))
        # first card
        cs = sorted([c for c in m["cards"] if c.get("min") is not None and c.get("team") in (h, a)], key=lambda x: x["min"])
        if cs:
            fc = cs[0]; T(fc["team"])["cardFirst"] += 1; _inc(T(fc["team"])["firstCarded"], fc.get("player"))
        # hooked-early subs
        for su in m["subs"]:
            if su.get("off") and su.get("team") in (h, a) and su.get("min") is not None:
                d = T(su["team"])["subOff"].setdefault(su["off"], {"n": 0, "minSum": 0})
                d["n"] += 1; d["minSum"] += su["min"]

    def _top(d, k=5):
        return [[p, c] for p, c in sorted(d.items(), key=lambda x: -x[1])[:k]]
    out = {}
    for team, x in agg.items():
        n = x["matches"] or 1
        hooked = []
        for pl, d in x["subOff"].items():
            avg = d["minSum"] / d["n"]
            if d["n"] >= 3 and avg <= 72:
                hooked.append({"player": pl, "offAvg": round(avg), "n": d["n"]})
        hooked.sort(key=lambda z: -z["n"])
        out[team] = {
            "matches": x["matches"],
            "timing": {"scored": x["scored"], "conceded": x["conceded"]},
            "opensScoring": round(x["opens"] / n, 2), "concedesFirst": round(x["concFirst"] / n, 2),
            "no1hGoal": round(x["no1h"] / n, 2), "cardedFirst": round(x["cardFirst"] / n, 2),
            "avgFirstScored": round(x["fsMin"] / x["fsN"]) if x["fsN"] else None,
            "avgFirstConceded": round(x["fcMin"] / x["fcN"]) if x["fcN"] else None,
            "firstScorers": _top(x["firstScorers"]), "lastScorers": _top(x["lastScorers"]),
            "lateScorers": _top(x["lateScorers"]), "firstCarded": _top(x["firstCarded"]),
            "hooked": hooked[:6],
        }
    json.dump({"updated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
               "matches": len(raw), "byTeam": out},
              open(os.path.join(DATA, "events.json"), "w"), separators=(",", ":"))
    print("EPL events: %d fixtures cached (+%d new this run), %d teams aggregated" % (len(raw), new, len(out)))

if __name__ == "__main__":
    main()
