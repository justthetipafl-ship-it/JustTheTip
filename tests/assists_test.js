/* PLAYMAKING MUST NOT CLAIM THE READ IT WAS BUILT FOR
 *
 *   node tests/assists_test.js
 *
 * This section started from an obvious premise: a fifth of a man's passing goes to one team-mate,
 * so when that team-mate sits the assists line should come down. Measured across 189 passers in
 * the 2026 season, splitting each man's games by whether his busiest target played, it does not -
 * the mean change is +0.00, the median +0.05, and only 87 of 189 went down. A rank-four target
 * sitting moved it slightly MORE. Aaron Gordon averages 1.96 more assists without Jokic, because
 * losing a target hands the passer the ball and the minutes.
 *
 * So the card shows the network as context and the measured null is on the page. These checks
 * hold the page to that: they assert the disclaimer is there and that the opponent column - the
 * one strand that survives out of sample, worth 0.65 of an assist between the softest and
 * toughest third - is the one carrying a ranking.
 *
 * They also hold the two bugs that nearly shipped. A target with no assists yet this season must
 * NOT be called departed (in October almost nobody has any), and a traded team-mate must be
 * marked from the roster rather than silently dropped.
 */
const { boot, ok, done } = require('./harness');

(async () => {
  const t = await boot('nba');
  await t.focus(0);
  const LAB = t.LAB;

  if (!LAB.playProbe) { ok('the page exposes Playmaking for testing', false); return done(); }
  ok('the page exposes Playmaking for testing', true);

  const S = LAB.state.sports.nba;
  const af = S.data.assists;
  ok('assists.json is loaded', !!af && !!af.players);
  if (!af || !af.players) return done();
  ok('with a usable pool of passers (' + af.players.length + ')', af.players.length >= 100);
  ok('and a concession table for the clubs (' + Object.keys(af.defence || {}).length + ')',
     Object.keys(af.defence || {}).length >= 25);

  /* THE DATA ITSELF. These are the properties the card reads; if one breaks the card shows
     something plausible and wrong rather than failing. */
  const bad = [];
  af.players.forEach((p) => {
    const sum = (p.targets || []).reduce((a, x) => a + x.share, 0);
    if (sum > 1.0001) bad.push(p.name + ' shares sum to ' + sum.toFixed(3));
    if ((p.targets || []).some((x) => x.id === p.playerId)) bad.push(p.name + ' assists himself');
    if ((p.targets || []).some((x, i) => i && p.targets[i - 1].n < x.n))
      bad.push(p.name + ' targets out of order');
    if ((p.targets || []).some((x) => x.nCur > x.n)) bad.push(p.name + ' has nCur above n');
    if (p.assistsCur > p.assists) bad.push(p.name + ' has more this season than in total');
  });
  ok('every network is internally consistent' + (bad.length ? ' - ' + bad.slice(0, 3).join('; ') : ''),
     bad.length === 0);

  /* THE CLUB LABELS. team_id arrives from the parquet as a float against an int home_team_id, so
     comparing them as strings matched NONE of 73,821 rows and credited every bucket to the away
     side - Jalen Brunson came out at PHI. The roster is the check: it must now broadly agree. */
  const rost = {};
  (S.data.players || []).forEach((p) => {
    if (p && p.playerId != null) rost[String(p.playerId).replace(/\.0+$/, '')] = p;
  });
  let agree = 0, dis = 0;
  af.players.forEach((p) => {
    const r = rost[p.playerId];
    if (!r || !r.team) return;
    if (r.team === p.team) agree++; else dis++;
  });
  ok('clubs agree with the roster for the overwhelming majority ('
     + agree + ' v ' + dis + ')', agree + dis > 100 && agree / (agree + dis) > 0.85);

  /* A STALE TARGET IS NOT A DEPARTED ONE. In October most targets carry nothing this season, so
     if the page used nCur to decide who had left it would mark nearly everyone as gone. */
  const zeroCur = af.players.reduce((a, p) =>
    a + (p.targets || []).filter((x) => !x.nCur).length, 0);
  const allT = af.players.reduce((a, p) => a + (p.targets || []).length, 0);
  ok('most targets have no assists yet this season (' + zeroCur + '/' + allT
     + ') - so nCur cannot be the departure test', allT > 0 && zeroCur / allT > 0.3);

  const fx = (S.data.fixture || [])[0];
  ok('there is a fixture to render', !!fx);
  if (!fx) return done();

  const cands = LAB.paCandsProbe('nba', fx);
  ok('the picker offers the men in this game (' + cands.length + ')', cands.length >= 4);
  ok('busiest passer first',
     cands.every((c, i) => i === 0 || cands[i - 1].rec.assists >= c.rec.assists));
  ok('and only men named for one of the two clubs',
     cands.every((c) => c.team === fx.home || c.team === fx.away));

  /* paStatus decides each target's standing, and it must do it by ROSTER. Pick a target whose
     roster club differs from the passer's and require the page to say so rather than drop him. */
  const me = cands[0];
  let moved = null, mate = null;
  cands.forEach((c) => {
    (c.rec.targets || []).forEach((x) => {
      const r = rost[x.id];
      if (!r || !r.team) return;
      if (r.team !== c.team && !moved) moved = { me: c, t: x, to: r.team };
      if (r.team === c.team && !mate) mate = { me: c, t: x };
    });
  });
  if (moved) {
    const st = LAB.paStatusProbe('nba', moved.me, moved.t);
    ok('a target now at another club is marked, not dropped ('
       + moved.t.name + ' -> ' + moved.to + ': "' + st.txt + '")',
       st.mate === false && /now at/.test(st.txt));
  } else {
    ok('a traded target was found to check', false, '(none in this fixture)');
  }
  if (mate) {
    const st = LAB.paStatusProbe('nba', mate.me, mate.t);
    ok('a current team-mate reads as one (' + mate.t.name + ': "' + st.txt + '")', st.mate === true);
  }
  ok('a target who is on no roster at all says so rather than reading as a team-mate',
     (() => { const st = LAB.paStatusProbe('nba', me, { id: '00000000', name: 'Nobody' });
              return st.mate === false && /not on a roster/.test(st.txt); })());

  /* THE RENDERED PANE. */
  const html = LAB.playProbe('nba', fx);
  ok('the section renders', !!html && html.length > 400);
  if (!html) return done();
  const txt = html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');

  ok('it names the passer and the opponent', txt.indexOf(me.name) >= 0);
  ok('it shows who he feeds', /Who he feeds/.test(txt));
  /* DEPARTED TARGETS ARE NOT LISTED. They used to be, on the reasoning that dropping them
     would stop the shares adding up against his real total - but a player at another team
     cannot be fed tonight, and on a passer who has changed clubs half the list was men who
     had left, pushing the men who are there off the top three. They come out, the remaining
     shares are renormalised over the squad, and the share that walked out the door is stated
     in one line underneath rather than as four rows.

     The line matters: without it the card would silently imply his network is intact. So this
     checks BOTH halves - the names are gone AND the share is declared. */
  const gone = (me.rec.targets || []).filter((x) => !LAB.paStatusProbe('nba', me, x).mate);
  const here = (me.rec.targets || []).filter((x) => LAB.paStatusProbe('nba', me, x).mate);
  if (gone.length) {
    ok('with the concentration measured over the players still alongside him',
       /of the players still alongside him, three take \d+%/.test(txt));
    ok('and the departed share declared rather than hidden',
       /% of his passing went to \d+ players? no longer alongside him/.test(txt));
    /* The footnote used to say they were "kept here rather than dropped". They are dropped
       now, and a line describing the old behaviour would be worse than no line. */
    ok('and the note describes what the card actually does',
       !/kept here rather than dropped/.test(txt) && /not listed/.test(txt));
    ok('no departed target is listed as someone he feeds ('
       + gone.map((x) => x.name).join(', ') + ')',
       gone.every((x) => txt.indexOf(x.name) < 0));
    /* The renormalisation is the point. If the shares were left alone the printed percentages
       would sum to well under 100 and quietly understate every man still there. */
    const pct = (html.match(/<b>(\d+)%<\/b>of the passing he still has/g) || [])
      .map((m) => +(m.match(/\d+/) || [0])[0]);
    ok('and the shares are renormalised over the squad (' + pct.join('+') + ')',
       pct.length > 0 && pct.reduce((a, b) => a + b, 0) >= 60);
    ok('the top one is bigger than it was before the departed were removed',
       pct.length > 0 && pct[0] > Math.round(here[0].share * 100));
  } else {
    ok('with the concentration spelled out', /top three take \d+% of his passing/.test(txt));
  }
  ok('it shows what he creates', /What he creates/.test(txt));
  ok('including his three-share against the league', /of his assists/.test(txt) && /v league/.test(txt));
  ok('it breaks the assists down by quarter', /By quarter/.test(txt));
  ok('and it shows what the opponent concedes', /assists conceded a game/.test(txt));

  /* WHY THEY CONCEDE IT. An assist needs a made shot, so the figure factors exactly into the
     baskets allowed and the share of them that were created. Both are worth seeing and NEITHER
     is worth pricing: each looks like its own edge out of sample (+0.093 and +0.084 against the
     concession's +0.102) but they overlap it at +0.85 and +0.88, and residualised they add
     +0.011 and -0.013. The card has to show them as the reason and say they are not extra. */
  const dOpp = (rf0 => rf0)(af.defence[LAB.normTeamProbe ? LAB.normTeamProbe(fx.away) : fx.away]
    || af.defence[fx.away]);
  ok('the file carries the breakdown', !!dOpp && dOpp.fgmAllowed != null && dOpp.astShare != null);
  if (dOpp) {
    ok('and it is an identity, not an estimate ('
       + dOpp.fgmAllowed + ' x ' + dOpp.astShare + ' = '
       + (dOpp.fgmAllowed * dOpp.astShare).toFixed(2) + ' v ' + dOpp.perGame + ')',
       Math.abs(dOpp.fgmAllowed * dOpp.astShare - dOpp.perGame) < 0.01);
    ok('both halves are ranked', dOpp.fgmRank >= 1 && dOpp.shareRank >= 1);
  }
  ok('the card shows the baskets allowed', /baskets allowed a game/.test(txt));
  ok('and how many of them were created for the shooter',
     /created for the shooter/.test(txt));
  ok('it says they multiply out rather than add',
     /multiply out to the figure above/.test(txt));
  ok('and that neither half is a second number to bet into',
     /not\s+a second number to bet into/.test(txt));
  /* Every club must carry it, not just the one on screen. */
  const missing = Object.keys(af.defence).filter((c) => af.defence[c].astShare == null);
  ok('every club has the breakdown (' + missing.length + ' missing)', missing.length === 0);
  const offBy = Object.keys(af.defence).filter((c) => {
    const v = af.defence[c];
    return Math.abs(v.fgmAllowed * v.astShare - v.perGame) > 0.01;
  });
  ok('and it adds up for all of them (' + offBy.length + ' off)', offBy.length === 0);

  /* THE HONESTY OF IT. This is the point of the whole section: the network is context, and the
     page must say the absence read was measured and failed rather than implying an edge. */
  ok('the page states the network is not a reason on its own',
     /context, not a reason on its own/.test(txt));
  ok('and gives the measured null rather than just hedging',
     /\+0\.00 on average/.test(txt) && /46%/.test(txt));
  ok('it names the opponent column as the part that holds up',
     /opponent column is the part that holds up/.test(txt));
  ok('and says the swing is the out-of-sample figure', /0\.65 of an assist/.test(txt));
  ok('it states the limit that an assist is only recorded on a make',
     /only on a shot that WENT IN/.test(txt));
  ok('and that the window is two seasons', /window is two seasons/.test(txt));

  /* THE SHARED PICKER. Switching between How He Scores and Playmaking must not change who you
     are looking at - two pickers in one card that disagree is the complaint that got the chips
     replaced in the first place. */
  const other = cands.filter((c) => c.name !== me.name)[0];
  if (other) {
    LAB.ptName(other.name);
    const h2 = LAB.playProbe('nba', fx);
    ok('the picker changes the man shown', h2.indexOf(other.name) >= 0);
    /* The two tabs share state.view.ptName, so How He Scores must now be on the same man. This
       check needs him to have a shot profile as well as a passing one - most men do, and if none
       in the fixture does the check says so rather than passing by not running. */
    const pt = LAB.ptCandsProbe('nba', fx) || [];
    const shared = cands.filter((c) => c.name !== me.name)
      .filter((c) => pt.some((x) => x.name === c.name))[0];
    if (shared) {
      LAB.ptName(shared.name);
      const h3 = LAB.actsProbe('nba', fx) || '';
      const h4 = LAB.playProbe('nba', fx) || '';
      ok('both tabs in the card follow the one picker (' + shared.name + ')',
         h3.indexOf(shared.name) >= 0 && h4.indexOf(shared.name) >= 0);
    } else {
      ok('a man with both a shot and a passing profile was found to check the shared picker',
         false, '(none in this fixture)');
    }
    LAB.ptName(me.name);
  }

  /* A sport with no assists file must render nothing rather than an empty shell. */
  const t2 = await boot('nfl');
  await t2.focus(0);
  const fx2 = (t2.LAB.state.sports.nfl.data.fixture || [])[0];
  ok('a sport without the file renders nothing at all',
     !t2.LAB.playProbe('nfl', fx2));

  /* ---- THE 2026-11-07 PASS: recent form, and a null worth printing --------------------
     Assists are the one of the three cards where a hot run partly survives its own minutes:
     over 57,648 player-games the hottest fifth beat the player's own rate by +0.31 the next
     game and +0.23 of that is still there per 30 minutes. So this card gets the recent-form
     panel From Deep already had, and Rebounding's version says the opposite about itself.

     And the obvious hypothesis was tested and failed. An assist needs the target to make the
     shot, so a network shooting well ought to lift the passer. Across 41,478 passer-games,
     weighting each target by his share of the passing: raw r=+0.0845, after the passer's own
     minutes +0.0461, after minutes AND his own recent assists -0.0006. The card has to say so,
     because the names are right there and inviting exactly that reading.
     --------------------------------------------------------------------------------------- */
  const play = LAB.playProbe ? LAB.playProbe('nba', fx) : null;
  ok('the playmaking card renders', !!play && play.length > 500);
  if (play) {
    const txt = play.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');
    ok('it has a last-5 panel', /His last 5/i.test(txt));
    ok('with minutes on it', /His last 5[\s\S]{0,400}Minutes/.test(txt));
    ok('and a per-minute line', /minutes taken out/.test(txt));
    ok('it states that a run here partly survives its minutes',
       /\+0\.23 of that survives per 30/.test(txt));
    /* The null, and the number. A card that merely omitted the claim would leave the reader to
       make it from the names on screen. */
    ok('the target-form null is stated', /not a read/.test(txt) && /41,478/.test(txt));
    ok('with the controlled figure, not just the raw one',
       /\u22120\.0006|\u22120.0006|-0\.0006/.test(txt));
    ok('and it does not oversell the raw correlation',
       /raw it looks like something/.test(txt));
  }


  /* ---- THE FORECAST PANEL ARRIVES WITH THE NEXT BUILD ----------------------------------
     build_nba_assists.box_form() writes league.stability. Until it has run the field is absent
     and the card must not draw the panel. Both branches are real assertions; the second one
     injects the block so the drawing path is exercised now rather than first appearing in
     front of a reader. */
  const afile = LAB.state.sports.nba.data.assists || {};
  const alg = afile.league || {};
  const ptxt = () => (LAB.playProbe('nba', fx) || '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');
  if (alg.stability && alg.stability.assists != null) {
    ok('the build wrote stability, so the forecast panel is drawn',
       /How much of this you can forecast/i.test(ptxt()));
  } else {
    ok('no stability block yet, so no forecast panel is drawn',
       !/How much of this you can forecast/i.test(ptxt()));
    const keep = afile.league;
    afile.league = Object.assign({}, alg, { stability: { assists: 0.877, minutes: 0.785, n: 562 } });
    const injected = ptxt();
    ok('and with one injected it draws, so the path is not dead code',
       /How much of this you can forecast/i.test(injected));
    ok('printing the r it was given', /r=\+0\.88/.test(injected));
    ok('over the stated number of players', /562 players/.test(injected));
    ok('and a per-game figure, not a career total', !/\b\d{3,}\b a game/.test(injected));
    afile.league = keep;
    ok('and the injection is cleaned up', !/How much of this you can forecast/i.test(ptxt()));
  }


  /* ---- THE 2026-11-11 SIMPLIFY PASS ---------------------------------------------------
     The card leads with a verdict strip and every explanatory note is folded to one plain
     line with the working behind a tap. The risk in a pass like this is that "simpler" turns
     into "missing": a caveat deleted rather than folded. So this checks the strip is there,
     that no note is left sprawling, and that every folded note still carries its full text. */
  {
    const h = LAB.playProbe('nba', fx) || '';
    ok('the card leads with a verdict strip', /class="nb-verdict"><span/.test(h));
    ok('which says where the opponent ranks', /give up the <b>\d+(st|nd|rd|th)<\/b>-most/.test(h));
    const folded = h.match(/<details class="nb-why"><summary>[\s\S]*?<\/details>/g) || [];
    ok('the notes are folded behind a tap (' + folded.length + ')', folded.length > 0);
    const loose = (h.replace(/<details class="nb-why">[\s\S]*?<\/details>/g, '')
      .match(/<(p|div) class="(av-note|pa-foot)">([\s\S]*?)<\/\1>/g) || [])
      .filter((n) => n.replace(/<[^>]*>/g, '').length >= 150);
    ok('no long note is left unfolded (' + loose.length + ')', loose.length === 0);
    ok('every folded note keeps its full text underneath, nothing deleted',
       folded.every((d) => {
         const line = (d.match(/<summary>([\s\S]*?)<u>/) || [, ''])[1].replace(/<[^>]*>/g, '');
         const body = d.replace(/<summary>[\s\S]*?<\/summary>/, '').replace(/<[^>]*>/g, '');
         return line.length > 20 && line.length < 170 && body.length > line.length;
       }));
    ok('and the one-liners carry no stats-speak', folded.every((d) =>
       !/r=|\d{2},\d{3}/.test((d.match(/<summary>([\s\S]*?)<u>/) || [, ''])[1])));
  }

  /* ---- THE PAGE-WIDE NOTE FOLD (2026-11-11) --------------------------------------------
     Every long .dd-note on every sport folds to its first sentence with the rest behind a tap,
     done once by watching the page rather than in the forty functions that write the notes.
     What has to hold: a long note is wrapped, its full text is MOVED not cut, and a note that
     is already a line long is left exactly as it was. */
  {
    const doc = t.w.document;
    const long = doc.createElement('div'); long.className = 'dd-note';
    const full = 'His last twelve games as recorded. A dash means the stat was not kept for that '
      + 'game, which is not the same as a zero, and the average beside it only counts the games '
      + 'where it was kept so the two columns can disagree without either being wrong.';
    long.textContent = full;
    const short = doc.createElement('div'); short.className = 'dd-note';
    short.textContent = 'Small samples: a few games say very little.';
    doc.body.appendChild(long); doc.body.appendChild(short);
    await new Promise((r) => setTimeout(r, 150));
    const wrap = long.parentNode;
    ok('a long note is folded behind a tap', wrap && wrap.tagName === 'DETAILS' && wrap.className === 'nb-why');
    ok('showing its first sentence',
       !!wrap.querySelector('summary') && wrap.querySelector('summary').textContent.indexOf('His last twelve games as recorded.') === 0);
    ok('with the whole note still underneath, moved not cut', long.textContent === full);
    ok('closed until asked for', wrap.open === false);
    ok('a note that is already one line is left alone', short.parentNode === doc.body);
    wrap.remove(); short.remove();
  }
  done();
})();
