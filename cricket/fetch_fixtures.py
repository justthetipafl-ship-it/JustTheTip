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

SCOPE (Phase 2, July 2026) — mirrors fetch_cricket.py:
  - Leagues: only IPL, BBL, The Hundred, CPL, SA20, PSL (LEAGUE_TOKENS).
    The Hundred is tagged format "T100".
  - Everything else is treated as INTL and kept ONLY when BOTH teams are
    ICC full members (FULL_MEMBERS). Kills associate qualifiers/tri-series.
  - Every dropped match is counted by reason and the tallies are logged, so
    a "wrote 0 fixtures" run explains itself in the Actions log.

Env:
  CRICKET_DATA_KEY   required (cricketdata.org API key)
  FIX_DAYS           look-ahead window in days (default 45)
  FIX_PAGES          max pages to walk, 25 matches each (default 6)
"""
import json, os, sys, urllib.request, urllib.parse, time
from datetime import datetime, timezone, timedelta
from collections import Counter

OUT = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(OUT, exist_ok=True)
KEY = os.environ.get("CRICKET_DATA_KEY", "").strip()
DAYS = int(os.environ.get("FIX_DAYS", "45"))
PAGES = int(os.environ.get("FIX_PAGES", "6"))
BASE = "https://api.cricapi.com/v1"

# matchType -> format. Anything else (t10, etc.) is skipped, EXCEPT that
# matches identified as The Hundred are re-tagged T100 whatever their type.
TYPE_FMT = {"t20": "T20", "odi": "ODI", "test": "TEST",
            "t20i": "T20", "it20": "T20", "odm": "ODI", "mdm": "TEST"}

# ICC full members — INTL fixtures kept only when BOTH teams are in here.
# CricAPI sometimes suffixes names ("India Women", "Australia A"); exact-match
# after stripping a trailing bracket keeps those variants OUT, which is what
# we want (no women's, no A-sides).
FULL_MEMBERS = {
    "Australia", "England", "India", "Pakistan", "South Africa",
    "New Zealand", "West Indies", "Sri Lanka", "Bangladesh",
    "Afghanistan", "Zimbabwe", "Ireland",
}

# League whitelist tokens -> canonical comp name. Order matters: first hit wins.
LEAGUES = [
    (("indian premier", "ipl"),                 "Indian Premier League"),
    (("big bash", "bbl"),                       "Big Bash League"),
    (("the hundred",),                          "The Hundred"),
    (("caribbean premier", "cpl"),              "Caribbean Premier League"),
    (("sa20",),                                 "SA20"),
    (("pakistan super", "psl"),                 "Pakistan Super League"),
]

def league_match(name, series):
    s = (name + " " + (series or "")).lower()
    if "women" in s:
        return None
    for tokens, canon in LEAGUES:
        if any(tok in s for tok in tokens):
            return canon
    return None

def clean_team(t):
    return (t or "").split("(")[0].strip()

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
    seen_total = 0
    drop = Counter()
    date_min = None; date_max = None

    def consider(m):
        nonlocal seen_total, date_min, date_max
        seen_total += 1
        name = m.get("name", "")
        series = m.get("series", "")
        mt = (m.get("matchType") or "").lower()
        fmt = TYPE_FMT.get(mt)
        league_comp = league_match(name, series)
        if league_comp == "The Hundred":
            fmt = "T100"  # 100-ball, whatever CricAPI calls the matchType
        if not fmt:
            drop["matchType"] += 1
            return
        ct = m.get("dateTimeGMT")
        try:
            dt = datetime.fromisoformat(ct.replace("Z", "")).replace(tzinfo=timezone.utc)
        except Exception:
            drop["badDate"] += 1
            return
        if date_min is None or dt < date_min: date_min = dt
        if date_max is None or dt > date_max: date_max = dt
        if dt < now - timedelta(hours=6):
            drop["past"] += 1
            return
        if dt > horizon:
            drop["beyondWindow"] += 1
            return
        teams = [clean_team(t) for t in (m.get("teams") or [])]
        if len(teams) < 2 or any(t in ("", "Tbc", "TBC") for t in teams):
            drop["tbcTeams"] += 1
            return
        if m.get("matchEnded"):
            drop["ended"] += 1
            return
        # ---- scope filter ----
        if league_comp:
            level, comp = "LEAGUE", league_comp
        else:
            if not all(t in FULL_MEMBERS for t in teams):
                drop["scope"] += 1
                return
            level = "INTL"
            comp = (series or name.split(",")[0] or fmt)
        venue, city = split_venue(m.get("venue", ""))
        auto.append({
            "matchId": m["id"], "format": fmt, "level": level,
            "comp": comp,
            "date": dt.strftime("%Y-%m-%d"), "utc": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "home": teams[0], "away": teams[1],
            "venue": venue, "city": city, "status": "upcoming",
        })


    # Pass 1: /matches — scheduled fixtures (free tier serves these weeks out).
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
            consider(m)

    # Pass 2: /currentMatches — the free /matches feed skips the next ~3 weeks
    # (in-progress series live here). Not-yet-started games only; live-score
    # handling is a UI-layer feature, not a fixtures concern.
    try:
        resp = get("currentMatches", offset=0)
        if resp.get("status") == "success":
            for m in resp.get("data", []):
                if not m.get("matchStarted"):
                    consider(m)
        else:
            log("currentMatches status:", resp.get("status"))
    except Exception as e:
        log("currentMatches failed", e)

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
    if date_min:
        log(f"API date coverage: {date_min:%Y-%m-%d} -> {date_max:%Y-%m-%d} "
            f"(window: now -> +{DAYS}d)")
    log(f"scanned {seen_total} matches; drops: " +
        (", ".join(f"{k}={v}" for k, v in sorted(drop.items())) or "none"))
    log(f"DONE wrote {len(merged)} fixtures ({len(auto)} auto, {len(manual)} manual)")

if __name__ == "__main__":
    main()
