#!/usr/bin/env python3
"""
fetch_fixtures.py — JTT Cricket upcoming-fixtures feed (CricAPI / cricketdata.org).

Cricsheet is historical-only, so upcoming matches come from CricAPI's
/matches endpoint, which includes venue. Writes/merges
cricket/data/cricket_fixtures.json in the JTT schema.

- Auto rows use the CricAPI match id as matchId.
- Manual rows (matchId starting "FX") are always preserved.
- Past auto rows are dropped each run.
- Quota: the free plan is request-limited (~100/day). This pulls a few pages
  every 6h, well inside that. info.hitsToday/hitsLimit is logged each run.

Env:
  CRICKET_DATA_KEY   required (cricketdata.org API key)
  FIX_DAYS           look-ahead window in days (default 21)
  FIX_PAGES          max pages to walk, 25 matches each (default 6)
"""
import json, os, sys, urllib.request, urllib.parse, time
from datetime import datetime, timezone, timedelta

OUT = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(OUT, exist_ok=True)
KEY = os.environ.get("CRICKET_DATA_KEY", "").strip()
DAYS = int(os.environ.get("FIX_DAYS", "21"))
PAGES = int(os.environ.get("FIX_PAGES", "6"))
BASE = "https://api.cricapi.com/v1"

# matchType -> format. Anything else (t10, 100-ball, etc.) is skipped.
TYPE_FMT = {"t20": "T20", "odi": "ODI", "test": "TEST",
            "t20i": "T20", "it20": "T20", "odm": "ODI", "mdm": "TEST"}

# League tokens -> LEAGUE; otherwise treated as INTL (national sides).
LEAGUE_TOKENS = ("indian premier", "ipl", "big bash", "bbl", "psl", "pakistan super",
                 "the hundred", "hundred", "caribbean premier", "cpl", "blast",
                 "sa20", "ilt20", "super smash", "lpl", "lanka premier", "bpl",
                 "bangladesh premier", "major league", "mlc", "county", "sheffield shield",
                 "marsh cup", "vitality", "abu dhabi", "global", "t20 challenge")

def is_league(name, series):
    s = (name + " " + (series or "")).lower()
    return any(tok in s for tok in LEAGUE_TOKENS)

def log(*a): print(*a, file=sys.stderr, flush=True)

def get(path, **params):
    params["apikey"] = KEY
    url = f"{BASE}/{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "jtt-cricket/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())

def split_venue(v):
    if not v:
        return "TBC", ""
    parts = [p.strip() for p in v.split(",")]
    if len(parts) >= 2:
        return ", ".join(parts[:-1]), parts[-1]
    return v, ""

def main():
    if not KEY:
        log("ERROR: CRICKET_DATA_KEY not set - skipping fixtures fetch"); return
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=DAYS)

    auto = []
    for page in range(PAGES):
        try:
            resp = get("matches", offset=page * 25)
        except Exception as e:
            log("matches failed", e); break
        if resp.get("status") != "success":
            log("API status:", resp.get("status"), resp.get("info")); break
        data = resp.get("data", [])
        info = resp.get("info", {})
        if page == 0 and info:
            log(f"quota: {info.get('hitsToday','?')}/{info.get('hitsLimit','?')} today")
        if not data:
            break
        for m in data:
            mt = (m.get("matchType") or "").lower()
            fmt = TYPE_FMT.get(mt)
            if not fmt:
                continue
            ct = m.get("dateTimeGMT")
            try:
                dt = datetime.fromisoformat(ct.replace("Z", "")).replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if dt < now - timedelta(hours=6) or dt > horizon:
                continue
            teams = m.get("teams") or []
            if len(teams) < 2 or any(t in ("", "Tbc", "TBC") for t in teams):
                continue
            if m.get("matchEnded"):
                continue
            venue, city = split_venue(m.get("venue", ""))
            name = m.get("name", "")
            level = "LEAGUE" if is_league(name, m.get("series", "")) else "INTL"
            auto.append({
                "matchId": m["id"], "format": fmt, "level": level,
                "comp": (m.get("series") or name.split(",")[0] or fmt),
                "date": dt.strftime("%Y-%m-%d"), "utc": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "home": teams[0], "away": teams[1],
                "venue": venue, "city": city, "status": "upcoming",
            })

    # de-dupe auto by id
    seen = {}
    for f in auto:
        seen[f["matchId"]] = f
    auto = list(seen.values())

    # merge with existing: keep manual FX rows, carry hand-edited venue/city
    path = os.path.join(OUT, "cricket_fixtures.json")
    existing = []
    if os.path.exists(path):
        try: existing = json.load(open(path)).get("fixtures", [])
        except Exception: pass
    prev = {f["matchId"]: f for f in existing}
    manual = [f for f in existing if str(f.get("matchId", "")).startswith("FX")]
    for f in auto:
        old = prev.get(f["matchId"])
        if old:
            if old.get("venue") and old["venue"] != "TBC": f["venue"] = old["venue"]
            if old.get("city"): f["city"] = old["city"]
    merged = manual + auto
    merged.sort(key=lambda x: x.get("utc", x.get("date", "")))

    ver = str(int(time.time() * 1000))
    json.dump({"fixtureCount": len(merged), "version": ver, "fixtures": merged},
              open(path, "w"), separators=(",", ":"))
    log(f"DONE wrote {len(merged)} fixtures ({len(auto)} auto, {len(manual)} manual)")

if __name__ == "__main__":
    main()
