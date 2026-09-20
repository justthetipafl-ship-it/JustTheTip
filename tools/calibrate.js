/* Walk-forward calibration of each sport's scoring model.
 *
 * For each of the last N match-days in the current season: configure the sport's own
 * scoring.js with ONLY the games that happened before that day, ask it for a probability
 * on a line it generates itself (drLine), then score it against what actually happened.
 * No look-ahead: the model never sees the day it is predicting.
 *
 * Reports mean predicted vs actual, Brier score, and a calibration table by decile.
 */
const fs = require('fs'), vm = require('vm'), path = require('path');
// Repo root: this file lives at tools/calibrate.js, so data sits at ../<SPORT>/data.
// Override with JTT_ROOT to run it against a checkout somewhere else.
const ROOT = process.env.JTT_ROOT || path.resolve(__dirname, '..');
const SERVE = ROOT;
const WRITE = process.env.CALIB_WRITE !== '0';      // set CALIB_WRITE=0 to report without committing
const SPORTS = [['afl','AFL'], ['nfl','nfl'], ['epl','EPL'], ['mlb','mlb']];
const TEST_DAYS = 8, MAX_MARKETS = 5;

const COVER = { epl: 'passes' };
function statOf(key, row, mkt){
  const v = row ? row[mkt] : null;
  if (v != null && isFinite(+v)) return +v;
  const cf = COVER[key];
  if (cf && row && row[cf] != null && isFinite(+row[cf])) return 0;
  return null;
}
const J = (d, f) => JSON.parse(fs.readFileSync(`${SERVE}/${d}/data/${f}.json`, 'utf8'));

function loadModule(dir){
  const sandbox = { window: {}, console, Math, Date, JSON, isFinite, isNaN, parseInt, parseFloat, Object, Array, String, Number, Set, Map };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(`${SERVE}/${dir}/config.js`, 'utf8'), sandbox);
  vm.runInContext(fs.readFileSync(`${SERVE}/${dir}/scoring.js`, 'utf8'), sandbox);
  return { scoring: sandbox.window.JTTScoring, cfg: sandbox.window.SPORT_CONFIG || sandbox.SPORT_CONFIG };
}

// day key: EPL/MLB have dates, AFL/NFL have season+round/week
const dayOf = r => {
  const d = String(r.date || r.Date || '').slice(0, 10);
  if (d) return d;
  const rd = String(r.RoundName != null ? r.RoundName : r.Week).match(/\d+/);
  return `${r.Year}-${String(rd ? rd[0] : 0).padStart(3, '0')}`;
};

function run(key, dir){
  const { scoring, cfg } = loadModule(dir);
  if (!scoring || !scoring.configure || !scoring.scoreCMP) return console.log(`${key}: no usable module`);
  const players = J(dir, 'players'), logs = J(dir, 'gamelogs');
  const teams = J(dir, 'teams'), dvp = J(dir, 'dvp');
  let teamsForm = teams; try { teamsForm = J(dir, 'teams_form'); } catch (e) {}
  let fixture = []; try { fixture = J(dir, 'fixture'); } catch (e) {}
  let meta = {}; try { meta = J(dir, 'meta'); } catch (e) {}
  const season = +(meta.currentSeason || Math.max(...logs.map(r => +(r.Year || r.year) || 0)));
  const markets = (cfg.oddsMkts || cfg.dvpStats || []).slice(0, MAX_MARKETS);
  const byName = {}; players.forEach(p => byName[p.name] = p);

  const perDay = {};
  logs.forEach(r => { const d = dayOf(r); (perDay[d] = perDay[d] || []).push(r); });
  const days = Object.keys(perDay).filter(d => {
    const y = +String(d).slice(0, 4); return y === season;
  }).sort();
  const testDays = days.slice(-TEST_DAYS);
  if (!testDays.length) return console.log(`${key}: no current-season days to test`);

  const preds = [];
  testDays.forEach(day => {
    const priorLogs = {};
    logs.forEach(r => {
      if (dayOf(r) >= day) return;                       // strictly before: no look-ahead
      const n = r.Player || r.player; if (n) (priorLogs[n] = priorLogs[n] || []).push(r);
    });
    try {
      scoring.configure({ players, teams, teamsForm, dvp, logsByName: priorLogs,
                          fixture, meta, env: {}, currentSeason: String(season) });
    } catch (e) { return; }

    perDay[day].forEach(row => {
      const nm = row.Player || row.player, p = byName[nm];
      if (!p || !(priorLogs[nm] || []).length) return;
      const opp = row.Opp || row.opponent || null;
      markets.forEach(m => {
        const k = m[0];
        if (key === 'mlb'){
          const BO = {H:1,TB:1,HR:1,RBI:1,R:1}, AO = {K:1};
          if (p.role === 'pitch' && BO[k]) return;
          if (p.role !== 'pitch' && AO[k]) return;
        }
        const hist = (priorLogs[nm] || []).map(r => statOf(key, r, k)).filter(v => v != null);
        if (hist.length < 5) return;
        const avg = hist.reduce((a, b) => a + b, 0) / hist.length;
        let line = null;
        try { line = scoring.drLine ? scoring.drLine(avg, k) : null; } catch (e) {}
        if (line == null || !isFinite(line) || line <= 0) return;
        let score = null;
        try { score = scoring.scoreCMP(p, k, line, opp); } catch (e) { return; }
        if (score == null || !isFinite(score)) return;
        let prob;
        if (key === 'epl' || key === 'mlb') prob = Math.max(.01, Math.min(.99, score / 20 + .5));
        else {
          let hr = null;
          try { hr = scoring.getHitRate ? scoring.getHitRate(nm, k, line, false) : null; } catch (e) {}
          const rate = hr == null ? null : (hr.rate != null ? hr.rate : (isFinite(+hr) ? +hr : null));
          if (rate == null) return;
          prob = Math.max(.01, Math.min(.99, rate));
        }
        const actualVal = statOf(key, row, k);
        if (actualVal == null) return;
        preds.push({ prob, hit: actualVal >= line ? 1 : 0, score, k, line, actual: actualVal });
      });
    });
  });

  if (!preds.length) return console.log(`${key.toUpperCase()}: no predictions generated`);
  const n = preds.length;
  const meanP = preds.reduce((a, x) => a + x.prob, 0) / n;
  const actual = preds.reduce((a, x) => a + x.hit, 0) / n;
  const brier = preds.reduce((a, x) => a + (x.prob - x.hit) ** 2, 0) / n;
  const base = preds.reduce((a, x) => a + (actual - x.hit) ** 2, 0) / n;   // always-predict-base-rate
  console.log(`\n===== ${key.toUpperCase()} — ${n} predictions over ${testDays.length} match-days (${testDays[0]} → ${testDays[testDays.length-1]})`);
  console.log(`  mean predicted ${(meanP*100).toFixed(1)}%  |  actually landed ${(actual*100).toFixed(1)}%  |  bias ${((meanP-actual)*100).toFixed(1)} pts`);
  console.log(`  Brier ${brier.toFixed(4)} vs ${base.toFixed(4)} for always guessing the base rate  (${brier < base ? 'model adds signal' : 'NO BETTER THAN THE BASE RATE'})`);
  const buckets = {};
  preds.forEach(x => { const b = Math.min(9, Math.floor(x.prob * 10)); (buckets[b] = buckets[b] || []).push(x); });
  console.log('  predicted    n     actual');
  Object.keys(buckets).map(Number).sort((a, b) => a - b).forEach(b => {
    const g = buckets[b], hit = g.reduce((a, x) => a + x.hit, 0) / g.length;
    const bar = '#'.repeat(Math.round(hit * 20));
    console.log(`   ${String(b*10).padStart(3)}-${String(b*10+10).padEnd(3)} ${String(g.length).padStart(5)}   ${(hit*100).toFixed(0).padStart(3)}%  ${bar}`);
  });
  // what the high-confidence tail actually does — that's where Green Lights live
  // Platt scaling: fit logit(actual) = a * logit(pred) + b by gradient descent.
  // a < 1 means the model is overconfident and needs shrinking toward the base rate.
  const lg = v => Math.log(Math.max(1e-6, v) / Math.max(1e-6, 1 - v));
  let a = 1, b = 0;
  for (let it = 0; it < 4000; it++){
    let ga = 0, gb = 0;
    preds.forEach(x => {
      const z = a * lg(x.prob) + b, q = 1 / (1 + Math.exp(-z)), e = q - x.hit;
      ga += e * lg(x.prob); gb += e;
    });
    a -= 0.05 * ga / n; b -= 0.05 * gb / n;
  }
  const cal = preds.map(x => 1 / (1 + Math.exp(-(a * lg(x.prob) + b))));
  const brierCal = preds.reduce((s2, x, i) => s2 + (cal[i] - x.hit) ** 2, 0) / n;
  console.log(`  PLATT FIT: a=${a.toFixed(3)} b=${b.toFixed(3)} -> Brier ${brierCal.toFixed(4)} (${brierCal < base ? 'beats' : 'still no better than'} base rate)`);
  // discrimination: does ranking by the model separate winners from losers at all?
  const sorted = preds.slice().sort((x, y) => y.prob - x.prob);
  const topQ = sorted.slice(0, Math.max(1, Math.floor(n / 4)));
  const botQ = sorted.slice(-Math.max(1, Math.floor(n / 4)));
  const th = topQ.reduce((s2, x) => s2 + x.hit, 0) / topQ.length;
  const bh = botQ.reduce((s2, x) => s2 + x.hit, 0) / botQ.length;
  console.log(`  DISCRIMINATION: top quartile lands ${(th*100).toFixed(1)}% vs bottom quartile ${(bh*100).toFixed(1)}% -> spread ${((th-bh)*100).toFixed(1)} pts`);

  // where does the bias live? break it out per market
  const byMkt = {};
  preds.forEach(x => { (byMkt[x.k] = byMkt[x.k] || []).push(x); });
  console.log('  per market:  pred   actual   n     mean line   mean actual');
  Object.keys(byMkt).forEach(k2 => {
    const g = byMkt[k2], mp = g.reduce((a,x)=>a+x.prob,0)/g.length, ac = g.reduce((a,x)=>a+x.hit,0)/g.length;
    const ml = g.reduce((a,x)=>a+x.line,0)/g.length, ma = g.reduce((a,x)=>a+x.actual,0)/g.length;
    console.log(`   ${k2.padEnd(14)} ${(mp*100).toFixed(0).padStart(4)}%  ${(ac*100).toFixed(0).padStart(5)}%  ${String(g.length).padStart(5)}   ${ml.toFixed(2).padStart(8)}   ${ma.toFixed(2).padStart(10)}`);
  });

  const hi = preds.filter(x => x.prob >= 0.775);
  if (hi.length) console.log(`  at the Green Light threshold (prob >= 77.5%): ${hi.length} picks, landed ${(hi.reduce((a,x)=>a+x.hit,0)/hi.length*100).toFixed(1)}%`);
  const lo = preds.filter(x => x.prob <= 0.325);
  if (lo.length) console.log(`  at the Death Rider threshold (prob <= 32.5%): ${lo.length} picks, landed ${(lo.reduce((a,x)=>a+x.hit,0)/lo.length*100).toFixed(1)}% (want LOW)`);

  writeCalib(dir, key, {
    a: +a.toFixed(3), b: +b.toFixed(3), spread: +(th - bh).toFixed(3), n,
    bias: +(meanP - actual).toFixed(3), brier: +brier.toFixed(4), brierCalibrated: +brierCal.toFixed(4),
    brierBaseRate: +base.toFixed(4), days: testDays.length,
    window: [testDays[0], testDays[testDays.length - 1]]
  });
}

// Each sport's coefficients are written next to its data so the shell can fetch them like
// any other file. The shell keeps a hardcoded fallback, so a failed run degrades to the last
// known-good numbers rather than to no calibration at all.
function writeCalib(dir, key, fit){
  if (!WRITE) return;
  const out = `${SERVE}/${dir}/data/calib.json`;
  let prev = {};
  try { prev = JSON.parse(fs.readFileSync(out, 'utf8')); } catch (e) {}
  if (fit.n < 300){
    console.log(`  NOT WRITING ${dir}/data/calib.json - only ${fit.n} predictions, keeping previous fit`);
    return;
  }
  const body = { sport: key, fitted: new Date().toISOString().slice(0, 19) + 'Z', ...fit,
                 previous: prev.fitted ? { fitted: prev.fitted, a: prev.a, b: prev.b, spread: prev.spread } : null };
  fs.writeFileSync(out, JSON.stringify(body, null, 2) + '\n');
  console.log(`  wrote ${dir}/data/calib.json  (a=${fit.a} b=${fit.b} spread=${fit.spread})`);
}

SPORTS.forEach(([k, d]) => { try { run(k, d); } catch (e) { console.log(`${k}: ${e.message}`); } });
