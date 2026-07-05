#!/usr/bin/env node
/* ============================================================================
 * JTT AFL — capture_signals.js   (runs in GitHub Actions, ~1-2h pre-slate)
 * ----------------------------------------------------------------------------
 * Freezes exactly which signals fired for the upcoming round, at what line/price,
 * BEFORE the games. Signals are recomputed live from moving inputs, so this is the
 * only honest moment to snapshot them. Uses the SAME scoring.js + signals.js the
 * browser uses (one code path), so the ledger grades the model users actually saw.
 *
 * Writes AFL/data/signals/<season>-R<round>.json  (idempotent — re-runs overwrite).
 *
 * Usage:
 *   node AFL/capture_signals.js \
 *     --data AFL/data \
 *     --odds https://<worker>.workers.dev/odds.json   # or a local odds.json path
 *     --out  AFL/data/signals
 *   (season/round default to meta.json; override with --season / --round)
 * ========================================================================== */
'use strict';
const fs = require('fs');
const path = require('path');
const https = require('https');

// ---- scoring.js needs a browser-ish global; shim it, then load (no edit to scoring.js) ----
global.window = {};
require(path.resolve(__dirname, 'scoring.js'));        // sets window.JTTScoring
const JTTScoring = global.window.JTTScoring;
const JTTSignals = require(path.resolve(__dirname, 'signals.js'));

function arg(name, def) { const i = process.argv.indexOf('--' + name); return i > -1 ? process.argv[i + 1] : def; }
const DATA = arg('data', path.resolve(__dirname, 'data'));
const OUT  = arg('out',  path.join(DATA, 'signals'));
const ODDS = arg('odds', path.join(DATA, 'odds.json'));

const readJSON = f => JSON.parse(fs.readFileSync(f, 'utf8'));
function fetchJSON(url) {
  return new Promise((res, rej) => {
    https.get(url, r => {
      if (r.statusCode >= 400) return rej(new Error('HTTP ' + r.statusCode + ' ' + url));
      let d = ''; r.on('data', c => d += c); r.on('end', () => { try { res(JSON.parse(d)); } catch (e) { rej(e); } });
    }).on('error', rej);
  });
}

// gamelog RoundName "Round 17" / "Opening Round" -> numeric key (mirrors the tool's _roundNum)
function roundNum(rn) {
  const s = String(rn || '');
  const m = s.match(/(\d+)/);
  if (m) return parseInt(m[1], 10);
  if (/opening/i.test(s)) return 0;
  return null;
}

(async () => {
  const players   = readJSON(path.join(DATA, 'players.json'));
  const teams     = readJSON(path.join(DATA, 'teams.json'));
  const teamsForm = fs.existsSync(path.join(DATA, 'teams_form.json')) ? readJSON(path.join(DATA, 'teams_form.json')) : teams;
  const dvp       = readJSON(path.join(DATA, 'dvp.json'));
  const gamelogs  = readJSON(path.join(DATA, 'gamelogs.json'));
  const fixture   = readJSON(path.join(DATA, 'fixture.json'));
  const meta      = readJSON(path.join(DATA, 'meta.json'));

  const season = String(arg('season', (meta && meta.currentSeason) || '2026'));
  const round  = parseInt(arg('round', (meta && meta.round) || 0), 10);

  // logsByName (group gamelogs by Player) — same shape configure() expects
  const logsByName = {};
  gamelogs.forEach(r => { const n = r.Player; if (!n) return; (logsByName[n] = logsByName[n] || []).push(r); });

  JTTScoring.configure({ players, teams, teamsForm, dvp, logsByName, currentSeason: season });

  // odds: URL (live Worker, freshest) or local file
  const oddsJson = /^https?:\/\//.test(ODDS) ? await fetchJSON(ODDS) : readJSON(ODDS);
  if (oddsJson && oddsJson._sample) console.warn('WARNING: odds snapshot is flagged _sample — capture will be sparse.');
  const oddsLk = JTTSignals.buildOddsLookup(oddsJson, players);
  const { oddsFor, priceForLine, snapToRung } = oddsLk;
  const logsFor = n => logsByName[n] || [];

  // deps for the generators
  const nextOppMap = {};
  fixture.forEach(g => { nextOppMap[g.home] = g.away; nextOppMap[g.away] = g.home; });
  const byTeam = {};
  players.forEach(p => { (byTeam[p.team] = byTeam[p.team] || []).push(p); });

  const deps = {
    JTTScoring,
    oddsFor, priceForLine, snapToRung, logsFor,
    nextOpp: t => nextOppMap[t] || null,
    playersOnTeam: t => byTeam[t] || [],
    hasAnyOdds: () => !!(oddsJson && ((oddsJson.lines || []).length)),
    SIGNAL_MIN_ODDS: { over: 1.70, under: null },  // must match index.html's SIGNAL_MIN_ODDS
    abbr: t => String(t || '').slice(0, 3).toUpperCase(),
    curSeason: () => season
  };
  const sig = JTTSignals.create(deps);

  // team list = teams playing the upcoming round (from fixture)
  const teamList = [];
  fixture.forEach(g => { [g.home, g.away].forEach(t => { if (teamList.indexOf(t) < 0) teamList.push(t); }); });

  const ctx = { season, round, capturedAt: new Date().toISOString() };
  const records = sig.captureOU(teamList, ctx)
    .concat(sig.captureMatchup(fixture, ctx));   // Green Lights / Death Riders + Matchup Multi legs

  fs.mkdirSync(OUT, { recursive: true });
  const outFile = path.join(OUT, season + '-R' + round + '.json');
  const payload = {
    season, round, capturedAt: ctx.capturedAt,
    oddsUpdated: (oddsJson && oddsJson.updated) || null,
    fixture: fixture.map(g => ({ home: g.home, away: g.away })),
    count: records.length,
    records
  };
  fs.writeFileSync(outFile, JSON.stringify(payload, null, 2));

  const byType = {};
  records.forEach(r => { byType[r.signalType] = (byType[r.signalType] || 0) + 1; });
  console.log('captured', records.length, 'signals for', season, 'R' + round, '->', outFile);
  Object.keys(byType).forEach(k => console.log('  ', k + ':', byType[k]));
})().catch(e => { console.error('capture failed:', e); process.exit(1); });
