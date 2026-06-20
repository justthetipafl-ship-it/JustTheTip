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
}
ANYTIME_MARKETS = {"player_goal_scorer_anytime"}   # yes/no -> over @ line 0.5

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


def main():
    if not API_KEY:
        print("[odds] ODDS_API_KEY not set — skipping (existing odds.json kept)")
        return 0

    # 1) upcoming events
    ev_url = f"{BASE}/sports/{SPORT}/events?apiKey={API_KEY}&regions={REGION}"
    try:
        events, hdr = _get(ev_url)
    except Exception as e:
        print(f"[odds] events fetch failed: {e} — keeping existing odds.json")
        return 0
    if not events:
        print("[odds] no upcoming AFL events — keeping existing odds.json")
        return 0
    print(f"[odds] {len(events)} events; quota remaining: {hdr.get('x-requests-remaining','?')}")

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
                is_anytime = raw in ANYTIME_MARKETS
                grp = {}  # player -> {line, over, under}
                for o in m.get("outcomes", []):
                    player = (o.get("description") or o.get("name") or "").strip()
                    if not player or player.lower() in ("over", "under", "yes", "no"):
                        # line markets carry the player in `description`; skip stray labels
                        if not o.get("description"):
                            continue
                        player = o.get("description").strip()
                    g = grp.setdefault(player, {"line": None, "over": None, "under": None})
                    price = _num(o.get("price"))
                    if is_anytime:
                        g["line"] = 0.5
                        g["over"] = price          # anytime scorer == over 0.5 goals
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
    if not lines:
        print("[odds] no priced lines resolved — keeping existing odds.json")
        return 0

    out = {
        "_sample": False,
        "updated": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ"),
        "source": "the-odds-api",
        "lines": sorted(lines, key=lambda x: (x["market"], -(x["line"] or 0))),
    }
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    tmp = OUT + ".tmp"
    json.dump(out, open(tmp, "w"))
    os.replace(tmp, OUT)
    players = len({l["player"] for l in lines})
    by_mkt = {}
    for l in lines:
        by_mkt[l["market"]] = by_mkt.get(l["market"], 0) + 1
    print(f"[odds] wrote {OUT}: {len(lines)} lines, {players} players, by market {by_mkt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
