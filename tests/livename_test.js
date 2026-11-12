/* OUR NAME FOR A MAN, AND THE LIVE FEED'S NAME FOR HIM
 *
 *   node tests/livename_test.js
 *
 * The tracker finds a player in the live feed by name, and the two sides do not spell him the same
 * way. Our data carries "James Cook"; ESPN's live boxscore carries "James Cook III". The key the
 * tracker matched on strips punctuation but keeps the suffix, so those are three letters apart and
 * a man playing in front of the reader was reported as "not in the live feed for NFL" - with his
 * carries and yards sitting right there in the payload. It runs both ways: 113 of our own NFL
 * players carry a suffix the feed may drop.
 *
 * The obvious fix - throw the suffix away - is wrong, and that is most of what this file is about.
 * Byron Murphy is a Vikings corner and Byron Murphy II is a Seahawks tackle. Chris Smith, DJ
 * Turner, David Long and Michael Carter each have a II or a Jr. who is a DIFFERENT MAN. Five pairs
 * in the NFL list alone collapse into one if the suffix is simply dropped, and the tracker would
 * then put one man's tackles against a bet on the other: a wrong number, stated confidently, in
 * place of the blank it replaced. A blank is recoverable. A confident wrong number is not.
 *
 * So: an exact spelling always wins, and the suffix-tolerant match is only used when it can mean
 * one man. Two candidates and it refuses and says so.
 */
const { boot, ok, done } = require('./harness');

(async () => {
  const t = await boot('nfl');
  await t.focus(0);
  const LAB = t.LAB;
  if (!LAB.liveNameProbe || !LAB.liveFeedProbe) {
    ok('the page exposes the live tracker for testing', false);
    return done();
  }
  ok('the page exposes the live tracker for testing', true);

  /* The real shape, taken from the worker mid-game: Buffalo at the Rams, 2nd quarter. The names
     are exactly as ESPN sent them - "James Cook III" is not a typo, it is the bug. */
  const player = (name, st) => Object.assign({ name }, st);
  const feed = {
    sport: 'nfl',
    markets: ['passYds','passTds','passAtt','passInt','rushYds','rushAtt','receptions','recYds',
              'rushRecYds','anytimeTd','tackles','sacks','kickingPts','fgMade'],
    games: [{
      id: '401872994', state: 'in', status: '5:57 - 2nd', home: 'LAR', away: 'BUF',
      players: [
        player('Josh Allen',      { passYds: 13, passTds: 0, passAtt: 4, rushYds: 21, rushAtt: 3 }),
        player('James Cook III',  { rushYds: 36, rushAtt: 7, receptions: 1, recYds: 7, rushRecYds: 43 }),
        player('Khalil Shakir',   { receptions: 1, recYds: 6, rushRecYds: 6 }),
        player('Puka Nacua',      { rushYds: 9, rushAtt: 2, receptions: 5, recYds: 32, rushRecYds: 41 }),
        player('Davante Adams',   { receptions: 1, recYds: 4, rushRecYds: 4, anytimeTd: 1 }),
        player('Christian Benford', { tackles: 6, sacks: 0 }),
      ]
    }]
  };
  const leg = (name, mkt, line, side) =>
    [{ type: 'single', key: 'nfl', name, mkt, line, side: side || 'over', label: mkt }];
  const row = (name, mkt, line, side) => LAB.liveFeedProbe('nfl', feed, leg(name, mkt, line, side))[0];

  // ---- the bug, exactly as it was reported ------------------------------------------------------
  let r = row('James Cook', 'rushYds', 60.5);
  ok('a man we spell without his suffix is found in a feed that spells him with one',
     r.have === 36 && r.state === 'open',
     '("James Cook" vs "James Cook III" - was: not in the live feed)');
  ok('and his progress bar is drawn against the line',
     Math.abs(r.pct - 36 / 61) < 0.01, '(36 toward 61)');

  // and the other direction, which is the commoner one in our own data
  const feed2 = JSON.parse(JSON.stringify(feed));
  feed2.games[0].players[1].name = 'James Cook';
  r = LAB.liveFeedProbe('nfl', feed2, leg('James Cook III', 'rushYds', 20.5))[0];
  ok('and a man we spell WITH a suffix is found in a feed that drops it',
     r.have === 36 && r.state === 'cashed', '(113 of our NFL names carry one)');

  // ---- an exact spelling is never overruled -----------------------------------------------------
  const both = JSON.parse(JSON.stringify(feed));
  both.games[0].players.push(player('Byron Murphy',    { tackles: 2 }));
  both.games[0].players.push(player('Byron Murphy II', { tackles: 9 }));
  r = LAB.liveFeedProbe('nfl', both, leg('Byron Murphy', 'tackles', 4.5))[0];
  ok('with both men in the feed, the exact spelling wins',
     r.have === 2 && r.state === 'open', '(the corner, not the tackle)');
  r = LAB.liveFeedProbe('nfl', both, leg('Byron Murphy II', 'tackles', 4.5))[0];
  ok('and so does the other exact spelling',
     r.have === 9 && r.state === 'cashed', '(the tackle, not the corner)');

  // ---- and where it cannot tell, it refuses -----------------------------------------------------
  const twoLoose = JSON.parse(JSON.stringify(feed));
  twoLoose.games[0].players.push(player('Chris Smith II',  { tackles: 3 }));
  twoLoose.games[0].players.push(player('Chris Smith Jr.', { tackles: 7 }));
  r = LAB.liveFeedProbe('nfl', twoLoose, leg('Chris Smith', 'tackles', 4.5))[0];
  ok('two men who differ only by suffix are never guessed between',
     r.have == null && r.state === 'untracked',
     '(3 tackles and 7 tackles - picking either is a fabricated number)');
  ok('and the panel says that is why',
     /more than one/.test(r.why || ''), '(' + r.why + ')');

  const look = LAB.liveNameProbe(twoLoose, 'Chris Smith');
  ok('the lookup reports the ambiguity rather than a hit',
     !look.hit && look.loose === 2);

  // ---- a two-word name is never cut down to one -------------------------------------------------
  const shortName = JSON.parse(JSON.stringify(feed));
  shortName.games[0].players.push(player('Dallas Turner', { tackles: 4 }));
  r = LAB.liveFeedProbe('nfl', shortName, leg('Turner', 'tackles', 1.5))[0];
  ok('a bare surname does not match a full name', r.have == null,
     '(stripping a last word must never leave a one-word key)');

  // ---- and a man genuinely absent still says so -------------------------------------------------
  r = row('Nobody McNobody', 'rushYds', 10.5);
  ok('a man who is not in the feed is still reported as not in it',
     r.state === 'untracked' && /not in the live feed/.test(r.why || ''));

  // ---- the rest of the tracker is untouched ------------------------------------------------------
  r = row('Josh Allen', 'passYds', 12.5);
  ok('an exactly-spelled man still tracks', r.have === 13 && r.state === 'cashed');
  r = row('Davante Adams', 'anytimeTd', 0.5);
  ok('an anytime scorer still lands', r.have === 1 && r.state === 'cashed');
  r = row('Puka Nacua', 'recYds', 80.5, 'under');
  ok('an under still reads the right way round', r.have === 32 && r.state === 'open');
  r = row('Khalil Shakir', 'tackles', 1.5);
  ok('a market the feed does not carry for him reads as not started, not as zero',
     r.have == null, '(he is in the feed; that column is not)');

  done();
})();
