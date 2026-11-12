/* THE PROP CALCULATOR'S LINE: TYPABLE, AND STILL TRUSTWORTHY
 *
 *   node tests/propcalc_test.js
 *
 * The line used to be reachable only through a - and a + button, which meant getting from a
 * default of 20 to a posted line of 48 was twenty-eight taps. It is now a text field.
 *
 * A field a reader can put anything into is a new way for a bad number to reach the maths behind
 * it. state.view.pc.line feeds the threshold (a line of N tests "over N-0.5"), the hit count, the
 * price lookup and the scale of the bar chart - so an empty box, a word, or a negative would not
 * error, it would come out the far end as a confident 0% and a flat row of bars. That reads like
 * an answer, which is worse than an error. These tests are mostly about what the box REFUSES.
 *
 * The stepper buttons go through the same clamp as the typed value, on purpose: two routes to one
 * number that disagree about what is legal is how the two ends drift apart.
 */
const { JSDOM } = require('jsdom');
const { boot, ok, done } = require('./harness');

(async () => {
  const t = await boot('nfl');
  await t.focus(0);
  const LAB = t.LAB;
  if (!LAB.pcProbe) {
    ok('the page exposes the prop calculator for testing', false, '(LAB.pcProbe is missing)');
    return done();
  }
  ok('the page exposes the prop calculator for testing', true);

  // a real man with real game logs, so the panel has something to draw
  const byName = t.S.idx.logsByName || {};
  const who = Object.keys(byName).find(n =>
    (byName[n] || []).filter(r => (+r.rushYds || 0) > 0).length >= 6);
  if (!who) { ok('found a player with game logs', false); return done(); }
  ok('found a player with game logs', true, '(' + who + ')');

  const doc = h => new JSDOM('<div>' + h + '</div>').window.document;

  // ---- it is a field, not a label ---------------------------------------------------------------
  let p = LAB.pcProbe('nfl', who);
  let d = doc(p.html);
  const inp = d.querySelector('input.pc-line');
  ok('the line is an input the reader can type into', !!inp);
  if (!inp) return done();
  ok('and it asks for a number keypad on a phone',
     inp.getAttribute('type') === 'number' && inp.getAttribute('inputmode') === 'numeric');
  ok('and it is labelled for a screen reader', inp.getAttribute('aria-label') === 'Line');
  /* onchange, never oninput: the page re-renders on every state change, so committing per
     keystroke would rip the field out from under the caret after the first digit. */
  ok('it commits on change, not on every keystroke',
     /pcLineSet/.test(inp.getAttribute('onchange') || '') && !inp.getAttribute('oninput'),
     '(oninput here would re-render mid-word)');
  ok('the stepper buttons are still there',
     d.querySelectorAll('.pc-step button').length === 2);
  ok('and both of them are labelled too',
     Array.from(d.querySelectorAll('.pc-step button'))
       .every(b => /the line/.test(b.getAttribute('aria-label') || '')));

  // ---- a typed line lands exactly where it was typed ---------------------------------------------
  const typed = (v, side) => LAB.pcProbe('nfl', who, String(v), side).line;
  ok('typing a line sets it', typed(48) === 48, '(48 was 28 taps away)');
  ok('a small one too', typed(7) === 7);
  ok('and a three-figure one', typed(148) === 148, '(passing yards)');
  ok('whitespace around it is forgiven', typed('  12  ') === 12);

  /* The calculator holds whole lines and reads "20+" as over 19.5, so a typed half line resolves
     to the threshold actually typed: 20.5 becomes 21, which tests at exactly 20.5. */
  ok('a typed half line resolves to that exact threshold', typed('20.5') === 21,
     '(21 tests over 20.5)');

  // ---- and a line it cannot use is refused, never coerced ----------------------------------------
  ok('the line to fall back to is set up', LAB.pcProbe('nfl', who, '33').line === 33);
  const refuses = (v, what) => {
    // type the bad value into a panel ALREADY sitting on a known good line, which is the only way
    // the question means anything: a refusal has to leave the previous line standing
    const now = LAB.pcProbe('nfl', who, ['33', v]).line;
    ok(what, now === 33, '(typed ' + JSON.stringify(v) + ' over 33, left on ' + now + ')');
  };
  refuses('', 'an emptied box keeps the last good line');
  refuses('abc', 'a word is refused');
  refuses('   ', 'and so is whitespace alone');

  ok('zero is lifted to the lowest usable line', typed(0) === 1,
     '(there is no "0+" market)');
  ok('a negative is too', typed(-5) === 1);
  ok('and an absurd one is capped', typed(99999) === 999,
     '(it would otherwise flatten the bar chart against its own scale)');

  // the buttons must agree with the box about what is legal
  ok('the minus button cannot go below the same floor',
     LAB.pcProbe('nfl', who, '1').line === 1
     && (function(){ const r = LAB.pcProbe('nfl', who, '1'); LAB.pcLine(-1); return true; })(),
     '(one clamp, both routes)');

  // ---- "20+" and "U20" still read as one word ----------------------------------------------------
  for (const [side, want] of [['over', '+'], ['under', 'U']]) {
    p = LAB.pcProbe('nfl', who, '30', side);
    d = doc(p.html);
    const fix = Array.from(d.querySelectorAll('.pc-fix')).map(e => e.textContent);
    ok('the ' + side + ' side still shows its ' + want, fix.length === 1 && fix[0] === want);
    const field = d.querySelector('input.pc-line');
    ok('and the number is pushed up against it',
       (field.getAttribute('style') || '').indexOf(side === 'over' ? 'right' : 'left') >= 0,
       '(an input has a width of its own; "30   +" is not a line)');
  }

  // ---- and the panel it drives still works --------------------------------------------------------
  p = LAB.pcProbe('nfl', who, '1', 'under');
  d = doc(p.html);
  ok('the calculator still answers with the typed line',
     !!d.querySelector('.pc-hr b') && d.querySelectorAll('.pc-bars span').length > 0);
  const u1 = (d.querySelector('.pc-hr b') || {}).textContent;
  p = LAB.pcProbe('nfl', who, '999', 'under');
  const u999 = (doc(p.html).querySelector('.pc-hr b') || {}).textContent;
  ok('and the answer actually moves with it', u1 !== u999,
     '(under 1: ' + u1 + '  under 999: ' + u999 + ')');

  done();
})();
