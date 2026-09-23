/* Snapshot today's odds so the models can one day be judged on prices you could actually
 * have bet, rather than on lines the model invented for itself.
 *
 * Run: node tools/snapshot-odds.js        (JTT_ROOT overrides the repo root)
 *
 * Writes <SPORT>/odds-history/YYYY-MM-DD.json.gz, one file per sport per day. Each run MERGES
 * into that day's file rather than replacing it, keeping the LAST price seen for every bet.
 *
 * That matters now the odds worker refreshes every 15-20 minutes through a slate. A bet's props
 * disappear from the feed once its game starts, so the last snapshot that still contains a bet is
 * the closest thing we have to its closing price. Overwriting kept only whatever survived the
 * final pull of the day - which for an early game is nothing at all. Merging gives every game a
 * near-closing price, which is what the grader settles against.
 *
 * Storage: the raw odds files are 0.9-5 MB and mostly duplication - the same line quoted by
 * a dozen books. Keeping the best price per player/market/line cuts the row count by ~70%,
 * and gzip takes the rest. A season of four sports lands in the low tens of MB.
 */
const fs = require('fs'), path = require('path'), zlib = require('zlib');
const ROOT = process.env.JTT_ROOT || path.resolve(__dirname, '..');
const SPORTS = ['AFL', 'nfl', 'EPL', 'mlb', 'nbl', 'nhl'];
const today = new Date().toISOString().slice(0, 10);

let wrote = 0;
for (const dir of SPORTS){
  const src = `${ROOT}/${dir}/data/odds.json`;
  if (!fs.existsSync(src)){ console.log(`${dir}: no odds.json, skipped`); continue; }

  let odds;
  try { odds = JSON.parse(fs.readFileSync(src, 'utf8')); }
  catch (e){ console.log(`${dir}: odds.json unreadable (${e.message}), skipped`); continue; }

  const two = (odds.books && odds.books.length ? odds.books : (odds.lines || []));
  const alt = odds.alt || [];
  const rows = two.concat(alt).filter(r => r && r.player && r.market && r.line != null);
  if (!rows.length){ console.log(`${dir}: no priced rows, skipped`); continue; }

  // Best available price per player/market/line. Keep the under too: Death Riders are priced
  // off it, and only two-way rows carry one.
  const best = {};
  for (const r of rows){
    const k = `${r.player}|${r.market}|${r.line}`;
    const e = best[k];
    if (!e){ best[k] = { player:r.player, market:r.market, line:+r.line,
                         over:r.over != null ? +r.over : null, overBook:r.over != null ? r.book : null,
                         under:r.under != null ? +r.under : null, underBook:r.under != null ? r.book : null };
             continue; }
    if (r.over != null && (e.over == null || +r.over > e.over)){ e.over = +r.over; e.overBook = r.book; }
    if (r.under != null && (e.under == null || +r.under > e.under)){ e.under = +r.under; e.underBook = r.book; }
  }
  const out = Object.values(best);

  // Fixtures in play at the time of capture, so a grader can tie a price to a game without
  // guessing which round it belonged to.
  let fixture = [];
  try {
    fixture = JSON.parse(fs.readFileSync(`${ROOT}/${dir}/data/fixture.json`, 'utf8'))
      // date (local), week/season and abbreviations are what the grader needs to find the
      // gamelog row each price was for - AFL/NFL gamelogs have no dates, MLB uses full names here
      .map(g => ({ home:g.home, away:g.away, utc:g.utc || g.gameTimeUTC || g.date || null,
                   date:g.date || null, season:g.season != null ? g.season : null,
                   homeAbbr:g.homeAbbr || null, awayAbbr:g.awayAbbr || null,
                   week:g.week != null ? g.week : null, gamePk:g.gamePk != null ? g.gamePk : null }));
  } catch (e) {}

  const captured = new Date().toISOString().slice(0, 19) + 'Z';
  const seenAt = odds.updated || captured;               // when these prices were PULLED
  const outDir = `${ROOT}/${dir}/odds-history`;
  fs.mkdirSync(outDir, { recursive: true });
  const file = `${outDir}/${today}.json.gz`;

  // merge into the day's file: last price seen wins, bets that have vanished are kept as they were
  let prior = null;
  if (fs.existsSync(file)){
    try { prior = JSON.parse(zlib.gunzipSync(fs.readFileSync(file)).toString('utf8')); }
    catch (e){ console.log(`${dir}: existing snapshot unreadable (${e.message}), starting fresh`); }
  }
  // the worker runs every 15 min and most runs pull nothing: if these are prices we have already
  // recorded, leave the file alone rather than stamping a fresher "last seen" on stale odds
  if (prior && Array.isArray(prior.pulls) && prior.pulls.indexOf(seenAt) >= 0){
    console.log(`${dir}: no new pull since ${seenAt}, snapshot unchanged`);
    continue;
  }
  const merged = {}, keyOf = r => `${r.player}|${r.market}|${r.line}`;
  let carried = 0, updated = 0, added = 0;
  if (prior && Array.isArray(prior.odds)){
    for (const r of prior.odds){ merged[keyOf(r)] = r; carried++; }
  }
  for (const r of out){
    const k = keyOf(r), had = merged[k];
    r.seen = seenAt;                                     // last pull that still carried this bet
    r.first = had && had.first ? had.first : seenAt;     // first time we saw it
    if (had){ updated++; carried--; } else added++;
    merged[k] = r;
  }
  const rowsOut = Object.values(merged);
  const pulls = ((prior && prior.pulls) || []).concat([seenAt]).filter((v, i, a) => a.indexOf(v) === i).slice(-200);

  const body = { sport:dir, captured:captured,
                 firstCaptured: (prior && prior.firstCaptured) || captured,
                 oddsUpdated: seenAt,                    // when the prices were pulled, not saved
                 pulls:pulls, rows:rowsOut.length, sourceRows:rows.length,
                 fixture: (fixture.length ? fixture : ((prior && prior.fixture) || [])), odds:rowsOut };

  fs.writeFileSync(file, zlib.gzipSync(Buffer.from(JSON.stringify(body)), { level: 9 }));
  const kb = (fs.statSync(file).size / 1024).toFixed(0);
  console.log(`${dir}: ${rows.length} rows -> ${added} new, ${updated} repriced, ${carried} carried `
    + `= ${rowsOut.length} bets over ${pulls.length} pull(s), ${kb} KB -> ${dir}/odds-history/${today}.json.gz`);
  wrote++;
}
console.log(wrote ? `\nsnapshotted ${wrote} sport(s) for ${today}` : '\nnothing snapshotted');
