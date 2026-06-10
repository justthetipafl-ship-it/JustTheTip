#!/usr/bin/env python3
"""
fetch_espn.py — JTT Tennis fixtures fetcher (ESPN hidden scoreboard API).

Pulls upcoming/live/completed singles + doubles matches for ATP and WTA from
ESPN's keyless scoreboard endpoint and emits a normalised fixture bundle for
the JTT Tennis pipeline.

    https://site.api.espn.com/apis/site/v2/sports/tennis/{tour}/scoreboard

ESPN is unofficial: shapes can change, no SLA. This parser is deliberately
defensive — it walks both the flat `competitions` layout and the
tournament -> `groupings` -> `competitions` nesting ESPN uses for tennis, and
never assumes a key exists. Surface and best_of are best-effort and nullable;
build.R backfills surface by tournament name from the Sackmann match files.

Quota note: ESPN is free and unmetered. The Odds API is NEVER used for
fixtures — only for the optional odds layer. Keep them decoupled.

Usage (GitHub Actions):
    python fetch_espn.py --days 2 --out data/tennis_fixtures.json

Debugging against a real response (run locally, inspect, then we tighten):
    python fetch_espn.py --dump-raw raw/                 # save raw JSON per tour/day
    python fetch_espn.py --mock-file raw/atp_20260629.json --tours atp   # parse offline
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from typing import Any, Iterable

try:
    import requests
except ImportError:
    sys.exit("requests not installed. In Actions: pip install requests")

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/tennis/{tour}/scoreboard"
TOURS = ("atp", "wta")

# ESPN sometimes 403s a bare client. A normal UA header avoids it.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; JTT-Tennis/1.0; +https://dubclub.win/JustTheTipAUS)",
    "Accept": "application/json",
}

# Grand Slam name fragments -> used only to infer best_of for men's singles.
SLAM_PATTERNS = ("australian open", "roland garros", "french open", "wimbledon", "us open")

SURFACE_KEYWORDS = {
    "clay": "Clay",
    "grass": "Grass",
    "hard": "Hard",
    "carpet": "Carpet",
}


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def fetch_scoreboard(tour: str, date: str | None, retries: int = 3, timeout: int = 15) -> dict[str, Any]:
    """GET one tour's scoreboard for one day (or 'today' if date is None)."""
    url = ESPN_BASE.format(tour=tour)
    params = {"dates": date} if date else {}
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            last_err = f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001 - want to retry on anything network-y
            last_err = repr(e)
        if attempt < retries:
            time.sleep(1.5 * attempt)
    log(f"  ! {tour} {date or 'today'}: fetch failed ({last_err})")
    return {}


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #
def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def first_str(*candidates: Any) -> str | None:
    """Return the first non-empty string-ish candidate."""
    for c in candidates:
        if isinstance(c, str) and c.strip():
            return c.strip()
    return None


def dig(obj: Any, *keys: str) -> Any:
    """Safely walk nested dict keys. Returns None if any level isn't a dict
    or a key is missing. ESPN sometimes returns ints where a dict is expected
    (e.g. season.type is an int code), so never assume the shape."""
    cur = obj
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def as_dict(obj: Any) -> dict[str, Any]:
    """Coerce to dict; ESPN occasionally sends ints/strings for keys we expect
    to be objects (notably `round` and `status.type`)."""
    return obj if isinstance(obj, dict) else {}


def _to_int(v: Any) -> int | None:
    try:
        return int(v) if v not in (None, "", "0", 0) else None
    except (TypeError, ValueError):
        return None


def detect_surface(*texts: Any) -> str | None:
    blob = " ".join(t for t in texts if isinstance(t, str)).lower()
    for kw, label in SURFACE_KEYWORDS.items():
        if kw in blob:
            return label
    return None


def normalise_round(display: str | None) -> tuple[str | None, str | None]:
    """Return (display_round, round_code) from ESPN's round.displayName.

    Real ESPN tennis uses several schemes that must be disambiguated:
      - "Qualifying 1st Round" / "Qualifying Final"  (qualifying draw)
      - "Round 1" / "Round 2"                        (smaller 250/500 draws)
      - "Round of 64" / "Round of 16"                (slam / large draws)
      - "Quarterfinal" / "Semifinal" / "Final"
    round_code is a clean token; build.R owns cross-tournament ordering since
    "Round 1" means different things in a 28-draw vs a 128-draw.
    """
    if not isinstance(display, str) or not display.strip():
        return None, None
    disp = display.strip()
    b = disp.lower()

    # Qualifying first — it often contains "1st round"/"final" which would
    # otherwise mis-map to a main-draw code.
    if "qualif" in b:
        if "final" in b:
            return disp, "Q-F"
        for frag, code in (("1st", "Q1"), ("first", "Q1"), ("2nd", "Q2"),
                           ("second", "Q2"), ("3rd", "Q3"), ("third", "Q3")):
            if frag in b:
                return disp, code
        return disp, "Q"

    # Compound finals before bare "final" (substring trap).
    for frag, code in (("quarter", "QF"), ("semi", "SF")):
        if frag in b:
            return disp, code
    if "final" in b:
        return disp, "F"

    # "Round of N" (large draws).
    for n in ("128", "64", "32", "16"):
        if f"round of {n}" in b:
            return disp, f"R{n}"

    # "Round N" / "Nth round" (smaller draws) — round-number based.
    m = re.search(r"round\s*(\d+)", b) or re.search(r"(\d+)(?:st|nd|rd|th)\s*round", b)
    if m:
        return disp, f"R{m.group(1)}"

    return disp, None


def infer_best_of(tour: str, tournament: str | None, event_type: str) -> int | None:
    if event_type != "singles":
        return 3
    name = (tournament or "").lower()
    is_slam = any(p in name for p in SLAM_PATTERNS)
    if tour == "atp" and is_slam:
        return 5
    return 3


def _athlete_id(ath: dict[str, Any], competitor: dict[str, Any]) -> str | None:
    """ESPN's athlete dict carries no numeric id — only a guid. The numeric id
    (the stable join key) lives on the competitor (id / uid 'a:NNNN') and in the
    athlete's playercard link href '/id/NNNN/'. Try all three."""
    for ln in ath.get("links") or []:
        if isinstance(ln, dict):
            m = re.search(r"/id/(\d+)", str(ln.get("href", "")))
            if m:
                return m.group(1)
    if ath.get("id") is not None:
        return str(ath["id"])
    if competitor.get("type") == "athlete" and competitor.get("id") is not None:
        return str(competitor["id"])
    m = re.search(r"a:(\d+)", str(competitor.get("uid", "")))
    return m.group(1) if m else None


def extract_players(competitors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pull player(s) from each competitor. Handles singles (one athlete) and
    doubles (multiple athletes via athletes/roster)."""
    players: list[dict[str, Any]] = []
    for comp in competitors:
        names: list[str] = []
        ids: list[str] = []
        country = None
        # singles: single athlete object
        ath = comp.get("athlete")
        ioc = None
        if isinstance(ath, dict):
            nm = first_str(ath.get("displayName"), ath.get("fullName"), ath.get("shortName"))
            if nm:
                names.append(nm)
            aid = _athlete_id(ath, comp)
            if aid:
                ids.append(aid)
            flag = as_dict(ath.get("flag"))
            country = first_str(flag.get("alt"), ath.get("citizenship"))
            # IOC code is embedded in the flag image path: .../countries/500/sui.png
            mc = re.search(r"/countries/\d+/([a-z]{2,3})\.png", str(flag.get("href", "")))
            if mc:
                ioc = mc.group(1).upper()
        # doubles / fallback: list of athletes
        for key in ("athletes", "roster"):
            lst = comp.get(key)
            if isinstance(lst, list):
                for a in lst:
                    a = a.get("athlete", a) if isinstance(a, dict) else {}
                    nm = first_str(a.get("displayName"), a.get("fullName"), a.get("shortName"))
                    if nm and nm not in names:
                        names.append(nm)
                    aid = _athlete_id(a, comp)
                    if aid and aid not in ids:
                        ids.append(aid)
        if not names:  # last-ditch
            nm = first_str(comp.get("displayName"), comp.get("name"))
            if nm:
                names.append(nm)

        # seed (tournament seeding) and rank (live ATP/WTA ranking) are distinct.
        seed = _to_int(comp.get("seed"))
        rank = _to_int(as_dict(comp.get("curatedRank")).get("current"))

        linescores = []
        for ls in comp.get("linescores") or []:
            if isinstance(ls, dict) and "value" in ls:
                v = ls["value"]
                linescores.append(int(v) if isinstance(v, (int, float)) and float(v).is_integer() else v)

        players.append({
            "name": " / ".join(names) if names else None,
            "espn_id": "/".join(ids) if ids else None,
            "seed": seed,
            "rank": rank,
            "country": country,
            "ioc": ioc,
            "winner": bool(comp.get("winner")) if "winner" in comp else None,
            "linescores": linescores or None,
        })
    return players


def parse_competition(
    comp: dict[str, Any],
    tour: str,
    tournament: str | None,
    surface_hint: str | None,
    grouping_label: str | None,
) -> dict[str, Any] | None:
    """Turn one ESPN competition (a single match) into a JTT fixture row."""
    competitors = comp.get("competitors") or []
    players = extract_players(competitors)
    if len([p for p in players if p["name"]]) < 2:
        return None  # not a parseable head-to-head

    status = as_dict(dig(comp, "status", "type"))
    state = first_str(status.get("state")) or "pre"  # pre | in | post
    completed = bool(status.get("completed"))

    # round: ESPN gives a clean round.displayName (authoritative). notes use the
    # key `text` and hold a match-result summary, NOT round info — keep separate.
    round_obj = as_dict(comp.get("round"))
    disp_round, round_code = normalise_round(round_obj.get("displayName"))
    notes = comp.get("notes") or []
    note = first_str(*(n.get("text") for n in notes if isinstance(n, dict)))

    # event_type: prefer competition.type.slug ("mens-singles"/"womens-doubles"),
    # then grouping label, then a fallback on joined names.
    type_slug = (first_str(dig(comp, "type", "slug"), grouping_label) or "").lower()
    if "double" in type_slug or any(p["name"] and " / " in p["name"] for p in players):
        event_type = "doubles"
    else:
        event_type = "singles"

    # Tour comes from the slug, NOT the endpoint: combined events (e.g. Libéma)
    # are returned under /atp but contain womens-* groupings. Check "women"
    # before "men" since "women" contains the substring "men".
    if "women" in type_slug:
        tour = "wta"
    elif "men" in type_slug:
        tour = "atp"
    # else: keep the endpoint tour (mixed doubles / unknown slug)

    surface = surface_hint or detect_surface(tournament, grouping_label)
    winner_idx = next((i for i, p in enumerate(players) if p.get("winner")), None)

    # Build a clean, name-free score from per-player linescores when completed.
    score = None
    if completed and len(players) == 2 and players[0].get("linescores") and players[1].get("linescores"):
        a, b = players[0]["linescores"], players[1]["linescores"]
        score = " ".join(f"{x}-{y}" for x, y in zip(a, b))
    score = score or note  # fall back to the ESPN result note

    return {
        "match_id": f"espn:{comp.get('id')}",
        "tour": tour,
        "tournament": tournament,
        "location": None,  # set by walk_events from event venue
        "surface": surface,                # nullable; build.R backfills by tournament
        "round": disp_round,
        "round_code": round_code,
        "event_type": event_type,
        "best_of": infer_best_of(tour, tournament, event_type),
        "start": first_str(comp.get("date"), comp.get("startDate")),
        "status": state,
        "completed": completed,
        "players": [
            {k: p[k] for k in ("name", "espn_id", "seed", "rank", "country", "ioc")} for p in players
        ],
        "winner_idx": winner_idx,
        "score": score,
        "linescores": {p["name"]: p["linescores"] for p in players if p.get("linescores")} or None,
    }


def walk_events(data: dict[str, Any], tour: str) -> Iterable[dict[str, Any]]:
    """Yield fixture rows from one scoreboard payload, handling both layouts."""
    events = data.get("events") or []
    for ev in events:
        tournament = first_str(ev.get("name"), ev.get("shortName"))
        location = first_str(dig(ev, "venue", "displayName"))  # e.g. "Stuttgart, Germany"
        # Surface is not reliably in the scoreboard payload. Derive what we can
        # from the tournament name; build.R backfills the rest by tournament.
        surface_hint = detect_surface(tournament)

        # Layout A: tournament -> groupings[] -> competitions[]
        groupings = ev.get("groupings")
        if isinstance(groupings, list) and groupings:
            for g in groupings:
                grp = as_dict(g)
                label = first_str(
                    dig(grp, "grouping", "displayName"),
                    dig(grp, "grouping", "slug"),
                    grp.get("displayName"),
                )
                for comp in grp.get("competitions") or []:
                    if not isinstance(comp, dict):
                        continue
                    row = parse_competition(comp, tour, tournament, surface_hint, label)
                    if row:
                        row["location"] = location
                        yield row
            continue

        # Layout B: flat event -> competitions[]
        for comp in ev.get("competitions") or []:
            if not isinstance(comp, dict):
                continue
            row = parse_competition(comp, tour, tournament, surface_hint, None)
            if row:
                row["location"] = location
                yield row


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def date_window(days: int) -> list[str]:
    today = dt.datetime.now(dt.timezone.utc).date()
    return [(today + dt.timedelta(days=d)).strftime("%Y%m%d") for d in range(days)]


def collect(tours: list[str], dates: list[str], dump_dir: str | None) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for tour in tours:
        t_count = 0
        for date in dates:
            data = fetch_scoreboard(tour, date)
            if dump_dir and data:
                os.makedirs(dump_dir, exist_ok=True)
                with open(os.path.join(dump_dir, f"{tour}_{date}.json"), "w", encoding="utf-8") as fh:
                    json.dump(data, fh, indent=2)
            for row in walk_events(data, tour):
                key = f"{row['tour']}:{row['match_id']}"
                # later days overwrite earlier (status may have advanced)
                seen[key] = row
                t_count += 1
            time.sleep(0.4)  # be polite to ESPN
        log(f"  {tour}: {t_count} match rows across {len(dates)} day(s)")
    return list(seen.values())


def collect_mock(mock_file: str, tours: list[str]) -> list[dict[str, Any]]:
    with open(mock_file, encoding="utf-8") as fh:
        data = json.load(fh)
    tour = tours[0] if tours else "atp"
    rows = list(walk_events(data, tour))
    log(f"  mock {mock_file}: {len(rows)} rows parsed as tour={tour}")
    return rows


def summarise(rows: list[dict[str, Any]]) -> None:
    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        by_type[r["event_type"]] = by_type.get(r["event_type"], 0) + 1
    log(f"  total fixtures : {len(rows)}")
    log(f"  by status      : {by_status}")
    log(f"  by event_type  : {by_type}")
    missing_surface = sum(1 for r in rows if not r["surface"])
    log(f"  missing surface: {missing_surface} (build.R backfills by tournament)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch ATP/WTA fixtures from ESPN scoreboard.")
    ap.add_argument("--days", type=int, default=2, help="forward window incl. today (default 2)")
    ap.add_argument("--date", help="single day YYYYMMDD (overrides --days)")
    ap.add_argument("--tours", default="atp,wta", help="comma list: atp,wta")
    ap.add_argument("--out", default="data/tennis_fixtures.json", help="output JSON path")
    ap.add_argument("--dump-raw", dest="dump_raw", help="dir to save raw ESPN payloads")
    ap.add_argument("--mock-file", dest="mock_file", help="parse a saved payload offline (no HTTP)")
    args = ap.parse_args()

    tours = [t.strip().lower() for t in args.tours.split(",") if t.strip() in TOURS]
    if not tours:
        tours = list(TOURS)

    log(f"JTT Tennis fixtures — tours={tours}")

    if args.mock_file:
        rows = collect_mock(args.mock_file, tours)
    else:
        dates = [args.date] if args.date else date_window(max(1, args.days))
        rows = collect(tours, dates, args.dump_raw)

    # stable sort: upcoming first by start time, then tournament/round
    rows.sort(key=lambda r: (r.get("start") or "", r.get("tournament") or "", r.get("round_code") or ""))

    bundle = {
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z",
        "source": "espn",
        "count": len(rows),
        "fixtures": rows,
    }

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, indent=2, ensure_ascii=False)

    summarise(rows)
    log(f"  wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
