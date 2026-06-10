#!/usr/bin/env python3
"""
match_players.py — bridge ESPN player identities to Sackmann player_ids.

ESPN ids and Sackmann ids are different namespaces, so the only way across is
the player's name (+ country). This resolves each ESPN singles player in the
fixtures bundle to a Sackmann player_id, then CACHES the result keyed by
espn_id — so every player is matched at most once, ever. Re-runs only touch
new (unseen) espn_ids.

Strategy, in order of confidence:
  1. alias      — manual override (by espn_id, or by "name|IOC")
  2. cached     — already resolved in player_map.json
  3. exact      — normalised "first last" hits exactly one Sackmann row
  4. exact+ioc  — name collides, but IOC country breaks the tie uniquely
  5. exact+active — still tied; pick the most plausibly-active player
  6. fuzzy      — no exact hit; best difflib ratio >= threshold among
                  same-last-token / same-IOC candidates
  -> anything else lands in unmatched.json for a one-line manual alias.

Normalisation handles the real gaps seen in the data: ESPN hyphenates compound
forenames ("Marc-Andrea") where Sackmann uses spaces ("Marc Andrea"); ESPN
"de Minaur" vs Sackmann "De Minaur" (case + particle in surname); accents
(Sackmann is mostly ASCII already, so this is insurance).

Usage:
    python match_players.py \
        --fixtures data/tennis_fixtures.json \
        --atp-csv data/atp_players.csv --wta-csv data/wta_players.csv \
        --map data/player_map.json --aliases data/player_aliases.json \
        --unmatched data/unmatched.json

    # grab Sackmann CSVs if you don't have them locally
    python match_players.py --download ...
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Any

SACKMANN = {
    "atp": "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_players.csv",
    "wta": "https://raw.githubusercontent.com/JeffSackmann/tennis_wta/master/wta_players.csv",
}
FUZZY_THRESHOLD = 0.90


def log(m: str) -> None:
    print(m, file=sys.stderr)


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #
def normalise(s: str | None) -> str:
    if not s:
        return ""
    # strip accents
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = s.lower()
    # hyphens / apostrophes / dots -> space; drop other punctuation
    out = []
    for c in s:
        if c.isalnum() or c.isspace():
            out.append(c)
        elif c in "-'.’":
            out.append(" ")
    return " ".join("".join(out).split())


# --------------------------------------------------------------------------- #
# Sackmann index
# --------------------------------------------------------------------------- #
class SackmannIndex:
    """Indexes one tour's player table for exact + fuzzy lookup."""

    def __init__(self, rows: list[dict[str, str]]):
        self.by_norm: dict[str, list[dict]] = defaultdict(list)
        self.by_lasttoken: dict[str, list[dict]] = defaultdict(list)
        self.by_ioc: dict[str, list[dict]] = defaultdict(list)
        self.players: list[dict] = []
        for r in rows:
            first, last = r.get("name_first", ""), r.get("name_last", "")
            if not last and not first:
                continue
            norm = normalise(f"{first} {last}")
            if not norm:
                continue
            try:
                pid_int = int(r.get("player_id") or 0)
            except ValueError:
                pid_int = 0
            rec = {
                "player_id": r.get("player_id"),
                "name": f"{first} {last}".strip(),
                "ioc": (r.get("ioc") or "").upper() or None,
                "dob": r.get("dob") or None,
                "norm": norm,
                # activeness proxy: higher id = registered more recently;
                # a wikidata id + height generally marks a real tour player.
                "active": pid_int + (5_000_000 if r.get("wikidata_id") else 0)
                          + (1_000_000 if r.get("height") else 0),
            }
            self.players.append(rec)
            self.by_norm[norm].append(rec)
            self.by_ioc[rec["ioc"] or ""].append(rec)
            last_tok = norm.split()[-1] if norm.split() else ""
            self.by_lasttoken[last_tok].append(rec)

    def exact(self, norm: str) -> list[dict]:
        return self.by_norm.get(norm, [])

    def fuzzy(self, norm: str, ioc: str | None) -> tuple[dict | None, float]:
        last_tok = norm.split()[-1] if norm.split() else ""
        pool = {id(r): r for r in self.by_lasttoken.get(last_tok, [])}
        if ioc:
            for r in self.by_ioc.get(ioc.upper(), []):
                pool[id(r)] = r
        best, best_ratio = None, 0.0
        for r in pool.values():
            ratio = SequenceMatcher(None, norm, r["norm"]).ratio()
            if ratio > best_ratio:
                best, best_ratio = r, ratio
        return best, best_ratio


def load_csv(path: str) -> list[dict[str, str]]:
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def maybe_download(tour: str, path: str) -> None:
    if os.path.exists(path):
        return
    import urllib.request
    log(f"  downloading {tour} players -> {path}")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    urllib.request.urlretrieve(SACKMANN[tour], path)


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #
def disambiguate(cands: list[dict], ioc: str | None) -> tuple[dict, str]:
    if len(cands) == 1:
        return cands[0], "exact"
    if ioc:
        ioc_hits = [c for c in cands if c["ioc"] == ioc.upper()]
        if len(ioc_hits) == 1:
            return ioc_hits[0], "exact+ioc"
        if ioc_hits:
            cands = ioc_hits  # narrow, then fall through to activeness
    return max(cands, key=lambda c: c["active"]), "exact+active"


def match_player(
    name: str, ioc: str | None, idx: SackmannIndex
) -> dict[str, Any] | None:
    norm = normalise(name)
    if not norm:
        return None
    cands = idx.exact(norm)
    rev_used = False
    if not cands:
        # ESPN lists some players surname-first (Chinese/Korean order):
        # "Zhu Lin" -> Sackmann "Lin Zhu". Try the reversed token order.
        rev = " ".join(reversed(norm.split()))
        if rev != norm:
            cands = idx.exact(rev)
            rev_used = bool(cands)
    if cands:
        chosen, method = disambiguate(cands, ioc)
        if rev_used:
            method = "rev-" + method
        return {"sackmann_id": chosen["player_id"], "name": chosen["name"],
                "ioc": chosen["ioc"], "method": method, "ratio": 1.0}
    best, ratio = idx.fuzzy(norm, ioc)
    if best and ratio >= FUZZY_THRESHOLD:
        return {"sackmann_id": best["player_id"], "name": best["name"],
                "ioc": best["ioc"], "method": "fuzzy", "ratio": round(ratio, 3)}
    return None


PLACEHOLDERS = {"tbd", "bye", "qualifier", "q", "lucky loser", "ll", "to be determined"}


def collect_singles_players(fixtures: list[dict]) -> dict[str, dict]:
    """Unique ESPN singles players keyed by espn_id (dedup across fixtures).
    Skips placeholder slots (TBD/Bye/Qualifier) which aren't real players."""
    players: dict[str, dict] = {}
    for fx in fixtures:
        if fx.get("event_type") != "singles":
            continue
        for p in fx.get("players", []):
            eid = p.get("espn_id")
            nm = p.get("name")
            if not eid or not nm or normalise(nm) in PLACEHOLDERS:
                continue
            if eid not in players:
                players[eid] = {"name": nm, "ioc": p.get("ioc"), "tour": fx.get("tour")}
    return players


def main() -> int:
    ap = argparse.ArgumentParser(description="Match ESPN players to Sackmann ids.")
    ap.add_argument("--fixtures", default="data/tennis_fixtures.json")
    ap.add_argument("--atp-csv", default="data/atp_players.csv")
    ap.add_argument("--wta-csv", default="data/wta_players.csv")
    ap.add_argument("--map", default="data/player_map.json", help="resolved cache (read+write)")
    ap.add_argument("--aliases", default="data/player_aliases.json", help="manual overrides")
    ap.add_argument("--unmatched", default="data/unmatched.json")
    ap.add_argument("--download", action="store_true", help="fetch Sackmann CSVs if missing")
    args = ap.parse_args()

    if args.download:
        maybe_download("atp", args.atp_csv)
        maybe_download("wta", args.wta_csv)

    fixtures = json.load(open(args.fixtures, encoding="utf-8")).get("fixtures", [])
    espn_players = collect_singles_players(fixtures)
    log(f"unique ESPN singles players: {len(espn_players)}")

    idx = {"atp": SackmannIndex(load_csv(args.atp_csv)),
           "wta": SackmannIndex(load_csv(args.wta_csv))}

    # caches / overrides
    cache: dict[str, dict] = {}
    if os.path.exists(args.map):
        cache = json.load(open(args.map, encoding="utf-8"))
    aliases = {"by_espn_id": {}, "by_name_ioc": {}}
    if os.path.exists(args.aliases):
        aliases.update(json.load(open(args.aliases, encoding="utf-8")))

    stats: dict[str, int] = defaultdict(int)
    unmatched: list[dict] = []

    for eid, p in espn_players.items():
        tour = p["tour"] if p["tour"] in idx else "atp"
        ioc = p.get("ioc")

        if eid in aliases["by_espn_id"]:
            sid = aliases["by_espn_id"][eid]
            cache[eid] = {"sackmann_id": sid, "name": p["name"], "ioc": ioc,
                          "method": "alias", "ratio": 1.0, "tour": tour}
            stats["alias"] += 1
            continue
        namekey = f"{normalise(p['name'])}|{(ioc or '').upper()}"
        if namekey in aliases["by_name_ioc"]:
            sid = aliases["by_name_ioc"][namekey]
            cache[eid] = {"sackmann_id": sid, "name": p["name"], "ioc": ioc,
                          "method": "alias", "ratio": 1.0, "tour": tour}
            stats["alias"] += 1
            continue
        if eid in cache:
            stats["cached"] += 1
            continue

        res = match_player(p["name"], ioc, idx[tour])
        if res:
            res["tour"] = tour
            cache[eid] = res
            stats[res["method"]] += 1
        else:
            stats["unmatched"] += 1
            # surface a few candidates to make the manual alias trivial
            norm = normalise(p["name"])
            cand_pool = idx[tour].by_lasttoken.get(norm.split()[-1] if norm.split() else "", [])[:5]
            unmatched.append({
                "espn_id": eid, "name": p["name"], "ioc": ioc, "tour": tour,
                "candidates": [{"sackmann_id": c["player_id"], "name": c["name"], "ioc": c["ioc"]}
                               for c in cand_pool],
            })

    # write outputs
    os.makedirs(os.path.dirname(args.map) or ".", exist_ok=True)
    json.dump(cache, open(args.map, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    json.dump(unmatched, open(args.unmatched, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    if not os.path.exists(args.aliases):
        json.dump({"by_espn_id": {}, "by_name_ioc": {}},
                  open(args.aliases, "w", encoding="utf-8"), indent=2)

    total = len(espn_players)
    resolved = total - stats["unmatched"]
    log("  " + " | ".join(f"{k}:{v}" for k, v in sorted(stats.items())))
    log(f"  resolved {resolved}/{total} = {resolved/total*100:.1f}%  "
        f"(unmatched {stats['unmatched']} -> {args.unmatched})")
    log(f"  wrote {args.map}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
