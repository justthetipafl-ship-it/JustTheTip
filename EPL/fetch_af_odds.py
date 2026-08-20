"""JTT EPL - player-prop odds from API-Football (/odds), filtered to AU-usable books.
Match/team totals come from rapidodds (all AU books); THIS covers PLAYER props only:
  Anytime Goal Scorer -> goals | Player Assists -> assists | Player Shots -> shots
  Player Shots On Target -> shotsOn | Player Fouls Committed -> fouls | Goalkeeper Saves -> saves
  Player to be booked -> cards
Ladder values ("Player - N+") -> over (N-0.5). Anytime values (bare player name) -> over 0.5.
Reads APIFOOTBALL_KEY. Writes EPL/data/odds.json in the shell's odds shape (over-only alt lines).
"""
import os, json, time, unicodedata, urllib.request, datetime

BASE = "https://v3.football.api-sports.io"
LEAGUE = 39
BOOKS = {"Bet365", "Unibet"}                 # both operate in AU
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
PACE = float(os.environ.get("APIFOOTBALL_PACE") or 1.5)
NEXT = int(os.environ.get("EPL_ODDS_FIXTURES") or 10)

_SPECIAL = str.maketrans({
    "\u00f8": "o", "\u00d8": "o", "\u00e6": "ae", "\u00c6": "ae", "\u0142": "l", "\u0141": "l",
    "\u0111": "d", "\u0110": "d", "\u00fe": "th", "\u00de": "th", "\u00f0": "d", "\u00d0": "d",
    "\u00df": "ss", "\u0131": "i", "\u0130": "i",
})
def nmkey(name):
    s = (name or "").translate(_SPECIAL)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return " ".join("".join(c for c in s.lower() if c.isalnum() or c == " ").split())

# AF bet name -> (JTT stat, kind)
MARKET_MAP = {
    "Anytime Goal Scorer": ("goals", "any"), "Home Anytime Goal Scorer": ("goals", "any"),
    "Away Anytime Goal Scorer": ("goals", "any"),
    "Player Assists": ("assists", "any"), "Home Player Assists": ("assists", "any"),
    "Away Player Assists": ("assists", "any"),
    "Home Player Shots": ("shots", "ladder"), "Away Player Shots": ("shots", "ladder"),
    "Player Shots On Target": ("shotsOn", "ladder"),
    "Home Player Shots On Target Total": ("shotsOn", "ladder"),
    "Player Fouls Committed": ("fouls", "ladder"),
    "Goalkeeper Saves": ("saves", "ladder"),
    "Player to be booked": ("cards", "any"),
}
SKIP = {"yes", "no", "no goalscorer", ""}

def parse_bet(name, values, book):
    m = MARKET_MAP.get(name)
    if not m:
        return []
    stat, kind = m
    out = []
    for v in values:
        val = (v.get("value") or "").strip()
        try:
            over = round(float(v.get("odd")), 2)
        except (TypeError, ValueError):
            continue
        if kind == "ladder":
            if " - " not in val:
                continue                                   # need "Player - N+"
            player, thr = val.rsplit(" - ", 1)
            thr = thr.strip()
            if not thr.endswith("+"):
                continue
            try:
                n = int(thr[:-1])
            except ValueError:
                continue
            out.append((player.strip(), stat, n - 0.5, over))
        else:
            low = val.lower()
            if low in SKIP or low.startswith("over") or low.startswith("under"):
                continue
            out.append((val, stat, 0.5, over))
    return out

def api(path, key):
    req = urllib.request.Request(BASE + path, headers={"x-apisports-key": key, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def build_name_index(players):
    exact, toksets, last = {}, [], {}
    for p in players:
        nm = p.get("name", "")
        keys = {nmkey(nm), p.get("nmkey", "")}
        for k in keys:
            if k:
                exact.setdefault(k, nm)
        ts = set()
        for k in keys:
            ts |= set(k.split())
        if ts:
            toksets.append((ts, nm))
            for t in ts:
                last.setdefault(t, set()).add(nm)
    return exact, toksets, last

def resolve(af_name, exact, toksets, last):
    k = nmkey(af_name)
    if k in exact:                                         # exact key match
        return exact[k]
    at = set(k.split())
    if len(at) >= 2:                                       # AF tokens subset of a unique player's tokens
        hits = {nm for ts, nm in toksets if at <= ts}
        if len(hits) == 1:
            return next(iter(hits))
    toks = k.split()
    if toks:                                               # unique surname fallback
        c = last.get(toks[-1])
        if c and len(c) == 1:
            return next(iter(c))
    return None

def main():
    key = os.environ.get("APIFOOTBALL_KEY")
    if not key:
        raise SystemExit("APIFOOTBALL_KEY not set")
    g = time.gmtime()
    season = os.environ.get("APIFOOTBALL_SEASON") or str(g.tm_year - (0 if g.tm_mon >= 7 else 1))
    players = json.load(open(os.path.join(DATA, "players.json")))
    exact, toksets, last = build_name_index(players)

    fx = api("/fixtures?league=%d&season=%s&next=%d" % (LEAGUE, season, NEXT), key).get("response", [])
    alt, books, unmatched, npp = [], set(), {}, 0
    for f in fx:
        fid = f["fixture"]["id"]
        try:
            od = api("/odds?fixture=%d" % fid, key).get("response", [])
        except Exception as e:
            print("  odds fetch failed for %d: %s" % (fid, e)); od = []
        for entry in od:
            for bk in entry.get("bookmakers", []):
                bn = bk.get("name", "")
                if bn not in BOOKS:
                    continue
                books.add(bn)
                for bet in bk.get("bets", []):
                    for (pl, stat, line, over) in parse_bet(bet.get("name", ""), bet.get("values", []), bn):
                        npp += 1
                        name = resolve(pl, exact, toksets, last)
                        if not name:
                            unmatched[pl] = unmatched.get(pl, 0) + 1
                            continue
                        alt.append({"player": name, "market": stat, "line": line,
                                    "over": over, "under": None, "book": bn})
        time.sleep(PACE)

    out = {"updated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
           "source": "api-football (player props: Bet365/Unibet)",
           "books": sorted(books), "lines": [], "alt": alt, "matchOdds": []}
    os.makedirs(DATA, exist_ok=True)
    json.dump(out, open(os.path.join(DATA, "odds.json"), "w"), separators=(",", ":"))
    matched = len(alt)
    print("EPL player props: %d priced (%d matched / %d unmatched) | books %s | %d fixtures" % (
        npp, matched, sum(unmatched.values()), ",".join(sorted(books)) or "-", len(fx)))
    if unmatched:
        top = sorted(unmatched.items(), key=lambda x: -x[1])[:20]
        print("  unmatched names (fix name-index if these are real players):", top)

if __name__ == "__main__":
    main()
