/* The odds board's hit-rate columns: the first real test on the rebuilt harness.
   These are the two faults that shipped unverified - an empty board and a frozen page. */
const { boot, ok, done } = require('./harness');

(async () => {
  const t = await boot('nfl');
  await t.focus(0);
  t.LAB.tab('odds');
  await t.wait(1200);

  const rows = Array.from(t.app().querySelectorAll('.od-row'));
  ok('the odds board renders rows', rows.length > 0, '(' + rows.length + ')');

  const hr = rows.length ? Array.from(rows[0].querySelectorAll('.od-hr')) : [];
  ok('each row carries three hit-rate cells', hr.length === 3, '(' + hr.length + ')');

  const withPct = rows.filter(r =>
    Array.from(r.querySelectorAll('.od-hr')).some(c => /%/.test(c.textContent))).length;
  ok('most rows have a real hit rate', withPct >= rows.length * 0.5,
     '(' + withPct + ' of ' + rows.length + ')');

  // the freeze: building the board must not take seconds
  const t0 = Date.now();
  t.LAB.mkt('nfl', 'recYds');
  await t.wait(50);
  const ms = Date.now() - t0;
  ok('switching market is responsive', ms < 4000, '(' + ms + 'ms)');

  done();
})().catch(e => { console.log('  FAIL harness threw: ' + e.message.slice(0, 120)); process.exit(1); });
