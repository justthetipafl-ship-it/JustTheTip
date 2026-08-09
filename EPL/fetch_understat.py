#!/usr/bin/env python3
"""
JTT Understat pull — per-player per-match shots / shots-on-target / goals / xG / key passes for EPL.

Understat has no API: each page embeds its data as a hex-escaped JSON string inside JSON.parse('...').
We read the league page for the match list, then each match page's shot-level data, and aggregate it
per player per match. Output merges onto the FPL gamelogs by nmkey (+ date/opponent per row).

Output (EPL/data/):
  understat_shots.json  {updated, season, rows: {nmkey: [ {match_id,date,opp,home,shots,sot,goals,xg,key_passes} ]}}

Resumable: skips matches already stored, so it backfills a season then maintains it incrementally.
Season is the START year: 2025 = 2025/26. Set UNDERSTAT_SEASON in the workflow (default = current).
Runs in a GitHub Action — Understat blocks CORS and rate-limits, so ~6s between match pages.
"""
import json
import os
import re
import time
import unicodedata
import urllib.request

BASE = "https://understat.com"
UA = {"User-Agent": "Mozilla/5.0 (JTT Understat data pull; contact justthetipafl)"}
THROTTLE = 6.0  # seconds between match pages (Understat's empirically safe rate)

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


def fetch(url, tries=4):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError("fetch failed after %d tries: %s (%s)" % (tries, url, last))


def parse_var(html, var):
    """Extract var = JSON.parse('<hex-escaped>') and decode it. Understat hex-escapes every byte."""
    m = re.search(var + r"\s*=\s*JSON\.parse\('(.*?)'\)", html, re.S)
    if not m:
        return None
    raw = m.group(1)
    # \xNN -> the raw byte, then reassemble UTF-8 (accented names are multi-byte)
    step = re.sub(r"\\x([0-9a-fA-F]{2})", lambda x: chr(int(x.group(1), 16)), raw)
    step = step.replace("\\/", "/").replace("\\'", "'")
    try:
        text = step.encode("latin-1", "ignore").decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001
        text = step
    return json.loads(text)


def aggregate_match(shots_data, home_team, away_team, date):
    """Fold shot-level data into per-player rows for one match."""
    by_player = {}   # name -> agg
    key_passes = {}  # assister name -> count
    for side in ("h", "a"):
        team = home_team if side == "h" else away_team
        opp = away_team if side == "h" else home_team
        for sh in (shots_data.get(side) or []):
            name = sh.get("player")
            if not name:
                continue
            a = by_player.setdefault(name, {
                "name": name, "team": team, "opp": opp, "home": side == "h",
                "shots": 0, "sot": 0, "goals": 0, "xg": 0.0, "key_passes": 0,
            })
            a["shots"] += 1
            res = sh.get("result")
            if res in ("Goal", "SavedShot"):
                a["sot"] += 1
            if res == "Goal":
                a["goals"] += 1
            try:
                a["xg"] += float(sh.get("xG") or 0)
            except (TypeError, ValueError):
                pass
            ass = (sh.get("player_assisted") or "").strip()
            if ass:
                key_passes[ass] = key_passes.get(ass, 0) + 1
    for name, kp in key_passes.items():
        if name in by_player:
            by_player[name]["key_passes"] = kp
    for a in by_player.values():
        a["xg"] = round(a["xg"], 3)
    return list(by_player.values())


def main():
    season = os.environ.get("UNDERSTAT_SEASON") or str(time.gmtime().tm_year - (0 if time.gmtime().tm_mon >= 7 else 1))
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "understat_shots.json")

    store = {"updated": 0, "season": season, "rows": {}}
    done = set()
    if os.path.exists(out_path):
        try:
            store = json.load(open(out_path))
            if str(store.get("season")) != str(season):
                store = {"updated": 0, "season": season, "rows": {}}
            for rows in store.get("rows", {}).values():
                for r in rows:
                    done.add(str(r.get("match_id")))
        except Exception:  # noqa: BLE001
            store = {"updated": 0, "season": season, "rows": {}}
    store.setdefault("rows", {})

    league = fetch("%s/league/EPL/%s" % (BASE, season))
    dates = parse_var(league, "datesData") or []
    todo = [d for d in dates if d.get("isResult") and str(d.get("id")) not in done]
    print("season %s: %d completed matches, %d already stored, %d to fetch" % (season, sum(1 for d in dates if d.get("isResult")), len(done), len(todo)))

    for i, d in enumerate(todo):
        mid = str(d.get("id"))
        home = (d.get("h") or {}).get("title")
        away = (d.get("a") or {}).get("title")
        date = d.get("datetime")
        try:
            mp = fetch("%s/match/%s" % (BASE, mid))
            shots = parse_var(mp, "shotsData")
            if not shots:
                continue
        except Exception as e:  # noqa: BLE001
            print("  skip match %s: %s" % (mid, e))
            time.sleep(THROTTLE)
            continue
        for a in aggregate_match(shots, home, away, date):
            k = nmkey(a["name"])
            if not k:
                continue
            store["rows"].setdefault(k, []).append({
                "match_id": mid, "date": date, "opp": a["opp"], "home": a["home"],
                "shots": a["shots"], "sot": a["sot"], "goals": a["goals"], "xg": a["xg"], "key_passes": a["key_passes"],
            })
        if (i + 1) % 20 == 0:
            store["updated"] = int(time.time())
            json.dump(store, open(out_path, "w"), separators=(",", ":"))  # checkpoint
            print("  ...%d/%d matches, %d players" % (i + 1, len(todo), len(store["rows"])))
        time.sleep(THROTTLE)

    store["updated"] = int(time.time())
    json.dump(store, open(out_path, "w"), separators=(",", ":"))
    n_rows = sum(len(v) for v in store["rows"].values())
    print("wrote understat_shots.json: %d players, %d match rows" % (len(store["rows"]), n_rows))


if __name__ == "__main__":
    main()
