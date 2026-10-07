/* NBA: everything added for the new sport, none of which had a test.
 *   - the sport loads at all
 *   - the Scoring tab is reachable (it was hard-restricted to NFL, so the profile was unreachable)
 *   - quarters and zones load, and their numbers are sane against known basketball
 *   - the signal band produces double and triple doubles
 */
const { boot, ok, done } = require('./harness');

(async () => {
  const t = await boot('nba');
  await t.focus(0);
  const LAB = t.LAB, S = t.S;

  ok('nba loads players and fixtures',
     (S.data.players || []).length > 300 && (S.data.fixture || []).length > 500,
     '(' + (S.data.players || []).length + ' players, ' + (S.data.fixture || []).length + ' fixtures)');

  // --- shot zones ------------------------------------------------------------------------------
  // The league figures are the check that matters: the hoop origin was wrong once and every zone
  // was quietly misclassified while still looking plausible.
  const z = S.data.zones;
  ok('zones load', !!(z && z.players && z.players.length), z && z.players ? '(' + z.players.length + ')' : '(absent)');
  if (z && z.league) {
    const rim = z.league.rim && z.league.rim.pct;
    const mid = z.league.midRange && z.league.midRange.pct;
    const corner = z.league.cornerThree && z.league.cornerThree.pct;
    ok('league shoots ~65% at the rim', rim > 0.60 && rim < 0.72, '(' + Math.round(rim * 100) + '%)');
    ok('mid-range is the worst shot', mid < rim && mid < corner, '(' + Math.round(mid * 100) + '%)');
    ok('corner threes beat above the break',
       corner > (z.league.armsThree && z.league.armsThree.pct),
       '(' + Math.round(corner * 100) + '% v ' + Math.round(z.league.armsThree.pct * 100) + '%)');
  }

  // --- quarters --------------------------------------------------------------------------------
  const q = S.data.quarters;
  ok('quarters load', !!(q && q.players && q.players.length), q && q.players ? '(' + q.players.length + ' rows)' : '(absent)');
  if (q && q.players) {
    // Q1-Q4 must sum to a little UNDER the season average: overtime is excluded on purpose. A sum
    // ABOVE it means the denominator is wrong, which is exactly the bug the first version had.
    const byName = {};
    q.players.forEach(r => { byName[r.name] = (byName[r.name] || 0) + (r.points || 0); });
    const players = (S.data.players || []).filter(p => p.games >= 40 && p.points > 15);
    let over = 0, checked = 0;
    players.forEach(p => {
      const sum = byName[p.name];
      if (sum == null) return;
      checked++;
      if (sum > p.points * 1.02) over++;
    });
    ok('no player sums above his season average', over === 0 && checked > 20,
       '(' + checked + ' checked, ' + over + ' over)');
  }

  // --- the Scoring tab -------------------------------------------------------------------------
  const name = (S.data.players || []).slice().sort((a, b) => b.points - a.points)[0].name;
  LAB.player('nba', name);
  await t.wait(600);
  const html0 = t.app().innerHTML;
  ok('the player page offers a Scoring tab', /Scoring/.test(html0), '(' + name + ')');
  LAB.ppTab('profile');
  await t.wait(600);
  const html = t.app().innerHTML;
  ok('the scoring profile renders a shooting line', /True shooting/.test(html));
  ok('and the zone split', /Where he shoots from/.test(html));

  done();
})().catch(e => { console.log('  FAIL harness threw: ' + e.message.slice(0, 160)); process.exit(1); });
