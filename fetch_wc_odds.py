#!/usr/bin/env python3
"""
fetch_wc_odds.py — Bet365 odds fetcher for WC 2026 fixtures.

Pulls match-level + team-level + player-level markets from API-Football
(bookmaker_id 8 = Bet365) and writes a structured JSON consumed by JTT WC.

This is Phase A of the player-odds integration. The fetcher pulls
extended markets but the UI doesn't surface them yet — that lands in
Phases B (JS plumbing) and C (UI integration).

EXISTING MATCH-LEVEL KEYS (preserved exactly — do not change shape):
    matchWinner, btts, goalsOU, cornersOU, cardsOU

NEW TEAM-LEVEL KEYS (additive):
    totalShotsOU, totalTacklesOU, offsidesOU, goalLine

NEW PLAYER KEYS (additive, under `players` dict keyed by normalised name):
    anytimeGoal, firstGoal, lastGoal, scorePenalty,
    assists, shotsOnTarget, shots, foulsCommitted, goalkeeperSaves

Plus `unmatchedPlayers` list of book player names we couldn't normalise
against lineups (audit only — appears empty 99% of the time).

Usage:
    export APIFOOTBALL_KEY=your_key
    python fetch_wc_odds.py
    # or with explicit paths
    python fetch_wc_odds.py --fixtures data/worldcup_fixtures_2026.json \\
                             --lineups  data/wc_lineups.json \\
                             --out      data/worldcup_odds_2026.json
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from urllib import request, parse, error

API_BASE = "https://v3.football.api-sports.io"
BOOKMAKER_ID = 8  # Bet365
LEAGUE_ID    = 1  # World Cup
SEASON       = 2026

# Bet IDs we care about. Format: bet_id -> (json_key, parser_type)
# parser_type drives how the API response values are parsed.
MARKET_MAP = {
    # === EXISTING MATCH-LEVEL ===
    1:   ("matchWinner",            "match_3way"),
    8:   ("btts",                   "btts"),
    5:   ("goalsOU",                "ou_simple"),       # "Goals Over/Under"
    45:  ("cornersOU",              "ou_simple"),       # "Corners Over Under"
    # Note: API-Football's "Total Cards" bet ID has historically varied;
    # check the probe output and adjust if your existing fetcher uses a
    # different ID. Common values: 52, 116, 138.
    52:  ("cardsOU",                "ou_simple"),

    # === NEW TEAM-LEVEL ===
    211: ("totalShotsOU",           "ou_simple"),       # "Total Shots"
    281: ("totalTacklesOU",         "ou_simple"),       # "Total Tackles"
    164: ("offsidesOU",             "ou_simple"),       # "Offsides Total"
    50:  ("goalLine",               "ou_simple"),       # Asian goals line

    # === NEW PLAYER MARKETS — goalscorer ===
    92:  ("anytimeGoal",            "player_single"),
    93:  ("firstGoal",              "player_single"),
    94:  ("lastGoal",               "player_single"),
    99:  ("scorePenalty",           "player_single"),

    # === NEW PLAYER MARKETS — stats (each player has an O/U line) ===
    212: ("assists",                "player_ou"),       # "Player Assists"
    242: ("shotsOnTarget",          "player_ou"),       # "Player Shots On Target"
    266: ("foulsCommitted",         "player_ou"),       # "Player Fouls Committed"
    267: ("goalkeeperSaves",        "player_ou"),       # "Goalkeeper Saves"

    # === NEW PLAYER MARKETS — per-team aggregated shots ===
    # These contain team-split player shots — we merge them into the shared
    # `shots` and `shotsOnTarget` dicts per player.
    240: ("_homePlayerShots",       "player_ou"),
    241: ("_awayPlayerShots",       "player_ou"),
    269: ("_homePlayerSOT",         "player_ou"),
    275: ("_awayPlayerSOT",         "player_ou"),
    276: ("_awayPlayerShotsTotal",  "player_ou"),
}


# ============================================================
# NAME NORMALISER
# ============================================================
# Bet365 may return "L. Martinez" while lineups have "Lautaro Martínez".
# We normalise to a stable key for matching across sources.

def strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalise_player_key(name: str) -> str:
    """Returns a stable, lowercase, ASCII key from a player name.

    Examples:
      'Lautaro Martinez'  -> 'lautaro_martinez'
      'L. Martinez'       -> 'l_martinez'
      'Cristiano Ronaldo' -> 'cristiano_ronaldo'
    """
    if not name:
        return ""
    s = strip_accents(name).lower().strip()
    # Drop common honorifics / titles
    s = re.sub(r"\b(jr|sr|i{1,3})\b\.?", "", s)
    # Collapse whitespace + punctuation
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s.replace(" ", "_")


def build_lineup_index(lineups_doc):
    """Walks all lineups and builds two indexes for fuzzy player matching:
        full_name_idx[normalised_full] -> {playerId, teamId, fullName}
        last_name_idx[normalised_last] -> [list of candidates]

    The list of candidates handles the case where 'Martinez' could match
    multiple players — we fall through to first-initial matching for
    those, and if still ambiguous, leave unmatched.
    """
    full_idx, last_idx = {}, {}
    if not lineups_doc or "byMatch" not in lineups_doc:
        return {"full": full_idx, "last": last_idx}

    for match_id, lineup in lineups_doc.get("byMatch", {}).items():
        for side in ("home", "away"):
            team = lineup.get(side) or {}
            team_id = team.get("teamId")
            for p in team.get("startXI", []) + team.get("substitutes", []):
                pid = p.get("playerId")
                full = p.get("name") or p.get("fullName") or ""
                if not full or pid is None:
                    continue
                full_norm = normalise_player_key(full)
                full_idx[full_norm] = {"playerId": pid, "teamId": team_id, "fullName": full}
                last_word = full_norm.split("_")[-1] if "_" in full_norm else full_norm
                last_idx.setdefault(last_word, []).append({
                    "playerId": pid, "teamId": team_id,
                    "fullName": full, "fullNorm": full_norm,
                })
    return {"full": full_idx, "last": last_idx}


def match_player_to_lineup(book_name, fixture_teams, lineup_idx):
    """Returns {playerId, teamId, fullName} or None.

    Match algorithm:
      1. Direct normalised full-name match
      2. First-initial + last-name match (e.g. 'L. Martinez' -> 'Lautaro Martinez')
      3. Last-name only if uniquely belongs to one of the two fixture teams
    """
    if not book_name:
        return None
    norm = normalise_player_key(book_name)

    if norm in lineup_idx["full"]:
        return lineup_idx["full"][norm]

    parts = norm.split("_")
    if len(parts) >= 2 and len(parts[0]) == 1:
        first_initial = parts[0]
        last_word = parts[-1]
        candidates = lineup_idx["last"].get(last_word, [])
        filtered = [c for c in candidates if c["fullNorm"].startswith(first_initial)]
        if fixture_teams and filtered:
            filtered = [c for c in filtered if c["teamId"] in fixture_teams]
        if len(filtered) == 1:
            return {"playerId": filtered[0]["playerId"], "teamId": filtered[0]["teamId"],
                    "fullName": filtered[0]["fullName"]}

    if len(parts) >= 1:
        last_word = parts[-1]
        candidates = lineup_idx["last"].get(last_word, [])
        if fixture_teams:
            in_fixture = [c for c in candidates if c["teamId"] in fixture_teams]
            if len(in_fixture) == 1:
                return {"playerId": in_fixture[0]["playerId"], "teamId": in_fixture[0]["teamId"],
                        "fullName": in_fixture[0]["fullName"]}

    return None


# ============================================================
# PARSERS — one per market type
# ============================================================

def parse_match_3way(values):
    out = {}
    for v in values:
        val = (v.get("value") or "").strip().lower()
        odd = v.get("odd")
        if not odd: continue
        try: odd = float(odd)
        except (TypeError, ValueError): continue
        if val == "home":   out["home"]  = odd
        elif val == "draw": out["draw"]  = odd
        elif val == "away": out["away"]  = odd
    return out if len(out) == 3 else None


def parse_btts(values):
    out = {}
    for v in values:
        val = (v.get("value") or "").strip().lower()
        odd = v.get("odd")
        if not odd: continue
        try: odd = float(odd)
        except (TypeError, ValueError): continue
        if val == "yes": out["yes"] = odd
        elif val == "no": out["no"]  = odd
    return out if len(out) == 2 else None


def parse_ou_simple(values):
    """Parses any Over/Under market into {line, over, under}. Returns the
    most-balanced line (closest over/under prices) plus allLines for
    secondary access."""
    by_line = {}
    for v in values:
        val = (v.get("value") or "").strip().lower()
        odd = v.get("odd")
        if not odd: continue
        try: odd = float(odd)
        except (TypeError, ValueError): continue
        m = re.match(r"(over|under)\s+([\d.]+)", val)
        if not m:
            continue
        side, line_str = m.group(1), m.group(2)
        try:
            line = float(line_str)
        except ValueError:
            continue
        by_line.setdefault(line, {})[side] = odd

    complete = [(line, prices) for line, prices in by_line.items()
                if "over" in prices and "under" in prices]
    if not complete:
        return None
    def balance_score(prices):
        return abs(prices["over"] - prices["under"])
    best_line, best_prices = min(complete, key=lambda x: balance_score(x[1]))
    return {"line": best_line, "over": best_prices["over"], "under": best_prices["under"],
            "allLines": {str(l): p for l, p in by_line.items()}}


def parse_player_single(values):
    """For markets where each value is a player + odds with no line."""
    out = {}
    for v in values:
        name = (v.get("value") or "").strip()
        odd = v.get("odd")
        if not name or not odd: continue
        low = name.lower()
        if low in ("no goalscorer", "any other player", "no first scorer",
                   "no last scorer", "no goal", "no penalty"):
            continue
        try: odd = float(odd)
        except (TypeError, ValueError): continue
        key = normalise_player_key(name)
        if key:
            out[key] = {"name": name, "odd": odd}
    return out


def parse_player_ou(values):
    """For player O/U markets where each value is 'Name Over/Under X.X'."""
    out = {}
    for v in values:
        raw = (v.get("value") or "").strip()
        odd = v.get("odd")
        if not raw or not odd:
            continue
        try: odd = float(odd)
        except (TypeError, ValueError): continue
        m = re.match(r"^(.*?)\s+(over|under)\s+([\d.]+)\s*$", raw, re.IGNORECASE)
        if not m:
            continue
        name = m.group(1).strip()
        side = m.group(2).lower()
        try:
            line = float(m.group(3))
        except ValueError:
            continue
        if not name:
            continue
        key = normalise_player_key(name)
        if not key:
            continue
        if key not in out:
            out[key] = {"name": name, "lines": {}}
        line_str = str(line)
        out[key]["lines"].setdefault(line_str, {})[side] = odd
    return out


PARSER_MAP = {
    "match_3way":     parse_match_3way,
    "btts":           parse_btts,
    "ou_simple":      parse_ou_simple,
    "player_single":  parse_player_single,
    "player_ou":      parse_player_ou,
}


# ============================================================
# FETCH
# ============================================================

def api_call(endpoint, params, key, retries=2):
    url = f"{API_BASE}/{endpoint}?{parse.urlencode(params)}"
    req = request.Request(url, headers={"x-apisports-key": key})
    for attempt in range(retries + 1):
        try:
            with request.urlopen(req, timeout=20) as r:
                return json.loads(r.read())
        except (error.URLError, error.HTTPError, TimeoutError) as e:
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            print(f"  ! API failed: {e}", file=sys.stderr)
            return None


def fetch_fixture_odds(fixture_id, fixture_teams, lineup_idx, key):
    data = api_call("odds", {
        "fixture": fixture_id, "bookmaker": BOOKMAKER_ID,
        "league": LEAGUE_ID, "season": SEASON,
    }, key)
    if not data or not data.get("response"):
        return None

    bets_by_id = {}
    for entry in data["response"]:
        for bm in entry.get("bookmakers", []):
            for bet in bm.get("bets", []):
                bid = bet.get("id")
                if bid is not None:
                    bets_by_id[bid] = bet.get("values", [])

    fixture_doc = {}
    players_merged = {}
    unmatched_names = set()

    for bet_id, (json_key, parser_type) in MARKET_MAP.items():
        if bet_id not in bets_by_id:
            continue
        values = bets_by_id[bet_id]
        parser = PARSER_MAP.get(parser_type)
        if not parser:
            continue
        parsed = parser(values)
        if parsed is None:
            continue

        if parser_type in ("match_3way", "btts", "ou_simple"):
            if not json_key.startswith("_"):
                fixture_doc[json_key] = parsed

        elif parser_type == "player_single":
            for pkey, payload in parsed.items():
                slot = players_merged.setdefault(pkey, {
                    "name": payload["name"], "normalisedKey": pkey,
                })
                if not json_key.startswith("_"):
                    slot[json_key] = payload["odd"]

        elif parser_type == "player_ou":
            public_market = {
                "_homePlayerShots":      "shots",
                "_awayPlayerShots":      "shots",
                "_awayPlayerShotsTotal": "shots",
                "_homePlayerSOT":        "shotsOnTarget",
                "_awayPlayerSOT":        "shotsOnTarget",
            }.get(json_key, json_key)
            if public_market.startswith("_"):
                continue
            for pkey, payload in parsed.items():
                slot = players_merged.setdefault(pkey, {
                    "name": payload["name"], "normalisedKey": pkey,
                })
                slot.setdefault(public_market, {}).update(payload["lines"])

    if players_merged:
        players_out = {}
        for pkey, payload in players_merged.items():
            match = match_player_to_lineup(payload["name"], fixture_teams, lineup_idx)
            if match:
                payload["playerId"]    = match["playerId"]
                payload["teamId"]      = match["teamId"]
                payload["matchedName"] = match["fullName"]
            else:
                unmatched_names.add(payload["name"])
            players_out[pkey] = payload
        fixture_doc["players"] = players_out

    if unmatched_names:
        fixture_doc["unmatchedPlayers"] = sorted(unmatched_names)

    return fixture_doc


def main():
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixtures", default="worldcup_fixtures_2026.json")
    ap.add_argument("--lineups",  default="wc_lineups.json")
    ap.add_argument("--out",      default="worldcup_odds_2026.json")
    ap.add_argument("--max-future-days", type=int, default=14)
    ap.add_argument("--include-past", action="store_true")
    args = ap.parse_args()

    key = os.environ.get("APIFOOTBALL_KEY")
    if not key:
        print("ERROR: APIFOOTBALL_KEY env var not set", file=sys.stderr)
        sys.exit(1)

    fixtures_path = Path(args.fixtures)
    if not fixtures_path.exists():
        print(f"ERROR: fixtures file not found: {fixtures_path}", file=sys.stderr)
        sys.exit(1)
    with fixtures_path.open() as f:
        fixtures_doc = json.load(f)
    fixtures = fixtures_doc.get("fixtures", [])

    lineup_idx = {"full": {}, "last": {}}
    lineups_path = Path(args.lineups)
    if lineups_path.exists():
        try:
            with lineups_path.open() as f:
                lineups_doc = json.load(f)
            lineup_idx = build_lineup_index(lineups_doc)
            print(f"Loaded {len(lineup_idx['full'])} players from lineups for ID matching")
        except (json.JSONDecodeError, OSError):
            print(f"Could not parse lineups file, proceeding without ID matching")
    else:
        print(f"No lineups file at {lineups_path} — player odds will lack playerId matching")

    now_ts = int(time.time())
    cutoff_future = now_ts + (args.max_future_days * 86400)
    eligible = []
    for fx in fixtures:
        ts = fx.get("timestamp", 0)
        status = fx.get("status", "")
        if status in ("FT", "AET", "PEN") and not args.include_past:
            continue
        if ts > cutoff_future:
            continue
        eligible.append(fx)

    print(f"Fetching odds for {len(eligible)} upcoming fixtures …")
    print(f"  ({len(fixtures) - len(eligible)} skipped: past or beyond {args.max_future_days}-day window)")

    by_match = {}
    fail_count = 0
    for i, fx in enumerate(eligible, 1):
        fid = fx["matchId"]
        teams = (fx.get("homeTeamId"), fx.get("awayTeamId"))
        print(f"  [{i}/{len(eligible)}] {fx.get('homeTeam')} vs {fx.get('awayTeam')} (id {fid})")
        doc = fetch_fixture_odds(fid, teams, lineup_idx, key)
        if doc:
            by_match[str(fid)] = doc
            n_players = len(doc.get("players", {}))
            n_markets = len([k for k in doc.keys() if k not in ("players", "unmatchedPlayers")])
            n_unmatched = len(doc.get("unmatchedPlayers", []))
            extra = f" ({n_unmatched} unmatched)" if n_unmatched else ""
            print(f"        ✓ {n_markets} match/team markets + {n_players} players{extra}")
        else:
            fail_count += 1
            print(f"        ✗ no odds returned")
        time.sleep(0.4)

    out_doc = {
        "fetchedAt":   dt.datetime.utcnow().isoformat() + "Z",
        "source":      "api-football",
        "bookmaker":   "Bet365",
        "bookmakerId": BOOKMAKER_ID,
        "byMatch":     by_match,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    with tmp_path.open("w") as f:
        json.dump(out_doc, f, separators=(",", ":"))
    tmp_path.replace(out_path)

    total_unmatched = sum(len(d.get("unmatchedPlayers", [])) for d in by_match.values())
    print(f"\n✓ Wrote {len(by_match)} fixture odds to {out_path}")
    print(f"  Failures: {fail_count} fixtures returned no odds")
    if total_unmatched:
        print(f"  Unmatched players across all fixtures: {total_unmatched}")
        print(f"  (See `unmatchedPlayers` arrays in the JSON for audit)")


if __name__ == "__main__":
    main()
