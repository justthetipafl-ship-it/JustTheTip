#!/usr/bin/env python3
"""
fetch_fixtures.py — JTT Cricket upcoming-fixtures feed (v4, two-source).

WHY TWO SOURCES (July 2026): CricAPI's free /matches feed carries nothing
nearer than ~3 weeks out (live series' remaining games aren't listed), so a
CricAPI-only slate goes empty exactly when cricket is on. The Odds API's
/events endpoint lists every upcoming match the books are pricing — which is
the JTT-relevant definition of a fixture — but has no venue. So:

  PASS 0  The Odds API /v4/sports/{key}/events  -> the authoritative slate
          (near-term + far, bettable by definition, no venue)
  PASS 1  CricAPI /matches                      -> far-out fixtures WITH venue
  PASS 2  CricAPI /currentMatches               -> not-yet-started series games

Rows are merged by (date, teams): a venue-bearing CricAPI row beats a
venue-less Odds row for the same match. Manual FX* rows are always kept.
Hand-edited venues on existing rows are preserved by matchId and by match key.

SCOPE mirrors fetch_cricket.py: leagues IPL/BBL/The Hundred/CPL/SA20/PSL,
everything else INTL and kept only when BOTH teams are ICC full members.
The Hundred is tagged format T100.

Env:
  ODDS_API_KEY       The Odds API key (recommended; pass 0 skipped if unset)
  CRICKET_DATA_KEY   cricketdata.org key (venue enrichment; passes 1-2 skipped if unset)
  FIX_DAYS           look-ahead window in days (default 45)
  FIX_PAGES          max CricAPI /matches pages, 25 each (default 6)
"""
import json, os, sys, urllib.request, urllib.parse, time
from datetime import datetime, timezone, timedelta
from collections import Counter

OUT = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(OUT, exist_ok=True)
KEY = os.environ.get("CRICKET_DATA_KEY", "").strip()
OKEY = os.environ.get("ODDS_API_KEY", "").strip()
DAYS = int(os.environ.get("FIX_DAYS", "45"))
PAGES = int(os.environ.get("FIX_PAGES", "6"))
BASE = "https://api.cricapi.com/v1"
OBASE = "https://api.the-odds-api.com/v4"

TYPE_FMT = {"t20": "T20", "odi": "ODI", "test": "TEST",
            "t20i": "T20", "it20": "T20", "odm": "ODI", "mdm": "TEST"}

FULL_MEMBERS = {
    "Australia", "England", "India", "Pakistan", "South Africa",
    "New Zealand", "West Indies", "Sri Lanka", "Bangladesh",
    "Afghanistan", "Zimbabwe", "Ireland",
}

LEAGUES = [
    (("indian premier", "ipl"),    "Indian Premier League"),
    (("big bash", "bbl"),          "Big Bash League"),
    (("the hundred",),             "The Hundred"),
    (("caribbean premier", "cpl"), "Caribbean Premier League"),
    (("sa20",),                    "SA20"),
    (("pakistan super", "psl"),    "Pakistan Super League"),
]

# The Odds API sport keys -> (format, level, comp). Comp None = derive later.
ODDS_SPORTS = {
    "cricket_international_t20":        ("T20",  "INTL",   None),
    "cricket_odi":                      ("ODI",  "INTL",   None),
    "cricket_test_match":               ("TEST", "INTL",   None),
    "cricket_ipl":                      ("T20",  "LEAGUE", "Indian Premier League"),
    "cricket_big_bash":                 ("T20",  "LEAGUE", "Big Bash League"),
    "cricket_psl":                      ("T20",  "LEAGUE", "Pakistan Super League"),
    "cricket_caribbean_premier_league": ("T20",  "LEAGUE", "Caribbean Premier League"),
    "cricket_the_hundred":              ("T100", "LEAGUE", "The Hundred"),
    "cricket_sa20":                     ("T20",  "LEAGUE", "SA20"),
}

def log(*a): print(*a, file=sys.stderr, flush=True)

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

def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "jtt-cricket/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())

def get(path, **params):
    params["apikey"] = KEY
    return get_json(f"{BASE}/{path}?" + urllib.parse.urlencode(params))

def oget(path, **params):
    params["apiKey"] = OKEY
    return get_json(f"{OBASE}/{path}?" + urllib.parse.urlencode(params))

def split_venue(v):
    if not v:
        return "TBC", ""
    parts = [p.strip() for p in v.split(",")]
    if len(parts) >= 2:
        return ", ".join(parts[:-1]), parts[-1]
    return v, ""

def xkey(date, home, away):
    """Cross-source merge key: same match seen by different feeds."""
    return date + "|" + "|".join(sorted([str(home).lower(), str(away).lower()]))

def main():
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=DAYS)
    auto = []
    seen_total = 0
    drop = Counter()
    inwin_drops = []
    date_min = None; date_max = None

    # ---------------- PASS 0: The Odds API events ----------------
    if not OKEY:
        log("ODDS_API_KEY not set — skipping the Odds API slate (recommended: add it)")
    else:
        try:
            sports = oget("sports")
            active = {s["key"] for s in sports if s.get("active")}
            cricket_keys = sorted(k for k in active if k.startswith("cricket"))
            unmapped = [k for k in cricket_keys if k not in ODDS_SPORTS]
            if unmapped:
                log("odds-api cricket keys not in map (ignored):", ", ".join(unmapped))
            for skey in (k for k in cricket_keys if k in ODDS_SPORTS):
                fmt, level, comp0 = ODDS_SPORTS[skey]
                try:
                    evs = oget(f"sports/{skey}/events", dateFormat="iso",
                               commenceTimeFrom=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                               commenceTimeTo=horizon.strftime("%Y-%m-%dT%H:%M:%SZ"))
                except Exception as e:
                    log(f"odds-api {skey} failed:", e); continue
                kept = 0
                for ev in evs:
                    home = clean_team(ev.get("home_team")); away = clean_team(ev.get("away_team"))
                    if not home or not away:
                        continue
                    if level == "INTL" and not (home in FULL_MEMBERS and away in FULL_MEMBERS):
                        drop["scope"] += 1
                        continue
                    try:
                        dt = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
                    except Exception:
                        drop["badDate"] += 1
                        continue
                    comp = comp0 or (ev.get("sport_title") or fmt)
                    auto.append({
                        "matchId": "OA" + str(ev["id"]), "format": fmt, "level": level, "comp": comp,
                        "date": dt.strftime("%Y-%m-%d"), "utc": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "home": home, "away": away,
                        "venue": "TBC", "city": "", "status": "upcoming",
                    })
                    kept += 1
                log(f"odds-api {skey}: {kept} events in window")
        except Exception as e:
            log("odds-api sports list failed:", e)

    # ---------------- CricAPI passes (venue-bearing) ----------------
    def consider(m):
        nonlocal seen_total, date_min, date_max
        seen_total += 1
        name = m.get("name", "")
        series = m.get("series", "")
        mt = (m.get("matchType") or "").lower()
        fmt = TYPE_FMT.get(mt)
        league_comp = league_match(name, series)
        if league_comp == "The Hundred":
            fmt = "T100"
        ct = m.get("dateTimeGMT")
        try:
            dt = datetime.fromisoformat(ct.replace("Z", "")).replace(tzinfo=timezone.utc)
        except Exception:
            drop["badDate"] += 1
            return
        if date_min is None or dt < date_min: date_min = dt
        if date_max is None or dt > date_max: date_max = dt
        in_window = (now - timedelta(hours=6)) <= dt <= horizon
        def rejected(reason):
            drop[reason] += 1
            if in_window and len(inwin_drops) < 12:
                inwin_drops.append(f"{reason}: [{mt}] {name} — {m.get('teams')} @ {ct}")
        if not fmt:
            rejected("matchType"); return
        if dt < now - timedelta(hours=6):
            drop["past"] += 1; return
        if dt > horizon:
            drop["beyondWindow"] += 1; return
        teams = [clean_team(t) for t in (m.get("teams") or [])]
        if len(teams) < 2 or any(t in ("", "Tbc", "TBC") for t in teams):
            rejected("tbcTeams"); return
        if m.get("matchEnded"):
            rejected("ended"); return
        if league_comp:
            level, comp = "LEAGUE", league_comp
        else:
            if not all(t in FULL_MEMBERS for t in teams):
                rejected("scope"); return
            level = "INTL"
            comp = (series or name.split(",")[0] or fmt)
        venue, city = split_venue(m.get("venue", ""))
        auto.append({
            "matchId": m["id"], "format": fmt, "level": level, "comp": comp,
            "date": dt.strftime("%Y-%m-%d"), "utc": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "home": teams[0], "away": teams[1],
            "venue": venue, "city": city, "status": "upcoming",
        })

    if not KEY:
        log("CRICKET_DATA_KEY not set — skipping CricAPI venue passes")
    else:
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
                log(f"cricapi quota: {info.get('hitsToday','?')}/{info.get('hitsLimit','?')} today")
            if not data:
                break
            for m in data:
                consider(m)
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

    # ---------------- merge ----------------
    # cross-source dedupe by (date, teams): a venue-bearing row wins; when a
    # CricAPI row replaces an OA row, the OA id is dropped in favour of the
    # CricAPI id (stable across runs since CricAPI ids persist).
    byx = {}
    for f in auto:
        k = xkey(f["date"], f["home"], f["away"])
        cur = byx.get(k)
        if cur is None:
            byx[k] = f
        elif cur.get("venue") in (None, "", "TBC") and f.get("venue") not in (None, "", "TBC"):
            byx[k] = f
    auto = list(byx.values())

    # preserve manual FX rows + hand-edited venues (by matchId, then by match key)
    path = os.path.join(OUT, "cricket_fixtures.json")
    existing = []
    if os.path.exists(path):
        try: existing = json.load(open(path)).get("fixtures", [])
        except Exception: pass
    prev_id = {f.get("matchId"): f for f in existing}
    prev_x = {xkey(f.get("date", ""), f.get("home", ""), f.get("away", "")): f for f in existing}
    manual = [f for f in existing if str(f.get("matchId", "")).startswith("FX")]
    for f in auto:
        old = prev_id.get(f["matchId"]) or prev_x.get(xkey(f["date"], f["home"], f["away"]))
        if old:
            if old.get("venue") and old["venue"] != "TBC" and f.get("venue") in (None, "", "TBC"):
                f["venue"] = old["venue"]
            if old.get("city") and not f.get("city"):
                f["city"] = old["city"]
    merged = manual + auto
    merged.sort(key=lambda x: x.get("utc", x.get("date", "")))

    ver = str(int(time.time() * 1000))
    json.dump({"fixtureCount": len(merged), "version": ver, "fixtures": merged},
              open(path, "w"), separators=(",", ":"))
    if date_min:
        log(f"cricapi date coverage: {date_min:%Y-%m-%d} -> {date_max:%Y-%m-%d} (window: now -> +{DAYS}d)")
    if inwin_drops:
        log("in-window cricapi drops (detail):")
        for d in inwin_drops: log("  " + d)
    log(f"cricapi scanned {seen_total}; drops: " +
        (", ".join(f"{k}={v}" for k, v in sorted(drop.items())) or "none"))
    log(f"DONE wrote {len(merged)} fixtures ({len(auto)} auto, {len(manual)} manual)")

if __name__ == "__main__":
    main()
