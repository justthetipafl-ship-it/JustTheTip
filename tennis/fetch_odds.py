#!/usr/bin/env python3
"""
fetch_odds.py — tennis odds from The Odds API, joined to our fixtures.

Stores PER-BOOKMAKER prices (so the UI can line-shop / filter to one book),
the best available price per outcome, and a de-vigged consensus implied
probability (for model-vs-market edge).

Cost-aware: free /sports call to find active tennis keys, then one /odds call
per key (regions=au, markets=h2h,totals), billed on markets returned.

Emits data/tennis_odds.json:
  { "n":.., "region":"au", "credits_used":"..",
    "books": { "sportsbet":"SportsBet", ... },           # key -> display title
    "odds": { "<match_id>": {
        "books": 7, "updated": "..ISO..",
        "h2h": { "<norm name>": {
            "imp": 0.62,                                  # de-vigged fair prob
            "best": {"price":1.98,"book":"sportsbet"},    # top available price
            "px": {"sportsbet":1.98,"tab":1.90, ...}      # every book's price
        }, ... },
        "total": {"line":22.5,
                  "over":  {"best":{...},"px":{...}},
                  "under": {"best":{...},"px":{...}}}
    }, ... } }

Usage: python fetch_odds.py   |   python fetch_odds.py --mock-file events.json
"""
from __future__ import annotations
import argparse, json, os, sys, unicodedata, urllib.parse, urllib.request
from statistics import median

BASE = "https://api.the-odds-api.com/v4"


def log(m): print(m, file=sys.stderr)

def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join("".join(c for c in s.lower() if c.isalnum() or c == " ").split())

def http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "jtt-tennis"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.load(r), r.headers.get("x-requests-used"), r.headers.get("x-requests-last")

def fair_two(da, db):
    if not da or not db:
        return None, None
    ra, rb = 1 / da, 1 / db
    t = ra + rb
    return round(ra / t, 3), round(rb / t, 3)

def best_of(px):
    """px: {book: price} -> {'best':{price,book}, 'px':{...}} or None."""
    if not px:
        return None
    bb = max(px, key=px.get)
    return {"best": {"price": round(px[bb], 2), "book": bb},
            "px": {k: round(v, 2) for k, v in px.items()}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=os.environ.get("ODDS_API_KEY", ""))
    ap.add_argument("--region", default="au")
    ap.add_argument("--markets", default="h2h,totals")
    ap.add_argument("--fixtures", default="data/tennis_fixtures.json")
    ap.add_argument("--out", default="data/tennis_odds.json")
    ap.add_argument("--mock-file", default=None)
    args = ap.parse_args()

    fx = json.load(open(args.fixtures))["fixtures"]
    lut = {}
    for f in fx:
        ps = [p.get("name") for p in (f.get("players") or []) if p.get("name")]
        if len(ps) == 2:
            k = frozenset(norm(n) for n in ps)
            if len(k) == 2:
                lut[k] = f["match_id"]
    log(f"fixtures with 2 named players: {len(lut)}")

    events, used = [], None
    if args.mock_file:
        events = json.load(open(args.mock_file)); log(f"mock: {len(events)} events")
    elif args.key:
        sports, _, _ = http_json(f"{BASE}/sports?apiKey={args.key}")
        keys = [s["key"] for s in sports if s.get("key", "").startswith("tennis_") and s.get("active")]
        log(f"active tennis keys: {keys}")
        for k in keys:
            q = urllib.parse.urlencode({"apiKey": args.key, "regions": args.region,
                                        "markets": args.markets, "oddsFormat": "decimal"})
            try:
                evs, used, last = http_json(f"{BASE}/sports/{k}/odds?{q}")
                events += evs; log(f"  {k}: {len(evs)} events (last cost={last})")
            except Exception as e:
                log(f"  {k}: error {e}")
        log(f"credits used this run: {used}")
    else:
        log("no ODDS_API_KEY and no --mock-file - writing empty odds bundle")

    out, book_titles, matched = {}, {}, 0
    for ev in events:
        mid = lut.get(frozenset(norm(n) for n in (ev.get("home_team"), ev.get("away_team")) if n))
        if not mid:
            continue
        h2h_px, tot_px = {}, {}
        for bk in ev.get("bookmakers", []):
            bkey = bk.get("key"); book_titles[bkey] = bk.get("title", bkey)
            for mk in bk.get("markets", []):
                if mk["key"] == "h2h":
                    for o in mk.get("outcomes", []):
                        h2h_px.setdefault(norm(o["name"]), {})[bkey] = o["price"]
                elif mk["key"] == "totals":
                    for o in mk.get("outcomes", []):
                        pt = o.get("point")
                        if pt is not None:
                            tot_px.setdefault(pt, {}).setdefault(o["name"], {})[bkey] = o["price"]
        rec = {"books": len(ev.get("bookmakers", [])), "updated": ev.get("commence_time")}
        nn = list(h2h_px.keys())
        if len(nn) == 2:
            a, b = nn
            fa, fb = fair_two(median(list(h2h_px[a].values())), median(list(h2h_px[b].values())))
            rec["h2h"] = {}
            for nm, fair in ((a, fa), (b, fb)):
                s = best_of(h2h_px[nm]); s["imp"] = fair
                rec["h2h"][nm] = s
        if tot_px:
            line, sides = max(tot_px.items(), key=lambda kv: sum(len(v) for v in kv[1].values()))
            rec["total"] = {"line": line, "over": best_of(sides.get("Over", {})),
                            "under": best_of(sides.get("Under", {}))}
        out[mid] = rec; matched += 1

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    json.dump({"n": matched, "region": args.region, "credits_used": used,
               "books": book_titles, "odds": out},
              open(args.out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    log(f"matched {matched} fixtures; {len(book_titles)} books; wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
