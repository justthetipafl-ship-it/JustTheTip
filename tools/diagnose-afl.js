/* AFL model diagnosis. Run: node tools/diagnose-afl.js  (HL/SHRINK/DW env vars sweep params)
 *
 * Findings as at 20 Sep 2026, 31,945 player-games:
 *   the shipped model is G (EWMA form, shrunk to season baseline, DVP-adjusted, negative binomial)
 *   REJECTED, each measured worse than G: per-TOG rate (15.4 vs 16.4), role drift from CBA/TOG
 *   (24.2 vs 24.6), opponent pace (24.2 vs 24.6). AFL disposals are high-count and low-variance;
 *   the pace factor only spans 0.95-1.03 and role drift averages 1.003, so neither has room to move
 *   a projection. Don't re-litigate these without new data.
 *
 * Original header:
 * AFL model diagnosis: walk-forward comparison of candidate probability models.
 *
 * Every model predicts the same thing on the same games - P(stat >= line), where the line is
 * placed the way drLine places it (round of the player's prior average). The current model is
 * the historical hit rate at that line. Each variant is scored on Brier and on how far the top
 * quartile of its picks outperforms the bottom (discrimination) - the thing AFL currently fails.
 */
const fs = require('fs');
const path = require('path');
const ROOT = process.env.JTT_ROOT || path.resolve(__dirname, '..');
const logs = JSON.parse(fs.readFileSync(`${ROOT}/AFL/data/gamelogs.json`, 'utf8'));
const dvp = JSON.parse(fs.readFileSync(`${ROOT}/AFL/data/dvp.json`, 'utf8'));
const players = JSON.parse(fs.readFileSync(`${ROOT}/AFL/data/players.json`, 'utf8'));
const STATS = ['disposals', 'marks', 'tackles', 'goals'];
const MIN_HIST = 6;

const nt = s => String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, '');
const posOf = {}; players.forEach(p => posOf[p.name] = p.position);
const seq = r => (+r.Year || 0) * 1000 + (+(String(r.RoundName).match(/\d+/) || [0])[0]);

// opponent from MatchId: both teams in a match share one
const pair = {};
logs.forEach(r => { const id = r.MatchId; if (!id) return;
  (pair[id] = pair[id] || []); if (pair[id].indexOf(r.Team) < 0) pair[id].push(r.Team); });
const oppOf = r => { const pr = pair[r.MatchId]; if (!pr || pr.length < 2) return null;
  return nt(pr[0]) === nt(r.Team) ? pr[1] : pr[0]; };

// DVP: what each team concedes to each position, as a % of league average
const dvpPct = {};
STATS.forEach(stat => {
  const byPos = {};
  dvp.forEach(r => { if (r[stat] == null) return; (byPos[r.pos] = byPos[r.pos] || []).push(r); });
  Object.keys(byPos).forEach(pos => {
    const rows = byPos[pos], avg = rows.reduce((a, r) => a + +r[stat], 0) / rows.length;
    rows.forEach(r => { dvpPct[`${stat}|${pos}|${nt(r.team)}`] = avg ? (+r[stat] - avg) / avg * 100 : 0; });
  });
});

// game pace: total disposals in matches against each team, relative to league average.
// A slow, congested opponent suppresses everyone's numbers regardless of position.
const teamAgainst = {}, matchTotals = {};
logs.forEach(r => { const id = r.MatchId; if (!id) return;
  matchTotals[id] = (matchTotals[id] || 0) + (+r.disposals || 0); });
Object.keys(matchTotals).forEach(id => {
  const pr = pair[id] || [];
  pr.forEach(t => { (teamAgainst[nt(t)] = teamAgainst[nt(t)] || []).push(matchTotals[id]); });
});
const leaguePace = mean0(Object.values(teamAgainst).flat());
const paceOf = t => { const a = teamAgainst[nt(t)]; return a && a.length && leaguePace
  ? Math.max(0.9, Math.min(1.1, mean0(a) / leaguePace)) : 1; };
function mean0(a){ return a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0; }

const byName = {};
logs.forEach(r => { if (r.Player) (byName[r.Player] = byName[r.Player] || []).push(r); });
Object.values(byName).forEach(a => a.sort((x, y) => seq(x) - seq(y)));

const mean = a => a.reduce((x, y) => x + y, 0) / a.length;
function ewma(vals, halflife){
  const lam = Math.pow(0.5, 1 / halflife);
  let num = 0, den = 0;
  for (let i = vals.length - 1, w = 1; i >= 0; i--, w *= lam){ num += vals[i] * w; den += w; }
  return den ? num / den : 0;
}
function poissonAtLeast(mu, k){
  if (!(mu > 0)) return 0;
  let cum = 0, term = Math.exp(-mu);
  for (let i = 0; i < k; i++){ cum += term; term = term * mu / (i + 1); }
  return Math.max(0, Math.min(1, 1 - cum));
}
function nbAtLeast(mu, k, vals){
  if (!(mu > 0)) return 0;
  if (vals.length < 4) return poissonAtLeast(mu, k);
  const m = mean(vals), v = mean(vals.map(x => (x - m) ** 2));
  if (!(v > m * 1.05)) return poissonAtLeast(mu, k);
  let size = (m * m) / (v - m);
  size = Math.max(0.35, Math.min(50, size));
  const p = size / (size + mu);
  let cum = 0, term = Math.pow(p, size);
  for (let i = 0; i < k; i++){ cum += term; term = term * (size + i) / (i + 1) * (1 - p); }
  return Math.max(0, Math.min(1, 1 - cum));
}

const HL = +(process.env.HL || 5), SHRINK = +(process.env.SHRINK || 5), DW = +(process.env.DW || 0.5);
const MODELS = {
  'A current (hit rate)': c => { const h = c.hist.filter(v => v >= c.line).length; return h / c.hist.length; },
  'B poisson(flat avg)':  c => poissonAtLeast(c.flat, c.line),
  'C negbin(flat avg)':   c => nbAtLeast(c.flat, c.line, c.hist),
  'D negbin(ewma hl=5)':  c => nbAtLeast(c.ewma, c.line, c.hist),
  'E D + per-TOG rate':   c => nbAtLeast(c.togProj, c.line, c.hist),
  'F E + DVP':            c => nbAtLeast(c.togProj * c.dvpF, c.line, c.hist),
  'G F + shrink':         c => nbAtLeast(c.shrunk * c.dvpF, c.line, c.hist),
  'H G + role (CBA/TOG)': c => nbAtLeast(c.shrunk * c.dvpF * c.roleF, c.line, c.hist),
  'I G + opponent pace':  c => nbAtLeast(c.shrunk * c.dvpF * c.paceF, c.line, c.hist),
  'J G + role + pace':    c => nbAtLeast(c.shrunk * c.dvpF * c.roleF * c.paceF, c.line, c.hist)
};

const diag = { role: [], pace: [] };
const preds = {}; Object.keys(MODELS).forEach(k => preds[k] = []);
let n = 0;
Object.keys(byName).forEach(name => {
  const games = byName[name], pos = posOf[name];
  for (let i = MIN_HIST; i < games.length; i++){
    const row = games[i];
    if (+row.Year !== 2026) continue;                       // test on the current season
    const hist10 = games.slice(Math.max(0, i - 10), i);
    const opp = oppOf(row);
    STATS.forEach(stat => {
      const hist = hist10.map(r => +r[stat] || 0);
      if (hist.length < MIN_HIST) return;
      const flat = mean(hist);
      const line = Math.round(flat);                        // drLine
      if (!(line > 0)) return;
      const actual = +row[stat] || 0;
      // time on ground: AFL's minutes analogue
      const togs = hist10.map(r => +r.tog || 0).filter(t => t > 0);
      const expTog = togs.length ? mean(togs) * (togs.length / hist10.length) : 80;
      const totStat = hist10.reduce((a, r) => a + (+r[stat] || 0), 0);
      const totTog = hist10.reduce((a, r) => a + (+r.tog || 0), 0);
      const togProj = totTog > 0 ? (totStat / totTog) * expTog : flat;
      const pct = dvpPct[`${stat}|${pos}|${nt(opp)}`];
      const dvpF = pct == null ? 1 : Math.max(0.75, Math.min(1.3, 1 + (pct / 100) * DW));
      // shrink a thin sample toward the player's own season baseline
      const seasonRows = games.slice(0, i).filter(r => +r.Year === 2026).map(r => +r[stat] || 0);
      const baseline = seasonRows.length ? mean(seasonRows) : flat;
      const k = SHRINK, shrunk = (ewma(hist, HL) * hist.length + baseline * k) / (hist.length + k);
      // role drift: recent centre-bounce and on-ground share against his own baseline. A
      // midfielder moved forward keeps his scoring history but loses the usage behind it.
      const cbaAll = hist10.map(r => +r.cba || 0), togAll = hist10.map(r => +r.tog || 0);
      const roleRecent = 0.6 * ewma(cbaAll, 3) + 0.4 * ewma(togAll, 3);
      const roleBase = 0.6 * mean(cbaAll) + 0.4 * mean(togAll);
      let roleF = roleBase > 0 && isFinite(roleRecent) ? roleRecent / roleBase : 1;
      roleF = isFinite(roleF) ? Math.max(0.8, Math.min(1.25, roleF)) : 1;
      let paceF = opp ? paceOf(opp) : 1;
      paceF = isFinite(paceF) ? paceF : 1;
      diag.role.push(roleF); diag.pace.push(paceF);
      const ctx = { hist, flat, ewma: ewma(hist, HL), line, togProj, dvpF, shrunk, roleF, paceF };
      const hit = actual >= line ? 1 : 0;
      Object.keys(MODELS).forEach(mk => {
        let p = MODELS[mk](ctx);
        p = Math.max(0.01, Math.min(0.99, p));
        preds[mk].push({ p, hit });
      });
      n++;
    });
  }
});

console.log(`AFL diagnosis — ${n} player-games tested (2026 season), ${STATS.join('/')}\n`);
console.log('model                    Brier    vs base   bias     spread   verdict');
const base0 = preds['A current (hit rate)'];
const baseRate = mean(base0.map(x => x.hit));
const baseBrier = mean(base0.map(x => (baseRate - x.hit) ** 2));
Object.keys(MODELS).forEach(mk => {
  const a = preds[mk], br = mean(a.map(x => (x.p - x.hit) ** 2));
  const bias = mean(a.map(x => x.p)) - baseRate;
  const sorted = a.slice().sort((x, y) => y.p - x.p);
  const q = Math.floor(a.length / 4);
  const top = mean(sorted.slice(0, q).map(x => x.hit)), bot = mean(sorted.slice(-q).map(x => x.hit));
  const spread = (top - bot) * 100;
  console.log(`${mk.padEnd(24)} ${br.toFixed(4)}  ${(br < baseBrier ? 'beats ' : 'WORSE ')}  `
    + `${(bias * 100).toFixed(1).padStart(5)}pt  ${spread.toFixed(1).padStart(6)}pt  `
    + `${spread > 15 ? 'strong' : spread > 8 ? 'useful' : spread > 3 ? 'weak' : 'NONE'}`);
});
console.log(`\nrole factor: mean ${mean(diag.role).toFixed(3)}, spread ${Math.min(...diag.role).toFixed(2)}-${Math.max(...diag.role).toFixed(2)} | pace factor: mean ${mean(diag.pace).toFixed(3)}, spread ${Math.min(...diag.pace).toFixed(2)}-${Math.max(...diag.pace).toFixed(2)}`);
console.log(`\nbase rate ${(baseRate * 100).toFixed(1)}%  |  always-base-rate Brier ${baseBrier.toFixed(4)}`);
