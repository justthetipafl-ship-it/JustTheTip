/* THE GLASS, AND THE CLAIM PLAYMAKING COULD NOT MAKE
 *
 *   node tests/rebounds_test.js
 *
 * Playmaking had to print a null: a passer's assists do not move when his busiest target sits
 * (+0.00 across 189 passers, 46% going down). Rebounds are a contested pool, so the same test
 * comes out the other way - +0.86 when the top rebounder on his club sits, against a +0.21
 * control, 73% going up - and that splits into +0.51 of inherited minutes and +0.34 of a
 * genuinely bigger share.
 *
 * Because this card DOES make a claim, these checks are stricter than the assists ones. They
 * hold the arithmetic of the lift to the fit in the file, they require the minutes and share
 * halves to be shown apart rather than as one number, and they require the error to be printed
 * beside it - a per-pair miss of 0.84 of a rebound is most of the effect, and a figure that size
 * presented without its error is a projection pretending to be a measurement.
 *
 * They also pin the easing. Ranking rivals on raw per-game put a man with 10.5 boards across TWO
 * games above Aaron Gordon as Jokic's biggest rival, and the gain formula multiplies that figure
 * - so his absence would have been priced at +0.7 rebounds off a two-game sample.
 */
const { boot, ok, done } = require('./harness');

(async () => {
  const t = await boot('nba');
  await t.focus(0);
  const LAB = t.LAB;

  if (!LAB.glassProbe) { ok('the page exposes Rebounding for testing', false); return done(); }
  ok('the page exposes Rebounding for testing', true);

  const S = LAB.state.sports.nba;
  const rf = S.data.rebounds;
  ok('rebounds.json is loaded', !!rf && !!rf.players);
  if (!rf || !rf.players) return done();
  ok('with a usable pool (' + rf.players.length + ')', rf.players.length >= 200);
  ok('and a concession table (' + Object.keys(rf.defence || {}).length + ')',
     Object.keys(rf.defence || {}).length >= 25);

  /* THE FIT the card prices absences from. */
  const fit = (rf.league || {}).rival || {};
  ok('the file carries the fitted gain', fit.slope != null && fit.intercept != null);
  ok('and the error it was fitted with (' + fit.mae + ')', fit.mae > 0 && fit.mae < 2);
  ok('the decomposition is there and adds up (' + fit.fromMinutes + ' + ' + fit.fromShare
     + ' = ' + fit.top + ')',
     Math.abs((fit.fromMinutes + fit.fromShare) - fit.top) < 0.02);
  ok('and the top-rival effect clears its own control (' + fit.top + ' v ' + fit.control + ')',
     fit.top > fit.control * 2);

  /* THE DATA. */
  const bad = [];
  rf.players.forEach((p) => {
    if (p.oreb + p.dreb - p.reb > 0.05) bad.push(p.name + ' splits exceed the total');
    if (p.trbPct != null && (p.trbPct <= 0 || p.trbPct > 45)) bad.push(p.name + ' rate ' + p.trbPct);
    if ((p.rivals || []).some((r) => r.id === p.playerId)) bad.push(p.name + ' is his own rival');
    if ((p.rivals || []).some((r, i) => i && p.rivals[i - 1].reb < r.reb))
      bad.push(p.name + ' rivals out of order');
  });
  ok('every profile is internally consistent' + (bad.length ? ' - ' + bad.slice(0, 3).join('; ') : ''),
     bad.length === 0);

  /* THE EASING. A short record must be pulled toward the league, and a settled one must not be
     meaningfully moved - a shrink that flattens everybody is as wrong as no shrink at all. */
  let pulled = 0, shortOnes = 0, offFormula = 0, all = 0;
  let shortMove = [], longMove = [];
  rf.players.forEach((p) => {
    (p.rivals || []).forEach((r) => {
      if (r.rebRaw == null) return;
      all++;
      /* The exact formula, rather than a hand-picked tolerance. A big rebounder on 50 games
         legitimately moves 1.18, so "moved by less than a board" would fail a correct file -
         which is what a first version of this check did. */
      const want = (r.rebRaw * r.games + rf.rivalShrink * rf.rivalPrior)
        / (r.games + rf.rivalShrink);
      if (Math.abs(want - r.reb) > 0.011) offFormula++;
      if (r.games <= 5 && r.rebRaw > rf.rivalPrior + 3) {
        shortOnes++;
        if (r.reb < r.rebRaw - 0.5) pulled++;
      }
      const frac = r.rebRaw > 0 ? Math.abs(r.reb - r.rebRaw) / r.rebRaw : 0;
      if (r.games <= 5) shortMove.push(frac);
      if (r.games >= 50) longMove.push(frac);
    });
  });
  const avg = (a) => a.reduce((x, y) => x + y, 0) / (a.length || 1);
  ok('every eased figure is exactly the shrink formula (' + all + ' rivals, ' + offFormula
     + ' off)', all > 500 && offFormula === 0);
  ok('a short record is eased down toward the league (' + pulled + '/' + shortOnes + ')',
     shortOnes > 5 && pulled === shortOnes);
  /* The shrink must bite HARDER on a short record than a long one. A shrink that moved everyone
     equally would be as wrong as none at all - it would just rescale the whole column. */
  ok('and it moves a short record far more than a settled one ('
     + Math.round(avg(shortMove) * 100) + '% v ' + Math.round(avg(longMove) * 100) + '%)',
     longMove.length > 50 && avg(shortMove) > avg(longMove) * 3);
  /* And the consequence that matters: nobody with a two-game record tops a rival list. */
  const topIsThin = rf.players.filter((p) => (p.rivals || [])[0]
    && p.rivals[0].games <= 5 && p.rivals.length > 1
    && p.rivals.some((r) => r.games >= 40 && r.rebRaw >= p.rivals[0].reb));
  ok('so no two-game man heads a rival list over a regular (' + topIsThin.length + ')',
     topIsThin.length === 0);

  const fx = (S.data.fixture || [])[0];
  ok('there is a fixture to render', !!fx);
  if (!fx) return done();

  const cands = LAB.rbCandsProbe('nba', fx);
  ok('the picker offers the men in this game (' + cands.length + ')', cands.length >= 4);
  ok('best rebounder first',
     cands.every((c, i) => i === 0 || cands[i - 1].rec.reb >= c.rec.reb));

  /* THE GAIN FORMULA. It must track the fit, and it must never pay out for a man who does not
     rebound - the intercept is below zero, so an unguarded formula would hand back a NEGATIVE
     lift and the row would read as though having him off the floor costs you boards. */
  const g10 = LAB.rbGainProbe('nba', 10);
  const g3 = LAB.rbGainProbe('nba', 3);
  ok('a ten-board rival out is worth real rebounds (+' + g10.toFixed(2) + ')',
     g10 > 0.4 && g10 < 1.2);
  ok('a three-board one is worth little (+' + g3.toFixed(2) + ')', g3 >= 0 && g3 < 0.35);
  ok('and the bigger rebounder is always worth more', g10 > g3);
  ok('a non-rebounder never returns a negative lift', LAB.rbGainProbe('nba', 0) >= 0);
  ok('the formula matches the fit in the file',
     Math.abs(g10 - Math.max(0, fit.slope * 10 + fit.intercept)) < 1e-9);

  const html = LAB.glassProbe('nba', fx);
  ok('the section renders', !!html && html.length > 400);
  if (!html) return done();
  const txt = html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');
  const me = LAB.rbPick ? null : cands[0];

  ok('it shows his share of the glass', /His share of the glass/.test(txt));
  ok('as a rate against the league, not just a count',
     /of the boards out there/.test(txt) && /v the league/.test(txt));
  ok('it splits offensive from defensive', /Defensive/.test(txt) && /Offensive/.test(txt));
  ok('it lists who he competes with', /Who he competes with/.test(txt));
  ok('with what each absence would be worth', /if out|no effect/.test(txt));
  ok('it breaks the rebounds down by quarter', /By quarter/.test(txt));
  ok('and what the opponent concedes', /rebounds conceded a game/.test(txt));

  /* PACE IS A SECOND READ, NOT A RESTATEMENT. Out of sample it is worth more than the concession
     (0.54 against 0.43) and residualised it adds +0.035 on top - most of the concession's own
     signal again. The board share, by contrast, adds +0.0008 and is only a breakdown. The card
     has to put pace beside the concession and the share below it. */
  const dClub = rf.defence[fx.away] || rf.defence[fx.home];
  ok('the file carries pace, ranked separately from the concession',
     !!dClub && dClub.pace != null && dClub.paceRank != null && dClub.rank != null);
  ok('and the boards breakdown', !!dClub && dClub.boards != null && dClub.oppShare != null);
  if (dClub) {
    ok('which multiplies back to the concession ('
       + dClub.boards + ' x ' + dClub.oppShare + ' = '
       + (dClub.boards * dClub.oppShare).toFixed(2) + ' v ' + dClub.perGame + ')',
       Math.abs(dClub.boards * dClub.oppShare - dClub.perGame) < 0.05);
  }
  /* The two rankings must genuinely differ somewhere, or pace is just the concession again
     wearing a different label - which is the thing the residual test was run to rule out. */
  const clubs = Object.keys(rf.defence);
  const differ = clubs.filter((c) => Math.abs(rf.defence[c].rank - rf.defence[c].paceRank) >= 5);
  ok('pace ranks a club differently from its concession for many of them ('
     + differ.length + '/' + clubs.length + ')', differ.length >= clubs.length / 4);
  ok('the card shows possessions a game', /possessions a game/.test(txt));
  ok('and the boards available with the share they allow',
     /boards in their/.test(txt) && /let the other side take/.test(txt));
  ok('it says the share adds almost nothing once the concession is known',
     /adds almost nothing once the concession is known/.test(txt));
  const offBy2 = clubs.filter((c) => {
    const v = rf.defence[c];
    return Math.abs(v.boards * v.oppShare - v.perGame) > 0.05;
  });
  ok('and the breakdown adds up for every club (' + offBy2.length + ' off)', offBy2.length === 0);

  /* THE HONESTY. */
  ok('it says a team-mate sitting really does move this one',
     /A team-mate sitting really does move this one/.test(txt));
  ok('and contrasts it with the assists null rather than quietly differing',
     /unlike assists/.test(txt));
  ok('it gives the measured effect and its control',
     /\+0\.86 rebounds/.test(txt) && /\+0\.21/.test(txt));
  ok('it keeps the minutes and the share halves apart',
     /\+0\.51 of that is simply the extra minutes/.test(txt) && /\+0\.34 is a genuinely bigger share/.test(txt));
  ok('it prints the error beside the claim rather than only the claim',
     /typical miss is 0\.\d\d of a rebound/.test(txt));
  ok('it says a short record was eased first',
     /eased toward the league before any of this/.test(txt));
  ok('and it calls the opponent strand the weak one',
     /opponent is the weak strand here/.test(txt));
  /* ...but it must not then show pace, worth MORE than the concession, without saying so -
     that reads as the card contradicting itself. */
  ok('while naming pace as the better half of it rather than leaving the contradiction',
     /Pace is the better half of it/.test(txt));
  ok('and giving what pace adds on top rather than only what it is worth alone',
     /ON TOP of the concession/.test(txt));
  ok('and saying the glass share adds nothing, so the two are not weighted alike',
     /which is nothing/.test(txt));
  ok('and that pace earns no place on the Playmaking card',
     /Playmaking card pace earns no place/.test(txt));

  /* THE LIFT BOX, where anyone is actually out. Its arithmetic must match the per-rival figures
     it was summed from, not merely look plausible. */
  /* ACROSS THE SLATE, not just the first fixture. The first version looked only at fixture 0,
     found nobody out, and reported a failure about the test rather than about the page - there
     are 25 men listed out in the data. */
  let withOut = null, fxOut = null;
  for (const f of (S.data.fixture || []).slice(0, 30)) {
    for (const c of (LAB.rbCandsProbe('nba', f) || [])) {
      const outs = (c.rec.rivals || []).filter((r) => {
        const st = LAB.paStatusProbe('nba', c, r);
        return st.mate && st.cls === 'pa-out';
      });
      if (outs.length) { withOut = { c, outs }; fxOut = f; break; }
    }
    if (withOut) break;
  }
  if (withOut) {
    LAB.ptName(withOut.c.name);
    const h = LAB.glassProbe('nba', fxOut);
    const tx = h.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');
    const want = withOut.outs.reduce((a, r) => a + LAB.rbGainProbe('nba', r.reb), 0);
    const m = tx.match(/\+(\d+\.\d\d) rebounds on tonight/);
    ok('the lift box appears when a rival is out (' + withOut.c.name + ')', !!m);
    if (m) {
      ok('and its total is the sum of the rivals who are actually out ('
         + m[1] + ' v ' + want.toFixed(2) + ')', Math.abs(+m[1] - want) < 0.011);
      /* THE SPLIT MUST COME FROM EACH RIVAL'S OWN SIZE. Measured by band, almost all of a
         bench man's effect is the minutes he frees up while a double-figure rebounder splits
         about two-to-one, so applying the top-rival ratio to everybody would inflate the share
         half - and the share half is the more interesting claim of the two. */
      const bands = ((rf.league || {}).rival || {}).bands || [];
      ok('the file carries a minutes fraction per rival size (' + bands.length + ')',
         bands.length >= 2);
      /* Not "bands.length < 2 ||  ..." - that passes by not running, which is how a check that
         proves nothing survives a review. */
      ok('and a smaller rival is more purely a minutes effect than a bigger one',
         bands.length >= 2 && bands[0].minuteFrac > bands[bands.length - 1].minuteFrac);
      const wantMin = withOut.outs.reduce(
        (a, r) => a + LAB.rbGainProbe('nba', r.reb) * LAB.rbMinFracProbe('nba', r.reb), 0);
      const mm = tx.match(/\+(\d+\.\d\d) from the minutes he inherits/);
      if (wantMin > 0.02 && (want - wantMin) > 0.02) {
        ok('the minutes half is summed from each rival\'s own band ('
           + (mm ? mm[1] : 'absent') + ' v ' + wantMin.toFixed(2) + ')',
           !!mm && Math.abs(+mm[1] - wantMin) < 0.011);
        ok('and the share half is the remainder, not a second guess',
           /from a bigger share of the glass/.test(tx));
      } else {
        ok('a lift that is all minutes says so instead of inventing a share half',
           /essentially all of it the minutes/.test(tx));
      }
      ok('and the names of who is out', withOut.outs.every((r) => tx.indexOf(r.name) >= 0));
      ok('and the give-or-take, so it does not read as a projection', /give or take 0\.\d\d/.test(tx));
    }
    LAB.ptName(cands[0].name);
  } else {
    ok('a fixture with an absent rival was found to check the lift box', false, '(none out)');
  }

  /* The picker is shared with the other tabs on this card. */
  const other = cands.filter((c) => c.name !== cands[0].name)[0];
  if (other) {
    LAB.ptName(other.name);
    ok('the picker changes the man shown',
       (LAB.glassProbe('nba', fx) || '').indexOf(other.name) >= 0);
    LAB.ptName(cands[0].name);
  }

  const t2 = await boot('nfl');
  await t2.focus(0);
  ok('a sport without the file renders nothing at all',
     !t2.LAB.glassProbe('nfl', (t2.LAB.state.sports.nfl.data.fixture || [])[0]));

  /* ---- THE 2026-11-07 PASS: rates, recent form, and which end of the floor -------------
     Three things were measured before any of this was built, and each one is checked here
     because each one is a claim the card now makes in words.

     1. orebPct and drebPct were in the file from the first build and the page read neither -
        it printed only the per-game counts, which are mostly a fact about how many shots get
        missed in his games rather than about him.
     2. A five-game rebounding run is a MINUTES run. Over 67,525 player-games the hottest fifth
        beat the player's own rate by +0.39 a game and played two minutes more; per 30 minutes
        the same run is worth -0.07. The panel therefore has to show minutes, and the per-minute
        line, or it would be showing form that is not there.
     3. The team-mate effect is DEFENSIVE. Re-measured over 8,847 pairs against a permutation
        null: averaged over all team-mates it is +0.02 a rebound per 30 minutes - nothing - and
        for a rival taking eight boards a game it is +0.44, of which +0.36 is defensive and
        +0.08 offensive at 54% up. An offensive-rebounds line barely moves on an absence.
     --------------------------------------------------------------------------------------- */
  const glass = LAB.glassProbe ? LAB.glassProbe('nba', fx) : null;
  ok('the rebounding card renders', !!glass && glass.length > 500);
  if (glass) {
    ok('the defensive row carries the RATE, not only the count',
       /Defensive[\s\S]{0,220}% of what was there/.test(glass.replace(/<[^>]*>/g, ' ')));
    ok('and so does the offensive row',
       /Offensive[\s\S]{0,220}% of what was there/.test(glass.replace(/<[^>]*>/g, ' ')));
    const txt = glass.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');
    ok('the last-5 panel is there', /His last 5/i.test(txt));
    ok('and shows minutes beside the boards, because minutes are what move',
       /His last 5[\s\S]{0,400}Minutes/.test(txt));
    ok('and a per-minute line with minutes taken out', /minutes taken out/.test(txt));
    /* The base has to come from the same games the window does. Comparing a five-game run
       against the build's multi-season average while calling it "his season" was the first
       version of this panel and it was quietly wrong. */
    /* The base is every game in the logs the page holds, which is NOT the set the card's
       header counts. The panel has to say which, or "v his season" is a claim about a window
       it does not have. */
    ok('the panel says how many games the base is drawn from',
       /of \d+ on file/.test(txt));
    ok('and does not call that window his season', !/v his season/.test(txt));
    ok('the card says the absence effect is defensive',
       /defensive boards are/i.test(txt) && /offensive-rebounds line/i.test(txt));
    ok('and that it is nothing for an average team-mate',
       /per 30 minutes it comes to \+0\.02/.test(txt));
  }


  /* ---- BLOCKS THAT ARRIVE WITH THE NEXT BUILD ------------------------------------------
     stability, recent and the split concession are written by build_nba_rebounds.py. Until
     that has run against live data the fields are simply absent, and the card must look as it
     did rather than printing a number nobody measured. A test that only checked "the panel is
     there" would fail in CI before the build lands; a test that only checked "it does not
     crash" would pass whatever the code did. So this asserts the PAIRING: absent field and no
     block, or present field and a block. One of the two branches runs every time, and the
     result says which.
     --------------------------------------------------------------------------------------- */
  const rfile = LAB.state.sports.nba.data.rebounds || {};
  const rlg = rfile.league || {};
  const anyDef = Object.keys(rfile.defence || {})
    .map((k) => rfile.defence[k]).filter((d) => d && d.drebAllowed != null);
  const gtxt = (LAB.glassProbe('nba', fx) || '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');

  if (rlg.stability && rlg.stability.reb != null) {
    ok('the build wrote stability, so the forecast panel is drawn',
       /How much of this you can forecast/i.test(gtxt));
    ok('and it reports r for both ends of the glass, not just the total',
       /Defensive[\s\S]{0,160}r=/.test(gtxt) && /Offensive[\s\S]{0,160}r=/.test(gtxt));
    ok('every r it prints is a real correlation',
       ['reb', 'oreb', 'dreb', 'minutes'].every((k) => rlg.stability[k] == null
          || (rlg.stability[k] >= -1 && rlg.stability[k] <= 1)));
    ok('measured over a stated number of players', rlg.stability.n > 20);
  } else {
    ok('no stability block yet, so no forecast panel is drawn',
       !/How much of this you can forecast/i.test(gtxt));
  }

  if (rlg.recent && rlg.recent.hottestPerMin != null) {
    /* The finding the card is built on. If a future window ever reverses it the card would be
       telling people the opposite of its own data, so check the sign rather than trust it. */
    const spread = rlg.recent.hottestPerMin - rlg.recent.coldestPerMin;
    ok('a hot rebounding run does not survive its own minutes (' + spread.toFixed(3) + ' per 30)',
       spread < 0.12, '— the card says it is minutes; if this went positive the card is wrong');
    ok('and the raw run does look hot, which is why the card has to say it',
       rlg.recent.hottest > 0.15);
    ok('with the extra minutes that explain it', rlg.recent.hottestMinutes > 0.5);
  } else {
    ok('no recent block yet — the card falls back to its written figures', true);
  }

  if (anyDef.length) {
    ok('the concession is split by end of the floor (' + anyDef.length + ' teams)',
       /defensive boards conceded/i.test(gtxt) && /offensive boards conceded/i.test(gtxt));
    const bad = anyDef.filter((d) => Math.abs((d.drebAllowed + d.orebAllowed) - d.perGame) > 0.6);
    ok('and the two halves add up to the total (' + bad.length + ' off)', bad.length === 0);
    ok('each half is ranked in its own table',
       anyDef.every((d) => d.drebAllowedRank >= 1 && d.orebAllowedRank >= 1));
  } else {
    ok('no split concession yet, so the card shows the total alone',
       !/defensive boards conceded/i.test(gtxt));
  }


  /* ---- THE 2026-11-11 SIMPLIFY PASS ---------------------------------------------------
     The card leads with a verdict strip and every explanatory note is folded to one plain
     line with the working behind a tap. The risk in a pass like this is that "simpler" turns
     into "missing": a caveat deleted rather than folded. So this checks the strip is there,
     that no note is left sprawling, and that every folded note still carries its full text. */
  {
    const h = LAB.glassProbe('nba', fx) || '';
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
  /* A rival now at another team was listed with "+0.17 if out" beside his name. He is out
     of every game for good; the figure invited a read on an absence that is not one. */
  ok('no rival who has left the team is listed',
     !/now at [A-Z]{2,4}/.test((LAB.glassProbe('nba', fx) || '').replace(/<[^>]*>/g, ' ')));
  done();
})();
