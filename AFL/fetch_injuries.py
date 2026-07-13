#!/usr/bin/env python3
"""
Fetch the official AFL injury list from afl.com.au and write it to AFL/data/injury.json
(and mirror into AFL/bundle.json["injury"]).

Why afl.com.au and not FootyWire: FootyWire sits behind Cloudflare bot protection and
refuses GitHub Actions' datacenter IPs (503 / managed challenge). afl.com.au serves the
injury list as plain server-rendered HTML — 18 tables (one per club, in alphabetical
order), each with rows of PLAYER | INJURY | ESTIMATED RETURN — and is reachable from the
same runners that already pull the AFL stats feed.

Non-destructive: if the fetch fails or the parse looks wrong (blocked / layout change),
the existing injuries are preserved and the run prints diagnostics instead of writing.
"""
import os
import sys
import json
import urllib.request
import urllib.error

from bs4 import BeautifulSoup

URL     = "https://www.afl.com.au/matches/injury-list"
BUNDLE  = os.environ.get("AFL_BUNDLE", "AFL/bundle.json")
OUT     = os.environ.get("INJURY_OUT", "AFL/data/injury.json")
TIMEOUT = 30
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
}

# afl.com.au lists all 18 clubs in this fixed alphabetical order, one <table> each.
# These names match the tool's team naming (players.json / fixture), so injuries line up.
CLUBS = [
    "Adelaide", "Brisbane", "Carlton", "Collingwood", "Essendon", "Fremantle",
    "Geelong", "Gold Coast", "Greater Western Sydney", "Hawthorn", "Melbourne",
    "North Melbourne", "Port Adelaide", "Richmond", "St Kilda", "Sydney",
    "West Coast", "Western Bulldogs",
]

MIN_TABLES, MIN_PLAYERS = 15, 30   # below this == almost certainly a broken/blocked parse
BLOCK_SIGNS = ("just a moment", "cloudflare", "captcha", "access denied",
               "attention required", "enable javascript", "unusual traffic", "cf-chl")


def fetch_html():
    req = urllib.request.Request(URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("iso-8859-1", "replace")


def parse(html):
    """Return (rows, table_count). Each club is one <table>; rows are 3-cell
    PLAYER / INJURY / ESTIMATED RETURN. Header and 'Updated: ...' rows are skipped."""
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    rows = []
    for i, table in enumerate(tables):
        if i >= len(CLUBS):
            break                                   # ignore any trailing/unrelated tables
        club = CLUBS[i]
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) < 3:
                continue                            # 'Updated: ...' colspan row, etc.
            player, injury, ret = cells[0], cells[1], cells[2]
            if not player:
                continue
            if player.upper() == "PLAYER":
                continue                            # header row
            if player.lower().startswith("updated"):
                continue
            rows.append({
                "Team": club,
                "Player": player,
                "Injury": injury,
                "Returning": ret,
            })
    return rows, len(tables)


def main():
    try:
        html = fetch_html()
    except urllib.error.HTTPError as e:
        print(f"::warning::afl.com.au HTTP {e.code} — keeping existing injuries.")
        if e.code in (403, 429, 503):
            print("::warning::a 403/429/503 from a GitHub-Actions IP would mean afl.com.au is "
                  "blocking the datacenter address; if this persists we'd need a proxy or an "
                  "alternate source.")
        return 0
    except Exception as e:
        print(f"::warning::afl.com.au fetch failed ({e}) — keeping existing injuries.")
        return 0

    rows, ntables = parse(html)
    teams = sorted(set(r["Team"] for r in rows))

    if ntables < MIN_TABLES or len(rows) < MIN_PLAYERS:
        print(f"::warning::parse looks wrong ({ntables} tables / {len(rows)} players "
              f"< {MIN_TABLES}/{MIN_PLAYERS}) — NOT writing, existing data preserved")
        low = html.lower()
        hit = [w for w in BLOCK_SIGNS if w in low]
        print(f"::warning::response was {len(html)} chars; <table> seen: {low.count('<table')}")
        if hit:
            print(f"::warning::looks like a BLOCK/CHALLENGE page (matched: {', '.join(hit)}). "
                  "afl.com.au is refusing the request from this IP.")
        else:
            print("::warning::no block signature — this looks like a LAYOUT CHANGE on afl.com.au "
                  "(the injury tables moved or changed structure).")
        print("::group::response head\n" + html[:400].replace("\n", " ") + "\n::endgroup::")
        return 0

    # --- write bundle["injury"] (abort if bundle unreadable, matching prior behaviour) ---
    try:
        with open(BUNDLE, encoding="utf-8") as f:
            bundle = json.load(f)
    except Exception as e:
        print(f"::error::cannot read {BUNDLE} ({e}) — aborting"); return 1
    bundle["injury"] = rows
    tmp = BUNDLE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, BUNDLE)

    # --- mirror to data/injury.json so it's live without a full rebuild ---
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp2 = OUT + ".tmp"
    with open(tmp2, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    os.replace(tmp2, OUT)

    print(f"wrote {len(rows)} injuries across {len(teams)} clubs from {ntables} tables "
          f"-> {BUNDLE}['injury'] + {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
