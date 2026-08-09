#!/usr/bin/env python3
"""
JTT API-Football pull — per-player per-match shots / shots-on-target / fouls / passes for EPL.

Fills the stats FPL doesn't carry (shots, SOT, fouls, passes) and merges onto the FPL gamelogs
by nmkey (+ date/opponent per row). EPL league id = 39.

Free tier = 100 requests/day, so this is RESUMABLE and SELF-LIMITING: it reads the quota headers,
stops before the daily cap, and skips fixtures already stored — so it backfills a season over ~5
daily runs, then maintains it. One /fixtures/players call per match = ~380 calls for a full season.

Auth: header x-apisports-key = your api-sports.io key (GitHub secret APIFOOTBALL_KEY).
Season = START year (2025 = 2025/26). Set APIFOOTBALL_SEASON in the workflow.
Output (EPL/data/): apifootball_stats.json
  {updated, season, rows: {nmkey: [ {fixture_id,date,opp,home,min,shots,sot,fouls,fouls_drawn,passes,key_passes,tackles} ]}}
"""
import json
import os
import time
import unicodedata
import urllib.error
import urllib.request

BASE = "https://v3.football.api-sports.io"
LEAGUE = 39
PACE = 4.0          # seconds between calls (free tier per-minute rate limit)
QUOTA_FLOOR = 3     # stop when this few daily requests remain

_SPECIAL = str.maketrans({
    "\u00f8": "o", "\u00d8": "o", "\u00e6": "ae", "\u00c6": "ae", "\u0142": "l", "\u0141": "l",
    "\u0111": "d", "\u0110": "d", "\u00fe": "th", "\u00de": "th", "\u00f0": "d", "\u00d0": "d",
    "\u00df": "ss", "\u0131": "i", "\u0130": "i",
})


def nmkey(name):
    """Accent-stripped, lowercased, alnum-only name key — must match fetch_fpl.py exactly."""
    s = (name or "").translate(_SPECIAL)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return " ".join("".join(c for c in s.lower() if c.isalnum() or c == " ").split())


def api(path, key, tries=4):
    """GET, returning (json, daily_requests_remaining). Raises HTTPError(429) so callers can stop cleanly."""
    req = urllib.request.Request(BASE + path, headers={"x-apisports-key": key, "Accept": "application/json"})
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                rem = r.headers.get("x-ratelimit-requests-remaining")
                data = json.loads(r.read().decode("utf-8", "replace"))
                return data, (int(rem) if rem and str(rem).isdigit() else None)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise
            last = e
            time.sleep(2 * (i + 1))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError("api failed after %d tries: %s (%s)" % (tries, path, last))


def player_stat(statistics):
    """Pull the fields we need from a player's statistics[0] block."""
    s = (statistics or [{}])[0] or {}
    sh = s.get("shots") or {}
    p = s.get("passes") or {}
    t = s.get("tackles") or {}
    fl = s.get("fouls") or {}
    g = s.get("games") or {}
    go = s.get("goals") or {}
    cd = s.get("cards") or {}
    return {
        "min": g.get("minutes"),
        "shots": sh.get("total"), "sot": sh.get("on"),
        "fouls": fl.get("committed"), "fouls_drawn": fl.get("drawn"),
        "passes": p.get("total"), "key_passes": p.get("key"),
        "tackles": t.get("total"),
        # also captured (same call, free) so the last-season backfill is a complete gamelog
        "goals": go.get("total"), "assists": go.get("assists"),
        "saves": go.get("saves"), "conceded": go.get("conceded"),
        "yellow": cd.get("yellow"), "red": cd.get("red"),
    }


def main():
    key = os.environ.get("APIFOOTBALL_KEY")
    if not key:
        raise SystemExit("APIFOOTBALL_KEY not set (add it as a repo secret)")
    g = time.gmtime()
    season = os.environ.get("APIFOOTBALL_SEASON") or str(g.tm_year - (0 if g.tm_mon >= 7 else 1))
    max_run = int(os.environ.get("APIFOOTBALL_MAX_PER_RUN") or 90)

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "apifootball_stats.json")

    store = {"updated": 0, "season": season, "rows": {}}
    done = set()
    if os.path.exists(out_path):
        try:
            store = json.load(open(out_path))
            if str(store.get("season")) != str(season):
                store = {"updated": 0, "season": season, "rows": {}}
            for rows in store.get("rows", {}).values():
                for r in rows:
                    done.add(str(r.get("fixture_id")))
        except Exception:  # noqa: BLE001
            store = {"updated": 0, "season": season, "rows": {}}
    store.setdefault("rows", {})

    fx, rem = api("/fixtures?league=%d&season=%s" % (LEAGUE, season), key)
    if fx.get("errors"):
        raise SystemExit("api-football error on fixtures list: %s" % fx["errors"])
    fixtures = fx.get("response") or []
    finished = [f for f in fixtures
                if (((f.get("fixture") or {}).get("status") or {}).get("short") in ("FT", "AET", "PEN"))
                and str((f.get("fixture") or {}).get("id")) not in done]
    total_fin = sum(1 for f in fixtures if (((f.get("fixture") or {}).get("status") or {}).get("short") in ("FT", "AET", "PEN")))
    print("season %s: %d finished total, %d stored, %d to pull this run (daily remaining ~%s)" % (season, total_fin, len(done), len(finished), rem))

    n = 0
    for f in finished:
        if n >= max_run:
            print("hit per-run cap (%d); resume next run" % max_run)
            break
        if rem is not None and rem <= QUOTA_FLOOR:
            print("near daily quota (%s left); resume next run" % rem)
            break
        fx_o = f.get("fixture") or {}
        fid = str(fx_o.get("id"))
        date = fx_o.get("date")
        home = ((f.get("teams") or {}).get("home") or {}).get("name")
        away = ((f.get("teams") or {}).get("away") or {}).get("name")
        try:
            pj, rem = api("/fixtures/players?fixture=%s" % fid, key)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print("rate/quota hit; resume next run")
                break
            raise
        if pj.get("errors"):
            print("  fixture %s error: %s" % (fid, pj["errors"]))
            time.sleep(PACE)
            continue
        for tb in (pj.get("response") or []):
            tname = ((tb.get("team")) or {}).get("name")
            is_home = (tname == home)
            opp = away if is_home else home
            for pl in (tb.get("players") or []):
                name = ((pl.get("player")) or {}).get("name")
                k = nmkey(name)
                if not k:
                    continue
                row = {"fixture_id": fid, "date": date, "opp": opp, "home": is_home}
                row.update(player_stat(pl.get("statistics")))
                store["rows"].setdefault(k, []).append(row)
        n += 1
        if n % 10 == 0:
            store["updated"] = int(time.time())
            json.dump(store, open(out_path, "w"), separators=(",", ":"))
            print("  ...%d fixtures this run, %d players stored" % (n, len(store["rows"])))
        time.sleep(PACE)

    store["updated"] = int(time.time())
    json.dump(store, open(out_path, "w"), separators=(",", ":"))
    n_rows = sum(len(v) for v in store["rows"].values())
    print("wrote apifootball_stats.json: %d players, %d match rows (%d fixtures pulled this run)" % (len(store["rows"]), n_rows, n))


if __name__ == "__main__":
    main()
