#!/usr/bin/env python3
"""
fetch_odds.py — pull tennis odds from The Odds API, join to our fixtures, emit
de-vigged implied probabilities + market totals so the front-end can show edges.

Cost-aware:
  * /sports listing call is FREE — used to find active tennis tournament keys.
  * one /odds call per active key, regions=au, markets=h2h,totals.
  * billed on markets RETURNED, so requesting totals costs nothing when a book
    doesn't price it.

The Odds API keys tennis per tournament (tennis_atp_french_open, tennis_wta_*).
We match each odds event to our ESPN fixture by normalised player-name pair.

Emits data/tennis_odds.json:
  { "n":.., "region":"au", "credits_used":"..",
    "odds": { "<match_id>": {
        "books": 7, "updated": "..ISO..",
        "h2h": { "<norm name>": {"price":1.85,"imp":0.62}, ... },
        "total": {"line":22.5,"over":1.90,"under":1.90}
    }, ... } }

Usage:
  python fetch_odds.py                         # uses $ODDS_API_KEY
  python fetch_odds.py --mock-file events.json # parse a saved response (no API call)
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=os.environ.get("ODDS_API_KEY", ""))
    ap.add_argument("--region", default="au")
    ap.add_argument("--markets", default="h2h,totals")
    ap.add_argument("--fixtures", default="data/tennis_fixtures.json")
    ap.add_argument("--out", default="data/tennis_odds.json")
    ap.add_argument("--mock-file", default=None)
    args = ap.parse_args()

    # fixture lookup: frozenset(2 normalised names) -> match_id
    fx = json.load(open(args.fixtures))["fixtures"]
    lut = {}
    for f in fx:
        ps = [p.get("name") for p in (f.get("players") or []) if p.get("name")]
        if len(ps) == 2:
            k = frozenset(norm(n) for n in ps)
            if len(k) == 2:
                lut[k] = f["match_id"]
    log(f"fixtures with 2 named players: {len(lut)}")

    events, used, last = [], None, None
    if args.mock_file:
        events = json.load(open(args.mock_file))
        log(f"mock: {len(events)} events")
    elif args.key:
        sports, _, _ = http_json(f"{BASE}/sports?apiKey={args.key}")
        keys = [s["key"] for s in sports if s.get("key", "").startswith("tennis_") and s.get("active")]
        log(f"active tennis keys: {keys}")
        for k in keys:
            q = urllib.parse.urlencode({"apiKey": args.key, "regions": args.region,
                                        "markets": args.markets, "oddsFormat": "decimal"})
            try:
                evs, used, last = http_json(f"{BASE}/sports/{k}/odds?{q}")
                events += evs
                log(f"  {k}: {len(evs)} events (last cost={last})")
            except Exception as e:
                log(f"  {k}: error {e}")
        log(f"credits used this run reported: {used}")
    else:
        log("no ODDS_API_KEY and no --mock-file — writing empty odds bundle")

    out, matched = {}, 0
    for ev in events:
        names = [ev.get("home_team"), ev.get("away_team")]
        key = frozenset(norm(n) for n in names if n)
        mid = lut.get(key)
        if not mid:
            continue
        h2h, totals = {}, {}
        for bk in ev.get("bookmakers", []):
            for mk in bk.get("markets", []):
                if mk["key"] == "h2h":
                    for o in mk.get("outcomes", []):
                        h2h.setdefault(norm(o["name"]), []).append(o["price"])
                elif mk["key"] == "totals":
                    for o in mk.get("outcomes", []):
                        pt = o.get("point")
                        if pt is None:
                            continue
                        totals.setdefault(pt, {}).setdefault(o["name"], []).append(o["price"])
        rec = {"books": len(ev.get("bookmakers", [])), "updated": ev.get("commence_time")}
        nn = list(h2h.keys())
        if len(nn) == 2:
            a, b = nn
            pa, pb = round(median(h2h[a]), 2), round(median(h2h[b]), 2)
            fa, fb = fair_two(pa, pb)
            rec["h2h"] = {a: {"price": pa, "imp": fa}, b: {"price": pb, "imp": fb}}
        if totals:
            line, sides = max(totals.items(), key=lambda kv: sum(len(v) for v in kv[1].values()))
            ov, un = sides.get("Over"), sides.get("Under")
            rec["total"] = {"line": line,
                            "over": round(median(ov), 2) if ov else None,
                            "under": round(median(un), 2) if un else None}
        out[mid] = rec
        matched += 1

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    json.dump({"n": matched, "region": args.region, "credits_used": used, "odds": out},
              open(args.out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    log(f"matched {matched} fixtures with odds; wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
