#!/usr/bin/env python3
# ============================================================
# fetch_injuries.py — FootyWire AFL injury list -> bundle["injury"] + data/injury.json
# ============================================================
# Closes the last manual AFL data gap. injury.json is a flat list of
#   {"Team","Player","Injury","Returning"}  (the shape the tool already reads).
#
# IMPORTANT: build_afl_data.py rewrites data/injury.json from bundle["injury"]
# on every build, so this updates BOTH:
#   * AFL/bundle.json -> ["injury"]  (survives every future rebuild's passthrough)
#   * AFL/data/injury.json           (mirror, so the change is live immediately)
#
# Source: FootyWire serves 18 server-rendered team tables (no JS, no token). We
# anchor on the 18 known team names rather than fragile CSS classes, and map
# FootyWire's labels to the exact strings already in injury.json.
#
# NON-DESTRUCTIVE: only writes if a sane parse (>= MIN_PLAYERS across >= MIN_TEAMS).
# A blocked fetch / login wall / layout change yields ~0 rows -> we abort and
# leave the existing injury data untouched. The first run should be eyeballed.
# ============================================================
import os, sys, json, re, urllib.request, urllib.error

URL    = "https://www.footywire.com/afl/footy/injury_list"
BUNDLE = os.environ.get("AFL_BUNDLE", "AFL/bundle.json")
OUT    = os.environ.get("INJURY_OUT", "AFL/data/injury.json")
UA     = "JTT-AFL-bot/1.0 (+https://justthetipaus.com; weekly injury refresh)"
TIMEOUT = 30
MIN_PLAYERS, MIN_TEAMS = 30, 8   # below this == almost certainly a broken parse

# (needle, canonical) — LONGEST/most-specific needles first so "north melbourne"
# beats "melbourne" and "port adelaide" beats "adelaide". Output strings match
# injury.json verbatim (FootyWire's own labels).
TEAM_KEYS = [
    ("western bulldogs", "Western Bulldogs"),
    ("north melbourne", "North Melbourne"),
    ("port adelaide", "Port Adelaide"),
    ("greater western sydney", "GWS GIANTS"),
    ("west coast", "West Coast Eagles"),
    ("gold coast", "Gold Coast SUNS"),
    ("st kilda", "St Kilda"),
    ("adelaide", "Adelaide Crows"),
    ("brisbane", "Brisbane Lions"),
    ("carlton", "Carlton"),
    ("collingwood", "Collingwood"),
    ("essendon", "Essendon"),
    ("fremantle", "Fremantle"),
    ("geelong", "Geelong Cats"),
    ("gws", "GWS GIANTS"),
    ("hawthorn", "Hawthorn"),
    ("melbourne", "Melbourne"),
    ("richmond", "Richmond"),
    ("sydney", "Sydney Swans"),
]
DATE_RE   = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}")              # FootyWire "Last Updated" cell
HEADER_RE = re.compile(r"^(player|injury|recovery|return|last updated|est\.?)\b", re.I)


def team_from_text(txt):
    t = txt.strip().lower()
    if not t or len(t) > 36:
        return None
    for needle, canon in TEAM_KEYS:
        if needle in t:
            return canon
    return None


def fetch_html():
    req = urllib.request.Request(URL, headers={"User-Agent": UA, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read()
    return raw.decode("iso-8859-1", "replace")  # FootyWire is ISO-8859-1


def parse(html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    rows, current = [], None
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        cells = [c for c in cells if c != ""]
        if not cells:
            continue
        joined = " ".join(cells)
        # team header row: short, dominated by a team name
        t = team_from_text(joined)
        if t and len(cells) <= 2 and len(joined) <= 36:
            current = t
            continue
        if not current:
            continue
        # skip column-header rows
        if HEADER_RE.match(cells[0]):
            continue
        # player row: first cell = name, then Injury / Return (drop any date "Last Updated")
        player = cells[0].strip()
        if len(player) < 3 or len(player) > 48 or player.lower() in ("player", "no injuries"):
            continue
        rest = [c for c in cells[1:] if c and not DATE_RE.search(c)]
        injury    = rest[0] if rest else ""
        returning = rest[1] if len(rest) > 1 else ""
        if not injury and not returning:
            continue
        rows.append({"Team": current, "Player": player, "Injury": injury, "Returning": returning})
    return rows


def main():
    try:
        html = fetch_html()
    except urllib.error.HTTPError as e:
        print(f"::warning::FootyWire HTTP {e.code} — keeping existing injuries"); return 0
    except Exception as e:
        print(f"::warning::FootyWire fetch failed ({e}) — keeping existing injuries"); return 0

    try:
        rows = parse(html)
    except Exception as e:
        print(f"::warning::parse failed ({e}) — keeping existing injuries"); return 0

    teams = sorted(set(r["Team"] for r in rows))
    print(f"parsed {len(rows)} players across {len(teams)} teams")
    by = {}
    for r in rows:
        by[r["Team"]] = by.get(r["Team"], 0) + 1
    for t in teams:
        print(f"  {t}: {by[t]}")
    if rows[:1]:
        print(f"  sample: {rows[0]}")

    if len(rows) < MIN_PLAYERS or len(teams) < MIN_TEAMS:
        print(f"::warning::parse looks wrong ({len(rows)} players / {len(teams)} teams "
              f"< {MIN_PLAYERS}/{MIN_TEAMS}) — NOT writing, existing data preserved")
        return 0

    # update bundle["injury"] (survives future rebuild passthrough)
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

    # mirror to data/injury.json so it's live without a full rebuild
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp2 = OUT + ".tmp"
    with open(tmp2, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    os.replace(tmp2, OUT)

    print(f"wrote {len(rows)} injuries -> {BUNDLE}['injury'] + {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
