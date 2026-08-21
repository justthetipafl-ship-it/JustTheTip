"""JTT — free multi-sport Degenerates page data engine.
Reads each sport's committed odds.json + gamelogs, ranks the day's best legs
(hit-rate where the market maps to a gamelog field, else odds-implied prob),
and assembles cross-sport products -> degenerates/data/degenerates.json:
  - dailyTrain    : the day's best mixed-sport legs (capped by MAX_LEGS)
  - ladderChallenge: the best-value cross-sport combo
  - yankee        : 4 legs from 4 different sports (11 bets); doubles up if < 4 sports
No Pyramid.
"""
import os, json, glob, datetime, math

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                       # repo root (degenerates/ sits at repo root)
OUT = os.path.join(HERE, "data")
MAX_LEGS = int(os.environ.get("DEGEN_MAX_LEGS") or 8)
HIT_WINDOW = 10
MIN_GAMES = 4
OVER_LO, OVER_HI = 1.25, 3.00                      # sane value band per leg
MIN_HR = 0.55                                      # only surface legs hitting >=55% (where gradable)

SPORTS = [
    {"key": "AFL",  "dir": "AFL", "name": "AFL"},
    {"key": "AFLW", "dir": "AFLW", "name": "AFLW"},
    {"key": "NFL",  "dir": "nfl", "name": "NFL"},
    {"key": "NCAAF","dir": "ncaaf", "name": "NCAAF"},
    {"key": "NBA",  "dir": "nba", "name": "NBA"},
    {"key": "WNBA", "dir": "wnba", "name": "WNBA"},
    {"key": "NHL",  "dir": "nhl", "name": "NHL"},
    {"key": "MLB",  "dir": "mlb", "name": "MLB"},
    {"key": "EPL",  "dir": "EPL", "name": "EPL"},
    {"key": "Cricket", "dir": "cricket", "name": "Cricket"},
]

def _load(path, d=None):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return d

def _gamelogs(base):
    rows = []
    for fp in sorted(glob.glob(os.path.join(base, "gamelogs*.json"))):
        d = _load(fp, [])
        if isinstance(d, list):
            rows.extend(d)
    return rows

def _index(gl):
    byp = {}
    for r in gl:
        nm = r.get("Player") or r.get("player") or r.get("name")
        if not nm:
            continue
        byp.setdefault(nm, []).append(r)
    return byp

def _hit_rate(games, field, line):
    vals = [g.get(field) for g in games[-HIT_WINDOW:] if g.get(field) is not None]
    if len(vals) < MIN_GAMES:
        return None
    try:
        return sum(1 for v in vals if float(v) >= line) / len(vals)
    except (TypeError, ValueError):
        return None

def sport_legs(sp):
    base = os.path.join(ROOT, sp["dir"], "data")
    od = _load(os.path.join(base, "odds.json"))
    if not od:
        return []
    byp = _index(_gamelogs(base))
    raw = (od.get("lines") or []) + (od.get("alt") or [])
    best = {}   # (player, market) -> best-scored leg
    for l in raw:
        pl, mk, over = l.get("player"), l.get("market"), l.get("over")
        ln = l.get("line")
        if not pl or not mk or over is None or ln is None:
            continue
        try:
            over = float(over)
        except (TypeError, ValueError):
            continue
        if not (OVER_LO <= over <= OVER_HI):
            continue
        hr = _hit_rate(byp.get(pl, []), mk, ln)          # None if not gradable in these logs
        if hr is not None and hr < MIN_HR:
            continue
        implied = 1.0 / over
        # confidence: real hit-rate if we have it, else the market's implied probability
        conf = hr if hr is not None else implied
        # value score rewards confident legs that still pay something
        score = conf * (0.6 + 0.4 * math.log(over) / math.log(OVER_HI))
        cur = best.get((pl, mk))
        if cur is None or score > cur["score"]:
            best[(pl, mk)] = {"sport": sp["key"], "player": pl, "market": mk, "line": ln,
                              "odds": round(over, 2), "book": l.get("book") or "",
                              "hitRate": round(hr, 2) if hr is not None else None,
                              "conf": round(conf, 3), "score": score}
    legs = sorted(best.values(), key=lambda x: -x["score"])
    # keep at most 6 per sport so no single sport dominates
    return legs[:6]

def _combined(legs):
    p = 1.0
    for l in legs:
        p *= l["odds"]
    return round(p, 2)

def main():
    all_legs, by_sport = [], {}
    for sp in SPORTS:
        legs = sport_legs(sp)
        if legs:
            by_sport[sp["key"]] = legs
            all_legs.extend(legs)
    all_legs.sort(key=lambda x: -x["score"])

    # ---- Daily Train: the day's most confident mixed-sport legs, capped ----
    train = all_legs[:MAX_LEGS]

    # ---- Ladder Challenge: best-value cross-sport combo (highest combined @ solid conf) ----
    ladder_pool = [l for l in all_legs if l["conf"] >= 0.55]
    ladder_pool.sort(key=lambda x: -(x["odds"] * x["conf"]))     # value + confidence
    ladder, seen_sports = [], set()
    for l in ladder_pool:
        if len(ladder) >= 5:
            break
        ladder.append(l); seen_sports.add(l["sport"])
    # ---- Yankee: 4 legs, ideally 4 different sports ----
    yankee, used = [], set()
    for l in all_legs:
        if l["sport"] in used:
            continue
        yankee.append(l); used.add(l["sport"])
        if len(yankee) == 4:
            break
    if len(yankee) < 4:                                          # not enough sports -> allow double-ups
        for l in all_legs:
            if l in yankee:
                continue
            yankee.append(l)
            if len(yankee) == 4:
                break

    def strip(l):
        return {k: l[k] for k in ("sport", "player", "market", "line", "odds", "book", "hitRate")}

    out = {
        "updated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "sports": sorted(by_sport.keys()),
        "signupUrl": os.environ.get("DEGEN_SIGNUP_URL") or "https://dubclub.win/JustTheTipAUS/",
        "dailyTrain": {"legs": [strip(l) for l in train], "combined": _combined(train), "maxLegs": MAX_LEGS},
        "ladderChallenge": {"legs": [strip(l) for l in ladder], "combined": _combined(ladder)},
        "yankee": {"legs": [strip(l) for l in yankee], "combined": _combined(yankee), "bets": 11},
    }
    os.makedirs(OUT, exist_ok=True)
    json.dump(out, open(os.path.join(OUT, "degenerates.json"), "w"), separators=(",", ":"))
    print("Degenerates: %d sports, %d legs | train %d (@%.2f) ladder %d (@%.2f) yankee %d (@%.2f)" % (
        len(by_sport), len(all_legs), len(train), out["dailyTrain"]["combined"],
        len(ladder), out["ladderChallenge"]["combined"], len(yankee), out["yankee"]["combined"]))

if __name__ == "__main__":
    main()
