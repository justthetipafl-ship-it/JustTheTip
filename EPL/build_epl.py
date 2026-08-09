#!/usr/bin/env python3
"""
JTT EPL build — merges the source pulls into the unified shell's data files.

Reads (committed by the other workflows):
  EPL/data/fpl_players.json      player index + prices + set-piece takers + last-season block
  EPL/data/fpl_gamelogs.json     FPL per-gameweek rows (this season), by FPL id
  EPL/data/apifootball_stats.json  API-Football per-match rows (shots/fouls/goals/...), by nmkey  [optional]
Fetches (keyless, from the FPL API):
  bootstrap-static  -> teams; /fixtures/ -> the fixture list

Emits (the shapes epl/scoring.js + signals.js + the shell consume):
  EPL/data/players.json   [{name, team, position, pos, matches, price, pens_order, ..., nmkey}]
  EPL/data/gamelogs.json  flat list of merged rows, grouped by the shell on Player
  EPL/data/teams.json     [{team, short}]
  EPL/data/fixture.json   [{home, away, date, gw}]  (next unfinished gameweek)

The join: FPL gamelogs are THIS season; API-Football backfill is LAST season. We union both by
player (nmkey) and match-day, preferring FPL for goals/assists/tackles/cards/saves and taking
shots/sot/fouls/passes from API-Football — with API-Football supplying the FPL-shaped fields too
for last-season rows where FPL has none. Contract field names come from EPL/config.js.
"""
import json
import os
import time
import urllib.request

FPL = "https://fantasy.premierleague.com/api"
UA = {"User-Agent": "python-requests/2.31.0"}
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def fetch(url, tries=4):
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError("fetch failed: %s (%s)" % (url, last))


def load(name):
    p = os.path.join(DATA, name)
    return json.load(open(p)) if os.path.exists(p) else None


def day(dt):
    return (dt or "")[:10]


def year_of(dt):
    return int((dt or "2026")[:4]) if (dt or "")[:4].isdigit() else 2026


def match_id(dy, team, opp):
    return dy + "|" + "|".join(sorted([team or "", opp or ""]))


def _num(v):
    return v if isinstance(v, (int, float)) else None


def merge_row(p, f, a, dy):
    """One merged gamelog row. f = FPL row (or None), a = API-Football row (or None)."""
    team = p.get("team")
    opp = (f or {}).get("opp") or (a or {}).get("opp")
    home = (f or {}).get("home")
    if home is None:
        home = (a or {}).get("home")
    yc = (f or {}).get("YC")
    if yc is None:
        yc = (a or {}).get("yellow")
    rc = (f or {}).get("RC")
    if rc is None:
        rc = (a or {}).get("red")
    yc = yc or 0
    rc = rc or 0

    def pick(fk, ak):
        v = (f or {}).get(fk)
        return v if v is not None else (a or {}).get(ak)

    return {
        "Player": p.get("name"), "Team": team, "Opp": opp, "home": home,
        "Year": year_of(dy), "Date": dy, "date": dy, "MatchId": match_id(dy, team, opp),
        "min": pick("min", "min"),
        # FPL-first fields (API-Football fallback for last-season rows)
        "goals": pick("G", "goals"), "assists": pick("A", "assists"),
        "tackles": pick("tackles", "tackles"), "saves": pick("saves", "saves"),
        "cs": pick("CS", "cs") if (f or a) else None, "conceded": pick("GC", "conceded"),
        "yellowCard": yc, "redCard": rc, "cards": yc + rc,
        "xg": _num((f or {}).get("xG")), "xa": _num((f or {}).get("xA")),
        # API-Football-only fields
        "shots": (a or {}).get("shots"), "shotsOn": (a or {}).get("sot"),
        "keyPasses": (a or {}).get("key_passes"), "foulsCommitted": (a or {}).get("fouls"),
        "foulsDrawn": (a or {}).get("fouls_drawn"), "passes": (a or {}).get("passes"),
    }


def build_gamelogs(fpl_players, fpl_logs, api):
    api_rows = (api or {}).get("rows", {})
    logs = (fpl_logs or {}).get("logs", {})
    out = []
    for p in fpl_players.get("players", []):
        nmk, fid = p.get("nmkey"), str(p.get("id"))
        fpl_by_day = {}
        for r in logs.get(fid, []):
            fpl_by_day[day(r.get("date"))] = r
        api_by_day = {}
        for r in api_rows.get(nmk, []):
            api_by_day[day(r.get("date"))] = r
        for dy in sorted(set(fpl_by_day) | set(api_by_day)):
            if not dy:
                continue
            out.append(merge_row(p, fpl_by_day.get(dy), api_by_day.get(dy), dy))
    return out


def build_players(fpl_players, match_counts):
    out = []
    for p in fpl_players.get("players", []):
        out.append({
            "name": p.get("name"), "team": p.get("team"), "teamShort": p.get("teamShort"),
            "position": p.get("pos"), "pos": p.get("pos"), "nmkey": p.get("nmkey"),
            "matches": match_counts.get(p.get("name"), 0),
            "price": p.get("price"), "owned": p.get("owned"), "form": p.get("form"),
            "status": p.get("status"), "news": p.get("news"),
            "pens_order": p.get("pens_order"), "corners_fk_order": p.get("corners_fk_order"),
            "direct_fk_order": p.get("direct_fk_order"),
            "G": p.get("G"), "A": p.get("A"), "xG": p.get("xG"), "xA": p.get("xA"),
            "last": p.get("last"),
        })
    return out


def build_teams(fpl_players, boot):
    # fpl_players carries a {teams: {id:{name,short}}} map; enrich from bootstrap if present
    tmap = {}
    for t in (boot or {}).get("teams", []):
        tmap[t["name"]] = {"team": t["name"], "short": t.get("short_name"), "id": t.get("id")}
    for tid, t in (fpl_players.get("teams") or {}).items():
        nm = t.get("name")
        if nm and nm not in tmap:
            tmap[nm] = {"team": nm, "short": t.get("short")}
    return list(tmap.values())


def build_fixtures(boot, fixtures, boot_teams):
    id2name = {t["id"]: t["name"] for t in (boot or {}).get("teams", [])}
    # next unfinished gameweek
    nxt = None
    for e in (boot or {}).get("events", []):
        if e.get("is_next"):
            nxt = e["id"]
            break
    if nxt is None:
        unfinished = [f for f in (fixtures or []) if not f.get("finished")]
        nxt = min((f.get("event") for f in unfinished if f.get("event")), default=None)
    out = []
    for f in (fixtures or []):
        if nxt is not None and f.get("event") != nxt:
            continue
        h, a = id2name.get(f.get("team_h")), id2name.get(f.get("team_a"))
        if not h or not a:
            continue
        out.append({"home": h, "away": a, "date": f.get("kickoff_time"), "gw": f.get("event")})
    return out


def main():
    fpl_players = load("fpl_players.json")
    if not fpl_players:
        raise SystemExit("EPL/data/fpl_players.json missing — run the FPL workflow first")
    fpl_logs = load("fpl_gamelogs.json") or {"logs": {}}
    api = load("apifootball_stats.json")  # may be None until the paid key runs

    boot = fetch(FPL + "/bootstrap-static/")
    fixtures = fetch(FPL + "/fixtures/")

    gamelogs = build_gamelogs(fpl_players, fpl_logs, api)
    counts = {}
    for r in gamelogs:
        counts[r["Player"]] = counts.get(r["Player"], 0) + 1
    players = build_players(fpl_players, counts)
    teams = build_teams(fpl_players, boot)
    fixture = build_fixtures(boot, fixtures, boot.get("teams", []))

    os.makedirs(DATA, exist_ok=True)
    for name, obj in [("players.json", players), ("gamelogs.json", gamelogs),
                      ("teams.json", teams), ("fixture.json", fixture)]:
        json.dump(obj, open(os.path.join(DATA, name), "w"), separators=(",", ":"))
    print("built: players %d | gamelogs %d rows | teams %d | fixture %d (next GW) | api-football: %s"
          % (len(players), len(gamelogs), len(teams), len(fixture), "merged" if api else "not present yet"))


if __name__ == "__main__":
    main()
