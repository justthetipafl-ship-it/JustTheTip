#!/usr/bin/env node
/* Populate tests/serve/ with each sport's real data, so the harness runs against the same files
 * the live page does.
 *
 *   node tests/fetch_data.js            # every sport
 *   node tests/fetch_data.js nfl nhl    # just these
 *
 * In CI the repo is already checked out, so this COPIES from the working tree rather than
 * downloading. Run with --remote to pull from GitHub instead (useful on a fresh machine).
 */
const fs = require('fs');
const path = require('path');

const REPO = path.join(__dirname, '..');
const OUT = path.join(__dirname, 'serve');
const RAW = 'https://raw.githubusercontent.com/justthetipafl-ship-it/JustTheTip/main';
const DIRS = { afl:'AFL', nfl:'nfl', epl:'EPL', mlb:'mlb', nbl:'nbl', nhl:'nhl',
               // NBA was missing here while nba_test.js existed, so that test could never
               // get data and failed with "unknown sport" rather than with a real result.
               nba:'nba' };
// enough for the page to boot and for most tests; gamelog season files are added from meta
const FILES = ['config.js', 'scoring.js'];
const DATA = ['meta.json', 'fixture.json', 'teams.json', 'players.json', 'odds.json',
              'gamelogs.json', 'dvp.json', 'results.json', 'injury.json', 'lineups.json',
              'calib.json', 'firstread.json', 'redzone.json', 'firsttd.json', 'goalies.json',
              // Tier-3 files the page loads but this stager never fetched, so every test that
              // needed one failed with "absent" and read as a page bug. nba_test's zone and
              // quarter checks are the clearest case: they could not have passed. A sport that
              // has no such file still 404s harmlessly here, exactly as goalies.json already did.
              'zones.json', 'quarters.json', 'special_teams.json', 'teams_form.json',
              'odds_open.json', 'profiles.json', 'volumes.json',
              // dna.json drives the league strand table AND dna_test. It was never staged, so that
              // test could only ever report "no DNA table" and skip every check it owns.
              'dna.json',
              // the shrinkage factors the shot matchup applies - without them the page falls back
              // to its baked-in defaults and a test could not tell the difference
              'zone_persistence.json',
              // Two more declared in a sport's config.dataFiles and never staged here. A file in
              // that list is one the page really loads, so leaving it out does not make a test
              // fail loudly - it makes the feature quietly absent and the test green about a
              // card that was never drawn. ground.json feeds NFL's Ground & Air; pickem.json the
              // Dabble Pick'Em comparison, whose whole test is about which fixture a line lands
              // on. A sport without either still 404s harmlessly, as goalies.json already does.
              'ground.json', 'pickem.json',
              // the on/off intervals behind the rotation chart; NBA only, 404s elsewhere
              'stints.json',
              // the passing networks behind Playmaking; NBA only, 404s elsewhere
              'assists.json',
              // and the glass behind Rebounding, likewise
              'rebounds.json',
              // and the shooting behind From Deep
              'threes.json'];

const remote = process.argv.includes('--remote');
const want = process.argv.slice(2).filter(a => !a.startsWith('--'));
const sports = want.length ? want : Object.keys(DIRS);

async function grab(rel, dest) {
  if (!remote) {
    const src = path.join(REPO, rel);
    if (!fs.existsSync(src)) return false;
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    fs.copyFileSync(src, dest);
    return true;
  }
  const res = await fetch(RAW + '/' + rel);
  if (!res.ok) return false;
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.writeFileSync(dest, Buffer.from(await res.arrayBuffer()));
  return true;
}

(async () => {
  for (const sp of sports) {
    const dir = DIRS[sp];
    if (!dir) { console.log('  unknown sport: ' + sp); continue; }
    let got = 0;
    for (const f of FILES) if (await grab(dir + '/' + f, path.join(OUT, dir, f))) got++;
    for (const f of DATA) if (await grab(dir + '/data/' + f, path.join(OUT, dir, 'data', f))) got++;
    /* EVERYTHING ELSE IN THE FOLDER. The list above is kept by hand and has been wrong five times:
       each time a builder started writing a new file, the page loaded it, and this stager did
       not - so the feature was quietly absent under test and the test was green, or red, about
       a card that was never drawn. playtypes.json was the latest (the shot-profile picker found
       nobody, and assists_test failed on a page that was fine). When the repo is on disk there
       is no reason to guess: stage every top-level data file. The list still serves --remote,
       where there is no folder to read. */
    if (!remote) {
      let names = [];
      try { names = fs.readdirSync(path.join(REPO, dir, 'data')); } catch (e) {}
      for (const f of names) {
        if (!/\.(json|txt)$/.test(f)) continue;
        const dest = path.join(OUT, dir, 'data', f);
        if (fs.existsSync(dest) && DATA.indexOf(f) >= 0) continue;      // already staged above
        if (await grab(dir + '/data/' + f, dest)) got++;
      }
    }
    // the per-season gamelog files the page actually loads
    try {
      const meta = JSON.parse(fs.readFileSync(path.join(OUT, dir, 'data', 'meta.json'), 'utf8'));
      for (const f of (meta.gamelogFiles || []))
        if (await grab(dir + '/data/' + f, path.join(OUT, dir, 'data', f))) got++;
    } catch (e) {}
    console.log('  ' + sp.padEnd(4) + ' ' + got + ' file(s) -> tests/serve/' + dir);
  }
  // shared files the page fetches at the root
  for (const f of ['data/ladder.json', 'data/signal_report.json'])
    await grab(f, path.join(OUT, f));
  console.log('done. Run: node tests/harness.js');
})();
