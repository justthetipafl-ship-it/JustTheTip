/* Grade stored odds against what actually happened.
 *
 * Run: node tools/grade-odds.js            (JTT_ROOT overrides the repo root)
 *      node tools/grade-odds.js --min-ev 5 (only count picks with 5%+ modelled edge)
 *
 * calibrate.js answers "is the model any good at predicting?". This answers the question that
 * pays: "would backing its picks, at prices you could actually have taken, have made money?"
 *
 * For each stored snapshot day:
 *   - configure the sport's scoring.js with ONLY games before that day (no look-ahead),
 *   - ask it for a probability on every player/market/line in the snapshot,
 *   - keep the picks that cleared the edge threshold,
 *   - resolve each against the gamelogs and settle it at the stored price.
 *
 * Reports strike rate, return on turnover, and a breakdown by price band and by edge band, so a
 * profit that comes entirely from one longshot is visible as exactly that.
 */
const fs = require('fs'), path = require('path'), zlib = require('zlib'), vm = require('vm');
const ROOT = process.env.JTT_ROOT || path.resolve(__dirname, '..');
const SPORTS = [['afl','AFL'], ['nfl','nfl'], ['epl','EPL'], ['mlb','mlb']];
const argEV = (() => { const i = process.argv.indexOf('--min-ev'); return i > 0 ? +process.argv[i+1] / 100 : 0.03; })();
const COVER = { epl: 'passes' };

function statOf(key, row, mkt){
  const v = row ? row[mkt] : null;
  if (v != null && isFinite(+v)) return +v;
  const cf = COVER[key];
  if (cf && row && row[cf] != null && isFinite(+row[cf])) return 0;
  return null;
}
function loadModule(dir){
  const sandbox = { window:{}, console, Math, Date, JSON, isFinite, isNaN, parseInt, parseFloat,
                    Object, Array, String, Number, Set, Map };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(`${ROOT}/${dir}/config.js`, 'utf8'), sandbox);
  vm.runInContext(fs.readFileSync(`${ROOT}/${dir}/scoring.js`, 'utf8'), sandbox);
  return { scoring: sandbox.window.JTTScoring, cfg: sandbox.window.SPORT_CONFIG || sandbox.SPORT_CONFIG };
}
function calibFor(dir){
  try { return JSON.parse(fs.readFileSync(`${ROOT}/${dir}/data/calib.json`, 'utf8')); } catch (e) { return null; }
}
function calibrate(c, p){
  if (!c || c.a == null || p == null) return p;
  const lg = Math.log(Math.max(1e-6, p) / Math.max(1e-6, 1 - p));
  return 1 / (1 + Math.exp(-(c.a * lg + c.b)));
}
const dayOf = r => {
  const d = String(r.date || r.Date || '').slice(0, 10);
  if (d) return d;
  const rd = String(r.RoundName != null ? r.RoundName : r.Week).match(/\d+/);
  return `${r.Year}-${String(rd ? rd[0] : 0).padStart(3, '0')}`;
};

function gradeSport(key, dir){
  const dirHist = `${ROOT}/${dir}/odds-history`;
  if (!fs.existsSync(dirHist)) return console.log(`${key.toUpperCase()}: no snapshots yet`);
  const files = fs.readdirSync(dirHist).filter(f => f.endsWith('.json.gz')).sort();
  if (!files.length) return console.log(`${key.toUpperCase()}: no snapshots yet`);

  const { scoring, cfg } = loadModule(dir);
  if (!scoring || !scoring.configure) return console.log(`${key.toUpperCase()}: no usable scoring module`);
  const players = JSON.parse(fs.readFileSync(`${ROOT}/${dir}/data/players.json`, 'utf8'));
  const logs = JSON.parse(fs.readFileSync(`${ROOT}/${dir}/data/gamelogs.json`, 'utf8'));
  const teams = JSON.parse(fs.readFileSync(`${ROOT}/${dir}/data/teams.json`, 'utf8'));
  const dvp = JSON.parse(fs.readFileSync(`${ROOT}/${dir}/data/dvp.json`, 'utf8'));
  let teamsForm = teams, fixture = [], meta = {};
  try { teamsForm = JSON.parse(fs.readFileSync(`${ROOT}/${dir}/data/teams_form.json`, 'utf8')); } catch (e) {}
  try { fixture = JSON.parse(fs.readFileSync(`${ROOT}/${dir}/data/fixture.json`, 'utf8')); } catch (e) {}
  try { meta = JSON.parse(fs.readFileSync(`${ROOT}/${dir}/data/meta.json`, 'utf8')); } catch (e) {}
  const season = String(meta.currentSeason || new Date().getFullYear());
  const byName = {}; players.forEach(p => byName[p.name] = p);
  const cal = calibFor(dir);
  const markets = new Set((cfg.oddsMkts || []).map(m => m[0]));

  const picks = [];
  let snapRows = 0, ungraded = 0;

  for (const file of files){
    const day = file.replace('.json.gz', '');
    const snap = JSON.parse(zlib.gunzipSync(fs.readFileSync(`${dirHist}/${file}`)).toString('utf8'));
    // history strictly before the capture day - the model must not see the game it is pricing
    const prior = {};
    logs.forEach(r => {
      if (dayOf(r) >= day) return;
      const n = r.Player || r.player; if (n) (prior[n] = prior[n] || []).push(r);
    });
    // The results OF that day - but a gamelog row can exist for a fixture that has not been
    // played yet (EPL writes placeholder rows for the upcoming round). Settling against those
    // loses every over at 0-0. Only rows with evidence of participation count.
    const played = r => {
      if (r.min != null) return +r.min > 0;
      return Object.keys(r).some(k => typeof r[k] === 'number' && r[k] > 0 &&
        !/^(year|week|season|gamePk|teamId|id)$/i.test(k));
    };
    const settled = {};
    logs.forEach(r => {
      if (dayOf(r) !== day) return;
      const n = r.Player || r.player;
      if (n && played(r)) settled[n] = r;
    });
    if (!Object.keys(settled).length){ ungraded += (snap.odds || []).length; continue; }

    try {
      scoring.configure({ players, teams, teamsForm, dvp, logsByName: prior,
                          fixture, meta, env:{}, currentSeason: season });
    } catch (e) { continue; }

    (snap.odds || []).forEach(row => {
      snapRows++;
      if (!markets.has(row.market)) return;
      const p = byName[row.player], res = settled[row.player];
      if (!p || !res) return;
      const actual = statOf(key, res, row.market);
      if (actual == null) return;
      const opp = res.Opp || res.opponent || null;
      ['over','under'].forEach(side => {
        const price = side === 'over' ? row.over : row.under;
        if (price == null || price < 1.2) return;
        // AFL and NFL expose prob(); EPL and MLB define scoreCMP as (prob - 0.5) * 20 and
        // export no prob at all, so invert it there - the same thing the shell does.
        let raw = null;
        try { if (scoring.prob) raw = scoring.prob(p, row.market, row.line, opp); } catch (e) {}
        if (raw == null || !isFinite(raw)){
          try {
            const sc = scoring.scoreCMP(p, row.market, row.line, opp);
            if (sc != null && isFinite(sc)) raw = Math.max(0.01, Math.min(0.99, sc / 20 + 0.5));
          } catch (e) {}
        }
        if (raw == null || !isFinite(raw)) return;
        const pOver = calibrate(cal, raw);
        const prob = side === 'over' ? pOver : 1 - pOver;
        const ev = prob * (price - 1) - (1 - prob);
        if (ev < argEV) return;
        if (prob > Math.min(0.97, (1/price) * 4)) return;      // same tail guard the builder uses
        const won = side === 'over' ? actual > row.line : actual <= row.line;
        picks.push({ day, key, player:row.player, market:row.market, line:row.line, side,
                     price:+price, prob, ev, won, actual });
      });
    });
  }

  console.log(`\n===== ${key.toUpperCase()} — ${files.length} snapshot day(s), ${snapRows} stored prices`);
  if (!picks.length){
    console.log(`  no gradable picks yet` + (ungraded ? ` (${ungraded} prices captured for games not yet played)` : ''));
    return;
  }
  const report = (label, rows) => {
    if (!rows.length) return;
    const n = rows.length, wins = rows.filter(r => r.won).length;
    const ret = rows.reduce((a, r) => a + (r.won ? r.price - 1 : -1), 0);
    const exp = rows.reduce((a, r) => a + r.ev, 0);
    console.log(`  ${label.padEnd(22)} ${String(n).padStart(5)} picks  ${(wins/n*100).toFixed(1).padStart(5)}% strike  `
      + `${(ret/n*100 >= 0 ? '+' : '')}${(ret/n*100).toFixed(1).padStart(6)}% ROI   (model expected ${(exp/n*100 >= 0 ? '+' : '')}${(exp/n*100).toFixed(1)}%)`);
  };
  report('ALL', picks);
  console.log('  by price:');
  [[1.2,2],[2,3],[3,6],[6,999]].forEach(b =>
    report(`   $${b[0]}-${b[1] === 999 ? '+' : b[1]}`, picks.filter(r => r.price >= b[0] && r.price < b[1])));
  console.log('  by modelled edge:');
  [[0.03,0.1],[0.1,0.2],[0.2,999]].forEach(b =>
    report(`   ${Math.round(b[0]*100)}-${b[1] === 999 ? '+' : Math.round(b[1]*100)}%`,
      picks.filter(r => r.ev >= b[0] && r.ev < b[1])));
  // a single longshot can carry a whole sample: say so plainly
  const best = picks.filter(r => r.won).sort((a,b) => b.price - a.price)[0];
  if (best){
    const without = picks.filter(r => r !== best);
    const roiAll = picks.reduce((a,r) => a + (r.won ? r.price-1 : -1), 0) / picks.length * 100;
    const roiEx = without.length ? without.reduce((a,r) => a + (r.won ? r.price-1 : -1), 0) / without.length * 100 : 0;
    console.log(`  biggest winner: ${best.player} ${best.market} @ $${best.price.toFixed(2)} `
      + `\u2014 drop it and ROI goes ${roiAll.toFixed(1)}% -> ${roiEx.toFixed(1)}%`);
  }
}

SPORTS.forEach(([k, d]) => { try { gradeSport(k, d); } catch (e) { console.log(`${k}: ${e.message}`); } });
console.log(`\nEdge threshold ${(argEV*100).toFixed(0)}% \u00b7 settled at the stored price \u00b7 `
  + `no look-ahead: each day is priced by a model that has only seen earlier games.`);
