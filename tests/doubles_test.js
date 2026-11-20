/* DOUBLE-DOUBLES AND TRIPLE-DOUBLES - the Doubles pane and the two tiles' price
 *
 *   node tests/doubles_test.js
 *
 * The odds feed carries no double-double or triple-double market for the NBA, so the tiles could
 * only ever say "N of his last 20" - and the one price they did look up was never shown, because
 * the tile asked a helper that returns {od, pr, ev} and then read .price off it.
 *
 * This checks the pane built from nba/data/doubles.json (the builder has its own selftest for the
 * counting and the measurements), and that the tile now shows the price that is actually posted:
 * 10+ in the leg that decides it, labelled as that and not as a double-double price.
 */
const { boot } = require('./harness');

let pass = 0, fail = 0;
function ok(what, cond, extra) {
  if (cond) { pass++; console.log('  ok   ' + what); }
  else { fail++; console.log('  FAIL ' + what + (extra ? ' ' + extra : '')); }
}
const text = (h) => String(h || '').replace(/<option[^>]*>[^<]*<\/option>/g, '')
  .replace(/<img(?:[^>"]|"[^"]*")*>/g, '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');
const CATS = ['points', 'rebounds', 'assists', 'steals', 'blocks'];
const tens = (r) => CATS.filter((k) => (+r[k] || 0) >= 10).length;

(async () => {
  const t = await boot('nba');
  await t.focus(0);
  const LAB = t.LAB, S = LAB.state.sports.nba;
  const df = S.data.doubles;
  ok('doubles.json is staged and loaded', !!(df && df.players && df.players.length >= 40),
     df ? '(' + (df.players || []).length + ' players)' : '(absent)');
  if (!df) return done();

  /* A fixture with candidates - and, if today's odds allow, one where a leg is priced. */
  const fx = (S.data.fixture || []).slice(0, 15);
  let g = null, gPriced = null;
  fx.forEach((f) => {
    const c = LAB.ddCandsProbe('nba', f);
    if (!c.length) return;
    if (!g) g = f;
    if (!gPriced && c[0].priced) gPriced = f;
  });
  ok('a fixture on the list has double-double candidates', !!g);
  if (!g) return done();

  S.data.doubles = null;
  ok('with no file the pane draws nothing', LAB.doubleProbe('nba', g) === '');
  S.data.doubles = df;
  ok('only for the NBA', LAB.doubleProbe('nfl', g) === '');

  const cands = LAB.ddCandsProbe('nba', g);
  ok('highest rate first', cands.every((c, i) => !i || cands[i - 1].rec.ddRate >= c.rec.ddRate));
  LAB.state.view.ddName = cands[0].name;
  const me = cands[0], rec = me.rec;
  const html = LAB.doubleProbe('nba', g), tx = text(html);
  ok('the pane draws for ' + me.name, html.length > 1500);
  ok('the header is his own count', tx.indexOf('double-double in ' + rec.dd + ' of ' + rec.games + ' games') > 0);

  /* THE FILE AGREES WITH THE GAME LOGS for a player whose every game on file is in the build's
     window - checked on the rule itself, ten in two categories. */
  const logs = (S.idx.logsByName[me.name] || []).filter((r) => (+r.minutes || 0) > 0);
  if (logs.length === rec.games) {
    ok('his count is what the game logs say (' + rec.dd + ')',
       logs.filter((r) => tens(r) >= 2).length === rec.dd);
    ok('and so are his triples (' + rec.td + ')', logs.filter((r) => tens(r) >= 3).length === rec.td);
  } else ok('(his logs on the page cover a different window - ' + logs.length + ' v ' + rec.games + ')', true);

  /* THE LEGS. */
  ok('three legs, in order of how often he reaches ten',
     rec.legs.length === 3 && rec.legs[0].rate >= rec.legs[1].rate && rec.legs[1].rate >= rec.legs[2].rate);
  ok('the deciding leg is the second one', rec.hard === rec.legs[1].k);
  ok('a double-double cannot be more likely than its harder leg', rec.ddRate <= rec.legs[1].rate + 1e-9);
  ok('the pane marks the leg that decides it', /the leg that decides it/.test(tx));
  ok('the fair figure is his record with the measured shrink taken off',
     Math.abs(rec.fair - Math.max(0, Math.min(0.98, df.league.shrink.intercept + df.league.shrink.slope * rec.ddRate))) < 0.002);
  ok('and is never above the record for a high-rate player', rec.ddRate < 0.3 || rec.fair <= rec.ddRate);
  ok('the page prints it as a price ($' + (1 / rec.fair).toFixed(2) + ')',
     tx.indexOf('fair $' + (1 / rec.fair).toFixed(2)) > 0);

  /* WHAT THE BUILD MEASURED IS WHAT THE PAGE SAYS. */
  const lg = df.league;
  ok('the build scored four forecasts', ['season', 'last20', 'last10', 'last5'].every((k) => lg.forecast[k] > 0));
  ok('the page quotes those scores, not its own',
     tx.indexOf('whole season ' + lg.forecast.season.toFixed(3)) > 0 && tx.indexOf('last 5 ' + lg.forecast.last5.toFixed(3)) > 0);
  ok('minute bands are drawn from the file (' + lg.minutes.length + ')',
     lg.minutes.length >= 3 && lg.minutes.every((b) => tx.indexOf(b.n + ' games') > 0));
  ok('more minutes never lands less often than far fewer',
     lg.minutes[lg.minutes.length - 1].actual > lg.minutes[0].actual);
  const keepF = lg.forecast, keepM = lg.minutes;
  lg.forecast = {}; lg.minutes = [];
  const bare = text(LAB.doubleProbe('nba', g));
  ok('with nothing measured the page asserts nothing',
     bare.indexOf('forecasts the next game') < 0 && bare.indexOf('What minutes do to it') < 0);
  ok('and still shows his legs', bare.indexOf('How he gets there') > 0);
  lg.forecast = keepF; lg.minutes = keepM;

  /* THE OPPONENT. */
  const D = df.defence, ranks = Object.values(D).map((d) => d.ddRank).sort((a, b) => a - b);
  ok('every team is ranked once (' + ranks.length + ')', ranks.length >= 28 && ranks.every((r, i) => r === i + 1));
  const top = Object.values(D).filter((d) => d.ddRank === 1)[0];
  ok('rank 1 concedes the most', Object.values(D).every((d) => d.dd <= top.dd));
  ok('the pane shows the concession, ranked', /double-doubles conceded a game \d+(st|nd|rd|th) of \d+/.test(tx));

  /* PRICED AS ITS LEGS. Never called a double-double price, because it is not one. */
  ok('the pane has the legs block', tx.indexOf('Priced as its legs') > 0);
  ok('nothing on the pane claims a double-double market', !/double-double (price|odds) (of|is|at) \$/i.test(tx));
  if (gPriced) {
    const c0 = LAB.ddCandsProbe('nba', gPriced)[0];
    LAB.state.view.ddName = c0.name;
    const h2 = LAB.doubleProbe('nba', gPriced), t2 = text(h2);
    const m = t2.match(/\$(\d+\.\d\d) 10\+ (points|rebounds|assists|steals|blocks) (\d+)% implied/);
    if (m) {
      ok('a posted leg shows its price and what that price implies ($' + m[1] + ' -> ' + m[3] + '%)',
         Math.abs(Math.round(100 / +m[1]) - +m[3]) <= 1);
      ok('next to how often he actually does it', /he does it \d+%/.test(t2));
    } else ok('(no 10+ rung posted for ' + c0.name + ' today)', true);
    /* THE TILE: the price that was looked up and never shown. */
    const rows = LAB.bigNightsProbe('nba', gPriced, 2);
    const withPrice = rows.filter((r) => r.b && r.b.price > 1);
    if (withPrice.length) {
      const r = withPrice[0];
      ok('the tile row carries a real price for ' + r.name + ' ($' + r.b.price + ')', r.b.price > 1 && !!r.b.book);
      ok('on the 10+ rung, not some other line', Math.ceil(r.b.line) === 10);
    } else ok('(no tile row has a 10+ price today)', true);
  } else ok('(no priced candidate on today\'s list - leg prices not checked)', true);

  /* THE TILE STILL RANKS BY HIS RECORD, and shows a row whether or not anything is priced. */
  const any = LAB.bigNightsProbe('nba', g, 2);
  ok('the tile is not gated on a price', any.length === 0 || any.some((r) => r.rate >= 0.4));

  /* ITS OWN PICKER. */
  if (cands.length > 1) {
    LAB.state.view.ddName = cands[1].name;
    ok('choosing another player redraws for him', text(LAB.doubleProbe('nba', g)).indexOf(cands[1].name + ' v ') >= 0);
  }
  LAB.state.view.ddName = 'Nobody'; LAB.state.view.ptName = 'Nobody';
  ok('an unknown pick falls back to the top of the list', text(LAB.doubleProbe('nba', g)).indexOf(cands[0].name + ' v ') >= 0);

  ok('long notes fold to one line', (html.match(/<details class="nb-why">/g) || []).length >= 2);
  ok('the scoring card offers the pane', LAB.cardPaneProbe('score', 'nba', g, 'dd').length > 1000);
  ok('no "club" and no "men" in the copy', !/\bclubs?\b|\bmen\b/i.test(tx));

  done();
  function done() {
    console.log('\n' + (fail ? 'FAILED ' : 'ALL GREEN ') + pass + '/' + (pass + fail));
    process.exit(fail ? 1 : 0);
  }
})();
