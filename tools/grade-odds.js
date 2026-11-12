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
// Every sport the tool ships has to be in this list. The NBA was built after these
// pipelines and was added to none of them, so it had no odds feed, no odds history, no
// grading and no calibration - all of which fail silently, because an absent sport just
// never appears. tests/odds_wiring_test.js checks this list against the shipped sports.
const SPORTS = [['afl','AFL'], ['nfl','nfl'], ['epl','EPL'], ['mlb','mlb'], ['nbl','nbl'],
                ['nhl','nhl'], ['nba','nba']];
const nt = s => String(s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
const argEV = (() => { const i = process.argv.indexOf('--min-ev'); return i > 0 ? +process.argv[i+1] / 100 : 0.03; })();
/* --rule <file>: grade a CANDIDATE RULE instead of the model's own EV picks.
 *
 * Every new idea for a tile starts as "this looks like it should win". The only honest way to
 * find out is to run it over prices that were really on the board, and before this the tool
 * could only grade what the model already liked - so a new idea had to be shipped to be
 * measured, which is how a tile ends up on the ledger at -65%.
 *
 * A rule file exports { name, markets?, pick(ctx) } and returns 'over', 'under' or nothing:
 *
 *     module.exports = {
 *       name: 'rebounds when a rival sits',
 *       markets: ['rebounds'],
 *       pick: ctx => ctx.absent.some(n => ctx.rivalReb(n) >= 5) ? 'over' : null
 *     };
 *
 * ctx carries: sport, player, market, line, over, under, side, opp, team, day,
 * prior (his games before this one, newest last), absent (team-mates who did not play),
 * data(file) to read that sport's data/<file>.json, and prob/evRaw/evCap from the model so a
 * rule can combine with it rather than replace it. The rule is applied INSTEAD of the EV gate,
 * so its picks are whatever it says and nothing else. */
const argRule = (() => {
  const i = process.argv.indexOf('--rule');
  if (i < 0) return null;
  const f = process.argv[i + 1];
  const p = path.isAbsolute(f) ? f : path.resolve(ROOT, f);
  const m = require(p);
  if (!m || typeof m.pick !== 'function') throw new Error('rule file must export pick()');
  return m;
})();
const argSport = (() => { const i = process.argv.indexOf('--sport'); return i > 0 ? process.argv[i+1].toLowerCase() : null; })();
/* --main: only the line a book actually hangs its market on.
 *
 * A snapshot stores the whole ALTERNATE ladder - one man's points can appear at twenty rungs
 * from 4.5 to 49.5, priced from $1.01 to $41. Grading a rule across all of them does not measure
 * the rule, it measures the ladder: "over rebounds when a rival sits" came back at -69% here
 * purely because 43 of its 90 picks were $6-plus rungs that were never going to land.
 *
 * A main line is the one with both sides priced like a two-way market. Everything outside that
 * is a different bet with a different question attached. */
const argMain = process.argv.indexOf('--main') > 0;
const MAIN_LO = 1.55, MAIN_HI = 2.45;
const isMainLine = row => !!(row && row.over != null && row.under != null
  && +row.over >= MAIN_LO && +row.over <= MAIN_HI
  && +row.under >= MAIN_LO && +row.under <= MAIN_HI);
const dataCache = {};
function dataOf(dir, file){
  const k = dir + '/' + file;
  if (!(k in dataCache)){
    try { dataCache[k] = JSON.parse(fs.readFileSync(`${ROOT}/${dir}/data/${file}.json`, 'utf8')); }
    catch (e) { dataCache[k] = null; }
  }
  return dataCache[k];
}
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

/* Team-mates who did not take the floor in the game a price settled on. A rule asks for this
   constantly - it is the whole family of "X is out tonight" ideas - and working it out from the
   gamelogs is the only way available after the fact. Two caveats a rule author should know and
   the write-up should repeat: it includes late scratches a bettor could not have known about,
   and it cannot see a man who was always going to rest. */
const absentCache = new Map();
function absentFor(res, logs){
  const mid = String(res.MatchId != null ? res.MatchId : (res.gamePk != null ? res.gamePk : ''));
  const team = res.Team || res.team;
  if (!mid || !team) return [];
  const k = mid + '|' + team;
  if (absentCache.has(k)) return absentCache.get(k);
  const playedIn = new Set(), squad = new Set();
  logs.forEach(r => {
    if ((r.Team || r.team) !== team) return;
    const n = r.Player || r.player; if (!n) return;
    squad.add(n);
    const mi = String(r.MatchId != null ? r.MatchId : (r.gamePk != null ? r.gamePk : ''));
    if (mi === mid && (+r.minutes > 0 || +r.min > 0 || +r.toiMin > 0)) playedIn.add(n);
  });
  const out = [...squad].filter(n => !playedIn.has(n));
  absentCache.set(k, out);
  return out;
}
function gradeSport(key, dir){
  const dirHist = `${ROOT}/${dir}/odds-history`;
  if (!fs.existsSync(dirHist)) return console.log(`${key.toUpperCase()}: no snapshots yet`);
  const files = fs.readdirSync(dirHist).filter(f => f.endsWith('.json.gz')).sort();
  if (!files.length) return console.log(`${key.toUpperCase()}: no snapshots yet`);

  const { scoring, cfg } = loadModule(dir);
  if (!scoring || !scoring.configure) return console.log(`${key.toUpperCase()}: no usable scoring module`);
  const players = JSON.parse(fs.readFileSync(`${ROOT}/${dir}/data/players.json`, 'utf8'));
  // NBL and NHL publish one gamelog file per season, listed in meta.json
  let logs;
  try { logs = JSON.parse(fs.readFileSync(`${ROOT}/${dir}/data/gamelogs.json`, 'utf8')); }
  catch (e){
    let m0 = {}; try { m0 = JSON.parse(fs.readFileSync(`${ROOT}/${dir}/data/meta.json`, 'utf8')); } catch (e2) {}
    logs = [].concat(...(m0.gamelogFiles || []).map(f =>
      JSON.parse(fs.readFileSync(`${ROOT}/${dir}/data/${f}`, 'utf8'))));
    if (!logs.length) throw e;
  }
  let results = [];
  try { results = JSON.parse(fs.readFileSync(`${ROOT}/${dir}/data/results.json`, 'utf8')); } catch (e) {}
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

  let picks = [];
  let snapRows = 0, ungraded = 0;

  /* Which game was each price for?  The first cut matched the snapshot's calendar date to the
     gamelog's, which fails three ways: AFL and NFL gamelogs have no dates at all (season + round
     or week), North American games land on the next UTC day (an NHL game at 7pm ET on the 29th
     is priced before a 02:00 UTC snapshot dated the 30th), and the 09:00 snapshot overwrites the
     02:00 one. So: take the time the ODDS were pulled, find each team's next fixture from the
     fixture list saved inside the snapshot, and match that fixture to the player's gamelog row
     by the key that sport actually records - local date, season+week, or season+round.       */
  const hasDate = logs.some(r => r.Date || r.date);
  const roundOf = r => {
    const m = String(r.RoundName != null ? r.RoundName : (r.Week != null ? r.Week : '')).match(/\d+/);
    return m ? +m[0] : null;
  };
  const rowKey = r => hasDate ? String(r.Date || r.date || '').slice(0, 10)
                              : (+(r.Year || r.year) || 0) * 1000 + (roundOf(r) || 0);
  // AFL: fixtures carry a date but no round; results.json has both, so it bridges the two
  const aflRound = {};
  results.forEach(m => { if (m && m.date && m.round != null)
    aflRound[m.date + '|' + nt(m.home) + '|' + nt(m.away)] = (+m.year || +String(m.date).slice(0, 4)) * 1000 + +m.round; });
  const fxKey = f => {
    if (hasDate) return String(f.date || f.utc || '').slice(0, 10);
    if (f.week != null) return (+f.season || +String(f.date || f.utc || '').slice(0, 4)) * 1000 + +f.week;
    return aflRound[String(f.date || '').slice(0, 10) + '|' + nt(f.home) + '|' + nt(f.away)] || null;
  };
  const played = r => {
    if (r.min != null) return +r.min > 0;
    if (r.toiMin != null) return +r.toiMin > 0;
    if (r.minutes != null && typeof r.minutes === 'number') return r.minutes > 0;
    return Object.keys(r).some(k => typeof r[k] === 'number' && r[k] > 0 &&
      !/^(year|week|season|gamePk|teamId|id|matchid|playerid|home)$/i.test(k));
  };
  const logsBy = {};
  logs.forEach(r => { const n = r.Player || r.player; if (n) (logsBy[n] = logsBy[n] || []).push(r); });

  for (const file of files){
    const snap = JSON.parse(zlib.gunzipSync(fs.readFileSync(`${dirHist}/${file}`)).toString('utf8'));
    const ref = Date.parse(snap.oddsUpdated || snap.captured) - 2 * 3600e3;
    const next = {};
    (snap.fixture || []).forEach(f => {
      // Snapshots from before the fixture-date fix store a bare date ("2026-09-20") as utc. Parsed
      // as midnight, every same-day game looked already started and whole snapshots went ungraded
      // (EPL 20 Sep: 4,263 prices). A bare date means "some time that day": use the day's end.
      const u = String(f.utc || f.date || '');
      const t = Date.parse(/^\d{4}-\d{2}-\d{2}$/.test(u) ? u + 'T23:59:59Z' : u);
      if (!isFinite(t) || t < ref) return;
      [f.home, f.away, f.homeAbbr, f.awayAbbr].forEach(tm => {
        if (!tm) return;
        const k = nt(tm);
        if (!next[k] || t < next[k].t) next[k] = { f, t };
      });
    });
    const target = {}, settled = {};
    let minKey = null;
    (snap.odds || []).forEach(row => {
      if (target[row.player] !== undefined) return;
      const p = byName[row.player];
      const nx = p && next[nt(p.team)];
      const k = nx ? fxKey(nx.f) : null;
      target[row.player] = k;
      if (k == null) return;
      if (minKey == null || k < minKey) minKey = k;
      const hit = (logsBy[row.player] || []).filter(r => rowKey(r) === k && played(r));
      if (hit.length) settled[row.player] = hit[0];
    });
    if (!Object.keys(settled).length){ ungraded += (snap.odds || []).length; continue; }
    // history strictly before the earliest game in this snapshot - no look-ahead
    const prior = {};
    logs.forEach(r => {
      if (!(rowKey(r) < minKey)) return;
      const n = r.Player || r.player; if (n) (prior[n] = prior[n] || []).push(r);
    });
    const lgCache = {};
    const lgRate = (mkt, line, side) => {
      const ck = mkt + '|' + line + '|' + side;
      if (lgCache[ck] != null) return lgCache[ck];
      let n = 0, h = 0;
      Object.keys(prior).forEach(nm => prior[nm].slice(-30).forEach(r => {
        const v = statOf(key, r, mkt); if (v == null) return;
        n++; if (side === 'under' ? v <= line : v > line) h++;
      }));
      return (lgCache[ck] = n >= 50 ? h / n : null);
    };
    const capProb = (name, mkt, line, side, prob) => {
      const rows = (prior[name] || []).slice().sort((a, b) => (rowKey(a) < rowKey(b) ? 1 : -1)).slice(0, 20);
      const vals = rows.map(r => statOf(key, r, mkt)).filter(v => v != null);
      if (vals.length < 8) return prob;
      const hits = vals.filter(v => side === 'under' ? v <= line : v > line).length;
      const lg = lgRate(mkt, line, side);
      const emp = lg != null ? (hits + 10 * lg) / (vals.length + 10) : (hits + 1) / (vals.length + 2);
      return Math.min(prob, emp + ((lg != null && lg < 0.30) ? 0 : 0.05));
    };

    try {
      scoring.configure({ players, teams, teamsForm, dvp, logsByName: prior,
                          fixture, meta, env:{}, currentSeason: season });
    } catch (e) { continue; }

    (snap.odds || []).forEach(row => {
      snapRows++;
      if (!markets.has(row.market)) return;
      if (argMain && !isMainLine(row)) return;
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
        // RECORD CAP, as the shell applies it to every signal: no more than his own record at
        // this line (shrunk toward the league rate over 10 pseudo-games) plus 5pt, or none on
        // markets that land under 30%. Graded side by side with the raw model, because what the
        // signals SHOW is the capped number - the raw model is only what they start from.
        const pCap = capProb(row.player, row.market, row.line, side, prob);
        const evCap = pCap * (price - 1) - (1 - pCap);
        if (argRule){
          /* A rule replaces the EV gate entirely. It is asked once per side and takes the side
             it names, so a rule that says 'under' is never also graded as an over. */
          if (argRule.markets && argRule.markets.indexOf(row.market) < 0) return;
          let want = null;
          try {
            want = argRule.pick({ sport:key, dir, player:row.player, market:row.market,
                                  line:row.line, over:row.over, under:row.under, side,
                                  price:+price, opp, team:res.Team || res.team, day:file.slice(0, 10),
                                  prior:(prior[row.player] || []), absent:absentFor(res, logs),
                                  data:f => dataOf(dir, f), prob, evRaw:ev, evCap });
          } catch (e) { return; }
          if (want !== side) return;
        } else {
          if (ev < argEV && evCap < argEV) return;
          if (prob > Math.min(0.97, (1/price) * 4) && pCap > Math.min(0.97, (1/price) * 4)) return;
        }
        const won = side === 'over' ? actual > row.line : actual <= row.line;
        picks.push({ day:file.replace('.json.gz', ''), game:target[row.player], key, player:row.player,
                     market:row.market, line:row.line, side,
                     price:+price, prob, ev, pCap, evCap, won, actual });
      });
    });
  }

  if (process.env.GRADE_DUMP) fs.writeFileSync(`${process.env.GRADE_DUMP}/${key}.json`, JSON.stringify(picks));
  console.log(`\n===== ${key.toUpperCase()} — ${files.length} snapshot day(s), ${snapRows} stored prices`);
  if (!picks.length){
    console.log(`  no gradable picks yet` + (ungraded ? ` (${ungraded} prices captured for games not yet played)` : ''));
    return;
  }
  /* AN ERROR BAR, CLUSTERED BY SLATE.
     A bare ROI invites the reader to treat it as the answer, and on a few hundred picks it is
     mostly noise - a band here showed +29% on 36 bets and a 90% interval of -3% to +56%.
     Resampling PICKS would report an interval several times too narrow, because picks move
     together: twelve legs off one blowout are nearer one observation than twelve.
     So the unit resampled is the fixture key each price settled on, which is the DAY'S SLATE
     for the sports whose gamelogs carry dates and the ROUND for NFL and AFL, which do not.
     That is coarser than a single game and deliberately so - it is the conservative direction,
     and it is why a 20,382-pick NFL sample honestly reports as five clusters and gets no
     interval at all rather than a flatteringly tight one. */
  const roiOf = rows => rows.reduce((a, r) => a + (r.won ? r.price - 1 : -1), 0) / rows.length * 100;
  const ciOf = (rows, draws) => {
    const byGame = new Map();
    rows.forEach(r => {
      const k = r.key + '|' + r.game;
      if (!byGame.has(k)) byGame.set(k, []);
      byGame.get(k).push(r);
    });
    const keys = [...byGame.keys()];
    if (keys.length < 8) return null;          // too few slates to say anything
    draws = draws || 1200;
    let seed = 20260101;                        // fixed, so two runs of the same data agree
    const rnd = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
    const out = [];
    for (let d = 0; d < draws; d++){
      const s = [];
      for (let i = 0; i < keys.length; i++) s.push(...byGame.get(keys[(rnd() * keys.length) | 0]));
      if (s.length) out.push(roiOf(s));
    }
    out.sort((a, b) => a - b);
    return [out[Math.floor(0.05 * out.length)], out[Math.floor(0.95 * out.length)]];
  };
  const report = (label, rows, evKey) => {
    if (!rows.length) return;
    evKey = evKey || 'ev';
    const n = rows.length, wins = rows.filter(r => r.won).length;
    const ret = rows.reduce((a, r) => a + (r.won ? r.price - 1 : -1), 0);
    const exp = rows.reduce((a, r) => a + r[evKey], 0);
    const ci = ciOf(rows);
    const slates = new Set(rows.map(r => r.key + '|' + r.game)).size;
    console.log(`  ${label.padEnd(22)} ${String(n).padStart(5)} picks  ${(wins/n*100).toFixed(1).padStart(5)}% strike  `
      + `${(ret/n*100 >= 0 ? '+' : '')}${(ret/n*100).toFixed(1).padStart(6)}% ROI   (model expected ${(exp/n*100 >= 0 ? '+' : '')}${(exp/n*100).toFixed(1)}%)`
      + (ci ? `   90% ${ci[0] >= 0 ? '+' : ''}${ci[0].toFixed(1)}..${ci[1] >= 0 ? '+' : ''}${ci[1].toFixed(1)}`
              + `${ci[1] < 0 ? ' LOSES' : ci[0] > 0 ? ' CLEARS' : ''}`
            : `   (only ${slates} slate${slates === 1 ? '' : 's'} - no interval)`));
  };
  const rawPicks = picks.filter(r => r.ev >= argEV), capPicks = picks.filter(r => r.evCap >= argEV);
  console.log('  RAW MODEL (what the signals start from):');
  report('ALL', rawPicks);
  console.log('  RECORD-CAPPED (what the signals show):');
  report('ALL', capPicks, 'evCap');
  [[1.2,2],[2,3],[3,6],[6,999]].forEach(b =>
    report(`   $${b[0]}-${b[1] === 999 ? '+' : b[1]}`, capPicks.filter(r => r.price >= b[0] && r.price < b[1]), 'evCap'));
  picks = rawPicks;
  console.log('  raw model by price:');
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

/* Guarded so the file can be required by a test without grading seven sports as a side effect.
   Running it directly is unchanged. */
if (require.main === module){
SPORTS.filter(([k]) => !argSport || k === argSport)
  .forEach(([k, d]) => { try { gradeSport(k, d); } catch (e) { console.log(`${k}: ${e.message}`); } });
console.log(argRule
  ? `\nRule "${argRule.name || 'unnamed'}"${argMain ? ' \u00b7 main lines only' : ''}`
    + ` \u00b7 settled at the stored price \u00b7 no look-ahead.`
    + `\nIntervals are 90%, resampled by SLATE (the day or round a price settled on, not the `
    + `pick), so a band whose interval spans zero has not been shown to do anything \u2014 `
    + `however good its ROI looks.`
  : `\nEdge threshold ${(argEV*100).toFixed(0)}%${argMain ? ' \u00b7 main lines only' : ''}`
    + ` \u00b7 settled at the stored price \u00b7 `
    + `no look-ahead: each day is priced by a model that has only seen earlier games.`
    + `\nIntervals are 90%, resampled by SLATE (the day or round a price settled on) rather `
    + `than by pick, because picks on one slate move together.`);
}
module.exports = { isMainLine, absentFor, MAIN_LO, MAIN_HI, SPORTS };
