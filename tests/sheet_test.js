/* THE CHEAT SHEET IS A DIGEST, AND A DIGEST THAT LIES BY OMISSION IS WORSE THAN NO DIGEST
 *
 *   node tests/sheet_test.js
 *
 * Everything on this page belongs to a tile that already exists - the same capture pass Degen
 * Crew runs, the same rows, the same edges - cut to the next 24 hours and trimmed to the top
 * few. So the risks are not arithmetic ones. They are:
 *
 *   The window silently widening, so "the next 24 hours" shows a game three days out.
 *   A section vanishing when it is quiet, leaving a reader unable to tell "nothing today" from
 *     "this was never checked" - and the second is how you stop noticing a feed has broken.
 *   Degen Crew's own window changing, because the sheet reaches into its functions.
 *   A row leaking onto the page for a game outside the window, or for a team not playing.
 *
 * Those are what these check.
 */
const { boot, ok, done } = require('./harness');

(async () => {
  const t = await boot('nba');
  await t.focus(0);
  const LAB = t.LAB;
  LAB.state.view.focus = null;

  if (!LAB.sheetProbe) { ok('the page exposes the Cheat Sheet for testing', false); return done(); }
  ok('the page exposes the Cheat Sheet for testing', true);
  ok('the window is 24 hours', LAB.csHoursProbe() === 24);

  /* DEGEN CREW MUST BE UNTOUCHED. The sheet calls crewGames/crewCapture with an extra argument;
     called without one they have to behave exactly as they did, or the sheet has quietly
     rewritten the view it borrowed from. */
  const crewG = LAB.state.sports.nba && typeof LAB.csGamesProbe === 'function';
  ok('the crew window and the sheet window are different lists', crewG);
  const sheetGames = LAB.csGamesProbe('nba') || [];
  ok('the sheet has games in its window (' + sheetGames.length + ')', sheetGames.length > 0);
  const now = Date.now();
  const late = sheetGames.filter((x) => x.ms > now + 24 * 36e5 + 6e4);
  ok('and not one of them starts beyond 24 hours (' + late.length + ' over)', late.length === 0);
  const past = sheetGames.filter((x) => x.ms < now - 6 * 36e5);
  ok('nor long finished', past.length === 0);

  const sports = LAB.csSportsProbe();
  ok('it reports which sports have games in the window (' + sports.join(', ') + ')',
     Array.isArray(sports));

  const html = LAB.sheetProbe();
  ok('the sheet renders', !!html && html.length > 1000);
  if (!html) return done();
  const txt = html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');

  /* EVERY SECTION APPEARS, EVERY TIME. Count them in the markup rather than the text, so a
     section that renders an empty state still counts - that is the whole point. */
  /* A SECTION WITH NOTHING IN IT NO LONGER RENDERS. Nine headed cards each saying "nothing
     today" was most of the sheet on a thin slate. But every one of the nine must still be
     ACCOUNTED FOR - either it has rows on the page, or it is named on the quiet line at the
     foot. A section that is in neither place has silently gone missing, which is the failure
     mode the old always-visible version existed to prevent. */
  const SECTIONS = ['Hottest streaks', 'Where the reads agree', 'Best of every signal',
    'Softest matchups', 'Offences', 'Defences', 'Ruled out', 'Moved since it opened',
    'Short turnarounds'];
  const secs = (html.match(/class="sig-tile cs-sec"/g) || []).length;
  ok('only sections with something in them are drawn (' + secs + ' of ' + SECTIONS.length + ')',
     secs > 0 && secs <= SECTIONS.length);
  const quietBlock = (html.match(/class="sig-quiet cs-quiet">([\s\S]*?)<\/details>/) || [, ''])[1];
  const quietTxt = quietBlock.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');
  /* Take the drawn titles from the section headers themselves. Splitting the markup on the
     section boundary swept up everything after the LAST section too - including the quiet
     line - so a folded-away section read as drawn and the caveat check below passed on a
     section that was not on the page. */
  const drawnList = (html.match(/class="sig-ttl">([^<]*)</g) || [])
    .map((m) => m.replace(/.*">/, '').replace(/<$/, ''));
  const drawn = drawnList.join(' | ');
  const lost = SECTIONS.filter((n) => drawn.indexOf(n) < 0 && quietTxt.indexOf(n) < 0);
  ok('and every one of the nine is either drawn or named on the quiet line ('
     + lost.join(', ') + ')', lost.length === 0);
  const named = SECTIONS.filter((n) => quietTxt.indexOf(n) >= 0);
  ok('the quiet line counts what it lists (' + named.length + ' named)',
     !quietBlock || +(quietBlock.match(/>(\d+) section/) || [, 0])[1] === named.length);
  ok('and gives a reason for each, not just a name',
     (quietBlock.match(/<span>([^<]*)</g) || []).every((m) => m.length > 28));
  /* Market against itself is deliberately gone, and so is the function that fed it. */
  ok('the nerd section is not on the page', txt.indexOf('Market against itself') < 0);
  ok('and its builder went with it', typeof LAB.csNerdProbe !== 'function');
  /* A DRAWN SECTION ALWAYS HAS ROWS. The empty state is gone from the body entirely now, so a
     headed card containing neither a table nor anything else is a section that got drawn when
     it should have been folded away - the exact thing this change was meant to stop. */
  const blocks = html.split('class="sig-tile cs-sec"').slice(1);
  const hollow = blocks.filter((b) => !/class="cs-tbl"/.test(b));
  ok('every section that IS drawn holds rows (' + blocks.length + ' drawn, '
     + hollow.length + ' hollow)', blocks.length > 0 && hollow.length === 0);
  ok('and no empty-state card survives in the body', html.indexOf('class="cs-none"') < 0);

  /* "NOTHING TODAY" AND "NEVER BUILT" ARE DIFFERENT SENTENCES. Four sports ship no
     teams_form.json, so their Offences and Defences sections are permanently empty - and
     saying "no team leads a market by enough" there is a claim about data nobody has.
     Drive it on a sport that genuinely lacks the file rather than trusting the branch. */
  const noForm = Object.keys(LAB.state.sports).filter((k) => {
    const S = LAB.state.sports[k];
    return S && S.data && !(S.data.teams_form && (S.data.teams_form.length
           || Object.keys(S.data.teams_form).length));
  });
  ok('at least one sport in this fixture set has no team form file (' + noForm.join(', ') + ')',
     noForm.length > 0);
  /* Only the booted sport is in the window here, so take ITS form file away for one render
     rather than skipping the branch - a check that silently does not run is not a check. */
  const keepForm = LAB.state.sports[sports[0]].data.teams_form;
  LAB.state.sports[sports[0]].data.teams_form = [];
  const h3 = LAB.sheetProbe().replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');
  ok('a sport with no team form file says so rather than claiming nothing stands out',
     /has no team form file yet/.test(h3)
     && !/leads a market by enough/.test(h3));
  LAB.state.sports[sports[0]].data.teams_form = keepForm;
  ok('and the real file renders rows again once it is back',
     /class="cs-tbl"/.test(LAB.sheetProbe()));

  /* THE LISTS THEMSELVES. */
  const caps = LAB.csCapsProbe(sports);
  const stk = LAB.csStreaksProbe(caps) || [];
  ok('streaks are capped at ten (' + stk.length + ')', stk.length <= 10);
  ok('and ordered best first',
     stk.every((r, i) => i === 0 || r.edge == null || stk[i - 1].edge == null
                         || stk[i - 1].edge >= r.edge));

  /* Only teams actually playing inside the window may appear in the team-level lists. A table
     of soft defences that includes a team with no game today is noise dressed as a shortlist. */
  const playing = new Set();
  sports.forEach((k) => (LAB.csGamesProbe(k) || []).forEach((x) => {
    playing.add(k + '|' + x.g.home); playing.add(k + '|' + x.g.away);
  }));
  const dvp = LAB.csDvpProbe(sports) || [];
  const off = LAB.csTeamsProbe(sports, false) || [];
  const def = LAB.csTeamsProbe(sports, true) || [];
  [['soft matchups', dvp, false], ['offences', off, true], ['defences', def, true]]
  .forEach(([label, rows, bothTails]) => {
    ok(label + ': capped at ten (' + rows.length + ')', rows.length <= 10);
    const stray = rows.filter((r) => !playing.has(r.k + '|' + r.team));
    ok(label + ': every team listed is playing in the window (' + stray.length + ' stray)',
       stray.length === 0);
    ok(label + ': ordered by distance from the league',
       rows.every((r, i) => i === 0 || rows[i - 1].pct >= r.pct));
    if (bothTails) {
      /* ONE ROW PER MARKET, and it is the leader. A second row for the same market means
         either the dedupe broke or a non-leader slipped in, and the whole point of the
         section is that the row IS the top of that table. */
      const per = {};
      rows.forEach((r) => { per[r.k + '|' + r.mkt] = (per[r.k + '|' + r.mkt] || 0) + 1; });
      const dupes = Object.keys(per).filter((x) => per[x] > 1);
      ok(label + ': one row per market (' + dupes.join(', ') + ')', dupes.length === 0);
    }
    ok(label + ': and every one of them is a real distance from it',
       rows.every((r) => (bothTails ? Math.abs(r.pct) >= 5 : r.pct > 0)));
    /* No team may fill the list. One team's three markets took three of ten places before this
       cap, which turns a view of the day into a view of that team. */
    const per = {};
    rows.forEach((r) => { per[r.k + '|' + r.team] = (per[r.k + '|' + r.team] || 0) + 1; });
    const hogs = Object.keys(per).filter((x) => per[x] > 2);
    ok(label + ': no team takes more than two places (' + hogs.join(', ') + ')', hogs.length === 0);
  });

  /* THE RANK AND THE MATCHUP, which are what the row now says out loud. */
  const teamRows = [...off, ...def];
  ok('every team row carries a rank and the field it ranks within (' + teamRows.length + ')',
     teamRows.every((r) => r.rank >= 1 && r.of >= 6 && r.rank <= r.of));
  /* The row is supposed to be the LEADER among the teams playing, so its rank cannot sit in
     the bottom half of the whole league - that would mean a non-leader got through. */
  ok('and it really is the top of that table, not a middling side',
     teamRows.every((r) => r.rank <= r.of / 2));
  ok('every one of them clears the floor above the league',
     teamRows.every((r) => r.pct >= 5));
  /* THE OPPONENT IS RANKED IN THE OTHER HALF OF THE SAME STAT. Pairing Boston's three-point
     offence with Orlando's three-point OFFENCE would read as a matchup and be nonsense, so
     check the pairing exists, sits inside its own table, and is not the team itself. */
  const paired = teamRows.filter((r) => r.oppRank != null);
  ok('the side they meet is ranked in the same stat (' + paired.length + ' of '
     + teamRows.length + ' paired)', teamRows.length === 0 || paired.length > 0);
  ok('and that rank sits inside its own table',
     paired.every((r) => r.oppRank >= 1 && r.oppOf >= 6 && r.oppRank <= r.oppOf));
  ok('a team is never paired against itself',
     teamRows.every((r) => String(r.team) !== String(r.opp)));
  /* The rendered row must print both numbers, or the pairing never reached the reader. */
  const meets = (html.match(/class="cs-meet[^"]*">([^<]*)</g) || []);
  ok('and the row on the page names who they meet (' + meets.length + ')',
     meets.length >= paired.length && meets.every((m) => /">v\s+\S/.test(m)));
  ok('with that side\'s rank beside it',
     paired.length === 0 || meets.some((m) => /\d+(st|nd|rd|th) of \d+/.test(m)));

  const outs = LAB.csOutsProbe(sports) || [];
  ok('ruled-out players are all from teams playing today (' + outs.length + ')',
     outs.every((r) => [...playing].some((p) => p.indexOf(r.k + '|') === 0)));
  /* injury.json names teams in full ("Brooklyn Nets") while every other file uses a code, so
     this row printed a full team name where the whole rest of the page prints three letters,
     and its badge could not resolve either. The team here must be the same token the fixtures
     use - which is also the test that it is a team the sheet can draw. */
  const badTeam = outs.filter((r) => !playing.has(r.k + '|' + r.team));
  ok('and each one carries the team code the fixtures use, not a full team name ('
     + badTeam.map((r) => r.team).join(', ') + ')', badTeam.length === 0);

  const rest = LAB.csRestProbe(sports) || [];
  ok('rest rows are short turnarounds only, never a normal week',
     rest.every((r) => r.days <= 1 || r.in7 >= 3));

  /* THE SPEC PARSER, which consensus is built on. */
  const spec = LAB.csSpecProbe('<button data-d="{&quot;name&quot;:&quot;A B&quot;,'
    + '&quot;mkt&quot;:&quot;points&quot;,&quot;line&quot;:20.5,&quot;side&quot;:&quot;over&quot;}">x</button>');
  ok('a row\'s embedded bet spec is read back', !!spec && spec.name === 'A B' && spec.line === 20.5);
  ok('and a row without one returns nothing rather than throwing',
     LAB.csSpecProbe('<div>no button</div>') === null);
  ok('as does malformed json', LAB.csSpecProbe('<button data-d="{oops">x</button>') === null);

  /* CONSENSUS keys on player + market + SIDE, not the line: two tiles liking the same man's
     rebounds at 11.5 and 12.5 are agreeing, and keying on the line would hide that. */
  const con = LAB.csConsensusProbe(caps) || [];
  ok('consensus rows each have two or more tiles behind them',
     con.every((e) => e.n >= 2));
  ok('and are ordered by how many agree', con.every((e, i) => i === 0 || con[i - 1].n >= e.n));
  ok('each one records the lines the tiles used', con.every((e) => !!e.lines));

  /* LINE MOVES cannot be exercised on real data - odds_open.json has never existed in the repo
     for any sport, because the odds workflow wrote it on the runner and never staged it. The
     section is built and the fix is in the workflow, so this drives it with a synthetic open
     file rather than leaving the code unrun until tomorrow. */
  const S = LAB.state.sports.nba;
  const lines = (S.data.odds && S.data.odds.lines) || [];
  const byName = S.idx.byName || {};
  const playingNba = new Set();
  (LAB.csGamesProbe('nba') || []).forEach((x) => { playingNba.add(x.g.home); playingNba.add(x.g.away); });
  const usable = lines.filter((r) => {
    const p = byName[r.player];
    return r.over && p && p.team && playingNba.has(p.team);
  }).slice(0, 3);
  /* THE ODDS FILE CAN BE A SLATE BEHIND. The odds job only pulls a sport close to its games, so
     for much of the day the main lines on file belong to the slate that has already been played
     and nobody on today's teams has one. That is how the feed works, not a fault, and this
     check is about the movement arithmetic - so when there is nothing real to drive it with,
     it prices three players from today's teams itself and takes them away again afterwards. */
  const lent = [];
  if (!usable.length) {
    Object.keys(byName).filter((n) => byName[n].team && playingNba.has(byName[n].team))
      .slice(0, 3).forEach((n) => {
        const r = { player: n, market: 'points', line: 10.5, over: 1.9, under: 1.9, book: 'TAB' };
        lines.push(r); lent.push(r); usable.push(r);
      });
  }
  ok('there are lines on today\'s teams to drive the movement check (' + usable.length
     + (lent.length ? ', lent' : '') + ')', usable.length > 0);
  if (usable.length) {
    const opened = {};
    usable.forEach((r, i) => {
      // first one shortened hard, the rest drifted, so both directions are exercised
      opened[r.player + '|' + r.market + '|' + r.line + '|' + r.book] =
        { o: +r.over * (i === 0 ? 1.5 : 0.7), u: r.under, t: '2026-01-01T00:00Z' };
    });
    S.data.oddsOpen = { day: 'x', opened: opened };
    const mv = LAB.csMovesProbe(['nba']) || [];
    ok('with opening prices present, movement is found (' + mv.length + ')', mv.length > 0);
    ok('a price below its open reads as backed', mv.some((m) => m.pct < 0));
    ok('and one above it as drifting', mv.some((m) => m.pct > 0));
    ok('every move clears the noise floor', mv.every((m) => Math.abs(m.pct) >= 6));
    ok('biggest move first',
       mv.every((m, i) => i === 0 || Math.abs(mv[i - 1].pct) >= Math.abs(m.pct)));
    const h2 = LAB.sheetProbe();
    ok('and the section renders them', /\$\d+\.\d\d → \$\d+\.\d\d/.test(h2));
    delete S.data.oddsOpen;
  }
  lent.forEach((r) => { const i = lines.indexOf(r); if (i >= 0) lines.splice(i, 1); });

  /* IT RENDERS ITS OWN ROWS, NOT THE TILES'. The first version embedded each captured row's
     markup, which is 1,800-2,500 characters of avatar, pip grid and three stat lines - ten of
     them is a screen and a half for one section. The sheet now extracts the bet, the headline
     number and the one-line reason and lays them out itself. If tile markup ever leaks back in,
     the page silently triples in length again, so this checks for the tells. */
  /* 'pimg' used to be on this list as a proxy for a leaked avatar. It cannot be any more: the
     sheet now puts a headshot on its own rows deliberately, through the same playerImgP every
     other page uses. The other four classes are written by tile code and nothing else, so they
     remain the tells. */
  ['stk-pips', 'row-btns', 'gl-left', 'elm-left'].forEach((cls) => {
    ok('no raw tile markup leaks into the sheet (' + cls + ')',
       html.indexOf('class="' + cls) < 0);
  });
  const rows = (html.match(/class="cs-r[ "]/g) || []).length;
  ok('and it uses one row shape throughout (' + rows + ' rows)', rows > 20);

  /* EVERY ROW CARRIES A FACE OR A BADGE, and the image column collapses when it has neither
     rather than leaving a hole. A row with an avatar cell and nothing in it is the failure this
     catches - that is what a broken image path looks like on the page. */
  const imgCells = (html.match(/class="cs-r-i"/g) || []).length;
  const noImg = (html.match(/class="cs-r cs-r-noi"/g) || []).length;
  ok('every row either carries an image or collapses the column (' + imgCells + ' with, '
     + noImg + ' without)', imgCells + noImg === rows);
  ok('and most of them do carry one (' + imgCells + '/' + rows + ')', imgCells > rows * 0.6);
  ok('an empty avatar cell is never printed', html.indexOf('class="cs-r-i"></div>') < 0);
  /* Both image paths must be exercised: player rows go through playerImgP (a headshot or an
     initials circle), team rows through teamLogo (a badge or a three-letter placeholder). */
  ok('player rows use the page\'s headshot helper',
     /class="cs-r-i">\s*<span class="pimg/.test(html) || /class="cs-r-i">\s*<span class="pimg-ini/.test(html));
  ok('team rows use the page\'s logo helper',
     /class="cs-r-i">\s*<(img class="fxr-logo|div class="fxr-logo-ph)/.test(html));
  ok('the whole page stays within a digest\'s length (' + Math.round(html.length / 1024)
     + 'KB)', html.length < 120000);

  /* A PRICE BOX MUST NEVER PRINT NaN. The Double Trouble tile guarded on the bet object
     existing rather than on it holding a price, so a row with neither printed "$NaN". */
  ok('no row prints a NaN price', html.indexOf('$NaN') < 0 && !/\$NaN/.test(txt));
  /* NOR 'undefined', WHICH IS THE SAME CLASS OF FAULT AND WAS ON THE PAGE. One blanket check,
     because the next field to go missing will not be the one that went missing last time. */
  ok('and no row prints "undefined" or "null" in place of a value',
     !/\bundefined\b|\bnull\b/.test(txt));

  /* ONE SPORT AT A TIME. The sheet is now per sport, so nothing from a sport other than the
     selected one may reach the page, and the tab row has to be there to switch with. */
  const shown = LAB.csPickProbe ? LAB.csPickProbe() : null;
  ok('the sheet names one sport as the selected one (' + shown + ')',
     !!shown && sports.indexOf(shown) >= 0);
  /* IT LIVES INSIDE DEGEN CREW, NOT ON A TAB OF ITS OWN. The sheet is a digest of the crew's
     own tiles cut to 24 hours, so a separate tab put it a navigation away from the thing it
     summarises. These check the move actually happened rather than left two doors open. */
  const bar = LAB.tabsProbe ? LAB.tabsProbe() : null;
  if (bar) ok('the Cheat Sheet is no longer a top-level tab (' + bar.join(', ') + ')',
              bar.indexOf('sheet') < 0);
  const crew = LAB.crewProbe ? LAB.crewProbe() : '';
  ok('the crew grid offers it as a tile',
     /LAB\.crewOpen\('sheet'\)/.test(crew) && crew.indexOf('Cheat Sheet') >= 0);

  const keepTile = LAB.state.view.crewTile;
  LAB.state.view.crewTile = 'sheet';
  const inCrew = LAB.crewProbe();
  ok('opening that tile renders the sheet inside the crew page',
     inCrew.indexOf('Hottest streaks') >= 0 && inCrew.indexOf('Defences') >= 0);
  ok('with a way back to the tiles', /LAB\.crewOpen\(null\)/.test(inCrew));

  /* ONE SPORT SELECTOR, NOT TWO. The crew's own chips drive the sheet; a second row of tabs
     inside it would only disagree with them. An unknown or "all" filter must fall back rather
     than blanking the page. */
  const keepSport = LAB.state.view.crewSport;
  LAB.state.view.crewSport = sports[0];
  ok('the crew chip picks the sheet\'s sport', LAB.csPickProbe() === sports[0]);
  LAB.state.view.crewSport = 'all';
  ok('"all" falls through to the soonest game rather than showing nothing',
     sports.indexOf(LAB.csPickProbe()) >= 0);
  LAB.state.view.crewSport = 'zzz-not-a-sport';
  ok('and a filter naming a sport with no game in the window falls back too',
     sports.indexOf(LAB.csPickProbe()) >= 0);
  LAB.state.view.crewSport = keepSport;
  ok('the sheet carries no sport tabs of its own any more',
     !/LAB\.csK\(/.test(inCrew) && typeof LAB.csK !== 'function');
  LAB.state.view.crewTile = keepTile;

  /* THE HONESTY LINE. This page ranks things the graded ledger says lose money overall; it has
     to say so rather than presenting an ordering as a shortlist of bets.
     The paragraph under every section is gone - ten of them was more words than rows. But the
     caveats themselves are NOT optional, so each one had to land somewhere: the page-level
     paragraph at the foot, and a handful of words in the section subtitles. These check it was
     a move and not a deletion. */
  ok('the sheet says it is a map of the day, not a list of bets',
     /map of the day, not a list of bets/.test(txt));
  ok('and gives the suite\'s graded record rather than implying an edge',
     /−6\.1%/.test(txt));
  ok('the streaks section still carries its own tile\'s record',
     /worst performer on the graded ledger/.test(txt));
  ok('and nothing here claims to be a new read', /nothing on this page is a new read/.test(txt));
  /* The notes are gone from under the sections, which is what was asked for - but exactly one
     note must survive, at the foot. Zero would mean the caveats went with them. */
  const notes = (html.match(/class="av-note/g) || []).length;
  ok('one note on the page, at the foot, not one under every section (' + notes + ')',
     notes === 1 && /class="av-note cs-foot"/.test(html));
  /* Each caveat rides in its own section's subtitle, so it is present exactly when that
     section is - checking for it unconditionally would fail the moment a quiet slate folded
     the section away, and checking for it never would prove nothing. Pair them. */
  [['Softest matchups', /a reason to look, not a reason on its own/],
   ['Where the reads agree', /agreement, not confirmation/],
   ['Moved since it opened', /never who was right/]].forEach(([name, re]) => {
    const shown = drawn.indexOf(name) >= 0;
    ok((shown ? name + ' is drawn, so it carries its caveat'
              : name + ' is quiet, so its caveat is correctly absent'),
       shown ? re.test(txt) : true);
  });
  /* At least one of them must actually have been exercised, or the loop above proved nothing. */
  ok('and at least one caveat-carrying section was on the page to check',
     ['Softest matchups', 'Where the reads agree', 'Moved since it opened']
       .some((n) => drawn.indexOf(n) >= 0));

  /* A sport switched off contributes nothing. */
  ok('every sport the sheet lists really has a game in the window',
     LAB.csSportsProbe().length > 0
     && LAB.csSportsProbe().every((k) => (LAB.csGamesProbe(k) || []).length > 0));

  done();
})();
