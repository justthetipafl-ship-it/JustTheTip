#!/usr/bin/env python3
"""MLB home-plate umpires -> mlb/data/umpires.json

    usage:  python3 scripts/fetch_umpires.py mlb/data [--max 400] [--seasons 2025,2026]

Why: the home-plate umpire sets the strike zone, and that moves strikeouts and walks - the markets
behind Strike Time, Grinders and Free Passes. We already do this for EPL referees (cards, fouls).

statsapi's boxscore carries both halves of the job in one request: the officials, and each side's
strikeout and walk totals. So one pass builds the tendencies AND the per-game history.

Incremental by design: processed gamePks are remembered, so the first run backfills (capped by
--max per run, ~2,400 games a season) and later runs cost one request per new game. Nothing is
overwritten on failure; a partial run just resumes next time.

Output:
  {updated, league:{kPerGame,bbPerGame,games},
   umps:{"Name":{games,kPerGame,bbPerGame,kIndex,bbIndex}},        # index: 1.00 = league average
   assignments:{"<gamePk>":{"ump":"Name","date":"YYYY-MM-DD"}},    # today's games once announced
   seen:[gamePk...]}
"""
import json, os, sys, time
import urllib.request

API = "https://statsapi.mlb.com/api/v1"
UA = {"User-Agent": "Mozilla/5.0 JTT"}


def get(url, tries=3):
    for i in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30))
        except Exception as e:
            if i == tries - 1:
                print(f"[umpires] fetch failed: {url.split('?')[0]} ({e})")
                return None
            time.sleep(1.5 * (i + 1))


def plate_umpire(box):
    """Home-plate umpire from a boxscore payload, whatever key the feed uses."""
    for o in (box.get("officials") or []):
        t = str(o.get("officialType") or "").lower()
        if "home plate" in t or t == "hp":
            return ((o.get("official") or {}).get("fullName") or "").strip() or None
    return None


def game_ks_bbs(box):
    """Strikeouts and walks by the BATTERS in this game (both sides), from a boxscore payload."""
    k = bb = 0
    teams = box.get("teams") or {}
    for side in ("home", "away"):
        bat = (((teams.get(side) or {}).get("teamStats") or {}).get("batting") or {})
        if not bat:
            return None, None
        k += int(bat.get("strikeOuts") or 0)
        bb += int(bat.get("baseOnBalls") or 0)
    return k, bb


def rebuild(state):
    """Recompute league averages and per-umpire indices from the stored per-game rows."""
    rows = state.get("games") or {}
    tot_k = tot_bb = 0
    per = {}
    for _, g in rows.items():
        ump, k, bb = g.get("ump"), g.get("k"), g.get("bb")
        if not ump or k is None or bb is None:
            continue
        tot_k += k; tot_bb += bb
        e = per.setdefault(ump, {"games": 0, "k": 0, "bb": 0})
        e["games"] += 1; e["k"] += k; e["bb"] += bb
    n = sum(e["games"] for e in per.values())
    lg_k = tot_k / n if n else 0
    lg_bb = tot_bb / n if n else 0
    umps = {}
    for name, e in per.items():
        kpg = e["k"] / e["games"]; bpg = e["bb"] / e["games"]
        umps[name] = {"games": e["games"], "kPerGame": round(kpg, 2), "bbPerGame": round(bpg, 2),
                      "kIndex": round(kpg / lg_k, 3) if lg_k else 1.0,
                      "bbIndex": round(bpg / lg_bb, 3) if lg_bb else 1.0}
    state["league"] = {"kPerGame": round(lg_k, 2), "bbPerGame": round(lg_bb, 2), "games": n}
    state["umps"] = dict(sorted(umps.items(), key=lambda kv: -kv[1]["games"]))
    return state


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "mlb/data"
    cap = 400
    seasons = None
    for i, a in enumerate(sys.argv):
        if a == "--max" and i + 1 < len(sys.argv): cap = int(sys.argv[i + 1])
        if a == "--seasons" and i + 1 < len(sys.argv): seasons = sys.argv[i + 1].split(",")
    path = os.path.join(out_dir, "umpires.json")
    try:
        state = json.load(open(path))
    except Exception:
        state = {}
    state.setdefault("games", {})
    state.setdefault("assignments", {})
    if not seasons:
        import datetime
        y = datetime.date.today().year
        seasons = [str(y - 1), str(y)]

    todo, upcoming = [], []
    for s in seasons:
        sched = get(f"{API}/schedule?sportId=1&season={s}&gameType=R&fields=dates,games,gamePk,officialDate,status,abstractGameState")
        if not sched:
            continue
        for d in (sched.get("dates") or []):
            for g in (d.get("games") or []):
                pk = str(g.get("gamePk"))
                st = ((g.get("status") or {}).get("abstractGameState") or "")
                if st == "Final" and pk not in state["games"]:
                    todo.append((pk, g.get("officialDate")))
                elif st in ("Preview", "Live"):
                    upcoming.append((pk, g.get("officialDate")))

    done = 0
    for pk, date in todo[:cap]:
        box = get(f"{API}/game/{pk}/boxscore")
        if not box:
            continue
        ump = plate_umpire(box)
        k, bb = game_ks_bbs(box)
        state["games"][pk] = {"date": date, "ump": ump, "k": k, "bb": bb}
        done += 1
        time.sleep(0.12)                      # be polite to a free API

    # today's and upcoming games: record the plate umpire as soon as it is announced
    for pk, date in upcoming[:120]:
        box = get(f"{API}/game/{pk}/boxscore")
        if not box:
            continue
        ump = plate_umpire(box)
        if ump:
            state["assignments"][pk] = {"ump": ump, "date": date}
        time.sleep(0.12)

    state = rebuild(state)
    state["updated"] = time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime())
    remaining = max(0, len(todo) - done)
    json.dump(state, open(path, "w"), separators=(",", ":"))
    named = sum(1 for g in state["games"].values() if g.get("ump"))
    print(f"[umpires] +{done} games this run ({remaining} still to backfill) | stored {len(state['games'])}"
          f" ({named} with an umpire named) | {len(state['umps'])} umpires"
          f" | league {state['league']['kPerGame']} K, {state['league']['bbPerGame']} BB per game"
          f" | {len(state['assignments'])} upcoming assignments -> {path}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        box = {"officials": [{"official": {"fullName": "Pat Hoberg"}, "officialType": "Home Plate"},
                             {"official": {"fullName": "Angel Hernandez"}, "officialType": "First Base"}],
               "teams": {"home": {"teamStats": {"batting": {"strikeOuts": 9, "baseOnBalls": 4}}},
                         "away": {"teamStats": {"batting": {"strikeOuts": 12, "baseOnBalls": 2}}}}}
        assert plate_umpire(box) == "Pat Hoberg", plate_umpire(box)
        assert game_ks_bbs(box) == (21, 6), game_ks_bbs(box)
        assert plate_umpire({"officials": []}) is None
        st = {"games": {"1": {"ump": "A", "k": 20, "bb": 6}, "2": {"ump": "A", "k": 16, "bb": 4},
                        "3": {"ump": "B", "k": 10, "bb": 8}, "4": {"ump": None, "k": 99, "bb": 99}}}
        st = rebuild(st)
        assert st["league"]["games"] == 3 and abs(st["league"]["kPerGame"] - 15.33) < 0.02, st["league"]
        assert st["umps"]["A"]["games"] == 2 and abs(st["umps"]["A"]["kIndex"] - 1.174) < 0.01, st["umps"]["A"]
        assert abs(st["umps"]["B"]["kIndex"] - 0.652) < 0.01, st["umps"]["B"]
        print("selftest ok | plate umpire, K/BB totals, league averages and indices:",
              json.dumps(st["umps"], separators=(",", ":")))
    else:
        main()
