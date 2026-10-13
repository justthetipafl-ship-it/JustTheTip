#!/usr/bin/env node
/* JTT signal ledger — take a fixed-time snapshot of every signal the tool is showing.
 *
 *   node scripts/snapshot_signals.js [--lead 60] [--min-lead 15] [--max-lead 240]
 *                                    [--serve .] [--out data/signal_log.json]
 *
 * WHY A FIXED LEAD TIME
 * The signals move with the odds. A row that shows at 9am, vanishes at noon when the price
 * shortens, and returns at 3pm is not three bets - it is the tile doing its job. Grading
 * "whatever was showing" makes the record depend on when the grader happened to look, and the
 * window could be tuned after the fact to flatter the results. So every game is snapshotted ONCE,
 * at a fixed lead time before it starts: late enough that lineups are out and the price is close
 * to what a subscriber would get, early enough that the bet is still placeable.
 *
 * WHY THE WINDOW IS WIDE, AND WHAT KEEPS IT HONEST
 * That used to be enforced by only looking at games inside a narrow band - 35 minutes either side
 * of the hour mark - which works only if the run actually happens. It does not. GitHub's scheduled
 * runs arrive late and get dropped: in the ledger's first week a half-hourly cron produced four
 * snapshots, each about 25 minutes off the half-hour grid, and Sunday's nine 17:00Z NFL kickoffs
 * needed a run between 16:42 and 17:17 that never came. A narrow band does not buy a fixed lead
 * time; it buys a sparse sample of whichever games a late cron happened to land on, which is a
 * worse thing to publish than a wide one because the bias is invisible.
 *
 * So the window is wide and the fixed lead time is enforced by RECORDING it instead. Every row
 * carries the actual lead in minutes, and the ledger keeps, for each game, the single snapshot
 * taken CLOSEST TO THE TARGET - a later run that gets nearer the hour mark replaces the whole of
 * an earlier, further-out capture of that game. Replacement is per GAME, never per row, so the
 * ledger can never hold a row from the four-hour pass beside a row from the one-hour pass and call
 * them one snapshot; and a game whose rows have already been graded is never disturbed. The rule
 * is "closest to a target fixed in advance", not "whichever looked best", and the lead it settled
 * on is on the face of every row.
 *
 * WHAT IS LOGGED
 * Only the rows the tile actually DISPLAYS, capped as they appear on screen - the ledger measures
 * what a subscriber saw, not every candidate the model considered.
 *
 * The signals only exist in the browser, so this loads the real page under jsdom and reads the
 * rendered tiles. That means the ledger can never drift from the tool: if the page changes, the
 * snapshot changes with it.
 */
const fs = require('fs');
const path = require('path');
const { JSDOM, ResourceLoader } = require('jsdom');

const arg = (k, d) => {
  const i = process.argv.indexOf('--' + k);
  return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : d;
};
const LEAD_MIN   = +arg('lead', 60);      // the target: how long before kick-off we WANT to look
const MIN_LEAD   = +arg('min-lead', 15);  // never log a bet this close to the off - unplaceable
const MAX_LEAD   = +arg('max-lead', 240); // nor one this far out - no lineups, no real price
const ROOT       = path.resolve(arg('serve', '.'));
const OUT        = path.resolve(arg('out', 'data/signal_log.json'));
const PAGE       = path.resolve(arg('page', 'index.html'));

const localPath = u => path.join(ROOT, String(u).replace(/^https?:\/\/[^/]+/, '').split('?')[0]);
const readLocal = f => (fs.existsSync(f) ? fs.readFileSync(f, 'utf8') : null);

// Serve the page's own scripts and styles from the checkout. This uses jsdom's documented
// ResourceLoader: requestInterceptor is not in the published package, and the first CI run died
// on "requestInterceptor is not a function".
class LocalLoader extends ResourceLoader {
  fetch(url) {
    const body = readLocal(localPath(url));
    return body == null ? null : Promise.resolve(Buffer.from(body));
  }
}
const loader = new LocalLoader();

const wait = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  if (!fs.existsSync(PAGE)) { console.error('no page at ' + PAGE); process.exit(1); }
  const dom = new JSDOM(fs.readFileSync(PAGE, 'utf8'), {
    url: 'http://localhost/index.html', runScripts: 'dangerously',
    resources: loader, pretendToBeVisual: true,
    beforeParse(w){
      w.fetch = u => {
        const f = localPath(u), t = readLocal(f);
        if (t == null) return Promise.resolve({ ok:false, status:404,
          json:() => Promise.reject(new Error('404')), text:() => Promise.resolve('') });
        return Promise.resolve({ ok:true, status:200,
          json:() => Promise.resolve(JSON.parse(t)), text:() => Promise.resolve(t) });
      };
      w.scrollTo = () => {};
    }
  });
  const w = dom.window;
  setTimeout(() => { try { w._authUnlock && w._authUnlock(); } catch (e) {} }, 50);
  await wait(3000);
  const L = w.LAB;
  if (!L) { console.error('the page did not boot'); process.exit(1); }

  // --now pins the clock, so a pass can be replayed against a slate that has already happened and
  // the window logic checked against games whose outcome is known
  const AT = arg('now', null);
  const now = AT && isFinite(Date.parse(AT)) ? Date.parse(AT) : Date.now();
  if (AT && !isFinite(Date.parse(AT))) { console.error('--now is not a date: ' + AT); process.exit(1); }
  const startOf = g => Date.parse(g.gameTimeUTC || g.utc || g.date || '');
  const leadOf = t => (t - now) / 60000;          // minutes from this run to kick-off

  /* THE FIXTURE'S OWN ID FOR THE GAME, where its format is proven against the gamelogs.
   * The grader needs this because two of the feeds carry no date at all: the NFL and AFL gamelogs
   * have Year/Week/MatchId and nothing else, so a date-matched grader voided every NFL row it was
   * ever given. Only the two derivations actually verified against logged games are emitted -
   * NFL's season_WW_away_home and EPL's date|home|away. The rest grade off the date, which works,
   * and a guessed id that keys to nothing would be worse than none: it could key to the WRONG
   * game. (NHL, NBA and NBL fixtures do carry a gameId, but their fixture files are forward-only,
   * so there is no played game to check the format against. Left alone deliberately.) */
  const matchIdOf = (key, g) => {
    if (key === 'nfl' && g.season && g.week && g.away && g.home)
      return g.season + '_' + String(g.week).padStart(2, '0') + '_' + g.away + '_' + g.home;
    if (key === 'epl' && g.date && g.home && g.away)
      return String(g.date).slice(0, 10) + '|' + g.home + '|' + g.away;
    return null;
  };

  const log = (() => {
    try { return JSON.parse(fs.readFileSync(OUT, 'utf8')) || { rows: [] }; }
    catch (e) { return { rows: [] }; }
  })();
  log.rows = log.rows || [];

  /* What the ledger already holds, per GAME: how close to target its snapshot was taken, and
   * whether any of it has been graded. A row written before leads were recorded is treated as
   * already perfect, so the fix can never quietly discard the history it inherited. */
  const gameKey = r => [r.sport, r.fixture, r.start].join('|');
  const held = {};
  log.rows.forEach(r => {
    const gk = gameKey(r);
    const off = r.lead == null ? 0 : Math.abs(r.lead - LEAD_MIN);
    const h = held[gk] || (held[gk] = { off: Infinity, graded: false });
    if (off < h.off) h.off = off;
    if (r.result) h.graded = true;
  });

  const sports = Object.keys(L.state.sports);
  let added = 0, games = 0, replaced = 0, skipped = 0;

  for (const key of sports) {
    // one sport at a time, so the page is only ever loading what this pass needs
    Object.keys(L.state.view.active).forEach(k => {
      if (!!L.state.view.active[k] !== (k === key)) L.toggle(k);
    });
    const S = L.state.sports[key];
    for (let i = 0; i < 80 && S.status !== 'ready'; i++) await wait(250);
    if (S.status !== 'ready') continue;

    /* Every game inside the wide window whose snapshot this run would IMPROVE on. Nearest the
     * target first, so if the pass is cut short it is the most representative games that got
     * done. A game already graded is left alone - the record is not re-cut under the grader. */
    const due = (S.data.fixture || []).map((g, i) => ({ g, i, t: startOf(g) }))
      .filter(x => x.t)
      .map(x => Object.assign(x, { lead: leadOf(x.t) }))
      .filter(x => x.lead >= MIN_LEAD && x.lead <= MAX_LEAD)
      .map(x => Object.assign(x, {
        gk: [key, (x.g.away || x.g.awayAbbr) + '@' + (x.g.home || x.g.homeAbbr),
             new Date(x.t).toISOString().slice(0, 16) + 'Z'].join('|'),
        off: Math.abs(x.lead - LEAD_MIN)
      }))
      .filter(x => {
        const h = held[x.gk];
        if (!h) return true;
        if (h.graded || x.off >= h.off) { skipped++; return false; }
        return true;
      })
      .sort((a, b) => a.off - b.off);
    if (!due.length) continue;

    for (const { g, i, t, lead, gk } of due) {
      L.focus(key, i);
      for (let n = 0; n < 200 && S.t3 !== 'ready'; n++) await wait(400);
      await wait(800);
      games++;
      const app = w.document.getElementById('app');
      const tiles = Array.from(app.querySelectorAll('.sig-tile'));
      const stamp = new Date(now).toISOString().slice(0, 16) + 'Z';
      const fixture = (g.away || g.awayAbbr) + '@' + (g.home || g.homeAbbr);
      const mid = matchIdOf(key, g);
      // this game's rows from THIS pass, kept apart until the pass is known to have produced
      // something - a game is replaced whole or not at all
      const fresh = [];
      const mine = new Set();

      tiles.forEach(tile => {
        const title = ((tile.querySelector('.sig-ttl') || {}).textContent || '').trim();
        if (!title) return;
        // every row type the tool renders, each keeping its own class
        const rows = Array.from(tile.querySelectorAll('.gl-row, .um-row, .stk-row, .elm-row, .h2h-sig-row'));
        rows.forEach(row => {
          const btn = row.querySelector('[data-d]');
          let spec = null;
          try { spec = btn ? JSON.parse(btn.getAttribute('data-d')) : null; } catch (e) {}
          const name = ((row.querySelector('.gl-nm b, .stk-nm b, .elm-nm b, .h2h-sig-nm b') || {}).textContent || '').trim();
          if (!name) return;
          /* every row type names its price box differently, and they do not all put the price
             first. Elite Matchups, Bunnies and Bogey render "270+ $3.05 Sportsbet" - the LINE,
             then the price. The dollar sign used to be optional in this pattern, so those rows
             logged 270 as the price, and the ledger paid a winner at 269-to-1: three NFL signals
             came out at +305 units and the published record said Elite Matchups was running at
             +10167% ROI.
             The dollar sign is now required. Every box that holds a price prints one, so a number
             without it is not a price, and null is the right answer - a missing price costs the
             row its units, a wrong one poisons the whole tile. */
          const priceTxt = ((row.querySelector('.gl-odds, .stk-odds, .elm-odds, .h2h-sig-odds') || {}).textContent || '');
          const price = parseFloat((priceTxt.match(/\$\s*([\d.]+)/) || [])[1]);
          const evTxt = ((row.querySelector('.gl-ev') || {}).textContent || '');
          const ev = parseFloat(evTxt.replace('%', ''));
          // the key is the BET, not the moment: one row per player-market-line-fixture
          const k = [key, fixture, title, name, (spec && spec.mkt) || '', (spec && spec.line) != null ? spec.line : ''].join('|');
          if (mine.has(k)) return;
          mine.add(k);
          fresh.push({
            k, at: stamp, sport: key, fixture, start: new Date(t).toISOString().slice(0, 16) + 'Z',
            lead: Math.round(lead),               // HOW FAR OUT this was taken: the ledger's warrant
            matchId: mid,                         // the two feeds with no date are graded off this
            tile: title, player: name,
            market: (spec && spec.mkt) || null,
            line: (spec && spec.line) != null ? spec.line : null,
            side: (spec && spec.side) || 'over',
            price: isFinite(price) ? price : null,
            book: (spec && spec.book) || null,
            edge: isFinite(ev) ? ev / 100 : null,
            result: null, actual: null            // filled in by the grader once the game is played
          });
        });
      });

      /* A pass that rendered nothing changes nothing. Without this an empty render - the page not
       * finished loading, a feed down - would wipe a good earlier snapshot and leave the game with
       * no signals at all, which reads in the record as "the tool said nothing about this game". */
      if (!fresh.length) continue;
      const before = log.rows.length;
      log.rows = log.rows.filter(r => gameKey(r) !== gk);
      if (log.rows.length !== before) replaced++;
      log.rows = log.rows.concat(fresh);
      held[gk] = { off: Math.abs(lead - LEAD_MIN), graded: false };
      added += fresh.length;
    }
  }

  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, JSON.stringify(log));
  console.log('signal ledger: ' + games + ' game(s) between ' + MIN_LEAD + ' and ' + MAX_LEAD
    + ' minutes out (target ' + LEAD_MIN + '), ' + added + ' row(s) logged, '
    + replaced + ' game(s) re-snapshotted closer to target, ' + skipped
    + ' already held at or nearer the target, ' + log.rows.length + ' total');
  process.exit(0);
})().catch(e => { console.error(String(e && e.stack || e)); process.exit(1); });
