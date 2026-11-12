/* VOLUMES
 *
 *   node tests/volumes_test.js
 *
 * A player's shots, targets or minutes barely move week to week. The percentage he converts them
 * at swings wildly, and the market prices the output - so three hot games push a line above
 * anything the opportunity behind it supports. The opportunity is still there next week. The
 * percentage is not. So the bet is the under.
 *
 * The rule lives in scripts/build_volumes.py, which proves it two ways of its own: --selftest for
 * the refusals, and --backtest, which replays the real odds snapshots through the same assess()
 * the build calls. That second one exists because two earlier analyses of this signal disagreed,
 * each having its own copy of the rule, and the one with a bug was the one I believed.
 *
 * What these check is the half the builder cannot: that the row lands on the right game, shows
 * BOTH halves of the claim (volume held, conversion hot), formats a rate as a percentage only
 * where that is what it is, and takes the UNDER. The last one matters more than it looks - this
 * signal has no over side at all, by measurement, so an over button here would be a bet the
 * backtest says loses money.
 */
const { JSDOM } = require('jsdom');
const { boot, ok, done } = require('./harness');

(async () => {
  const t = await boot('nfl');
  const LAB = t.LAB;
  if (!LAB.volProbe) { ok('the page exposes the volumes tile for testing', false); return done(); }
  ok('the page exposes the volumes tile for testing', true);

  const doc = h => new JSDOM('<div>' + h + '</div>').window.document;
  const game = (home, away) => ({ home: home, away: away });
  const txtOf = h => doc(h).querySelector('.vl-row').textContent.replace(/\s+/g, ' ');

  const row = over => Object.assign({
    player: 'Test Man', team: 'AAA', pos: 'WR', market: 'recYds', side: 'sell',
    volStat: 'targets', volLabel: 'Targets', rateLabel: 'yds/target', ratePct: false,
    line: 76.5, over: 1.9, under: 1.88, book: 'Bet365',
    proj: 60.07, edge: -0.215,
    rate: 8.58, recRate: 16.24,
    volBase: 9.1, volRec: 7.0, baseOut: 78.1, recOut: 113.7,
    baseGames: 5, games: 3,
    nextOpp: { team: 'DDD', rank: 4, of: 32, allowed: 41.2 }
  }, over || {});

  // ---- it shows on the game it belongs to, and nowhere else -------------------------------------
  let p = LAB.volProbe('nfl', { rows: [row()] }, game('AAA', 'DDD'));
  ok('a volumes row shows on the game its player is in',
     doc(p.html).querySelectorAll('.vl-row').length === 1);
  p = LAB.volProbe('nfl', { rows: [row()] }, game('ZZZ', 'YYY'));
  ok('and not on anyone else’s game', doc(p.html).querySelectorAll('.vl-row').length === 0);

  // ---- both halves of the claim have to be on the row -------------------------------------------
  let txt = txtOf(LAB.volProbe('nfl', { rows: [row()] }, game('AAA', 'DDD')).html);
  ok('it says the volume held, normal then recent',
     /Targets held: 9\.1 → 7 over 3/.test(txt),
     '(without this the row is just "his line looks high")');
  ok('and that the conversion ran hot, recent against long-run',
     /yds\/target 16\.24 vs 8\.58 long-run/.test(txt),
     '(this is the half that has to come back down)');
  ok('and how far above the projection the line sits', /↑22%/.test(txt));
  ok('and what the projection actually is', /proj 60\.07/.test(txt));

  /* The matchup is the third leg, and the one that turned out to carry most: split by the upcoming
     defence's rank, the softest third returned -24.2% in the NFL while the toughest returned
     +39.7%. The builder refuses the soft third outright, so a rank on a row is a rank that passed
     - it has to be visible, and it has to say which position it is relative to, because "#4 of 32"
     means nothing without "against receivers". */
  ok('it names the defence he meets next, with its rank',
     /vs DDD/.test(txt) && /#4/.test(txt) && /of 32/.test(txt), '(' + txt + ')');
  ok('and says the rank is against his own position', /vs WR/.test(txt),
     '("#4 of 32" is meaningless without naming what it is #4 against)');

  /* The whole signal is a one-sided bet. There is no over side - measured at -8.9% in the NFL at
     every threshold - so the row must offer the under and the price that goes with it. */
  ok('it takes the UNDER at the posted line', /U76\.5/.test(txt),
     '(there is no over side; the backtest says it loses)');
  ok('and quotes the under price, not the over', /\$1\.88/.test(txt) && !/\$1\.90/.test(txt),
     '(' + (txt.match(/\$[\d.]+/g) || []).join(' ') + ')');

  // ---- a rate is shown as a percentage only when it is one --------------------------------------
  const pct = row({ market: 'receptions', rateLabel: 'catch rate', ratePct: true,
                    line: 5.5, under: 2.15, proj: 4.48, edge: -0.185,
                    rate: 0.64, recRate: 0.86 });
  const ptxt = txtOf(LAB.volProbe('nfl', { rows: [pct] }, game('AAA', 'DDD')).html);
  ok('a catch rate reads as a percentage', /catch rate 86% vs 64% long-run/.test(ptxt),
     '("0.86 vs 0.64" would be a catch rate nobody writes that way)');
  ok('and yards per target does not', /16\.24 vs 8\.58/.test(txt) && !/1624%/.test(txt));

  /* A row whose opponent could not be ranked is still shown - missing information is not a soft
     matchup, and refusing on absent data would quietly drop whole positions. It just says less. */
  const noRank = row({ nextOpp: { team: 'DDD' } });
  const nrTxt = txtOf(LAB.volProbe('nfl', { rows: [noRank] }, game('AAA', 'DDD')).html);
  ok('an unrankable opponent still shows, without inventing a rank',
     /vs DDD/.test(nrTxt) && !/#/.test(nrTxt), '(' + nrTxt + ')');
  const noOpp = row({ nextOpp: null });
  ok('and a row with no opponent at all still renders',
     doc(LAB.volProbe('nfl', { rows: [noOpp] }, game('AAA', 'DDD')).html)
       .querySelectorAll('.vl-row').length === 1);

  // ---- a sport with no volumes build says nothing at all ----------------------------------------
  p = LAB.volProbe('nfl', null, game('AAA', 'DDD'));
  ok('no volumes file means no tile, not an empty one', p.html === '',
     '(an empty tile reads as "nothing qualified", which is a different claim)');
  p = LAB.volProbe('nfl', { rows: [] }, game('AAA', 'DDD'));
  ok('a build with nothing in it renders no tile, but says why in the quiet list',
     p.html === '' && p.quiet.length === 1
     && p.quiet[0].title === 'Volumes'
     && /converting above their long-run rate/.test(p.quiet[0].why),
     '(' + p.quiet.map(q => q.title).join(', ') + ')');

  // ---- it is one tile, so the record can tell it apart from the rest -----------------------------
  p = LAB.volProbe('nfl', { rows: [row(), pct] }, game('AAA', 'DDD'));
  let d = doc(p.html);
  const titles = Array.from(d.querySelectorAll('.sig-ttl')).map(e => e.textContent.trim());
  ok('Volumes is its own tile', titles.length === 1 && titles[0] === 'Volumes',
     '(' + titles.join(', ') + ')');
  ok('and carries every qualifying row for the game', d.querySelectorAll('.vl-row').length === 2);

  // ---- the row is a bet, so the ledger has to be able to read it ---------------------------------
  /* snapshot_signals.js scrapes these tiles for the published record: the player out of .elm-nm b
     and the price out of the odds box, which must carry a dollar sign or the scrape takes the line
     by mistake - that is exactly how Elite Matchups once reported +305 units and +10167% ROI. */
  const one = doc(LAB.volProbe('nfl', { rows: [row()] }, game('AAA', 'DDD')).html);
  ok('the ledger can find the player on the row',
     (one.querySelector('.elm-nm b') || {}).textContent === 'Test Man');
  const box = one.querySelector('.elm-odds');
  ok('and the price box carries a dollar sign', box && /\$\s*1\.88/.test(box.textContent),
     '(' + (box || {}).textContent + ')');
  ok('and the row offers an add-to-card control',
     one.querySelectorAll('[data-d]').length > 0);
  /* The card has to settle as an under. A row scraped as an over would be graded against the
     wrong side of the line and the record would be exactly inverted. */
  const spec = JSON.parse(one.querySelector('[data-d]').getAttribute('data-d'));
  ok('the bet it hands the card is the under', spec.side === 'under',
     '(got ' + spec.side + ')');
  ok('at the posted line and price', spec.line === 76.5 && spec.odds === 1.88);

  // ---- and it works off the real file, if one is staged -----------------------------------------
  /* volumes.json is a tier-3 file, and tier 3 does not load until a fixture is focused. Reading
     S.data.volumes straight after boot() finds nothing and the real-data checks below quietly
     skip - which looks identical to passing. So: focus first, then look. */
  await t.focus(0);
  const live = t.S.data.volumes;
  if (live && live.rows && live.rows.length) {
    const r0 = live.rows[0];
    const lp = LAB.volProbe('nfl', live, game(r0.team, 'ZZZ'));
    ok('the real build renders', doc(lp.html).querySelectorAll('.vl-row').length >= 1,
       '(' + live.rows.length + ' rows in nfl/data/volumes.json)');
    ok('every real row is a sell with both halves of the claim on it',
       live.rows.every(r => r.side === 'sell' && r.recRate > r.rate
                            && r.volBase && r.volRec && r.line >= 2 && r.edge <= -0.10),
       '(a row with recRate <= rate would be a model disagreeing with the market, not a hot run)');
    /* And none of them face a soft-third defence. This is the gate worth guarding in the built
       file rather than only in the builder's own cases: it is the difference between +35.3% and
       -24.2% in the NFL, and a threshold edit that removed it would leave every other check here
       passing. */
    const softest = live.rows
      .filter(r => r.nextOpp && r.nextOpp.rank)
      .map(r => (r.nextOpp.rank - 1) / Math.max(r.nextOpp.of - 1, 1));
    ok('and no real row faces a defence in the softest third for its position',
       softest.every(s => s <= 2 / 3 + 1e-9),
       '(softest kept: ' + (softest.length ? Math.round(100 * Math.max(...softest)) + '%' : 'n/a') + ')');
  } else {
    ok('the real build renders', true, '(no volumes.json staged; synthetic cases only)');
  }

  done();
})();
