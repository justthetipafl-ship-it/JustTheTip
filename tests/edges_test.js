/* WHERE THE MATCHUP LEANS, THE PROJECTED FIVE, AND THE TAB ROW
 *
 *   node tests/edges_test.js
 *
 * Three things added to the NBA fixture page together.
 *
 * EDGES reads zones.json and playtypes.json for one team's attack against the other's defence.
 * The promise it makes is the one worth testing: a lean is only drawn where the build measured
 * that a defence's gap to the league CARRIES, and at the size that carries. Three-point
 * percentage allowed does not carry (zone_persistence.json), so it must never appear as an edge
 * however big the gap is.
 *
 * THE PROJECTED FIVE fills a pane that said "No lineup on file" for every NBA team.
 *
 * THE TAB ROW is one scrolling line instead of a wrapped box.
 */
const { boot } = require('./harness');
const fs = require('fs');
const path = require('path');

let pass = 0, fail = 0;
function ok(what, cond, extra) {
  if (cond) { pass++; console.log('  ok   ' + what); }
  else { fail++; console.log('  FAIL ' + what + (extra ? ' ' + extra : '')); }
}
const text = (h) => String(h || '').replace(/<img(?:[^>"]|"[^"]*")*>/g, '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');

(async () => {
  const t = await boot('nba');
  await t.focus(0);
  const LAB = t.LAB, S = LAB.state.sports.nba, g = LAB.state.view.focus.g;
  const zf = S.data.zones, pf = S.data.playtypes, zp = S.data.zonePersist;
  ok('zones, shot actions and zone persistence are loaded', !!(zf && pf && zp && zp.keep));
  if (!zf || !pf || !zp) return done();

  /* ---------------- EDGES ---------------- */
  const html = LAB.edgesProbe('nba', g), tx = text(html);
  ok('the pane draws', html.length > 1500);
  ok('only for the NBA', LAB.edgesProbe('nfl', g) === '');
  const R = LAB.edRowsProbe('nba', g.away, g.home);
  ok('there are leans to show (' + R.edges.length + ') and others set aside (' + R.rest.length + ')',
     R.edges.length >= 2 && R.rest.length >= 2);

  const zEdges = R.edges.filter((r) => r.kind === 'zone'), aEdges = R.edges.filter((r) => r.kind === 'action');
  ok('no three-point zone is ever drawn as an edge',
     zEdges.every((r) => r.part !== 'cornerThree' && r.part !== 'armsThree'));
  ok('both three-point zones are in the set-aside list, with their numbers',
     ['cornerThree', 'armsThree'].every((z) => R.rest.some((r) => r.part === z && r.allowed != null)));
  ok('every zone edge is one whose gap has been shown to carry',
     zEdges.every((r) => (zp.keep[r.part] || 0) >= 0.25));
  const floor = pf.rankFloor != null ? pf.rankFloor : 0.5;
  ok('every shot-type edge clears the build\'s own noise floor',
     aEdges.every((r) => ((pf.spread[r.part] || {}).real || 0) >= floor));
  ok('and every shot type that does not is set aside',
     R.rest.filter((r) => r.kind === 'action').every((r) => ((pf.spread[r.part] || {}).real || 0) < floor));

  const all = R.edges.concat(R.rest);
  ok('the lean is the gap times the share that carries, never the raw gap',
     all.every((r) => Math.abs(r.carries - r.gap * r.keep) < 1e-9));
  ok('so no lean is bigger than the gap it came from', all.every((r) => Math.abs(r.carries) <= Math.abs(r.gap) + 1e-9));
  const rim = all.filter((r) => r.kind === 'zone' && r.part === 'rim')[0];
  const dKey = Object.keys(zf.defence).filter((k) => text(html).indexOf(k) >= 0 && zf.defence[k].rim.pct === rim.allowed)[0];
  ok('the rim figure is the defence\'s own from the file', !!dKey);
  ok('its gap is measured against the league (' + rim.gap.toFixed(2) + ')',
     Math.abs(rim.gap - (rim.allowed - zf.league.rim.pct) * 100) < 1e-9);
  const softer = Object.values(zf.defence).filter((d) => d.rim.pct > rim.allowed).length;
  ok('rank 1 allows the most (' + rim.rank.rank + ' of ' + rim.rank.of + ')', rim.rank.rank === softer + 1);
  ok('leans are ordered by how much of the offence they touch',
     R.edges.every((r, i) => !i || R.edges[i - 1].weight >= r.weight - 1e-12));

  /* WHO IT TOUCHES: on the attacking team, playing, and leaning on that spot more than the league. */
  const squad = {};
  (S.idx.players || []).forEach((p) => { squad[p.name] = p.team; });
  const touched = [].concat.apply([], R.edges.map((r) => r.touch.map((x) => ({ r, x }))));
  ok('players are named against the leans (' + touched.length + ')', touched.length >= 2);
  ok('each takes a bigger share there than the league does',
     touched.every((o) => o.x.share >= o.r.lgShare * 1.2 - 1e-9 && o.x.att >= 15));
  ok('at most three a row', R.edges.every((r) => r.touch.length <= 3));
  const inj = S.data.injury;
  if (touched.length) {
    const who = touched[0].x.name;
    S.data.injury = (inj || []).concat([{ Player: who, Team: squad[who], Status: 'Out', Injury: 'test' }]);
    delete S.idx.injIdx;
    const R2 = LAB.edRowsProbe('nba', g.away, g.home);
    ok('a player ruled out is not named (' + who + ')',
       R2.edges.every((r) => r.touch.every((x) => x.name !== who)));
    S.data.injury = inj; delete S.idx.injIdx;
  }
  ok('the note says three-point percentage allowed does not carry', /does not carry at all/.test(tx));
  ok('no grades and no instruction to bet', !/\bfade\b|who to back|\bgrade\b/i.test(tx.replace(/not who to back/, '').replace(/Post \/ fade/g, '')));
  ok('the team card offers the pane', LAB.cardPaneProbe('team', 'nba', g, 'edges').length > 1000);
  ok('no "club" and no "men" in the copy', !/\bclubs?\b|\bmen\b/i.test(tx));

  /* ---------------- THE PROJECTED FIVE ---------------- */
  [g.away, g.home].forEach((tm) => {
    const d = LAB.nbaFiveProbe('nba', tm, g);
    ok(tm + ': a lineup is drawn, and called projected', !!d && d.status === 'Projected' && /not a confirmed five/.test(d.sub));
    if (!d) return;
    const five = d.groups[0].players;
    ok(tm + ': five players', d.groups[0].name === 'Projected five' && five.length === 5);
    const outNames = {};
    (inj || []).forEach((r) => { if (/^out$/i.test(String(r.Status || r.status || ''))) outNames[r.Player || r.player] = 1; });
    ok(tm + ': nobody ruled out is in the five or the rotation',
       d.groups.slice(0, 2).every((gr) => gr.name === 'Regular starters out' || gr.players.every((p) => !outNames[p.name])));
    const miss = d.groups.filter((gr) => gr.name === 'Regular starters out')[0];
    ok(tm + ': regular starters who are out are listed, not dropped',
       !miss || miss.players.every((p) => p.flag && p.flag.out));
    const names = [].concat.apply([], d.groups.map((gr) => gr.players.map((p) => p.name)));
    ok(tm + ': nobody appears twice', new Set(names).size === names.length);
  });
  ok('the Available pane no longer says there is no lineup',
     text(LAB.cardPaneProbe('player', 'nba', g, 'avail')).indexOf('No lineup on file') < 0);

  /* ---------------- THE TAB ROW ---------------- */
  const page = fs.readFileSync(path.join(__dirname, process.env.JTT_PAGE || 'index.html'), 'utf8');
  const css = (page.match(/\.bld-tabs\{[^}]*\}/) || [''])[0];
  ok('the tab row is one line that scrolls, not a wrapped box',
     /flex-wrap:nowrap/.test(css) && /overflow-x:auto/.test(css));
  ok('the tab in use is underlined', /\.bld-tab\.on\{[^}]*border-bottom-color/.test(page));
  ok('tabs are not shouted in capitals any more',
     /\.bld-tab\{[^}]*text-transform:none/.test(page));
  ok('the pick\'em switch inside a pane keeps its own compact look',
     /\.pk-tabs \.bld-tab\{[^}]*text-transform:uppercase/.test(page));

  done();
  function done() {
    console.log('\n' + (fail ? 'FAILED ' : 'ALL GREEN ') + pass + '/' + (pass + fail));
    process.exit(fail ? 1 : 0);
  }
})();
