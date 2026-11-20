/* FIRST BASKET - the First Score pane for the NBA
 *
 *   node tests/first_test.js
 *
 * The pane reads nba/data/first.json (nba/build_nba_first.py has its own selftest) and, when the
 * books have posted it, the first-basket price. It multiplies three things out for tonight's
 * projected five: the tip, the team, the player. This checks that the arithmetic on the page is
 * the arithmetic it describes, that each piece is weighted by what the BUILD measured it to be
 * worth, and that the chances it prints add up.
 */
const { boot } = require('./harness');

let pass = 0, fail = 0;
function ok(what, cond, extra) {
  if (cond) { pass++; console.log('  ok   ' + what); }
  else { fail++; console.log('  FAIL ' + what + (extra ? ' ' + extra : '')); }
}
const text = (h) => String(h || '').replace(/<img(?:[^>"]|"[^"]*")*>/g, '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');
const near = (a, b, e) => Math.abs(a - b) < (e || 1e-9);

(async () => {
  const t = await boot('nba');
  await t.focus(0);
  const LAB = t.LAB, S = LAB.state.sports.nba, g = LAB.state.view.focus.g;
  const ff = S.data.first;
  ok('first.json is staged and loaded', !!(ff && ff.players && ff.players.length >= 150),
     ff ? '(' + (ff.players || []).length + ' players)' : '(absent)');
  if (!ff) return done();

  S.data.first = null;
  ok('with no file the pane draws nothing', LAB.firstProbe('nba', g) === '');
  S.data.first = ff;
  ok('only for the NBA', LAB.firstProbe('nfl', g) === '');

  /* WHAT THE BUILD MEASURED. */
  const lg = ff.league, st = lg.stability;
  ok('the tip winner scores first more than half the time, and well short of always (' + lg.tipToFirst + ')',
     lg.tipToFirst > 0.52 && lg.tipToFirst < 0.8 && lg.tipGames > 500);
  ok('a jumper\'s record carries better than a scorer\'s (' + st.jumper + ' v ' + st.basket + ')', st.jumper > st.basket);
  ok('taking the first shot carries better than making the first basket (' + st.shot + ' v ' + st.basket + ')',
     st.shot > st.basket);
  ok('the honest bands pull both ends toward the middle',
     lg.honest.length >= 2 && lg.honest[0].then > lg.honest[0].said
     && lg.honest[lg.honest.length - 1].then < lg.honest[lg.honest.length - 1].said);
  ok('every player\'s counts are consistent',
     ff.players.every((p) => p.gameFirst <= p.teamFirst && p.teamFirst <= p.starts && p.firstShot <= p.starts));

  /* THE MODEL, PIECE BY PIECE. */
  const M = LAB.fbModelProbe('nba', g);
  ok('there is a projected five on each side', M.away.length === 5 && M.home.length === 5);
  const eff = (j) => (j ? 0.5 + (j.rate - 0.5) * M.keepJ : 0.5);
  const a = eff(M.ja), h = eff(M.jh);
  ok('a jumper\'s rate is pulled toward even by how much of it carries',
     near(M.keepJ, Math.max(0, Math.min(1, st.jumper))));
  ok('the tip is the two jumpers set against each other', near(M.tipAway, (a * (1 - h)) / (a * (1 - h) + h * (1 - a)), 1e-9));
  ok('the team\'s chance follows from the tip and the measured rate',
     near(M.firstAway, M.tipAway * M.q + (1 - M.tipAway) * (1 - M.q)) && near(M.q, lg.tipToFirst));
  ok('each five\'s shares add to all of their team\'s chance',
     near(M.away.reduce((x, r) => x + r.fair, 0), M.firstAway, 1e-9)
     && near(M.home.reduce((x, r) => x + r.fair, 0), 1 - M.firstAway, 1e-9));
  ok('and the ten add to one basket', near(M.away.concat(M.home).reduce((x, r) => x + r.fair, 0), 1, 1e-9));
  const r0 = M.away.concat(M.home).filter((r) => r.rec)[0];
  ok('the model leans on whichever record the build measured as the better predictor',
     M.useShot === (M.keepS > M.keepP) && near(M.keepS, Math.max(0, Math.min(1, st.shotToBasket))));
  ok('a player\'s own record moves him only as far as it has been shown to be worth',
     near(r0.own, M.useShot ? 0.2 + (r0.rec.firstShotRate - 0.2) * M.keepS
                            : 0.2 + (r0.rec.teamFirstRate - 0.2) * M.keepP));
  ok('so nobody in a five is given more than double an even share',
     M.away.concat(M.home).every((r) => r.share < 0.4 && r.share > 0.1));
  ok('rows are in order of chance', M.away.every((r, i) => !i || M.away[i - 1].fair >= r.fair));

  /* THE JUMPER IS SOMEONE IN TONIGHT'S FIVE. */
  [['away', M.ja], ['home', M.jh]].forEach(([k, j]) => {
    ok(k + ': the jumper, if there is one, is in the projected five', !j || M[k].some((r) => r.name === j.name));
  });
  const keepJ = ff.jumpers; ff.jumpers = {}; delete S.idx.fbJump;
  const M0 = LAB.fbModelProbe('nba', g);
  ok('with no tip record on either side the tip is even and so is the team', near(M0.tipAway, 0.5) && near(M0.firstAway, 0.5));
  ff.jumpers = keepJ; delete S.idx.fbJump;

  /* A PLAYER WITH NO STARTS ON FILE is treated as one in five, and the page says so. */
  const keepIdx = S.idx.fbIdx; S.idx.fbIdx = {};
  const M1 = LAB.fbModelProbe('nba', g);
  ok('with no records the five split their team\'s chance evenly', M1.away.every((r) => near(r.share, 0.2)));
  ok('and the pane says why', /no starts on file/.test(text(LAB.firstProbe('nba', g))));
  S.idx.fbIdx = keepIdx;

  /* THE PANE. */
  const html = LAB.firstProbe('nba', g), tx = text(html);
  ok('the pane draws', html.length > 2000);
  ok('it prints each team\'s tip and first-basket chance',
     tx.indexOf('wins the tip ' + Math.round(M.tipAway * 100) + '%') > 0
     && tx.indexOf('scores first ' + Math.round(M.firstAway * 100) + '%') > 0);
  ok('fair prices are one over the chance',
     tx.indexOf((M.away[0].fair * 100).toFixed(1) + '% fair $' + (1 / M.away[0].fair).toFixed(2)) > 0);
  ok('the note quotes the build\'s own figures',
     tx.indexOf(lg.tipGames.toLocaleString() + ' games') > 0 && tx.indexOf('r=+' + st.jumper.toFixed(2)) > 0);
  ok('it says a free throw does not count', /free throw does not count/.test(tx));
  ok('and that it is for the projected five', /projected five/.test(tx));

  /* A POSTED PRICE sits beside the fair one. */
  const od = S.data.odds, keepAlt = od.alt;
  od.alt = (keepAlt || []).concat([{ player: M.away[0].name, market: 'firstScorer', line: 0.0, over: 8.5, under: null, book: 'TestBook' }]);
  if (S.idx) { delete S.idx.oddsRows; delete S.idx.oddsAll; }
  const priced = text(LAB.firstProbe('nba', g));
  ok('a posted first-basket price is shown with what it implies',
     /\$8\.50 TestBook · implies 12%/.test(priced) || /\$\d+\.\d\d \w+ · implies \d+%/.test(priced));
  od.alt = keepAlt;
  if (S.idx) { delete S.idx.oddsRows; delete S.idx.oddsAll; }

  ok('the scoring card offers the pane', LAB.cardPaneProbe('score', 'nba', g, 'first').length > 2000);
  ok('other sports keep their own first-scorer pane', LAB.cardPaneProbe('score', 'nba', g, 'first').indexOf('First TD') < 0);
  ok('the note folds to one line', /<details class="nb-why">/.test(html));
  ok('no "club" and no "men" in the copy', !/\bclubs?\b|\bmen\b/i.test(tx));

  done();
  function done() {
    console.log('\n' + (fail ? 'FAILED ' : 'ALL GREEN ') + pass + '/' + (pass + fail));
    process.exit(fail ? 1 : 0);
  }
})();
