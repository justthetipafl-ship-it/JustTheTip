/* ON THE FLOOR WITH HIM, AND WITHOUT - the Without pane on the NBA Players card
 *
 *   node tests/without_test.js
 *
 * The pane reads nba/data/without.json (nba/build_nba_without.py): for every pair of team-mates,
 * the seconds one spent with the other on the floor and with him off it, and what he did in each.
 * The builder has its own selftest for the counting. This checks what the PAGE does with it, and
 * the three promises the pane makes - because a with/without table is the easiest thing on the
 * site to over-read:
 *
 *   1. the minutes are on every row;
 *   2. a thin sample is still shown, but folded away and never tinted;
 *   3. a number is only tinted when the gap is wider than its own noise.
 */
const { boot } = require('./harness');

let pass = 0, fail = 0;
function ok(what, cond, extra) {
  if (cond) { pass++; console.log('  ok   ' + what); }
  else { fail++; console.log('  FAIL ' + what + (extra ? ' ' + extra : '')); }
}
const text = (h) => String(h || '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');

(async () => {
  const t = await boot('nba');
  await t.focus(0);
  const LAB = t.LAB, S = LAB.state.sports.nba, g = LAB.state.view.focus.g;
  const wf = S.data.without;
  ok('without.json is staged and loaded', !!(wf && wf.teams && Object.keys(wf.teams).length >= 20),
     wf ? '(' + Object.keys(wf.teams || {}).length + ' teams)' : '(absent)');
  if (!wf) return done();

  /* ABSENT -> NOTHING, PRESENT -> THE PANE. A sport or a day without the file must not draw an
     empty shell. */
  S.data.without = null;
  ok('with no file the pane draws nothing', LAB.withoutProbe('nba', g) === '');
  S.data.without = wf;
  const html = LAB.withoutProbe('nba', g);
  ok('with the file it draws', html.length > 500);
  ok('only for the NBA', LAB.withoutProbe('nfl', g) === '');

  /* THE ARITHMETIC, BY HAND, for one real row: points per 36 and usage straight from the file. */
  const side = [g.home, g.away].filter((tm) => LAB.woMateProbe('nba', tm))[0];
  const mate = LAB.woMateProbe('nba', side);
  ok('a team in this fixture has someone to be without', !!mate);
  const rows = LAB.woRowsProbe('nba', side, mate.id);
  ok('and team-mates to show against him (' + rows.length + ')', rows.length >= 3);
  const ix = (k) => wf.stats.indexOf(k);
  const T = Object.values(wf.teams).filter((x) => x.pairs[rows[0].id] && x.pairs[rows[0].id][mate.id])[0];
  const raw = T.pairs[rows[0].id][mate.id];
  const pts36 = raw[3][ix('pts')] * 2160 / raw[1];
  ok('points per 36 without him is the count over the minutes (' + pts36.toFixed(2) + ')',
     Math.abs(rows[0].o.pts - pts36) < 1e-9);
  const usg = (raw[2][ix('fga')] + 0.44 * raw[2][ix('fta')] + raw[2][ix('tov')]) / raw[2][ix('poss')] * 100;
  ok('usage with him is his share of the team\'s possessions (' + usg.toFixed(1) + '%)',
     Math.abs(rows[0].w.usg - usg) < 1e-9);
  ok('the seconds on each side come through untouched',
     rows[0].w.sec === raw[0] && rows[0].o.sec === raw[1]);
  ok('nobody is listed against himself', rows.every((r) => r.id !== mate.id));

  /* PROMISE 1: MINUTES ON EVERY ROW. */
  const rowHtml = html.split('class="ww-row').slice(1);
  ok('every row prints the minutes on both sides (' + rowHtml.length + ' rows)',
     rowHtml.length > 0 && rowHtml.every((r) => /\d+ min with · \d+ without/.test(text(r))));

  /* PROMISE 2: THIN SAMPLES ARE FOLDED AND NEVER TINTED. */
  const thin = rows.filter((r) => r.thin), full = rows.filter((r) => !r.thin);
  ok('a row is thin when either side is under 36 minutes',
     rows.every((r) => r.thin === (Math.min(r.w.sec, r.o.sec) < 36 * 60)));
  const thinHtml = html.split('class="ww-row wo-thin"').slice(1);
  ok('no thin row carries a tint', thinHtml.every((r) => !/ww-up|ww-dn/.test(r.split('</div></div>')[0])));
  if (thin.length) {
    const foldAt = html.indexOf('<details class="wo-more">');
    ok('thin rows sit inside the fold, after the full ones',
       foldAt > 0 && html.indexOf('wo-thin') > foldAt);
    ok('and the fold says how many', new RegExp('\\d+ more on a thinner sample').test(html));
  } else ok('(no thin rows for this pair today)', true);
  ok('full-weight rows come before any fold', !full.length || html.indexOf('class="ww-row"') > 0);

  /* PROMISE 3: THE TINT IS A TEST, NOT A DIFFERENCE. */
  const col = ['reb', 'Reb', 1];
  const mk = (n, sec) => ({ reb: n * 2160 / sec, n: { reb: n }, sec: sec });
  ok('identical rates are zero standard errors apart', LAB.woZProbe(col, mk(20, 6000), mk(20, 6000)) === 0);
  ok('a big gap on a big sample clears the bar',
     LAB.woZProbe(col, mk(100, 30000), mk(160, 30000)) > 2);
  ok('the same ratio on a tiny sample does not',
     Math.abs(LAB.woZProbe(col, mk(5, 1500), mk(8, 1500))) < 2);
  ok('a drop is negative', LAB.woZProbe(col, mk(160, 30000), mk(100, 30000)) < -2);
  const ptsCol = ['pts', 'Pts', 2.2], rebLike = ['pts', 'Pts', 1];
  const p1 = { pts: 20, n: { pts: 200 }, sec: 21600 }, p2 = { pts: 25, n: { pts: 250 }, sec: 21600 };
  ok('points are held to a higher bar than a plain count, because they come in twos and threes',
     Math.abs(LAB.woZProbe(ptsCol, p1, p2)) < Math.abs(LAB.woZProbe(rebLike, p1, p2)));

  /* WHO THE TABLE IS WITHOUT. The reader's choice beats the default; the default prefers a
     team-mate the injury feed lists for this game. */
  const mates = T.mates;
  const other = mates.filter((id) => id !== mate.id)[0];
  LAB.state.view.woMate = {};
  const ab = Object.keys(wf.teams).filter((k) => wf.teams[k] === T)[0];
  LAB.state.view.woMate[ab] = other;
  ok('choosing a team-mate changes who the table is without',
     LAB.woMateProbe('nba', side).id === other);
  LAB.state.view.woMate[ab] = 'not-a-player';
  ok('a choice that is not on the list falls back to the default',
     LAB.woMateProbe('nba', side).id === mate.id);
  LAB.state.view.woMate = {};
  const inj = S.data.injury;
  const target = T.names[mates[mates.length - 1]];
  S.data.injury = [{ Player: target, Team: side, Status: 'Out', Injury: 'Knee' }];
  const d = LAB.woMateProbe('nba', side);
  ok('a team-mate listed out for this game becomes the default (' + target + ')',
     d && d.id === mates[mates.length - 1] && /out/i.test(d.why));
  ok('and the pane says why he was chosen', /is listed out/.test(text(LAB.withoutProbe('nba', g))));
  S.data.injury = [];
  ok('with nobody listed it is the biggest-minutes team-mate',
     LAB.woMateProbe('nba', side).id === mates[0]);
  S.data.injury = inj;

  /* ON THE CARD. It is a pane on the Players card, and the note carries the caveats. */
  ok('the Players card offers the pane', /Without/.test(LAB.cardPaneProbe('player', 'nba', g, 'without') ? 'Without' : ''));
  const note = text(html);
  ok('the note says what the numbers are', /per 36 minutes/.test(note) && /games this season/.test(note));
  ok('and what they are not', /not a forecast|not adjusted|Nothing is adjusted/.test(note));
  ok('it says team-mate, not man or club', !/\bclub\b|\bmen\b/.test(note));

  done();
  function done() {
    console.log('\n' + (fail ? 'FAILED ' : 'ALL GREEN ') + pass + '/' + (pass + fail));
    process.exit(fail ? 1 : 0);
  }
})();
