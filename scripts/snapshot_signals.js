#!/usr/bin/env node
/* JTT signal ledger — take a fixed-time snapshot of every signal the tool is showing.
 *
 *   node scripts/snapshot_signals.js [--lead 60] [--window 30] [--serve .] [--out data/signal_log.json]
 *
 * WHY A FIXED LEAD TIME
 * The signals move with the odds. A row that shows at 9am, vanishes at noon when the price
 * shortens, and returns at 3pm is not three bets - it is the tile doing its job. Grading
 * "whatever was showing" makes the record depend on when the grader happened to look, and the
 * window could be tuned after the fact to flatter the results. So every game is snapshotted ONCE,
 * at a fixed lead time before it starts: late enough that lineups are out and the price is close
 * to what a subscriber would get, early enough that the bet is still placeable.
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
const LEAD_MIN   = +arg('lead', 60);      // snapshot this long before kick-off
const WINDOW_MIN = +arg('window', 30);    // the run catches games inside this band
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

  const now = Date.now();
  const lo = now + (LEAD_MIN - WINDOW_MIN / 2) * 60000;
  const hi = now + (LEAD_MIN + WINDOW_MIN / 2) * 60000;
  const startOf = g => Date.parse(g.gameTimeUTC || g.utc || g.date || '');

  const log = (() => {
    try { return JSON.parse(fs.readFileSync(OUT, 'utf8')) || { rows: [] }; }
    catch (e) { return { rows: [] }; }
  })();
  log.rows = log.rows || [];
  const seen = new Set(log.rows.map(r => r.k));

  const sports = Object.keys(L.state.sports);
  let added = 0, games = 0;

  for (const key of sports) {
    // one sport at a time, so the page is only ever loading what this pass needs
    Object.keys(L.state.view.active).forEach(k => {
      if (!!L.state.view.active[k] !== (k === key)) L.toggle(k);
    });
    const S = L.state.sports[key];
    for (let i = 0; i < 80 && S.status !== 'ready'; i++) await wait(250);
    if (S.status !== 'ready') continue;

    const due = (S.data.fixture || []).map((g, i) => ({ g, i, t: startOf(g) }))
      .filter(x => x.t && x.t >= lo && x.t <= hi);
    if (!due.length) continue;

    for (const { g, i, t } of due) {
      L.focus(key, i);
      for (let n = 0; n < 200 && S.t3 !== 'ready'; n++) await wait(400);
      await wait(800);
      games++;
      const app = w.document.getElementById('app');
      const tiles = Array.from(app.querySelectorAll('.sig-tile'));
      const stamp = new Date().toISOString().slice(0, 16) + 'Z';
      const fixture = (g.away || g.awayAbbr) + '@' + (g.home || g.homeAbbr);

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
          // every row type names its price box differently
          const priceTxt = ((row.querySelector('.gl-odds, .stk-odds, .elm-odds, .h2h-sig-odds') || {}).textContent || '');
          const price = parseFloat((priceTxt.match(/\$?([\d.]+)/) || [])[1]);
          const evTxt = ((row.querySelector('.gl-ev') || {}).textContent || '');
          const ev = parseFloat(evTxt.replace('%', ''));
          // the key is the BET, not the moment: one row per player-market-line-fixture, ever
          const k = [key, fixture, title, name, (spec && spec.mkt) || '', (spec && spec.line) != null ? spec.line : ''].join('|');
          if (seen.has(k)) return;
          seen.add(k);
          log.rows.push({
            k, at: stamp, sport: key, fixture, start: new Date(t).toISOString().slice(0, 16) + 'Z',
            tile: title, player: name,
            market: (spec && spec.mkt) || null,
            line: (spec && spec.line) != null ? spec.line : null,
            side: (spec && spec.side) || 'over',
            price: isFinite(price) ? price : null,
            book: (spec && spec.book) || null,
            edge: isFinite(ev) ? ev / 100 : null,
            result: null, actual: null            // filled in by the grader once the game is played
          });
          added++;
        });
      });
    }
  }

  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, JSON.stringify(log));
  console.log('signal ledger: ' + games + ' game(s) in the ' + LEAD_MIN + '-minute window, '
    + added + ' new row(s), ' + log.rows.length + ' total');
  process.exit(0);
})().catch(e => { console.error(String(e && e.stack || e)); process.exit(1); });
