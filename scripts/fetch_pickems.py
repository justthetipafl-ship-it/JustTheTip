#!/usr/bin/env python3
"""Dabble Pick'Em lines: the number with no price attached.

    ROA_API_KEY=... python scripts/fetch_pickem.py --sport NBA --out nba/data
    python scripts/fetch_pickem.py --from-file sample.json --sport NBA --out nba/data
    python scripts/fetch_pickem.py --selftest        # no network; checks the normaliser

WHAT THIS IS
A pick'em product posts a line and asks which side of it you want. There is no price, so there is
no implied probability and nothing to de-vig - which means none of the tool's edge machinery
applies to these at all. What they ARE good for is comparison: the same man has a line at the
sportsbooks too, and where the two disagree, the disagreement is the whole story.

THE RESPONSE
    {"sport":"NBA","bookmaker":"Dabble Pick'Em",
     "games":[{"game":{"commence_time":..., "home_team":..., "away_team":...},
               "lines":[{"player_name":..., "key":"player_points", "point":26.5,
                         "last_update":...}]}]}

Flatter than the odds endpoint: `lines` hangs straight off the game, with no bookmaker level,
because there is only ever the one. One market per request, so the loop below asks per market.

WHAT IS WRITTEN
A flat row per line, keyed the way the rest of the tool keys things - the JTT market name from the
odds fetcher's own map, so a pick'em row joins to the same gamelog field as everything else:

    {"player": "Anthony Edwards", "market": "points", "line": 26.5,
     "home": "Cleveland Cavaliers", "away": "Minnesota Timberwolves", "start": "...", ...}

Teams are left exactly as the feed writes them. The shell matches a player to a fixture through
its own roster, not through these strings, so translating them here would add a mapping to get
wrong for no gain.
"""
import argparse, datetime, json, os, sys

# The same market map the odds fetcher uses, so "points" here and "points" there are the same
# gamelog field. Importing it rather than copying means a market added for odds cannot silently
# go missing from pick'em.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from fetch_odds import SPORTS as ODDS_MARKETS
except Exception:                                           # pragma: no cover - selftest path
    ODDS_MARKETS = {}

BASE = "https://api.rapidoddsapi.com"
# The pick'em product covers these; asking for a sport it does not cover wastes a credit.
PICKEM_SPORTS = ("NBA", "WNBA", "NFL")


def market_map(sport):
    return dict(ODDS_MARKETS.get(sport) or {})


def normalise(payload, sport):
    """The feed's shape -> flat rows. Tolerant on purpose: a line with no point, no player or an
    unmapped market is dropped rather than written half-formed, and the count of what was dropped
    comes back with the rows so the caller can say so instead of silently shipping less."""
    mm = market_map(sport)
    rows, dropped = [], {"no_point": 0, "no_player": 0, "unmapped": 0}
    games = (payload or {}).get("games") or []
    for gx in games:
        g = (gx or {}).get("game") or {}
        for ln in (gx or {}).get("lines") or []:
            name = (ln or {}).get("player_name")
            pt = (ln or {}).get("point")
            key = (ln or {}).get("key")
            if not name:
                dropped["no_player"] += 1; continue
            if pt is None:
                dropped["no_point"] += 1; continue
            mkt = mm.get(key)
            if not mkt:
                dropped["unmapped"] += 1; continue
            rows.append({
                "player": name, "market": mkt, "line": float(pt),
                "home": g.get("home_team"), "away": g.get("away_team"),
                "start": g.get("commence_time"), "updated": ln.get("last_update"),
            })
    return rows, dropped


def too_fresh(out_dir, every_min):
    """True when the existing file is younger than `every_min`, so the pull is skipped.

    The same gate fetch_odds.py uses, and it matters MORE here than there. These lines are only
    ever read next to a book line, so the comparison is only as honest as the older of the two.
    A pick'em file left sitting for twelve hours against prices refreshed every fifteen minutes
    does not show a stale number - it shows a GAP that does not exist, which is the one thing
    this section must never do. So pick'em is pulled on the refresh cadence too, and this keeps
    that from turning into a request a minute.
    """
    if not every_min:
        return False
    path = os.path.join(out_dir, "pickem.json")
    if not os.path.exists(path):
        return False
    age = (datetime.datetime.now().timestamp() - os.path.getmtime(path)) / 60.0
    if age < every_min:
        print("  pickem.json is %.0f min old (< %d) - skipping" % (age, every_min))
        return True
    return False


def fetch(sport, markets, key):
    import urllib.request, urllib.parse
    out = []
    for mk in markets:
        url = "%s/sports/%s/pickem?%s" % (BASE, sport, urllib.parse.urlencode(
            {"api_key": key, "market_type": mk}))
        try:
            with urllib.request.urlopen(url, timeout=45) as r:
                payload = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            # One market failing must not lose the others - a 404 on a market this sport does not
            # carry is an ordinary answer, not a reason to write nothing.
            print("  %s: %s" % (mk, str(e)[:140]))
            continue
        rows, dropped = normalise(payload, sport)
        print("  %-34s %4d lines%s" % (mk, len(rows),
              ("  (dropped %s)" % dropped) if any(dropped.values()) else ""))
        out.extend(rows)
    return out


def write(out_dir, sport, rows):
    os.makedirs(out_dir, exist_ok=True)
    payload = {
        "updated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "source": "rapidoddsapi pick'em (Dabble Pick'Em)",
        "sport": sport,
        "book": "Dabble Pick'Em",
        "rows": rows,
    }
    path = os.path.join(out_dir, "pickem.json")
    with open(path, "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    print("  wrote %s (%.0f KB) - %d lines, %d players, %d markets" % (
        path, os.path.getsize(path) / 1024, len(rows),
        len({r["player"] for r in rows}), len({r["market"] for r in rows})))


def selftest():
    ok = True

    def check(label, cond):
        nonlocal ok
        print(("  ok   " if cond else "  FAIL ") + label)
        ok = ok and bool(cond)

    sample = {"sport": "NBA", "bookmaker": "Dabble Pick'Em", "games": [
        {"game": {"commence_time": "2026-10-26T23:05:00",
                  "home_team": "Cleveland Cavaliers", "away_team": "Minnesota Timberwolves"},
         "lines": [
             {"player_name": "Anthony Edwards", "key": "player_points", "point": 26.5,
              "last_update": "2026-10-26T08:20:15"},
             {"player_name": "No Point Man", "key": "player_points", "point": None},
             {"player_name": "", "key": "player_points", "point": 10.5},
             {"player_name": "Unmapped Market", "key": "player_fouls_drawn", "point": 2.5},
             {"player_name": "Zero Line", "key": "player_points", "point": 0},
         ]}]}
    rows, dropped = normalise(sample, "NBA")
    check("a good line comes through", len(rows) == 2)
    check("the market is translated to the tool's own key",
          rows and rows[0]["market"] == "points")
    check("the line is a number", rows and rows[0]["line"] == 26.5)
    check("the fixture travels with the row",
          rows and rows[0]["home"] == "Cleveland Cavaliers")
    check("a missing point is dropped and counted", dropped["no_point"] == 1)
    check("a missing player is dropped and counted", dropped["no_player"] == 1)
    check("a market we do not carry is dropped and counted", dropped["unmapped"] == 1)
    # a line OF zero is a real line, and `if not pt` would have thrown it away with the Nones
    check("a line of 0 is kept, not mistaken for missing",
          any(r["line"] == 0 for r in rows))

    empty, d2 = normalise({}, "NBA")
    check("an empty payload yields nothing and does not throw", empty == [])
    empty2, _ = normalise({"games": [{"game": {}, "lines": []}]}, "NBA")
    check("a game with no lines yields nothing", empty2 == [])

    check("the market map came from the odds fetcher",
          market_map("NBA").get("player_points") == "points")
    print("SELFTEST", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="NBA")
    ap.add_argument("--out", default=None)
    ap.add_argument("--markets", default="", help="comma separated; default is every market the odds fetcher asks for")
    ap.add_argument("--from-file", default=None, help="normalise a saved response instead of calling the API")
    ap.add_argument("--every", type=int, default=0, help="skip if pickem.json is younger than this many minutes")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())
    sport = a.sport.upper()
    out_dir = a.out or {"NBA": "nba/data", "NFL": "nfl/data", "WNBA": "wnba/data"}.get(sport)
    if not out_dir:
        sys.exit("no output directory for %s" % sport)
    if a.from_file:
        with open(a.from_file) as f:
            rows, dropped = normalise(json.load(f), sport)
        print("normalised %s: %d lines%s" % (a.from_file, len(rows),
              ("  (dropped %s)" % dropped) if any(dropped.values()) else ""))
        write(out_dir, sport, rows)
        sys.exit(0)
    if sport not in PICKEM_SPORTS:
        sys.exit("pick'em does not cover %s" % sport)
    if too_fresh(out_dir, a.every):
        sys.exit(0)
    key = os.environ.get("ROA_API_KEY", "")
    if not key:
        sys.exit("ROA_API_KEY not set")
    markets = [m for m in a.markets.split(",") if m] or sorted(market_map(sport))
    print("pick'em %s: %d markets" % (sport, len(markets)))
    write(out_dir, sport, fetch(sport, markets, key))
