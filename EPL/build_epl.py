#!/usr/bin/env python3
"""
JTT EPL build - merges the source pulls into the unified shell's data files.

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
  EPL/data/fixture.json   [{home, away, date, gw, venue, referee, lineups}]  (next unfinished GW; venue/ref/lineups when API-Football meta present)
  EPL/data/referees.json  {refStats: {name: {matches, cardsPG, foulsPG, ydsPG, redPG}}}   [when meta present]
  (teams.json rows gain {gf, ga, cardsPG, venue} when API-Football meta present)

The join: FPL gamelogs are THIS season; API-Football backfill is LAST season. We union both by
player (nmkey) and match-day, preferring FPL for goals/assists/tackles/cards/saves and taking
shots/sot/fouls/passes from API-Football - with API-Football supplying the FPL-shaped fields too
for last-season rows where FPL has none. Contract field names come from EPL/config.js.
"""
import json
import datetime
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


# canonical club key so FPL names and API-Football names line up
_CANON = {
    "wolves": "wolverhampton", "wolverhamptonwanderers": "wolverhampton",
    "brightonhovealbion": "brighton", "spurs": "tottenham", "tottenhamhotspur": "tottenham",
    "manchesterunited": "manutd", "manchesterutd": "manutd", "manutd": "manutd",
    "manchestercity": "mancity", "mancity": "mancity",
    "newcastleunited": "newcastle", "westhamunited": "westham",
    "nottinghamforest": "nottingham", "nottmforest": "nottingham",
    "afcbournemouth": "bournemouth", "lutontown": "luton", "ipswichtown": "ipswich",
    "leicestercity": "leicester", "leedsunited": "leeds",
}
def canon(name):
    k = "".join(c for c in (name or "").lower() if c.isalnum())
    return _CANON.get(k, k)


def build_referees(meta, stats, players):
    """Referee card/foul tendencies from finished fixtures (referee per fixture x cards/fouls in it)."""
    if not meta:
        return None
    team_by_nm = {p.get("nmkey"): p.get("team") for p in players.get("players", [])}
    # per-fixture card + foul totals from the player rows
    fx_cards, fx_fouls, fx_red = {}, {}, {}
    for nm, rows in (stats or {}).get("rows", {}).items():
        for r in rows:
            fid = str(r.get("fixture_id"))
            fx_cards[fid] = fx_cards.get(fid, 0) + (r.get("yellow") or 0) + (r.get("red") or 0)
            fx_red[fid] = fx_red.get(fid, 0) + (r.get("red") or 0)
            fx_fouls[fid] = fx_fouls.get(fid, 0) + (r.get("fouls") or 0)
    agg = {}
    for fid, fx in (meta.get("fixtures") or {}).items():
        ref = fx.get("referee")
        if not ref or fx.get("status") not in ("FT", "AET", "PEN"):
            continue
        d = agg.setdefault(ref, {"matches": 0, "cards": 0, "fouls": 0, "red": 0})
        d["matches"] += 1
        d["cards"] += fx_cards.get(fid, 0)
        d["fouls"] += fx_fouls.get(fid, 0)
        d["red"] += fx_red.get(fid, 0)
    refStats = {}
    for ref, d in agg.items():
        n = max(1, d["matches"])
        refStats[ref] = {
            "matches": d["matches"],
            "cardsPG": round(d["cards"] / n, 2),
            "foulsPG": round(d["fouls"] / n, 2),
            "redPG": round(d["red"] / n, 3),
        }
    return {"refStats": refStats}


def team_stats_and_venues(meta, stats, players):
    """Per-team goals for/against, cards per game, and home venue - from the fixture meta + player rows."""
    if not meta:
        return {}
    team_by_nm = {p.get("nmkey"): p.get("team") for p in players.get("players", [])}
    # per-fixture, per-side cards (side derived from the player's 'home' flag)
    fx_side_cards = {}   # (fid, is_home) -> cards
    for nm, rows in (stats or {}).get("rows", {}).items():
        for r in rows:
            key = (str(r.get("fixture_id")), bool(r.get("home")))
            fx_side_cards[key] = fx_side_cards.get(key, 0) + (r.get("yellow") or 0) + (r.get("red") or 0)
    agg = {}   # canon(team) -> {name, gf, ga, cards, games, venue counts}
    for fid, fx in (meta.get("fixtures") or {}).items():
        if fx.get("status") not in ("FT", "AET", "PEN"):
            continue
        h, a, gh, ga = fx.get("home"), fx.get("away"), fx.get("gh"), fx.get("ga")
        ven = fx.get("venue")
        for team, is_home in ((h, True), (a, False)):
            if not team:
                continue
            d = agg.setdefault(canon(team), {"name": team, "gf": 0, "ga": 0, "cards": 0, "games": 0, "ven": {}, "form": [], "cf": 0, "ca": 0, "cn": 0})
            gfor = gh if is_home else ga
            gagn = ga if is_home else gh
            if gfor is not None:
                d["gf"] += gfor
            if gagn is not None:
                d["ga"] += gagn
            if gfor is not None and gagn is not None:
                d["form"].append((fx.get("date") or "", "W" if gfor > gagn else ("L" if gfor < gagn else "D")))
            d["cards"] += fx_side_cards.get((fid, is_home), 0)
            d["games"] += 1
            corn = fx.get("corners") or {}
            cf = corn.get("home") if is_home else corn.get("away")
            ca = corn.get("away") if is_home else corn.get("home")
            if cf is not None and ca is not None:
                d["cf"] += cf; d["ca"] += ca; d["cn"] += 1
            if is_home and ven:
                d["ven"][ven] = d["ven"].get(ven, 0) + 1
    out = {}
    for ck, d in agg.items():
        n = max(1, d["games"])
        venue = max(d["ven"], key=d["ven"].get) if d["ven"] else None
        form = "".join(r for _, r in sorted(d["form"], key=lambda x: x[0])[-6:])  # last 6 results, oldest->newest
        cn = max(1, d["cn"])
        out[ck] = {"gfPG": round(d["gf"] / n, 2), "gaPG": round(d["ga"] / n, 2),
                   "cardsPG": round(d["cards"] / n, 2), "venue": venue, "games": d["games"], "form": form,
                   "cornersFor": (round(d["cf"] / cn, 2) if d["cn"] else None),
                   "cornersAgainst": (round(d["ca"] / cn, 2) if d["cn"] else None)}
    return out


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
        # YYYYMMDD round so the shell's dedupe keys each match-day distinctly (EPL has no AFL-style rounds,
        # and the same two clubs meet twice a season - without this, home+away fixtures would collapse into one)
        "RoundName": (dy or "").replace("-", ""),
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


def build_teams(fpl_players, boot, tstats):
    # fpl_players carries a {teams: {id:{name,short}}} map; enrich from bootstrap if present
    tmap = {}
    for t in (boot or {}).get("teams", []):
        code = t.get("code")
        # official Premier League club badge (the PL's own CDN, same one FPL serves), keyed by club code
        logo = ("https://resources.premierleague.com/premierleague/badges/70/t%d.png" % code) if code else None
        tmap[t["name"]] = {"team": t["name"], "short": t.get("short_name"), "id": t.get("id"), "code": code, "logo": logo}
    for tid, t in (fpl_players.get("teams") or {}).items():
        nm = t.get("name")
        if nm and nm not in tmap:
            tmap[nm] = {"team": nm, "short": t.get("short")}
    for nm, row in tmap.items():
        st = (tstats or {}).get(canon(nm))
        if st:
            row["gf"], row["ga"] = st.get("gfPG"), st.get("gaPG")
            row["cardsPG"], row["venue"] = st.get("cardsPG"), st.get("venue")
            row["form"] = st.get("form")
            row["cornersFor"], row["cornersAgainst"] = st.get("cornersFor"), st.get("cornersAgainst")
    return list(tmap.values())


def build_fixtures(boot, fixtures, boot_teams, meta, tstats):
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
    # index the API-Football fixture meta by (canon home, canon away, date10) for matching
    mfx = (meta or {}).get("fixtures", {})
    mlu = (meta or {}).get("lineups", {})
    by_key = {}
    for fid, fx in mfx.items():
        k = (canon(fx.get("home")), canon(fx.get("away")), day(fx.get("date")))
        by_key[k] = fid
    out = []
    for f in (fixtures or []):
        if nxt is not None and f.get("event") != nxt:
            continue
        h, a = id2name.get(f.get("team_h")), id2name.get(f.get("team_a"))
        if not h or not a:
            continue
        row = {"home": h, "away": a, "date": f.get("kickoff_time"), "gw": f.get("event")}
        # venue = home club's stadium (known from the team stats), always available
        hv = (tstats or {}).get(canon(h))
        if hv and hv.get("venue"):
            row["venue"] = hv["venue"]
        # referee + lineups from the matched API-Football fixture (may be absent until assigned/posted)
        fid = by_key.get((canon(h), canon(a), day(f.get("kickoff_time"))))
        if fid:
            fx = mfx.get(fid) or {}
            if fx.get("referee"):
                row["referee"] = fx["referee"]
            if not row.get("venue") and fx.get("venue"):
                row["venue"] = fx["venue"]
            lu = mlu.get(fid)
            if lu:
                hn, an = fx.get("home"), fx.get("away")
                row["lineups"] = {"home": lu.get(hn), "away": lu.get(an)}
        out.append(row)
    return out


DVP_STATS = ["goals", "assists", "shots", "shotsOn", "tackles", "saves",
             "keyPasses", "passes", "cards", "foulsCommitted", "conceded", "min"]


def build_dvp(gamelogs, players):
    """List of {team, pos, <stat>: avg allowed} - what each team allows opposing players of each position."""
    name2pos = {}
    for p in players:
        nm = p.get("name")
        if nm:
            name2pos[nm] = p.get("pos") or p.get("position")
    agg = {}
    for r in gamelogs:
        opp = r.get("Opp")
        pos = name2pos.get(r.get("Player"))
        if not opp or not pos:
            continue
        d = agg.setdefault((opp, pos), {})
        for stat in DVP_STATS:
            v = r.get(stat)
            if v is not None:
                try:
                    d.setdefault(stat, []).append(float(v))
                except Exception:
                    pass
    out = []
    for (opp, pos), stats in agg.items():
        item = {"team": opp, "pos": pos}
        for stat, vals in stats.items():
            if vals:
                item[stat] = round(sum(vals) / len(vals), 3)
        out.append(item)
    return out


def build_meta(players, gamelogs, teams, fixture, dvp):
    years = sorted({str(r.get("Year")) for r in gamelogs if r.get("Year") is not None})
    cur = years[-1] if years else str(datetime.date.today().year)
    return {
        "version": str(int(datetime.datetime.now(datetime.timezone.utc).timestamp())),
        "created": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "league": "epl", "label": "EPL", "day": datetime.date.today().isoformat(),
        "gameMin": 45, "sportKey": "soccer_epl",
        "seasons": years, "currentSeason": cur, "gamelogFiles": ["gamelogs.json"],
        "summary": {"players": len(players), "teams": len(teams), "dvp": len(dvp),
                    "gamelogs": len(gamelogs), "fixtures": len(fixture)},
    }


def build_teams_form(gamelogs):
    """Per team: recent for/against averages (from player gamelogs summed to team-match totals)."""
    STATS = ["goals", "assists", "shots", "shotsOn", "tackles", "passes", "saves", "cards"]
    match = {}
    for r in gamelogs:
        mid, tm = r.get("MatchId"), r.get("Team")
        if not mid or not tm:
            continue
        t = match.setdefault(mid, {}).setdefault(tm, {})
        for st in STATS:
            v = r.get(st)
            if v is not None:
                try:
                    t[st] = t.get(st, 0) + float(v)
                except Exception:
                    pass
    tmatch = {}
    for mid, teams in match.items():
        names = list(teams.keys())
        if len(names) != 2:
            continue
        a, b = names
        tmatch.setdefault(a, []).append((teams[a], teams[b]))
        tmatch.setdefault(b, []).append((teams[b], teams[a]))
    out = []
    for tm, ms in tmatch.items():
        ms = ms[-10:]
        item = {"team": tm, "matches": len(ms)}
        for st in STATS:
            fv = [f[st] for (f, ag) in ms if st in f]
            av = [ag[st] for (f, ag) in ms if st in ag]
            if fv:
                item[st] = round(sum(fv) / len(fv), 2)
            if av:
                item[st + "_a"] = round(sum(av) / len(av), 2)
        out.append(item)
    return out


def main():
    fpl_players = load("fpl_players.json")
    if not fpl_players:
        raise SystemExit("EPL/data/fpl_players.json missing - run the FPL workflow first")
    fpl_logs = load("fpl_gamelogs.json") or {"logs": {}}
    api = load("apifootball_stats.json")  # may be None until the paid key runs
    meta = load("apifootball_meta.json")  # referee/venue/lineups; None until the paid key runs

    boot = fetch(FPL + "/bootstrap-static/")
    fixtures = fetch(FPL + "/fixtures/")

    gamelogs = build_gamelogs(fpl_players, fpl_logs, api)
    counts = {}
    for r in gamelogs:
        counts[r["Player"]] = counts.get(r["Player"], 0) + 1
    players = build_players(fpl_players, counts)
    referees = build_referees(meta, api, fpl_players)
    tstats = team_stats_and_venues(meta, api, fpl_players)
    teams = build_teams(fpl_players, boot, tstats)
    fixture = build_fixtures(boot, fixtures, boot.get("teams", []), meta, tstats)
    dvp = build_dvp(gamelogs, players)
    teams_form = build_teams_form(gamelogs)
    meta_out = build_meta(players, gamelogs, teams, fixture, dvp)

    os.makedirs(DATA, exist_ok=True)
    outputs = [("players.json", players), ("gamelogs.json", gamelogs),
               ("teams.json", teams), ("fixture.json", fixture),
               ("dvp.json", dvp), ("teams_form.json", teams_form), ("meta.json", meta_out)]
    if referees is not None:
        outputs.append(("referees.json", referees))
    for name, obj in outputs:
        json.dump(obj, open(os.path.join(DATA, name), "w"), separators=(",", ":"))
    print("built: players %d | gamelogs %d | teams %d | fixture %d | dvp %d | referees %s | meta: %s"
          % (len(players), len(gamelogs), len(teams), len(fixture), len(dvp),
             (len((referees or {}).get("refStats", {})) if referees else "-"),
             "merged" if meta else "not present yet"))


if __name__ == "__main__":
    main()
