/* THE STATS BOARD, BY POSITION
 *
 *   node tests/stats_test.js
 *
 * The scanner used to show one set of eight columns to everybody, taken off the front of the
 * sport's market list. A quarterback and a cornerback both got Receptions, Targets and Rec Yds.
 * Meanwhile the three numbers that actually describe a quarterback's game - completion percentage
 * over expected, time to throw, how often he throws into tight coverage - were sitting in the
 * gamelogs, carried on 87-89% of his games, and had never been on the page.
 *
 * So the first thing these check is that each position gets columns that MEAN something for it,
 * and that the cells are mostly full rather than a grid of dashes - a column that never has a
 * number in it is worse than no column, because it reads as missing data rather than a bad choice.
 *
 * The second thing is the join. profiles.json, redzone.json and firstread.json are season-to-date
 * files that key on a player name, and among the top 25 at each position they cover 88-96% of
 * them. They have no per-game form, so they cannot move with the window chips - and a target share
 * silently read as "his last five" would be a wrong number nobody could see was wrong. Those
 * columns are marked, and the mark is asserted here.
 */
const { JSDOM } = require('./harness').JSDOM || require('jsdom');
const { boot, ok, done } = require('./harness');

(async () => {
  const t = await boot('nfl');
  await t.focus(0);
  const LAB = t.LAB;
  if (!LAB.stScanProbe || !LAB.stTeamProbe) {
    ok('the page exposes the stats board for testing', false);
    return done();
  }
  ok('the page exposes the stats board for testing', true);

  const doc = h => new JSDOM('<div>' + h + '</div>').window.document;
  const read = (pos, win) => {
    const p = LAB.stScanProbe('nfl', pos, win);
    const d = doc(p.html);
    const head = Array.from(d.querySelectorAll('.st-t thead th')).map(e => e.textContent.trim());
    const rows = Array.from(d.querySelectorAll('.st-t tbody tr'));
    return { p, head, rows, d };
  };

  // ---- each position gets its own columns -------------------------------------------------------
  const qb = read('QB'), rb = read('RB'), wr = read('WR'), te = read('TE'), db = read('DB');
  ok('a quarterback and a cornerback no longer share a column set',
     qb.head.join() !== db.head.join(),
     '(QB ' + qb.p.cols.length + ' cols, DB ' + db.p.cols.length + ')');
  ok('the quarterback board carries the passing game',
     ['PaYd', 'Att', 'Cmp', 'PaTD', 'INT'].every(l => qb.head.some(h => h.indexOf(l) === 0)));

  /* The find that started this: three advanced quarterback fields that were already in every
     gamelog and had never been shown. */
  ['CPOE', 'TTT', 'AGG'].forEach(l =>
    ok('the quarterback board shows ' + l, qb.head.some(h => h.indexOf(l) === 0),
       l === 'CPOE' ? '(completion % over expected - in the logs all along)' : ''));

  ok('a cornerback is not asked about receptions',
     !db.head.some(h => /^(Rec|Tgt|ReYd|PaYd)/.test(h)),
     '(' + db.head.slice(2).join(' ') + ')');
  ok('and gets the defensive counting stats instead',
     ['Tk+A', 'Solo', 'Ast', 'TFL', 'Sack'].every(l => db.head.some(h => h.indexOf(l) === 0)));
  ok('the running back board leads with the run',
     rb.head[2].indexOf('RuYd') === 0 && rb.head.some(h => h.indexOf('YPC') === 0));
  ok('the receiver board leads with the catch',
     wr.head[2].indexOf('Rec') === 0 && wr.head.some(h => h.indexOf('aDOT') === 0));
  ok('a tight end reads like a receiver', te.head.join() === wr.head.join(),
     '(the difference between them is route mix, which no free feed publishes)');

  // ---- and the cells are actually full ----------------------------------------------------------
  const fill = r => {
    let cells = 0, dash = 0;
    r.rows.slice(0, 20).forEach(tr => Array.from(tr.querySelectorAll('td')).slice(2).forEach(td => {
      cells++; if (td.textContent.trim() === '—') dash++;
    }));
    return cells ? 1 - dash / cells : 0;
  };
  [['QB', qb], ['RB', rb], ['WR', wr], ['TE', te], ['DB', db]].forEach(function(x){
    const f = fill(x[1]);
    ok('the ' + x[0] + ' board is mostly numbers, not dashes', f >= 0.8,
       '(' + Math.round(f * 100) + '% of cells filled across the top 20)');
  });

  // ---- the season columns are marked, and do not move with the window ---------------------------
  const seasonCols = wr.p.cols.filter(c => c.kind === 's');
  ok('the receiver board joins the season-level files',
     seasonCols.length >= 5, '(' + seasonCols.map(c => c.label).join(', ') + ')');
  ok('and they are marked in the header', /st-sdot/.test(wr.p.html),
     '(a target share must not be read as "his last five")');
  ok('and the table says so underneath', /do not move with the window/.test(wr.p.html));
  ok('a position with no season columns says nothing about them',
     !/do not move with the window/.test(db.p.html), '(the defensive board has none)');

  /* The assertion that the marker is honest: change the window and the gamelog columns move while
     the season ones stay put. */
  const season = read('WR', 'season'), l5 = read('WR', 'l5');
  // follow one named man across both windows rather than whoever happens to be top, which is a
  // different player once the window changes
  const cellFor = (r, who, label) => {
    const i = r.head.findIndex(h => h.indexOf(label) === 0);
    const tr = r.rows.find(x => (x.querySelector('td b') || {}).textContent === who);
    return (i >= 0 && tr) ? tr.querySelectorAll('td')[i].textContent.trim() : null;
  };
  const who = (season.rows[0].querySelector('td b') || {}).textContent;
  const sAdot = cellFor(season, who, 'aDOT'), lAdot = cellFor(l5, who, 'aDOT');
  const sRec = cellFor(season, who, 'Rec'), lRec = cellFor(l5, who, 'Rec');
  ok('a season column reads the same whichever window is chosen',
     who && sAdot != null && sAdot === lAdot,
     '(' + who + ' aDOT ' + sAdot + ' on both)');
  ok('while the gamelog columns beside it do move',
     sRec !== lRec || season.rows.length !== l5.rows.length,
     '(' + who + ' Rec ' + sRec + ' season vs ' + lRec + ' over 5)');

  // ---- the join itself ---------------------------------------------------------------------------
  ok('the supporting files are indexed by player name', qb.p.advN > 500,
     '(' + qb.p.advN + ' players with at least one supporting row)');

  // ---- a sort left over from another position cannot blank the board -----------------------------
  LAB.state.view.stSort = 'cpoe';            // a QB column
  const afterSwitch = read('DB');             // ...viewed on the defensive board
  ok('switching position drops a sort the new columns do not have',
     afterSwitch.rows.length > 0 && /▾/.test(afterSwitch.p.html),
     '(otherwise every row sorts equal and the order is meaningless)');
  LAB.state.view.stSort = null;

  // ---- other sports are untouched -----------------------------------------------------------------
  const epl = LAB.stScanProbe('epl', '', 'season');
  ok('a sport with no position sets keeps the board it had',
     epl.cols.length > 0 && epl.cols.every(c => c.kind === 'g'),
     '(' + epl.cols.map(c => c.label).join(' ') + ')');

  // ---- the teams board ----------------------------------------------------------------------------
  const tFor = doc(LAB.stTeamProbe('nfl', false).html);
  const tAll = doc(LAB.stTeamProbe('nfl', true).html);
  const headOf = d => Array.from(d.querySelectorAll('.st-t thead th')).map(e => e.textContent.trim());
  const hFor = headOf(tFor), hAll = headOf(tAll);
  ok('the teams board gains yards per play and completion rate',
     ['Y/P', 'Cmp%'].every(l => hFor.some(h => h.indexOf(l) === 0)),
     '(both were arithmetic on columns already there)');
  ok('and pass rate over expected', hFor.some(h => h.indexOf('PROE') === 0));
  /* How often a defence plays man is a fact about that defence. On the For view it would be
     describing the wrong team. */
  ok('coverage rates appear only on the Allowed side',
     ['Man', 'Zone', 'Deep'].every(l => hAll.some(h => h.indexOf(l) === 0))
     && !hFor.some(h => h.indexOf('Man') === 0),
     '(man rate is a fact about the defence, not the offence)');
  const teamRows = tAll.querySelectorAll('.st-t tbody tr');
  ok('every team is still listed on the allowed side', teamRows.length === 32,
     '(' + teamRows.length + ')');
  let tc = 0, td_ = 0;
  Array.from(teamRows).forEach(tr => Array.from(tr.querySelectorAll('td')).slice(2).forEach(c => {
    tc++; if (c.textContent.trim() === '—') td_++;
  }));
  ok('and every cell on it has a number', td_ === 0, '(' + td_ + ' dashes of ' + tc + ')');

  done();
})();
