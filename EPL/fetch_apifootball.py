#!/usr/bin/env python3
"""
JTT API-Football pull - per-player per-match shots / shots-on-target / fouls / passes for EPL.

Fills the stats FPL doesn't carry (shots, SOT, fouls, passes) and merges onto the FPL gamelogs
by nmkey (+ date/opponent per row). EPL league id = 39.

Free tier = 100 requests/day, so this is RESUMABLE and SELF-LIMITING: it reads the quota headers,
stops before the daily cap, and skips fixtures already stored - so it backfills a season over ~5
daily runs, then maintains it. One /fixtures/players call per match = ~380 calls for a full season.

Auth: header x-apisports-key = your api-sports.io key (GitHub secret APIFOOTBALL_KEY).
Season = START year (2025 = 2025/26). Set APIFOOTBALL_SEASON in the workflow.
Output (EPL/data/):
  apifootball_stats.json  {updated, season, rows: {nmkey: [ {fixture_id,date,...stats} ]}}
  apifootball_meta.json   {updated, season,
     fixtures: {fid: {referee, venue, city, home, away, date, gh, ga, round, status}},
     lineups:  {fid: {home:{formation,startXI:[name],subs:[name],coach}, away:{...}}} }
Referee + venue come free from the /fixtures list (already fetched). Lineups + corner stats cost two extra calls per fixture (/fixtures/lineups, /fixtures/statistics).
"""
import json
import os
import time
import unicodedata
import urllib.error
import urllib.request

BASE = "https://v3.football.api-sports.io"
LEAGUE = 39
PACE = float(os.environ.get("APIFOOTBALL_PACE") or 4.0)   # seconds between calls; free tier ~4.0, paid plans can drop to ~0.5
QUOTA_FLOOR = 3     # stop when this few daily requests remain

_SPECIAL = str.maketrans({
    "\u00f8": "o", "\u00d8": "o", "\u00e6": "ae", "\u00c6": "ae", "\u0142": "l", "\u0141": "l",
    "\u0111": "d", "\u0110": "d", "\u00fe": "th", "\u00de": "th", "\u00f0": "d", "\u00d0": "d",
    "\u00df": "ss", "\u0131": "i", "\u0130": "i",
})


def nmkey(name):
    """Accent-stripped, lowercased, alnum-only name key - must match fetch_fpl.py exactly."""
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


def fetch_lineup(fid, key):
    """/fixtures/lineups -> {home:{...}, away:{...}} by side, or None."""
    lj, rem = api("/fixtures/lineups?fixture=%s" % fid, key)
    if lj.get("errors"):
        return None, rem
    out = {}
    for tb in (lj.get("response") or []):
        side = "home" if ((tb.get("team") or {}).get("name")) else None
        nm = (tb.get("team") or {}).get("name")
        entry = {
            "team": nm, "formation": tb.get("formation"),
            "startXI": [((x.get("player") or {}).get("name")) for x in (tb.get("startXI") or [])],
            "subs": [((x.get("player") or {}).get("name")) for x in (tb.get("substitutes") or [])],
            "coach": (tb.get("coach") or {}).get("name"),
        }
        out[nm] = entry  # keyed by team name; build_epl maps to home/away
    return (out or None), rem


def fetch_stats(fid, key):
    """/fixtures/statistics -> {team_name: corner_kicks}, or None."""
    sj, rem = api("/fixtures/statistics?fixture=%s" % fid, key)
    if sj.get("errors"):
        return None, rem
    out = {}
    for tb in (sj.get("response") or []):
        nm = (tb.get("team") or {}).get("name")
        c = None
        for st in (tb.get("statistics") or []):
            if st.get("type") == "Corner Kicks":
                c = st.get("value"); break
        if nm is not None:
            out[nm] = c
    return (out or None), rem


def main():
    key = os.environ.get("APIFOOTBALL_KEY")
    if not key:
        raise SystemExit("APIFOOTBALL_KEY not set (add it as a repo secret)")
    g = time.gmtime()
    season_env = os.environ.get("APIFOOTBALL_SEASON") or str(g.tm_year - (0 if g.tm_mon >= 7 else 1))
    seasons = [s.strip() for s in season_env.split(",") if s.strip()]   # comma list, e.g. "2024,2025" = both last seasons
    max_run = int(os.environ.get("APIFOOTBALL_MAX_PER_RUN") or 90)

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "apifootball_stats.json")
    meta_path = os.path.join(out_dir, "apifootball_meta.json")

    def _load(path, empty):
        # accumulate across seasons; only wipe if a previously-stored season is no longer requested
        obj = dict(empty)
        if os.path.exists(path):
            try:
                obj = json.load(open(path))
                stored = obj.get("seasons") or ([str(obj.get("season"))] if obj.get("season") else [])
                if not set(stored).issubset(set(seasons)):
                    obj = dict(empty)
                else:
                    obj["seasons"] = sorted(set(stored) | set(seasons))
            except Exception:  # noqa: BLE001
                obj = dict(empty)
        obj["seasons"] = obj.get("seasons") or list(seasons)
        obj.pop("season", None)   # migrate the old single-season field
        return obj

    store = _load(out_path, {"updated": 0, "seasons": list(seasons), "rows": {}})
    store.setdefault("rows", {})
    meta = _load(meta_path, {"updated": 0, "seasons": list(seasons), "fixtures": {}, "lineups": {}})
    meta.setdefault("fixtures", {}); meta.setdefault("lineups", {})

    done = set()
    for rows in store.get("rows", {}).values():
        for r in rows:
            done.add(str(r.get("fixture_id")))

    n = 0            # fixtures pulled this run, shared across seasons (respects the per-run cap)
    rem = None
    for season in seasons:
        if n >= max_run or (rem is not None and rem <= QUOTA_FLOOR):
            break
        fx, rem = api("/fixtures?league=%d&season=%s" % (LEAGUE, season), key)
        if fx.get("errors"):
            print("api-football error on fixtures list (season %s): %s" % (season, fx["errors"]))
            continue
        fixtures = fx.get("response") or []
        for f in fixtures:   # referee/venue/goals for every fixture (free - already fetched)
            fo = f.get("fixture") or {}
            ven = fo.get("venue") or {}; tm = f.get("teams") or {}; gl = f.get("goals") or {}
            ht = ((f.get("score") or {}).get("halftime") or {})
            meta["fixtures"][str(fo.get("id"))] = {
                "referee": fo.get("referee"), "venue": ven.get("name"), "city": ven.get("city"),
                "home": (tm.get("home") or {}).get("name"), "away": (tm.get("away") or {}).get("name"),
                "date": fo.get("date"), "gh": gl.get("home"), "ga": gl.get("away"),
                "hh": ht.get("home"), "ha": ht.get("away"),    # half-time score (free) -> By The Halves
                "round": (f.get("league") or {}).get("round"), "status": ((fo.get("status") or {}).get("short")),
            }
        finished = [f for f in fixtures
                    if (((f.get("fixture") or {}).get("status") or {}).get("short") in ("FT", "AET", "PEN"))
                    and str((f.get("fixture") or {}).get("id")) not in done]
        total_fin = sum(1 for f in fixtures if (((f.get("fixture") or {}).get("status") or {}).get("short") in ("FT", "AET", "PEN")))
        print("season %s: %d finished total, %d to pull this run (daily remaining ~%s)" % (season, total_fin, len(finished), rem))
        for f in finished:
            if n >= max_run:
                print("hit per-run cap (%d); resume next run" % max_run); break
            if rem is not None and rem <= QUOTA_FLOOR:
                print("near daily quota (%s left); resume next run" % rem); break
            fx_o = f.get("fixture") or {}
            fid = str(fx_o.get("id")); date = fx_o.get("date")
            home = ((f.get("teams") or {}).get("home") or {}).get("name")
            away = ((f.get("teams") or {}).get("away") or {}).get("name")
            if fid not in meta["lineups"]:
                try:
                    lu, rem = fetch_lineup(fid, key)
                    if lu:
                        meta["lineups"][fid] = lu
                    time.sleep(PACE)
                except urllib.error.HTTPError as e:
                    if e.code == 429:
                        print("rate/quota hit (lineups); resume next run"); break
            fxm = meta["fixtures"].get(fid) or {}
            if not fxm.get("corners"):
                try:
                    sc, rem = fetch_stats(fid, key)
                    if sc:
                        fxm["corners"] = {"home": sc.get(fxm.get("home")), "away": sc.get(fxm.get("away"))}
                        meta["fixtures"][fid] = fxm
                    time.sleep(PACE)
                except urllib.error.HTTPError as e:
                    if e.code == 429:
                        print("rate/quota hit (stats); resume next run"); break
            try:
                pj, rem = api("/fixtures/players?fixture=%s" % fid, key)
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    print("rate/quota hit; resume next run"); break
                raise
            if pj.get("errors"):
                print("  fixture %s error: %s" % (fid, pj["errors"])); time.sleep(PACE); continue
            for tb in (pj.get("response") or []):
                tname = ((tb.get("team")) or {}).get("name")
                is_home = (tname == home); opp = away if is_home else home
                for pl in (tb.get("players") or []):
                    name = ((pl.get("player")) or {}).get("name"); k = nmkey(name)
                    if not k:
                        continue
                    row = {"fixture_id": fid, "date": date, "opp": opp, "home": is_home}
                    row.update(player_stat(pl.get("statistics")))
                    store["rows"].setdefault(k, []).append(row)
            done.add(fid); n += 1
            if n % 10 == 0:
                store["updated"] = int(time.time()); json.dump(store, open(out_path, "w"), separators=(",", ":"))
                print("  ...%d fixtures this run, %d players stored" % (n, len(store["rows"])))
            time.sleep(PACE)

    # Championship (league 40) finished results so newly-promoted sides carry recent form
    # into the shell's Match sections. Scores + half-time ride the fixtures list (1 call/season).
    LEAGUE_CH = 40
    for season in seasons:
        if rem is not None and rem <= QUOTA_FLOOR:
            print("near daily quota - skipping Championship pull this run"); break
        chfx, rem = api("/fixtures?league=%d&season=%s" % (LEAGUE_CH, season), key)
        if chfx.get("errors"):
            print("api-football error on Championship fixtures (season %s): %s" % (season, chfx["errors"])); continue
        nch = 0
        for f in (chfx.get("response") or []):
            fo = f.get("fixture") or {}
            if ((fo.get("status") or {}).get("short")) not in ("FT", "AET", "PEN"):
                continue
            tm = f.get("teams") or {}; gl = f.get("goals") or {}
            ht = ((f.get("score") or {}).get("halftime") or {}); ven = fo.get("venue") or {}
            meta["fixtures"][str(fo.get("id"))] = {
                "referee": fo.get("referee"), "venue": ven.get("name"), "city": ven.get("city"),
                "home": (tm.get("home") or {}).get("name"), "away": (tm.get("away") or {}).get("name"),
                "date": fo.get("date"), "gh": gl.get("home"), "ga": gl.get("away"),
                "hh": ht.get("home"), "ha": ht.get("away"),
                "round": (f.get("league") or {}).get("round"),
                "status": ((fo.get("status") or {}).get("short")), "comp": "ch",
            }
            nch += 1
        print("Championship %s: %d finished results added (for promoted-side form)" % (season, nch))

    store["updated"] = int(time.time()); json.dump(store, open(out_path, "w"), separators=(",", ":"))
    meta["updated"] = int(time.time()); json.dump(meta, open(meta_path, "w"), separators=(",", ":"))
    n_rows = sum(len(v) for v in store["rows"].values())
    print("seasons %s | apifootball_meta.json: %d fixtures (referee/venue), %d lineups" % (",".join(seasons), len(meta["fixtures"]), len(meta["lineups"])))
    print("apifootball_stats.json: %d players, %d match rows (%d fixtures pulled this run)" % (len(store["rows"]), n_rows, n))


if __name__ == "__main__":
    main()
