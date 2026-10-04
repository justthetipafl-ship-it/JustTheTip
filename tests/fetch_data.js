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
const DIRS = { afl:'AFL', nfl:'nfl', epl:'EPL', mlb:'mlb', nbl:'nbl', nhl:'nhl' };
// enough for the page to boot and for most tests; gamelog season files are added from meta
const FILES = ['config.js', 'scoring.js'];
const DATA = ['meta.json', 'fixture.json', 'teams.json', 'players.json', 'odds.json',
              'gamelogs.json', 'dvp.json', 'results.json', 'injury.json', 'lineups.json',
              'calib.json', 'firstread.json', 'redzone.json', 'firsttd.json', 'goalies.json'];

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
