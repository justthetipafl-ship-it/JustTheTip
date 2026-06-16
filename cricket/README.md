# JTT Cricket — Phase 1

Single-file betting research tool. Same house chrome as WC/NRL/MLB (auth gate,
boot overlay, freshness pill, Check My Bet FAB, Degen Crew). Data is a 4-file
JSON bundle refreshed by GitHub Actions from **Cricsheet** ball-by-ball.

## Repo layout (drop into repo root)
```
cricket/
  index.html              ← the tool (assembled, single file)
  fetch_cricket.py        ← Cricsheet → bundle (runs in Actions)
  make_sample.py          ← synthetic bundle for local/demo
  data/                   ← the JSON bundle (sample shipped; Actions overwrites)
    cricket_logs.json     per-innings batting+bowling rows
    cricket_stats.json    per-team / per-format rate profiles
    cricket_fixtures.json upcoming matches (MANUAL — see below)
    cricket_ratings.json  name aliases + pace/spin lookup + ICC tier bands
    version.txt           cache-bust + freshness pill
    auth.json             {"passwordHash": "..."} (written by Actions from secret)
.github/workflows/cricket-data.yml
```

Live target: `justthetipaus.com/cricket` (Cloudflare Pages, same as the others).

## Scope (Phase 1)
- **Formats:** T20 (IT20 + IPL/BBL leagues) **and** International (T20I/ODI/Test).
  Format + Level are first-class filters on every page.
- **Venue splits:** in (free from Cricsheet match metadata).
- **6 Degen signals:** Locked In, Form Alerts, Cap Race, Mismatch Alert,
  Boundary Bullies, Wicket Hauls.
- Tabs: Focused Fixtures · Players · Degen Crew · Check My Bet (FAB).

## Setup
1. Commit the layout above.
2. Add repo secret **`CRICKET_PASSWORD`** = the subscriber code. The workflow
   hashes it (SHA-256) into `cricket/data/auth.json`. No secret = gate off.
3. Point Cloudflare Pages at `/cricket` (or set up the route like the others).
4. Run the **JTT Cricket data** workflow once (Actions → Run workflow) to pull
   real data. It then runs every 6h.

## ⚠ Fixtures are manual
Cricsheet is **historical only** — it has no upcoming fixtures. `fetch_cricket.py`
**preserves** `cricket_fixtures.json` if it exists and never overwrites it.
Maintain upcoming matches by hand (or wire a secondary feed later — e.g. the
post-WC Odds API plan). Schema per fixture:
```json
{ "matchId":"FX1", "format":"T20", "level":"INTL", "comp":"T20I Series",
  "date":"2026-06-20", "utc":"2026-06-20T09:30:00Z",
  "home":"Australia", "away":"India", "venue":"The Gabba", "city":"Brisbane",
  "status":"upcoming" }
```
`cricket_ratings.json` (pace/spin + ICC tiers) is likewise hand-maintained and
preserved across refreshes. Cricsheet does **not** tag pace vs spin — that comes
from the `bowlType` map in the ratings file.

## Local preview
```
python make_sample.py        # writes a demo bundle into ./data
python assemble.py           # (dev) re-injects css+engine → index.html
python -m http.server        # open http://localhost:8000/index.html
```
(`index.html` is already assembled; `assemble.py` is only needed if you edit
`engine.js` / `cricket_style.css` / `wc_style.css`.)

## Data change = redeploy
The bundle schema changed vs other tools, so a data refresh **and** the new
`index.html` must ship together. After editing the engine, re-run `assemble.py`.
