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
import os, sys, json, re, urllib.request, urllib.error, urllib.parse

URL    = "https://www.footywire.com/afl/footy/injury_list"
BUNDLE = os.environ.get("AFL_BUNDLE", "AFL/bundle.json")
OUT    = os.environ.get("INJURY_OUT", "AFL/data/injury.json")
# A real browser UA + headers. A bot UA is the first thing FootyWire's edge blocks;
# this makes a plain HTTP request look like Chrome. (If the block is IP-based rather
# than UA-based, this won't help — the diagnostics below will say so.)
UA     = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
    "Referer": "https://www.footywire.com/",
    "Connection": "close",
}
TIMEOUT = 30
RETRIES = 3                       # (kept for reference; multi-source fetch below supersedes it)
MIN_PLAYERS, MIN_TEAMS = 30, 8   # below this == almost certainly a broken parse
# substrings that betray a block / challenge page rather than the real injury list
BLOCK_SIGNS = ("just a moment", "cloudflare", "captcha", "access denied",
               "attention required", "enable javascript", "unusual traffic", "cf-chl")
# FootyWire blocks GitHub's datacenter IPs, so we also try public relays that fetch the
# page from THEIR servers and return the raw HTML (parser stays the same). Best-effort:
# if a relay is down/rate-limited we fall through; if all fail we keep the existing data.
RELAYS = [
    lambda u: "https://api.allorigins.win/raw?url=" + urllib.parse.quote(u, safe=""),
    lambda u: "https://corsproxy.io/?url="        + urllib.parse.quote(u, safe=""),
    lambda u: "https://thingproxy.freeboard.io/fetch/" + u,
]

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


def _get(u):
    req = urllib.request.Request(u, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("iso-8859-1", "replace")  # FootyWire is ISO-8859-1


def _looks_ok(html):
    low = html.lower()
    hit = [w for w in BLOCK_SIGNS if w in low]
    if hit:
        return False, "block/challenge page (" + ", ".join(hit) + ")"
    if "<table" not in low:
        return False, f"no <table> in response ({len(html)} chars)"
    return True, ""


def fetch_html():
    """Try FootyWire directly, then via public relays that fetch it server-side.
    Returns raw HTML from the first source that yields a real injury page.
    Raises the last error if every source fails."""
    sources = [("footywire direct", URL)]
    sources += [(f"relay {i+1}", mk(URL)) for i, mk in enumerate(RELAYS)]
    last = None
    for label, u in sources:
        try:
            html = _get(u)
        except urllib.error.HTTPError as e:
            print(f"::warning::{label}: HTTP {e.code}"); last = e; continue
        except Exception as e:
            print(f"::warning::{label}: {e}"); last = e; continue
        ok, why = _looks_ok(html)
        if not ok:
            print(f"::warning::{label}: {why}"); last = RuntimeError(why); continue
        print(f"fetched injuries via {label} ({len(html)} chars)")
        return html
    if last:
        raise last
    raise RuntimeError("all injury sources failed")


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
        print(f"::warning::all sources failed (last HTTP {e.code}) — keeping existing injuries.")
        print("::warning::FootyWire blocks GitHub's datacenter IPs and every relay also failed this run. "
              "If this persists, the durable fix is an AU residential proxy/host or a different injury source."); return 0
    except Exception as e:
        print(f"::warning::all sources failed ({e}) — keeping existing injuries"); return 0

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
        # Diagnostics so the Actions log reveals *why* (block page vs. layout change).
        low = html.lower()
        hit = [w for w in BLOCK_SIGNS if w in low]
        print(f"::warning::response was {len(html)} chars; "
              f"tables seen: {low.count('<table')}, rows seen: {low.count('<tr')}")
        if hit:
            print(f"::warning::looks like a BLOCK/CHALLENGE page (matched: {', '.join(hit)}). "
                  "FootyWire is refusing the request — most likely an IP block on GitHub's runners. "
                  "Options: run the fetch from an AU host/proxy, or switch to another injury source.")
        else:
            print("::warning::no block signature found — this looks like a LAYOUT CHANGE on FootyWire. "
                  "The table structure the parser anchors on has moved.")
        # first 400 chars help identify the served page at a glance
        print("::group::response head\n" + html[:400].replace("\n", " ") + "\n::endgroup::")
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
