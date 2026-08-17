/* JTT EPL — scoring.js  (window.JTTScoring)
   Soccer projection engine adapted to the unified shell's JTTScoring contract.
   Player props: blended per-match rate x opponent leaky-defence (DVP) factor, turned into a
   Poisson P(>= line) and blended with the empirical hit rate. Season-form overlay (this season
   only) replaces the WC tournament-form. Lines picked from the per-market ladders in config.js.
   Reads gamelog rows with the contract fields: goals, assists, xg, xa, shots, shotsOn, keyPasses,
   tackles, foulsCommitted, foulsDrawn, saves, cs, conceded, cards, min, date, opp, home. */
window.JTTScoring = (function () {
  'use strict';

  var RECENT_WIN = 10;    // base projection window (last N matches, any season)
  var DVP_WEIGHT = 0.5;   // how hard the opponent leaky-defence signal moves the projection
  var MODEL_BLEND = 0.6;  // Poisson model weight vs empirical hit rate

  var players = [], teams = [], dvp = [], logs = {}, fixture = [], meta = {}, curSeason = '2026';
  var byName = {}, byTeamGame = {}, teamByName = {}, dvpByTeam = {}, dvpAvg = {};
  var CFG = {}, SEASON_START = '', LADDERS = {};

  function _clamp(x, lo, hi) { return x < lo ? lo : x > hi ? hi : x; }
  function poissonAtLeast(lambda, k) {
    lambda = Math.max(0, lambda); if (k <= 0) return 1;
    var cdf = 0, term = Math.exp(-lambda);
    for (var i = 0; i < k; i++) { cdf += term; term *= lambda / (i + 1); }
    return _clamp(1 - cdf, 0, 1);
  }

  function logsOf(name) { return logs[name] || []; }
  function slice(name) { var a = logsOf(name); return a.length > RECENT_WIN ? a.slice(-RECENT_WIN) : a; }
  function avgOf(name, key) { var s = slice(name); if (!s.length) return 0; var t = 0; for (var i = 0; i < s.length; i++) t += (s[i][key] || 0); return t / s.length; }
  function hitRateLogs(name, key, line) { var s = slice(name); if (!s.length) return 0; var h = 0; for (var i = 0; i < s.length; i++) if ((s[i][key] || 0) >= line) h++; return h / s.length; }

  // Season-form overlay: form in THIS season's rows only (replaces WC tournament-form).
  // Pre-season there are no current rows, so callers fall back to the base window.
  function seasonForm(name, valKey, line) {
    if (line == null) return null;
    var rows = logsOf(name).filter(function (r) { return (r.date || '') >= SEASON_START; });
    if (!rows.length) return null;
    var hits = rows.filter(function (r) { return (r[valKey] || 0) >= line; }).length;
    return { games: rows.length, hits: hits, rate: hits / rows.length };
  }
  function seasonFormWeight(sf) { return (!sf || sf.games < 2) ? 0 : (sf.rate - 0.5) * 4; }

  // Last-season per-match rate from the FPL 'last' aggregate (fallback when a player has no logs).
  function seasonBaseline(p, stat) {
    if (!p || !p.last) return 0;
    var L = p.last, starts = Math.max(1, L.starts || L.games || 0);
    var map = { goals: 'G', assists: 'A', saves: 'saves', cs: 'CS', xg: 'xG', xa: 'xA' };
    var k = map[stat];
    return (k && L[k] != null) ? L[k] / starts : 0;
  }

  // Bayesian shrinkage: recent per-match blended toward a small prior of the same window.
  function blendedRate(name, stat) {
    var s = slice(name), n = s.length;
    if (!n) return seasonBaseline(byName[name], stat);
    var sum = 0; for (var i = 0; i < n; i++) sum += (s[i][stat] || 0);
    var basePG = seasonBaseline(byName[name], stat) || (sum / n);
    var prior = 5;
    return (sum + basePG * prior) / (n + prior);
  }

  function gameForTeam(team) { return byTeamGame[team] || null; }
  function oppOfTeam(team) { var g = gameForTeam(team); return g ? (g.home === team ? g.away : g.home) : null; }

  // ---- projection: base rate x opponent leaky-defence ----
  function projPlayer(p, stat) {
    var base = blendedRate(p.name, stat), f = 1, opp = oppOfTeam(p.team), pct = null;
    if (opp) {
      pct = getDVPPct(opp, p.pos, stat);
      if (pct != null) f *= _clamp(1 + (pct / 100) * DVP_WEIGHT, 0.75, 1.3);
    }
    return { proj: base * f, opp: opp, dvpPct: pct };
  }
  function prob(p, stat, line) {
    var r = projPlayer(p, stat), model = poissonAtLeast(r.proj, Math.ceil(line));
    var emp = hitRateLogs(p.name, stat, line);
    return _clamp(MODEL_BLEND * model + (1 - MODEL_BLEND) * emp, 0.01, 0.99);
  }

  // ---- O/U line from the config ladder (highest line the avg comfortably clears) ----
  function roundLineFromLadder(avg, ladder) {
    if (avg == null || avg <= 0 || !ladder) return null;
    var best = null;
    for (var i = 0; i < ladder.length; i++) { if (avg > ladder[i]) best = ladder[i]; else break; }
    return best;
  }
  function drLine(avg, stat) { return roundLineFromLadder(avg, LADDERS[stat] || [0.5, 1.5, 2.5]); }

  // ---- interface ----
  function scoreCMP(p, stat, line, opp) {
    if (!p) return 0;
    return (prob(p, stat, line) - 0.5) * 20;   // 0.5->0, 0.62->2.4, 0.775->5.5
  }
  function verdict(p, stat, line, opp) {
    var score = scoreCMP(p, stat, line, opp), label, col;
    if (score >= 5.5) { label = 'Green Light OVER'; col = '#22c55e'; }
    else if (score >= 3.5) { label = 'Lean OVER'; col = '#86efac'; }
    else if (score <= -3.5) { label = 'Strong Lean UNDER'; col = '#ef4444'; }
    else if (score <= -2) { label = 'Lean UNDER'; col = '#f97316'; }
    else { label = 'No Clear Edge'; col = '#888'; }
    return { score: score, label: label, col: col };
  }
  function getHitRate(name, stat, line, curOnly) {
    var games = logsOf(name);
    if (curOnly) games = games.filter(function (g) { return (g.date || '') >= SEASON_START; });
    if (games.length < 3) return null;
    var hits = games.filter(function (g) { return (g[stat] || 0) >= line; }).length;
    return { rate: hits / games.length, n: games.length };
  }
  function getRecentAvg(name, stat, n) {
    var a = logsOf(name).slice(-(n || RECENT_WIN)).map(function (r) { return r[stat]; }).filter(function (v) { return v != null; });
    return a.length ? a.reduce(function (s, v) { return s + v; }, 0) / a.length : null;
  }
  function getL() { return null; }   // per-line shopping lands with the odds feed

  // ---- DVP: what the opponent concedes per stat vs league (leaky-defence signal) ----
  var POS_TO_DVP = { GK: 'GK', DEF: 'DEF', MID: 'MID', FWD: 'FWD' };
  function getDVPPct(oppTeam, pos, stat) {
    var row = dvpByTeam[oppTeam]; if (!row) return null;
    var val = row[stat]; if (val == null) return null;
    var avg = dvpAvg[stat]; if (avg == null || avg === 0) return null;
    return ((val - avg) / avg) * 100;
  }
  function muInfo(pct) {
    if (pct === null || pct === undefined) return null;
    if (pct > 12) return { t: 'Soft', c: '#22c55e', cl: 'c-soft' };
    if (pct > 4) return { t: 'Fav', c: '#eab308', cl: 'c-fav' };
    if (pct > -4) return { t: 'Neutral', c: '#555', cl: 'c-neu' };
    if (pct > -12) return { t: 'Tough', c: '#f97316', cl: 'c-tough' };
    return { t: 'V.Tough', c: '#ef4444', cl: 'c-vtough' };
  }

  // ---- matchup tags: leaky defence, set-piece role, hot/cold season form ----
  function getContextSignals(p, stat, line, opp) {
    var tags = []; if (!p) return tags;
    var oppT = opp || oppOfTeam(p.team);
    if (oppT && (stat === 'shots' || stat === 'shotsOn' || stat === 'goals' || stat === 'assists')) {
      var pct = getDVPPct(oppT, p.pos, stat);
      if (pct != null && pct > 8) tags.push({ t: 'vs leaky defence', good: true });
      else if (pct != null && pct < -8) tags.push({ t: 'vs stingy defence', good: false });
    }
    if ((stat === 'shots' || stat === 'shotsOn' || stat === 'goals') && p.pens_order === 1) tags.push({ t: 'On penalties', good: true });
    if ((stat === 'shots' || stat === 'shotsOn' || stat === 'assists' || stat === 'keyPasses') && (p.corners_fk_order === 1 || p.direct_fk_order === 1)) tags.push({ t: 'On set pieces', good: true });
    if (line != null) {
      var sf = seasonForm(p.name, stat, line);
      if (sf && sf.games >= 2 && sf.rate === 1) tags.push({ t: 'Hot form (' + sf.hits + '/' + sf.games + ')', good: true });
      else if (sf && sf.games >= 3 && sf.rate === 0) tags.push({ t: 'Cold (' + sf.hits + '/' + sf.games + ')', good: false });
    }
    return tags;
  }
  function cmpFactors(p, stat, line, opp, lbl) {
    var out = []; if (!p) return out;
    var av = avgOf(p.name, stat), hr = getHitRate(p.name, stat, line, false), r = projPlayer(p, stat);
    out.push({ label: 'Recent avg', val: av.toFixed(2), good: null });
    if (hr) out.push({ label: 'Hit rate (L' + Math.min(RECENT_WIN, hr.n) + ')', val: Math.round(hr.rate * 100) + '% (' + hr.n + ')', good: hr.rate >= 0.6 });
    out.push({ label: 'Projection', val: r.proj.toFixed(2), good: r.proj >= line });
    if (r.dvpPct != null) { var mu = muInfo(r.dvpPct); if (mu) out.push({ label: 'Matchup', val: mu.t + ' (' + (r.dvpPct >= 0 ? '+' : '') + Math.round(r.dvpPct) + '%)', good: r.dvpPct > 4 ? true : r.dvpPct < -4 ? false : null }); }
    getContextSignals(p, stat, line, opp).forEach(function (tg) { out.push({ label: 'Signal', val: tg.t, good: tg.good }); });
    return out;
  }

  var POS = ['GK', 'DEF', 'MID', 'FWD'];

  function configure(ctx) {
    ctx = ctx || {};
    players = ctx.players || []; teams = ctx.teams || []; dvp = ctx.dvp || [];
    logs = ctx.logsByName || {}; fixture = ctx.fixture || []; meta = ctx.meta || {};
    curSeason = ctx.currentSeason || '2026';
    CFG = (typeof window !== 'undefined' && window.SPORT_CONFIG) || {};
    SEASON_START = CFG.seasonStart || (curSeason + '-08-01');
    LADDERS = CFG.ladders || {};
    byName = {}; players.forEach(function (p) { byName[p.name] = p; });
    byTeamGame = {}; fixture.forEach(function (g) { if (g.home) byTeamGame[g.home] = g; if (g.away) byTeamGame[g.away] = g; });
    teamByName = {}; teams.forEach(function (t) { teamByName[t.team || t.name] = t; });
    // DVP table: per team, what it concedes per stat; + league averages
    dvpByTeam = {}; var sums = {}, cnt = {};
    dvp.forEach(function (row) {
      var key = row.team || row.name; if (!key) return;
      dvpByTeam[key] = row;
      ['shots', 'shotsOn', 'goals', 'assists', 'tackles', 'foulsCommitted', 'corners'].forEach(function (k) {
        if (row[k] != null) { sums[k] = (sums[k] || 0) + row[k]; cnt[k] = (cnt[k] || 0) + 1; }
      });
    });
    dvpAvg = {}; Object.keys(sums).forEach(function (k) { dvpAvg[k] = sums[k] / cnt[k]; });
  }

  return {
    configure: configure, verdict: verdict, scoreCMP: scoreCMP, drLine: drLine,
    getHitRate: getHitRate, getRecentAvg: getRecentAvg, getDVPPct: getDVPPct,
    getL5Avg: function (n, st) { return getRecentAvg(n, st, 5); },   // shell's Check My Bet expects this name
    muInfo: muInfo, getContextSignals: getContextSignals, cmpFactors: cmpFactors,
    getL: getL, POS: POS, POS_TO_DVP: POS_TO_DVP,
    // EPL extras used by epl/signals.js
    projPlayer: projPlayer, prob: prob, roundLineFromLadder: roundLineFromLadder,
    seasonForm: seasonForm, seasonFormWeight: seasonFormWeight,
    avgOf: avgOf, hitRateLogs: hitRateLogs, slice: slice, logsOf: logsOf, oppOfTeam: oppOfTeam
  };
})();
