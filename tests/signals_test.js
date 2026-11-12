/* THE SIGNAL RECORD: DOES THE PAGE SAY WHAT THE GRADER FOUND?
 *
 *   node tests/signals_test.js
 *
 * data/signal_report.json is the contract between scripts/grade_signals.py and the "How the
 * signals have done" table. The table is the single most load-bearing number on the site - it is
 * the thing a subscriber judges the tool by - and it has already been wrong in a way nobody could
 * see from the page.
 *
 * What went wrong: a sport whose rows had all been written off had no line in the table, and the
 * table said nothing about it. Eight MLB rows were voided for a gamelog feed that stops at the end
 * of the regular season, and the page simply showed NHL and looked complete. So the case these
 * tests care about most is not the arithmetic - it is that a sport with nothing settled is still
 * NAMED, and that a thin sample is still marked thin.
 *
 * The grader's own rules are checked by `python3 scripts/grade_signals.py --selftest`, and the two
 * are deliberately kept apart: that one proves the verdicts, this one proves the page repeats them
 * without quietly dropping any.
 */
const path = require('path');
const { execFileSync } = require('child_process');
const fs = require('fs');
const { boot, ok, done } = require('./harness');

const GRADER = path.resolve(__dirname, '..', 'scripts', 'grade_signals.py');

(async () => {
  const t = await boot('nfl');
  const LAB = t.LAB;
  if (!LAB.sigRecProbe) {
    ok('the page exposes the signal record for testing', false, '(LAB.sigRecProbe is missing)');
    return done();
  }
  ok('the page exposes the signal record for testing', true);

  const tile = (sport, name, w, l, v, u) =>
    ({ sport, tile: name, n: w + l + v, w, l, v, u });

  // ---- a sport that has settled nothing is still named -----------------------------------------
  let r = LAB.sigRecProbe({
    updated: '2026-10-12T07:57Z',
    tiles: [tile('nhl', 'Streakers', 13, 6, 0, 1.86)],
    waiting: { mlb: { open: 8, stalled: 8 }, epl: { open: 51, stalled: 0 } }
  });
  ok('a sport with rows but nothing settled is named on the page',
     /MLB/.test(r.html) && /EPL/.test(r.html),
     '(8 voided MLB rows once vanished from this table entirely)');
  ok('and the ones stuck behind a gamelog feed say so',
     /waiting on the game logs/.test(r.html));
  ok('and they are excluded from the figures out loud',
     /not counted above/.test(r.html));
  ok('the settled sport still reports its own numbers',
     /Streakers/.test(r.html) && /19/.test(r.html) && /68%/.test(r.html));

  // ---- nothing settled at all: a band and an explanation, not a blank ---------------------------
  r = LAB.sigRecProbe({ updated: '2026-10-12T07:57Z', tiles: [],
                        waiting: { mlb: { open: 8, stalled: 8 } } });
  ok('with nothing settled anywhere the page explains itself',
     /Nothing has settled yet/.test(r.html) && /MLB/.test(r.html),
     '(rather than rendering an empty section)');

  // ---- and with genuinely nothing to say, it says nothing ---------------------------------------
  r = LAB.sigRecProbe({ updated: null, tiles: [] });
  ok('an empty report renders nothing at all', r.html === '',
     '(no band, no table, no note)');
  r = LAB.sigRecProbe({ updated: null, tiles: [], waiting: { nhl: { open: 0, stalled: 0 } } });
  ok('and a sport with zero open rows is not announced', r.html === '');

  // ---- a thin sample is marked thin ------------------------------------------------------------
  r = LAB.sigRecProbe({ updated: 'x', tiles: [tile('nhl', 'Power Play', 1, 1, 0, -1)] });
  ok('a tile with a handful of bets is flagged as too few to read',
     /too few to read/.test(r.html) && /sr-young/.test(r.html),
     '(2 bets is not a hit rate)');

  // ---- a void is never counted as a loss -------------------------------------------------------
  r = LAB.sigRecProbe({ updated: 'x', tiles: [tile('nhl', 'T', 5, 5, 40, 0)] });
  ok('voids are kept out of the bet count the table prints',
     />10</.test(r.html), '(50 rows, 10 of them decided)');

  /* ---- UNITS THE LEDGER DOES NOT HAVE ----------------------------------------------------------
     Elite Matchups renders its odds box as "270+ $3.05" - the line, then the price - and the
     snapshot's price pattern had an optional dollar sign, so it read 270 as the price. Two winners
     paid 269-to-1 and 37-to-1 and the published table said Elite Matchups NFL was +305.0u at
     +10167% ROI. The price at capture is gone, so the honest report is a dash, not a zero: a zero
     reads as a break-even signal, which is a claim. */
  const unpriced = (sport, name, w, l, u, p) =>
    ({ sport, tile: name, n: w + l, w, l, v: 0, u, p });
  r = LAB.sigRecProbe({ updated: 'x', tiles: [
    unpriced('nfl', 'Elite Matchups', 2, 1, 0, 0),
    unpriced('nhl', 'Streakers', 1, 0, 0.9, 1)
  ] });
  ok('a tile whose rows carry no price shows a dash, not a number',
     r.byTile['nfl|Elite Matchups'].priced === 0
     && r.byTile['nfl|Elite Matchups'].roi === null,
     '(a 0.0u would read as break-even, which is a claim about what it paid)');
  ok('and it still reports its bets and hit rate',
     /67%/.test(r.html) && r.byTile['nfl|Elite Matchups'].decided === 3,
     '(the signals happened; only the return is unknown)');
  ok('a priced tile beside it is unaffected',
     Math.abs(r.byTile['nhl|Streakers'].roi - 0.9) < 1e-9);
  ok('and the note explains the dash', /price those signals were posted at was not/.test(r.html));

  /* A report written before the priced count existed meant "all of them priced" at the time, and
     must keep reading that way rather than silently turning every old tile into a dash. */
  r = LAB.sigRecProbe({ updated: 'x', tiles: [tile('nhl', 'Streakers', 13, 6, 0, 1.86)] });
  ok('a report from before the price count is read as fully priced',
     r.byTile['nhl|Streakers'].priced === 19 && r.byTile['nhl|Streakers'].roi != null);

  // ---- the shape the grader actually emits -----------------------------------------------------
  if (!fs.existsSync(GRADER)) {
    ok('scripts/grade_signals.py is present', false, '(' + GRADER + ')');
    return done();
  }
  let out = '';
  try {
    out = execFileSync('python3', ['-I', GRADER, '--selftest'], { encoding: 'utf8' });
    ok('the grader passes its own selftest', !/FAIL/.test(out));
  } catch (e) {
    ok('the grader passes its own selftest', false,
       '(' + String(e.stdout || e.message).split('\n').filter(l => /FAIL/.test(l)).join('; ').slice(0, 200) + ')');
  }

  /* The field the whole fix rests on. Every row the snapshot writes now carries the lead time it
     was taken at, because the window is wide and the fixed lead is a recorded fact rather than
     something a punctual cron enforces. If the snapshot ever stops writing it, the ledger silently
     goes back to being untraceable - so it is asserted here, against the real script. */
  const SNAP = path.resolve(__dirname, '..', 'scripts', 'snapshot_signals.js');
  if (fs.existsSync(SNAP)) {
    const src = fs.readFileSync(SNAP, 'utf8');
    ok('the snapshot records the lead time it captured each row at', /lead:\s*Math\.round/.test(src));
    ok('and the fixture id the dateless feeds are graded by', /matchId:\s*mid/.test(src));
    ok('and it replaces a game whole rather than mixing two passes',
       /log\.rows\.filter\(r => gameKey\(r\) !== gk\)/.test(src));
  } else {
    ok('scripts/snapshot_signals.js is present', false);
  }

  done();
})();
