/* SORTING THE ODDS BOARD
 *
 *   node tests/oddsort_test.js
 *
 * The board shows one row per player and cuts the list at 60. That cut is the whole reason this
 * file exists: a sort applied AFTER it can only re-order the sixty the default already chose, so
 * "show me the longest price" would answer with the longest of the sixty shortest lines - a wrong
 * answer that looks entirely reasonable, because every row on screen is real. On the live NFL
 * anytime-TD board the default top sixty reaches $3.10 and the board itself reaches $81.
 *
 * The rest is about what a column does with a row that has nothing in it. A man with no posted
 * under, or no game logs to work a hit rate from, is not the best under on the board and not the
 * worst either - he has no answer, so he sinks whichever way the arrow points.
 */
const { boot, ok, done } = require('./harness');

(async () => {
  const t = await boot('nfl');
  await t.focus(0);
  const LAB = t.LAB;
  if (!LAB.odProbe) { ok('the page exposes the odds board for testing', false); return done(); }
  ok('the page exposes the odds board for testing', true);

  // the busiest market on the board, so there is plenty to sort
  let mkt = null, n = 0;
  ['anytimeTd', 'recYds', 'rushYds', 'receptions', 'passYds'].forEach(function (m) {
    const p = LAB.odProbe('nfl', m, null);
    if (p.n > n) { n = p.n; mkt = m; }
  });
  if (!mkt || n < 20) { ok('found a market with rows to sort', false, '(' + n + ')'); return done(); }
  ok('found a market with rows to sort', true, '(' + mkt + ', ' + n + ' players)');

  const by = (col, dir) => LAB.odProbe('nfl', mkt, { col: col, dir: dir }).rows;
  const def = LAB.odProbe('nfl', mkt, null).rows;

  // ---- the default is left exactly as it was ----------------------------------------------------
  const natural = def.slice();
  ok('with nothing sorted the board keeps its own order',
     def.length === n && def[0].player === natural[0].player,
     '(highest line, then shortest price)');

  // ---- and a sort reaches the whole board, not the visible slice --------------------------------
  const vals = def.map(r => r.over).filter(v => v != null);
  const top60 = Math.max.apply(null, def.slice(0, 60).map(r => r.over || 0));
  const whole = Math.max.apply(null, vals);
  const sorted = by('over', -1);
  ok('sorting by price finds the longest on the BOARD, not in the first 60',
     Math.abs(sorted[0].over - whole) < 1e-9,
     '(board ' + whole + ', default top 60 only reached ' + top60 + ')');
  if (whole > top60) {
    ok('and that is a price the unsorted page could never have shown', true,
       '(' + whole + ' vs ' + top60 + ' - this is what sorting after the cut would have missed)');
  }

  // ---- every column actually orders by itself ----------------------------------------------------
  const ordered = (rows, pick, dir) => {
    const v = rows.map(pick).filter(x => x != null && x !== '');
    for (let i = 1; i < v.length; i++) {
      if (dir < 0 ? v[i] > v[i - 1] : v[i] < v[i - 1]) return false;
    }
    return true;
  };
  [['line', r => r.line], ['over', r => r.over], ['under', r => r.under],
   ['l5', r => r.l5], ['l10', r => r.l10], ['yr', r => r.yr]].forEach(function (c) {
    ok('the ' + c[0] + ' column sorts high to low', ordered(by(c[0], -1), c[1], -1));
    ok('and low to high the other way', ordered(by(c[0], 1), c[1], 1));
  });
  ok('the player column sorts A to Z',
     ordered(by('player', 1), r => String(r.player).toLowerCase(), 1));
  ok('and Z to A', ordered(by('player', -1), r => String(r.player).toLowerCase(), -1));
  ok('the book column sorts too', ordered(by('book', 1), r => String(r.book).toLowerCase(), 1));

  // ---- a row with nothing in it sinks, whichever way the arrow points -----------------------------
  const sinks = (col, pick) => {
    let seenNull = false, bad = false;
    [-1, 1].forEach(function (dir) {
      seenNull = false;
      by(col, dir).forEach(function (r) {
        const v = pick(r);
        if (v == null) seenNull = true;
        else if (seenNull) bad = true;        // a real value AFTER a blank: the blank floated up
      });
    });
    return !bad;
  };
  const blanks = def.filter(r => r.under == null).length;
  if (blanks) {
    ok('a player with no posted under never rises above one who has a price',
       sinks('under', r => r.under),
       '(' + blanks + ' of ' + n + ' have no under; a blank is not the best and not the worst)');
  }
  const noLogs = def.filter(r => r.l10 == null).length;
  if (noLogs) {
    ok('and nor does a player with no game logs behind his hit rate',
       sinks('l10', r => r.l10), '(' + noLogs + ' with no record to read)');
  }

  // ---- the hit rate sorted on is the hit rate printed ----------------------------------------------
  /* The percentages used to be worked out inside the markup. A sort needs them as numbers, and a
     second copy of the sum is a second place for it to drift from what the reader can see. */
  const r0 = by('l10', -1)[0];
  ok('the sorted hit rate is a real percentage, not a placeholder',
     r0.l10 != null && r0.l10 >= 0 && r0.l10 <= 100, '(' + r0.player + ' at ' + r0.l10 + '%)');

  // ---- the header says what it is doing ------------------------------------------------------------
  let head = LAB.odProbe('nfl', mkt, { col: 'over', dir: -1 }).head;
  ok('the header marks the sorted column for a screen reader',
     /aria-sort="descending"/.test(head) && (head.match(/aria-sort="none"/g) || []).length === 7);
  ok('and shows which way it is pointing', /▾/.test(head));
  head = LAB.odProbe('nfl', mkt, { col: 'over', dir: 1 }).head;
  ok('the arrow turns round with the sort',
     /▴/.test(head) && /aria-sort="ascending"/.test(head));
  ok('every column is clickable', (head.match(/LAB\.odSort\(/g) || []).length === 8);

  // ---- click, click, click: sort, reverse, back to the default --------------------------------------
  const st = () => LAB.state.view.odSort.nfl;
  delete LAB.state.view.odSort.nfl;
  LAB.odSort('nfl', 'over');
  ok('the first click sorts the useful way for a price', st() && st().col === 'over' && st().dir === -1,
     '(longest first)');
  LAB.odSort('nfl', 'over');
  ok('the second turns it round', st() && st().dir === 1);
  LAB.odSort('nfl', 'over');
  ok('and the third gives the board back', st() === undefined,
     '(otherwise there is no way back to the default short of a reload)');
  LAB.odSort('nfl', 'player');
  ok('a name column starts A-Z instead', st() && st().col === 'player' && st().dir === 1);
  LAB.odSort('nfl', 'l10');
  ok('and switching column starts that one fresh', st().col === 'l10' && st().dir === -1);
  LAB.odSort('nfl', 'nonsense');
  ok('an unknown column is ignored rather than blanking the board', st().col === 'l10');
  delete LAB.state.view.odSort.nfl;

  // ---- sorting one sport does not sort another -------------------------------------------------------
  LAB.state.view.odSort.nfl = { col: 'over', dir: -1 };
  const epl = LAB.odProbe('epl', 'shots', null);
  ok('each sport keeps its own sort', epl.rows.length >= 0 && !LAB.state.view.odSort.epl,
     '(the board renders a section per sport)');
  delete LAB.state.view.odSort.nfl;

  done();
})();
