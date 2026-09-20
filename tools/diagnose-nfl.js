/* AFL model diagnosis: walk-forward comparison of candidate probability models.
 *
 * Every model predicts the same thing on the same games - P(stat >= line), where the line is
 * placed the way drLine places it (round of the player's prior average). The current model is
 * the historical hit rate at that line. Each variant is scored on Brier and on how far the top
 * quartile of its picks outperforms the bottom (discrimination) - the thing AFL currently fails.
 */
const fs = require('fs');
const path = require('path');
const ROOT = process.env.JTT_ROOT || path.resolve(__dirname, '..');
const logs = JSON.parse(fs.readFileSync(`${ROOT}/nfl/data/gamelogs.json`, 'utf8'));
const dvp = JSON.parse(fs.readFileSync(`${ROOT}/nfl/data/dvp.json`, 'utf8'));
const players = JSON.parse(fs.readFileSync(`${ROOT}/nfl/data/players.json`, 'utf8'));
// NFL splits into two families: counts (receptions, TDs) and yardage (continuous, right-skewed).
// Modelling yards as a count is the thing to test.
const COUNTS = ['receptions', 'passTds', 'rushAtt'];
const YARDS = ['recYds', 'rushYds', 'passYds'];
const STATS = COUNTS.concat(YARDS);
const MIN_HIST = 6;

const nt = s => String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, '');
const posOf = {}; players.forEach(p => posOf[p.name] = p.position);
const seq = r => (+r.Year || 0) * 1000 + (+r.Week || 0);

// opponent from MatchId: both teams in a match share one
const pair = {};
logs.forEach(r => { const id = r.MatchId; if (!id) return;
  (pair[id] = pair[id] || []); if (pair[id].indexOf(r.Team) < 0) pair[id].push(r.Team); });
const oppOf = r => r.Opp || null;

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
// gamma tail for yardage: shape from the player's own coefficient of variation
function gammaAtLeast(mu, x, vals){
  if (!(mu > 0)) return 0;
  const m = vals.length ? mean(vals) : mu;
  const v = vals.length > 3 ? mean(vals.map(z => (z - m) ** 2)) : m * m;
  const cv2 = m > 0 ? Math.max(0.05, v / (m * m)) : 1;
  const shape = Math.max(0.5, Math.min(40, 1 / cv2)), scale = mu / shape;
  // regularised upper incomplete gamma by series/continued fraction
  const a = shape, z = x / scale;
  if (z <= 0) return 1;
  const gln = (() => { const c = [76.18009172947146,-86.50532032941677,24.01409824083091,
    -1.231739572450155,0.1208650973866179e-2,-0.5395239384953e-5];
    let y = a, tmp = a + 5.5; tmp -= (a + 0.5) * Math.log(tmp);
    let ser = 1.000000000190015;
    for (let j = 0; j < 6; j++) ser += c[j] / ++y;
    return -tmp + Math.log(2.5066282746310005 * ser / a); })();
  if (z < a + 1){
    let ap = a, sum = 1 / a, del = sum;
    for (let i = 0; i < 300; i++){ ap++; del *= z / ap; sum += del; if (Math.abs(del) < Math.abs(sum) * 1e-9) break; }
    return Math.max(0, Math.min(1, 1 - sum * Math.exp(-z + a * Math.log(z) - gln)));
  }
  let b = z + 1 - a, c2 = 1e30, d = 1 / b, h = d;
  for (let i = 1; i < 300; i++){
    const an = -i * (i - a); b += 2;
    d = an * d + b; if (Math.abs(d) < 1e-30) d = 1e-30;
    c2 = b + an / c2; if (Math.abs(c2) < 1e-30) c2 = 1e-30;
    d = 1 / d; const del = d * c2; h *= del;
    if (Math.abs(del - 1) < 1e-9) break;
  }
  return Math.max(0, Math.min(1, Math.exp(-z + a * Math.log(z) - gln) * h));
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
const dist = (c, mu) => c.isYards ? gammaAtLeast(mu, c.line, c.hist) : nbAtLeast(mu, c.line, c.hist);
const MODELS = {
  'A current (hit rate)': c => { const h = c.hist.filter(v => v >= c.line).length; return h / c.hist.length; },
  'B count dist(flat)':   c => nbAtLeast(c.flat, c.line, c.hist),
  'C right dist(flat)':   c => dist(c, c.flat),
  'D + ewma hl=5':        c => dist(c, c.ewma),
  'E + volume x eff':     c => dist(c, c.togProj),
  'F + DVP':              c => dist(c, c.togProj * c.dvpF),
  'G + shrink':           c => dist(c, c.shrunk * c.dvpF)
};

const preds = {}; Object.keys(MODELS).forEach(k => preds[k] = []);
let n = 0;
Object.keys(byName).forEach(name => {
  const games = byName[name], pos = posOf[name];
  for (let i = MIN_HIST; i < games.length; i++){
    const row = games[i];
    if (+row.Year < 2025) continue;                         // 2025 season + what exists of 2026
    const hist10 = games.slice(Math.max(0, i - 10), i);
    const opp = oppOf(row);
    STATS.forEach(stat => {
      const hist = hist10.map(r => +r[stat] || 0);
      if (hist.length < MIN_HIST) return;
      const flat = mean(hist);
      // drLine-style: counts round to the nearest whole, yards to the nearest 5
      // Only test players who actually do this thing: a lineman's receiving yards are trivially
      // predictable and would flatter every model. Yards need a real workload, counts a real rate.
      if (YARDS.includes(stat) ? flat < 15 : flat < 0.75) return;
      const line = YARDS.includes(stat) ? Math.round(flat / 5) * 5 : Math.round(flat);
      if (!(line > 0)) return;
      const actual = +row[stat] || 0;
      // time on ground: AFL's minutes analogue
      // volume x efficiency: yards come from opportunities, which are steadier than the yards
      const volKey = stat === 'recYds' ? 'targets' : stat === 'rushYds' ? 'rushAtt'
                   : stat === 'passYds' ? 'passAtt' : null;
      let togProj = flat;
      if (volKey){
        const vols = hist10.map(r => +r[volKey] || 0);
        const totVol = vols.reduce((a, b) => a + b, 0);
        const totStat2 = hist10.reduce((a, r) => a + (+r[stat] || 0), 0);
        if (totVol > 0) togProj = (totStat2 / totVol) * ewma(vols, 5);   // per-opportunity x expected volume
      }
      const pct = dvpPct[`${stat}|${pos}|${nt(opp)}`];
      const dvpF = pct == null ? 1 : Math.max(0.75, Math.min(1.3, 1 + (pct / 100) * DW));
      // shrink a thin sample toward the player's own season baseline
      const seasonRows = games.slice(0, i).filter(r => +r.Year === +row.Year).map(r => +r[stat] || 0);
      const baseline = seasonRows.length ? mean(seasonRows) : flat;
      const k = SHRINK, shrunk = (ewma(hist, HL) * hist.length + baseline * k) / (hist.length + k);
      const ctx = { hist, flat, ewma: ewma(hist, HL), line, togProj, dvpF, shrunk,
                    isYards: YARDS.includes(stat) };
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

console.log(`NFL diagnosis — ${n} player-games tested (2026 season), ${STATS.join('/')}\n`);
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
console.log(`\nbase rate ${(baseRate * 100).toFixed(1)}%  |  always-base-rate Brier ${baseBrier.toFixed(4)}`);
