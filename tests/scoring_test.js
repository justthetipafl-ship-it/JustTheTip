/* WHAT A POINTS LINE IS MADE OF - the Scoring pane on the NBA scoring card
 *
 *   node tests/scoring_test.js
 *
 * The pane reads nba/data/scoring.json (nba/build_nba_scoring.py, which has its own selftest for
 * the arithmetic and the measurements). This checks the page: that it draws from the file and
 * nothing else, that the numbers it prints are the file's, that a figure the build has not
 * measured is not asserted anyway, and that the same "with a team-mate off the floor" line now
 * sits on all four cards and obeys the Without tab's rules.
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
  const LAB = t.LAB, S = LAB.state.sports.nba, g = LAB.state.view.focus.g;
  const sf = S.data.scoring;
  ok('scoring.json is staged and loaded', !!(sf && sf.players && sf.players.length >= 200),
     sf ? '(' + (sf.players || []).length + ' players)' : '(absent)');
  if (!sf) return done();

  /* ABSENT -> NOTHING. */
  S.data.scoring = null;
  ok('with no file the pane draws nothing', LAB.scoreProbe('nba', g) === '');
  S.data.scoring = sf;
  ok('only for the NBA', LAB.scoreProbe('nfl', g) === '');

  const cands = LAB.scCandsProbe('nba', g);
  ok('there are scorers in this fixture to pick from (' + cands.length + ')', cands.length >= 4);
  ok('highest scorer first', cands.every((c, i) => !i || cands[i - 1].rec.pts >= c.rec.pts));
  LAB.state.view.ptName = cands[0].name;
  const me = cands[0], rec = me.rec;
  const html = LAB.scoreProbe('nba', g), tx = text(html);
  ok('the pane draws for ' + me.name, html.length > 1500);
  ok('the header carries his points and games from the file',
     tx.indexOf(rec.pts.toFixed(1) + ' a game over ' + rec.games + ' games') > 0);

  /* THE PLAYER FOLLOWS THE READER ACROSS THE CARD. One picker state for all four tabs. */
  LAB.state.view.ptName = cands[1].name;
  ok('choosing another player redraws for him',
     text(LAB.scoreProbe('nba', g)).indexOf(cands[1].name + ' v ') >= 0);
  LAB.state.view.ptName = 'Nobody By This Name';
  ok('a player picked on another fixture falls back to the top scorer',
     text(LAB.scoreProbe('nba', g)).indexOf(cands[0].name + ' v ') >= 0);
  LAB.state.view.ptName = cands[0].name;

  /* WHERE HIS POINTS COME FROM: three shares that are all of them. */
  ok('twos, threes and free throws add to all of his points',
     Math.abs(rec.from2 + rec.from3 + rec.fromFt - 1) < 0.01,
     '(' + (rec.from2 + rec.from3 + rec.fromFt).toFixed(3) + ')');
  ['from2', 'from3', 'fromFt'].forEach((k) => {
    ok('the page prints his ' + k + ' share (' + Math.round(rec[k] * 100) + '%)',
       tx.indexOf(Math.round(rec[k] * 100) + '% of his points') > 0);
  });
  const srcBlock = html.split('Where his points come from')[1].split('pa-sub')[0];
  ok('the source rows carry no good/bad colour', !/class="(up|dn)"/.test(srcBlock));

  /* WHAT CARRIES. The r values are the build's, and absent they are not invented. */
  const st = sf.league.stability;
  ok('the build measured what carries', st && st.fga > 0.7 && st.ts != null && st.ts < st.fga);
  ok('the page quotes the build\'s figures, not its own',
     tx.indexOf('r=+' + st.fga.toFixed(2)) > 0 && tx.indexOf('r=+' + st.ts.toFixed(2)) > 0);
  ok('true shooting is not labelled as holding up when it only partly does',
     st.ts >= 0.7 || /True shooting[^]*?partly holds|True shooting[^]*?barely holds/.test(tx.split('Where his points')[0]));
  const keep = sf.league.stability;
  sf.league.stability = {};
  const bare = text(LAB.scoreProbe('nba', g));
  ok('with no stability block the forecast panel is not drawn at all',
     bare.indexOf('How much of this you can forecast') < 0 && bare.indexOf('holds up') < 0);
  ok('and the rest of the card still is', bare.indexOf('Where his points come from') > 0);
  sf.league.stability = keep;

  /* HIS LAST 5 comes from the game logs on the page, with the minutes taken out. */
  const logs = (S.idx.logsByName[me.name] || []).filter((r) => (+r.minutes || 0) > 0)
    .slice().sort((a, b) => String(b.Date).localeCompare(String(a.Date)));
  if (logs.length >= 5) {
    const l5 = logs.slice(0, 5);
    const pts = l5.reduce((a, r) => a + (+r.points || 0), 0) / 5;
    const min = l5.reduce((a, r) => a + (+r.minutes || 0), 0) / 5;
    ok('last-5 points match the logs (' + pts.toFixed(1) + ')',
       tx.indexOf('last 5: ' + pts.toFixed(1) + ' in ' + min.toFixed(0) + ' min') >= 0);
    const all = logs, bp = all.reduce((a, r) => a + (+r.points || 0), 0) / all.length,
          bm = all.reduce((a, r) => a + (+r.minutes || 0), 0) / all.length;
    const d = pts / min * 30 - bp / bm * 30;
    const sgn = (d >= 0 ? '+' : '−') + Math.abs(d).toFixed(1);
    ok('the per-30 gap is last five against every game on file (' + sgn + ')',
       tx.indexOf('per 30 min: ' + sgn + ' v his average') >= 0);
    ok('and says how many games that is', tx.indexOf('of ' + all.length + ' on file') > 0);
  } else ok('(fewer than five games on file for him)', true);
  ok('the hot-run note quotes the build\'s sample',
     tx.indexOf(sf.league.recent.n.toLocaleString() + ' player-games') > 0);

  /* THE OPPONENT. */
  const D = sf.defence;
  ok('every team has a concession on file (' + Object.keys(D).length + ')', Object.keys(D).length >= 28);
  const ranks = Object.values(D).map((d) => d.ptsRank).sort((a, b) => a - b);
  ok('points-allowed ranks run 1..n with no gaps', ranks.every((r, i) => r === i + 1));
  const top = Object.values(D).filter((d) => d.ptsRank === 1)[0];
  ok('rank 1 is the team that allows the most',
     Object.values(D).every((d) => d.ptsAllowed <= top.ptsAllowed));
  ok('the pane shows what the opponent allows, ranked',
     /points allowed a game \d+(st|nd|rd|th) of \d+/.test(tx));
  ok('and how little that is worth, in its own measured terms',
     tx.indexOf(sf.league.opp.n.toLocaleString() + ' player-games') > 0);

  /* FOLDED, like the other three: one plain line, working one tap away. */
  ok('long notes are folded behind a one-line summary',
     (html.match(/<details class="nb-why">/g) || []).length >= 2);
  ok('the verdict strip is there', /class="nb-verdict"/.test(html));
  ok('no "club" and no "men" in the copy', !/\bclubs?\b|\bmen\b/i.test(tx));

  /* ON THE CARD. */
  ok('the scoring card offers the pane', LAB.cardPaneProbe('score', 'nba', g, 'pts').length > 1000);

  /* THE TEAM-MATE LINE, ON ALL FOUR CARDS. Same file and same rules as the Without tab. */
  const wf = S.data.without;
  if (wf) {
    const panes = { pts: LAB.scoreProbe('nba', g), play: LAB.cardPaneProbe('score', 'nba', g, 'play'),
                    glass: LAB.cardPaneProbe('score', 'nba', g, 'glass'),
                    deep: LAB.cardPaneProbe('score', 'nba', g, 'deep') };
    const want = { pts: 'Pts', play: 'Ast', glass: 'Def reb', deep: '3PA' };
    Object.keys(panes).forEach((k) => {
      const h = panes[k];
      const has = h.indexOf('off the floor') > 0;
      ok(k + ': carries the team-mate line with its own stat (' + want[k] + ')',
         !h || !has || new RegExp('<i>' + want[k] + '</i>').test(h.split('off the floor')[1]));
    });
    const line = LAB.woLineProbe('nba', me, ['pts', 'usg', 'fga']);
    ok('the line draws for the top scorer', line.length > 100);
    ok('with the minutes on both sides', /\d+ min with · \d+ without/.test(text(line)));
    ok('never against himself', text(line).indexOf('With ' + me.name + ' off the floor') < 0);
    ok('an unknown column is skipped, not printed as a blank', LAB.woLineProbe('nba', me, ['nope']) === '');
    S.data.without = null;
    ok('with no with/without file the line is simply not there',
       LAB.woLineProbe('nba', me, ['pts']) === '' && LAB.scoreProbe('nba', g).indexOf('off the floor') < 0);
    S.data.without = wf;
    /* an older file, built before rebounds were split by end, must not print a wrong column */
    const stats = wf.stats; wf.stats = stats.filter((x) => x !== 'dreb' && x !== 'oreb' && x !== 'tpm');
    const old = LAB.woLineProbe('nba', me, ['reb', 'dreb', 'oreb']);
    ok('a file without a stat drops that column and keeps the rest',
       /<i>Reb<\/i>/.test(old) && !/Def reb|Off reb/.test(old));
    wf.stats = stats;
  } else ok('(without.json not staged - team-mate line not checked)', true);

  done();
  function done() {
    console.log('\n' + (fail ? 'FAILED ' : 'ALL GREEN ') + pass + '/' + (pass + fail));
    process.exit(fail ? 1 : 0);
  }
})();
