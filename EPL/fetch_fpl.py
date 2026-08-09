#!/usr/bin/env python3
"""
JTT FPL pull — walks the Fantasy Premier League API into per-player gamelog JSON.

Outputs (written to the working dir; commit into EPL/data/):
  fpl_players.json    player index: name, team, position, price, ownership, set-piece takers,
                      injury status, season-to-date + last-season aggregates. Includes byName (nmkey -> id).
  fpl_gamelogs.json   per-player per-gameweek stat rows (the projection base), keyed by FPL id.

Merge design: every player carries `nmkey` (accent-stripped, lowercased name) so Understat shot
data and RapidOdds odds join onto FPL by name. `byName` maps nmkey -> FPL id for O(1) lookups.

No API key needed. The FPL API blocks browser CORS and can soft-block datacentre IPs, so run this
in your build pipeline (GitHub Action -> committed JSON), and test one element-summary call first.

Pre-season note: element-summary `history` (this season, per-gameweek) is empty until Round 1,
so gamelogs fill as the season plays. `history_past` (last-season aggregates) is captured now into
each player's `last` block to seed projections before Round 1. Per-match LAST season data comes from
Understat / an FPL archive in a separate pull.
"""
import json
import os
import time
import unicodedata
import urllib.request

BASE = "https://fantasy.premierleague.com/api"
UA = {"User-Agent": "Mozilla/5.0 (JTT FPL data pull; contact justthetipafl)"}
POS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


def fetch(url, tries=4):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError("fetch failed after %d tries: %s (%s)" % (tries, url, last))


_SPECIAL = str.maketrans({
    "\u00f8": "o", "\u00d8": "o", "\u00e6": "ae", "\u00c6": "ae", "\u0142": "l", "\u0141": "l",
    "\u0111": "d", "\u0110": "d", "\u00fe": "th", "\u00de": "th", "\u00f0": "d", "\u00d0": "d",
    "\u00df": "ss", "\u0131": "i", "\u0130": "i",
})


def nmkey(name):
    """Accent-stripped, lowercased, alnum-only name key for cross-source joins.
    Pre-maps letters NFKD can't decompose (\u00d8\u00e6\u0142\u0111\u00de\u00df) so e.g. \u00d8degaard -> odegaard."""
    s = (name or "").translate(_SPECIAL)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return " ".join("".join(c for c in s.lower() if c.isalnum() or c == " ").split())


def _f(v):
    """Coerce FPL's stringy numbers to float, else None."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def build_player(e, teams):
    full = ("%s %s" % (e.get("first_name", ""), e.get("second_name", ""))).strip()
    t = teams.get(e.get("team"), {})
    last = None
    hp = e.get("_history_past") or []
    if hp:
        h = hp[-1]  # most recent past season
        last = {
            "season": h.get("season_name"), "min": h.get("minutes"), "G": h.get("goals_scored"),
            "A": h.get("assists"), "xG": _f(h.get("expected_goals")), "xA": _f(h.get("expected_assists")),
            "saves": h.get("saves"), "CS": h.get("clean_sheets"), "YC": h.get("yellow_cards"),
            "RC": h.get("red_cards"), "starts": h.get("starts"), "pts": h.get("total_points"),
        }
    return {
        "id": e.get("id"), "name": full, "web_name": e.get("web_name"), "nmkey": nmkey(full),
        "team": t.get("name"), "teamShort": t.get("short"), "pos": POS.get(e.get("element_type")),
        "price": (e.get("now_cost") or 0) / 10.0, "owned": _f(e.get("selected_by_percent")),
        "form": _f(e.get("form")), "status": e.get("status"), "news": e.get("news") or "",
        "chance": e.get("chance_of_playing_next_round"),
        "pens_order": e.get("penalties_order"),
        "corners_fk_order": e.get("corners_and_indirect_freekicks_order"),
        "direct_fk_order": e.get("direct_freekicks_order"),
        # season-to-date totals
        "minutes": e.get("minutes"), "G": e.get("goals_scored"), "A": e.get("assists"),
        "xG": _f(e.get("expected_goals")), "xA": _f(e.get("expected_assists")),
        "xGI": _f(e.get("expected_goal_involvements")), "saves": e.get("saves"),
        "CS": e.get("clean_sheets"), "YC": e.get("yellow_cards"), "RC": e.get("red_cards"),
        "bonus": e.get("bonus"), "starts": e.get("starts"),
        "tackles": e.get("tackles"), "cbi": e.get("clearances_blocks_interceptions"),
        "recoveries": e.get("recoveries"), "defcon": e.get("defensive_contribution"),
        "last": last,
    }


def build_gamelog(history, teams):
    rows = []
    for h in history or []:
        opp = teams.get(h.get("opponent_team"), {})
        rows.append({
            "gw": h.get("round"), "date": h.get("kickoff_time"),
            "opp": opp.get("name"), "oppShort": opp.get("short"), "home": h.get("was_home"),
            "min": h.get("minutes"), "G": h.get("goals_scored"), "A": h.get("assists"),
            "xG": _f(h.get("expected_goals")), "xA": _f(h.get("expected_assists")),
            "saves": h.get("saves"), "CS": h.get("clean_sheets"), "GC": h.get("goals_conceded"),
            "YC": h.get("yellow_cards"), "RC": h.get("red_cards"), "bonus": h.get("bonus"), "bps": h.get("bps"),
            "tackles": h.get("tackles"), "cbi": h.get("clearances_blocks_interceptions"),
            "recoveries": h.get("recoveries"), "defcon": h.get("defensive_contribution"),
            "ict": _f(h.get("ict_index")), "pts": h.get("total_points"),
        })
    return rows


def main():
    boot = fetch(BASE + "/bootstrap-static/")
    teams = {t["id"]: {"name": t["name"], "short": t.get("short_name")} for t in boot["teams"]}
    els = boot["elements"]
    print("bootstrap: %d players, %d teams" % (len(els), len(teams)))

    players, gamelogs, byName = [], {}, {}
    for i, e in enumerate(els):
        summ = fetch(BASE + "/element-summary/%d/" % e["id"])
        e["_history_past"] = summ.get("history_past")
        p = build_player(e, teams)
        players.append(p)
        gamelogs[str(e["id"])] = build_gamelog(summ.get("history"), teams)
        if p["nmkey"]:
            byName.setdefault(p["nmkey"], p["id"])  # first wins; collisions are rare across a single squad set
        # polite pacing so the FPL backend doesn't soft-block
        time.sleep(0.9 if (i + 1) % 20 == 0 else 0.12)

    now = int(time.time())
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "fpl_players.json"), "w") as f:
        json.dump({"updated": now, "teams": teams, "players": players, "byName": byName}, f, separators=(",", ":"))
    with open(os.path.join(out, "fpl_gamelogs.json"), "w") as f:
        json.dump({"updated": now, "logs": gamelogs}, f, separators=(",", ":"))
    n_rows = sum(len(v) for v in gamelogs.values())
    print("wrote fpl_players.json (%d) + fpl_gamelogs.json (%d rows)" % (len(players), n_rows))


if __name__ == "__main__":
    main()
