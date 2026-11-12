/* THE TOOL THAT DECIDES WHETHER A TILE SHIPS
 *
 *   node tests/backtest_test.js
 *
 * tools/grade-odds.js answers the only question that matters before a tile goes live: would
 * backing this, at prices that were really on the board, have made money? Two things were
 * missing from it and both are checked here.
 *
 * AN ERROR BAR. It reported bare ROIs, and on a few hundred picks a bare ROI is mostly noise -
 * a band showed +29% on 36 bets whose honest interval ran from -3% to +56%. The interval is
 * resampled by GAME rather than by pick, because twelve legs off one blowout are nearer one
 * observation than twelve; resampling picks would report an interval several times too narrow
 * and would have made the same mistake look rigorous.
 *
 * A MAIN-LINE FILTER. A snapshot stores the whole alternate ladder - one man's points at twenty
 * rungs from $1.01 to $41 - so grading a rule across all of them measures the ladder, not the
 * rule. "Rebounds over when a rival sits" came back at -69% purely because 43 of its 90 picks
 * were $6-plus rungs that were never going to land.
 *
 * And the rule files themselves have to be real: a rule that returns a side it was not asked
 * about would be graded on both sides of the same bet.
 */
const path = require('path');
const fs = require('fs');
const { execFileSync } = require('child_process');
const { ok, done } = require('./harness');

const ROOT = path.resolve(__dirname, '..');
const TOOL = path.join(ROOT, 'tools', 'grade-odds.js');

(() => {
  ok('the grading tool is there', fs.existsSync(TOOL));
  if (!fs.existsSync(TOOL)) return done();

  /* It must be requirable WITHOUT grading seven sports as a side effect, or no test can reach
     its internals. */
  let mod = null;
  try { mod = require(TOOL); } catch (e) { ok('requiring it does not run it (' + e.message + ')', false); }
  ok('it can be required without running', !!mod && typeof mod.isMainLine === 'function');
  if (!mod) return done();

  /* THE MAIN-LINE FILTER, against the shapes a real snapshot holds. */
  const main = mod.isMainLine;
  ok('a two-way market at even money is a main line',
     main({ over: 1.9, under: 1.92 }) === true);
  ok('and one priced a little off centre still is',
     main({ over: 2.3, under: 1.7 }) === true);
  ok('a $1.01 rung near the bottom of the ladder is not',
     main({ over: 1.01, under: null }) === false);
  ok('nor a $41 rung at the top', main({ over: 41, under: null }) === false);
  ok('nor one with only a single side priced', main({ over: 1.9, under: null }) === false);
  ok('nor a rung whose other side is a longshot', main({ over: 1.2, under: 4.5 }) === false);
  ok('and nothing at all is not a main line', main(null) === false);
  /* The band has to be narrow enough to exclude the ladder and wide enough to keep real
     markets - a filter that kept everything or nothing would pass the checks above by accident
     if the bounds were silly. */
  ok('the band is a plausible two-way range (' + mod.MAIN_LO + '-' + mod.MAIN_HI + ')',
     mod.MAIN_LO >= 1.4 && mod.MAIN_LO <= 1.7 && mod.MAIN_HI >= 2.2 && mod.MAIN_HI <= 2.8);

  /* ABSENT TEAM-MATES, which every "X is out tonight" rule leans on. */
  const logs = [
    { MatchId: '1', Team: 'DEN', Player: 'Plays', minutes: 30 },
    { MatchId: '1', Team: 'DEN', Player: 'Sits', minutes: 0 },
    { MatchId: '2', Team: 'DEN', Player: 'Sits', minutes: 28 },
    { MatchId: '1', Team: 'BOS', Player: 'Other', minutes: 25 }
  ];
  const abs = mod.absentFor({ MatchId: '1', Team: 'DEN' }, logs);
  ok('a team-mate who did not take the floor is absent', abs.indexOf('Sits') >= 0);
  ok('one who played is not', abs.indexOf('Plays') < 0);
  ok('and nobody from the other club is listed', abs.indexOf('Other') < 0);
  ok('a row with no match or club returns nothing rather than throwing',
     mod.absentFor({}, logs).length === 0);

  /* EVERY SHIPPED SPORT must be in the tool's list, or it grades nothing and says nothing -
     which is exactly how the NBA ended up with no odds history, no grading and no calibration
     while looking fine. */
  const dirs = fs.readdirSync(ROOT).filter(d =>
    fs.existsSync(path.join(ROOT, d, 'config.js')) && fs.existsSync(path.join(ROOT, d, 'scoring.js')));
  const listed = new Set(mod.SPORTS.map(([, d]) => d));
  const missing = dirs.filter(d => !listed.has(d));
  ok('every sport that ships is in the grader' + (missing.length ? ' - missing ' + missing.join(', ') : ''),
     missing.length === 0);

  /* THE RULE FILES. */
  const rdir = path.join(ROOT, 'tools', 'rules');
  ok('there is a rules directory with examples', fs.existsSync(rdir));
  const rules = fs.existsSync(rdir) ? fs.readdirSync(rdir).filter(f => f.endsWith('.js')) : [];
  ok('with at least two rules to copy from (' + rules.length + ')', rules.length >= 2);
  ok('and a README explaining how to read the result',
     fs.existsSync(path.join(rdir, 'README.md')));

  const ctx = {
    sport: 'nba', player: 'Nobody', market: 'rebounds', line: 8.5, over: 1.9, under: 1.9,
    side: 'over', price: 1.9, opp: 'BOS', team: 'DEN', day: '2026-10-20',
    prior: [], absent: [], data: () => null, prob: 0.5, evRaw: 0, evCap: 0
  };
  rules.forEach((f) => {
    let r = null;
    try { r = require(path.join(rdir, f)); } catch (e) { ok(f + ' loads (' + e.message + ')', false); return; }
    ok(f + ' exports a name and a pick()', !!r.name && typeof r.pick === 'function');
    /* A rule is asked once per side. If it answers with a side it was not asked about, the
       same bet gets graded twice - once as an over and once as an under. */
    let bad = null;
    ['over', 'under'].forEach((side) => {
      let v;
      try { v = r.pick(Object.assign({}, ctx, { side })); } catch (e) { bad = 'threw: ' + e.message; return; }
      if (v != null && v !== side) bad = 'asked for ' + side + ', answered ' + v;
    });
    ok(f + ' answers only the side it was asked about' + (bad ? ' - ' + bad : ''), bad === null);
    /* And it must cope with the data file being absent, which is the normal case for a sport
       that has no such build. */
    let threw = false;
    try { r.pick(Object.assign({}, ctx, { data: () => null })); } catch (e) { threw = true; }
    ok(f + ' survives its data file being missing', !threw);
  });

  /* IT STILL RUNS. A tool that cannot be invoked is worth nothing, and the rule and main-line
     paths are new enough to be worth actually executing rather than reasoning about. */
  const run = (args) => {
    try {
      return execFileSync('node', [TOOL].concat(args), { cwd: ROOT, encoding: 'utf8',
                                                         timeout: 600000, stdio: ['ignore', 'pipe', 'pipe'] });
    } catch (e) { return 'FAILED: ' + (e.message || ''); }
  };
  const outRule = run(['--rule', 'tools/rules/price-cap.js', '--sport', 'nba', '--main']);
  ok('it runs with a rule and a main-line filter', outRule.indexOf('FAILED:') < 0);
  ok('and says which rule it graded', /Rule "model picks under/.test(outRule));
  ok('and that it was main lines only', /main lines only/.test(outRule));
  ok('and warns that an interval spanning zero shows nothing',
     /has not been shown to do anything/.test(outRule));
  ok('and the rule footer names the slate unit too', /resampled by SLATE/.test(outRule));

  const outDefault = run(['--sport', 'nba']);
  ok('it still runs in its original mode', outDefault.indexOf('FAILED:') < 0);
  ok('reporting the edge threshold rather than a rule', /Edge threshold/.test(outDefault));
  ok('and explaining that intervals are resampled by slate, not by pick',
     /resampled by SLATE/.test(outDefault));
  /* The unit has to be named accurately. It is the fixture key - a day's slate for the sports
     with dated gamelogs, a round for NFL and AFL - not a single game, and calling it a game
     would overstate how fine the clustering is. */
  ok('and naming what a slate is rather than leaving it vague',
     /the day or round a price settled on/.test(outDefault));
  /* Either an interval or an explicit refusal to give one - never a bare ROI, which is the
     thing this change exists to stop. */
  const lines = outDefault.split('\n').filter(l => /% ROI/.test(l));
  const bare = lines.filter(l => !/90% /.test(l) && !/no interval/.test(l));
  const bare2 = lines.filter(l => !/90% /.test(l) && !/no interval/.test(l));
  ok('every ROI carries an interval or says why it cannot (' + lines.length + ' lines, '
     + bare2.length + ' bare)', lines.length > 0 && bare2.length === 0);
  ok('and a refusal says how many slates it had', !/no interval/.test(outDefault)
     || /only \d+ slates? - no interval/.test(outDefault));

  done();
})();
