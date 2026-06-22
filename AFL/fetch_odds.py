#!/usr/bin/env python3
# ============================================================
# fetch_odds.py — AFL odds from The Odds API
# ============================================================
# Writes the odds.json contract the tool consumes:
#   { "_sample": false, "updated": "...", "source": "...",
#     "lines":     [ {player,market,line,over,under,book} ],   # standard O/U + anytime + 2+ goal
#     "alt":       [ {player,market,line,over,book} ],          # alternate ladders (e.g. disposals 20+/25+/30+)
#     "matchOdds": [ {home,away,commence,h2h,line,total} ] }
#
# RESILIENT MARKET DISCOVERY: AFL player-prop keys aren't fully published and an
# invalid key 422s the whole call, so each market is requested on its own. A 422
# marks that key unavailable (skipped for the rest of the run) instead of aborting.
# The run log prints which markets actually returned, so the catalog can be trimmed.
#
# Cost (The Odds API): events call = 1 credit; each event-odds call = markets x
# regions, and only markets that RETURN DATA count. Empty/invalid markets are free.
# Non-destructive: a section that comes back empty keeps its previous value; atomic write.
# ============================================================
import os, sys, json, time, datetime, urllib.request, urllib.parse, urllib.error

API_KEY = os.environ.get("ODDS_API_KEY", "").strip()
OUT     = os.environ.get("AFL_ODDS_OUT", "AFL/data/odds.json")
SPORT   = os.environ.get("ODDS_SPORT", "aussierules_afl")
REGION  = os.environ.get("ODDS_REGION", "au")
BASE    = "https://api.the-odds-api.com/v4"

# Player markets. kind:
#   ou        = over/under at a posted point          -> lines[]
#   anytime   = yes/no scorer -> over @ line 0.5       -> lines[] (market 'goals')
#   milestone = over-only X+ lines, keep the 2+ line   -> lines[] (market 'goalsx', short-price fallback)
#   altline   = over-only X+ lines, keep the whole ladder -> alt[]
# Several keys are best-guess candidates; invalid ones 422 once and are skipped.
PLAYER_MARKETS = [
    # Exact AFL player-prop market keys offered by The Odds API (AU books). The extra
    # stat markets use the "_over" alt-line form (X+ ladders); only disposals and AFL
    # fantasy expose a true two-way O/U. The bare player_marks / player_tackles / etc.
    # O/U keys do NOT exist for AFL — the "_over" variants are the ones that resolve.
    ("player_disposals",              "disposals",  "ou"),         # Disposals O/U (two-way)
    ("player_disposals_over",         "disposals",  "altline"),    # Disposals X+ ladder
    ("player_kicks_over",             "kicks",      "altline"),    # Kicks X+
    ("player_handballs_over",         "handballs",  "altline"),    # Handballs X+
    ("player_marks_over",             "marks",      "altline"),    # Marks X+
    ("player_tackles_over",           "tackles",    "altline"),    # Tackles X+
    ("player_clearances_over",        "clearances", "altline"),    # Clearances X+
    ("player_goals_scored_over",      "goalsx",     "milestone"),  # Goals 2+/3+/4+/5+
    ("player_goal_scorer_anytime",    "goals",      "anytime"),    # Anytime goal (1+)
    ("player_afl_fantasy_points",     "dreamteam",  "ou"),         # Fantasy O/U (two-way)
    ("player_afl_fantasy_points_over","dreamteam",  "altline"),    # Fantasy X+ ladder
]

MATCH_MARKETS = "h2h,spreads,totals"   # featured, one call covers every game

TEAM_NORM = {
    "Adelaide Crows": "Adelaide", "Brisbane Lions": "Brisbane",
    "Carlton Blues": "Carlton", "Collingwood Magpies": "Collingwood",
    "Essendon Bombers": "Essendon", "Fremantle Dockers": "Fremantle",
    "Geelong Cats": "Geelong", "Gold Coast Suns": "Gold Coast",
    "Greater Western Sydney Giants": "Greater Western Sydney",
    "GWS Giants": "Greater Western Sydney", "Hawthorn Hawks": "Hawthorn",
    "Melbourne Demons": "Melbourne", "North Melbourne Kangaroos": "North Melbourne",
    "Port Adelaide Power": "Port Adelaide", "Richmond Tigers": "Richmond",
    "St Kilda Saints": "St Kilda", "Sydney Swans": "Sydney",
    "West Coast Eagles": "West Coast", "Western Bulldogs": "Western Bulldogs",
}
def norm_team(x):
    x = (x or "").strip()
    if x in TEAM_NORM:
        return TEAM_NORM[x]
    for full, canon in TEAM_NORM.items():
        if x == canon or x.startswith(canon):
            return canon
    return x

BOOK_PRIORITY = ["sportsbet", "ladbrokes_au", "tab", "pointsbetau",
                 "betr_au", "unibet", "betfair_ex_au"]


def _get(url, tries=3, skip_codes=()):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "jtt-afl-odds/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8")), dict(r.headers)
        except urllib.error.HTTPError as e:
            if e.code in skip_codes:                 # e.g. 422 invalid market -> caller skips
                return None, dict(e.headers or {})
            body = e.read().decode("utf-8", "ignore")[:200]
            print(f"[odds] HTTP {e.code} on {url.split('?')[0]}: {body}")
            if e.code in (401, 422):
                raise
            last = e
        except Exception as e:
            last = e
        time.sleep(1.2 * (i + 1))
    raise last


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _upd(d, key, rank, val):
    prev = d.get(key)
    if prev is None or rank < prev["_rank"]:
        d[key] = {**val, "_rank": rank}


def fetch_match_odds():
    url = (f"{BASE}/sports/{SPORT}/odds?apiKey={API_KEY}"
           f"&regions={REGION}&markets={MATCH_MARKETS}&oddsFormat=decimal")
    try:
        data, hdr = _get(url)
    except Exception as e:
        print(f"[odds] match-odds fetch failed: {e}")
        return []
    print(f"[odds] match-odds: {len(data)} events; quota remaining: {hdr.get('x-requests-remaining','?')}")
    out = []
    for ev in data:
        home, away = norm_team(ev.get("home_team")), norm_team(ev.get("away_team"))
        if not home or not away:
            continue
        rh, ra = ev.get("home_team", ""), ev.get("away_team", "")
        h2h = line = total = None
        h2h_r = line_r = total_r = 99
        for bk in ev.get("bookmakers", []):
            bkey = bk.get("key", "")
            rank = BOOK_PRIORITY.index(bkey) if bkey in BOOK_PRIORITY else 99
            for m in bk.get("markets", []):
                mk, outs = m.get("key", ""), m.get("outcomes", [])
                if mk == "h2h" and rank < h2h_r:
                    hp = next((_num(o.get("price")) for o in outs if o.get("name") == rh), None)
                    ap = next((_num(o.get("price")) for o in outs if o.get("name") == ra), None)
                    if hp and ap:
                        h2h, h2h_r = {"home": hp, "away": ap, "book": bkey}, rank
                elif mk == "spreads" and rank < line_r:
                    ho = next((o for o in outs if o.get("name") == rh), None)
                    ao = next((o for o in outs if o.get("name") == ra), None)
                    if ho and ao:
                        line, line_r = {"home": _num(ho.get("point")), "homeOdds": _num(ho.get("price")),
                                        "away": _num(ao.get("point")), "awayOdds": _num(ao.get("price")), "book": bkey}, rank
                elif mk == "totals" and rank < total_r:
                    ov = next((o for o in outs if (o.get("name") or "").lower() == "over"), None)
                    un = next((o for o in outs if (o.get("name") or "").lower() == "under"), None)
                    if ov and un:
                        total, total_r = {"points": _num(ov.get("point")), "over": _num(ov.get("price")),
                                          "under": _num(un.get("price")), "book": bkey}, rank
        if h2h or line or total:
            row = {"home": home, "away": away, "commence": ev.get("commence_time", "")}
            if h2h:   row["h2h"] = h2h
            if line:  row["line"] = line
            if total: row["total"] = total
            out.append(row)
    print(f"[odds] resolved match odds for {len(out)} games")
    return out


def fetch_props(events):
    """Per-market, per-event so an invalid AFL key 422s once and is skipped."""
    best, altbest, bk_acc = {}, {}, {}
    seen, unavailable, books = set(), set(), set()
    for ev in events:
        eid = ev.get("id")
        if not eid:
            continue
        for raw, internal, kind in PLAYER_MARKETS:
            if raw in unavailable:
                continue
            url = (f"{BASE}/sports/{SPORT}/events/{eid}/odds?apiKey={API_KEY}"
                   f"&regions={REGION}&markets={raw}&oddsFormat=decimal")
            try:
                data, _ = _get(url, skip_codes=(422,))
            except Exception as e:
                print(f"[odds]  {raw} @ {eid}: {e}")
                continue
            if data is None:                 # 422 -> not an AFL market, stop trying it
                unavailable.add(raw)
                continue
            for bk in data.get("bookmakers", []):
                bkey = bk.get("key", "")
                books.add(bkey)
                rank = BOOK_PRIORITY.index(bkey) if bkey in BOOK_PRIORITY else 99
                for m in bk.get("markets", []):
                    if m.get("key") != raw:
                        continue
                    seen.add(raw)
                    outs = m.get("outcomes", [])
                    if kind == "anytime":
                        for o in outs:
                            player = (o.get("description") or "").strip()
                            if not player:
                                continue
                            _upd(best, (player, internal), rank,
                                 {"line": 0.5, "over": _num(o.get("price")), "under": None, "book": bkey})
                            bk_acc[(player, internal, 0.5, bkey)] = {"over": _num(o.get("price")), "under": None}
                    elif kind in ("milestone", "altline"):
                        permp = {}
                        for o in outs:
                            player = (o.get("description") or "").strip()
                            pt, pr = _num(o.get("point")), _num(o.get("price"))
                            if not player or pt is None or pr is None:
                                continue
                            permp.setdefault(player, []).append((pt, pr))
                        for player, lst in permp.items():
                            if kind == "milestone":
                                ge = [x for x in lst if x[0] >= 1.5]
                                pt, pr = min(ge, key=lambda x: x[0]) if ge else min(lst, key=lambda x: x[0])
                                _upd(best, (player, internal), rank,
                                     {"line": pt, "over": pr, "under": None, "book": bkey})
                                bk_acc[(player, internal, pt, bkey)] = {"over": pr, "under": None}
                            else:  # altline -> keep the whole ladder
                                for pt, pr in lst:
                                    _upd(altbest, (player, internal, pt), rank, {"over": pr, "book": bkey})
                                    bk_acc[(player, internal, pt, bkey)] = {"over": pr, "under": None}
                    else:  # ou
                        grp = {}
                        for o in outs:
                            player = (o.get("description") or o.get("name") or "").strip()
                            if not player or player.lower() in ("over", "under", "yes", "no"):
                                if not o.get("description"):
                                    continue
                                player = o.get("description").strip()
                            g = grp.setdefault(player, {"line": None, "over": None, "under": None})
                            if g["line"] is None:
                                g["line"] = _num(o.get("point"))
                            side = (o.get("name") or "").lower()
                            if "over" in side:
                                g["over"] = _num(o.get("price"))
                            elif "under" in side:
                                g["under"] = _num(o.get("price"))
                        for player, g in grp.items():
                            if g["line"] is None:
                                continue
                            _upd(best, (player, internal), rank, {**g, "book": bkey})
                            bk_acc[(player, internal, g["line"], bkey)] = {"over": g["over"], "under": g["under"]}
            time.sleep(0.05)
    if unavailable:
        print(f"[odds] markets unavailable for AFL (skipped): {sorted(unavailable)}")
    print(f"[odds] markets returning data: {sorted(seen)}")
    print(f"[odds] books seen: {sorted(books)}")
    lines = [{"player": p, "market": m, "line": v["line"], "over": v["over"],
              "under": v["under"], "book": v["book"]} for (p, m), v in best.items()]
    alt = [{"player": p, "market": m, "line": pt, "over": v["over"], "book": v["book"]}
           for (p, m, pt), v in altbest.items()]
    bookrows = [{"player": p, "market": m, "line": ln, "book": bk,
                 "over": v["over"], "under": v["under"]}
                for (p, m, ln, bk), v in bk_acc.items()]
    print(f"[odds] per-book rows: {len(bookrows)}")
    return lines, alt, bookrows


def main():
    if not API_KEY:
        print("[odds] ODDS_API_KEY not set — skipping (existing odds.json kept)")
        return 0

    existing = {}
    if os.path.exists(OUT):
        try:
            existing = json.load(open(OUT))
        except Exception:
            existing = {}

    match_odds = fetch_match_odds()

    lines, alt, bookrows = [], [], []
    ev_url = f"{BASE}/sports/{SPORT}/events?apiKey={API_KEY}&regions={REGION}"
    try:
        events, hdr = _get(ev_url)
        print(f"[odds] {len(events)} events; quota remaining: {hdr.get('x-requests-remaining','?')}")
    except Exception as e:
        print(f"[odds] events fetch failed: {e}")
        events = []
    if events:
        lines, alt, bookrows = fetch_props(events)

    if not lines:
        lines = existing.get("lines", [])
        if lines: print("[odds] no fresh props — preserving existing player lines")
    if not alt:
        alt = existing.get("alt", [])
        if alt: print("[odds] no fresh alt lines — preserving existing alt ladder")
    if not bookrows:
        bookrows = existing.get("books", [])
        if bookrows: print("[odds] no fresh per-book rows — preserving existing books")
    if not match_odds:
        match_odds = existing.get("matchOdds", [])
        if match_odds: print("[odds] no fresh match odds — preserving existing match odds")
    if not lines and not alt and not match_odds:
        print("[odds] nothing resolved — keeping existing odds.json")
        return 0

    out = {
        "_sample": False,
        "updated": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ"),
        "source": "the-odds-api",
        "lines": sorted(lines, key=lambda x: (x["market"], -(x["line"] or 0))),
        "alt": sorted(alt, key=lambda x: (x["market"], x["player"], x["line"] or 0)),
        "books": sorted(bookrows, key=lambda x: (x["market"], x["player"], x["line"] or 0, x["book"])),
        "matchOdds": match_odds,
    }
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    tmp = OUT + ".tmp"
    json.dump(out, open(tmp, "w"))
    os.replace(tmp, OUT)
    by_mkt = {}
    for l in lines:
        by_mkt[l["market"]] = by_mkt.get(l["market"], 0) + 1
    print(f"[odds] wrote {OUT}: {len(lines)} lines {by_mkt}, {len(alt)} alt lines, {len(match_odds)} match games")
    return 0


if __name__ == "__main__":
    sys.exit(main())
