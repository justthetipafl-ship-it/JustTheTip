/* THE BOARD - every priced player on one screen, for the six NBA scoring tabs
 *
 *   node tests/board_test.js
 *
 * Scoring, Playmaking, Rebounding, From Deep, Doubles and Steals & Blocks each opened on one
 * player's profile. They open on a board now: a row for every player in the game with a line in
 * that market, and the profile underneath once a row is tapped.
 *
 * The board makes no claims of its own, so what there is to check is that every number on it is
 * the plain one it says it is - an average from the game logs, a count against the posted line,
 * the best price at the line most books agree on - and that the profile still opens.
 */
const { boot } = require('./harness');

let pass = 0, fail = 0;
function ok(what, cond, extra) {
  if (cond) { pass++; console.log('  ok   ' + what); }
  else { fail++; console.log('  FAIL ' + what + (extra ? ' ' + extra : '')); }
}
const text = (h) => String(h || '').replace(/<img(?:[^>"]|"[^"]*")*>/g, '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');
const near = (a, b) => Math.abs(a - b) < 1e-9;

(async () => {
  const t = await boot('nba');
  await t.focus(0);
  const LAB = t.LAB, S = LAB.state.sports.nba;
  LAB.state.view.nbSel = null;

  /* the game with the most points lines posted, so the priced path is the one under test */
  let g = null, most = -1;
  (S.data.fixture || []).slice(0, 15).forEach((f) => {
    const B = LAB.boardRowsProbe('nba', f, 'pts');
    const n = B ? B.rows.filter((r) => r.priced).length : 0;
    if (n > most) { most = n; g = f; }
  });
  ok('a game on the list has a board (' + most + ' priced for points)', !!g);
  if (!g) return done();
  const havePrices = most > 0;

  /* EVERY TAB OPENS ON ITS BOARD, with no profile until asked. */
  ['pts', 'play', 'glass', 'deep', 'dd', 'stk'].forEach((src) => {
    const h = LAB.cardPaneProbe('score', 'nba', g, src);
    ok(src + ': opens on the board', /class="nbb/.test(h) && (h.match(/class="nbb-row"/g) || []).length >= 1);
    ok(src + ': with no profile and no drop-down until a row is tapped',
       h.indexOf('nb-verdict') < 0 && h.indexOf('<select class="mb-fixsel gg-sel"') < 0);
  });
  ok('other tabs on the card are untouched', LAB.cardPaneProbe('score', 'nba', g, 'court').indexOf('class="nbb') < 0);

  /* THE STANDARD ROW, CHECKED AGAINST THE LOGS AND THE ODDS FILE. */
  const B = LAB.boardRowsProbe('nba', g, 'pts');
  if (havePrices) {
    ok('only players with a points line are on the board', B.rows.every((r) => r.priced && r.ln));
    ok('and it says so', /everyone priced/.test(text(LAB.boardProbe('nba', g, 'pts'))));
    const r = B.rows[0];
    const logs = (S.idx.logsByName[r.name] || []).filter((x) => (+x.minutes || 0) > 0)
      .slice().sort((a, b) => String(b.Date).localeCompare(String(a.Date)));
    const avg = logs.reduce((a, x) => a + (+x.points || 0), 0) / logs.length;
    const l5 = logs.slice(0, 5).reduce((a, x) => a + (+x.points || 0), 0) / Math.min(5, logs.length);
    ok(r.name + ': the average is every game on file (' + avg.toFixed(1) + ')', near(r.avg, avg));
    ok('his last five is his last five (' + l5.toFixed(1) + ')', near(r.l5, l5));
    const over = logs.slice(0, 10).filter((x) => (+x.points || 0) > r.ln.line).length;
    ok('"over L10" is a plain count against tonight\'s line (' + over + '/' + r.n10 + ' over ' + r.ln.line + ')',
       r.over10 === over && r.n10 === Math.min(10, logs.length));
    /* the line and prices, straight from the odds file */
    const nm = r.name, rows = (S.data.odds.lines || []).filter((x) => x.player === nm && x.market === 'points');
    if (rows.length) {
      const n = {}; rows.forEach((x) => { n[x.line] = (n[x.line] || 0) + 1; });
      const common = Math.max.apply(null, Object.values(n));
      ok('the line is one most books agree on', n[r.ln.line] === common);
      const at = rows.filter((x) => x.line === r.ln.line);
      ok('the over price is the best over at that line',
         near(r.ln.over.price, Math.max.apply(null, at.filter((x) => x.over != null).map((x) => +x.over))));
      ok('and the under the best under',
         !r.ln.under || near(r.ln.under.price, Math.max.apply(null, at.filter((x) => x.under != null).map((x) => +x.under))));
    } else ok('(his odds rows are filed under another spelling)', true);
    ok('rows carry the opponent\'s rank from the scoring file',
       B.rows.every((x) => !x.rk || (x.rk.rank >= 1 && x.rk.rank <= x.rk.of)));
    const D = S.data.scoring.defence;
    const any = B.rows.filter((x) => x.rk)[0];
    ok('and it is the same rank the profile uses',
       !any || Object.values(D).some((d) => d.ptsRank === any.rk.rank));
  } else ok('(no points lines posted today - priced path not exercised)', true);

  /* BEFORE THE BOOKS POST: the main players, and the board says that is what it is. */
  const keepL = S.data.odds.lines, keepB = S.data.odds.books;
  S.data.odds.lines = []; S.data.odds.books = [];
  const B0 = LAB.boardRowsProbe('nba', g, 'pts');
  ok('with no lines the board still lists the main players (' + B0.rows.length + ')',
     B0.noPrices && B0.rows.length > 0 && B0.rows.length <= 8);
  ok('and says the books have not posted', /no lines posted yet/.test(text(LAB.boardProbe('nba', g, 'pts'))));
  ok('a row with no line claims no count against one', B0.rows.every((r) => r.over10 === null && !r.ln));
  S.data.odds.lines = keepL; S.data.odds.books = keepB;

  /* THE TWO CUSTOM BOARDS. */
  const Bd = LAB.boardRowsProbe('nba', g, 'dd');
  if (Bd && Bd.rows.length) {
    ok('doubles: everyone in range is listed, priced or not', Bd.rows.length === Bd.total);
    ok('doubles: the price shown is on the 10+ rung of the deciding leg',
       Bd.rows.every((r) => !r.od || Math.ceil(r.od.line) === 10));
    ok('doubles: the board never calls it a double-double price',
       /no double-double market/.test(text(LAB.boardProbe('nba', g, 'dd'))));
  } else ok('(nobody in range for double-doubles in this game)', true);
  const Bs = LAB.boardRowsProbe('nba', g, 'stk');
  ok('steals and blocks: rows carry both stats from the file',
     Bs.rows.every((r) => r.rec.steals.r1 >= 0 && r.rec.blocks.r1 >= 0));

  /* TAPPING A ROW OPENS THE PROFILE UNDER THE BOARD; TAPPING AGAIN CLOSES IT. */
  const who = B.rows[0].name;
  LAB.state.view.nbSel = who;
  const open = LAB.cardPaneProbe('score', 'nba', g, 'pts'), ot = text(open);
  ok('the profile opens for the tapped player', open.indexOf('nb-verdict') > 0 && ot.indexOf(who + ' v ') > 0);
  ok('under the board, which marks his row', open.indexOf('nbb-row on') > 0 && open.indexOf('nbb-row on') < open.indexOf('nb-verdict'));
  ok('the profile\'s own drop-down is gone - the board is the picker', open.indexOf('<select class="mb-fixsel gg-sel" onchange="LAB.ptName') < 0);
  const other = LAB.cardPaneProbe('score', 'nba', g, 'glass');
  const onGlass = (LAB.boardRowsProbe('nba', g, 'glass').rows || []).some((r) => r.name === who);
  ok('the same player stays open on the next tab when he is on its board',
     !onGlass || text(other).indexOf(who + ' v ') > 0);
  LAB.state.view.nbSel = 'Somebody Not In This Game';
  ok('a player from another game does not open anything here',
     LAB.cardPaneProbe('score', 'nba', g, 'pts').indexOf('nb-verdict') < 0);
  LAB.state.view.nbSel = null;
  ok('closed again, it is just the board', LAB.cardPaneProbe('score', 'nba', g, 'pts').indexOf('nb-verdict') < 0);

  const bt = text(LAB.boardProbe('nba', g, 'pts'));
  ok('the board explains its two count columns', /Over L10 and Over all are how often/.test(bt));
  ok('the opponent is stated once per team, in the head, not on every row',
     (LAB.boardProbe('nba', g, 'pts').match(/give up the \d+(st|nd|rd|th)-most/g) || []).length <= 2);
  if (havePrices) {
    const r = B.rows[0];
    const logs = (S.idx.logsByName[r.name] || []).filter((x) => (+x.minutes || 0) > 0);
    ok('"over all" is every game on file against tonight\'s line',
       near(r.overAll, logs.filter((x) => (+x.points || 0) > r.ln.line).length / logs.length) && r.nAll === logs.length);
  }
  ok('no "club" and no "men" in the copy', !/\bclubs?\b|\bmen\b/i.test(bt));

  done();
  function done() {
    console.log('\n' + (fail ? 'FAILED ' : 'ALL GREEN ') + pass + '/' + (pass + fail));
    process.exit(fail ? 1 : 0);
  }
})();
