/* Snapshot today's odds so the models can one day be judged on prices you could actually
 * have bet, rather than on lines the model invented for itself.
 *
 * Run: node tools/snapshot-odds.js        (JTT_ROOT overrides the repo root)
 *
 * Writes <SPORT>/odds-history/YYYY-MM-DD.json.gz, one file per sport per day. Each run
 * updates the current day's file rather than adding another, so the history grows by one
 * file per sport per day no matter how often the workflow fires.
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

  const body = { sport:dir, captured:new Date().toISOString().slice(0, 19) + 'Z',
                 oddsUpdated: odds.updated || null,     // when the prices were pulled, not saved
                 rows:out.length, sourceRows:rows.length, fixture, odds:out };

  const outDir = `${ROOT}/${dir}/odds-history`;
  fs.mkdirSync(outDir, { recursive: true });
  const file = `${outDir}/${today}.json.gz`;
  fs.writeFileSync(file, zlib.gzipSync(Buffer.from(JSON.stringify(body)), { level: 9 }));
  const kb = (fs.statSync(file).size / 1024).toFixed(0);
  console.log(`${dir}: ${rows.length} rows -> ${out.length} best-price rows, ${kb} KB gzipped -> ${dir}/odds-history/${today}.json.gz`);
  wrote++;
}
console.log(wrote ? `\nsnapshotted ${wrote} sport(s) for ${today}` : '\nnothing snapshotted');
