#!/usr/bin/env node
/* ============================================================================
 * JTT AFL — settle_signals.js   (runs in the data build, after results land)
 * ----------------------------------------------------------------------------
 * Grades every FROZEN signal against the box score and appends to ledger.json.
 * NEVER recomputes the signal (inputs have moved). Grading is single-source:
 * it calls JTTSignals.grade(), the exact engine tested in Phase 1.
 *
 * Honesty guarantees enforced here:
 *   - a round is only settled once its box scores exist (else left pending)
 *   - DNP / scratch / 0 TOG  -> void, never loss
 *   - an already-settled record is never re-graded (append-only ledger)
 *   - grades the CAPTURED line at the CAPTURED price
 *
 * Usage:
 *   node AFL/settle_signals.js --data AFL/data
 *     [--signals AFL/data/signals] [--gamelogs AFL/data/gamelogs.json]
 *     [--ledger AFL/data/ledger.json]
 * ========================================================================== */
'use strict';
const fs = require('fs');
const path = require('path');
const JTTSignals = require(path.resolve(__dirname, 'signals.js'));

function arg(name, def) { const i = process.argv.indexOf('--' + name); return i > -1 ? process.argv[i + 1] : def; }
const DATA     = arg('data', path.resolve(__dirname, 'data'));
const SIGDIR   = arg('signals', path.join(DATA, 'signals'));
const GAMELOGS = arg('gamelogs', path.join(DATA, 'gamelogs.json'));
const LEDGER   = arg('ledger', path.join(DATA, 'ledger.json'));

const readJSON = f => JSON.parse(fs.readFileSync(f, 'utf8'));
function roundNum(rn) { const m = String(rn || '').match(/(\d+)/); if (m) return parseInt(m[1], 10); if (/opening/i.test(rn || '')) return 0; return null; }

// box-score index: key "season|round|norm名" -> row (played = row present with tog>0)
function normName(n) { return String(n || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z ]/g, '').replace(/\s+/g, ' ').trim(); }

(function () {
  const gamelogs = readJSON(GAMELOGS);
  const box = {};                 // key -> {row, played}
  const roundsPlayed = new Set();  // "season|round" that have ANY box score (=> settleable)
  gamelogs.forEach(r => {
    const season = String(r.Year), rd = roundNum(r.RoundName);
    if (rd == null) return;
    roundsPlayed.add(season + '|' + rd);
    box[season + '|' + rd + '|' + normName(r.Player)] = { row: r, tog: +r.tog };
  });

  const ledger = fs.existsSync(LEDGER) ? readJSON(LEDGER) : { updated: null, records: [] };
  const seen = new Set(ledger.records.map(r => r.id));   // already settled -> never touch again

  if (!fs.existsSync(SIGDIR)) { console.log('no signals dir yet:', SIGDIR); return; }
  const files = fs.readdirSync(SIGDIR).filter(f => /\.json$/.test(f));

  let added = 0, pending = 0, voids = 0;
  files.forEach(f => {
    const pack = readJSON(path.join(SIGDIR, f));
    (pack.records || []).forEach(rec => {
      if (seen.has(rec.id)) return;                       // append-only: never re-settle
      const key = rec.season + '|' + rec.round;
      if (!roundsPlayed.has(key)) { pending++; return; }  // round not played yet -> leave pending
      const b = box[key + '|' + normName(rec.player)];
      const played = !!(b && isFinite(b.tog) && b.tog > 0);
      const actual = played ? +b.row[rec.market] : null;
      const g = JTTSignals.grade(rec, actual, played);
      const settled = Object.assign({}, rec, { result: g.result, actual: g.actual, settledAt: new Date().toISOString() });
      ledger.records.push(settled);
      seen.add(rec.id);
      added++; if (g.result === 'void') voids++;
    });
  });

  ledger.updated = new Date().toISOString();
  fs.writeFileSync(LEDGER, JSON.stringify(ledger, null, 2));

  const roll = JTTSignals.rollup(ledger.records);
  console.log('settled', added, 'new (' + voids + ' void),', pending, 'still pending. ledger total:', ledger.records.length);
  Object.keys(roll).forEach(k => {
    const b = roll[k];
    console.log('  ', k + ':', b.hitRate == null ? '—' : (b.hitRate * 100).toFixed(1) + '%',
      '(' + b.wins + '-' + b.losses + (b.pushes ? '-' + b.pushes + 'p' : '') + ', n=' + b.n + ')',
      (b.units >= 0 ? '+' : '') + b.units.toFixed(2) + 'u', 'roi', b.roi == null ? '—' : (b.roi * 100).toFixed(1) + '%');
  });
})();
