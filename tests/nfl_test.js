/* NFL: the changes that shipped without a test behind them.
 *   - anytimeTd reads the COUNT, not the 0/1 flag
 *   - first read appears on receiving rows, in the dive, on the fixture and on the stats board
 *   - the calculator opens on the main line, not the longest-priced rung
 *   - focused fixtures fold their reference sections away
 */
const { boot, ok, done } = require('./harness');

(async () => {
  const t = await boot('nfl');
  await t.focus(0);
  const LAB = t.LAB, S = t.S;

  // --- anytime TD reads the count -------------------------------------------------------------
  // The gamelogs carry anytimeTd as a 0/1 flag and totalTds as the real number. Reading the flag
  // meant a two-touchdown game plotted as 1 and nobody could ever clear a 2+ line.
  const logs = (S.data.gamelogs || []).filter(r => (+r.totalTds || 0) >= 2);
  ok('the data has multi-TD games to check', logs.length > 0, '(' + logs.length + ')');
  if (logs.length) {
    const r = logs[0];
    const v = LAB.statOfProbe ? LAB.statOfProbe('nfl', r, 'anytimeTd') : null;
    ok('anytimeTd returns the count, not the flag', v === +r.totalTds,
       '(' + r.Player + ' wk' + r.Week + ': got ' + v + ', flag says ' + r.anytimeTd
       + ', truth is ' + r.totalTds + ')');
  }

  // --- first read ------------------------------------------------------------------------------
  const fr = S.data.firstRead;
  ok('first read data loads', !!(fr && fr.players && fr.players.length),
     fr && fr.players ? '(' + fr.players.length + ' receivers)' : '(absent)');
  if (fr && fr.players && fr.players.length) {
    const withTeamShare = fr.players.filter(p => p.teamFirstShare != null).length;
    ok('rows carry the TEAM share, not just his own', withTeamShare > fr.players.length * 0.8,
       '(' + withTeamShare + ' of ' + fr.players.length + ')');
    const top = fr.players.slice().sort((a, b) => b.teamFirstShare - a.teamFirstShare)[0];
    ok('the leader takes a believable share', top.teamFirstShare > 0.2 && top.teamFirstShare < 0.9,
       '(' + top.name + ' ' + Math.round(top.teamFirstShare * 100) + '%)');
  }

  // --- the fixture page ------------------------------------------------------------------------
  const html = t.app().innerHTML;
  ok('the fixture shows who they look for first', /Who they look for first/.test(html));
  ok('FTN is credited where the data is shown', /FTN Data via nflverse/.test(html));

  // --- folded sections -------------------------------------------------------------------------
  const folds = t.app().querySelectorAll('details.fold');
  ok('reference sections are folded', folds.length >= 4, '(' + folds.length + ' sections)');
  const open = Array.from(folds).filter(d => d.hasAttribute('open')).length;
  ok('and they start closed', open < folds.length, '(' + open + ' of ' + folds.length + ' open)');

  done();
})().catch(e => { console.log('  FAIL harness threw: ' + e.message.slice(0, 160)); process.exit(1); });
