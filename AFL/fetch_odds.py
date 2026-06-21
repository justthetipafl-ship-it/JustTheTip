#!/usr/bin/env python3
# ============================================================
# fetch_odds.py — AFL player-prop lines from The Odds API
# ============================================================
# Pulls per-event player props (region=au) and writes them into the
# odds.json contract the tool already consumes:
#   {"_sample": false, "updated": "...", "lines": [
#       {"player","market","line","over","under","book"} ]}
#
#  * Key from env ODDS_API_KEY (GitHub secret) — no key => clean skip,
#    existing odds.json is left untouched (non-destructive).
#  * Writes atomically (tmp -> replace) so a half-write can't corrupt
#    the live file, and never overwrites with an empty result.
#  * Self-diagnosing: logs the market + bookmaker keys it actually sees,
#    so coverage can be confirmed and MARKETS expanded with confidence.
#
# Cost model (The Odds API): events call = 1 credit; each event-odds call
# = [markets] x [regions]. Default 2 markets x 1 region x ~9 games ≈ 19
# credits/refresh — trivial against the Business tier's 200k/mo.
# ============================================================
import os, sys, json, time, datetime, urllib.request, urllib.parse, urllib.error

API_KEY = os.environ.get("ODDS_API_KEY", "").strip()
OUT     = os.environ.get("AFL_ODDS_OUT", "AFL/data/odds.json")
SPORT   = os.environ.get("ODDS_SPORT", "aussierules_afl")
REGION  = os.environ.get("ODDS_REGION", "au")
BASE    = "https://api.the-odds-api.com/v4"

# Odds API market key -> tool internal market key.
# player_disposals is the driver (Green Lights / Death Riders / Multi gate on it).
# player_goal_scorer_anytime is a yes/no market -> mapped to goals @ line 0.5
# (scoring 1+ goal == over 0.5), giving Snags an inline goal price.
MARKETS = {
    "player_disposals":          "disposals",
    "player_goal_scorer_anytime":"goals",
    "player_goals_scored_over":  "goalsx",   # milestone X+ lines (2+, 3+ …) for short-priced scorers
}
ANYTIME_MARKETS = {"player_goal_scorer_anytime"}   # yes/no -> over @ line 0.5
MILESTONE_MARKETS = {"player_goals_scored_over"}    # over-only, multiple points/player -> pick the 2+ line

# Match-level (featured) markets — one call covers every event.
#   h2h = head-to-head win odds, spreads = line/handicap, totals = total points
# Cost = [markets] x [regions] = 3 x 1 = 3 credits per refresh (all games).
MATCH_MARKETS = "h2h,spreads,totals"

# The Odds API team names -> tool canonical names (mirror of the R pipeline map)
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
    # tolerate "<City> <Nickname>" by stripping a known nickname suffix
    for full, canon in TEAM_NORM.items():
        if x == canon or x.startswith(canon):
            return canon
    return x

# Preferred book per player/market (first available wins). Unknown keys still
# used, just at lowest priority. Exact keys are confirmed by the run log.
BOOK_PRIORITY = ["sportsbet", "ladbrokes_au", "tab", "pointsbetau",
                 "betr_au", "unibet", "betfair_ex_au"]


def _get(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "jtt-afl-odds/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8")), dict(r.headers)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")[:300]
            print(f"[odds] HTTP {e.code} on {url.split('?')[0]}: {body}")
            if e.code in (401, 422):   # bad key / bad market — don't retry
                raise
            last = e
        except Exception as e:
            last = e
        time.sleep(1.5 * (i + 1))
    raise last


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_match_odds():
    """One featured-markets call -> list of per-match h2h/line/total odds,
    each market resolved to the highest-priority available book."""
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
        home = norm_team(ev.get("home_team"))
        away = norm_team(ev.get("away_team"))
        if not home or not away:
            continue
        rh, ra = ev.get("home_team", ""), ev.get("away_team", "")
        h2h = line = total = None
        h2h_rank = line_rank = total_rank = 99
        for bk in ev.get("bookmakers", []):
            bkey = bk.get("key", "")
            rank = BOOK_PRIORITY.index(bkey) if bkey in BOOK_PRIORITY else 99
            for m in bk.get("markets", []):
                mk = m.get("key", "")
                outs = m.get("outcomes", [])
                if mk == "h2h" and rank < h2h_rank:
                    hp = next((_num(o.get("price")) for o in outs if o.get("name") == rh), None)
                    ap = next((_num(o.get("price")) for o in outs if o.get("name") == ra), None)
                    if hp and ap:
                        h2h = {"home": hp, "away": ap, "book": bkey}; h2h_rank = rank
                elif mk == "spreads" and rank < line_rank:
                    ho = next((o for o in outs if o.get("name") == rh), None)
                    ao = next((o for o in outs if o.get("name") == ra), None)
                    if ho and ao:
                        line = {"home": _num(ho.get("point")), "homeOdds": _num(ho.get("price")),
                                "away": _num(ao.get("point")), "awayOdds": _num(ao.get("price")),
                                "book": bkey}; line_rank = rank
                elif mk == "totals" and rank < total_rank:
                    ov = next((o for o in outs if (o.get("name") or "").lower() == "over"), None)
                    un = next((o for o in outs if (o.get("name") or "").lower() == "under"), None)
                    if ov and un:
                        total = {"points": _num(ov.get("point")), "over": _num(ov.get("price")),
                                 "under": _num(un.get("price")), "book": bkey}; total_rank = rank
        if h2h or line or total:
            row = {"home": home, "away": away, "commence": ev.get("commence_time", "")}
            if h2h:   row["h2h"] = h2h
            if line:  row["line"] = line
            if total: row["total"] = total
            out.append(row)
    print(f"[odds] resolved match odds for {len(out)} games")
    return out


def main():
    if not API_KEY:
        print("[odds] ODDS_API_KEY not set — skipping (existing odds.json kept)")
        return 0

    # existing file -> used to preserve whichever section a refresh can't get
    existing = {}
    if os.path.exists(OUT):
        try:
            existing = json.load(open(OUT))
        except Exception:
            existing = {}

    # ---- match-level odds (single featured-markets call) ----
    match_odds = fetch_match_odds()

    # ---- player props (per-event calls) ----
    lines = []
    ev_url = f"{BASE}/sports/{SPORT}/events?apiKey={API_KEY}&regions={REGION}"
    try:
        events, hdr = _get(ev_url)
        print(f"[odds] {len(events)} events; quota remaining: {hdr.get('x-requests-remaining','?')}")
    except Exception as e:
        print(f"[odds] events fetch failed: {e}")
        events = []

    if events:
        mkt_param = urllib.parse.quote(",".join(MARKETS))
        seen_markets, seen_books = set(), set()
        best = {}  # (player, internal_market) -> {line, over, under, book, _rank}
        for ev in events:
            eid = ev.get("id")
            if not eid:
                continue
            url = (f"{BASE}/sports/{SPORT}/events/{eid}/odds?apiKey={API_KEY}"
                   f"&regions={REGION}&markets={mkt_param}&oddsFormat=decimal")
            try:
                data, _ = _get(url)
            except Exception as e:
                print(f"[odds]  event {eid} skipped: {e}")
                continue
            for bk in data.get("bookmakers", []):
                bkey = bk.get("key", "")
                seen_books.add(bkey)
                rank = BOOK_PRIORITY.index(bkey) if bkey in BOOK_PRIORITY else 99
                for m in bk.get("markets", []):
                    raw = m.get("key", "")
                    seen_markets.add(raw)
                    internal = MARKETS.get(raw)
                    if not internal:
                        continue
                    if raw in MILESTONE_MARKETS:
                        # over-only milestone market: each player can have several points.
                        # Pick the smallest point >= 1.5 (the "2+" line), else the smallest.
                        permp = {}
                        for o in m.get("outcomes", []):
                            player = (o.get("description") or "").strip()
                            pt, pr = _num(o.get("point")), _num(o.get("price"))
                            if not player or pt is None or pr is None:
                                continue
                            permp.setdefault(player, []).append((pt, pr))
                        for player, lst in permp.items():
                            ge = [x for x in lst if x[0] >= 1.5]
                            pt, pr = min(ge, key=lambda x: x[0]) if ge else min(lst, key=lambda x: x[0])
                            key = (player, internal)
                            prev = best.get(key)
                            if prev is None or rank < prev["_rank"]:
                                best[key] = {"line": pt, "over": pr, "under": None, "book": bkey, "_rank": rank}
                        continue
                    is_anytime = raw in ANYTIME_MARKETS
                    grp = {}  # player -> {line, over, under}
                    for o in m.get("outcomes", []):
                        player = (o.get("description") or o.get("name") or "").strip()
                        if not player or player.lower() in ("over", "under", "yes", "no"):
                            if not o.get("description"):
                                continue
                            player = o.get("description").strip()
                        g = grp.setdefault(player, {"line": None, "over": None, "under": None})
                        price = _num(o.get("price"))
                        if is_anytime:
                            g["line"] = 0.5
                            g["over"] = price
                        else:
                            if g["line"] is None:
                                g["line"] = _num(o.get("point"))
                            side = (o.get("name") or "").lower()
                            if "over" in side:
                                g["over"] = price
                            elif "under" in side:
                                g["under"] = price
                    for player, g in grp.items():
                        if g["line"] is None:
                            continue
                        key = (player, internal)
                        prev = best.get(key)
                        if prev is None or rank < prev["_rank"]:
                            best[key] = {**g, "book": bkey, "_rank": rank}
        print(f"[odds] markets seen: {sorted(seen_markets)}")
        print(f"[odds] books seen:   {sorted(seen_books)}")
        lines = [{"player": p, "market": m, "line": v["line"],
                  "over": v["over"], "under": v["under"], "book": v["book"]}
                 for (p, m), v in best.items()]

    # ---- merge: keep prior section if a refresh came back empty ----
    if not lines:
        lines = existing.get("lines", [])
        if lines:
            print("[odds] no fresh props — preserving existing player lines")
    if not match_odds:
        match_odds = existing.get("matchOdds", [])
        if match_odds:
            print("[odds] no fresh match odds — preserving existing match odds")
    if not lines and not match_odds:
        print("[odds] nothing resolved — keeping existing odds.json")
        return 0

    out = {
        "_sample": False,
        "updated": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ"),
        "source": "the-odds-api",
        "lines": sorted(lines, key=lambda x: (x["market"], -(x["line"] or 0))),
        "matchOdds": match_odds,
    }
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    tmp = OUT + ".tmp"
    json.dump(out, open(tmp, "w"))
    os.replace(tmp, OUT)
    players = len({l["player"] for l in lines})
    by_mkt = {}
    for l in lines:
        by_mkt[l["market"]] = by_mkt.get(l["market"], 0) + 1
    print(f"[odds] wrote {OUT}: {len(lines)} props ({players} players, {by_mkt}), "
          f"{len(match_odds)} match-odds games")
    return 0


if __name__ == "__main__":
    sys.exit(main())
