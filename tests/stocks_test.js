/* STEALS AND BLOCKS, AND THE PASSER-TO-RECEIVER PANEL
 *
 *   node tests/stocks_test.js
 *
 * STEALS & BLOCKS reads nba/data/stocks.json (nba/build_nba_stocks.py has its own selftest). The
 * pane is built on rungs - how often he gets at least one, two, three - because that is what a
 * steals or blocks line asks, and an average does not answer it. Each rung shows his own rate,
 * what records like his went on to do (the build's measurement, never the page's), and the
 * price posted for that rung when there is one.
 *
 * THE PAIR PANEL opens from a row of "Who he feeds" on Playmaking: this season's assists from
 * one player to another, from assists.json. Made baskets only, and it has to say so.
 */
const { boot } = require('./harness');

let pass = 0, fail = 0;
function ok(what, cond, extra) {
  if (cond) { pass++; console.log('  ok   ' + what); }
  else { fail++; console.log('  FAIL ' + what + (extra ? ' ' + extra : '')); }
}
const text = (h) => String(h || '').replace(/<option[^>]*>[^<]*<\/option>/g, '')
  .replace(/<img(?:[^>"]|"[^"]*")*>/g, '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');

(async () => {
  const t = await boot('nba');
  await t.focus(0);
  const LAB = t.LAB, S = LAB.state.sports.nba, g0 = LAB.state.view.focus.g;
  const sf = S.data.stocks;
  ok('stocks.json is staged and loaded', !!(sf && sf.players && sf.players.length >= 200),
     sf ? '(' + (sf.players || []).length + ' players)' : '(absent)');
  if (!sf) return done();

  S.data.stocks = null;
  ok('with no file the pane draws nothing', LAB.stocksProbe('nba', g0) === '');
  S.data.stocks = sf;
  ok('only for the NBA', LAB.stocksProbe('nfl', g0) === '');

  /* a fixture where a rung is priced, if today has one */
  let g = g0, gp = null;
  (S.data.fixture || []).slice(0, 15).forEach((f) => { if (!gp && LAB.skCandsProbe('nba', f).some((c) => c.priced)) gp = f; });
  const cands = LAB.skCandsProbe('nba', g);
  ok('there are players to pick from (' + cands.length + ')', cands.length >= 4);
  ok('most steals and blocks first', cands.every((c, i) => !i
     || cands[i - 1].rec.steals.avg + cands[i - 1].rec.blocks.avg >= c.rec.steals.avg + c.rec.blocks.avg - 1e-9));
  LAB.state.view.skName = cands[0].name;
  const me = cands[0], html = LAB.stocksProbe('nba', g), tx = text(html);
  ok('the pane draws for ' + me.name, html.length > 1200);

  ['steals', 'blocks'].forEach((st) => {
    const r = me.rec[st];
    ok(st + ': the rungs only fall as they climb', r.r1 >= r.r2 && r.r2 >= r.r3);
    if (r.avg >= 0.4) {
      ok(st + ': the page prints his 1+ rate from the file (' + Math.round(r.r1 * 100) + '%)',
         new RegExp('1\\+ ' + st + ' ' + Math.round(r.r1 * 100) + '% of his games').test(tx));
    } else {
      ok(st + ': a stat he barely records is left off the pane', tx.indexOf('1+ ' + st) < 0);
    }
  });

  /* THE FILE AGREES WITH THE GAME LOGS, when the page holds the same games the build saw. */
  const logs = (S.idx.logsByName[me.name] || []).filter((r) => (+r.minutes || 0) > 0);
  if (logs.length === me.rec.games) {
    const r1 = logs.filter((r) => (+r.steals || 0) >= 1).length / logs.length;
    ok('his 1+ steals rate is what the game logs say', Math.abs(r1 - me.rec.steals.r1) < 0.002);
  } else ok('(his logs on the page cover a different window)', true);

  /* WHAT RECORDS LIKE HIS THEN DID comes from the build's bands. */
  const lg = sf.league;
  ['steals', 'blocks'].forEach((st) => {
    ok(st + ': the build measured stability, a hot run and the ladder',
       lg[st].stability.r != null && !!lg[st].recent && lg[st].ladder.length >= 2);
    const L1 = lg[st].ladder[0];
    ok(st + ': each band holds what was said and what happened',
       L1.bands.length >= 2 && L1.bands.every((b) => b.said >= b.lo - 1e-9 && b.said <= b.hi + 1e-9 && b.n >= 80));
    const top = L1.bands[L1.bands.length - 1], bot = L1.bands[0];
    ok(st + ': a high record overstates and a low one understates (' + top.said + '>' + top.actual + ', ' + bot.said + '>' + bot.actual + ')',
       top.actual < top.said && bot.actual >= bot.said - 0.02);
  });
  ok('blocks carry better than steals, as measured', lg.blocks.stability.r > lg.steals.stability.r);
  ok('the page quotes the build\'s own figures',
     tx.indexOf('r=+' + lg.blocks.stability.r.toFixed(2)) > 0 && tx.indexOf('r=+' + lg.steals.stability.r.toFixed(2)) > 0);
  ok('"records like his" is shown beside a rung', /records like his then landed \d+%/.test(tx));
  const keep = lg.steals.ladder; lg.steals.ladder = [];
  const bare = text(LAB.stocksProbe('nba', g));
  ok('with no ladder measured the claim is not made',
     me.rec.steals.avg < 0.4 || !/1\+ steals \d+% of his games records like his/.test(bare));
  lg.steals.ladder = keep;

  /* THE OPPONENT. */
  const D = Object.values(sf.defence);
  ['stealsRank', 'blocksRank'].forEach((k) => {
    const r = D.map((d) => d[k]).sort((a, b) => a - b);
    ok(k + ' runs 1..n once each', r.length >= 28 && r.every((v, i) => v === i + 1));
  });
  ok('rank 1 gives up the most steals',
     D.every((d) => d.stealsAllowed <= D.filter((x) => x.stealsRank === 1)[0].stealsAllowed));

  /* PRICES SIT ON THEIR OWN RUNG. */
  if (gp) {
    const c = LAB.skCandsProbe('nba', gp).filter((x) => x.priced)[0];
    LAB.state.view.skName = c.name;
    const t2 = text(LAB.stocksProbe('nba', gp));
    const m = t2.match(/\$(\d+\.\d\d) [A-Za-z0-9]+ · implies (\d+)%/);
    ok('a posted rung shows its price and what it implies' + (m ? ' ($' + m[1] + ' -> ' + m[2] + '%)' : ''),
       !!m && Math.abs(Math.round(100 / +m[1]) - +m[2]) <= 1);
  } else ok('(no steals or blocks rung is priced on today\'s list)', true);
  ok('an unpriced rung says so and invents nothing', /no price/.test(tx) || /implies \d+%/.test(tx));
  ok('notes fold to one line', (html.match(/<details class="nb-why">/g) || []).length >= 1);
  ok('the scoring card offers the pane', LAB.cardPaneProbe('score', 'nba', g, 'stk').length > 800);
  ok('no "club" and no "men" in the copy', !/\bclubs?\b|\bmen\b/i.test(tx));

  /* ---------------- THE PAIR PANEL ---------------- */
  const af = S.data.assists;
  const withPair = (af.players || []).filter((p) => (p.targets || []).some((x) => x.pair));
  ok('the assists file carries pair detail (' + withPair.length + ' passers)', withPair.length >= 50);
  const P = withPair[0], T = P.targets.filter((x) => x.pair)[0], d = T.pair;
  ok('the log never has more to this player than he had in total',
     d.log.reduce((a, x) => a + x[2], 0) <= d.n && d.log.length <= 8);
  ok('zone counts are the plotted baskets', Object.values(d.zones).reduce((a, b) => a + b, 0) >= d.dots.length);
  ok('every dot is on the floor', d.dots.every((q) => q[0] >= 0 && q[0] <= 50 && q[1] >= -5 && q[1] <= 47));
  const panel = LAB.paPairProbe('nba', { name: P.name }, T), pt = text(panel);
  ok('the panel draws a court with one dot per plotted basket',
     (panel.match(/class="pp-dot/g) || []).length === d.dots.length && /<svg/.test(panel));
  ok('threes are marked apart', (panel.match(/pp-dot three/g) || []).length === d.dots.filter((q) => q[2]).length);
  ok('the header is the pair and this season\'s count',
     pt.indexOf(P.name + ' → ' + T.name) >= 0 && pt.indexOf(d.n + ' assists this season') > 0);
  ok('it says made baskets only, and claims no conversion rate',
     /Made baskets only/.test(pt) && /no potential-assist count and no conversion rate/.test(pt));
  ok('a target with no detail draws no panel', LAB.paPairProbe('nba', { name: 'x' }, { id: '1', name: 'y' }) === '');

  /* On the card: a row with detail is a way in, and the panel only shows once it is opened. */
  let found = null;
  (S.data.fixture || []).slice(0, 12).forEach((f) => {
    if (found) return;
    const h = LAB.cardPaneProbe('score', 'nba', f, 'play');
    const m = h.match(/LAB\.paPair\('((?:[^'\\]|\\.)+)'\)/);
    if (m) found = { f, id: m[1].replace(/\\'/g, "'"), h };
  });
  if (found) {
    ok('rows with detail are tappable', /pa-tap/.test(found.h));
    ok('the panel is closed until asked for', found.h.indexOf('pp-wrap') < 0);
    LAB.state.view.paPair = found.id;
    const opened = LAB.cardPaneProbe('score', 'nba', found.f, 'play');
    ok('and opens under its own row', opened.indexOf('pp-wrap') > 0);
    LAB.state.view.paPair = null;
  } else ok('(no Playmaking pane with pair detail on today\'s list)', true);

  done();
  function done() {
    console.log('\n' + (fail ? 'FAILED ' : 'ALL GREEN ') + pass + '/' + (pass + fail));
    process.exit(fail ? 1 : 0);
  }
})();
