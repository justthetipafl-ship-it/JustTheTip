/* A THREES LINE IS A VOLUME BET, AND THE CARD HAS TO SAY SO
 *
 *   node tests/threes_test.js
 *
 * Three things were measured before this card was built, and the card exists to report them
 * rather than to decorate a number:
 *
 *   1. Split-half over 297 shooters: attempts carry at r=+0.855, made at +0.802, PERCENTAGE at
 *      +0.306. How many he lets go persists; how well he shoots barely does.
 *   2. Over 15,048 player-games, recent 3P% predicts the next game's 3P% at r=-0.0275, and the
 *      hottest fifth of those runs then shoot 1.9 points BELOW their own rate. Recent attempts
 *      predict next attempts at +0.522. A hot streak is the commonest reason anyone takes a
 *      threes over and it points slightly the wrong way.
 *   3. The creator read fails, exactly as the passing network did: top feeder out moved a
 *      shooter +0.006 threes against a +0.084 control, and the catch-and-shoot specialists
 *      (>=90% assisted) came out at +0.024, no worse than the self-creators at -0.014.
 *
 * So these checks are mostly about WORDS, deliberately. A card that printed a hot streak without
 * that verdict would be worse than no card, because the reader would supply the usual conclusion
 * themselves. They also pin the one thing the layout must not do: rank the opponent's
 * three-point percentage allowed, which is worth r=-0.016.
 */
const { boot, ok, done } = require('./harness');

(async () => {
  const t = await boot('nba');
  await t.focus(0);
  const LAB = t.LAB;

  if (!LAB.threeProbe) { ok('the page exposes From Deep for testing', false); return done(); }
  ok('the page exposes From Deep for testing', true);

  const S = LAB.state.sports.nba;
  const tf = S.data.threes;
  ok('threes.json is loaded', !!tf && !!tf.players);
  if (!tf || !tf.players) return done();
  ok('with a usable pool (' + tf.players.length + ')', tf.players.length >= 200);
  ok('and a concession table (' + Object.keys(tf.defence || {}).length + ')',
     Object.keys(tf.defence || {}).length >= 25);

  /* THE MEASUREMENTS THE CARD QUOTES must live in the file, not be typed into the page - and
     they must still say what the card claims. If a rebuild ever reported a stable percentage,
     the card's whole argument would be wrong and this should fail rather than the page quietly
     printing a contradiction. */
  const st = (tf.league || {}).stability || {};
  ok('the split-half stability is in the file', st.att != null && st.pct != null);
  ok('attempts are the stable half (' + st.att + ')', st.att > 0.7);
  ok('and percentage is not (' + st.pct + ')', st.pct < 0.5);
  ok('which is the gap the card is built on', st.att - st.pct > 0.3);
  const rc = (tf.league || {}).recent || {};
  ok('the hot-hand measurement is carried', rc.pctToPct != null && rc.attToAtt != null);
  ok('recent percentage does not predict the next game (' + rc.pctToPct + ')', rc.pctToPct < 0.05);
  ok('recent attempts do (' + rc.attToAtt + ')', rc.attToAtt > 0.4);
  const co = (tf.league || {}).creatorOut || {};
  ok('and so is the creator-absence null', co.gain != null && co.control != null);
  ok('with the catch-and-shoot group no worse than the self-creators ('
     + co.catchShoot + ' v ' + co.selfCreate + ')', co.catchShoot >= co.selfCreate);

  /* THE DATA. */
  const bad = [];
  tf.players.forEach((p) => {
    if (p.made > p.att + 0.01) bad.push(p.name + ' makes exceed attempts');
    if (p.pct != null && (p.pct < 0 || p.pct > 1)) bad.push(p.name + ' pct ' + p.pct);
    if (p.assistedShare != null && (p.assistedShare < 0 || p.assistedShare > 1))
      bad.push(p.name + ' assisted ' + p.assistedShare);
    if ((p.creators || []).some((c) => c.id === p.playerId)) bad.push(p.name + ' creates his own');
    if ((p.creators || []).some((c, i) => i && p.creators[i - 1].n < c.n))
      bad.push(p.name + ' creators out of order');
  });
  ok('every profile is internally consistent' + (bad.length ? ' - ' + bad.slice(0, 3).join('; ') : ''),
     bad.length === 0);
  /* The assisted share must span a real range, or it is not telling the reader anything about
     what kind of shooter a player is. */
  const shares = tf.players.filter((p) => p.assistedShare != null).map((p) => p.assistedShare);
  ok('the assisted share spans a real range (' + Math.round(Math.min.apply(null, shares) * 100)
     + '% to ' + Math.round(Math.max.apply(null, shares) * 100) + '%, n=' + shares.length + ')',
     shares.length > 100 && Math.max.apply(null, shares) - Math.min.apply(null, shares) > 0.4);

  /* THE OPPONENT. Attempts and made are ranked; the percentage allowed must NOT be. */
  const anyD = tf.defence[Object.keys(tf.defence)[0]];
  ok('attempts and makes allowed are ranked', anyD.attRank >= 1 && anyD.madeRank >= 1);
  ok('the percentage allowed is carried', anyD.pct != null);
  ok('but never ranked, because out of sample it is worth r=-0.016',
     Object.keys(tf.defence).every((c) => tf.defence[c].pctRank === undefined));

  const fx = (S.data.fixture || [])[0];
  const cands = LAB.t3CandsProbe('nba', fx);
  ok('the picker offers the shooters in this game (' + cands.length + ')', cands.length >= 4);
  ok('best shooter first', cands.every((c, i) => i === 0 || cands[i - 1].rec.made >= c.rec.made));

  /* RECENT FORM comes off the logs the page already holds, not the file - it changes nightly. */
  const r5 = LAB.t3RecentProbe('nba', cands[0].name, 5);
  ok('the recent window is read from the gamelogs', !!r5 && r5.games > 0 && r5.games <= 5);
  ok('and carries attempts as well as the percentage',
     !!r5 && r5.att >= 0 && (r5.pct === null || (r5.pct >= 0 && r5.pct <= 1)));

  const html = LAB.threeProbe('nba', fx);
  ok('the section renders', !!html && html.length > 400);
  if (!html) return done();
  const txt = html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');

  ok('it leads with what can be forecast', /How much of this you can forecast/.test(txt));
  ok('it labels attempts as the half that holds up', /holds up r=\+0\.\d\d/.test(txt));
  ok('and the percentage as the half that barely does', /barely holds r=\+0\.\d\d/.test(txt));
  ok('it shows his recent run', /His last \d/.test(txt));
  ok('it says what kind of shooter he is', /What kind of shooter he is/.test(txt));
  ok('including how much is created for him', /of his makes/.test(txt));
  /* ...and WITHOUT calling a low share worse. Making your own threes is a kind of shooter, not
     a failing, so it must not be printed in the red that means "below par" elsewhere here. */
  ok('described as a kind of shooter rather than graded',
     /more of a catch-and-shoot player|makes more of his own/.test(txt));
  ok('and the share is not coloured as good or bad',
     !/class="[^"]*\bdn\b[^"]*">[^<]*v league \u00b7/.test(html));
  ok('and where he shoots from', /From the corner/.test(txt) && /Above the break/.test(txt));
  ok('it breaks the threes down by quarter', /By quarter/.test(txt));
  /* ATTEMPTS AND MAKES MUST BE DISTINGUISHED. The first version labelled the attempts figure
     "threes allowed a game", which read as though Portland concede 34.6 made threes rather than
     34.6 attempts - a wrong number on the page, not a wording quibble. */
  ok('and what the opponent allows, in attempts',
     /three-point ATTEMPTS allowed a game/.test(txt));
  ok('with the makes shown separately, so the two cannot be read as the same figure',
     /of them go in/.test(txt) && /made allowed/.test(txt));
  const dC = tf.defence[fx.away] || tf.defence[fx.home];
  ok('and the attempts figure really is the bigger of the two ('
     + (dC ? dC.att + ' v ' + dC.made : '?') + ')', !!dC && dC.att > dC.made * 2);

  /* THE HONESTY, which is most of the point of this card. */
  ok('it calls a threes line a volume bet',
     /volume bet wearing a shooting bet/.test(txt));
  ok('and gives both stability figures so the claim can be checked',
     /attempts carry from one half of his season/.test(txt));
  ok('it says the creators are context, not a read',
     /players who create his threes are context, not a read/.test(txt));
  ok('backed by the measured null rather than a hedge',
     /against a \+0\.0\d\d control/.test(txt));
  ok('it calls the opponent block the weakest on any of these cards',
     /weakest block on any of these cards/.test(txt));
  ok('and prints the percentage allowed without a rank, saying why',
     /not ranked/.test(txt) && /worth nothing/.test(txt));

  /* THE VERDICT ON A STREAK. Whichever state the man on screen is in, the card must say what it
     is worth - a streak printed bare is the one thing this card must not do. */
  ok('the recent block carries a verdict, not just two numbers',
     /wrong reason to take an over/.test(txt) || /not a reason either/.test(txt)
     || /about his own rate/.test(txt));
  ok('and always states that attempts are the part that carries',
     /recent volume predicts his next game/.test(txt));

  /* Find a man who is actually hot and require the explicit warning. */
  let hot = null;
  for (const c of cands) {
    const r = LAB.t3RecentProbe('nba', c.name, 5);
    if (r && r.pct != null && c.rec.pct != null && (r.pct - c.rec.pct) >= 0.05) { hot = c; break; }
  }
  if (hot) {
    LAB.ptName(hot.name);
    const h2 = (LAB.threeProbe('nba', fx) || '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');
    ok('a shooter on a hot run is flagged as the wrong reason to bet (' + hot.name + ')',
       /He is hot, and that is the wrong reason to take an over/.test(h2));
    ok('with the measured reversion beside it, not just a warning',
       /points BELOW their own rate/.test(h2));
    LAB.ptName(cands[0].name);
  } else {
    ok('no shooter in this fixture is on a hot run, so the warning could not be checked', true);
  }

  const t2 = await boot('nfl');
  await t2.focus(0);
  ok('a sport without the file renders nothing at all',
     !t2.LAB.threeProbe('nfl', (t2.LAB.state.sports.nfl.data.fixture || [])[0]));


  /* ---- THE 2026-11-11 SIMPLIFY PASS ---------------------------------------------------
     The card leads with a verdict strip and every explanatory note is folded to one plain
     line with the working behind a tap. The risk in a pass like this is that "simpler" turns
     into "missing": a caveat deleted rather than folded. So this checks the strip is there,
     that no note is left sprawling, and that every folded note still carries its full text. */
  {
    const h = LAB.threeProbe('nba', fx) || '';
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
  done();
})();
